#!/usr/bin/env python3
"""Runtime probe for the sliding Punting Form window wall.

DUPLICATED MINIMALLY from pf_verify_backfill.py (DM-K1, PR #25
pf/verify-backfill) because that branch is not merged when DM-K3 lands.
When both are on main, consolidate to ONE wall-probing helper — the verify
script, the historical/trials importers and any future ingestion workflow
must share it, since the wall slides daily and every caller needs the same
boundary.

Measured reality: the wall is ~31 days back, not the ~53 first assumed
(DM-K1 verification, 2026-08-01: meetingslist 400s before 2026-07-01).
Runtime probing is the only source of truth.
"""
import datetime

import pf_client

WALL_PROBE_MAX_CALLS = 8
WALL_SEARCH_SPAN_DAYS = 120  # 2**7 >= 120: endpoint check + 7 bisections = 8 calls


def probe_date(iso):
    """One meetingslist call: served (True) or beyond the wall (False).
    retries=1 keeps the search inside its call budget; only an HTTP 400
    means 'before the wall' — anything else is a real error."""
    try:
        pf_client.get("/form/meetingslist", {"meetingDate": iso}, retries=1)
        return True
    except pf_client.PFError as e:
        if "400" in str(e):
            return False
        raise


def find_wall(probe, lo_bad, hi_ok, max_calls=WALL_PROBE_MAX_CALLS):
    """Probe the sliding PF wall by binary search on the 400 boundary.

    probe(iso_date_str) -> True if the date is served. lo_bad must be before
    the wall (assumed 400), hi_ok after it (verified, 1 call). Returns
    (earliest_served_date, last_known_bad_date, calls_used); when max_calls
    runs out early the true wall lies in (last_bad, earliest_served]."""
    if not probe(hi_ok.isoformat()):
        raise RuntimeError(f"wall probe: even {hi_ok} is not served — cannot bound the search")
    calls = 1
    lo, hi = lo_bad, hi_ok
    while (hi - lo).days > 1 and calls < max_calls:
        mid = lo + (hi - lo) // 2
        if probe(mid.isoformat()):
            hi = mid
        else:
            lo = mid
        calls += 1
    return hi, lo, calls


def today_aest():
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=10)).date()
