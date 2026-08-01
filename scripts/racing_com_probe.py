#!/usr/bin/env python3
"""Racing.com GraphQL capability probe — maps what the API can replace now
that The Racing API has ceased Australian coverage.

Read-only. Answers, using the same endpoint/auth as the existing sectionals
collector (graphql.rmdprod.racing.com, x-api-key + referer):

  1. What queries exist (schema introspection, if enabled)
  2. Which states GetRaceMeetingsByState actually serves, and whether
     daysForward returns FUTURE meetings (racecards)
  3. What a future meeting's races look like pre-race (entries available?)
  4. Argument lists for any race/meet/form/entry-shaped queries, to find the
     racecard/form query the site itself must use

Run in CI (racing-com-probe workflow) or locally:
    RACING_COM_API_KEY=... python3 scripts/racing_com_probe.py
Never prints the key.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

GRAPHQL_ENDPOINT = "https://graphql.rmdprod.racing.com/"
API_KEY = os.environ.get("RACING_COM_API_KEY", "")
REFERER = "https://www.racing.com/"

INTERESTING = ("race", "meet", "form", "entry", "entrie", "runner", "horse", "odds", "field", "card")


def banner(t):
    print()
    print("=" * 72)
    print(t)
    print("=" * 72)


def gql(query, label="", timeout=30):
    params = urllib.parse.urlencode({"query": query})
    req = urllib.request.Request(
        f"{GRAPHQL_ENDPOINT}?{params}",
        headers={"x-api-key": API_KEY, "referer": REFERER,
                 "User-Agent": "Mozilla/5.0 (compatible; RacingAnalytics/1.0)",
                 "Accept": "application/json"},
        method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        print(f"  [{label}] HTTP {e.code}: {e.read()[:200]!r}")
        return None
    except Exception as e:
        print(f"  [{label}] {type(e).__name__}: {e}")
        return None
    if body.get("errors"):
        msgs = "; ".join(str(err.get("message", err))[:150] for err in body["errors"][:3])
        print(f"  [{label}] GraphQL errors: {msgs}")
    return body.get("data")


def main():
    if not API_KEY:
        print("RACING_COM_API_KEY is not set — nothing to probe")
        return 2

    banner("1. Schema introspection: every query the API exposes")
    data = gql("{ __schema { queryType { fields { name } } } }", "introspection")
    all_fields = []
    if data and data.get("__schema"):
        all_fields = [f["name"] for f in data["__schema"]["queryType"]["fields"]]
        print(f"  {len(all_fields)} queries exposed:")
        for i in range(0, len(all_fields), 4):
            print("    " + ", ".join(all_fields[i:i + 4]))
    else:
        print("  introspection disabled or failed — will rely on candidate-name probing")

    banner("2. Argument lists for race/meet/form/entry-shaped queries")
    if all_fields:
        data = gql("{ __schema { queryType { fields { name args { name type { name kind ofType { name } } } } } } }",
                   "introspection-args")
        if data and data.get("__schema"):
            for f in data["__schema"]["queryType"]["fields"]:
                if any(k in f["name"].lower() for k in INTERESTING):
                    args = []
                    for a in f.get("args", []):
                        t = a.get("type") or {}
                        tname = t.get("name") or (t.get("ofType") or {}).get("name") or t.get("kind", "?")
                        args.append(f"{a['name']}:{tname}")
                    print(f"    {f['name']}({', '.join(args)})")
    else:
        print("  skipped (no introspection)")

    banner("3. GetRaceMeetingsByState: which states, and are FUTURE meetings served?")
    future_meet = None
    for states in ["VIC", "NSW", "QLD", "SA", "WA", "TAS", "ACT", "NT",
                   "VIC|NSW|QLD|SA|WA|TAS|ACT|NT"]:
        q = '{ GetRaceMeetingsByState(daysBack: 0, daysForward: 3, states: "%s") { id venue venueCode state status date racesCount } }' % states
        data = gql(q, f"meetings {states}")
        meetings = (data or {}).get("GetRaceMeetingsByState") or []
        sample = ", ".join(f"{m.get('venue')}({m.get('date', '')[:10]})" for m in meetings[:4])
        print(f"    {states:<28} -> {len(meetings):3d} meetings   {sample}")
        if meetings and future_meet is None and states != "VIC|NSW|QLD|SA|WA|TAS|ACT|NT":
            future_meet = meetings[0]

    banner("4. A future meeting's races, pre-race (getNoCacheRacesForMeet)")
    if future_meet:
        code = future_meet.get("venueCode") or future_meet.get("id")
        print(f"  probing meeting: {future_meet.get('venue')} {future_meet.get('date', '')[:10]} (meetCode {code})")
        q = ('{ getNoCacheRacesForMeet(meetCode: "%s") { id raceNumber raceStatus name distance '
             'hasResults hasSectionals raceEntryTimes { horseName barrierNumber saddleNumber '
             'jockeyName trainerName finishPosition } } }') % code
        data = gql(q, "races-for-meet")
        races = (data or {}).get("getNoCacheRacesForMeet") or []
        print(f"    {len(races)} races returned")
        for r in races[:3]:
            entries = r.get("raceEntryTimes") or []
            entry_note = f"{len(entries)} entries"
            if entries:
                e = entries[0]
                entry_note += (f" (e.g. {e.get('horseName')}, barrier {e.get('barrierNumber')},"
                               f" jockey {e.get('jockeyName')}, finishPosition {e.get('finishPosition')})")
            print(f"    R{r.get('raceNumber')} '{str(r.get('name'))[:40]}' status={r.get('raceStatus')} "
                  f"dist={r.get('distance')} -> {entry_note}")
    else:
        print("  no future meeting found in step 3 — cannot probe")

    banner("5. Candidate racecard/form queries (only run if named in the schema)")
    if all_fields:
        candidates = [f for f in all_fields
                      if any(k in f.lower() for k in ("form", "entry", "entrie", "card", "field"))]
        print(f"  schema names worth building on next: {candidates or 'none found'}")
    else:
        print("  skipped (no introspection)")

    print()
    print("PROBE COMPLETE — this output is the input for the migration design.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
