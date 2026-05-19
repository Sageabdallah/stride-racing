#!/usr/bin/env python3
"""Fast targeted z-score backfill: only races that currently have NULL z_200m."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:
    pass

import psycopg2
from psycopg2.extras import execute_batch
from sectional_quant import _compute_z_scores_for_race


UPDATE_SQL = """
    UPDATE sectional_times
    SET z_200m=%s, z_400m=%s, z_600m=%s, z_800m=%s
    WHERE track=%s AND race_date=%s AND race_number=%s AND horse_name=%s
"""


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("DATABASE_URL not set")

    conn = psycopg2.connect(db_url)
    conn.autocommit = False  # batched commits to avoid per-row round trips

    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT track, race_date, race_number
            FROM sectional_times
            WHERE last_200m_speed IS NOT NULL
              AND z_200m IS NULL
            ORDER BY race_date, track, race_number
        """)
        races = cur.fetchall()

    total = len(races)
    print(f"Races needing z-scores: {total}", flush=True)

    start = time.time()
    updated = 0
    skipped = 0
    pending = []
    BATCH = 500

    cur = conn.cursor()

    def flush():
        nonlocal updated
        if not pending:
            return
        execute_batch(cur, UPDATE_SQL, pending, page_size=200)
        conn.commit()
        updated += len(pending)
        pending.clear()

    for idx, (track, race_date, race_number) in enumerate(races, 1):
        try:
            z_dict = _compute_z_scores_for_race(conn, track, race_date, race_number)
        except Exception as e:
            print(f"  WARN {track} {race_date} R{race_number}: {e}", flush=True)
            z_dict = {}

        if not z_dict:
            skipped += 1
        else:
            for horse_name, z in z_dict.items():
                pending.append((
                    z.get('z_200m'), z.get('z_400m'), z.get('z_600m'), z.get('z_800m'),
                    track, race_date, race_number, horse_name,
                ))

        if len(pending) >= BATCH:
            flush()

        if idx % 50 == 0 or idx == total:
            elapsed = time.time() - start
            rate = idx / elapsed if elapsed else 0
            eta = (total - idx) / rate if rate else 0
            print(f"  [{idx}/{total}] updated={updated} skipped={skipped} elapsed={elapsed:.0f}s ETA={eta:.0f}s", flush=True)

    flush()
    cur.close()
    conn.close()

    print(f"\nDone. Races processed: {total}, rows updated: {updated}, skipped (<3 runners): {skipped}")


if __name__ == "__main__":
    main()
