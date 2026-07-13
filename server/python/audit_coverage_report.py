"""
Read-only diagnostic: where does prediction_audit full-field coverage die?

Why: the LambdaRank head-to-head run (docs/12 §5.4) found that only
1,199 of 8,472 usable races in training_view_v2 have stored probabilities
covering the full field, and coverage dries up after ~Nov 2025 — yet
mc_api.log_prediction_audit writes unconditionally on every simulated race
(and silently swallows failures by design). Two hypotheses separate
cleanly with data:

  A. Writes stopped — a production env/connection change made
     log_prediction_audit no-op silently. Signature: the raw
     prediction_audit monthly counts fall off a cliff.
  B. Writes continued but the view's normalized join fails on newer rows
     (track/horse-name/date format drift vs race_results_history).
     Signature: raw counts stay healthy while race-key or horse-level
     match rates collapse.

This module prints monthly tables that answer exactly that, using the same
stride_norm_track / stride_norm_name functions the materialized view uses,
so a mismatch here IS the view's mismatch. Every query is a SELECT and the
session is opened read-only — this reporter can never mutate the database.

Usage:
    python audit_coverage_report.py          # self-test (no DB): read-only
                                             # guards + formatter behaviour
    python audit_coverage_report.py --run    # run the report (DATABASE_URL)

The audit-coverage GitHub Action runs --run next to the database and the
run log is the report.
"""

import argparse
import os
import re
import sys

# (title, sql) executed in order. Type-agnostic on purpose: race_date is
# text in prediction_audit and date-or-text elsewhere, so every date is
# handled as left(x::text, n) and nothing is ever cast to ::date.
QUERIES = [
    (
        "1. prediction_audit writes by race month (raw, no join)",
        """
        SELECT left(race_date::text, 7) AS month,
               COUNT(*) AS rows,
               COUNT(DISTINCT (stride_norm_track(track), race_number,
                               left(race_date::text, 10))) AS races,
               left(MIN(created_at)::text, 10) AS first_write,
               left(MAX(created_at)::text, 10) AS last_write
        FROM prediction_audit
        WHERE race_date IS NOT NULL AND race_date::text <> ''
        GROUP BY 1 ORDER BY 1
        """,
    ),
    (
        "2. unjoinable audit rows (empty race_date) by write month",
        """
        SELECT left(created_at::text, 7) AS write_month,
               COUNT(*) AS rows
        FROM prediction_audit
        WHERE race_date IS NULL OR race_date::text = ''
        GROUP BY 1 ORDER BY 1
        """,
    ),
    (
        "3. training_view_v2 stored-prob coverage by month",
        """
        WITH per_race AS (
            SELECT left(race_date::text, 7) AS month,
                   track, race_number, left(race_date::text, 10) AS rd,
                   COUNT(*) AS runners,
                   COUNT(predicted_win_prob) AS covered
            FROM training_view_v2
            GROUP BY 1, 2, 3, 4
        )
        SELECT month,
               COUNT(*) AS races,
               SUM(CASE WHEN covered > 0 THEN 1 ELSE 0 END) AS any_coverage,
               SUM(CASE WHEN covered = runners AND runners >= 4
                        THEN 1 ELSE 0 END) AS full_coverage
        FROM per_race GROUP BY 1 ORDER BY 1
        """,
    ),
    (
        "4. audit race keys unmatched in race_results_history (view join, race level)",
        """
        WITH audit_races AS (
            SELECT DISTINCT stride_norm_track(track) AS tn,
                   race_number AS rn,
                   left(race_date::text, 10) AS rd
            FROM prediction_audit
            WHERE race_date IS NOT NULL AND race_date::text <> ''
              AND predicted_win_prob IS NOT NULL
        ),
        rrh_races AS (
            SELECT DISTINCT stride_norm_track(track) AS tn,
                   race_number AS rn,
                   left(race_date::text, 10) AS rd
            FROM race_results_history
        )
        SELECT left(a.rd, 7) AS month,
               COUNT(*) AS audit_races,
               SUM(CASE WHEN r.tn IS NULL THEN 1 ELSE 0 END) AS race_key_unmatched
        FROM audit_races a
        LEFT JOIN rrh_races r
          ON r.tn = a.tn AND r.rn = a.rn AND r.rd = a.rd
        GROUP BY 1 ORDER BY 1
        """,
    ),
    (
        "5. audit horse rows unmatched in race_results_history (view join, horse level)",
        """
        WITH a AS (
            SELECT stride_norm_track(track) AS tn,
                   race_number AS rn,
                   left(race_date::text, 10) AS rd,
                   stride_norm_name(horse_name) AS hn
            FROM prediction_audit
            WHERE race_date IS NOT NULL AND race_date::text <> ''
              AND horse_name IS NOT NULL AND predicted_win_prob IS NOT NULL
        ),
        r AS (
            SELECT DISTINCT stride_norm_track(track) AS tn,
                   race_number AS rn,
                   left(race_date::text, 10) AS rd,
                   stride_norm_name(horse_name) AS hn
            FROM race_results_history
            WHERE horse_name IS NOT NULL
        )
        SELECT left(a.rd, 7) AS month,
               COUNT(*) AS audit_rows,
               SUM(CASE WHEN r.hn IS NULL THEN 1 ELSE 0 END) AS horse_unmatched
        FROM a
        LEFT JOIN r
          ON r.tn = a.tn AND r.rn = a.rn AND r.rd = a.rd AND r.hn = a.hn
        GROUP BY 1 ORDER BY 1
        """,
    ),
    (
        "6. newest unmatched audit races, raw values (eyeball the format drift)",
        """
        WITH audit_races AS (
            SELECT stride_norm_track(track) AS tn,
                   race_number AS rn,
                   left(race_date::text, 10) AS rd,
                   MIN(track) AS raw_track,
                   COUNT(*) AS rows
            FROM prediction_audit
            WHERE race_date IS NOT NULL AND race_date::text <> ''
              AND predicted_win_prob IS NOT NULL
            GROUP BY 1, 2, 3
        ),
        rrh_races AS (
            SELECT DISTINCT stride_norm_track(track) AS tn,
                   race_number AS rn,
                   left(race_date::text, 10) AS rd
            FROM race_results_history
        )
        SELECT a.rd AS race_date, a.raw_track AS track,
               a.rn AS race_number, a.rows
        FROM audit_races a
        LEFT JOIN rrh_races r
          ON r.tn = a.tn AND r.rn = a.rn AND r.rd = a.rd
        WHERE r.tn IS NULL
        ORDER BY a.rd DESC
        LIMIT 15
        """,
    ),
    (
        "7. race_results_history spine by month (context)",
        """
        SELECT left(race_date::text, 7) AS month,
               COUNT(DISTINCT (stride_norm_track(track), race_number,
                               left(race_date::text, 10))) AS races,
               COUNT(*) AS rows
        FROM race_results_history
        GROUP BY 1 ORDER BY 1
        """,
    ),
    (
        "8. selections stored by race month (did the pipeline itself run?)",
        """
        SELECT left(race_date::text, 7) AS month,
               COUNT(*) AS selections
        FROM selections
        WHERE race_date IS NOT NULL AND race_date::text <> ''
        GROUP BY 1 ORDER BY 1
        """,
    ),
]

