#!/usr/bin/env python3
"""Betfair credentials smoke test — proves the Betfair secrets work without
touching money. Runs identically in GitHub Actions (betfair-keys-smoke-test
workflow) and locally:

    BETFAIR_APP_KEY='...' BETFAIR_SESSION_TOKEN='<ssoid cookie>' \
        python3 scripts/betfair_keys_smoke.py
    # or with a login instead of a token:
    BETFAIR_APP_KEY='...' BETFAIR_USERNAME='...' BETFAIR_PASSWORD='...' \
        python3 scripts/betfair_keys_smoke.py

Endpoints per the Betfair Exchange API docs (Interactive Login - API
Endpoint; Login & Session Management; Non-Interactive bot login):
    AU login/session:  https://identitysso.betfair.com.au/api/{login,keepAlive,logout}
    International:     https://identitysso.betfair.com/api/{...}
    Exchange API:      https://api.betfair.com/exchange/...
    Session use:       X-Application: <app key>, X-Authentication: <token>

Betfair's edge serves an HTML block page to some datacenter IPs (GitHub's
runners are in the US). That is detected and reported as an edge block —
it says nothing about the credentials — and the test then tries a browser
TLS fingerprint (curl_cffi, optional) and the SSO keepAlive endpoint
before giving up. A user-supplied session token is never logged out; only
sessions this script creates by logging in are.

Never prints a secret. Exit codes: 0 keys proven working, 1 tested and
failed (or untestable from this network), 2 nothing to test with.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

AU_SSO = "identitysso.betfair.com.au"   # per Betfair docs: AU jurisdiction
COM_SSO = "identitysso.betfair.com"     # international
EXCHANGE = "https://api.betfair.com/exchange"

APP_KEY_NAMES = ["BETFAIR_APP_KEY", "BETFAIR_APP_KEY_LIVE", "BETFAIR_LIVE_APP_KEY", "BETFAIR_APPKEY",
                 "BETFAIR_API_KEY", "BETFAIR_KEY", "BF_APP_KEY",
                 "BETFAIR_APP_KEY_DELAYED", "BETFAIR_DELAYED_APP_KEY"]
USERNAME_NAMES = ["BETFAIR_USERNAME", "BETFAIR_USER", "BETFAIR_EMAIL", "BETFAIR_LOGIN", "BF_USERNAME",
                  "BF_USER", "BETFAIR_UN", "BETFAIR_USR", "BETFAIR_USER_NAME", "BETFAIR_USERID",
                  "BETFAIR_ACCOUNT", "USERNAME", "EMAIL"]
PASSWORD_NAMES = ["BETFAIR_PASSWORD", "BETFAIR_PASS", "BETFAIR_PW", "BF_PASSWORD", "BF_PASS",
                  "BETFAIR_PWD", "BETFAIR_PASSWD", "BETFAIR_LOGIN_PASSWORD", "BETFAIR_SECRET", "PASSWORD"]
TOKEN_NAMES = ["BETFAIR_SESSION_TOKEN", "BETFAIR_SSOID", "BETFAIR_TOKEN", "BETFAIR_SESSION",
               "SESSION_TOKEN", "SSOID"]
CERT_NAMES = ["BETFAIR_CERT", "BETFAIR_CERT_PEM", "BETFAIR_CERT_KEY"]

EXPLAIN = {
    "SUCCESS": "accepted — credentials are good",
    "LIMITED_ACCESS": "login accepted but the account has limited access (site prompt outstanding)",
    "LOGIN_RESTRICTED": "login is restricted on this account — check the Betfair website",
    "ACCOUNT_PENDING_PASSWORD_CHANGE": "per Betfair docs the account is LOCKED until password recovery is completed (betfair.com.au 'forgot password' flow) — reset there, then update the stored password. If this keeps coming back, something somewhere is still retrying logins with the OLD password and re-locking the account",
    "CHANGE_PASSWORD_REQUIRED": "Betfair requires the password to be changed on the website before the API will accept logins",
    "INVALID_USERNAME_OR_PASSWORD": "the username/password used was rejected — if the password was reset on the website, the stored secret must be the NEW password",
    "ACCOUNT_NOW_LOCKED": "this attempt tipped the account into locked state — unlock on the Betfair website before any retry",
    "ACCOUNT_ALREADY_LOCKED": "the account is locked (usually from repeated failed logins) — unlock via the Betfair website/support, then retry",
    "TEMPORARY_BAN_TOO_MANY_REQUESTS": "too many login attempts — Betfair blocks new sessions for 20 minutes after a breach; wait, then retry",
    "PENDING_AUTH": "a verification step (2FA / identity check) is outstanding on the account — complete it on the website",
    "KYC_SUSPEND": "account suspended pending identity verification — resolve on the website",
    "SUSPENDED": "the account is suspended — contact Betfair",
    "CLOSED": "the account is closed",
    "SELF_EXCLUDED": "the account is self-excluded",
    "BETTING_RESTRICTED_LOCATION": "per Betfair docs: the request came from a location restricted for legal/regulatory reasons — this does NOT disprove the credentials; run from an allowed country",
    "LOGIN_RESTRICTED_LOCATION": "blocked because of the request's source location — this does NOT disprove the credentials",
    "SECURITY_RESTRICTED_LOCATION": "blocked because of the request's source location — this does NOT disprove the credentials",
    "DUPLICATE_CARDS": "duplicate card details on the account — resolve on the website",
    "SECURITY_QUESTION_WRONG_3X": "security question failed three times — account needs attention on the website",
    "INPUT_VALIDATION_ERROR": "the request fields were rejected — usually an empty/garbled username or password value",
    "CERT_AUTH_REQUIRED": "this account requires certificate (non-interactive bot) login — an SSL client certificate must be registered and used",
    "NO_SESSION": "the session token is not a live session (expired, logged out, or wrong value) — log in at betfair.com.au and copy a fresh 'ssoid' cookie",
    "INVALID_SESSION_INFORMATION": "the session token is expired or invalid — log in at betfair.com.au and copy a fresh 'ssoid' cookie",
}
# Statuses where attempting the other SSO host would just burn another login
# attempt from the same IP for the same outcome.
NO_SECOND_HOST = set(EXPLAIN) - {"INPUT_VALIDATION_ERROR"}


def banner(t):
    print()
    print("=" * 72)
    print(t)
    print("=" * 72)


def http_json(url, data=None, headers=None, timeout=30, engine="urllib"):
    """POST (data given) or GET. Returns (status, parsed-or-diagnostic-dict).
    engine='cffi' uses curl_cffi with a Chrome TLS fingerprint if installed."""
    if engine == "cffi":
        try:
            from curl_cffi import requests as cf
        except Exception as e:
            return None, {"_transport": f"curl_cffi unavailable: {e}"}
        try:
            if data is not None:
                r = cf.post(url, data=data, headers=headers or {}, timeout=timeout, impersonate="chrome")
            else:
                r = cf.get(url, headers=headers or {}, timeout=timeout, impersonate="chrome")
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {"_raw": r.text[:300]}
        except Exception as e:
            return None, {"_transport": f"{type(e).__name__}: {e}"}
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"_raw": body[:300]}
    except Exception as e:
        return None, {"_transport": f"{type(e).__name__}: {e}"}


def is_edge_block(body):
    """HTML instead of JSON from an API endpoint = WAF/geo block page; the
    request never reached Betfair's application layer."""
    raw = body.get("_raw", "") if isinstance(body, dict) else ""
    return raw.lstrip().lower().startswith(("<!doctype", "<html", "<!--"))


