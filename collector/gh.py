"""GitHub API client: GraphQL and REST over the standard library.

The service host gets no venv, so this speaks HTTP directly. Three things
here are not obvious and cost an afternoon each if left out:

* **Rate limit.** GraphQL bills by node count, not by request, and a poll that
  fetches every pull request in full costs about two points each. The budget
  is 5000 points per hour, so the collector must be able to see how much is
  left and back off before GitHub starts refusing.
* **Secondary rate limits.** These are separate from the hourly budget and
  arrive as a 403 with `Retry-After`. Retrying immediately makes them worse.
* **Conditional requests.** A REST reply that has not changed since the last
  `ETag` returns 304 and is not billed at all. The notifications endpoint is
  polled constantly and changes rarely, which is exactly the case for it.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
UA = "github-zoo (+https://github.com/anton-vinogradov/github-zoo)"


class RateLimited(Exception):
    """Raised when GitHub asks us to wait, carrying how long it wants."""

    def __init__(self, retry_after: float, message: str = ""):
        super().__init__(message or f"rate limited, retry in {retry_after:.0f}s")
        self.retry_after = retry_after


class GraphQLError(Exception):
    """A 200 response whose body carries `errors`.

    GraphQL reports a missing repository or a revoked scope this way, not with
    an HTTP status, so a client that only checks the status code sees success
    and then trips over a None several frames later.
    """

    def __init__(self, errors: list, data: dict | None = None):
        types = {e.get("type", "?") for e in errors}
        super().__init__("; ".join(sorted(types)) or "GraphQL error")
        self.errors = errors
        self.data = data or {}


class Client:
    """One authenticated GitHub session, safe to share between threads."""

    def __init__(self, token: str, timeout: int = 45):
        self.token = token
        self.timeout = timeout
        self.lock = threading.Lock()
        self.etags: dict[str, str] = {}
        # What the last call learned about the budget, for the dashboard to
        # show and for the poll loop to slow down on.
        self.limit = {"remaining": None, "limit": None, "reset": 0, "cost": 0}
        # Secondary limits apply to the whole account; a wait asked for by one
        # call is honoured by all of them.
        self.blocked_until = 0.0
        self.calls = 0

    # ---------- transport ----------

    def _request(self, method: str, url: str, body: bytes | None = None,
                 headers: dict | None = None) -> tuple[int, dict, bytes]:
        wait = self.blocked_until - time.time()
        if wait > 0:
            raise RateLimited(wait, "waiting out a secondary rate limit")

        head = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": UA,
            "Accept-Encoding": "gzip",
        }
        head.update(headers or {})
        req = urllib.request.Request(url, data=body, headers=head, method=method)
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return resp.status, dict(resp.headers), raw
        except urllib.error.HTTPError as err:
            raw = err.read() or b""
            if err.headers.get("Content-Encoding") == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except OSError:
                    pass
            headers_out = dict(err.headers)
            if err.code == 304:
                return 304, headers_out, b""
            if err.code in (403, 429):
                retry = headers_out.get("Retry-After")
                if retry:
                    delay = float(retry)
                else:
                    # A depleted hourly budget reports remaining=0 and the wall
                    # clock when it refills; anything else gets a short pause
                    # rather than a hot retry loop.
                    reset = float(headers_out.get("X-RateLimit-Reset") or 0)
                    depleted = headers_out.get("X-RateLimit-Remaining") == "0"
                    delay = max(reset - time.time(), 1) if depleted and reset else 60.0
                with self.lock:
                    self.blocked_until = max(self.blocked_until, time.time() + delay)
                raise RateLimited(delay, _message(raw)) from err
            raise

    def _note_rest_limit(self, headers: dict) -> None:
        remaining = headers.get("X-RateLimit-Remaining")
        if remaining is None:
            return
        with self.lock:
            self.limit["rest_remaining"] = int(remaining)

    # ---------- GraphQL ----------

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        """Run one GraphQL query and return its `data`.

        Partial failures are the norm here: asking about twenty pull requests
        where one lives in a repository that has since been deleted returns
        nineteen good answers *and* an error. Throwing all twenty away would
        make one dead repository empty the dashboard, so the caller gets the
        data and decides.
        """
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        _, headers, raw = self._request(
            "POST", f"{API}/graphql", body,
            {"Content-Type": "application/json"})
        payload = json.loads(raw or b"{}")
        data = payload.get("data") or {}

        cost = ((data.get("rateLimit") or {}) if isinstance(data, dict) else {})
        if cost:
            with self.lock:
                self.limit.update({
                    "remaining": cost.get("remaining"),
                    "limit": cost.get("limit"),
                    "cost": cost.get("cost"),
                    "reset": cost.get("resetAt") or self.limit.get("reset"),
                })

        errors = payload.get("errors") or []
        if errors and not _useful(data):
            raise GraphQLError(errors, data)
        if errors:
            data = dict(data)
            data["_errors"] = errors
        self._note_rest_limit(headers)
        return data

    # ---------- REST ----------

    def rest(self, path: str, conditional: bool = False,
             params: dict | None = None) -> tuple[object, bool]:
        """GET one REST path. Returns (payload, changed).

        With `conditional`, an unchanged resource answers 304 — free, and not
        counted against the hourly budget — and this returns (None, False).
        """
        url = path if path.startswith("http") else f"{API}{path}"
        if params:
            import urllib.parse
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)

        headers = {}
        if conditional and url in self.etags:
            headers["If-None-Match"] = self.etags[url]

        status, resp_headers, raw = self._request("GET", url, None, headers)
        self._note_rest_limit(resp_headers)
        if status == 304:
            return None, False
        if conditional and resp_headers.get("ETag"):
            self.etags[url] = resp_headers["ETag"]
        return json.loads(raw or b"null"), True

    def rest_paged(self, path: str, params: dict | None = None,
                   max_pages: int = 10) -> list:
        """Follow `Link: rel="next"` and concatenate the pages."""
        out: list = []
        url = path if path.startswith("http") else f"{API}{path}"
        if params:
            import urllib.parse
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        for _ in range(max_pages):
            status, headers, raw = self._request("GET", url, None, {})
            self._note_rest_limit(headers)
            if status == 304:
                break
            page = json.loads(raw or b"null")
            if not isinstance(page, list):
                break
            out.extend(page)
            nxt = _next_link(headers.get("Link", ""))
            if not nxt:
                break
            url = nxt
        return out

    def patch(self, path: str, body: dict | None = None) -> object:
        url = path if path.startswith("http") else f"{API}{path}"
        payload = json.dumps(body or {}).encode()
        _, headers, raw = self._request("PATCH", url, payload,
                                        {"Content-Type": "application/json"})
        self._note_rest_limit(headers)
        return json.loads(raw) if raw else None

    # ---------- diagnostics ----------

    def budget(self) -> dict:
        with self.lock:
            out = dict(self.limit)
        out["blocked_for"] = max(0, self.blocked_until - time.time())
        out["calls"] = self.calls
        return out

    def check(self) -> tuple[bool, str]:
        """Is the token usable, and as whom? Used by install and by /api/selftest."""
        if not self.token:
            return False, "токен не задан"
        try:
            data = self.graphql("query { viewer { login } rateLimit { limit remaining resetAt } }")
        except RateLimited as err:
            return False, f"rate limit: подождать {err.retry_after:.0f}с"
        except urllib.error.HTTPError as err:
            if err.code == 401:
                return False, "токен отвергнут (401) — истёк или отозван"
            return False, f"HTTP {err.code}"
        except GraphQLError as err:
            return False, f"GraphQL: {err}"
        except OSError as err:
            return False, f"сеть: {err}"
        login = (data.get("viewer") or {}).get("login")
        if not login:
            return False, "ответ без viewer.login"
        return True, login


def _useful(data: object) -> bool:
    """Did a partially-failed GraphQL reply still carry anything?"""
    if not isinstance(data, dict):
        return False
    return any(v for k, v in data.items() if k != "rateLimit")


def _message(raw: bytes) -> str:
    try:
        return json.loads(raw).get("message", "")
    except Exception:
        return ""


def _next_link(link_header: str) -> str:
    for part in link_header.split(","):
        chunk = part.split(";")
        if len(chunk) < 2 or 'rel="next"' not in chunk[1]:
            continue
        return chunk[0].strip().strip("<>")
    return ""
