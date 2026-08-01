"""Betfair Exchange API-NG authentication.

Non-interactive (certificate) login is the preferred path — it's the flow
Betfair designates for unattended bots, and it never hits CAPTCHA/2FA.
Interactive JSON login is the fallback for accounts where the certificate
has not been uploaded yet. Australian accounts authenticate against the
.com.au identity hosts; the betting API itself is the global api.betfair.com.

Endpoint contracts (verified live 2026-07-31, scripts/BETFAIR_KEYS_STATUS.md):
    cert login:   POST https://identitysso-cert.betfair.com.au/api/certlogin
                  form-encoded username/password, client cert pair on the TLS
                  connection, X-Application header
                  -> {"sessionToken": ..., "loginStatus": "SUCCESS"|<code>}
    interactive:  POST https://identitysso.betfair.com.au/api/login
                  form-encoded username/password, X-Application header
                  -> {"token": ..., "status": "SUCCESS"|<code>, "error": ...}
    keepAlive:    POST https://identitysso.betfair.com.au/api/keepAlive
                  X-Application + X-Authentication headers
                  -> {"status": "SUCCESS"|..., "error": ...}
    logout:       POST https://identitysso.betfair.com.au/api/logout
                  same headers as keepAlive

The app key alone only identifies the application (X-Application header).
Every call also needs a session token (X-Authentication header) from this
module. Tokens are cached to disk so the scheduler's separate task runs
(00:30 / 08:00 / 10:00) share one login instead of three.

Login lockout protection — repeated failed logins LOCK the Betfair account,
and an unattended scheduler would otherwise hammer it every run:

  * Every login function makes exactly ONE attempt on a given credential —
    no function ever retries. The one path that submits credentials twice in
    a single run is cert-login answering CERT_AUTH_REQUIRED (cert present but
    not yet uploaded to the account) falling back to interactive login; any
    TERMINAL status on the first attempt re-raises with no fallback, so a
    wrong password never reaches the second attempt.
  * A terminal status (wrong password, pending password change, locked)
    raises BetfairLoginBlocked AND writes a marker file
    (intelligence/betfair_login_blocked.json). The block is raised even if the
    marker write fails, and an existing-but-unreadable marker still blocks.
    While the marker exists, all further login attempts are refused before any
    network I/O. Fix the credentials on the Betfair website, then delete the
    marker to re-enable.
  * An HTML response instead of JSON is Betfair's edge/geo block page: the
    request never reached the application layer, so the credentials were
    NOT tested. That raises BetfairEdgeBlocked and writes no marker.

Env (in Race-Analytics/.env):
  BETFAIR_APP_KEY        application key (delayed key is fine for STRIDE)
  BETFAIR_USERNAME       betfair.com.au account username
  BETFAIR_PASSWORD       betfair.com.au account password
  BETFAIR_CERT_PATH      client certificate (default certs/betfair-client.crt)
  BETFAIR_KEY_PATH       client private key  (default certs/betfair-client.key)
  BETFAIR_SESSION_TOKEN  optional pre-made session (ssoid cookie); used as-is,
                         never cached, never logged out by this module
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests

_ROOT = Path(__file__).resolve().parents[3]

_env_path = _ROOT / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

CERT_LOGIN_URL = "https://identitysso-cert.betfair.com.au/api/certlogin"
INTERACTIVE_LOGIN_URL = "https://identitysso.betfair.com.au/api/login"
KEEP_ALIVE_URL = "https://identitysso.betfair.com.au/api/keepAlive"
LOGOUT_URL = "https://identitysso.betfair.com.au/api/logout"

# Betfair global sessions last ~8h idle; refresh well inside that so a token
# handed to a long pipeline run can't expire mid-flight.
TOKEN_MAX_AGE_SECONDS = 4 * 3600
# Inside this window before expiry a keepAlive (which resets Betfair's idle
# clock) is cheaper and safer than burning another login.
KEEPALIVE_WINDOW_SECONDS = 30 * 60

_INTEL_DIR = Path(__file__).resolve().parent.parent / "intelligence"
_TOKEN_CACHE = _INTEL_DIR / "betfair_session.json"
_LOGIN_BLOCK_MARKER = _INTEL_DIR / "betfair_login_blocked.json"

# Login statuses after which a retry can only hurt: the password is wrong or
# the account is (about to be) locked, and Betfair locks accounts on repeated
# failures. Meanings mirror the EXPLAIN table in scripts/betfair_keys_smoke.py.
TERMINAL_LOGIN_STATUSES = {
    "INVALID_USERNAME_OR_PASSWORD":
        "the username/password used was rejected — if the password was reset "
        "on the website, the stored secret must be the NEW password",
    "ACCOUNT_PENDING_PASSWORD_CHANGE":
        "per Betfair docs the account is LOCKED until password recovery is "
        "completed (betfair.com.au 'forgot password' flow) — reset there, then "
        "update the stored password. If this keeps coming back, something "
        "somewhere is still retrying logins with the OLD password and "
        "re-locking the account",
    "ACCOUNT_NOW_LOCKED":
        "this attempt tipped the account into locked state — unlock on the "
        "Betfair website before any retry",
    "ACCOUNT_ALREADY_LOCKED":
        "the account is locked (usually from repeated failed logins) — unlock "
        "via the Betfair website/support, then retry",
}


class BetfairAuthError(Exception):
    """Raised with Betfair's loginStatus code so failures are diagnosable."""
    exit_code = 1