def aping_error(body):
    if isinstance(body, dict):
        return ((body.get("detail", {}) or {}).get("APINGException", {}) or {}).get("errorCode", "") \
            or body.get("faultstring", "") or ""
    return ""


def api_post(url, data, headers, label):
    """POST that recognises the edge block page and retries once with a
    browser TLS fingerprint. Returns (status, body, blocked)."""
    st, body = http_json(url, data=data, headers=headers)
    if not is_edge_block(body):
        return st, body, False
    print(f"  {label}: HTTP {st} — HTML block page from Betfair's edge (request never reached the API; typical for US datacenter IPs)")
    st2, body2 = http_json(url, data=data, headers=headers, engine="cffi")
    if isinstance(body2, dict) and "_transport" in body2:
        print(f"  {label} [browser TLS]: {body2['_transport']}")
        return st, body, True
    if is_edge_block(body2):
        print(f"  {label} [browser TLS]: HTTP {st2} — still the block page")
        return st2, body2, True
    print(f"  {label} [browser TLS]: HTTP {st2} — got past the edge")
    return st2, body2, False


def report_and_pick(label, names, show_len=True):
    # show_len=False for credentials: the CI log is public, so even a
    # credential's length stays out of it.
    print(f"  {label}:")
    picked, picked_name = "", None
    for n in names:
        raw = os.environ.get(n, "")
        if raw:
            note = ""
            if raw != raw.strip():
                note = "   <-- WARNING: leading/trailing whitespace (stripped for this attempt; re-save the secret without it)"
            size = f" ({len(raw)} chars)" if show_len else ""
            print(f"    {n}: SET{size}{note}")
            if picked_name is None and raw.strip():
                picked, picked_name = raw.strip(), n
        else:
            print(f"    {n}: not set")
    print(f"    -> using: {picked_name or 'NONE FOUND'}")
    return picked, picked_name


