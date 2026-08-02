"""Login state machine tests for providers/betfair_auth.py.

The one property that matters most: no code path may ever retry a failed
login — repeated attempts with a wrong password LOCK the Betfair account.
All HTTP is faked; these tests can never touch Betfair.
"""

import json
import time

import pytest

from providers import betfair_auth
from providers.betfair_auth import (
    BetfairAuthError, BetfairEdgeBlocked, BetfairLoginBlocked,
    TOKEN_MAX_AGE_SECONDS,
)


class FakeRequestException(Exception):
    pass


class FakeResp:
    def __init__(self, body=None, text=None, status=200):
        self.text = text if text is not None else json.dumps(body)
        self.status_code = status
        self.ok = status < 400

    def json(self):
        return json.loads(self.text)


class FakeRequests:
    RequestException = FakeRequestException

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected HTTP call to {url}")
        return self.responses.pop(0)


@pytest.fixture
def auth_env(monkeypatch, tmp_path):
    """Isolated credentials + cache/marker paths; no cert files, no env token."""
    monkeypatch.setenv("BETFAIR_APP_KEY", "test-app-key")
    monkeypatch.setenv("BETFAIR_USERNAME", "user@example.com")
    monkeypatch.setenv("BETFAIR_PASSWORD", "hunter2")
    monkeypatch.setenv("BETFAIR_CERT_PATH", str(tmp_path / "missing.crt"))
    monkeypatch.setenv("BETFAIR_KEY_PATH", str(tmp_path / "missing.key"))
    monkeypatch.delenv("BETFAIR_SESSION_TOKEN", raising=False)
    monkeypatch.setattr(betfair_auth, "_TOKEN_CACHE", tmp_path / "session.json")
    monkeypatch.setattr(betfair_auth, "_LOGIN_BLOCK_MARKER", tmp_path / "blocked.json")
    return tmp_path


def _install(monkeypatch, responses):
    fake = FakeRequests(responses)
    monkeypatch.setattr(betfair_auth, "requests", fake)
    return fake


def test_interactive_success_caches_token(auth_env, monkeypatch):
    fake = _install(monkeypatch, [FakeResp({"token": "TOK1", "status": "SUCCESS"})])
    assert betfair_auth.get_session_token() == "TOK1"
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == betfair_auth.INTERACTIVE_LOGIN_URL
    cached = json.loads(betfair_auth._TOKEN_CACHE.read_text())
    assert cached["token"] == "TOK1"
    assert cached["self_created"] is True
    assert cached["auth_method"] == "interactive"


def test_cert_login_preferred_when_cert_files_exist(auth_env, monkeypatch):
    (auth_env / "missing.crt").write_text("cert")
    (auth_env / "missing.key").write_text("key")
    fake = _install(monkeypatch, [FakeResp({"sessionToken": "CTOK", "loginStatus": "SUCCESS"})])
    assert betfair_auth.get_session_token() == "CTOK"
    assert [url for url, _ in fake.calls] == [betfair_auth.CERT_LOGIN_URL]


def test_cert_auth_required_falls_back_to_interactive(auth_env, monkeypatch):
    (auth_env / "missing.crt").write_text("cert")
    (auth_env / "missing.key").write_text("key")
    fake = _install(monkeypatch, [
        FakeResp({"loginStatus": "CERT_AUTH_REQUIRED"}),
        FakeResp({"token": "ITOK", "status": "SUCCESS"}),
    ])
    assert betfair_auth.get_session_token() == "ITOK"
    assert [url for url, _ in fake.calls] == [
        betfair_auth.CERT_LOGIN_URL, betfair_auth.INTERACTIVE_LOGIN_URL]


