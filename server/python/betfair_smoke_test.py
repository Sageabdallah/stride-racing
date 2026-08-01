#!/usr/bin/env python3
"""Betfair auth chain smoke test: login -> listEventTypes.

Proves app key + session token + betting API all work together before any
real ingestion is built on top. Run from server/python:

  python3 betfair_smoke_test.py                 # cert login (production path)
  python3 betfair_smoke_test.py --interactive   # before the cert is uploaded
"""

import argparse
import sys

import requests

from providers.betfair_auth import (
    BetfairAuthError, get_session_token, missing_config, _config,
)

BETTING_API_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"


def list_event_types(app_key: str, token: str):
    resp = requests.post(
        BETTING_API_URL,
        json=[{
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listEventTypes",
            "params": {"filter": {}},
            "id": 1,
        }],
        headers={"X-Application": app_key, "X-Authentication": token,
                 "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()[0]
    if "error" in body:
        raise RuntimeError(f"betting API error: {body['error']}")
    return body["result"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interactive", action="store_true",
                        help="fall back to interactive login if cert login fails")
    args = parser.parse_args()

    print("=" * 55)
    print("  BETFAIR AUTH SMOKE TEST")
    print("=" * 55)

    missing = missing_config()
    if missing:
        print(f"\n  NOT CONFIGURED — missing: {', '.join(missing)}")
        print("  Fill the BETFAIR_* entries in Race-Analytics/.env")
        sys.exit(2)

    try:
        print("\n  1. Login...", end=" ", flush=True)
        token = get_session_token(force=True, allow_interactive=args.interactive)
        print(f"OK (token {token[:6]}...)")
    except BetfairAuthError as e:
        print(f"FAILED\n\n  {e}")
        if "CERT_AUTH_REQUIRED" in str(e):
            print("  -> The client certificate is not uploaded to your Betfair account yet.")
            print("     Upload certs/betfair-client.crt at:")
            print("     https://myaccount.betfair.com/accountdetails/mysecurity?showAPI=1")
            print("     (Automated Betting Program Access -> Edit)")
        sys.exit(1)

    try:
        print("  2. listEventTypes...", end=" ", flush=True)
        event_types = list_event_types(_config()["app_key"], token)
        print(f"OK ({len(event_types)} event types)")
    except Exception as e:
        print(f"FAILED\n\n  {e}")
        sys.exit(1)

    horse_racing = next(
        (et for et in event_types if et["eventType"]["name"] == "Horse Racing"), None)
    print()
    for et in event_types[:5]:
        print(f"     {et['eventType']['id']:>6}  {et['eventType']['name']}  ({et['marketCount']} markets)")
    if horse_racing:
        print(f"\n  Horse Racing found: eventType id {horse_racing['eventType']['id']}, "
              f"{horse_racing['marketCount']} open markets")
        print("\n  AUTH CHAIN VERIFIED — ready to build odds ingestion")
    else:
        print("\n  WARNING: Horse Racing not in event types — check account jurisdiction")
        sys.exit(1)


if __name__ == "__main__":
    main()
