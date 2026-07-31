"""Betfair Exchange API-NG authentication.

Non-interactive (certificate) login is the primary path — it's the flow
Betfair designates for unattended bots, and it never hits CAPTCHA/2FA.
Australian accounts authenticate against the .com.au identity host; the
betting API itself is the global api.betfair.com.

The app key alone only identifies the application (X-Application header).
Every call also needs a session token (X-Authentication header) from this
module. Tokens are cached to disk so the scheduler's separate task runs
(00:30 / 08:00 / 10:00) share one login instead of three.

Env (in Race-Analytics/.env):
  BETFAIR_APP_KEY     application key (delayed key is fine for STRIDE)
  BETFAIR_USERNAME    betfair.com.au account username
  BETFAIR_PASSWORD    betfair.com.au account password
  BETFAIR_CERT_PATH   client certificate (default certs/betfair-client.crt)
  BETFAIR_KEY_PATH    client private key  (default certs/betfair-client.key)
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

# Betfair global sessions last ~8h idle; refresh well inside that so a token
# handed to a long pipeline run can't expire mid-flight.
TOKEN_MAX_AGE_SECONDS = 4 * 3600

_TOKEN_CACHE = Path(__file__).resolve().parent.parent / "intelligence" / "betfair_session.json"


class BetfairAuthError(Exception):
    """Raised with Betfair's loginStatus code so failures are diagnosable."""


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


def missing_config() -> list:
    cfg = _config()
    missing = [k for k in ("app_key", "username", "password") if not cfg[k] or cfg[k].startswith("your-")]
    for k in ("cert_path", "key_path"):
        if not Path(cfg[k]).exists():
            missing.append(k)
    return missing


def _read_cached_token() -> Optional[str]:
    try:
        cached = json.loads(_TOKEN_CACHE.read_text())
        if time.time() - cached["obtained_at"] < TOKEN_MAX_AGE_SECONDS:
            return cached["token"]
    except (OSError, ValueError, KeyError):
        pass
    return None


def _write_cached_token(token: str):
    _TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_CACHE.write_text(json.dumps({"token": token, "obtained_at": time.time()}))
    _TOKEN_CACHE.chmod(0o600)


def cert_login() -> str:
    cfg = _config()
    resp = requests.post(
        CERT_LOGIN_URL,
        data={"username": cfg["username"], "password": cfg["password"]},
        cert=(cfg["cert_path"], cfg["key_path"]),
        headers={"X-Application": cfg["app_key"],
                 "Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("loginStatus") != "SUCCESS":
        raise BetfairAuthError(f"cert login failed: {body.get('loginStatus')}")
    return body["sessionToken"]


def interactive_login() -> str:
    """Username/password login without a certificate. Betfair discourages it
    for bots (may trip CAPTCHA/2FA) — kept only so the chain can be tested
    before the certificate is uploaded to the Betfair account."""
    cfg = _config()
    resp = requests.post(
        INTERACTIVE_LOGIN_URL,
        data={"username": cfg["username"], "password": cfg["password"]},
        headers={"X-Application": cfg["app_key"], "Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("status", body.get("loginStatus")) != "SUCCESS":
        raise BetfairAuthError(f"interactive login failed: {body.get('error') or body.get('status') or body.get('loginStatus')}")
    return body.get("token") or body.get("sessionToken")


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


def get_session_token(force: bool = False, allow_interactive: bool = False) -> str:
    """Cached token if fresh, else a new cert login. Call with force=True
    after an INVALID_SESSION response from the betting API."""
    if not force:
        token = _read_cached_token()
        if token:
            return token

    missing = missing_config()
    if missing:
        raise BetfairAuthError(f"missing Betfair config: {', '.join(missing)} (see .env)")

    try:
        token = cert_login()
    except BetfairAuthError as e:
        # CERT_AUTH_REQUIRED means the .crt was never uploaded to the account
        if allow_interactive:
            print(f"  [betfair] {e} — falling back to interactive login", file=sys.stderr)
            token = interactive_login()
        else:
            raise

    _write_cached_token(token)
    return token
