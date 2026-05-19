#!/usr/bin/env python3
"""Backfill z_200m, z_400m, z_600m, z_800m sectional z-score columns for all existing sectional_times rows."""

import os
import sys
import statistics
import time
from datetime import datetime

PYTHON_DIR = r'c:\Users\sagea\OneDrive\Desktop\Race-Analytics\Race-Analytics\server\python'
sys.path.insert(0, PYTHON_DIR)

ENV_PATH = r'c:\Users\sagea\OneDrive\Desktop\Race-Analytics\Race-Analytics\.env'

def _load_env(path: str) -> None:
    """Minimal .env loader — sets os.environ for KEY=VALUE lines."""
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
        print(f"[env] Loaded environment from {path}")
    except FileNotFoundError:
        print(f"[env] WARNING: .env file not found at {path}", file=sys.stderr)

_load_env(ENV_PATH)

# Try python-dotenv as well (if installed), which handles edge cases better
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH, override=False)
except ImportError:
    pass  # Fallback already applied above

try:
    import psycopg2
    import psycopg2.extras
except ImportError as exc:
    sys.exit(f"[FATAL] psycopg2 not installed: {exc}")

from sectional_quant import _compute_z_scores_for_race


COMMIT_EVERY_N_RACES = 50    # Commit after this many races processed
LOG_EVERY_N_RACES    = 100   # Print progress after this many races processed



def get_connection():
    """Open a psycopg2 connection using DATABASE_URL from the environment."""
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        sys.exit('[FATAL] DATABASE_URL not found in environment. Check your .env file.')
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = False
        return conn
    except psycopg2.OperationalError as exc:
        sys.exit(f'[FATAL] Cannot connect to database: {exc}')


def fetch_distinct_races(conn) -> list:
    """Return all distinct (track, race_date, race_number) tuples that have at least one row with a non-null last_200m sectional time."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT track, race_date, race_number
            FROM   sectional_times
            WHERE  last_200m_speed IS NOT NULL
            ORDER  BY race_date ASC, track ASC, race_number ASC
        """)
        rows = cur.fetchall()
    return rows


def update_z_scores_for_horse(cur, track: str, race_date, race_number: int,
                               horse_name: str, z: dict) -> int:
    """
    UPDATE a single row's z-score columns.

    Returns 1 if a row was matched, 0 otherwise.
    """
    cur.execute(
        """
        UPDATE sectional_times
        SET    z_200m = %s,
               z_400m = %s,
               z_600m = %s,
               z_800m = %s
        WHERE  track        = %s
          AND  race_date    = %s
          AND  race_number  = %s
          AND  horse_name   = %s
        """,
        (
            z.get('z_200m'),
            z.get('z_400m'),
            z.get('z_600m'),
            z.get('z_800m'),
            track,
            race_date,
            race_number,
            horse_name,
        )
    )
    return cur.rowcount



def compute_distribution_stats(values: list) -> dict:
    """Return basic descriptive statistics for a list of floats."""
    if not values:
        return {}
    n = len(values)
    mu = statistics.mean(values)
    try:
        sigma = statistics.stdev(values)
    except statistics.StatisticsError:
        sigma = 0.0
    sorted_v = sorted(values)
    p25 = sorted_v[int(n * 0.25)]
    p50 = sorted_v[int(n * 0.50)]
    p75 = sorted_v[int(n * 0.75)]
    return {
        'n':      n,
        'mean':   round(mu, 4),
        'std':    round(sigma, 4),
        'min':    round(sorted_v[0], 4),
        'p25':    round(p25, 4),
        'p50':    round(p50, 4),
        'p75':    round(p75, 4),
        'max':    round(sorted_v[-1], 4),
    }



