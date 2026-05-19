#!/usr/bin/env python3
"""
NSW Sectional Times Collector (Punter's Intelligence / pidata API)

Downloads per-horse sectional timing data from Racing NSW's pidata API.
Parses .tol files containing GPS-based 200m interval sectionals.

API: http://pidata.racingnsw.com.au/RNSW/RacesLogsMetadata.json
Data: Per-horse intermediate times at 200m intervals (1200m, 1000m, 800m, 600m, 400m, 200m, Finish)

Coverage: 16 NSW tracks (Randwick, Rosehill, Canterbury, Newcastle, Hawkesbury,
          Kembla Grange, Warwick Farm, Gosford, Scone, Wyong, etc.)
"""

import os
import sys
import json
import argparse
import time
import re
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(__file__))

PIDATA_BASE = "http://pidata.racingnsw.com.au/RNSW"
METADATA_URL = f"{PIDATA_BASE}/RacesLogsMetadata.json"

NSW_VENUE_ALIASES = {
    "royal randwick": "Royal Randwick",
    "randwick": "Royal Randwick",
    "rosehill gardens": "Rosehill Gardens",
    "rosehill": "Rosehill Gardens",
    "canterbury park": "Canterbury Park",
    "canterbury": "Canterbury Park",
    "warwick farm": "Warwick Farm",
    "newcastle": "Newcastle",
    "beaumont newcastle": "Beaumont Newcastle",
    "hawkesbury": "Hawkesbury",
    "kembla grange": "Kembla Grange",
    "gosford": "Gosford",
    "scone": "Scone",
    "wyong": "Wyong",
    "kensington": "Kensington",
    "muswellbrook": "Muswellbrook",
    "tamworth": "Tamworth",
    "nowra": "Nowra",
    "port macquarie": "Port Macquarie",
}


def normalize_venue(raw):
    if not raw:
        return raw
    return NSW_VENUE_ALIASES.get(raw.strip().lower(), raw.strip())


def normalize_track_key(raw):
    normalized = normalize_venue(raw) if raw else ""
    return re.sub(r"[^a-z0-9]+", "", str(normalized).lower())


def track_match_score(left, right):
    left_key = normalize_track_key(left)
    right_key = normalize_track_key(right)
    if not left_key or not right_key:
        return 0
    if left_key == right_key:
        return 3
    if left_key in right_key or right_key in left_key:
        return 2
    return 1 if SequenceMatcher(None, left_key, right_key).ratio() >= 0.85 else 0


from horse_names import normalize_horse_name, horse_name_match


def get_db_connection():
    try:
        import psycopg2
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            print("ERROR: DATABASE_URL not set")
            sys.exit(1)
        return psycopg2.connect(db_url)
    except ImportError:
        print("ERROR: psycopg2 not installed")
        sys.exit(1)


def fetch_metadata():
    try:
        req = urllib.request.Request(METADATA_URL)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"ERROR fetching metadata: {e}")
        return None


def download_tol_file(filename):
    url = f"{PIDATA_BASE}/{urllib.request.quote(filename)}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f"  ERROR downloading {filename}: {e}")
        return None