class BetfairLoginBlocked(BetfairAuthError):
    """Terminal credential state — retrying would lock (or keep locked) the
    account. Carries the Betfair status code and its meaning."""
    exit_code = 3

    def __init__(self, status: str, meaning: str):
        self.status = status
        self.meaning = meaning
        super().__init__(f"login blocked [{status}]: {meaning}")


class BetfairEdgeBlocked(BetfairAuthError):
    """Betfair's edge served its HTML block page (geo/WAF). The request never
    reached the application layer — the credentials were NOT tested."""
    exit_code = 4

    def __init__(self, context: str):
        super().__init__(
            f"{context}: HTML block page from Betfair's edge — network/geo "
            "problem, credentials NOT tested. Run from an allowed country "
            "(e.g. an AU machine).")


def looks_like_edge_block(text: str) -> bool:
    """HTML instead of JSON from an API endpoint = WAF/geo block page; the
    request never reached Betfair's application layer."""
    return str(text or "").lstrip().lower().startswith(("<!doctype", "<html", "<!--"))


def _resolve_path(value: str) -> str:
    # Relative cert paths in .env are relative to the repo root, not the
    # caller's cwd — pipeline scripts run from server/python.
    path = Path(value)
    return str(path if path.is_absolute() else _ROOT / path)


def _config():
    return {
        "app_key": os.environ.get("BETFAIR_APP_KEY", ""),
        "username": os.environ.get("BETFAIR_USERNAME", ""),
        "password": os.environ.get("BETFAIR_PASSWORD", ""),
        "cert_path": _resolve_path(os.environ.get("BETFAIR_CERT_PATH", "certs/betfair-client.crt")),
        "key_path": _resolve_path(os.environ.get("BETFAIR_KEY_PATH", "certs/betfair-client.key")),
    }


def _cert_files_exist(cfg=None) -> bool:
    cfg = cfg or _config()
    return Path(cfg["cert_path"]).exists() and Path(cfg["key_path"]).exists()


def missing_config() -> list:
    # Certs are optional: interactive login is the fallback when they don't
    # exist yet (they must still be uploaded to the Betfair account to work).
    cfg = _config()
    return [k for k in ("app_key", "username", "password")
            if not cfg[k] or cfg[k].startswith("your-")]


