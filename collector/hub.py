#!/usr/bin/env python3
"""github-zoo hub: polls GitHub and serves the dashboard.

Standard library only, so the service host needs nothing but python3.

A background thread refreshes the snapshot on a timer and the HTTP layer only
ever hands out the last completed one. That separation is the whole design:
GitHub can be slow, rate-limited or briefly unreachable without the page ever
hanging or emptying — it just shows an older timestamp.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acks as acks_mod  # noqa: E402
import alerts as alerts_mod  # noqa: E402
import collect  # noqa: E402
import gh  # noqa: E402
import rules  # noqa: E402
import secrets  # noqa: E402
import settings as settings_mod  # noqa: E402
import store as store_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATHS = [
    Path(os.environ.get("GITHUB_ZOO_CONFIG", "")),
    Path("/etc/github-zoo.json"),
    ROOT / "collector" / "config.json",
    ROOT / "collector" / "config.example.json",
]
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/ui/core.js": ("ui/core.js", "application/javascript; charset=utf-8"),
    "/ui/cards.js": ("ui/cards.js", "application/javascript; charset=utf-8"),
    "/ui/details.js": ("ui/details.js", "application/javascript; charset=utf-8"),
    "/ui/wiring.js": ("ui/wiring.js", "application/javascript; charset=utf-8"),
    "/settings": ("settings.html", "text/html; charset=utf-8"),
    "/settings.html": ("settings.html", "text/html; charset=utf-8"),
    "/ui/settings.js": ("ui/settings.js", "application/javascript; charset=utf-8"),
}


def read_version() -> dict:
    """The note deploy.sh leaves behind: which commit, when, and was it clean."""
    try:
        lines = (ROOT / "VERSION").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"commit": "unknown"}
    first = lines[0].strip() if lines else "unknown"
    return {"commit": first.split()[0],
            "dirty": "правки-вне-коммита" in first,
            "deployed": lines[1].strip() if len(lines) > 1 else ""}


def load_config() -> dict:
    for path in CONFIG_PATHS:
        if path and path.is_file():
            with path.open(encoding="utf-8") as fh:
                cfg = json.load(fh)
            cfg["_path"] = str(path)
            return cfg
    raise SystemExit("github-zoo: конфиг не найден (искал /etc/github-zoo.json)")


class Board:
    """Owns the snapshot and the polling loop."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.snapshot: dict = {"generated": 0, "items": [], "summary": {},
                               "polling": False, "error": ""}
        self.cache: dict = {}
        self.login = cfg.get("login") or ""

        token = secrets.load(cfg, "token") or os.environ.get("GITHUB_ZOO_TOKEN", "")
        if not token and cfg.get("token_from_gh_cli", True):
            token = secrets.from_gh_cli()
        self.client = gh.Client(token, cfg.get("http_timeout", 45))

        self.store = store_mod.Store(cfg.get("history_db", "/var/lib/github-zoo/history.db"),
                                     cfg.get("history_retention_days", 180))
        self.acks = acks_mod.Acks(cfg.get("acks_file", "/var/lib/github-zoo/acks.json"))
        self.settings = settings_mod.Settings(
            cfg.get("settings_file", "/var/lib/github-zoo/settings.json"))
        # Applied before anything reads a threshold, including Alerts below.
        self.settings.apply_to(cfg)
        self.alerts = alerts_mod.Alerts(cfg)
        self.version = read_version()

        restored = self.store.last_snapshot()
        if restored:
            restored["restored"] = True
            self.snapshot = restored

    # ---------- polling ----------

    def poll_once(self) -> None:
        with self.lock:
            self.snapshot = dict(self.snapshot, polling=True)
        started = time.time()
        error = ""
        try:
            if not self.login:
                ok, who = self.client.check()
                if not ok:
                    raise RuntimeError(who)
                self.login = who

            raw = collect.collect(self.client, self.login, self.cfg, self.cache)
            items = list(raw["items"].values())
            now = collect.now()
            for item in items:
                item["verdict"] = rules.classify(item, self.login, self.cfg, now)
                item.pop("found_by_set", None)

            events = self.store.reconcile(items, now)
            # reconcile knows the true age; the verdict was computed from
            # GitHub's timestamps alone and must agree with what is displayed.
            for item in items:
                since = item.get("turn_since")
                if since:
                    item["verdict"]["waiting_since"] = since
                    item["verdict"]["waiting_days"] = max(0.0, (now - since) / 86400.0)
                    item["verdict"]["stale"] = (
                        item["verdict"]["turn"] == rules.YOU
                        and item["verdict"]["waiting_days"] >= float(self.cfg.get("stale_days", 14)))

            hidden = self.acks.apply(items, now)
            items.sort(key=lambda i: -(i["verdict"]["priority"]))
            summary = rules.summarise([i for i in items if not i.get("hidden")])
            summary["hidden"] = hidden

            snapshot = {
                "generated": now,
                "login": self.login,
                "items": items,
                "summary": summary,
                "orphan_notifications": raw["orphan_notifications"],
                "notification_count": raw["notification_count"],
                "events": events[:100],
                "recent": self.store.recent_events(now - 7 * 86400, 200),
                "warnings": raw["warnings"],
                "budget": self.client.budget(),
                "took": round(time.time() - started, 2),
                "polling": False,
                "version": self.version,
                "error": "",
            }
            with self.lock:
                self.snapshot = snapshot
            self.store.save_snapshot(snapshot)
            try:
                self.alerts.process([i for i in items if not i.get("hidden")], summary)
            except Exception:  # noqa: BLE001 — alerting must not break polling
                traceback.print_exc()

        except gh.RateLimited as err:
            error = f"GitHub просит подождать {err.retry_after:.0f} с — лимит запросов"
        except urllib.error.HTTPError as err:
            # After the retries in gh.py, a 5xx here means GitHub was down for
            # the better part of a minute — worth saying plainly, and worth
            # naming as temporary so it does not read as a broken dashboard.
            if err.code in gh.RETRY_STATUSES:
                error = f"GitHub временно недоступен (HTTP {err.code}), пробую снова"
            elif err.code == 401:
                error = "GitHub отверг токен (401) — истёк или отозван"
            else:
                error = f"GitHub ответил HTTP {err.code}"
        except OSError as err:
            error = f"сеть недоступна: {err}"
        except Exception as err:  # noqa: BLE001
            error = f"{type(err).__name__}: {err}"
            traceback.print_exc()

        if error:
            # Keep the last good snapshot on screen and say what went wrong,
            # rather than replacing real data with an error page. The time of
            # the last success travels with it: "503" alone does not say
            # whether what is on screen is a minute or a day old.
            with self.lock:
                self.snapshot = dict(self.snapshot, polling=False, error=error,
                                     error_at=time.time(), checked=time.time())

    def reclassify(self) -> None:
        """Re-run the rules over the snapshot in hand.

        Changing a threshold is a question about the cards already collected,
        not about GitHub. Without this, moving `ignore_days` did nothing
        visible until the next poll, which is exactly long enough to conclude
        the setting is broken and change it again.
        """
        with self.lock:
            snapshot = self.snapshot
            items = snapshot.get("items") or []
            if not items:
                return
            now = time.time()
            for item in items:
                item["verdict"] = rules.classify(item, self.login, self.cfg, now)
                since = item.get("turn_since")
                if since:
                    item["verdict"]["waiting_since"] = since
                    item["verdict"]["waiting_days"] = max(0.0, (now - since) / 86400.0)
            items.sort(key=lambda i: -(i["verdict"]["priority"]))
            hidden = self.acks.apply(items, now)
            summary = rules.summarise([i for i in items if not i.get("hidden")])
            summary["hidden"] = hidden
            self.snapshot = dict(snapshot, items=items, summary=summary)

    def reapply_acks(self) -> None:
        """Re-hide and recount on the snapshot already in hand.

        Snoozing is a decision about the board, not a question for GitHub, and
        it used to be applied only inside the poll — so a card stayed put until
        the next poll finished and the page happened to reload. Doing it here
        makes the button instant and costs no API calls.
        """
        with self.lock:
            snapshot = self.snapshot
            items = snapshot.get("items") or []
            if not items:
                return
            for item in items:
                item.pop("hidden", None)
                item.pop("hidden_until", None)
            hidden = self.acks.apply(items, time.time())
            summary = rules.summarise([i for i in items if not i.get("hidden")])
            summary["hidden"] = hidden
            self.snapshot = dict(snapshot, items=items, summary=summary)

    def loop(self) -> None:
        while True:
            self.poll_once()
            # Re-read every time: a new interval from the settings page should
            # not need a restart to take hold.
            self.wake.wait(timeout=int(self.cfg.get("poll_seconds", 300)))
            self.wake.clear()

    def state(self) -> dict:
        with self.lock:
            return self.snapshot