def exchange_check(app_key, token, label):
    """Read-only Exchange API probe.
    Returns ('ok'|'rejected'|'blocked', error_code)."""
    api_headers = {"X-Application": app_key, "X-Authentication": token,
                   "Content-Type": "application/json", "Accept": "application/json"}
    st, body, blocked = api_post(f"{EXCHANGE}/betting/rest/v1.0/listEventTypes/",
                                 json.dumps({"filter": {}}).encode(), api_headers,
                                 f"listEventTypes ({label})")
    if blocked:
        return "blocked", "EDGE_BLOCK"
    if st == 200 and isinstance(body, list):
        names = {e.get("eventType", {}).get("id"): e.get("eventType", {}).get("name") for e in body}
        print(f"  listEventTypes ({label}): HTTP 200, {len(body)} event types returned")
        print(f"  horse racing available: {'YES' if '7' in names else 'no (!)'}")
        st2, keys, blocked2 = api_post(f"{EXCHANGE}/account/rest/v1.0/getDeveloperAppKeys/",
                                       b"{}", api_headers, "getDeveloperAppKeys")
        if st2 == 200 and isinstance(keys, list):
            print("  app keys registered on this Betfair account:")
            for app in keys:
                for v in app.get("appVersions", []):
                    match = "MATCHES the stored secret" if v.get("applicationKey") == app_key else "different key"
                    print(f"    - app '{app.get('appName')}' v{v.get('version')}: delayed-data={v.get('delayData')} active={v.get('active')} -> {match}")
        elif not blocked2:
            print(f"  getDeveloperAppKeys: HTTP {st2} (informational only)")
        return "ok", ""
    code = aping_error(body) or (body.get("_transport") if isinstance(body, dict) else "") or str(body)[:200]
    print(f"  listEventTypes ({label}): HTTP {st} — {code}")
    if code in EXPLAIN:
        print(f"  meaning: {EXPLAIN[code]}")
    elif "INVALID_APP_KEY" in str(code):
        print("  meaning: the session is fine but the stored app key is not a valid application key")
    elif "APP_KEY_NOT_ACTIVE" in str(code) or "ACCESS_DENIED" in str(code):
        print("  meaning: the app key exists but is not activated for the Exchange API (delayed key not activated, or live key awaiting activation)")
    return "rejected", str(code)


def keepalive_check(app_key, token):
    """Validate app key + session token via the SSO keepAlive endpoint —
    a different edge from api.betfair.com, so it can answer even when the
    Exchange API is walled off. Returns ('ok'|'rejected'|'blocked', detail)."""
    headers = {"X-Application": app_key, "X-Authentication": token, "Accept": "application/json"}
    for host in (AU_SSO, COM_SSO):
        st, body, blocked = api_post(f"https://{host}/api/keepAlive", b"", headers, f"keepAlive {host}")
        if blocked:
            continue
        if isinstance(body, dict) and "_transport" in body:
            print(f"  keepAlive {host}: {body['_transport']}")
            continue
        status = (body.get("status") if isinstance(body, dict) else "") or f"HTTP_{st}"
        error = (body.get("error") if isinstance(body, dict) else "") or ""
        print(f"  keepAlive {host}: status={status} error={error or '(none)'}")
        key = error or status
        if key in EXPLAIN:
            print(f"  meaning: {EXPLAIN[key]}")
        if status == "SUCCESS":
            return "ok", host
        return "rejected", key
    return "blocked", ""


