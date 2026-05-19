#!/usr/bin/env python3
"""
Canonical results orchestrator that uses prediction_audit as the single ingestion source of truth.

Flow:
1) Ensure race_schedule has pending rows from prediction_audit.
2) Run auto_results_collector to fetch/match/update prediction_audit.
3) Project resulted rows from prediction_audit into compatibility tables:
   - selection_results (analytics/UI)
   - training_data (legacy consumers)
"""

import json
import os
import sys
from datetime import datetime
from typing import Dict

import psycopg2
from psycopg2.extras import RealDictCursor

from auto_results_collector import process_pending_races
from results_projection import (
    ensure_race_schedule_from_prediction_audit,
    project_resulted_prediction_audit,
)
from sp_health import compute_sp_health


def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable required")
    return psycopg2.connect(database_url)


def summarize_selection_results(conn, race_date: str) -> Dict:
    """Summarize projected selection_results for compatibility with existing API consumers."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) AS results_matched,
                COALESCE(SUM(CASE WHEN won THEN 1 ELSE 0 END), 0) AS winners,
                COALESCE(SUM(CASE WHEN placed THEN 1 ELSE 0 END), 0) AS placers,
                COALESCE(SUM(profit_loss), 0) AS profit_loss
            FROM selection_results
            WHERE race_date = %s
            """,
            (race_date,),
        )
        row = dict(cur.fetchone() or {})

        cur.execute(
            """
            SELECT COUNT(*) AS selections_found
            FROM selections
            WHERE race_date = %s
            """,
            (race_date,),
        )
        sel_row = dict(cur.fetchone() or {})

    matched = int(row.get("results_matched") or 0)
    winners = int(row.get("winners") or 0)
    placers = int(row.get("placers") or 0)
    profit_loss = float(row.get("profit_loss") or 0.0)

    strike_rate = (winners / matched * 100.0) if matched > 0 else 0.0
    roi = (profit_loss / (matched * 100.0) * 100.0) if matched > 0 else 0.0

    return {
        "selections_found": int(sel_row.get("selections_found") or 0),
        "results_matched": matched,
        "winners": winners,
        "placers": placers,
        "strike_rate": round(strike_rate, 1),
        "roi": round(roi, 1),
        "profit_loss": round(profit_loss, 2),
    }


def collect_results_for_date(race_date: str) -> Dict:
    """Canonical one-shot collection for a date."""
    seed_conn = get_db_connection()
    try:
        seeded = ensure_race_schedule_from_prediction_audit(seed_conn, race_date)
        seed_conn.commit()
    finally:
        seed_conn.close()

    auto_summary = process_pending_races(race_date)

    proj_conn = get_db_connection()
    try:
        projection = project_resulted_prediction_audit(proj_conn, race_date)
        summary = summarize_selection_results(proj_conn, race_date)
        sp_health = compute_sp_health(proj_conn, [race_date])
        proj_conn.commit()
    finally:
        proj_conn.close()

    return {
        "success": True,
        "date": race_date,
        "canonical_source": "prediction_audit",
        "race_schedule_seeded": seeded,
        "results_collected": int(auto_summary.get("results_collected", 0)),
        "audit_rows_updated": int(auto_summary.get("audit_rows_updated", 0)),
        "selection_results_projected": int(projection.get("selection_results_inserted", 0)),
        "training_rows_projected": int(projection.get("training_data_inserted", 0)),
        "sp_health": sp_health,
        **summary,
    }


def archive_results_to_training(race_date: str) -> Dict:
    """
    Backward-compatible archive mode.
    Archives from canonical prediction_audit instead of selection_results.
    """
    conn = get_db_connection()
    try:
        projection = project_resulted_prediction_audit(
            conn,
            race_date=race_date,
            project_selection_results=False,
            project_training_data=True,
        )
        conn.commit()
        archived = int(projection.get("training_data_inserted", 0))
        return {
            "success": True,
            "date": race_date,
            "archived": archived,
            "message": f"Archived {archived} rows from prediction_audit to training_data",
        }
    finally:
        conn.close()


def main():
    race_date = datetime.now().strftime("%Y-%m-%d")
    archive_mode = False

    if len(sys.argv) > 1:
        if sys.argv[1] == "--archive":
            archive_mode = True
            if len(sys.argv) > 2:
                race_date = sys.argv[2]
        else:
            race_date = sys.argv[1]

    try:
        if archive_mode:
            result = archive_results_to_training(race_date)
        else:
            result = collect_results_for_date(race_date)
        print(json.dumps(result))
    except Exception as e:
        import traceback

        print(
            json.dumps(
                {
                    "success": False,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }
            )
        )


if __name__ == "__main__":
    main()
