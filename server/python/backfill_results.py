"""
Gap backfill: race results + sectional times, week by week.

Why: the 2026-07 coverage report (docs/12 §4c) found every table stops at
~2026-04-18 — the production collectors have not written for months. This
driver reuses the EXACT production ingestion path to close a date gap:

  results     fetch_and_import_date.fetch_date / import_meetings
              (The Racing API; race-level skip of existing rows)
  NSW         nsw_sectional_collector.run_backfill(since_date=START)
              (pidata; one pass, all venues, internal dedup)
  QLD         sectional_times_collector.collect_for_date_track per
              (date, track) actually raced (Racing Queensland CSVs;
              ON CONFLICT DO NOTHING; phase-2 primitives computed on import)
  VIC/SA      racing_com_sectionals_collector.collect_for_date per date
              (needs RACING_COM_API_KEY; skipped loudly when unset)

No data leakage, by construction: everything written here is a settled
post-race fact (finishing position, margin, SP, sectional splits) going
into the HISTORY tables (race_results_history, sectional_times) keyed by
race_date. Nothing touches selections, prediction_audit, tips or any
pre-race table, and training stays leak-free because retrain_v2 and
training_view_v2 consume history through time-aware, race_date-bounded
lookups — a backfilled April result is only ever visible to features and
folds dated after it. Every writer is idempotent, so re-running a chunk
cannot duplicate rows.

Usage:
    python backfill_results.py                 # self-test (stdlib only, no DB)
    python backfill_results.py --start 2026-04-19 [--end 2026-05-02]
        [--skip-nsw] [--skip-qld] [--skip-vic-sa]

The backfill-results GitHub Action runs this next to the database in
date-bounded chunks (see .github/workflows/backfill-results.yml).
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta

QLD_ALIASES = {
    # meeting-name -> QLD_TRACKS key (mirrors ingest_target_track aliasing)
    "aquis park gold coast": "Gold Coast",
}


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def week_chunks(start, end):
    """
    Split [start, end] (inclusive date objects) into consecutive runs of at
    most 7 days, first chunk anchored at start. Raises on inverted ranges.
    """
    if end < start:
        raise ValueError(f"end {end} before start {start}")
    chunks, cur = [], start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=6), end)
        chunks.append([cur + timedelta(days=i)
                       for i in range((chunk_end - cur).days + 1)])
        cur = chunk_end + timedelta(days=1)
    return chunks


def qld_tracks_for_meetings(meetings, qld_tracks):
    """Map the day's meeting courses onto QLD_TRACKS keys (case-insensitive)."""
    hits = []
    lowered = {k.lower(): k for k in qld_tracks}
    for m in meetings or []:
        course = str(m.get("course") or "").strip().lower()
        if not course:
            continue
        key = QLD_ALIASES.get(course) or lowered.get(course)
        if key is None:
            # containment either way catches "Gold Coast" vs "Gold Coast Poly"
            for low, canonical in lowered.items():
                if low in course or course in low:
                    key = canonical
                    break
        if key and key not in hits:
            hits.append(key)
    return hits


