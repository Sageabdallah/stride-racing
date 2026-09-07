#!/usr/bin/env python3
"""Phase E probe: does the Starter subscription actually support wiring
Conditions / Speedmaps / Ratings / Strike Rates, and what does flucs look
like? `PUNTINGFORM_MIGRATION.md` Phase E lists these four endpoints as
ranked, unwired levers; `pf_client.py` has typed, contract-tested accessors
for all four, and nothing has ever called them against the live API — the
only caller anywhere is a monkeypatched unit test
(`providers/test_puntingform.py::test_phase_e_accessor_contracts`), which
asserts the path and params sent and never opens a socket. The endpoint
paths and payload shapes were pinned from Punting Form's published
reference pages (2026-08-01), not confirmed against the live API. This
probe is that confirmation, before any feature-wiring code gets written.

Read-only. Calls through `pf_client` (the code that will actually be wired)
so this validates the client, not just the API; falls back to a raw HTTP
call only when an accessor raises, to help diagnose what the client can't
show (auth header issues, redirects, an envelope shape the client doesn't
expect). Never prints the key.

Answers five specific questions (see the final report section):

  1. Does the Starter key actually serve all four endpoints, or does any of
     them 403 / come back with an envelope error?
  2. Is `flucs` (on the Results payload) a scalar or a timestamped series,
     and are its timestamps pre-race or post-race?
  3. Do Speedmaps and Ratings serve resulted (past) meetings, or only
     upcoming ones? This decides whether Phase E can backfill training data
     immediately or needs weeks of shadow accrual like the Betfair T-5 capture.
  4. Are the documented-but-untested fields (penetrometer, irrigation,
     rainfall, pfaiScore, ...) actually populated, or null in practice?
  5. Do the `strike_rates(entity_type=...)` enum values return genuinely
     different jockey vs trainer datasets, and what does `startDate` anchor?

Every raw payload is saved using the existing pf_<endpoint>_<label>.json
naming convention, so this run's output becomes the golden fixtures for the
contract tests when wiring starts — that work isn't done twice. The default
destination is server/python/providers/fixtures/, which is what CI wants
(ephemeral checkout, uploaded as an artifact); a LOCAL run should pass
--out-dir to somewhere scratch, so payloads never land in the tracked
fixtures directory beside real ones.

    PUNTINGFORM_API_KEY=... python3 scripts/puntingform_phase_e_probe.py
    PUNTINGFORM_API_KEY=... python3 scripts/puntingform_phase_e_probe.py \
        --out-dir /tmp/pf_phase_e
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server" / "python"))

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURES_DIR = REPO_ROOT / "server" / "python" / "providers" / "fixtures"

# Set by main() from --out-dir. Defaults to the real fixtures directory
# because that is where CI wants them (ephemeral checkout, uploaded as an
# artifact), but a local run against a real key should point this somewhere
# scratch: writing straight into the tracked fixtures directory is how a
# batch of mock payloads once ended up looking like real captures.
OUT_DIR = DEFAULT_FIXTURES_DIR

API_KEY = (os.environ.get("PUNTINGFORM_API_KEY") or "").strip()
UA = "Mozilla/5.0 (compatible; StrideRacing/1.0)"
BASE = "https://api.puntingform.com.au/v2"


def redact(text):
    return text.replace(API_KEY, "<KEY>") if API_KEY else text


def banner(t):
    print()
    print("=" * 72)
    print(t)
    print("=" * 72)


def save_fixture(name, payload):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    try:
        shown = path.relative_to(REPO_ROOT)
    except ValueError:
        shown = path  # --out-dir pointed outside the repo
    print(f"  saved -> {shown}")
    return path


def union_keys(records):
    """Every key seen across ALL records, not just the first.

    A field missing from record[0] but present later would otherwise never
    be reported at all — and a missing key is not the same fact as a null
    key. Q4's whole job is saying which documented fields are really
    populated, so reading one record would quietly truncate the answer on
    exactly the endpoint where it matters most (Ratings, 40+ fields, the
    shape confirmed least)."""
    keys = set()
    for r in records:
        if isinstance(r, dict):
            keys.update(r.keys())
    return sorted(keys)


def raw_fallback(path, params):
    """Only used to diagnose a PFError — bypasses the client's envelope
    check and prints the literal HTTP response so a 403 body, a redirect,
    or a shape the client doesn't expect is visible instead of swallowed."""
    import urllib.parse
    q = dict(params or {})
    q["apiKey"] = API_KEY
    url = f"{BASE}{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            print(f"  [raw fallback] {redact(url)} -> HTTP {resp.status}")
            print(f"  {redact(body.decode('utf-8', 'replace'))[:500]}")
    except urllib.error.HTTPError as e:
        body = e.read(500)
        print(f"  [raw fallback] {redact(url)} -> HTTP {e.code}")
        print(f"  {redact(body.decode('utf-8', 'replace'))[:500]}")
    except Exception as e:
        print(f"  [raw fallback] {redact(url)} -> {type(e).__name__}: {e}")


