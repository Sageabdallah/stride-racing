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


# Betfair's certlogin requires a CLIENT certificate. `openssl req -x509`
# defaults to basicConstraints CA:TRUE, which produces a CA certificate
# that is refused at the endpoint — the 2026-07-31 pair failed exactly
# this way and read as "not uploaded" for two days. scripts/betfair-client-openssl.cnf
# pins these four; this is the same list, asserted at check time.
REQUIRED_EXTENSIONS = (
    ("CA:FALSE", "basicConstraints = CA:FALSE"),
    ("SSL Client", "nsCertType = client"),
    ("Digital Signature", "keyUsage = digitalSignature"),
    ("Key Encipherment", "keyUsage = keyEncipherment"),
    ("TLS Web Client Authentication", "extendedKeyUsage = clientAuth"),
)


def _cert_text(path: str) -> str:
    import subprocess
    try:
        return subprocess.run(["openssl", "x509", "-in", path, "-noout",
                               "-text"], capture_output=True, text=True,
                              timeout=30).stdout
    except Exception as e:
        return f"(could not read certificate: {e})"


def _print_extension_check(path: str = None) -> bool:
    """Print the extension block verdict. True only if every line PASSes."""
    path = path or _cert_path()
    text = _cert_text(path)
    ok = True
    for needle, label in REQUIRED_EXTENSIONS:
        hit = needle in text
        ok = ok and hit
        print(f"    {'PASS' if hit else 'FAIL'}  {label}")
    if "CA:TRUE" in text:
        ok = False
        print("    FAIL  basicConstraints is CA:TRUE — this is a CA "
              "certificate, not a client certificate, and Betfair will "
              "refuse it no matter how often it is uploaded")
    return ok


def _cert_path() -> str:
    p = os.environ.get("BETFAIR_CERT_PATH", "certs/betfair-client.crt")
    return p if os.path.isabs(p) else str(ROOT / p)


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

    # Checked BEFORE the login attempt: a certificate Betfair cannot accept
    # is knowable locally, and spending an attempt against a lockout-prone
    # account to learn it is the wrong order.
    print("\ncertificate extensions:")
    if not _print_extension_check():
        print("\nFAIL: this certificate cannot succeed at certlogin "
              "regardless of upload state. Regenerate with "
              "scripts/betfair-client-openssl.cnf, then upload the NEW .crt.")
        return 5

    try:
        ba._refuse_if_blocked()
    except Exception as e:
        print(f"REFUSING to attempt: a previous run recorded a terminal "
              f"login block ({e}). Clear the cause before retrying — "
              f"another attempt spends a login against a lockout-prone "
              f"account.")
        return 4

    print("\nattempting ONE cert login...")
    print(f"  endpoint: {ba.CERT_LOGIN_URL}")
    try:
        token = ba.cert_login()
    except Exception as e:
        name = type(e).__name__
        print(f"\nFAIL ({name}): {e}")
        if "CERT_AUTH_REQUIRED" in str(e):
            # This message previously asserted "pre-upload state" as fact.
            # It cannot know that. CERT_AUTH_REQUIRED is returned for BOTH
            # a certificate that was never uploaded AND one Betfair rejects
            # on its contents, and the 2026-07-31 pair was the second case:
            # generated with basicConstraints CA:TRUE and no nsCertType, so
            # it was refused however many times it was uploaded. Stating
            # one cause sent the operator to check the upload for a defect
            # that was in the file. List both, and print what is locally
            # checkable so the reader can rule one out immediately.
            print("\n  CERT_AUTH_REQUIRED has TWO causes and this script "
                  "cannot tell them apart from the response alone:")
            print("    (a) the .crt is not registered on the Betfair account")
            print("    (b) it is registered but Betfair rejects its contents")
            print("\n  Local extension check (rules out (b) if all PASS):")
            _print_extension_check()
        return 3

    # Never print the token. Its length is enough to show one came back.
    print(f"\nCERT LOGIN PROVEN — session token received ({len(token)} chars)")
    print("Nothing was changed on AWS. Report this result and the cert "
          "materialisation can be deployed next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
