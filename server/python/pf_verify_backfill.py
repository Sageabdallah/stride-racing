#!/usr/bin/env python3
"""Verify the Phase B Punting Form backfill and produce the definitive gap
report for PUNTINGFORM_MIGRATION.md.

READ-ONLY and re-runnable: SELECTs against race_results_history /
pf_raw_payloads plus Punting Form calendar reads. Nothing is written,
anywhere. Five labelled checks:

  1. Per-day coverage — PF resulted-meeting count per date (meetingslist,
     trials excluded) and expected race/runner counts (from the archived
     pf_raw_payloads where possible, live results fetch only for dates the
     archive predates) vs meetings/races/rows in race_results_history.
     Days are flagged when DB < PF beyond scratching-explainable slack
     (>2 runners/race deficit, or any missing race/meeting).
  2. NULL-rate table on race_id LIKE 'pf%' rows for distance_m, race_class,
     class_level, going, sp_odds, margin_lengths, race_name, opponents_json.
  3. Pipeline death date — last row date for non-pf% rows (old pipeline) vs
     pf% rows, and the gap between them.
  4. Lost dates — the PF wall is probed at runtime by binary-searching the
     HTTP 400 boundary (max 8 calls; the wall slides daily). Dates before
     the wall with zero DB rows from ANY source are unrecoverable without
     the $1,100 archive and are listed explicitly. Zero-row dates at/after
     the wall are missing-but-recoverable and listed separately.
  5. Bridge audit — bridged vs new pf% horse-id counts, plus 20 random
     bridged samples (PF name -> matched horse_id + that horse's most
     recent prior old-pipeline name) for eyeballing.

Usage (CI: pf-verify workflow; local works with both env vars):
    DATABASE_URL=... PUNTINGFORM_API_KEY=... \
        python3 server/python/pf_verify_backfill.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]

Defaults: --start 2026-06-08 (the wall as first measured 2026-07-31),
--end today AEST. Any unexpected error exits non-zero with nothing
half-printed as fact (loud failure, same rule as the backfill).
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pf_client
from pf_backfill_results import normalize_finish_position, safe_int

DEFAULT_START = "2026-06-08"
WALL_PROBE_MAX_CALLS = 8
SLACK_RUNNERS_PER_RACE = 2
WALL_SEARCH_SPAN_DAYS = 120  # 2**7 >= 120: endpoint check + 7 bisections = 8 calls

NULL_COLUMNS = (
    "distance_m", "race_class", "class_level", "going",
    "sp_odds", "margin_lengths", "race_name", "opponents_json",
)


# ---------------------------------------------------------------- pure logic

def aus_race_meetings(meetings):
    """AUS non-barrier-trial meetings (same filter as the backfill)."""
    return [m for m in meetings or []
            if (m.get("track") or {}).get("country") == "AUS"
            and not m.get("isBarrierTrial")]


def meeting_resulted(m):
    """Best-effort resulted flag from a meetingslist entry: the JSON service
    carries resultsUpdated (legacy CSV had an explicit 'resulted' field)."""
    return bool(m.get("resulted") or m.get("resultsUpdated"))


def expected_from_results_payload(payload):
    """(n_races, n_runners) one results payload should contribute to
    race_results_history. Runner filter mirrors the backfill exactly:
    scratched/no-position runners excluded."""
    races = runners = 0
    for block in payload or []:
        for rr in block.get("raceResults") or []:
            if not safe_int(rr.get("raceNumber")):
                continue
            placed = [r for r in (rr.get("runners") or [])
                      if normalize_finish_position(r.get("position")) is not None]
            if placed:
                races += 1
                runners += len(placed)
    return races, runners


def assess_day(pf_meetings, pf_races, pf_runners, db_meetings, db_races, db_rows):
    """The slack rule. A day is flagged when the DB holds less than PF says
    ran, beyond what scratchings explain: any missing meeting, any missing
    race, or a runner deficit above 2 per race. None = unknown (no data)."""
    flags = []
    if pf_meetings is not None and db_meetings < pf_meetings:
        flags.append(f"missing meeting(s): DB {db_meetings} < PF {pf_meetings}")
    if pf_races is not None and db_races < pf_races:
        flags.append(f"missing race(s): DB {db_races} < PF {pf_races}")
    if pf_runners is not None and pf_races:
        deficit = pf_runners - db_rows
        if deficit > SLACK_RUNNERS_PER_RACE * pf_races:
            flags.append(
                f"runner deficit {deficit} > {SLACK_RUNNERS_PER_RACE * pf_races} "
                f"scratching slack ({pf_runners} expected, {db_rows} in DB)")
    return flags


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


def classify_gap_days(zero_db_dates, wall, au_racing_dates):
    """Split zero-row dates into (lost, recoverable).

    lost: before the runtime wall — PF can no longer confirm or serve them;
    unrecoverable without the $1,100 archive. recoverable: at/after the wall
    with AU racing confirmed on the PF calendar (reachable, so only these
    count as genuinely missing)."""
    lost = sorted(d for d in zero_db_dates if d < wall)
    recoverable = sorted(d for d in zero_db_dates
                         if d >= wall and d in au_racing_dates)
    return lost, recoverable


# ---------------------------------------------------------------- DB access

def db_day_counts(cur, start, end):
    """Per date: (meetings, races, rows, pf_rows) in race_results_history.
    'meetings' is COUNT(DISTINCT lower(track)) — a proxy that collapses the
    rare same-track double meeting; noted in the report."""
    cur.execute("""
        SELECT race_date::text,
               COUNT(DISTINCT LOWER(track)),
               COUNT(DISTINCT race_id),
               COUNT(*),
               COUNT(*) FILTER (WHERE race_id LIKE 'pf%%')
        FROM race_results_history
        WHERE race_date >= %s AND race_date <= %s
        GROUP BY race_date ORDER BY race_date
    """, (start, end))
    return {r[0]: {"meetings": r[1], "races": r[2], "rows": r[3], "pf_rows": r[4]}
            for r in cur.fetchall()}


def archive_day_counts(cur, start, end):
    """Per date: expected (races, runners, meeting_ids) from the archived raw
    results payloads — no refetching for days the backfill already pulled."""
    cur.execute("""
        SELECT ref_date::text, meeting_id, payload
        FROM pf_raw_payloads
        WHERE kind = 'results' AND ref_date >= %s AND ref_date <= %s
        ORDER BY ref_date
    """, (start, end))
    days = {}
    for ref_date, meeting_id, payload in cur.fetchall():
        d = days.setdefault(ref_date, {"races": 0, "runners": 0, "meetings": set()})
        races, runners = expected_from_results_payload(payload)
        d["races"] += races
        d["runners"] += runners
        d["meetings"].add(meeting_id)
    return days


def null_rates(cur):
    """Percent NULL per column over race_id LIKE 'pf%' rows. One query."""
    sums = ", ".join(f"COUNT(*) FILTER (WHERE {c} IS NULL)" for c in NULL_COLUMNS)
    cur.execute(
        f"SELECT COUNT(*), {sums} FROM race_results_history WHERE race_id LIKE 'pf%'")
    row = cur.fetchone()
    total = row[0]
    out = []
    for col, nulls in zip(NULL_COLUMNS, row[1:]):
        pct = (100.0 * nulls / total) if total else 0.0
        out.append({"column": col, "nulls": nulls, "pct": pct})
    return total, out


def death_dates(cur):
    """(last old-pipeline date, last pf date, first pf date)."""
    cur.execute("SELECT MAX(race_date)::text FROM race_results_history "
                "WHERE race_id NOT LIKE 'pf%'")
    old_last = cur.fetchone()[0]
    cur.execute("SELECT MIN(race_date)::text, MAX(race_date)::text FROM race_results_history "
                "WHERE race_id LIKE 'pf%'")
    pf_first, pf_last = cur.fetchone()
    return old_last, pf_first, pf_last


def bridge_audit(cur, sample_size=20):
    """Bridged vs new pf% horse ids over PF-sourced rows, plus a random
    sample of bridged horses for the operator's eyeball."""
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE horse_id LIKE 'pf%%'),
               COUNT(*) FILTER (WHERE horse_id NOT LIKE 'pf%%'),
               COUNT(DISTINCT horse_id) FILTER (WHERE horse_id LIKE 'pf%%'),
               COUNT(DISTINCT horse_id) FILTER (WHERE horse_id NOT LIKE 'pf%%')
        FROM race_results_history WHERE race_id LIKE 'pf%%'
    """)
    new_rows, bridged_rows, new_ids, bridged_ids = cur.fetchone()
    cur.execute("""
        WITH picked AS (
            SELECT DISTINCT ON (horse_id) horse_id, horse_name, race_date
            FROM race_results_history
            WHERE race_id LIKE 'pf%%' AND horse_id NOT LIKE 'pf%%'
            ORDER BY horse_id, race_date DESC
        )
        SELECT s.horse_id, s.horse_name,
               (SELECT h.horse_name FROM race_results_history h
                WHERE h.horse_id = s.horse_id AND h.race_id NOT LIKE 'pf%%'
                      AND h.race_date < s.race_date
                ORDER BY h.race_date DESC LIMIT 1)
        FROM picked s ORDER BY random() LIMIT %s
    """, (sample_size,))
    samples = cur.fetchall()
    return {"bridged_rows": bridged_rows, "new_rows": new_rows,
            "bridged_ids": bridged_ids, "new_ids": new_ids, "samples": samples}


# ---------------------------------------------------------------- PF access

def probe_date(iso):
    """One meetingslist call: served (True) or beyond the wall (False).
    retries=1 keeps the wall search inside its call budget; only an HTTP
    400 means 'before the wall' — anything else is a real error."""
    try:
        pf_client.get("/form/meetingslist", {"meetingDate": iso}, retries=1)
        return True
    except pf_client.PFError as e:
        if "400" in str(e):
            return False
        raise


def fetch_day_calendar(iso):
    """AUS race meetings for a date. Returns (meetings, unreachable)."""
    try:
        return aus_race_meetings(pf_client.meetings_for_date(iso)), False
    except pf_client.PFError as e:
        if "400" in str(e):
            return [], True
        raise


# ---------------------------------------------------------------- report

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_START,
                    help=f"first date to verify (ISO); default {DEFAULT_START}")
    ap.add_argument("--end", help="last date (ISO); default today AEST")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL is not set")
        return 1
    if not (os.environ.get("PUNTINGFORM_API_KEY") or "").strip():
        print("ERROR: PUNTINGFORM_API_KEY is not set")
        return 1

    now_aest = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=10)
    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end) if args.end else now_aest.date()
    dates = [(start + datetime.timedelta(days=i)).isoformat()
             for i in range((end - start).days + 1)]

    # All DB reads happen in one quick burst up front: the API-heavy per-day
    # loop takes minutes at 0.4s pacing and serverless Postgres drops idle
    # connections (learned the hard way — verify run 1 died mid-report).
    import psycopg2
    conn = psycopg2.connect(db_url)
    conn.set_session(readonly=True)  # belt and braces: this script never writes
    cur = conn.cursor()
    db_days = db_day_counts(cur, dates[0], dates[-1])
    arch_days = archive_day_counts(cur, dates[0], dates[-1])
    pf_total, rates = null_rates(cur)
    old_last, pf_first, pf_last = death_dates(cur)
    audit = bridge_audit(cur)
    conn.close()

    print(f"VERIFY {dates[0]} .. {dates[-1]} ({len(dates)} days) — read-only")

    # ---- runtime wall first: per-day check needs it to explain 400s
    wall, wall_lo, wall_calls = find_wall(
        probe_date, end - datetime.timedelta(days=WALL_SEARCH_SPAN_DAYS), end)
    wall_exact = (wall - wall_lo).days == 1
    print(f"runtime PF wall: {wall} (binary search, {wall_calls} calls"
          f"{'' if wall_exact else f', APPROXIMATE — true wall in ({wall_lo}, {wall}]'})")

    # ---- check 1: per-day coverage
    print("\n== CHECK 1: per-day coverage (DB vs PF) ==")
    print("date        PF mtg(res/tot)  exp races/runners(src)   DB mtg/race/rows(pf)   flags")
    flagged, unreachable, zero_row, au_racing_dates = [], [], [], set()
    for iso in dates:
        db = db_days.get(iso, {"meetings": 0, "races": 0, "rows": 0, "pf_rows": 0})
        if db["rows"] == 0:
            zero_row.append(iso)
        meetings, beyond = fetch_day_calendar(iso)
        if beyond:
            unreachable.append(iso)
            print(f"{iso}  beyond PF wall (HTTP 400) — DB mtg/race/rows: "
                  f"{db['meetings']}/{db['races']}/{db['rows']}")
            continue
        resulted = [m for m in meetings if meeting_resulted(m)]
        if meetings:
            au_racing_dates.add(iso)
        arch = arch_days.get(iso)
        if arch:
            exp_races, exp_runners, src = arch["races"], arch["runners"], "archive"
        elif resulted:
            # archive predates this date: fetch results live for the count
            exp_races = exp_runners = 0
            for m in resulted:
                try:
                    r, n = expected_from_results_payload(
                        pf_client.results_for_meeting(m.get("meetingId")))
                except pf_client.PFError as e:
                    print(f"  ! {iso} meeting {m.get('meetingId')}: results fetch failed: {e}")
                    continue
                exp_races += r
                exp_runners += n
            src = "live"
        else:
            exp_races = exp_runners = None
            src = "n/a"
        flags = assess_day(len(resulted) if meetings else 0,
                           exp_races, exp_runners,
                           db["meetings"], db["races"], db["rows"])
        if flags:
            flagged.append(iso)
        mtg = f"{len(resulted)}/{len(meetings)}"
        exp = f"{exp_races}/{exp_runners}({src})" if exp_races is not None else f"-({src})"
        print(f"{iso}  {mtg:>7}  {exp:>20}   "
              f"{db['meetings']}/{db['races']}/{db['rows']}({db['pf_rows']})"
              + ("  FLAG: " + "; ".join(flags) if flags else ""))
    print(f"\ncoverage summary: {len(dates)} days checked, {len(flagged)} flagged, "
          f"{len(unreachable)} beyond wall, {len(zero_row)} with zero DB rows")
    if flagged:
        print("flagged days:", ", ".join(flagged))

    # ---- check 2: NULL rates on pf% rows
    print("\n== CHECK 2: NULL rates on race_id LIKE 'pf%' rows ==")
    print(f"pf% rows: {pf_total}")
    for r in rates:
        print(f"  {r['column']:<16} {r['nulls']:>6} NULL  ({r['pct']:.1f}%)")

    # ---- check 3: pipeline death date
    print("\n== CHECK 3: pipeline death date ==")
    print(f"old pipeline (race_id NOT LIKE 'pf%'): last row {old_last}")
    print(f"PF backfill (race_id LIKE 'pf%'):      rows {pf_first} .. {pf_last}")
    if old_last and pf_last:
        gap = (datetime.date.fromisoformat(pf_last)
               - datetime.date.fromisoformat(old_last)).days
        print(f"old pipeline died {old_last}; PF coverage now extends {gap} days past it")

    # ---- check 4: lost dates
    print("\n== CHECK 4: lost dates (zero DB rows, any source) ==")
    print(f"PF wall at runtime: {wall}")
    lost, recoverable = classify_gap_days(zero_row, wall.isoformat(), au_racing_dates)
    if lost:
        print("LOST (before the PF wall — unrecoverable without the $1,100 archive):")
        for d in lost:
            print(f"  {d}")
        print("(PF cannot confirm AU racing pre-wall — these are zero-row candidates)")
    else:
        print("LOST: none — every zero-row date is at/after the wall (recoverable).")
    if recoverable:
        print("MISSING BUT RECOVERABLE (>= wall, AU racing on the PF calendar, zero DB rows):")
        for d in recoverable:
            print(f"  {d}")
    else:
        print("MISSING BUT RECOVERABLE: none.")

    # ---- check 5: bridge audit
    print("\n== CHECK 5: horse-ID bridge audit ==")
    tot_rows = audit["bridged_rows"] + audit["new_rows"]
    pct = (100.0 * audit["bridged_rows"] / tot_rows) if tot_rows else 0.0
    print(f"bridged rows: {audit['bridged_rows']} ({pct:.1f}%)  "
          f"new-id rows: {audit['new_rows']}")
    print(f"distinct horses: {audit['bridged_ids']} bridged, {audit['new_ids']} new pf% ids")
    print("random bridged sample (PF name -> horse_id | prior old-pipeline name):")
    for horse_id, pf_name, prior_name in audit["samples"]:
        print(f"  {pf_name:<28} -> {horse_id:<10} | {prior_name}")

    print("\nVERIFY COMPLETE (read-only; nothing written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