def parse_tol_data(tol_text):
    lines = tol_text.strip().split('\n')

    race_info = {}
    horses = {}
    sections = []
    intermediates = []
    results = {}
    final_times = {}
    track_condition = None

    for line in lines:
        line = line.strip('\r')
        if not line or 'HR|' not in line:
            continue

        pipe_start = line.find('HR|')
        if pipe_start < 0:
            continue
        msg = line[pipe_start:]
        parts = msg.split('|')
        if len(parts) < 4:
            continue

        msg_type = parts[3]

        if msg_type == 'R!' and len(parts) >= 13:
            race_info = {
                'race_id': parts[4],
                'race_number': int(parts[5]) if parts[5].isdigit() else 0,
                'race_name': parts[6],
                'scheduled_time': parts[7],
                'distance': float(parts[8]) if parts[8].replace('.', '').isdigit() else 0,
                'track_length': float(parts[9]) if parts[9].replace('.', '').isdigit() else 0,
                'num_runners': int(parts[11]) if parts[11].isdigit() else 0,
                'horse_ids': parts[12:] if len(parts) > 12 else [],
            }

        elif msg_type == 'V!' and len(parts) >= 6:
            race_info['venue'] = parts[5]

        elif msg_type == 'H' and len(parts) >= 7:
            horse_id = parts[4]
            horse_name = parts[5]
            status = parts[6]
            barrier = int(parts[7]) if len(parts) > 7 and parts[7].isdigit() else 0
            saddle = int(parts[12]) if len(parts) > 12 and parts[12].isdigit() else 0
            horses[horse_id] = {
                'name': horse_name,
                'status': status,
                'barrier': barrier,
                'saddle_number': saddle,
            }

        elif msg_type == 'S!' and len(parts) >= 7:
            sections.append({
                'race_id': parts[4],
                'index': int(parts[5]) if parts[5].isdigit() else -1,
                'name': parts[6],
                'distance': float(parts[7]) if len(parts) > 7 and parts[7].replace('.', '').replace('-', '').isdigit() else 0,
            })

        elif msg_type == 'I' and len(parts) >= 12:
            intermediates.append({
                'race_id': parts[4],
                'horse_id': parts[5],
                'section_index': int(parts[6]) if parts[6].isdigit() else -1,
                'section_name': parts[7],
                'cumulative_time_str': parts[8],
                'section_time_str': parts[9],
                'position': int(parts[10]) if parts[10].isdigit() else 0,
                'speed1': float(parts[11]) if parts[11].replace('.', '').replace('-', '').isdigit() else 0,
                'speed2': float(parts[12]) if len(parts) > 12 and parts[12].replace('.', '').replace('-', '').isdigit() else 0,
            })

        elif msg_type == 'R' and len(parts) >= 6 and msg_type != 'R!':
            i = 5
            while i + 1 < len(parts):
                hid = parts[i]
                pos_str = parts[i + 1] if i + 1 < len(parts) else '--'
                if pos_str != '--' and pos_str.isdigit():
                    results[hid] = int(pos_str)
                i += 2

        elif msg_type == 'F' and len(parts) >= 6:
            i = 5
            while i + 1 < len(parts):
                hid = parts[i]
                time_str = parts[i + 1] if i + 1 < len(parts) else '--:--.--'
                if time_str != '--:--.--':
                    final_times[hid] = time_str
                i += 2

        elif msg_type == 'A' and len(parts) >= 7:
            track_condition = parts[6] if len(parts) > 6 else None

    return {
        'race_info': race_info,
        'horses': horses,
        'sections': sections,
        'intermediates': intermediates,
        'results': results,
        'final_times': final_times,
        'track_condition': track_condition,
    }


def parse_time_str(time_str):
    if not time_str or time_str in ['--:--.--', 'NaN', '--']:
        return None
    try:
        time_str = time_str.strip()
        if ':' in time_str:
            parts = time_str.split(':')
            if len(parts) == 2:
                mins = float(parts[0])
                secs = float(parts[1])
                return mins * 60 + secs
            elif len(parts) == 3:
                hours = float(parts[0])
                mins = float(parts[1])
                secs = float(parts[2])
                return hours * 3600 + mins * 60 + secs
        return float(time_str)
    except (ValueError, TypeError):
        return None


