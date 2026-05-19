#!/usr/bin/env python3
"""Checks SP quality and invalid finishing positions for a given date."""

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

import psycopg2
from psycopg2.extras import RealDictCursor

from sp_health import compute_sp_health


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        return database_url

    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                database_url = line.split("=", 1)[1].strip()
                break

    if not database_url:
        raise RuntimeError("DATABASE_URL not found in environment or project .env")

    os.environ["DATABASE_URL"] = database_url
    return database_url


def get_connection():
    return psycopg2.connect(load_database_url())


def compute_position_health(conn, date_str: str) -> Dict:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) AS rows_considered,
                COUNT(*) FILTER (
                    WHERE position IS NOT NULL
                      AND (position >= 100 OR (field_size IS NOT NULL AND position > field_size))
                ) AS invalid_positions,
                COUNT(*) FILTER (WHERE position IS NULL) AS null_positions
            FROM race_results_history
            WHERE race_date::date = %s::date
            """,
            (date_str,),
        )
        stats = dict(cur.fetchone() or {})

        cur.execute(
            """
            SELECT
                race_date::text AS race_date,
                track,
                race_number,
                horse_name,
                position,
                field_size
            FROM race_results_history
            WHERE race_date::date = %s::date
              AND position IS NOT NULL
              AND (position >= 100 OR (field_size IS NOT NULL AND position > field_size))
            ORDER BY track, race_number, horse_name
            LIMIT 10
            """,
            (date_str,),
        )
        samples = [dict(row) for row in cur.fetchall()]

    rows_considered = int(stats.get("rows_considered") or 0)
    invalid_positions = int(stats.get("invalid_positions") or 0)
    null_positions = int(stats.get("null_positions") or 0)
    invalid_pct = round((invalid_positions / rows_considered) * 100.0, 2) if rows_considered else 0.0

    notes = []
    if rows_considered == 0:
        status = "NO_DATA"
        notes.append("No RRH rows found for this date.")
    elif invalid_positions > 0:
        status = "CORRUPTED"
        notes.append("Invalid finishing positions remain after import; investigate source mapping or normalization.")
    elif null_positions > 0:
        status = "AMBER"
        notes.append("Some finishing positions are null, usually due to invalid source status codes or scratches.")
    else:
        status = "GREEN"

    return {
        "status": status,
        "rows_considered": rows_considered,
        "invalid_positions": invalid_positions,
        "invalid_pct": invalid_pct,
        "null_positions": null_positions,
        "notes": notes,
        "samples": samples,
    }


def overall_status(sp_health: Dict, position_health: Dict) -> str:
    statuses = {sp_health.get("status"), position_health.get("status")}
    if "CORRUPTED" in statuses:
        return "CORRUPTED"
    if "AMBER" in statuses:
        return "AMBER"
    if statuses == {"GREEN"}:
        return "GREEN"
    return "NO_DATA"


def main():
    parser = argparse.ArgumentParser(description="Results data quality health check")
    parser.add_argument("--date", help="Race date YYYY-MM-DD (defaults to yesterday)")
    args = parser.parse_args()

    if args.date:
        date_str = args.date
    else:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    with get_connection() as conn:
        sp_health = compute_sp_health(conn, [date_str])
        position_health = compute_position_health(conn, date_str)

    print(json.dumps({
        "date": date_str,
        "overall_status": overall_status(sp_health, position_health),
        "sp_health": sp_health,
        "position_health": position_health,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }, indent=2))


if __name__ == "__main__":
    main()
