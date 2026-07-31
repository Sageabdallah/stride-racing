#!/usr/bin/env python3
"""Automated Race Results Collector — monitors race_schedule, fetches results from Punting Form (The Racing API is dead), and updates prediction_audit. Derived analytics/training tables are projected from prediction_audit."""

import os
import re
import sys
import time
import argparse
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from results_projection import project_resulted_prediction_audit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pf_client
from pf_results_mapper import aus_race_meetings


def normalize_name(value):
    """Strip to lowercase alphanumeric only."""
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


TRACK_ALIASES = {
    "rosehill": "rosehillgardens",
    "rosehillgardens": "rosehillgardens",
    "ascot": "ascotwa",
    "ascotwa": "ascotwa",
    "randwick": "randwick",
    "royalrandwick": "randwick",
    "kensington": "kensington",
    "flemington": "flemington",
    "morphettville": "morphettville",
    "morphettvilleparks": "morphettville",
    "doomben": "doomben",
    "eaglefarm": "eaglefarm",
    "caulfield": "caulfield",
    "mrc": "caulfield",
    "mooneevalley": "mooneevalley",
    "sandownhillside": "sandownhillside",
    "sandownlakeside": "sandownlakeside",
    "sandown": "sandownhillside",
    "bendigo": "bendigo",
    "ballarat": "ballarat",
    "geelong": "geelong",
    "pakenham": "pakenham",
    "cranbourne": "cranbourne",
}


def canonical_track_key(value):
    """Canonicalize track name for reliable matching."""
    text = normalize_name(value)
    return TRACK_ALIASES.get(text, text)


def normalize_finish_position(raw_position, field_size: Optional[int] = None) -> Optional[int]:
    if isinstance(raw_position, str):
        raw_position = ''.join(filter(str.isdigit, raw_position)) or None
    try:
        position = int(raw_position) if raw_position is not None else None
    except (ValueError, TypeError):
        return None
    if position is None or position <= 0:
        return None
    if position >= 100:
        return None
    if field_size and position > field_size:
        return None
    return position


def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL environment variable required")
    return psycopg2.connect(database_url)


