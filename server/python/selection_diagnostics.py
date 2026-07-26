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
        WHERE race_date >= CURRENT_DATE - INTERVAL '%(days)s days'
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
        WHERE race_date >= CURRENT_DATE - INTERVAL '%(days)s days'
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
        WHERE race_date >= CURRENT_DATE - INTERVAL '%(days)s days'
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
        WHERE race_date >= CURRENT_DATE - INTERVAL '%(days)s days'
    """),

    ("Q10/Q11 — confidence ladder and the staking it drives", """
        SELECT COALESCE(confidence, '(null)')             AS confidence,
               COUNT(*)                                   AS runners,
               ROUND(AVG(kelly_stake)::numeric, 4)        AS avg_kelly_stake,
               ROUND(AVG(market_odds)::numeric, 2)        AS avg_price,
               ROUND(AVG(edge)::numeric, 3)               AS avg_edge_pp
        FROM selections
        WHERE race_date >= CURRENT_DATE - INTERVAL '%(days)s days'
        GROUP BY confidence
        ORDER BY runners DESC
    """),

    ("Q12 — value_rating mix (the label the override and merit paths set)", """
        SELECT COALESCE(value_rating, '(null)')           AS value_rating,
               COUNT(*)                                   AS runners,
               ROUND(AVG(edge)::numeric, 3)               AS avg_edge_pp,
               ROUND(AVG(market_odds)::numeric, 2)        AS avg_price
        FROM selections
        WHERE race_date >= CURRENT_DATE - INTERVAL '%(days)s days'
        GROUP BY value_rating
        ORDER BY runners DESC
    """),

    ("Coverage — selections per day, to size every figure above", """
        SELECT left(race_date::text, 7)                   AS month,
               COUNT(DISTINCT race_date)                  AS race_days,
               COUNT(DISTINCT (track, race_number, race_date)) AS races,
               COUNT(*)                                   AS runners
        FROM selections
        WHERE race_date >= CURRENT_DATE - INTERVAL '%(days)s days'
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
        WHERE race_date >= CURRENT_DATE - INTERVAL '%(days)s days'
        GROUP BY calibration_state
        ORDER BY runners DESC
    """),

    ("Q13 — prediction_audit fill rate (was near-empty; migration may be pending)", """
        SELECT left(race_date::text, 7)                   AS month,
               COUNT(*)                                   AS rows,
               COUNT(final_win_prob)                      AS with_final_prob
        FROM prediction_audit
        WHERE race_date >= CURRENT_DATE - INTERVAL '%(days)s days'
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
    conn.set_session(readonly=True)  # server-side guarantee, not just convention
    cur = conn.cursor()
    for title, sql in QUERIES:
        print("\n" + "=" * 72)
        print(f"{title}   [last {days} days]")
        print("=" * 72)
        try:
            cur.execute(sql % {"days": int(days)})
            cols = [d[0] for d in cur.description]
            print(_fmt_table(cols, cur.fetchall()))
        except Exception as exc:  # print and continue: partial report > none
            conn.rollback()
            print(f"  QUERY FAILED: {exc}")
    cur.close()


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
        run_report(connection, days=args.days)
    finally:
        connection.close()
