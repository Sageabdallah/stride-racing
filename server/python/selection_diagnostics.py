#!/usr/bin/env python3
"""Read-only counters for the selection wrapper's unmeasured behaviour.

SYSTEM_MAP §9 lists a set of empirical facts about the live pipeline that
nothing in the repository counts. Each one is a decision the wrapper makes
often, on a hard-coded threshold that was never fitted, with no measurement of
what it costs or saves. This report counts them.

The questions, and why each matters:

  Q7   How often does the crowd gate flip a decision, in each direction, and
       what would the flipped sets have returned? SYSTEM_MAP calls
       "what would the MODEL_ONLY picks have returned?" the single
       highest-leverage unknown in the system.
  Q8   How often does the `odds > 15` hard ceiling (run_tips_pipeline.py:1812)
       kill a pick? It is the largest single ROI constraint in the code and has
       no supporting measurement.
  Q10  How often does mc_is_flat fire? When it does, the whole card is
       zero-staked and the LLM's ranking silently takes over via the [5,3,1]
       boost.
  Q11  How often does apply_safety_filters fall back to the three
       shortest-priced runners? That path directly contradicts the value
       philosophy and nothing counts it.
  Q12  How often does the intelligence override fire? It bypasses the entire
       edge gate.
  Q14  Were the mw ladder and the band thresholds ever fitted? No fitting
       script exists, so the observed edge and price distributions are the only
       evidence available about whether they are doing anything.

Following audit_coverage_report.py: every query is a SELECT, the session is
opened read-only, and the self-test asserts both — this reporter can never
mutate the database (constraint 35).

Nothing here depends on the new selection_ledger table; these run against the
schema as it exists today, which is the point. They can be run before any of
the Workstream A-E work is enabled.

    python selection_diagnostics.py          # self-test (no DB)
    python selection_diagnostics.py --run    # report against DATABASE_URL
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))


# Window applied by every query, so a run is bounded and comparable.
DEFAULT_DAYS = 90


QUERIES = [
    ("Schema — actual column types, so nothing below has to guess", """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name IN ('selections', 'prediction_audit', 'selection_ledger')
          AND column_name IN ('race_date', 'market_odds', 'edge', 'expected_value',
                              'raw_win_prob', 'calibrated_win_prob', 'final_win_prob',
                              'predicted_win_prob', 'win_percentage')
          AND %(days)s = %(days)s
        ORDER BY table_name, column_name
    """),

    ("Q8 — price ceiling: selections by price band (the odds>15 rule)", """
        SELECT CASE
                 WHEN market_odds IS NULL       THEN 'no quote'
                 WHEN market_odds <  3          THEN 'a. <$3'
                 WHEN market_odds <  5          THEN 'b. $3-5'
                 WHEN market_odds <= 15         THEN 'c. $5-15'
                 ELSE                                'd. >$15 (ceiling)'
               END AS price_band,
               COUNT(*)                                   AS runners,
               COUNT(*) FILTER (WHERE is_active)          AS active,
               ROUND(AVG(edge)::numeric, 3)               AS avg_edge_pp,
               ROUND(AVG(win_percentage)::numeric, 2)     AS avg_win_pct
        FROM selections
        WHERE race_date::text >= to_char(CURRENT_DATE - INTERVAL '%(days)s days', 'YYYY-MM-DD')
        GROUP BY price_band
        ORDER BY price_band
    """),

    ("Q7 — convergence gate: how the crowd layer classified each selection", """
        SELECT COALESCE(convergence_gate, '(null)')       AS convergence_gate,
               COUNT(*)                                   AS runners,
               ROUND(AVG(market_odds)::numeric, 2)        AS avg_price,
               ROUND(AVG(edge)::numeric, 3)               AS avg_edge_pp,
               ROUND(AVG(expected_value)::numeric, 4)     AS avg_ev
        FROM selections
        WHERE race_date::text >= to_char(CURRENT_DATE - INTERVAL '%(days)s days', 'YYYY-MM-DD')
        GROUP BY convergence_gate
        ORDER BY runners DESC
    """),

    ("Q14 — edge distribution against the band gates (4 / 2.5 / 3 pp)", """
        SELECT CASE
                 WHEN edge IS NULL   THEN '(null)'
                 WHEN edge <= 0      THEN 'a. <= 0'
                 WHEN edge <  2.5    THEN 'b. 0-2.5'
                 WHEN edge <  3      THEN 'c. 2.5-3'
                 WHEN edge <  4      THEN 'd. 3-4'
                 WHEN edge < 10      THEN 'e. 4-10'
                 ELSE                     'f. >= 10'
               END AS edge_band,
               COUNT(*)                                   AS runners,
               ROUND(AVG(market_odds)::numeric, 2)        AS avg_price,
               ROUND(AVG(win_percentage)::numeric, 2)     AS avg_win_pct
        FROM selections
        WHERE race_date::text >= to_char(CURRENT_DATE - INTERVAL '%(days)s days', 'YYYY-MM-DD')
        GROUP BY edge_band
        ORDER BY edge_band
    """),

    ("Q14 — recalibration: how far the calibration layer moves a probability", """
        SELECT COUNT(*)                                             AS runners,
               COUNT(*) FILTER (WHERE recalibration_applied)        AS recalibrated,
               ROUND(AVG(raw_win_prob)::numeric, 4)                 AS avg_raw,
               ROUND(AVG(calibrated_win_prob)::numeric, 4)          AS avg_calibrated,
               ROUND(AVG(ABS(COALESCE(recalibration_shift, 0)))::numeric, 4)
                                                                    AS avg_abs_shift,
               ROUND(MAX(ABS(COALESCE(recalibration_shift, 0)))::numeric, 4)
                                                                    AS max_abs_shift
        FROM selections
        WHERE race_date::text >= to_char(CURRENT_DATE - INTERVAL '%(days)s days', 'YYYY-MM-DD')
    """),

    ("Q10/Q11 — confidence ladder and the staking it drives", """
        SELECT COALESCE(confidence, '(null)')             AS confidence,
               COUNT(*)                                   AS runners,
               ROUND(AVG(kelly_stake)::numeric, 4)        AS avg_kelly_stake,
               ROUND(AVG(market_odds)::numeric, 2)        AS avg_price,
               ROUND(AVG(edge)::numeric, 3)               AS avg_edge_pp
        FROM selections
        WHERE race_date::text >= to_char(CURRENT_DATE - INTERVAL '%(days)s days', 'YYYY-MM-DD')
        GROUP BY confidence
        ORDER BY runners DESC
    """),

    ("Q12 — value_rating mix (the label the override and merit paths set)", """
        SELECT COALESCE(value_rating, '(null)')           AS value_rating,
               COUNT(*)                                   AS runners,
               ROUND(AVG(edge)::numeric, 3)               AS avg_edge_pp,
               ROUND(AVG(market_odds)::numeric, 2)        AS avg_price
        FROM selections
        WHERE race_date::text >= to_char(CURRENT_DATE - INTERVAL '%(days)s days', 'YYYY-MM-DD')
        GROUP BY value_rating
        ORDER BY runners DESC
    """),

    ("Coverage — selections per day, to size every figure above", """
        SELECT left(race_date::text, 7)                   AS month,
               COUNT(DISTINCT race_date)                  AS race_days,
               COUNT(DISTINCT (track, race_number, race_date)) AS races,
               COUNT(*)                                   AS runners
        FROM selections
        WHERE race_date::text >= to_char(CURRENT_DATE - INTERVAL '%(days)s days', 'YYYY-MM-DD')
        GROUP BY month
        ORDER BY month
    """),

    ("Q1/Q2 — is anything actually calibrated? raw vs calibrated agreement", """
        SELECT CASE
                 WHEN raw_win_prob IS NULL OR calibrated_win_prob IS NULL
                      THEN 'missing one side'
                 WHEN ABS(raw_win_prob - calibrated_win_prob) < 1e-9
                      THEN 'identical (calibrator inert or absent)'
                 ELSE 'differs (calibrator fired)'
               END AS calibration_state,
               COUNT(*)                                   AS runners
        FROM selections
        WHERE race_date::text >= to_char(CURRENT_DATE - INTERVAL '%(days)s days', 'YYYY-MM-DD')
        GROUP BY calibration_state
        ORDER BY runners DESC
    """),

    # No reference to final_win_prob: migrations/final_prob_audit.sql adds it,
    # but naming a column that may not exist takes the whole query down. The
    # schema probe above reports whether it is there.
    ("Q13 — prediction_audit fill rate (was near-empty; migration may be pending)", """
        SELECT left(race_date::text, 7)                   AS month,
               COUNT(*)                                   AS rows
        FROM prediction_audit
        WHERE race_date::text >= to_char(CURRENT_DATE - INTERVAL '%(days)s days', 'YYYY-MM-DD')
        GROUP BY month
        ORDER BY month
    """),
]


_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"REFRESH|COPY|VACUUM|LOCK|CALL|DO)\b",
    re.IGNORECASE,
)


def _fmt_table(columns, rows):
    cells = [["-" if c is None else str(c) for c in r] for r in rows]
    widths = [len(str(c)) for c in columns]
    for r in cells:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    lines = [
        "  " + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(columns)),
        "  " + "  ".join("-" * widths[i] for i in range(len(columns))),
    ]
    for r in cells:
        lines.append("  " + "  ".join(r[i].ljust(widths[i]) for i in range(len(columns))))
    if not cells:
        lines.append("  (no rows)")
    return "\n".join(lines)


def run_report(conn, days=DEFAULT_DAYS):
    """Run every query, returning (ok, failed).

    Individual queries are allowed to fail — a partial report beats none, and a
    column that does not exist should not take the other eight down. But the
    caller MUST act on the counts: the first version of this reporter printed
    nine 'QUERY FAILED' blocks and still exited 0, so the workflow that ran it
    reported success while producing nothing. That is the exact failure mode
    this module exists to detect elsewhere in the system, and silence has to
    cost something here too.
    """
    conn.set_session(readonly=True)  # server-side guarantee, not just convention
    cur = conn.cursor()
    ok, failed = 0, []
    for title, sql in QUERIES:
        print("\n" + "=" * 72)
        print(f"{title}   [last {days} days]")
        print("=" * 72)
        try:
            cur.execute(sql % {"days": int(days)})
            cols = [d[0] for d in cur.description]
            print(_fmt_table(cols, cur.fetchall()))
            ok += 1
        except Exception as exc:  # print and continue: partial report > none
            conn.rollback()
            failed.append(title)
            print(f"  QUERY FAILED: {exc}")
    cur.close()

    print("\n" + "=" * 72)
    print(f"SUMMARY: {ok} of {len(QUERIES)} queries returned rows, {len(failed)} failed")
    print("=" * 72)
    for title in failed:
        print(f"  FAILED: {title}")
    return ok, failed


def _self_test():
    print("=" * 60)
    print("selection_diagnostics self-test (no DB)")
    print("=" * 60)

    for title, sql in QUERIES:
        s = sql.strip()
        assert s.upper().startswith(("SELECT", "WITH")), title
        m = _FORBIDDEN.search(s)
        assert m is None, f"{title}: forbidden word {m.group(0) if m else ''}"
        # every query must be windowed, or a run is unbounded and incomparable
        assert "%(days)s" in s, f"{title}: not windowed"
    print(f"  {len(QUERIES)} queries: all SELECT/WITH, no mutating keywords, all windowed")

    for title, sql in QUERIES:
        rendered = sql % {"days": 90}
        assert "%(days)s" not in rendered
        assert _FORBIDDEN.search(rendered) is None, title
    print("  rendered form stays read-only once the window is substituted")

    assert _FORBIDDEN.search("SELECT created_at FROM t") is None
    assert _FORBIDDEN.search("DELETE FROM t") is not None
    assert _FORBIDDEN.search("select * from t; drop table t") is not None
    print("  forbidden-word guard: created_at passes, DELETE/DROP caught")

    table = _fmt_table(["band", "runners"], [("a. <$3", 12), ("d. >$15", None)])
    lines = table.splitlines()
    assert len(lines) == 4 and "-" in lines[3]
    assert "(no rows)" in _fmt_table(["a"], [])
    print("  formatter: alignment, None->'-', empty-result rendering")

    # --- run_report must REPORT failure, not swallow it ---------------------
    # The first version of this module printed nine "QUERY FAILED" blocks and
    # returned nothing, so the caller exited 0 and the workflow showed a green
    # tick over a report that said nothing at all. These assertions exist
    # because that actually happened.
    class _Cur:
        def __init__(self, outer): self.outer, self.description = outer, [("c",)]
        def execute(self, sql):
            self.outer.executed.append(sql)
            if self.outer.mode == "all_fail":
                raise RuntimeError("operator does not exist: text >= timestamp")
            if self.outer.mode == "one_fail" and len(self.outer.executed) == 2:
                raise RuntimeError('column "final_win_prob" does not exist')
        def fetchall(self): return [("x",)]
        def close(self): pass

    class _Conn:
        def __init__(self, mode): self.mode, self.executed, self.rollbacks = mode, [], 0
        def set_session(self, readonly=False): self.readonly = readonly
        def cursor(self): return _Cur(self)
        def rollback(self): self.rollbacks += 1

    c_ok = _Conn("ok")
    ok, failed = run_report(c_ok, days=30)
    assert c_ok.readonly is True, "the session must be opened read-only"
    assert ok == len(QUERIES) and failed == [], (ok, failed)
    assert all("%(days)s" not in q for q in c_ok.executed), "window not substituted"

    c_bad = _Conn("all_fail")
    ok_b, failed_b = run_report(c_bad, days=30)
    assert ok_b == 0, "total failure must report zero successes"
    assert len(failed_b) == len(QUERIES), failed_b
    assert c_bad.rollbacks == len(QUERIES), "each failure must roll back"

    c_one = _Conn("one_fail")
    ok_o, failed_o = run_report(c_one, days=30)
    assert ok_o == len(QUERIES) - 1 and len(failed_o) == 1, (ok_o, failed_o)
    print(f"  run_report: returns ({len(QUERIES)},0) clean, (0,{len(QUERIES)}) on total "
          f"failure, ({len(QUERIES) - 1},1) on a single bad column — caller exits non-zero")

    # The window predicate must not assume race_date is a DATE. It is TEXT in
    # `selections`, which is what broke every query on the first live run.
    for title, sql in QUERIES:
        if "information_schema" in sql:
            continue          # the schema probe reads catalogue metadata, not races
        if "race_date" in sql and "WHERE" in sql:
            assert "race_date::text >=" in sql, f"{title}: window assumes a date type"
    assert not any("final_win_prob" in sql for _, sql in QUERIES
                   if "prediction_audit" in sql and "information_schema" not in sql), \
        "Q13 must not name a column that migrations/final_prob_audit.sql may not have applied"
    print("  window predicate is type-agnostic; Q13 names no possibly-absent column")

    print("All selection_diagnostics self-tests passed.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Read-only diagnostics for the selection wrapper")
    parser.add_argument("--run", action="store_true",
                        help="Run against DATABASE_URL (read-only session)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"Lookback window in days (default: {DEFAULT_DAYS})")
    args = parser.parse_args()

    if not args.run:
        _self_test()
        sys.exit(0)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)

    import psycopg2
    connection = psycopg2.connect(db_url)
    try:
        ok, failed = run_report(connection, days=args.days)
    finally:
        connection.close()

    # Exit non-zero when the report is empty or degraded, so a CI run cannot
    # show a green tick over a page of failures.
    if ok == 0:
        print("\nERROR: every query failed — this report says nothing about the "
              "system. Check the schema probe output for the real column types.",
              file=sys.stderr)
        sys.exit(1)
    if failed:
        print(f"\nERROR: {len(failed)} of {len(QUERIES)} queries failed — the report "
              "is partial and must not be read as complete.", file=sys.stderr)
        sys.exit(1)
