#!/usr/bin/env python3
"""
SP data quality health helpers.
"""

from typing import Dict, List

from psycopg2.extras import RealDictCursor


def compute_sp_health(conn, dates: List[str]) -> Dict:
    if not dates:
        return {
            "status": "NO_DATA",
            "rows_considered": 0,
            "rows_with_sp": 0,
            "coverage_pct": 0.0,
            "avg_sp": None,
            "suspect_sp_equals_race_number": 0,
            "corrupted_data": False,
            "notes": ["No dates supplied."],
            "suspect_samples": [],
        }

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) AS rows_considered,
                COUNT(*) FILTER (WHERE sp_odds IS NOT NULL) AS rows_with_sp,
                AVG(sp_odds) FILTER (WHERE sp_odds IS NOT NULL) AS avg_sp,
                COUNT(*) FILTER (
                    WHERE sp_odds IS NOT NULL
                      AND race_number BETWEEN 1 AND 12
                      AND sp_odds = race_number
                ) AS suspect_sp_equals_race_number
            FROM race_results_history
            WHERE race_date = ANY(%s)
            """,
            (dates,),
        )
        stats = dict(cur.fetchone() or {})

        cur.execute(
            """
            SELECT
                race_date::text AS race_date,
                track,
                race_number,
                horse_name,
                sp_odds,
                position
            FROM race_results_history
            WHERE race_date = ANY(%s)
              AND sp_odds IS NOT NULL
              AND race_number BETWEEN 1 AND 12
              AND sp_odds = race_number
            ORDER BY race_date DESC, track, race_number, horse_name
            LIMIT 10
            """,
            (dates,),
        )
        suspect_samples = [dict(row) for row in cur.fetchall()]

    rows_considered = int(stats.get("rows_considered") or 0)
    rows_with_sp = int(stats.get("rows_with_sp") or 0)
    coverage_pct = round((rows_with_sp / rows_considered) * 100.0, 1) if rows_considered else 0.0
    avg_sp_raw = stats.get("avg_sp")
    avg_sp = round(float(avg_sp_raw), 2) if avg_sp_raw is not None else None
    suspect_count = int(stats.get("suspect_sp_equals_race_number") or 0)

    notes = []
    corrupted_data = bool(avg_sp is not None and avg_sp <= 2.0)
    if corrupted_data:
        status = "CORRUPTED"
        notes.append("Average SP is <= 2.0, which indicates corrupted or unusably skewed odds data.")
    elif suspect_count > 0:
        status = "AMBER"
        notes.append("Some SP values exactly match race_number; these rows are suspicious and need review.")
    elif coverage_pct >= 90.0:
        status = "GREEN"
    elif coverage_pct >= 80.0:
        status = "AMBER"
    else:
        status = "RED"

    return {
        "status": status,
        "rows_considered": rows_considered,
        "rows_with_sp": rows_with_sp,
        "coverage_pct": coverage_pct,
        "avg_sp": avg_sp,
        "suspect_sp_equals_race_number": suspect_count,
        "corrupted_data": corrupted_data,
        "notes": notes,
        "suspect_samples": suspect_samples,
    }