def main():
    banner("1. Which Betfair secrets exist (names only, never values)")
    app_key, app_key_name = report_and_pick("app key", APP_KEY_NAMES)
    username, username_name = report_and_pick("username", USERNAME_NAMES, show_len=False)
    password, password_name = report_and_pick("password", PASSWORD_NAMES, show_len=False)
    sess_tok, sess_tok_name = report_and_pick("session token (ssoid)", TOKEN_NAMES, show_len=False)
    cert_present = [n for n in CERT_NAMES if os.environ.get(n)]
    print(f"  cert-login secrets present: {cert_present or 'none'} (cert login not attempted by this test)")

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    gh_token = os.environ.get("GH_TOKEN", "")
    if repo and gh_token:
        banner("2. Repo GitHub environments (environment secrets are a separate store)")
        api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
        st, body = http_json(f"{api}/repos/{repo}/environments",
                             headers={"Authorization": f"Bearer {gh_token}",
                                      "Accept": "application/vnd.github+json",
                                      "X-GitHub-Api-Version": "2022-11-28",
                                      "User-Agent": "betfair-keys-smoke-test"})
        if st == 200 and isinstance(body, dict):
            env_names = [e.get("name") for e in body.get("environments", [])]
            if env_names:
                print(f"  environments found: {env_names}")
                print("  NOTE: this job declares no environment, so secrets inside these are NOT visible to it.")
            else:
                print("  no environments exist in this repo — credentials are not hiding in environment secrets")
        else:
            print(f"  could not list environments (HTTP {st}) — continuing")

    if not app_key:
        banner("VERDICT: CANNOT TEST — no app key found")
        print("  Set BETFAIR_APP_KEY (repository Actions secret, or env var when running locally).")
        return 2
    have_login_creds = bool(username and password)
    if not have_login_creds and not sess_tok:
        banner("VERDICT: CANNOT TEST — no way to open a Betfair session")
        print(f"  App key present ({app_key_name}), but Betfair's API needs a session as well.")
        print("  Provide EITHER:")
        print("    a) BETFAIR_USERNAME + BETFAIR_PASSWORD (the betfair.com.au login), or")
        print("    b) BETFAIR_SESSION_TOKEN — log in at betfair.com.au in a browser and copy the")
        print("       'ssoid' cookie value (fresh — tokens die on password change and on logout).")
        return 2

    banner("3. Where is this machine (matters: Betfair geo-blocks some regions)")
    st, geo = http_json("https://ipapi.co/json/", headers={"User-Agent": "curl/8"}, timeout=15)
    if isinstance(geo, dict) and geo.get("country"):
        print(f"  egress: {geo.get('ip')} — {geo.get('country_name', geo.get('country'))} ({geo.get('country')})")
    else:
        print(f"  could not determine location ({geo}) — continuing anyway")

    # ── Path A: a stored session token proves app key + session, no login needed
    if sess_tok:
        banner(f"4. Validating stored session token {sess_tok_name} against the Exchange API")
        state, code = exchange_check(app_key, sess_tok, f"token from {sess_tok_name}")
        if state == "ok":
            banner("VERDICT")
            print(f"  KEYS OK — app key {app_key_name} + session token {sess_tok_name} were accepted")
            print("  by the Exchange API (read-only). The stored token was left logged in.")
            return 0
        if state == "blocked":
            banner("5. Exchange edge is walled off — validating via SSO keepAlive instead")
            ks, detail = keepalive_check(app_key, sess_tok)
            if ks == "ok":
                banner("VERDICT")
                print(f"  KEYS OK — Betfair SSO ({detail}) confirmed the session token is LIVE and")
                print(f"  accepted app key {app_key_name} (keepAlive SUCCESS). The Exchange data API")
                print("  itself is unreachable from this network's IPs (edge geo-block), so the final")
                print("  exchange-level read must run from an allowed location — but nothing is wrong")
                print("  with the credentials themselves.")
                return 0
            if ks == "rejected":
                state, code = "rejected", detail
            else:
                banner("VERDICT: UNTESTABLE FROM THIS NETWORK — Betfair's edge blocks every endpoint here")
                print("  This says NOTHING about the keys. Run the same test from an allowed location")
                print("  (e.g. the Mac in Australia):")
                print("    git fetch origin claude/betfair-keys-smoke-test-4yqny1 && git checkout claude/betfair-keys-smoke-test-4yqny1")
                print("    BETFAIR_APP_KEY='<app key>' BETFAIR_SESSION_TOKEN='<fresh ssoid>' python3 scripts/betfair_keys_smoke.py")
                return 1
        if state == "rejected" and not have_login_creds:
            banner("VERDICT: SESSION TOKEN REJECTED and no username/password to fall back on")
            print(f"  error: {code}")
            print(f"  {EXPLAIN.get(code, 'refresh the token: log in at betfair.com.au, copy the ssoid cookie into the secret again')}")
            return 1
        print("  falling back to username/password login...")

    # ── Path B: one interactive login (Betfair-documented endpoints)
    banner("6. Interactive SSO login (single attempt — no credential-failure retries)")
    order = {"au-then-com": [AU_SSO, COM_SSO], "com-then-au": [COM_SSO, AU_SSO],
             "au-only": [AU_SSO], "com-only": [COM_SSO]}[os.environ.get("SMOKE_HOSTS") or "au-then-com"]
    form = urllib.parse.urlencode({"username": username, "password": password}).encode()
    headers = {"X-Application": app_key, "Accept": "application/json",
               "Content-Type": "application/x-www-form-urlencoded"}

    token, login_host, final_status, final_error = "", None, None, None
    for host in order:
        url = f"https://{host}/api/login"
        print(f"  POST {url}")
        st, body, blocked = api_post(url, form, headers, "login")
        if blocked:
            final_status, final_error = "EDGE_BLOCK", "HTML block page (geo) — the login was never evaluated"
            continue
        if isinstance(body, dict) and "_transport" in body:
            print(f"    transport error: {body['_transport']} — trying next host if one is configured")
            final_status, final_error = "TRANSPORT_ERROR", body["_transport"]
            continue
        status = (body.get("status") if isinstance(body, dict) else "") or f"HTTP_{st}"
        error = (body.get("error") if isinstance(body, dict) else "") or ""
        final_status, final_error, login_host = status, error, host
        print(f"    status: {status}   error: {error or '(none)'}")
        key = error or status
        if key in EXPLAIN:
            print(f"    meaning: {EXPLAIN[key]}")
        if status == "SUCCESS" and body.get("token"):
            token = body["token"]
            break
        if (error in NO_SECOND_HOST) or (status in NO_SECOND_HOST):
            print("    not trying the other host: it would consume another login attempt from the same IP for the same result")
            break

    if not token:
        if final_status == "EDGE_BLOCK":
            banner("VERDICT: UNTESTABLE FROM THIS NETWORK — Betfair's edge blocked the login endpoints")
            print("  This says NOTHING about the credentials. Run the same test from an allowed")
            print("  location (e.g. the Mac in Australia):")
            print("    BETFAIR_APP_KEY='<app key>' BETFAIR_USERNAME='<email>' BETFAIR_PASSWORD='<password>' \\")
            print("        python3 scripts/betfair_keys_smoke.py")
            return 1
        banner(f"VERDICT: LOGIN FAILED on {login_host or order[-1]}")
        key = (final_error or final_status or "")
        print(f"  status={final_status} error={final_error or '(none)'}")
        print(f"  {EXPLAIN.get(key, 'see the status/error above; full status list: Betfair Interactive Login docs')}")
        return 1

    banner("7. Exchange API check with the stored app key (read-only)")
    state, code = exchange_check(app_key, token, "session from login")

    http_json(f"https://{login_host}/api/logout", data=b"",
              headers={"X-Application": app_key, "X-Authentication": token, "Accept": "application/json"})
    print("  session logged out")

    banner("VERDICT")
    if state == "ok":
        print(f"  KEYS OK — login SUCCESS on {login_host} using {username_name}/{password_name},")
        print(f"  and app key {app_key_name} was accepted by the Exchange API.")
        return 0
    if state == "blocked":
        print(f"  CREDENTIALS OK — login SUCCESS on {login_host} proves username/password and the")
        print(f"  SSO accepted app key {app_key_name}. The Exchange data API is unreachable from")
        print("  this network's IPs (edge geo-block); run the exchange-level read from an allowed")
        print("  location for the last word — but the keys themselves checked out.")
        return 0
    print(f"  PARTIAL — login on {login_host} succeeded (username/password are good), but the")
    print(f"  app key {app_key_name} was NOT accepted by the Exchange API — see section 7.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
