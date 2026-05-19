#!/usr/bin/env python3
"""
Batch script to populate/refresh the horse_prep_profiles table.
Computes per-horse win rate and performance-vs-expectation (PvE)
for each combination of distance band × run number in campaign.

PvE metric: expected_position = field_size × (1 - 1/sp_odds)
            pve = expected_position - actual_position
            Positive = beat market expectation.
"""

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from psycopg2.extras import execute_values

from intelligence_common import get_connection

from fitness_peak import _detect_preparations, _parse_date

SPELL_THRESHOLD_DAYS = 60
MIN_RUNS = 5

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS horse_prep_profiles (
    horse_id         TEXT NOT NULL,
    distance_band    TEXT NOT NULL,
    run_number       INTEGER NOT NULL,
    starts           INTEGER NOT NULL DEFAULT 0,
    wins             INTEGER NOT NULL DEFAULT 0,
    places           INTEGER NOT NULL DEFAULT 0,
    avg_pve          NUMERIC(8,4),
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (horse_id, distance_band, run_number)
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_hpp_horse_id ON horse_prep_profiles (horse_id);
"""

UPSERT_SQL = """
INSERT INTO horse_prep_profiles (horse_id, distance_band, run_number, starts, wins, places, avg_pve, updated_at)
VALUES %s
ON CONFLICT (horse_id, distance_band, run_number)
DO UPDATE SET
    starts = EXCLUDED.starts,
    wins = EXCLUDED.wins,
    places = EXCLUDED.places,
    avg_pve = EXCLUDED.avg_pve,
    updated_at = NOW();
"""


def classify_distance_band(distance_m):
    """Classify distance into sprint/middle/staying."""
    if distance_m is None:
        return "middle"
    d = int(distance_m)
    if d < 1200:
        return "sprint"
    if d <= 1600:
        return "middle"
    return "staying"


def calculate_pve(position, sp_odds, field_size):
    """
    Calculate performance-vs-expectation.
    Returns float or None if data insufficient.
    """
    if position is None or sp_odds is None or field_size is None:
        return None
    try:
        pos = int(position)
        odds = float(sp_odds)
        fs = int(field_size)
    except (ValueError, TypeError):
        return None
    if odds <= 1.0 or fs <= 0 or pos <= 0:
        return None
    expected_position = fs * (1.0 - 1.0 / odds)
    return expected_position - pos


def build_profiles_for_horse(runs):
    """
    Build prep profile matrix for a single horse's run history.
    Returns list of (distance_band, run_number, starts, wins, places, avg_pve) tuples.
    """
    formatted_runs = []
    for r in runs:
        formatted_runs.append({
            'race_date': r['race_date'],
            'position': r['position'],
            'distance_m': r['distance_m'],
            'sp_odds': r['sp_odds'],
            'field_size': r['field_size'],
        })

    preps = _detect_preparations(formatted_runs)
    if not preps:
        return []

    stats = defaultdict(lambda: {'starts': 0, 'wins': 0, 'places': 0, 'pve_values': []})

    for prep in preps:
        for run in prep:
            rn = run.get('run_number', 1)
            rn_capped = min(rn, 5)
            dist_band = classify_distance_band(run.get('distance_m'))
            key = (dist_band, rn_capped)

            pos = run.get('position')
            stats[key]['starts'] += 1
            if pos is not None:
                try:
                    p = int(pos)
                    if p == 1:
                        stats[key]['wins'] += 1
                        stats[key]['places'] += 1
                    elif p <= 3:
                        stats[key]['places'] += 1
                except (ValueError, TypeError):
                    pass

            pve = calculate_pve(pos, run.get('sp_odds'), run.get('field_size'))
            if pve is not None:
                stats[key]['pve_values'].append(pve)

    results = []
    for (dist_band, rn), s in stats.items():
        avg_pve = None
        if s['pve_values']:
            avg_pve = round(sum(s['pve_values']) / len(s['pve_values']), 4)
        results.append((dist_band, rn, s['starts'], s['wins'], s['places'], avg_pve))

    return results


def main():
    parser = argparse.ArgumentParser(description="Build horse prep profiles table.")
    parser.add_argument('--horse-ids', nargs='+', help='Specific horse_ids to refresh')
    parser.add_argument('--since', type=str, help='Only re-process horses with runs since YYYY-MM-DD')
    parser.add_argument('--batch-size', type=int, default=500, help='Batch size for processing')
    args = parser.parse_args()

    conn = get_connection()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SET statement_timeout TO 0")

    print("[PREP PROFILES] Creating table if not exists...", file=sys.stderr)
    cur.execute(CREATE_TABLE_SQL)
    cur.execute(CREATE_INDEX_SQL)

    if args.horse_ids:
        horse_ids = args.horse_ids
        print(f"[PREP PROFILES] Processing {len(horse_ids)} specified horses", file=sys.stderr)
    elif args.since:
        cur.execute("""
            SELECT horse_id, COUNT(*) as cnt
            FROM race_results_history
            WHERE horse_id IS NOT NULL
            GROUP BY horse_id
            HAVING COUNT(*) >= %s
              AND MAX(race_date::date) >= %s::date
            ORDER BY horse_id
        """, (MIN_RUNS, args.since))
        horse_ids = [row[0] for row in cur.fetchall()]
        print(f"[PREP PROFILES] Found {len(horse_ids)} horses with {MIN_RUNS}+ runs and activity since {args.since}", file=sys.stderr)
    else:
        cur.execute("""
            SELECT horse_id, COUNT(*) as cnt
            FROM race_results_history
            WHERE horse_id IS NOT NULL
            GROUP BY horse_id
            HAVING COUNT(*) >= %s
            ORDER BY horse_id
        """, (MIN_RUNS,))
        horse_ids = [row[0] for row in cur.fetchall()]
        print(f"[PREP PROFILES] Found {len(horse_ids)} horses with {MIN_RUNS}+ runs", file=sys.stderr)

    if not horse_ids:
        print("[PREP PROFILES] No horses to process", file=sys.stderr)
        conn.close()
        return

    start_time = time.time()
    total_upserted = 0
    total_processed = 0

    for batch_start in range(0, len(horse_ids), args.batch_size):
        batch = horse_ids[batch_start:batch_start + args.batch_size]
        batch_num = batch_start // args.batch_size + 1
        total_batches = (len(horse_ids) + args.batch_size - 1) // args.batch_size

        print(f"[PREP PROFILES] Batch {batch_num}/{total_batches} ({len(batch)} horses)...", file=sys.stderr)

        cur.execute("""
            SELECT horse_id, race_date, position, distance_m, sp_odds, field_size
            FROM race_results_history
            WHERE horse_id = ANY(%s)
              AND position IS NOT NULL
            ORDER BY horse_id, race_date::date ASC, race_number ASC, id ASC
        """, (batch,))

        rows = cur.fetchall()

        runs_by_horse = defaultdict(list)
        for row in rows:
            runs_by_horse[row[0]].append({
                'race_date': row[1],
                'position': row[2],
                'distance_m': row[3],
                'sp_odds': row[4],
                'field_size': row[5],
            })

        upsert_batch = []
        for hid in batch:
            runs = runs_by_horse.get(hid, [])
            if len(runs) < MIN_RUNS:
                continue

            profiles = build_profiles_for_horse(runs)
            for dist_band, rn, starts, wins, places, avg_pve in profiles:
                upsert_batch.append((hid, dist_band, rn, starts, wins, places, avg_pve))

            total_processed += 1

        if upsert_batch:
            execute_values(
                cur,
                UPSERT_SQL,
                upsert_batch,
                template="(%s, %s, %s, %s, %s, %s, %s, NOW())",
                page_size=1000,
            )
            total_upserted += len(upsert_batch)
            print(
                f"[PREP PROFILES] Batch {batch_num} wrote {len(upsert_batch)} profile rows "
                f"(total {total_upserted})",
                file=sys.stderr
            )

    elapsed = time.time() - start_time
    print(
        f"[PREP PROFILES] Done. Processed {total_processed} horses, "
        f"upserted {total_upserted} profile rows in {elapsed:.1f}s",
        file=sys.stderr
    )

    conn.close()


if __name__ == '__main__':
    main()
