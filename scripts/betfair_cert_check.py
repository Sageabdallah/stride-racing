#!/usr/bin/env python3
"""Prove the Betfair cert login works — one attempt, then stop.

Run this the moment the .crt is registered on the account. It answers one
question: does cert login succeed now? It is deliberately not wired into
any job.

ONE login attempt, never a retry loop. This account has been locked
before, and betfair_auth's own block-marker exists because a terminal
login status must not be hammered. If a previous run recorded a block,
this refuses to try at all rather than spending another attempt.

Nothing is materialised on AWS by this script. That ordering is
deliberate: stride-late-odds-watch is a Lambda firing every 5 minutes
across the racing window, and its read-only /var/task means the session
token never persists, so every invocation logs in fresh. Shipping cert
files to AWS before the upload is registered would turn each of those
into a rejected cert login plus an interactive fallback — roughly 190
credential submissions a day against an account with a lockout history.
Prove it here first.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "python"))

# Load .env the same way the pipeline does, so this needs no exports.
ENV = ROOT / ".env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# A user-supplied session token would make cert login succeed without
# proving anything about the certificate, which is the whole question.
os.environ.pop("BETFAIR_SESSION_TOKEN", None)


def main() -> int:
    from providers import betfair_auth as ba

    cert = os.environ.get("BETFAIR_CERT_PATH", "")
    key = os.environ.get("BETFAIR_KEY_PATH", "")
    for label, p in (("cert", cert), ("key", key)):
        full = p if os.path.isabs(p) else str(ROOT / p)
        if not os.path.exists(full):
            print(f"FAIL: {label} file not found at {full}")
            return 2
        print(f"  {label}: {full}")

    if not ba._cert_files_exist():
        print("FAIL: betfair_auth does not see the cert pair; check "
              "BETFAIR_CERT_PATH / BETFAIR_KEY_PATH are relative to the "
              "repo root and that the process cwd is the repo root.")
        return 2

    missing = ba.missing_config()
    if missing:
        print(f"FAIL: missing config: {', '.join(missing)}")
        return 2

    try:
        ba._refuse_if_blocked()
    except Exception as e:
        print(f"REFUSING to attempt: a previous run recorded a terminal "
              f"login block ({e}). Clear the cause before retrying — "
              f"another attempt spends a login against a lockout-prone "
              f"account.")
        return 4

    print("\nattempting ONE cert login...")
    try:
        token = ba.cert_login()
    except Exception as e:
        name = type(e).__name__
        print(f"\nFAIL ({name}): {e}")
        if "CERT_AUTH_REQUIRED" in str(e):
            print("\n  This is the pre-upload state: Betfair does not yet "
                  "have this certificate registered against the account. "
                  "The .crt still needs uploading.")
        return 3

    # Never print the token. Its length is enough to show one came back.
    print(f"\nCERT LOGIN PROVEN — session token received ({len(token)} chars)")
    print("Nothing was changed on AWS. Report this result and the cert "
          "materialisation can be deployed next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
