#!/usr/bin/env python3
"""Measure the OBSERVED data delay of the configured Betfair app key.

Why measured, not quoted: the stored key is v1.0-DELAY and the retrain
window opened 2026-08-02, so whether it stays or the live key is activated
is a today decision — made on evidence from the endpoints the pipeline
actually calls, not Betfair's documentation.

What is measurable without a live key to compare against:
  1. isMarketDataDelayed on every listMarketBook response — the API's own
     per-response statement that this key serves delayed data.
  2. Update cadence: poll the most active upcoming AU thoroughbred (or any
     racing) markets every POLL_S seconds and record the inter-update
     interval of price/volume changes. A delayed key conflates the live
     stream into periodic snapshots; the observed refresh interval IS the
     effective staleness bound per request.
  3. The same cadence inside a late-odds-style window (near jump), where
     the watcher polls on a 5-minute grid.

One login, session kept alive, read-only calls, logout at the end.
Output: human-readable report to stdout (the workflow log is the record).
"""

import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

IDENTITY = "https://identitysso.betfair.com.au/api/login"
API = "https://api.betfair.com/exchange/betting/rest/v1.0/"
POLL_S = float(os.environ.get("PROBE_POLL_SECONDS", "2.0"))
WINDOW_S = int(os.environ.get("PROBE_WINDOW_SECONDS", "300"))
N_MARKETS = 3


def _post(url, body, headers):
    req = urllib.request.Request(url, data=body.encode(), headers=headers,
                                 method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def login():
    body = (f"username={urllib.parse.quote(os.environ['BETFAIR_USERNAME'])}"
            f"&password={urllib.parse.quote(os.environ['BETFAIR_PASSWORD'])}")
    out = _post(IDENTITY, body, {
        "X-Application": os.environ["BETFAIR_APP_KEY"],
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"})
    if out.get("status") != "SUCCESS":
        print(f"LOGIN FAILED: {out.get('status')} {out.get('error')} — "
              "single attempt, not retrying", file=sys.stderr)
        sys.exit(2)
    return out["token"]


def api(token, method, payload):
    return _post(API + method + "/", json.dumps(payload), {
        "X-Application": os.environ["BETFAIR_APP_KEY"],
        "X-Authentication": token,
        "Content-Type": "application/json", "Accept": "application/json"})


def pick_markets(token):
    """Nearest not-yet-jumped AU racing win markets; fall back to any
    racing market currently trading so the cadence is measurable on a
    quiet evening."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for event_types, country in ((["7"], "AU"), (["7"], None),
                                 (["4339", "7"], None)):
        f = {"eventTypeIds": event_types, "marketTypeCodes": ["WIN"],
             "marketStartTime": {"from": now}}
        if country:
            f["marketCountries"] = [country]
        cats = api(token, "listMarketCatalogue", {
            "filter": f, "maxResults": N_MARKETS,
            "sort": "FIRST_TO_START",
            "marketProjection": ["EVENT", "MARKET_START_TIME"]})
        if cats:
            return cats
    return []


def probe(token, market_ids):
    changes = {m: [] for m in market_ids}   # timestamps of observed updates
    delayed_flags = set()
    last = {}
    t_end = time.time() + WINDOW_S
    polls = 0
    while time.time() < t_end:
        book = api(token, "listMarketBook", {
            "marketIds": market_ids,
            "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}})
        polls += 1
        now = time.time()
        for mb in book:
            delayed_flags.add(bool(mb.get("isMarketDataDelayed")))
            sig = json.dumps({
                "tm": mb.get("totalMatched"),
                "rn": [(r.get("selectionId"),
                        (r.get("ex", {}).get("availableToBack") or [{}])[:1],
                        r.get("totalMatched"))
                       for r in mb.get("runners", [])]}, sort_keys=True)
            mid = mb["marketId"]
            if last.get(mid) is not None and sig != last[mid]:
                changes[mid].append(now)
            last[mid] = sig
        time.sleep(POLL_S)
    return polls, delayed_flags, changes


def main():
    token = login()
    try:
        cats = pick_markets(token)
        if not cats:
            print("NO ACTIVE MARKETS FOUND — rerun when racing is trading "
                  "(cadence unmeasurable right now; the delayed flag still "
                  "needs an open market).")
            return 3
        ids = [c["marketId"] for c in cats]
        for c in cats:
            print(f"market {c['marketId']}: {c.get('event', {}).get('name')} "
                  f"- {c.get('marketName', 'WIN')} "
                  f"start {c.get('marketStartTime')}")
        print(f"polling every {POLL_S}s for {WINDOW_S}s ...")
        polls, delayed_flags, changes = probe(token, ids)

        print("\n==== RESULT ====")
        print(f"isMarketDataDelayed on responses: {sorted(delayed_flags)}")
        all_gaps = []
        for mid, ts in changes.items():
            gaps = [round(b - a, 1) for a, b in zip(ts, ts[1:])]
            all_gaps += gaps
            if gaps:
                print(f"{mid}: {len(ts)} updates over {WINDOW_S}s; "
                      f"inter-update gaps s: min {min(gaps)}, "
                      f"median {statistics.median(gaps)}, max {max(gaps)}")
            else:
                print(f"{mid}: {len(ts)} update(s) — too quiet to measure")
        if all_gaps:
            print(f"OVERALL effective refresh interval: median "
                  f"{statistics.median(all_gaps)}s, p90 "
                  f"{sorted(all_gaps)[int(len(all_gaps) * 0.9) - 1]}s "
                  f"over {len(all_gaps)} update gaps, {polls} polls")
        else:
            print("OVERALL: markets too quiet for a cadence estimate — "
                  "rerun closer to a jump.")
        return 0
    finally:
        try:
            _post("https://identitysso.betfair.com.au/api/logout", "", {
                "X-Application": os.environ["BETFAIR_APP_KEY"],
                "X-Authentication": token, "Accept": "application/json"})
            print("session logged out")
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
