"""The API client's bad-weather behaviour.

GitHub is not always up, and the interesting question is never "did it work"
but "what happens when it did not". A 503 from the search API is routine;
treating one as a failed poll put an error banner over a perfectly good board
for the whole interval.
"""

import io
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))

import gh  # noqa: E402


class FakeResponse(io.BytesIO):
    """Enough of an HTTP response for the client to read."""

    def __init__(self, body=b"{}", headers=None, status=200):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def http_error(code, headers=None, body=b"{}"):
    return urllib.error.HTTPError("https://api.github.com/graphql", code,
                                  "boom", headers or {}, io.BytesIO(body))


@pytest.fixture
def client(monkeypatch):
    # Retries sleep between attempts; tests must not.
    monkeypatch.setattr(gh.time, "sleep", lambda _s: None)
    return gh.Client("token")


def responder(monkeypatch, outcomes):
    """Serve `outcomes` in order: exceptions are raised, responses returned."""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(gh.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_a_503_is_retried_and_then_succeeds(client, monkeypatch):
    calls = responder(monkeypatch, [
        http_error(503),
        FakeResponse(b'{"data": {"viewer": {"login": "me"}}}'),
    ])
    data = client.graphql("query { viewer { login } }")
    assert data["viewer"]["login"] == "me"
    assert len(calls) == 2
    assert client.retries == 1


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_every_server_error_is_worth_another_try(client, monkeypatch, code):
    calls = responder(monkeypatch, [http_error(code), FakeResponse()])
    client.graphql("query {}")
    assert len(calls) == 2


def test_a_dropped_connection_is_retried_too(client, monkeypatch):
    calls = responder(monkeypatch, [
        urllib.error.URLError("connection reset"),
        FakeResponse(b'{"data": {"ok": 1}}'),
    ])
    assert client.graphql("query {}") == {"ok": 1}
    assert len(calls) == 2


def test_retries_are_not_infinite(client, monkeypatch):
    """Four attempts, then the caller hears about it — otherwise a real outage
    would keep one poll running for ever."""
    calls = responder(monkeypatch, [http_error(503)])
    with pytest.raises(urllib.error.HTTPError):
        client.graphql("query {}")
    assert len(calls) == len(gh.RETRY_DELAYS) + 1


def test_a_404_is_not_retried(client, monkeypatch):
    """Asking again cannot help, and hides the real answer behind a delay."""
    calls = responder(monkeypatch, [http_error(404)])
    with pytest.raises(urllib.error.HTTPError):
        client.graphql("query {}")
    assert len(calls) == 1


def test_a_bad_token_is_reported_not_retried(client, monkeypatch):
    responder(monkeypatch, [http_error(401)])
    ok, message = client.check()
    assert ok is False
    assert "401" in message


def test_a_rate_limit_is_not_a_server_error(client, monkeypatch):
    """403 with Retry-After means wait, not ask again immediately."""
    calls = responder(monkeypatch, [
        http_error(403, {"Retry-After": "30"}, b'{"message": "slow down"}')])
    with pytest.raises(gh.RateLimited) as caught:
        client.graphql("query {}")
    assert caught.value.retry_after == 30
    assert len(calls) == 1


def test_the_wait_applies_to_later_calls_too(client, monkeypatch):
    responder(monkeypatch, [http_error(403, {"Retry-After": "30"})])
    with pytest.raises(gh.RateLimited):
        client.graphql("query {}")
    # A second call must not go out at all while the wait stands.
    with pytest.raises(gh.RateLimited):
        client.graphql("query {}")


def test_partial_graphql_failure_keeps_the_good_half(client, monkeypatch):
    """One deleted repository must not empty the whole board."""
    responder(monkeypatch, [FakeResponse(
        b'{"data": {"p0": {"pullRequest": {"number": 1}}, "p1": null},'
        b' "errors": [{"type": "NOT_FOUND"}]}')])
    data = client.graphql("query {}")
    assert data["p0"]["pullRequest"]["number"] == 1
    assert data["_errors"]


def test_graphql_errors_with_no_data_are_raised(client, monkeypatch):
    responder(monkeypatch, [FakeResponse(
        b'{"data": null, "errors": [{"type": "FORBIDDEN"}]}')])
    with pytest.raises(gh.GraphQLError):
        client.graphql("query {}")


def test_the_budget_is_remembered_from_the_reply(client, monkeypatch):
    responder(monkeypatch, [FakeResponse(
        b'{"data": {"rateLimit": {"cost": 7, "remaining": 4321, "limit": 5000}}}')])
    client.graphql("query {}")
    budget = client.budget()
    assert budget["remaining"] == 4321
    assert budget["cost"] == 7