def get_pending_races(conn, target_date: Optional[str] = None) -> List[Dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if target_date:
            cur.execute("""
                SELECT id, track, race_number, race_date, off_time, result_due_at,
                       result_status, retry_count
                FROM race_schedule
                WHERE result_status = 'pending'
                  AND retry_count < 5
                  AND race_date = %s
                ORDER BY race_date, race_number
            """, (target_date,))
        else:
            cur.execute("""
                SELECT id, track, race_number, race_date, off_time, result_due_at,
                       result_status, retry_count
                FROM race_schedule
                WHERE result_status = 'pending'
                  AND result_due_at <= NOW()
                  AND retry_count < 5
                ORDER BY race_date, race_number
            """)
        return [dict(row) for row in cur.fetchall()]


def fetch_results_for_date(race_date: str) -> List[Dict]:
    """Fetch resulted races for a date from Punting Form (The Racing API
    ceased AU coverage; its credentials return 401). Returns the same
    internal shape as before: {course, track, race_number, race_name,
    distance, runners:[{horse_name, horse, position, sp}]}. Error contract
    unchanged: failures return [] so the caller increments retry_count and
    comes back later instead of settling on partial data."""
    try:
        meetings = aus_race_meetings(pf_client.meetings_for_date(race_date))
    except pf_client.PFError as e:
        print(f"[AutoResults] PF meetings fetch failed for {race_date}: {e}", file=sys.stderr)
        return []

    print(f"[AutoResults] Found {len(meetings)} meets for {race_date}", file=sys.stderr)

    all_results = []
    for meet in meetings:
        meet_id = meet.get('meetingId')
        track_name = (meet.get('track') or {}).get('name') or 'Unknown'
        if not meet_id:
            continue

        try:
            blocks = pf_client.results_for_meeting(meet_id)
        except pf_client.PFError as e:
            print(f"[AutoResults] Error fetching races for {track_name}: {e}", file=sys.stderr)
            continue

        for block in blocks or []:
            block_track = block.get('track') or track_name
            for rr in block.get('raceResults') or []:
                runners = rr.get('runners') or []
                has_results = any(
                    normalize_finish_position(r.get('position')) is not None
                    for r in runners)
                if not has_results:
                    continue
                all_results.append({
                    'course': block_track,
                    'track': block_track,
                    'race_number': rr.get('raceNumber'),
                    'race_name': rr.get('raceName') or '',
                    'distance': rr.get('distance') or '',
                    'runners': [
                        {'horse_name': r.get('runner'),
                         'horse': r.get('runner'),
                         'position': r.get('position'),
                         'sp': r.get('price')}
                        for r in runners
                    ]
                })

    print(f"[AutoResults] Found {len(all_results)} completed races with results", file=sys.stderr)
    return all_results


def find_race_in_results(track: str, race_number: int, results: List[Dict]) -> Optional[Dict]:
    sel_canonical = canonical_track_key(track)
    for result in results:
        result_track = result.get('course') or result.get('track', '')
        result_race_num_raw = result.get('race_number') or result.get('number', 0)
        try:
            result_race_num = int(result_race_num_raw) if result_race_num_raw else 0
        except (ValueError, TypeError):
            result_race_num = 0

        track_match = sel_canonical == canonical_track_key(result_track)

        if track_match and race_number == result_race_num:
            return result
    return None


def extract_runner_result(runner: Dict, field_size: Optional[int] = None) -> Dict:
    position = normalize_finish_position(
        runner.get('position') or runner.get('finishing_position') or runner.get('finish_position'),
        field_size=field_size,
    )

    sp_raw = runner.get('sp') or runner.get('starting_price') or runner.get('win_odds') or runner.get('odds')
    if isinstance(sp_raw, dict):
        sp_raw = sp_raw.get('decimal') or sp_raw.get('dec') or sp_raw.get('value') or 0
    try:
        if isinstance(sp_raw, str):
            sp_raw = float(sp_raw.replace('$', '').strip())
        else:
            sp_raw = float(sp_raw) if sp_raw else 0.0
    except (ValueError, TypeError):
        sp_raw = 0.0

    return {
        'position': position,
        'starting_price': sp_raw
    }


def get_runner_name(runner: Dict) -> str:
    return (runner.get('horse_name') or runner.get('horse') or
            runner.get('name') or runner.get('runner_name', '')).lower().strip()


def update_prediction_audit(conn, track: str, race_number: int, race_date: str, race_result: Dict) -> int:
    runners = race_result.get('runners', [])
    field_size = len([r for r in runners if r.get('position') or r.get('finishing_position')])
    updated_count = 0

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, horse_name
            FROM prediction_audit
            WHERE track = %s AND race_number = %s AND race_date = %s
              AND (result_status = 'pending' OR result_status IS NULL)
        """, (track, race_number, race_date))
        audit_rows = cur.fetchall()

    if not audit_rows:
        return 0

    for audit_row in audit_rows:
        audit_horse = audit_row['horse_name'].lower().strip()
        audit_norm = normalize_name(audit_horse)

        best_match = None
        for runner in runners:
            runner_name = get_runner_name(runner)
            if not runner_name:
                continue
            runner_norm = normalize_name(runner_name)
            if audit_norm == runner_norm:
                best_match = runner
                break
            if best_match is None and (audit_norm in runner_norm or runner_norm in audit_norm):
                best_match = runner

        if best_match:
            result_data = extract_runner_result(best_match, field_size=field_size)
            position = result_data['position']
            sp = result_data['starting_price']
            won = position == 1
            placed = bool(position is not None and position <= 3)

            if won and sp > 0:
                profit_loss = (sp - 1) * 100
            else:
                profit_loss = -100.0

            with conn.cursor() as cur2:
                cur2.execute("""
                    UPDATE prediction_audit
                    SET actual_position = %s,
                        field_size = %s,
                        won = %s,
                        placed = %s,
                        starting_price = %s,
                        profit_loss = %s,
                        result_status = 'resulted',
                        result_collected_at = NOW()
                    WHERE id = %s
                """, (position, field_size, won, placed, sp if sp > 0 else None,
                      round(profit_loss, 2), audit_row['id']))

                if sp > 0:
                    cur2.execute("""
                        UPDATE race_results_history
                        SET sp_odds = %s
                        WHERE track ILIKE %s AND race_number = %s
                          AND race_date::date = %s::date
                          AND lower(regexp_replace(coalesce(horse_name,''), '[^a-z0-9]+', '', 'g')) = %s
                          AND sp_odds IS NULL
                    """, (sp, track, race_number, race_date, audit_norm))

            updated_count += 1

    return updated_count


def update_race_schedule_collected(conn, schedule_id: str):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE race_schedule
            SET result_status = 'collected',
                result_collected_at = NOW()
            WHERE id = %s
        """, (schedule_id,))