def build_horse_sectionals(parsed):
    horse_data = {}
    intermediates = parsed['intermediates']
    horses = parsed['horses']
    race_info = parsed['race_info']
    distance = race_info.get('distance', 0)

    for inter in intermediates:
        hid = inter['horse_id']
        if hid not in horse_data:
            horse_info = horses.get(hid, {})
            horse_data[hid] = {
                'horse_id': hid,
                'horse_name': horse_info.get('name', f'Unknown_{hid}'),
                'saddle_number': horse_info.get('saddle_number', 0),
                'barrier': horse_info.get('barrier', 0),
                'sections': {},
            }

        section_name = inter['section_name']
        section_time = parse_time_str(inter['section_time_str'])
        cumulative_time = parse_time_str(inter['cumulative_time_str'])

        horse_data[hid]['sections'][section_name] = {
            'section_index': inter['section_index'],
            'section_name': section_name,
            'section_time': section_time,
            'cumulative_time': cumulative_time,
            'position': inter['position'],
            'speed_ms': inter['speed1'],
            'peak_speed_ms': inter['speed2'],
        }

    for hid, hdata in horse_data.items():
        secs = hdata['sections']

        splits = {}
        for sname, sdata in sorted(secs.items(), key=lambda x: x[1]['section_index']):
            splits[sname] = {
                'time': round(sdata['section_time'], 3) if sdata['section_time'] else None,
                'cumulative': round(sdata['cumulative_time'], 3) if sdata['cumulative_time'] else None,
                'position': sdata['position'],
                'speed_ms': round(sdata['speed_ms'], 3) if sdata['speed_ms'] else None,
                'peak_speed_ms': round(sdata['peak_speed_ms'], 3) if sdata['peak_speed_ms'] else None,
            }

        hdata['splits_json'] = splits

        if '200m' in secs:
            s = secs['200m']
            hdata['last_200m_time'] = s['section_time']
            hdata['last_200m_speed'] = s['speed_ms']
        if '400m' in secs:
            s200 = secs.get('200m', {})
            s400 = secs['400m']
            t200 = s200.get('section_time')
            t400 = s400.get('section_time')
            if t200 is not None and t400 is not None:
                hdata['last_400m_time'] = t200 + t400
            else:
                hdata['last_400m_time'] = s400.get('cumulative_time')
            sp200 = s200.get('speed_ms')
            sp400 = s400.get('speed_ms')
            if sp200 is not None and sp400 is not None:
                hdata['last_400m_speed'] = (sp200 + sp400) / 2
            else:
                hdata['last_400m_speed'] = sp400
        if '600m' in secs:
            section_keys = ['200m', '400m', '600m']
            times = [secs.get(k, {}).get('section_time') for k in section_keys]
            valid_times = [t for t in times if t is not None]
            hdata['last_600m_time'] = sum(valid_times) if len(valid_times) == 3 else None
            speeds = [secs.get(k, {}).get('speed_ms') for k in section_keys]
            valid_speeds = [s for s in speeds if s is not None]
            hdata['last_600m_speed'] = sum(valid_speeds) / len(valid_speeds) if valid_speeds else None
        if '800m' in secs:
            section_keys = ['200m', '400m', '600m', '800m']
            times = [secs.get(k, {}).get('section_time') for k in section_keys]
            valid_times = [t for t in times if t is not None]
            hdata['last_800m_time'] = sum(valid_times) if len(valid_times) == 4 else None
            speeds = [secs.get(k, {}).get('speed_ms') for k in section_keys]
            valid_speeds = [s for s in speeds if s is not None]
            hdata['last_800m_speed'] = sum(valid_speeds) / len(valid_speeds) if valid_speeds else None

        if hdata.get('last_200m_speed') and distance > 0:
            all_speeds = [s.get('speed_ms') for s in secs.values() if s.get('speed_ms')]
            avg = sum(all_speeds) / len(all_speeds) if all_speeds else 0
            hdata['avg_speed'] = avg
            hdata['finishing_burst'] = (hdata['last_200m_speed'] / avg * 100) if avg > 0 else None

    return horse_data


def match_to_race_history(conn, venue, race_date, race_number, horse_name):
    cur = conn.cursor()
    cur.execute("""
        SELECT id, horse_name, track
        FROM race_results_history
        WHERE race_date = %s AND race_number = %s
    """, (race_date, race_number))

    rows = cur.fetchall()
    fallback_match = None
    best_match = None
    best_score = -1
    for row in rows:
        if not horse_name_match(row[1], horse_name):
            continue
        if fallback_match is None:
            fallback_match = row[0]
        score = track_match_score(venue, row[2])
        if score > best_score:
            best_match = row[0]
            best_score = score
    return best_match or fallback_match