def login_block() -> Optional[dict]:
    """The lockout marker, if a previous run hit a terminal login status.
    While it exists no login is attempted — delete the file after fixing the
    credentials on the Betfair website.

    Fail SAFE: the file's mere presence is the block signal, so an
    existing-but-unreadable marker (truncated/corrupt) is treated as a block,
    never silently ignored — otherwise a half-written marker would re-enable
    logins with a password already known bad. Only genuine absence returns
    None."""
    if not _LOGIN_BLOCK_MARKER.exists():
        return None
    try:
        return json.loads(_LOGIN_BLOCK_MARKER.read_text())
    except (OSError, ValueError):
        return {
            "status": "UNREADABLE_MARKER",
            "meaning": "a login-block marker exists but could not be read/parsed; "
                       "refusing to log in until it is deleted",
            "at": "unknown",
        }


def _write_login_block(status: str, meaning: str):
    # Atomic write (tmp + replace): a partial/truncated marker would be read as
    # UNREADABLE and still block, but never as a valid no-block state.
    _LOGIN_BLOCK_MARKER.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "status": status,
        "meaning": meaning,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "action": "fix credentials on betfair.com.au, then delete this file to re-enable logins",
    }, indent=2)
    tmp = _LOGIN_BLOCK_MARKER.with_name(_LOGIN_BLOCK_MARKER.name + ".tmp")
    tmp.write_text(payload)
    tmp.replace(_LOGIN_BLOCK_MARKER)


def _refuse_if_blocked():
    block = login_block()
    if block:
        raise BetfairLoginBlocked(
            block.get("status", "UNKNOWN"),
            f"previous run at {block.get('at')} hit a terminal login status; "
            f"{block.get('meaning', '')} — delete "
            f"{_LOGIN_BLOCK_MARKER} after fixing the credentials")


def _check_login_body(body: dict, context: str) -> str:
    """Shared status handling for both login flavours. Returns the session
    token on SUCCESS; terminal statuses write the lockout marker and raise."""
    status = body.get("status") or body.get("loginStatus") or ""
    error = body.get("error") or ""
    token = body.get("token") or body.get("sessionToken") or ""
    if status == "SUCCESS" and token:
        return token
    key = error or status or "NO_STATUS"
    if key in TERMINAL_LOGIN_STATUSES:
        meaning = TERMINAL_LOGIN_STATUSES[key]
        # Persisting the marker must never suppress the block itself: if the
        # write fails the terminal status is still raised (exit 3), so this run
        # stops even though the cross-run guard could not be written.
        try:
            _write_login_block(key, meaning)
        except OSError as e:
            print(f"  [betfair] WARNING: could not persist login-block marker "
                  f"({e}) — raising block for this run anyway; the next run "
                  f"cannot see it, so fix the credentials before rerunning",
                  file=sys.stderr)
        raise BetfairLoginBlocked(key, meaning)
    raise BetfairAuthError(f"{context} failed: {key}")


def _read_cache() -> Optional[dict]:
    try:
        cached = json.loads(_TOKEN_CACHE.read_text())
        cached["age"] = time.time() - cached["obtained_at"]
        return cached
    except (OSError, ValueError, KeyError):
        return None


def _write_cached_token(token: str, method: str = "cert"):
    _TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    # self_created marks sessions this module opened — the only ones logout()
    # may kill (a user-supplied ssoid must survive us).
    _TOKEN_CACHE.write_text(json.dumps({
        "token": token, "obtained_at": time.time(),
        "self_created": True, "auth_method": method,
    }))
    _TOKEN_CACHE.chmod(0o600)