def increment_retry_count(conn, schedule_id: str):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE race_schedule
            SET retry_count = retry_count + 1
            WHERE id = %s
        """, (schedule_id,))


def collect_sectional_times(dates_needed):
    """Collect QLD sectional times for given dates using the sectional_times_collector."""
    try:
        from sectional_times_collector import QLD_TRACKS, collect_for_date_track
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            return 0
        
        total_imported = 0
        for race_date in dates_needed:
            for track_name in QLD_TRACKS:
                runners, matched, imported, skipped = collect_for_date_track(
                    race_date, track_name, db_url, verbose=False
                )
                if imported > 0:
                    print(f"[AutoResults] Sectionals: {track_name} {race_date} - {imported} imported ({matched} matched)", file=sys.stderr)
                    total_imported += imported
        
        if total_imported > 0:
            print(f"[AutoResults] Total QLD sectional times collected: {total_imported}", file=sys.stderr)
        return total_imported
    except Exception as e:
        print(f"[AutoResults] QLD Sectional collection error: {e}", file=sys.stderr)
        return 0


def collect_racing_com_sectionals(dates_needed):
    """Collect VIC/SA sectional times from racing.com API."""
    try:
        from racing_com_sectionals_collector import collect_for_date
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            return 0
        
        total_imported = 0
        for race_date in dates_needed:
            try:
                imported = collect_for_date(race_date, states="VIC|SA", db_url=db_url, verbose=False)
                if imported > 0:
                    print(f"[AutoResults] Racing.com sectionals: {race_date} - {imported} imported", file=sys.stderr)
                    total_imported += imported
            except Exception as e:
                print(f"[AutoResults] Racing.com sectionals error for {race_date}: {e}", file=sys.stderr)
        
        if total_imported > 0:
            print(f"[AutoResults] Total racing.com sectional times collected: {total_imported}", file=sys.stderr)
        return total_imported
    except Exception as e:
        print(f"[AutoResults] Racing.com sectional collection error: {e}", file=sys.stderr)
        return 0


def _ensure_sp_backfill_index(conn):
    """Create composite index for efficient SP backfill queries if not exists."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rrh_date_track_racenum
                ON race_results_history (race_date, track, race_number)
            """)
        conn.commit()
    except Exception as e:
        print(f"[AutoResults] Index creation skipped: {e}", file=sys.stderr)
        conn.rollback()


def process_pending_races(target_date: Optional[str] = None) -> Dict:
    conn = get_db_connection()
    _ensure_sp_backfill_index(conn)
    pending = get_pending_races(conn, target_date)

    if not pending:
        print(f"[AutoResults] No pending races to process", file=sys.stderr)
        conn.close()
        return {
            'success': True,
            'message': 'No pending races to process',
            'races_checked': 0,
            'results_collected': 0
        }

    print(f"[AutoResults] Found {len(pending)} pending race(s) to process", file=sys.stderr)

    dates_needed = set(r['race_date'] for r in pending)
    results_cache: Dict[str, List[Dict]] = {}

    for race_date in dates_needed:
        print(f"[AutoResults] Fetching results for {race_date}...", file=sys.stderr)
        results_cache[race_date] = fetch_results_for_date(race_date)

    total_collected = 0
    total_audit_updated = 0
    total_failed = 0

    for race in pending:
        schedule_id = race['id']
        track = race['track']
        race_number = race['race_number']
        race_date = race['race_date']

        results_for_date = results_cache.get(race_date, [])

        if not results_for_date:
            print(f"[AutoResults] No results available for {race_date}, incrementing retry for {track} R{race_number}", file=sys.stderr)
            increment_retry_count(conn, schedule_id)
            conn.commit()
            total_failed += 1
            continue

        race_result = find_race_in_results(track, race_number, results_for_date)

        if not race_result:
            print(f"[AutoResults] Race not found in results: {track} R{race_number} on {race_date}", file=sys.stderr)
            increment_retry_count(conn, schedule_id)
            conn.commit()
            total_failed += 1
            continue

        try:
            audit_updated = update_prediction_audit(conn, track, race_number, race_date, race_result)
            update_race_schedule_collected(conn, schedule_id)
            conn.commit()

            total_collected += 1
            total_audit_updated += audit_updated
            print(f"[AutoResults] Collected: {track} R{race_number} ({race_date}) - {audit_updated} audit rows updated", file=sys.stderr)

        except Exception as e:
            conn.rollback()
            print(f"[AutoResults] Error processing {track} R{race_number}: {e}", file=sys.stderr)
            increment_retry_count(conn, schedule_id)
            conn.commit()
            total_failed += 1

    conn.close()

    projected_selection_results = 0
    projected_training_rows = 0
    if total_collected > 0:
        projection_conn = get_db_connection()
        try:
            for race_date in sorted(dates_needed):
                projection = project_resulted_prediction_audit(projection_conn, race_date)
                projected_selection_results += projection.get('selection_results_inserted', 0)
                projected_training_rows += projection.get('training_data_inserted', 0)
            projection_conn.commit()
        except Exception as e:
            projection_conn.rollback()
            print(f"[AutoResults] Projection error: {e}", file=sys.stderr)
        finally:
            projection_conn.close()

    sectionals_collected = 0
    racing_com_sectionals = 0
    if total_collected > 0:
        sectionals_collected = collect_sectional_times(dates_needed)
        racing_com_sectionals = collect_racing_com_sectionals(dates_needed)

    summary = {
        'success': True,
        'races_checked': len(pending),
        'results_collected': total_collected,
        'audit_rows_updated': total_audit_updated,
        'selection_results_projected': projected_selection_results,
        'training_rows_projected': projected_training_rows,
        'failed': total_failed,
        'sectionals_collected': sectionals_collected + racing_com_sectionals,
        'sectionals_qld': sectionals_collected,
        'sectionals_racing_com': racing_com_sectionals,
        'dates_processed': list(dates_needed),
        'timestamp': datetime.now().isoformat()
    }

    print(
        f"[AutoResults] Summary: {total_collected} collected, "
        f"{total_audit_updated} audit rows updated, "
        f"{projected_selection_results} selection rows projected, "
        f"{projected_training_rows} training rows projected, "
        f"{total_failed} failed, {sectionals_collected} QLD sectionals, "
        f"{racing_com_sectionals} racing.com sectionals",
        file=sys.stderr
    )
    return summary


def run_daemon(check_interval: int = 5):
    print(f"[AutoResults] Starting daemon mode, checking every {check_interval} minutes", file=sys.stderr)
    print(f"[AutoResults] Press Ctrl+C to stop", file=sys.stderr)

    while True:
        try:
            print(f"\n[AutoResults] [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running check...", file=sys.stderr)
            result = process_pending_races()
            if result.get('results_collected', 0) > 0:
                print(f"[AutoResults] Collected {result['results_collected']} race result(s) this cycle", file=sys.stderr)
        except KeyboardInterrupt:
            print(f"\n[AutoResults] Daemon stopped by user", file=sys.stderr)
            break
        except Exception as e:
            print(f"[AutoResults] Error in daemon loop: {e}", file=sys.stderr)

        try:
            time.sleep(check_interval * 60)
        except KeyboardInterrupt:
            print(f"\n[AutoResults] Daemon stopped by user", file=sys.stderr)
            break


def main():
    parser = argparse.ArgumentParser(
        description='Automated Race Results Collector - fetches results and updates prediction_audit table'
    )
    parser.add_argument(
        '--date',
        type=str,
        default=None,
        help='Process a specific date (YYYY-MM-DD). Default: process all pending races due now.'
    )
    parser.add_argument(
        '--check-interval',
        type=int,
        default=0,
        help='If > 0, loop every N minutes checking for new races to result. Default: 0 (run once).'
    )
    parser.add_argument(
        '--daemon',
        action='store_true',
        help='Run continuously, checking every 5 minutes for races needing results.'
    )

    args = parser.parse_args()

    if not os.environ.get('DATABASE_URL'):
        print("ERROR: DATABASE_URL environment variable is required", file=sys.stderr)
        sys.exit(1)

    target_date = args.date
    if target_date is None and args.check_interval == 0 and not args.daemon:
        target_date = datetime.now().strftime('%Y-%m-%d')

    if args.daemon:
        run_daemon(check_interval=args.check_interval if args.check_interval > 0 else 5)
    elif args.check_interval > 0:
        run_daemon(check_interval=args.check_interval)
    else:
        result = process_pending_races(target_date)
        import json
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