def save_to_db(conn, records):
    if not records:
        return 0

    cur = conn.cursor()
    saved = 0

    for rec in records:
        rec_id = str(uuid.uuid4())
        try:
            cur.execute("SAVEPOINT sec_row")
            history_id = match_to_race_history(conn, rec['track'], rec['race_date'], rec['race_number'], rec['horse_name'])

            cur.execute("""
                INSERT INTO sectional_times (
                    id, race_results_history_id, track, race_date, race_number, race_name,
                    horse_name, saddle_number, distance_m, winning_time, track_config,
                    splits_json, last_200m_speed, last_200m_time, last_400m_speed, last_400m_time,
                    last_600m_speed, last_600m_time, last_800m_speed, last_800m_time,
                    finishing_burst, avg_speed, source, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, NOW()
                )
                ON CONFLICT (race_date, track, race_number, horse_name) DO NOTHING
            """, (
                rec_id,
                history_id,
                rec['track'],
                rec['race_date'],
                rec['race_number'],
                rec.get('race_name', ''),
                rec['horse_name'],
                rec.get('saddle_number', 0),
                rec.get('distance_m', 0),
                rec.get('winning_time'),
                rec.get('track_condition'),
                json.dumps(rec.get('splits_json', {})),
                rec.get('last_200m_speed'),
                rec.get('last_200m_time'),
                rec.get('last_400m_speed'),
                rec.get('last_400m_time'),
                rec.get('last_600m_speed'),
                rec.get('last_600m_time'),
                rec.get('last_800m_speed'),
                rec.get('last_800m_time'),
                rec.get('finishing_burst'),
                rec.get('avg_speed'),
                'nsw_pidata',
            ))
            if cur.rowcount > 0:
                saved += 1
            cur.execute("RELEASE SAVEPOINT sec_row")
        except Exception as e:
            print(f"  ERROR saving {rec['horse_name']}: {e}")
            cur.execute("ROLLBACK TO SAVEPOINT sec_row")
            continue

    conn.commit()
    return saved


def process_race(meta_entry, conn=None):
    filename = meta_entry['FileName']
    venue = meta_entry['Venue']
    race_number = meta_entry['RaceNumber']
    race_name = meta_entry['RaceName']

    date_ms = int(re.search(r'(\d+)', meta_entry['RaceDateAndTime']).group(1))
    race_date = datetime.utcfromtimestamp(date_ms / 1000).strftime('%Y-%m-%d')

    tol_text = download_tol_file(filename)
    if not tol_text:
        return 0

    parsed = parse_tol_data(tol_text)
    if not parsed['intermediates']:
        print(f"  No intermediate data in {filename}")
        return 0

    horse_sectionals = build_horse_sectionals(parsed)
    distance = parsed['race_info'].get('distance', 0)
    track_condition = parsed.get('track_condition')

    winner_time = None
    results = parsed.get('results', {})
    final_times = parsed.get('final_times', {})
    for hid, pos in results.items():
        if pos == 1 and hid in final_times:
            winner_time = final_times[hid]
            break

    records = []
    for hid, hdata in horse_sectionals.items():
        records.append({
            'track': normalize_venue(venue),
            'race_date': race_date,
            'race_number': race_number,
            'race_name': race_name,
            'horse_name': hdata['horse_name'],
            'saddle_number': hdata.get('saddle_number', 0),
            'distance_m': int(distance),
            'winning_time': winner_time,
            'track_condition': track_condition,
            'splits_json': hdata.get('splits_json', {}),
            'last_200m_speed': hdata.get('last_200m_speed'),
            'last_200m_time': hdata.get('last_200m_time'),
            'last_400m_speed': hdata.get('last_400m_speed'),
            'last_400m_time': hdata.get('last_400m_time'),
            'last_600m_speed': hdata.get('last_600m_speed'),
            'last_600m_time': hdata.get('last_600m_time'),
            'last_800m_speed': hdata.get('last_800m_speed'),
            'last_800m_time': hdata.get('last_800m_time'),
            'finishing_burst': hdata.get('finishing_burst'),
            'avg_speed': hdata.get('avg_speed'),
        })

    if conn:
        return save_to_db(conn, records)
    else:
        for rec in records:
            print(f"    {rec['horse_name']}: L200 {rec.get('last_200m_time', 'N/A')}s @ {rec.get('last_200m_speed', 'N/A')} m/s")
        return len(records)