@pytest.mark.parametrize("status", sorted(betfair_auth.TERMINAL_LOGIN_STATUSES))
def test_terminal_status_no_retry_and_marker(auth_env, monkeypatch, status):
    fake = _install(monkeypatch, [
        FakeResp({"status": "FAIL", "error": status}),
        # A second canned response exists so a buggy retry would succeed
        # silently instead of tripping the FakeRequests guard.
        FakeResp({"token": "SHOULD_NEVER_HAPPEN", "status": "SUCCESS"}),
    ])
    with pytest.raises(BetfairLoginBlocked) as exc:
        betfair_auth.get_session_token()
    assert len(fake.calls) == 1, "terminal login status must never be retried"
    assert exc.value.status == status
    assert exc.value.exit_code == 3
    marker = json.loads(betfair_auth._LOGIN_BLOCK_MARKER.read_text())
    assert marker["status"] == status
    assert marker["at"]
    # The meaning comes from the smoke script's EXPLAIN semantics.
    assert marker["meaning"] == betfair_auth.TERMINAL_LOGIN_STATUSES[status]


def test_marker_file_refuses_login_before_any_network(auth_env, monkeypatch):
    betfair_auth._write_login_block(
        "ACCOUNT_ALREADY_LOCKED",
        betfair_auth.TERMINAL_LOGIN_STATUSES["ACCOUNT_ALREADY_LOCKED"])
    fake = _install(monkeypatch, [])
    with pytest.raises(BetfairLoginBlocked) as exc:
        betfair_auth.get_session_token()
    assert fake.calls == [], "marker present: no login attempt may reach the wire"
    assert "ACCOUNT_ALREADY_LOCKED" in str(exc.value)


def test_corrupt_marker_still_blocks(auth_env, monkeypatch):
    # A truncated/corrupt marker must be treated as a block (fail safe): its
    # presence is the signal, so a half-written file must never re-enable
    # logins with a password already known bad.
    betfair_auth._LOGIN_BLOCK_MARKER.write_text('{"status": "ACCOUNT_ALREA')  # truncated JSON
    fake = _install(monkeypatch, [FakeResp({"token": "NOPE", "status": "SUCCESS"})])
    with pytest.raises(BetfairLoginBlocked):
        betfair_auth.get_session_token()
    assert fake.calls == [], "unreadable marker must refuse login before any network I/O"


def test_marker_write_failure_still_raises_block(auth_env, monkeypatch):
    # If persisting the marker fails (e.g. disk full), the terminal status must
    # still raise BetfairLoginBlocked (exit 3) — the block for THIS run may
    # never be silently downgraded to a generic error that a scheduler retries.
    def boom(*_a, **_k):
        raise OSError("No space left on device")
    monkeypatch.setattr(betfair_auth, "_write_login_block", boom)
    fake = _install(monkeypatch, [
        FakeResp({"status": "FAIL", "error": "INVALID_USERNAME_OR_PASSWORD"}),
        FakeResp({"token": "SHOULD_NEVER_HAPPEN", "status": "SUCCESS"}),
    ])
    with pytest.raises(BetfairLoginBlocked) as exc:
        betfair_auth.get_session_token()
    assert exc.value.exit_code == 3
    assert len(fake.calls) == 1, "must not retry after a terminal status even when the marker write fails"


def test_edge_block_html_is_network_error_not_credential_error(auth_env, monkeypatch):
    fake = _install(monkeypatch, [FakeResp(text="<!DOCTYPE html><html>blocked</html>", status=403)])
    with pytest.raises(BetfairEdgeBlocked) as exc:
        betfair_auth.get_session_token()
    assert len(fake.calls) == 1
    assert "credentials NOT tested" in str(exc.value)
    assert exc.value.exit_code == 4
    # No marker: nothing was learned about the credentials.
    assert not betfair_auth._LOGIN_BLOCK_MARKER.exists()


def test_fresh_cache_used_without_network(auth_env, monkeypatch):
    betfair_auth._TOKEN_CACHE.write_text(json.dumps({
        "token": "CACHED", "obtained_at": time.time(),
        "self_created": True, "auth_method": "interactive"}))
    fake = _install(monkeypatch, [])
    assert betfair_auth.get_session_token() == "CACHED"
    assert fake.calls == []


