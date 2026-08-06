#!/usr/bin/env python3
"""MP-4 verification: does the venue-coverage comparison compare the right things?

The morning-odds per-venue postcondition (infra/jobs/handler.py) compares
race_schedule.track against betfair_odds_snapshots.track through the DB
function stride_norm_track. Its ELSE branch used to delete every uppercase
letter ('Canterbury' -> 'anterbury'), so a capitalised schedule row could
never match a lowercased snapshot row and the check reported every venue
missing on a perfect day. Unit tests cannot prove SQL semantics (they fake
the cursor), so this script executes the actual comparison against the real
schema. READ-ONLY: SELECTs only, inside a read-only session.

    python3 scripts/verify_venue_coverage.py            # self-test (no DB)
    python3 scripts/verify_venue_coverage.py --run      # against DATABASE_URL
    python3 scripts/verify_venue_coverage.py --run 2026-08-05 2026-08-06

Expected after the stride_norm_track fix is applied (through the repo's
normal migration path — never by this script):

    2026-08-05: scheduled=4  missing=['Belmont Park', 'Cranbourne']
    2026-08-06: scheduled=1  missing=['Ballarat Synthetic']

If Aug 5 still lists Canterbury or Doomben, the function is not fixed.
The coverage SQL mirrors the handler's check minus the snapshot_time >= t0
freshness bound (that bound means "since this job started", which is only
meaningful inside the 08:00 job; verification asks about the whole day).
"""
import argparse
import os
import re
import sys

# The case table: capitalised schedule values, an already-lowercase snapshot
# value (must be byte-identical through the function), and one name per
# explicit CASE/LIKE alias branch.
CASE_TABLE = [
    "Canterbury",
    "Belmont Park",
    "Ballarat Synthetic",
    "canterbury",
    "Royal Randwick",
    "Caulfield",
]

EXPECTED_NORM = {
    "Canterbury": "canterbury",
    "Belmont Park": "belmontpark",
    "Ballarat Synthetic": "ballaratsynthetic",
    "canterbury": "canterbury",
    "Royal Randwick": "randwick",
    "Caulfield": "caulfield",
}

# Known-answer dates: the 2026-08-05 silent failure (2 of 4 venues dark,
# green tick) and 2026-08-06 (nothing captured at all; the alarm DID fire).
KNOWN_ANSWERS = {
    "2026-08-05": (4, ["Belmont Park", "Cranbourne"]),
    "2026-08-06": (1, ["Ballarat Synthetic"]),
}

DEFAULT_DATES = sorted(KNOWN_ANSWERS)

SCHEDULED_SQL = """
SELECT COUNT(DISTINCT stride_norm_track(track)) FROM race_schedule
WHERE race_date = %s
"""

MISSING_SQL = """
SELECT COALESCE(array_agg(sched.track ORDER BY sched.track), '{}')
FROM (
  SELECT MIN(track) AS track, stride_norm_track(track) AS v
  FROM race_schedule WHERE race_date = %s GROUP BY v
) sched LEFT JOIN (
  SELECT DISTINCT stride_norm_track(track) AS v
  FROM betfair_odds_snapshots
  WHERE race_date = %s AND snapshot_type = 'MORNING_CHECK'
) cov ON cov.v = sched.v
WHERE cov.v IS NULL
"""

QUERIES = [SCHEDULED_SQL, MISSING_SQL]

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"REFRESH|COPY|VACUUM|LOCK|CALL|DO)\b",
    re.IGNORECASE,
)


def run_verification(conn, dates):
    conn.set_session(readonly=True)  # server-side guarantee, not convention
    cur = conn.cursor()
    failures = 0
    try:
        print("stride_norm_track:")
        for name in CASE_TABLE:
            cur.execute("SELECT stride_norm_track(%s)", (name,))
            got = cur.fetchone()[0]
            want = EXPECTED_NORM[name]
            mark = "ok" if got == want else f"EXPECTED {want!r}"
            if got != want:
                failures += 1
            print(f"  {name:<18} -> {got:<18} {mark}")
        print()
        for date in dates:
            cur.execute(SCHEDULED_SQL, (date,))
            scheduled = cur.fetchone()[0]
            cur.execute(MISSING_SQL, (date, date))
            missing = sorted(cur.fetchone()[0])
            line = f"{date}: scheduled={scheduled}  missing={missing}"
            if date in KNOWN_ANSWERS:
                want_count, want_missing = KNOWN_ANSWERS[date]
                ok = scheduled == want_count and missing == want_missing
                line += f"   {'MATCH' if ok else f'MISMATCH (expected scheduled={want_count} missing={want_missing})'}"
                if not ok:
                    failures += 1
            print(line)
    finally:
        cur.close()
    return failures


# ---------------------------------------------------------------- self-test
def _self_test():
    print("verify_venue_coverage self-test (no DB)")
    for sql in QUERIES + ["SELECT stride_norm_track(%s)"]:
        s = sql.strip()
        assert s.upper().startswith(("SELECT", "WITH")), s[:60]
        m = _FORBIDDEN.search(s)
        assert m is None, f"forbidden word {m.group(0) if m else ''}"
    print(f"  {len(QUERIES) + 1} queries: all SELECT, no mutating keywords")
    # The comparison must normalise BOTH sides — a one-sided edit is the
    # regression this script exists to catch.
    assert MISSING_SQL.count("stride_norm_track(track)") == 2
    assert "snapshot_time >=" not in MISSING_SQL  # whole-day, not job-fresh
    assert sorted(EXPECTED_NORM) == sorted(CASE_TABLE)
    for name, want in EXPECTED_NORM.items():
        assert want == want.lower(), name
    print("  both-sides normalisation, whole-day bound, case table: ok")
    print("All verify_venue_coverage self-tests passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="store_true",
                        help="Run read-only verification against DATABASE_URL")
    parser.add_argument("dates", nargs="*", default=DEFAULT_DATES,
                        help="Race dates to check (default: the known-answer dates)")
    args = parser.parse_args()

    if not args.run:
        _self_test()
        sys.exit(0)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)
    import psycopg2  # lazy: self-test path must not require it
    conn = psycopg2.connect(db_url)
    try:
        sys.exit(1 if run_verification(conn, args.dates) else 0)
    finally:
        conn.close()