def run_backfill(start, end, skip_nsw=False, skip_qld=False, skip_vic_sa=False):
    # production imports stay lazy so the self-test needs no DB deps
    from fetch_and_import_date import fetch_date, import_meetings
    from sectional_times_collector import QLD_TRACKS, collect_for_date_track
    import psycopg2

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1

    racing_com_key = os.environ.get("RACING_COM_API_KEY", "")
    totals = {"days": 0, "days_with_meetings": 0, "results_rows": 0,
              "skipped_races": 0, "qld_sectional_rows": 0,
              "vic_sa_sectional_rows": 0}

    chunks = week_chunks(start, end)
    print(f"Backfill {start} -> {end}: {sum(len(c) for c in chunks)} days "
          f"in {len(chunks)} week-chunks")
    if not racing_com_key and not skip_vic_sa:
        print("NOTE: RACING_COM_API_KEY not set — VIC/SA sectionals will be "
              "SKIPPED (results + NSW + QLD unaffected)")

    for week in chunks:
        print("\n" + "=" * 72)
        print(f"WEEK {week[0]} -> {week[-1]}")
        print("=" * 72)
        for day in week:
            ds = day.isoformat()
            totals["days"] += 1
            meetings = fetch_date(ds, metro_only=False)
            if not meetings:
                print(f"  {ds}: no meetings")
                continue
            totals["days_with_meetings"] += 1

            conn = psycopg2.connect(db_url)
            try:
                rows, skipped, _ = import_meetings(meetings, conn)
            finally:
                conn.close()
            totals["results_rows"] += rows
            totals["skipped_races"] += skipped
            print(f"  {ds}: {len(meetings)} meetings, +{rows} result rows "
                  f"({skipped} races already present)")

            if not skip_qld:
                for track in qld_tracks_for_meetings(meetings, QLD_TRACKS):
                    _, _, imported, _ = collect_for_date_track(
                        ds, track, db_url, verbose=True)
                    totals["qld_sectional_rows"] += imported

            if not skip_vic_sa and racing_com_key:
                from racing_com_sectionals_collector import collect_for_date
                got = collect_for_date(ds, states="VIC|SA", db_url=db_url,
                                       verbose=True)
                totals["vic_sa_sectional_rows"] += int(got or 0)

    if not skip_nsw:
        # one pass over pidata covers every NSW venue in range; internal
        # dedup skips anything already imported
        print("\n" + "=" * 72)
        print(f"NSW sectionals (pidata) since {start}")
        print("=" * 72)
        from nsw_sectional_collector import run_backfill as nsw_backfill
        nsw_backfill(since_date=start.isoformat())

    print("\n" + "=" * 72)
    print("BACKFILL TOTALS")
    print("=" * 72)
    for k, v in totals.items():
        print(f"  {k:24s} {v}")
    print("  (NSW rows reported by its own collector above; "
          "sectional_times upserts are ON CONFLICT DO NOTHING)")
    return 0


# ---------------------------------------------------------------- self-test
def _self_test():
    print("=" * 60)
    print("backfill_results self-test (stdlib only)")
    print("=" * 60)

    c = week_chunks(date(2026, 4, 19), date(2026, 5, 4))
    assert [len(x) for x in c] == [7, 7, 2], [len(x) for x in c]
    assert c[0][0] == date(2026, 4, 19) and c[-1][-1] == date(2026, 5, 4)
    flat = [d for chunk in c for d in chunk]
    assert flat == sorted(set(flat)) and len(flat) == 16  # no gaps, no dupes
    assert [len(x) for x in week_chunks(date(2026, 1, 1), date(2026, 1, 1))] == [1]
    try:
        week_chunks(date(2026, 2, 2), date(2026, 2, 1))
        raise AssertionError("inverted range must raise")
    except ValueError:
        pass
    print("  week_chunks: full/partial weeks, single day, inversion guarded")

    fake = [{"course": "Aquis Park Gold Coast"}, {"course": "eagle farm"},
            {"course": "Flemington"}, {"course": ""}]
    hits = qld_tracks_for_meetings(fake, {"Eagle Farm": [], "Gold Coast": []})
    assert hits == ["Gold Coast", "Eagle Farm"], hits
    print("  qld track mapping: alias + case-insensitive match, non-QLD ignored")

    assert parse_iso("2026-04-19") == date(2026, 4, 19)
    print("All backfill_results self-tests passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill results + sectionals for a date gap")
    parser.add_argument("--start", help="First date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", help="Last date YYYY-MM-DD (inclusive; default today UTC)")
    parser.add_argument("--skip-nsw", action="store_true")
    parser.add_argument("--skip-qld", action="store_true")
    parser.add_argument("--skip-vic-sa", action="store_true")
    args = parser.parse_args()

    if not args.start:
        _self_test()
        sys.exit(0)

    start = parse_iso(args.start)
    end = parse_iso(args.end) if args.end else datetime.utcnow().date()
    sys.exit(run_backfill(start, end, skip_nsw=args.skip_nsw,
                          skip_qld=args.skip_qld, skip_vic_sa=args.skip_vic_sa))