def backfill_z_scores() -> None:
    start_time = time.time()
    print(f"\n{'='*60}")
    print(f"  Z-Score Backfill  —  started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    conn = get_connection()
    print("[db] Connected to database.")

    print("[db] Fetching distinct races with sectional data...")
    races = fetch_distinct_races(conn)
    total_races = len(races)
    print(f"[db] Found {total_races:,} distinct races to process.\n")

    if total_races == 0:
        print("Nothing to do — no rows with last_200m_speed found.")
        conn.close()
        return

    races_processed   = 0
    races_skipped     = 0   # fewer than 3 runners → _compute returns {}
    horses_updated    = 0
    horses_no_match   = 0   # z-score computed but DB row not matched
    pending_commit    = 0

    all_z200: list = []
    all_z400: list = []
    all_z600: list = []
    all_z800: list = []

    cur = conn.cursor()

    for idx, (track, race_date, race_number) in enumerate(races, start=1):

        try:
            z_dict = _compute_z_scores_for_race(conn, track, race_date, race_number)
        except Exception as exc:
            print(f"  [WARN] Exception computing z-scores for "
                  f"{track} {race_date} R{race_number}: {exc}")
            z_dict = {}

        if not z_dict:
            races_skipped += 1
            races_processed += 1
            pending_commit += 1
        else:
            for horse_name, z in z_dict.items():
                try:
                    matched = update_z_scores_for_horse(
                        cur, track, race_date, race_number, horse_name, z
                    )
                    if matched:
                        horses_updated += 1
                        if z.get('z_200m') is not None:
                            all_z200.append(z['z_200m'])
                        if z.get('z_400m') is not None:
                            all_z400.append(z['z_400m'])
                        if z.get('z_600m') is not None:
                            all_z600.append(z['z_600m'])
                        if z.get('z_800m') is not None:
                            all_z800.append(z['z_800m'])
                    else:
                        horses_no_match += 1
                except Exception as exc:
                    print(f"  [WARN] Update failed for {horse_name} in "
                          f"{track} {race_date} R{race_number}: {exc}")

            races_processed += 1
            pending_commit  += 1

        if pending_commit >= COMMIT_EVERY_N_RACES:
            conn.commit()
            pending_commit = 0

        if idx % LOG_EVERY_N_RACES == 0 or idx == total_races:
            elapsed = time.time() - start_time
            rate = idx / elapsed if elapsed > 0 else 0
            eta_s  = (total_races - idx) / rate if rate > 0 else 0
            print(
                f"  [{idx:>6,}/{total_races:,}]  "
                f"horses_updated={horses_updated:,}  "
                f"skipped={races_skipped:,}  "
                f"elapsed={elapsed:.1f}s  "
                f"rate={rate:.1f} races/s  "
                f"ETA={eta_s:.0f}s"
            )

    if pending_commit > 0:
        conn.commit()

    cur.close()
    conn.close()

    elapsed_total = time.time() - start_time

    print(f"\n{'='*60}")
    print(f"  Backfill Complete  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    print(f"  Total races found         : {total_races:,}")
    print(f"  Races processed           : {races_processed:,}")
    print(f"  Races skipped (<3 runners): {races_skipped:,}")
    print(f"  Horses updated            : {horses_updated:,}")
    print(f"  Horse rows not matched    : {horses_no_match:,}")
    print(f"  Total runtime             : {elapsed_total:.1f}s")

    print(f"\n--- Z-Score Distribution (sample from updated rows) ---")
    for label, values in [
        ('z_200m', all_z200),
        ('z_400m', all_z400),
        ('z_600m', all_z600),
        ('z_800m', all_z800),
    ]:
        stats = compute_distribution_stats(values)
        if stats:
            print(
                f"  {label}: n={stats['n']:,}  "
                f"mean={stats['mean']:+.4f}  std={stats['std']:.4f}  "
                f"min={stats['min']:+.4f}  p25={stats['p25']:+.4f}  "
                f"p50={stats['p50']:+.4f}  p75={stats['p75']:+.4f}  "
                f"max={stats['max']:+.4f}"
            )
        else:
            print(f"  {label}: no data")

    print()


if __name__ == '__main__':
    backfill_z_scores()