# Words that must never appear in a diagnostic query. \b keeps created_at
# from tripping the CREATE check.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"REFRESH|COPY|VACUUM|LOCK|CALL|DO)\b",
    re.IGNORECASE,
)


def _fmt_table(columns, rows):
    """Plain aligned text table; None renders as '-'."""
    cells = [[("-" if v is None else str(v)) for v in row] for row in rows]
    widths = [
        max(len(str(c)), *(len(r[i]) for r in cells)) if cells else len(str(c))
        for i, c in enumerate(columns)
    ]
    lines = [
        "  " + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(columns)),
        "  " + "  ".join("-" * widths[i] for i in range(len(columns))),
    ]
    for r in cells:
        lines.append("  " + "  ".join(r[i].ljust(widths[i]) for i in range(len(columns))))
    if not cells:
        lines.append("  (no rows)")
    return "\n".join(lines)


def run_report(conn):
    conn.set_session(readonly=True)  # server-side guarantee, not just convention
    cur = conn.cursor()
    for title, sql in QUERIES:
        print("\n" + "=" * 72)
        print(title)
        print("=" * 72)
        try:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            print(_fmt_table(cols, cur.fetchall()))
        except Exception as exc:  # print and continue: partial report > none
            conn.rollback()
            print(f"  QUERY FAILED: {exc}")
    cur.close()


# ---------------------------------------------------------------- self-test
def _self_test():
    print("=" * 60)
    print("audit_coverage_report self-test (no DB)")
    print("=" * 60)

    for title, sql in QUERIES:
        s = sql.strip()
        assert s.upper().startswith(("SELECT", "WITH")), title
        m = _FORBIDDEN.search(s)
        assert m is None, f"{title}: forbidden word {m.group(0) if m else ''}"
    print(f"  {len(QUERIES)} queries: all SELECT/WITH, no mutating keywords")

    # the guard itself must not false-positive on created_at / false-negative
    assert _FORBIDDEN.search("SELECT created_at FROM t") is None
    assert _FORBIDDEN.search("DELETE FROM t") is not None
    assert _FORBIDDEN.search("select * from t; drop table t") is not None
    print("  forbidden-word guard: created_at passes, DELETE/DROP caught")

    table = _fmt_table(
        ["month", "rows", "note"],
        [("2025-11", 1234, None), ("2025-12", 7, "x")],
    )
    lines = table.splitlines()
    assert len(lines) == 4 and "2025-11" in lines[2] and "-" in lines[2]
    empty = _fmt_table(["a"], [])
    assert "(no rows)" in empty
    print("  formatter: alignment, None->'-', empty-result rendering")
    print("All audit_coverage_report self-tests passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="prediction_audit coverage diagnostic")
    parser.add_argument("--run", action="store_true",
                        help="Run the read-only report against DATABASE_URL")
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
        run_report(conn)
    finally:
        conn.close()