def process_race_worker(args):
    race_meta, = args
    try:
        filename = race_meta['FileName']
        tol_text = download_tol_file(filename)
        if not tol_text:
            return None
        parsed = parse_tol_data(tol_text)
        if not parsed['intermediates']:
            return None

        horse_sectionals = build_horse_sectionals(parsed)
        distance = parsed['race_info'].get('distance', 0)
        track_condition = parsed.get('track_condition')
        venue = race_meta['Venue']
        race_number = race_meta['RaceNumber']
        race_name = race_meta['RaceName']
        date_ms = int(re.search(r'(\d+)', race_meta['RaceDateAndTime']).group(1))
        race_date = datetime.utcfromtimestamp(date_ms / 1000).strftime('%Y-%m-%d')

        winner_time = None
        results = parsed.get('results', {})
        final_times = parsed.get('final_times', {})
        for hid, pos in results.items():
            if pos == 1 and hid in final_times:
                winner_time = final_times[hid]
                break

        records = []
        for hid, hdata in horse_sectionals.items():
            records.append({
                'track': normalize_venue(venue),
                'race_date': race_date,
                'race_number': race_number,
                'race_name': race_name,
                'horse_name': hdata['horse_name'],
                'saddle_number': hdata.get('saddle_number', 0),
                'distance_m': int(distance),
                'winning_time': winner_time,
                'track_condition': track_condition,
                'splits_json': hdata.get('splits_json', {}),
                'last_200m_speed': hdata.get('last_200m_speed'),
                'last_200m_time': hdata.get('last_200m_time'),
                'last_400m_speed': hdata.get('last_400m_speed'),
                'last_400m_time': hdata.get('last_400m_time'),
                'last_600m_speed': hdata.get('last_600m_speed'),
                'last_600m_time': hdata.get('last_600m_time'),
                'last_800m_speed': hdata.get('last_800m_speed'),
                'last_800m_time': hdata.get('last_800m_time'),
                'finishing_burst': hdata.get('finishing_burst'),
                'avg_speed': hdata.get('avg_speed'),
            })
        return records
    except Exception as e:
        return None


def run_backfill(date_filter=None, venue_filter=None, limit=None, since_date=None):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    print("Fetching NSW pidata metadata...")
    meta = fetch_metadata()
    if not meta:
        return

    races = meta['RacesMetadata']
    print(f"Total races available: {len(races)}")

    if since_date:
        filtered = []
        for r in races:
            date_ms = int(re.search(r'(\d+)', r['RaceDateAndTime']).group(1))
            race_date = datetime.utcfromtimestamp(date_ms / 1000).strftime('%Y-%m-%d')
            if race_date >= since_date:
                filtered.append(r)
        races = filtered
        print(f"Filtered to {len(races)} races since {since_date}")

    if date_filter:
        filtered = []
        for r in races:
            log_time = r.get('LogFileUploadTimeString', '')
            filename = r.get('FileName', '')
            race_date = None
            try:
                date_ms = int(re.search(r'(\d+)', r['RaceDateAndTime']).group(1))
                race_date = datetime.utcfromtimestamp(date_ms / 1000).strftime('%Y-%m-%d')
            except Exception:
                race_date = None

            if (
                date_filter in log_time
                or date_filter in filename
                or race_date == date_filter
            ):
                filtered.append(r)

        races = filtered
        print(f"Filtered to {len(races)} races for date {date_filter}")

    if venue_filter:
        venue_lower = venue_filter.lower()
        races = [r for r in races if venue_lower in r['Venue'].lower()]
        print(f"Filtered to {len(races)} races for venue {venue_filter}")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT track || '|' || race_date || '|' || race_number FROM sectional_times WHERE source = 'nsw_pidata'")
    existing = set(r[0] for r in cur.fetchall())
    print(f"Already imported: {len(existing)} race-day combinations")

    to_process = []
    for r in races:
        venue = normalize_venue(r['Venue'])
        date_ms = int(re.search(r'(\d+)', r['RaceDateAndTime']).group(1))
        race_date = datetime.utcfromtimestamp(date_ms / 1000).strftime('%Y-%m-%d')
        key = f"{venue}|{race_date}|{r['RaceNumber']}"
        if key not in existing:
            to_process.append(r)

    print(f"Races to process: {len(to_process)} (skipping {len(races) - len(to_process)} already imported)")

    if limit:
        to_process = to_process[:limit]

    total_saved = 0
    batch_size = 10

    for batch_start in range(0, len(to_process), batch_size):
        batch = to_process[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (len(to_process) + batch_size - 1) // batch_size

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(process_race_worker, (r,)): r for r in batch}
            for future in as_completed(futures):
                race_meta = futures[future]
                try:
                    records = future.result()
                    if records:
                        saved = save_to_db(conn, records)
                        total_saved += saved
                        date_ms = int(re.search(r'(\d+)', race_meta['RaceDateAndTime']).group(1))
                        race_date = datetime.utcfromtimestamp(date_ms / 1000).strftime('%Y-%m-%d')
                        if saved > 0:
                            print(f"  {race_date} {race_meta['Venue']} R{race_meta['RaceNumber']}: {saved} records")
                except Exception as e:
                    print(f"  ERROR: {e}")

        print(f"Batch {batch_num}/{total_batches} done. Total saved: {total_saved}")

    conn.close()
    print(f"\nBackfill complete: {len(to_process)} races processed, {total_saved} records saved")
    return total_saved


