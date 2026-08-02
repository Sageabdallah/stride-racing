#!/usr/bin/env python3
"""Seed race_schedule for a date, decoupled from the tips pipeline (WP-2).

The Betfair capture maps Exchange markets against race_schedule, but until
now the table only filled when the tips pipeline ran (mc_api) or when
results_projection seeded it from prediction_audit after the fact. A
scheduled morning capture therefore mapped nothing. This script seeds the
schedule from whichever source exists, in order of preference:

  1. racecards/racecard_<date>.json (track, race_number, off_time)
  2. prediction_audit rows for the date (results_projection helper)

Idempotent: the insert is ON CONFLICT DO NOTHING on the natural key, same
as mc_api.log_race_schedule. Exit codes: 0 = schedule present after run
(seeded now or already there), 1 = nothing to seed from, 2 = config error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CARD_DIRS = (PROJECT_ROOT / "racecards",
             Path(__file__).resolve().parent / "racecards")

INSERT_SQL = """
INSERT INTO race_schedule (track, race_number, race_date, off_time,
                           result_due_at, result_status)
VALUES (%s, %s, %s, %s, %s, 'pending')
ON CONFLICT (track, race_number, race_date) DO NOTHING
"""


def seed_from_racecard(cur, date_str: str) -> int:
    card = None
    for d in CARD_DIRS:
        p = d / f"racecard_{date_str}.json"
        if p.exists():
            card = json.loads(p.read_text())
            break
    if card is None:
        return 0
    inserted = 0
    for meet in card if isinstance(card, list) else [card]:
        for race in meet.get("races", []):
            if race.get("is_trial") or race.get("is_jump_out"):
                continue
            track = race.get("course") or meet.get("course")
            number = race.get("race_number")
            off_raw = race.get("off_time")
            if not track or number is None:
                continue
            off_time = None
            due = None
            if off_raw:
                try:
                    off_time = datetime.fromisoformat(
                        str(off_raw).replace("Z", "+00:00"))
                    due = off_time + timedelta(minutes=30)
                except (ValueError, TypeError):
                    pass
            cur.execute(INSERT_SQL,
                        (track, int(number), date_str, off_time, due))
            inserted += cur.rowcount
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="?",
                        default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("CONFIG ERROR: DATABASE_URL not set", file=sys.stderr)
        return 2
    conn = psycopg2.connect(url)
    cur = conn.cursor()

    inserted = seed_from_racecard(cur, args.date)
    source = "racecard"
    if inserted == 0:
        from results_projection import ensure_race_schedule_from_prediction_audit
        inserted = ensure_race_schedule_from_prediction_audit(conn, args.date)
        source = "prediction_audit"
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM race_schedule WHERE race_date = %s",
                (args.date,))
    total = cur.fetchone()[0]
    conn.close()
    print(f"[SCHEDULE] {args.date}: +{inserted} rows from {source}, "
          f"{total} total for the date")
    if total == 0:
        print(f"[SCHEDULE] nothing to seed {args.date} from: no racecard "
              "file and no prediction_audit rows", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