class Handler(BaseHTTPRequestHandler):
    board: Board = None  # set in main()
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003 — silence per-request logging
        pass

    # ---------- helpers ----------

    def _send(self, status: int, body: bytes, ctype: str,
              headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8",
                   {"Cache-Control": "no-store"})

    def _static(self, name: str, ctype: str) -> None:
        path = ROOT / name
        try:
            body = path.read_bytes()
        except OSError:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        # The dashboard is edited and redeployed often; a cached stylesheet
        # from two deploys ago is a confusing way to find that out.
        self._send(200, body, ctype, {"Cache-Control": "no-cache"})

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return {}

    # ---------- routes ----------

    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path in STATIC_FILES:
            name, ctype = STATIC_FILES[path]
            self._static(name, ctype)
        elif path == "/api/state":
            self._json(self.board.state())
        elif path == "/api/selftest":
            ok, who = self.board.client.check()
            tg_ok, tg_msg = self.board.alerts.test() if self.board.alerts.enabled \
                else (False, "алерты выключены")
            self._json({"github": {"ok": ok, "as": who if ok else "", "error": "" if ok else who},
                        "telegram": {"ok": tg_ok, "message": tg_msg},
                        "budget": self.board.client.budget(),
                        "store": {"ok": self.board.store.ok},
                        "config": self.board.cfg.get("_path", "")})
        elif path == "/api/settings":
            self._json({
                "schema": settings_mod.SCHEMA,
                "values": self.board.settings.current(self.board.cfg),
                "overridden": sorted(self.board.settings.values),
                "config_path": self.board.cfg.get("_path", ""),
                "version": self.board.version,
                "budget": self.board.client.budget(),
                "hidden": self.board.acks.snapshot(),
                "telegram_ready": bool(self.board.alerts.cfg.get("chats")),
            })
        elif path == "/healthz":
            state = self.board.state()
            fresh = time.time() - (state.get("generated") or 0) < 3600
            self._send(200 if fresh else 503,
                       json.dumps({"ok": fresh, "generated": state.get("generated")}).encode(),
                       "application/json")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        body = self._body()
        if path == "/api/refresh":
            self.board.wake.set()
            self._json({"ok": True})
        elif path == "/api/snooze":
            item_id, hours = body.get("id"), float(body.get("hours") or 24)
            if not item_id:
                self._json({"ok": False, "error": "нет id"}, 400)
                return
            self.board.acks.snooze(item_id, int(hours * 3600), _updated(self.board, item_id))
            self.board.reapply_acks()
            self._json({"ok": True, "summary": self.board.state().get("summary")})
        elif path == "/api/mute":
            item_id = body.get("id")
            if not item_id:
                self._json({"ok": False, "error": "нет id"}, 400)
                return
            self.board.acks.mute(item_id, _updated(self.board, item_id))
            self.board.reapply_acks()
            self._json({"ok": True, "summary": self.board.state().get("summary")})
        elif path == "/api/unhide":
            self.board.acks.clear(body.get("id") or "")
            self.board.reapply_acks()
            self._json({"ok": True, "summary": self.board.state().get("summary")})
        elif path == "/api/read":
            # Marking a notification thread read on GitHub, from here, so the
            # inbox and the dashboard do not drift apart.
            thread = body.get("thread")
            if not thread:
                self._json({"ok": False, "error": "нет thread"}, 400)
                return
            try:
                self.board.client.patch(f"/notifications/threads/{thread}")
                self._json({"ok": True})
            except Exception as err:  # noqa: BLE001
                self._json({"ok": False, "error": str(err)}, 502)
        elif path == "/api/settings":
            applied, rejected = self.board.settings.update(
                body.get("values") or {}, self.board.cfg)
            # Thresholds change what the cards already collected mean, so the
            # board is recomputed here; only a changed poll interval or a new
            # search needs GitHub, and that happens on the next cycle anyway.
            if applied:
                self.board.reclassify()
            self._json({"ok": not rejected, "applied": applied,
                        "rejected": rejected,
                        "values": self.board.settings.current(self.board.cfg),
                        "summary": self.board.state().get("summary")})
        elif path == "/api/settings/reset":
            self.board.settings.reset(self.board.cfg)
            # The config is the floor: dropping overrides means re-reading it.
            fresh = load_config()
            self.board.cfg.clear()
            self.board.cfg.update(fresh)
            self.board.reclassify()
            self._json({"ok": True,
                        "values": self.board.settings.current(self.board.cfg)})
        elif path == "/api/alerts/test":
            ok, message = self.board.alerts.test()
            self._json({"ok": ok, "message": message})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")


def _updated(board: Board, item_id: str) -> float:
    for item in board.state().get("items") or []:
        if item.get("id") == item_id:
            return item.get("updated") or 0.0
    return 0.0


def main() -> None:
    cfg = load_config()
    board = Board(cfg)
    Handler.board = board

    ok, who = board.client.check()
    print(f"github-zoo: конфиг {cfg.get('_path')}")
    print(f"github-zoo: токен — {secrets.describe(cfg, 'token')}; "
          f"GitHub: {'ok, ' + who if ok else 'НЕ ОК: ' + who}")
    if not board.store.ok:
        print(f"github-zoo: история недоступна ({getattr(board.store, 'error', '?')})")

    threading.Thread(target=board.loop, daemon=True).start()

    port = int(cfg.get("port", 8817))
    server = ThreadingHTTPServer((cfg.get("bind", "0.0.0.0"), port), Handler)
    print(f"github-zoo: слушаю :{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