def show_stats():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM sectional_times WHERE source = 'nsw_pidata'")
    total = cur.fetchone()[0]
    print(f"Total NSW pidata sectional records: {total}")

    cur.execute("SELECT track, COUNT(*) FROM sectional_times WHERE source = 'nsw_pidata' GROUP BY track ORDER BY COUNT(*) DESC")
    rows = cur.fetchall()
    print(f"\nBy track:")
    for row in rows:
        print(f"  {row[0]}: {row[1]}")

    cur.execute("SELECT race_date, COUNT(*) FROM sectional_times WHERE source = 'nsw_pidata' GROUP BY race_date ORDER BY race_date DESC LIMIT 10")
    rows = cur.fetchall()
    print(f"\nMost recent dates:")
    for row in rows:
        print(f"  {row[0]}: {row[1]} records")

    cur.execute("""
        SELECT COUNT(*) FROM sectional_times 
        WHERE source = 'nsw_pidata' AND race_results_history_id IS NOT NULL
    """)
    matched = cur.fetchone()[0]
    print(f"\nMatched to race_results_history: {matched}/{total} ({matched/total*100:.1f}%)" if total > 0 else "\nNo records yet")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description='NSW Sectional Times Collector (pidata API)')
    parser.add_argument('--backfill', action='store_true', help='Import all available races')
    parser.add_argument('--date', type=str, help='Filter by date (YYYY-MM-DD)')
    parser.add_argument('--days', type=int, help='Only import races from last N days')
    parser.add_argument('--venue', type=str, help='Filter by venue name')
    parser.add_argument('--limit', type=int, help='Limit number of races to process')
    parser.add_argument('--stats', action='store_true', help='Show collection statistics')
    parser.add_argument('--test', action='store_true', help='Test with one race (no DB save)')

    args = parser.parse_args()

    if args.stats:
        show_stats()
    elif args.test:
        meta = fetch_metadata()
        if meta:
            race = meta['RacesMetadata'][-1]
            print(f"Testing with: {race['Venue']} R{race['RaceNumber']} - {race['RaceName']}")
            process_race(race)
    elif args.days:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
        print(f"Importing NSW races from last {args.days} days (since {cutoff})")
        run_backfill(date_filter=None, venue_filter=args.venue, limit=args.limit, since_date=cutoff)
    elif args.backfill or args.date or args.venue:
        run_backfill(date_filter=args.date, venue_filter=args.venue, limit=args.limit)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
