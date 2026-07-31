#!/usr/bin/env python3
"""Punting Form API probe — Phase A of PUNTINGFORM_MIGRATION.md.

Read-only recon, run from a GitHub runner (or any machine with open egress):

  1. Fetches Punting Form's own machine-readable API reference
     (docs.puntingform.com.au publishes an llms.txt index) and prints it, so
     the confirmed endpoints land in the run log.
  2. Exercises candidate endpoint shapes with the real key to confirm base
     URL + auth style + a working meetings call for today (AEST).

Never prints the key (and redacts it from any URL it echoes).

    PUNTINGFORM_API_KEY=... python3 scripts/puntingform_probe.py
"""
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API_KEY = (os.environ.get("PUNTINGFORM_API_KEY") or "").strip()
UA = "Mozilla/5.0 (compatible; StrideRacing/1.0)"

DOCS_CANDIDATES = [
    "https://docs.puntingform.com.au/llms.txt",
    "https://docs.puntingform.com.au/openapi.json",
]

# Candidate API shapes seen across Punting Form's documented services; the
# probe reports which respond. {d} = date d-M-yyyy, {iso} = yyyy-MM-dd.
ENDPOINT_CANDIDATES = [
    "https://api.puntingform.com.au/v2/form/meetingslist?meetingDate={iso}",
    "https://api.puntingform.com.au/v2/form/meetingslist?meetingDate={d}",
    "https://www.puntingform.com.au/api/formdataservice/ExportMeetings/{d}",
    "https://api.puntingform.com.au/formdataservice/ExportMeetings/{d}",
    "https://old.puntingform.com.au/api/formdataservice/ExportMeetings/{d}",
]


def redact(text):
    return text.replace(API_KEY, "<KEY>") if API_KEY else text


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", e.read()[:400]
    except Exception as e:
        return None, "", f"{type(e).__name__}: {e}".encode()


def banner(t):
    print()
    print("=" * 72)
    print(t)
    print("=" * 72)


def main():
    if not API_KEY:
        print("PUNTINGFORM_API_KEY is not set — nothing to probe")
        return 2
    if API_KEY != os.environ.get("PUNTINGFORM_API_KEY", ""):
        print("note: key had leading/trailing whitespace; stripped for this run — re-save the secret without it")

    banner("1. Punting Form's own API reference (for the run log)")
    for url in DOCS_CANDIDATES:
        st, ctype, body = fetch(url)
        print(f"  GET {url} -> {st} {ctype}")
        if st == 200:
            text = body.decode("utf-8", "replace")
            print("-" * 72)
            print(text[:12000])
            if len(text) > 12000:
                print(f"... [{len(text) - 12000} more chars truncated]")
            print("-" * 72)
            break

    # AEST date (UTC+10) — race days roll over on Australian time.
    now_aest = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=10)
    d = f"{now_aest.day}-{now_aest.month}-{now_aest.year}"
    iso = now_aest.strftime("%Y-%m-%d")

    banner(f"2. Candidate endpoints with the real key (today AEST = {iso})")
    for base in ENDPOINT_CANDIDATES:
        url = base.format(d=d, iso=iso)
        sep = "&" if "?" in url else "?"
        for auth_label, full in [("apiKey param", f"{url}{sep}apiKey={API_KEY}"),
                                 ("ApiKey param", f"{url}{sep}ApiKey={API_KEY}")]:
            st, ctype, body = fetch(full)
            preview = redact(body.decode("utf-8", "replace")[:220]).replace("\n", " ")
            print(f"  [{auth_label}] {redact(url)}")
            print(f"      -> {st} {ctype[:40]}   {preview}")
            if st == 200:
                break

    print()
    print("PROBE COMPLETE — the working shape above feeds pf_client.py (Phase B).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