def null_rate_report(label, records, fields):
    """records: list of flat dicts. fields: iterable of field names to check.
    A field that is 100% null/empty/missing across every record is not a
    usable feature regardless of what the docstring promised."""
    if not records:
        print(f"  {label}: 0 records — cannot assess null rates")
        return
    n = len(records)
    print(f"  {label}: {n} record(s)")
    for field in sorted(fields):
        present = sum(1 for r in records if r.get(field) not in (None, "", []))
        pct = 100.0 * present / n
        flag = "" if pct > 0 else "  <-- ALWAYS NULL/EMPTY, not a usable feature"
        print(f"    {field:24} {present:>4}/{n} populated ({pct:5.1f}%){flag}")


def flatten_speedmap_items(payload):
    """Speedmaps payload (per pf_client docstring): a list of per-race maps,
    each carrying an `items` list of per-runner records. Flatten to runner
    records; fall back to treating the payload itself as flat if the nested
    shape isn't what the docstring says (that mismatch is itself a finding)."""
    if not isinstance(payload, list):
        return []
    flat = []
    shape_matched = True
    for entry in payload:
        if isinstance(entry, dict) and isinstance(entry.get("items"), list):
            flat.extend(i for i in entry["items"] if isinstance(i, dict))
        elif isinstance(entry, dict):
            shape_matched = False
            flat.append(entry)
    if not shape_matched:
        print("  NOTE: speedmaps payload did not nest runners under 'items' as "
              "documented — treating top-level entries as records instead. "
              "This is itself a finding: the docstring shape is wrong.")
    return flat


def pick_meeting(meetings, prefer_country="AUS", require_resulted=None):
    if not meetings:
        return None
    aus = [m for m in meetings if (m.get("track") or {}).get("country") == prefer_country]
    pool = aus or meetings
    if require_resulted is True:
        resulted = [m for m in pool if m.get("resultsUpdated")]
        pool = resulted or pool
    elif require_resulted is False:
        upcoming = [m for m in pool if not m.get("resultsUpdated")]
        pool = upcoming or pool
    return pool[0] if pool else None