def test_keepalive_refreshes_near_expiry(auth_env, monkeypatch):
    old = time.time() - (TOKEN_MAX_AGE_SECONDS - 600)
    betfair_auth._TOKEN_CACHE.write_text(json.dumps({
        "token": "NEARLY", "obtained_at": old,
        "self_created": True, "auth_method": "interactive"}))
    fake = _install(monkeypatch, [FakeResp({"status": "SUCCESS"})])
    assert betfair_auth.get_session_token() == "NEARLY"
    assert [url for url, _ in fake.calls] == [betfair_auth.KEEP_ALIVE_URL]
    cached = json.loads(betfair_auth._TOKEN_CACHE.read_text())
    assert cached["obtained_at"] > old, "keepAlive success must restart the cache clock"


def test_expired_cache_triggers_fresh_login(auth_env, monkeypatch):
    betfair_auth._TOKEN_CACHE.write_text(json.dumps({
        "token": "EXPIRED", "obtained_at": time.time() - TOKEN_MAX_AGE_SECONDS - 60,
        "self_created": True, "auth_method": "interactive"}))
    fake = _install(monkeypatch, [FakeResp({"token": "FRESH", "status": "SUCCESS"})])
    assert betfair_auth.get_session_token() == "FRESH"
    assert [url for url, _ in fake.calls] == [betfair_auth.INTERACTIVE_LOGIN_URL]


def test_env_session_token_bypasses_login_and_cache(auth_env, monkeypatch):
    monkeypatch.setenv("BETFAIR_SESSION_TOKEN", "ENVTOK")
    fake = _install(monkeypatch, [])
    assert betfair_auth.get_session_token() == "ENVTOK"
    assert fake.calls == []
    assert not betfair_auth._TOKEN_CACHE.exists()


def test_logout_only_kills_self_created_sessions(auth_env, monkeypatch):
    # User-supplied token in the cache (not self-created): must survive.
    betfair_auth._TOKEN_CACHE.write_text(json.dumps({
        "token": "USERTOK", "obtained_at": time.time(), "self_created": False}))
    fake = _install(monkeypatch, [])
    assert betfair_auth.logout() is False
    assert fake.calls == []

    betfair_auth._write_cached_token("MINE", "interactive")
    fake = _install(monkeypatch, [FakeResp({"status": "SUCCESS"})])
    assert betfair_auth.logout() is True
    assert [url for url, _ in fake.calls] == [betfair_auth.LOGOUT_URL]
    assert not betfair_auth._TOKEN_CACHE.exists()


def test_readonly_token_cache_does_not_fail_a_good_login(auth_env, monkeypatch):
    """A read-only filesystem must not defeat a successful login.

    _TOKEN_CACHE lives inside the code tree, which is read-only on Lambda
    (/var/task). Unguarded, the cache write raised OSError immediately
    AFTER the login succeeded, so the Betfair Lambdas could never succeed
    however healthy the credentials were. The token is still returned; only
    the reuse optimisation is lost.
    """
    (auth_env / "missing.crt").write_text("cert")
    (auth_env / "missing.key").write_text("key")

    def readonly(*a, **k):
        raise OSError(30, "Read-only file system")
    monkeypatch.setattr(betfair_auth, "_write_cached_token", readonly)

    fake = _install(monkeypatch, [
        FakeResp({"sessionToken": "CTOK", "loginStatus": "SUCCESS"})])
    assert betfair_auth.get_session_token() == "CTOK"
    assert [url for url, _ in fake.calls] == [betfair_auth.CERT_LOGIN_URL]


def test_cache_write_failure_still_leaves_no_cache(auth_env, monkeypatch):
    # The optimisation is genuinely lost, not silently half-written.
    def readonly(*a, **k):
        raise OSError(30, "Read-only file system")
    monkeypatch.setattr(betfair_auth, "_write_cached_token", readonly)
    betfair_auth._cache_token_best_effort("TOK", "cert")
    assert not betfair_auth._TOKEN_CACHE.exists()


def test_missing_config_is_loud(auth_env, monkeypatch):
    monkeypatch.setenv("BETFAIR_PASSWORD", "")
    fake = _install(monkeypatch, [])
    with pytest.raises(BetfairAuthError) as exc:
        betfair_auth.get_session_token()
    assert "password" in str(exc.value)
    assert fake.calls == []