def cert_login() -> str:
    """Single attempt, terminal statuses write the lockout marker."""
    _refuse_if_blocked()
    cfg = _config()
    resp = requests.post(
        CERT_LOGIN_URL,
        data={"username": cfg["username"], "password": cfg["password"]},
        cert=(cfg["cert_path"], cfg["key_path"]),
        headers={"X-Application": cfg["app_key"],
                 "Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if looks_like_edge_block(resp.text):
        raise BetfairEdgeBlocked("cert login")
    try:
        body = resp.json()
    except ValueError:
        raise BetfairAuthError(f"cert login: non-JSON response (HTTP {resp.status_code})")
    return _check_login_body(body, "cert login")


def interactive_login() -> str:
    """Username/password login without a certificate. Betfair discourages it
    for bots (may trip CAPTCHA/2FA) — kept as the fallback until the
    certificate is uploaded to the Betfair account. Single attempt, terminal
    statuses write the lockout marker."""
    _refuse_if_blocked()
    cfg = _config()
    resp = requests.post(
        INTERACTIVE_LOGIN_URL,
        data={"username": cfg["username"], "password": cfg["password"]},
        headers={"X-Application": cfg["app_key"], "Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if looks_like_edge_block(resp.text):
        raise BetfairEdgeBlocked("interactive login")
    try:
        body = resp.json()
    except ValueError:
        raise BetfairAuthError(f"interactive login: non-JSON response (HTTP {resp.status_code})")
    return _check_login_body(body, "interactive login")


def keep_alive(token: str) -> bool:
    cfg = _config()
    try:
        resp = requests.post(
            KEEP_ALIVE_URL,
            headers={"X-Application": cfg["app_key"], "X-Authentication": token,
                     "Accept": "application/json"},
            timeout=15,
        )
        return resp.ok and resp.json().get("status") == "SUCCESS"
    except (requests.RequestException, ValueError):
        return False


def logout() -> bool:
    """Log out the cached session — only if this module created it. Returns
    True when a logout was actually sent."""
    cached = _read_cache()
    if not cached or not cached.get("self_created"):
        return False
    cfg = _config()
    try:
        requests.post(
            LOGOUT_URL,
            headers={"X-Application": cfg["app_key"],
                     "X-Authentication": cached["token"],
                     "Accept": "application/json"},
            timeout=15,
        )
    except requests.RequestException:
        pass
    try:
        _TOKEN_CACHE.unlink()
    except OSError:
        pass
    return True


def get_session_token(force: bool = False, allow_interactive: bool = True) -> str:
    """Session token: env override, else fresh cache, else keepAlive refresh
    near expiry, else one login (cert preferred when the cert pair exists).
    Call with force=True after an INVALID_SESSION response from the betting
    API."""
    # A user-supplied session needs no login and must never be logged out.
    env_token = os.environ.get("BETFAIR_SESSION_TOKEN", "").strip()
    if env_token:
        return env_token

    if not force:
        cached = _read_cache()
        if cached:
            if cached["age"] < TOKEN_MAX_AGE_SECONDS - KEEPALIVE_WINDOW_SECONDS:
                return cached["token"]
            if cached["age"] < TOKEN_MAX_AGE_SECONDS and keep_alive(cached["token"]):
                # keepAlive reset Betfair's idle clock, so the cache clock
                # restarts too — no login consumed.
                _write_cached_token(cached["token"], cached.get("auth_method", "cert"))
                return cached["token"]

    _refuse_if_blocked()
    missing = missing_config()
    if missing:
        raise BetfairAuthError(f"missing Betfair config: {', '.join(missing)} (see .env)")

    if _cert_files_exist():
        try:
            token = cert_login()
            method = "cert"
        except BetfairLoginBlocked:
            raise
        except BetfairAuthError as e:
            # CERT_AUTH_REQUIRED means the .crt was never uploaded to the
            # account; only that specific state may fall through — anything
            # else must not consume a second login attempt.
            if allow_interactive and "CERT_AUTH_REQUIRED" in str(e):
                print(f"  [betfair] {e} — falling back to interactive login", file=sys.stderr)
                token = interactive_login()
                method = "interactive"
            else:
                raise
    elif allow_interactive:
        token = interactive_login()
        method = "interactive"
    else:
        raise BetfairAuthError(
            "cert files missing and interactive login disabled "
            f"(looked for {_config()['cert_path']})")

    _write_cached_token(token, method)
    return token