def main(argv=None):
    global OUT_DIR
    parser = argparse.ArgumentParser(
        description="Probe the four unwired Punting Form Phase E endpoints.")
    parser.add_argument(
        "--out-dir", default=None,
        help="Directory for the raw payloads (default: "
             "server/python/providers/fixtures/, which is what CI wants). "
             "Point this somewhere scratch for a local run so payloads do "
             "not land in the tracked fixtures directory.")
    args = parser.parse_args(argv)
    if args.out_dir:
        OUT_DIR = Path(args.out_dir).expanduser().resolve()
        print(f"payloads -> {OUT_DIR}")

    if not API_KEY:
        print("PUNTINGFORM_API_KEY is not set — nothing to probe")
        return 2

    import pf_client

    findings = {
        "q1_endpoint_availability": {},
        "q2_flucs_shape": {},
        "q3_historical_coverage": {},
        "q4_null_rates": "see printed report above the summary",
        "q5_entity_enum": {},
    }

    now_aest = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=10)
    today_iso = now_aest.strftime("%Y-%m-%d")
    week_ago_iso = (now_aest - datetime.timedelta(days=7)).strftime("%Y-%m-%d")

    # ---------------------------------------------------------- meetings
    banner(f"0. Meetings — today ({today_iso}, upcoming) and a week ago ({week_ago_iso}, resulted)")
    try:
        today_meetings = pf_client.meetings_for_date(today_iso)
    except pf_client.PFError as e:
        print(f"  meetings_for_date({today_iso}) failed: {e}")
        today_meetings = []
    try:
        past_meetings = pf_client.meetings_for_date(week_ago_iso)
    except pf_client.PFError as e:
        print(f"  meetings_for_date({week_ago_iso}) failed: {e}")
        past_meetings = []

    save_fixture(f"pf_meetingslist_{today_iso}_phase_e_probe.json", today_meetings)
    save_fixture(f"pf_meetingslist_{week_ago_iso}_phase_e_probe.json", past_meetings)

    upcoming_meeting = pick_meeting(today_meetings, require_resulted=False)
    resulted_meeting = pick_meeting(past_meetings, require_resulted=True)
    upcoming_id = upcoming_meeting.get("meetingId") if upcoming_meeting else None
    resulted_id = resulted_meeting.get("meetingId") if resulted_meeting else None
    print(f"  upcoming meetingId: {upcoming_id} "
          f"({(upcoming_meeting or {}).get('track', {}).get('name')})")
    print(f"  resulted meetingId: {resulted_id} "
          f"({(resulted_meeting or {}).get('track', {}).get('name')})")

    # ------------------------------------------------- Q1 endpoint availability
    banner("1. Does the Starter key actually serve each endpoint? "
           "(envelope statusCode/error, not just HTTP 200)")

    def try_call(label, fn, *args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            print(f"  {label}: OK — {len(result) if isinstance(result, list) else 'non-list'} item(s)")
            findings["q1_endpoint_availability"][label] = "OK"
            return result
        except pf_client.PFAuthError as e:
            print(f"  {label}: AUTH ERROR — {e}")
            findings["q1_endpoint_availability"][label] = f"AUTH ERROR: {e}"
            raw_fallback(*_endpoint_for(label, args, kwargs))
            return None
        except pf_client.PFError as e:
            print(f"  {label}: ENVELOPE/HTTP ERROR — {e}")
            findings["q1_endpoint_availability"][label] = f"ERROR: {e}"
            raw_fallback(*_endpoint_for(label, args, kwargs))
            return None

    def _endpoint_for(label, args, kwargs):
        mapping = {
            "conditions": ("/Updates/Conditions", {}),
            "speedmaps(upcoming)": ("/User/Speedmaps",
                                    {"meetingId": upcoming_id, "raceNo": 0}),
            "speedmaps(resulted)": ("/User/Speedmaps",
                                     {"meetingId": resulted_id, "raceNo": 0}),
            "ratings(upcoming)": ("/Ratings/MeetingRatings", {"meetingId": upcoming_id}),
            "ratings(resulted)": ("/Ratings/MeetingRatings", {"meetingId": resulted_id}),
            "strike_rates(entity_type=0)": ("/form/strikerate", {"entityType": 0}),
            "strike_rates(entity_type=1)": ("/form/strikerate", {"entityType": 1}),
            "results(resulted)": ("/form/results", {"meetingId": resulted_id}),
        }
        return mapping.get(label, ("/unknown", {}))

    conditions_payload = try_call("conditions", pf_client.conditions)
    if conditions_payload is not None:
        save_fixture("pf_conditions_phase_e_probe.json", conditions_payload)

    speedmaps_upcoming = speedmaps_resulted = None
    ratings_upcoming = ratings_resulted = None
    if upcoming_id:
        speedmaps_upcoming = try_call("speedmaps(upcoming)",
                                       pf_client.speedmaps_for_meeting, upcoming_id)
        if speedmaps_upcoming is not None:
            save_fixture(f"pf_speedmaps_{upcoming_id}_upcoming.json", speedmaps_upcoming)
        ratings_upcoming = try_call("ratings(upcoming)",
                                     pf_client.ratings_for_meeting, upcoming_id)
        if ratings_upcoming is not None:
            save_fixture(f"pf_ratings_{upcoming_id}_upcoming.json", ratings_upcoming)
    else:
        print("  no upcoming meetingId found — skipping speedmaps/ratings(upcoming)")

    if resulted_id:
        speedmaps_resulted = try_call("speedmaps(resulted)",
                                       pf_client.speedmaps_for_meeting, resulted_id)
        if speedmaps_resulted is not None:
            save_fixture(f"pf_speedmaps_{resulted_id}_resulted.json", speedmaps_resulted)
        ratings_resulted = try_call("ratings(resulted)",
                                     pf_client.ratings_for_meeting, resulted_id)
        if ratings_resulted is not None:
            save_fixture(f"pf_ratings_{resulted_id}_resulted.json", ratings_resulted)
    else:
        print("  no resulted meetingId found — skipping speedmaps/ratings(resulted)")

    strike_entity0 = try_call("strike_rates(entity_type=0)", pf_client.strike_rates, entity_type=0)
    if strike_entity0 is not None:
        save_fixture("pf_strikerate_entity0.json", strike_entity0)
    strike_entity1 = try_call("strike_rates(entity_type=1)", pf_client.strike_rates, entity_type=1)
    if strike_entity1 is not None:
        save_fixture("pf_strikerate_entity1.json", strike_entity1)

    results_payload = None
    if resulted_id:
        results_payload = try_call("results(resulted)", pf_client.results_for_meeting, resulted_id)
        if results_payload is not None:
            save_fixture(f"pf_results_{resulted_id}_phase_e_probe.json", results_payload)

    # ------------------------------------------------------------ Q2 flucs
    banner("2. flucs — scalar or series? pre-race or post-race?")
    flucs_sample = None
    official_time = None
    if results_payload:
        for meeting in results_payload:
            for race in (meeting.get("raceResults") or []):
                official_time = race.get("officialRaceTime")
                for runner in (race.get("runners") or []):
                    if runner.get("flucs") not in (None, "", []):
                        flucs_sample = runner.get("flucs")
                        break
                if flucs_sample is not None:
                    break
            if flucs_sample is not None:
                break

    if flucs_sample is None:
        print("  no non-empty flucs field found in the resulted meeting sampled — "
              "either the field is genuinely absent/always empty at Starter tier, "
              "or this specific race/runner had none. Re-run against another "
              "resulted meetingId before concluding it's unavailable.")
        findings["q2_flucs_shape"] = {"found": False}
    else:
        print(f"  raw flucs value (verbatim): {flucs_sample!r}")
        print(f"  Python type: {type(flucs_sample).__name__}")
        print(f"  officialRaceTime for comparison: {official_time!r}")
        shape = "unknown"
        if isinstance(flucs_sample, (int, float, str)):
            shape = "scalar"
        elif isinstance(flucs_sample, list):
            if flucs_sample and isinstance(flucs_sample[0], dict):
                has_ts = any("time" in k.lower() or "date" in k.lower()
                             for item in flucs_sample if isinstance(item, dict)
                             for k in item.keys())
                shape = "timestamped series" if has_ts else "series without visible timestamps"
            else:
                shape = "flat list, no per-entry timestamp visible"
        print(f"  classified shape: {shape}")
        print("  MANUAL CHECK REQUIRED: compare any timestamps in flucs against "
              "officialRaceTime above — if entries run past the jump, this is "
              "settlement/price-fluctuation-to-close data, not a T-minus series "
              "usable for movement features.")
        findings["q2_flucs_shape"] = {
            "found": True, "python_type": type(flucs_sample).__name__,
            "classified_shape": shape, "raw_sample": flucs_sample,
            "official_race_time": official_time,
        }

    # ---------------------------------------------- Q3 historical coverage
    banner("3. Do Speedmaps/Ratings serve resulted meetings, or upcoming only?")
    for label, upcoming_data, resulted_data in [
        ("speedmaps", speedmaps_upcoming, speedmaps_resulted),
        ("ratings", ratings_upcoming, ratings_resulted),
    ]:
        u_len = len(upcoming_data) if isinstance(upcoming_data, list) else None
        r_len = len(resulted_data) if isinstance(resulted_data, list) else None
        print(f"  {label}: upcoming meeting -> {u_len} item(s); "
              f"resulted meeting (7d old) -> {r_len} item(s)")
        if r_len is not None and r_len > 0:
            verdict = "SERVES HISTORY — backfill + immediate walk-forward A/B is possible"
        elif r_len == 0:
            verdict = ("UPCOMING-ONLY (as tested) — needs shadow accrual before any A/B, "
                       "same constraint as the Betfair T-5 capture")
        else:
            verdict = "could not be determined (call failed — see Q1)"
        print(f"    verdict: {verdict}")
        findings["q3_historical_coverage"][label] = {
            "upcoming_count": u_len, "resulted_count": r_len, "verdict": verdict,
        }

    # ------------------------------------------------------ Q4 null rates
    banner("4. Are the documented fields actually populated?")
    if conditions_payload:
        null_rate_report(
            "conditions", conditions_payload,
            ["trackCondition", "trackConditionNumber", "weather", "wind",
             "windDirection", "abandonded", "rail", "penetrometer",
             "irrigation", "rainfall", "comment", "source", "lastUpdate"])
    sm_records = flatten_speedmap_items(speedmaps_resulted or speedmaps_upcoming or [])
    if sm_records:
        null_rate_report(
            "speedmaps (flattened items)", sm_records,
            ["pfScore", "neuralPrice", "neuralPriceRank", "pfaiScore", "pfaiPrice",
             "pfaiRank", "assessedPrice", "speed", "settle", "barrier", "mapA2E",
             "jockeyA2E", "ratedRunStyle", "ratedSettle"])
    ratings_records = ratings_resulted or ratings_upcoming or []
    if isinstance(ratings_records, list) and ratings_records:
        null_rate_report("ratings", ratings_records, union_keys(ratings_records))
    for label, sr in [("strike_rates(entity_type=0)", strike_entity0),
                       ("strike_rates(entity_type=1)", strike_entity1)]:
        if isinstance(sr, list) and sr:
            null_rate_report(label, sr, union_keys(sr))

    # ----------------------------------------------------- Q5 entity enum
    banner("5. Does entity_type actually split jockey vs trainer? What does startDate anchor?")
    for label, sr in [("entity_type=0", strike_entity0), ("entity_type=1", strike_entity1)]:
        if isinstance(sr, list):
            names = sorted({r.get("entityName") for r in sr if isinstance(r, dict)})[:10]
            dates = sorted({r.get("startDate") for r in sr if isinstance(r, dict)})
            print(f"  {label}: {len(sr)} entities; sample names: {names}")
            print(f"    distinct startDate values: {dates[:5]}"
                  f"{' ...' if len(dates) > 5 else ''} ({len(dates)} distinct)")
    if isinstance(strike_entity0, list) and isinstance(strike_entity1, list) and strike_entity0 and strike_entity1:
        names0 = {r.get("entityName") for r in strike_entity0 if isinstance(r, dict)}
        names1 = {r.get("entityName") for r in strike_entity1 if isinstance(r, dict)}
        overlap = names0 & names1
        print(f"  overlap between entity_type=0 and entity_type=1 name sets: "
              f"{len(overlap)} of {len(names0)}/{len(names1)}")
        if overlap == names0 == names1:
            print("  WARNING: identical entity sets — entity_type may not be "
                  "filtering at all (check the raw params sent, not just the client call)")
        findings["q5_entity_enum"] = {
            "entity0_count": len(names0), "entity1_count": len(names1),
            "overlap_count": len(overlap),
            "sets_identical": overlap == names0 == names1,
        }

    # -------------------------------------------------------------- report
    banner("SUMMARY — answers to the five questions (verify against printed detail above)")
    print(json.dumps(findings, indent=2, default=str)[:4000])
    print()
    print("Raw payloads saved under server/python/providers/fixtures/ — these "
          "become the golden fixtures for contract tests when wiring starts.")
    print("PROBE COMPLETE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
