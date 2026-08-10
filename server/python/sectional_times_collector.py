#!/usr/bin/env python3
"""
Sectional Times Collector — downloads sectional time CSV data from Racing Queensland and imports into the database, matching records to existing race_results_history entries.

URL Pattern: https://www.racingqueensland.com.au/RacingFile.ashx?path=/Sectional/YYYYMMDD_Track_Name_T.csv
"""

import os
import sys
import json
import argparse
import time
import re
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(__file__))

RQ_BASE_URL = "https://www.racingqueensland.com.au/RacingFile.ashx?path=/Sectional/"

QLD_TRACKS = {
    "Eagle Farm": ["Eagle_Farm"],
    "Doomben": ["Doomben"],
    "Gold Coast": ["Gold_Coast", "Aquis_Park_Gold_Coast", "Aquis_Park_Gold_Coast_Poly"],
    "Sunshine Coast": ["Sunshine_Coast"],
    "Ipswich": ["Ipswich"],
    "Toowoomba": ["Toowoomba"],
    "Rockhampton": ["Rockhampton"],
    "Townsville": ["Townsville"],
    "Cairns": ["Cairns"],
    "Mackay": ["Mackay"],
    "Gatton": ["Gatton"],
    "Dalby": ["Dalby"],
    "Kilcoy": ["Kilcoy"],
    "Beaudesert": ["Beaudesert"],
    "Cannon Park": ["Ladbrokes_Cannon_Park", "Cannon_Park", "LadbrokesCannonPark"],
}

TRACK_NAME_ALIASES = {
    "eagle farm": "Eagle Farm",
    "eaglefarm": "Eagle Farm",
    "doomben": "Doomben",
    "gold coast": "Gold Coast",
    "aquis park gold coast": "Gold Coast",
    "aquis park gold coast poly": "Gold Coast",
    "sunshine coast": "Sunshine Coast",
    "sunshinecoast": "Sunshine Coast",
    "ipswich": "Ipswich",
    "toowoomba": "Toowoomba",
    "rockhampton": "Rockhampton",
    "townsville": "Townsville",
    "cairns": "Cairns",
    "mackay": "Mackay",
    "gatton": "Gatton",
    "dalby": "Dalby",
    "kilcoy": "Kilcoy",
    "beaudesert": "Beaudesert",
    "cannon park": "Cannon Park",
    "ladbrokes cannon park": "Cannon Park",
    "ladbrokescannonpark": "Cannon Park",
}


def normalize_track_name(raw_name):
    if not raw_name:
        return raw_name
    lower = raw_name.strip().lower()
    return TRACK_NAME_ALIASES.get(lower, raw_name.strip())


def normalize_track_key(raw_name):
    normalized = normalize_track_name(raw_name) if raw_name else ""
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


def parse_time_to_seconds(time_str):
    if not time_str or time_str == "00:00:00.000":
        return 0.0
    try:
        parts = time_str.split(":")
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        elif len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            return minutes * 60 + seconds
    except (ValueError, IndexError):
        pass
    return 0.0


def parse_rq_csv(csv_text, track_name=None):
    lines = csv_text.strip().split('\n')
    races = []
    current_race = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        if line.startswith("Date,RaceNumber,"):
            header_meta = line.split(",", 2)[2] if len(line.split(",", 2)) > 2 else ""
            meta_parts = header_meta.split(";")
            
            race_name = meta_parts[2] if len(meta_parts) > 2 else ""
            winning_time = meta_parts[3] if len(meta_parts) > 3 else ""
            track_config = meta_parts[4] if len(meta_parts) > 4 else ""
            
            raw_track = ""
            if len(meta_parts) > 1:
                track_part = meta_parts[1]
                match = re.match(r'(.+?)(?:-Professional|-Provincial|-Country|-Metropolitan)', track_part)
                if match:
                    raw_track = match.group(1).strip()
                else:
                    raw_track = track_part.split("-")[0].strip() if "-" in track_part else track_part.strip()
            
            distance_match = re.search(r'(\d+)m', track_config)
            distance = int(distance_match.group(1)) if distance_match else None
            
            current_race = {
                "race_name": race_name.strip(),
                "winning_time": winning_time.strip(),
                "track_config": track_config.strip(),
                "raw_track": raw_track,
                "distance": distance,
                "runners": []
            }
            races.append(current_race)
            continue
        
        if current_race is None:
            continue
        
        parts = line.split(",", 2)
        if len(parts) < 3:
            continue
        
        date_str = parts[0].strip()
        try:
            race_num = int(parts[1].strip())
        except (ValueError, IndexError):
            continue
        
        current_race["date"] = date_str
        current_race["race_number"] = race_num
        
        runner_data = parts[2]
        runner_parts = runner_data.split(";")
        
        if len(runner_parts) < 2:
            continue
        
        horse_name = runner_parts[0].strip()
        try:
            saddle_number = int(runner_parts[1].strip())
        except (ValueError, IndexError):
            saddle_number = 0
        
        splits = []
        i = 2
        while i + 2 < len(runner_parts):
            try:
                mark = int(runner_parts[i].strip())
                speed = float(runner_parts[i + 1].strip())
                time_val = parse_time_to_seconds(runner_parts[i + 2].strip())
                if speed > 0:
                    splits.append({
                        "mark": mark,
                        "speed_ms": speed,
                        "split_time": round(time_val, 3)
                    })
            except (ValueError, IndexError):
                pass
            i += 3
        
        if not splits:
            continue
        
        splits.sort(key=lambda x: x["mark"])
        
        last_200m_speed = None
        last_200m_time = None
        last_400m_speed = None
        last_400m_time = None
        last_600m_speed = None
        last_600m_time = None
        last_800m_speed = None
        last_800m_time = None
        
        distance = current_race.get("distance", 0) or 0
        
        for split in splits:
            remaining = distance - split["mark"] if distance > 0 else None
            if remaining is not None:
                if remaining == 0:
                    pass
            
            if split["mark"] == distance:
                last_200m_speed = split["speed_ms"]
                last_200m_time = split["split_time"]
            elif split["mark"] == distance - 200:
                last_400m_speed = split["speed_ms"]
                last_400m_time = split["split_time"]
            elif split["mark"] == distance - 400:
                last_600m_speed = split["speed_ms"]
                last_600m_time = split["split_time"]
            elif split["mark"] == distance - 600:
                last_800m_speed = split["speed_ms"]
                last_800m_time = split["split_time"]
        
        if not last_200m_speed and splits:
            last_split = splits[-1]
            last_200m_speed = last_split["speed_ms"]
            last_200m_time = last_split["split_time"]
            
            if len(splits) >= 2:
                second_last = splits[-2]
                last_400m_speed = second_last["speed_ms"]
                last_400m_time = second_last["split_time"]
            
            if len(splits) >= 3:
                third_last = splits[-3]
                last_600m_speed = third_last["speed_ms"]
                last_600m_time = third_last["split_time"]
            
            if len(splits) >= 4:
                fourth_last = splits[-4]
                last_800m_speed = fourth_last["speed_ms"]
                last_800m_time = fourth_last["split_time"]
        
        finishing_burst = None
        if last_200m_speed and len(splits) >= 2:
            avg_early = sum(s["speed_ms"] for s in splits[:-1]) / len(splits[:-1])
            if avg_early > 0:
                finishing_burst = (last_200m_speed - avg_early) / avg_early * 100
        
        avg_speed = None
        if splits:
            avg_speed = sum(s["speed_ms"] for s in splits) / len(splits)
        
        resolved_track = track_name or normalize_track_name(current_race.get("raw_track", ""))
        
        if date_str:
            try:
                if "/" in date_str:
                    dt = datetime.strptime(date_str, "%d/%m/%Y")
                    iso_date = dt.strftime("%Y-%m-%d")
                else:
                    iso_date = date_str
            except ValueError:
                iso_date = date_str
        else:
            iso_date = ""
        
        runner_record = {
            "track": resolved_track,
            "race_date": iso_date,
            "race_number": race_num,
            "race_name": current_race["race_name"],
            "horse_name": horse_name,
            "saddle_number": saddle_number,
            "distance_m": current_race.get("distance"),
            "winning_time": current_race["winning_time"],
            "track_config": current_race["track_config"],
            "splits_json": splits,
            "last_200m_speed": last_200m_speed,
            "last_200m_time": last_200m_time,
            "last_400m_speed": last_400m_speed,
            "last_400m_time": last_400m_time,
            "last_600m_speed": last_600m_speed,
            "last_600m_time": last_600m_time,
            "last_800m_speed": last_800m_speed,
            "last_800m_time": last_800m_time,
            "finishing_burst": round(finishing_burst, 3) if finishing_burst is not None else None,
            "avg_speed": round(avg_speed, 3) if avg_speed is not None else None,
        }
        current_race["runners"].append(runner_record)
    
    all_runners = []
    for race in races:
        all_runners.extend(race.get("runners", []))
    
    return all_runners


def download_csv(date_str, track_variants):
    """
    Returns (content, variant, diagnosis). On success diagnosis is None.
    On failure content/variant are None and diagnosis names WHY — the old
    code swallowed every exception and returned a bare None, so a site-wide
    Cloudflare block (see the 2026-07 net-probe finding, docs/12 §4c) looked
    identical to a genuinely absent file. The caller logs the diagnosis.
    """
    date_formatted = date_str.replace("-", "")
    reasons = []

    for variant in track_variants:
        url = f"{RQ_BASE_URL}{date_formatted}_{variant}_T.csv"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; RacingAnalytics/1.0)"
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read().decode('utf-8', errors='replace')
                if resp.status == 200 and content.strip() and "RaceNumber" in content:
                    return content, variant, None
                if "Just a moment" in content or "cf-" in content:
                    reasons.append(f"{variant}: anti-bot challenge page")
                else:
                    reasons.append(f"{variant}: {resp.status} but not a sectional CSV")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read(400).decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code == 403 or "Just a moment" in body:
                reasons.append(f"{variant}: HTTP 403 anti-bot challenge (Cloudflare)")
            else:
                reasons.append(f"{variant}: HTTP {e.code}")
        except Exception as e:
            reasons.append(f"{variant}: {type(e).__name__}")

    return None, None, "; ".join(reasons) if reasons else "no variants tried"


def match_to_race_results(records, db_url):
    import psycopg2
    
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    matched = 0
    unmatched = 0
    
    dates_tracks = set()
    for r in records:
        dates_tracks.add((r["race_date"], r["track"]))
    
    results_cache = {}
    for race_date, track in dates_tracks:
        cur.execute("""
            SELECT id, horse_name, race_number, race_date, track
            FROM race_results_history
            WHERE race_date = %s AND (track = %s OR track ILIKE %s)
        """, (race_date, track, f"%{track}%"))

        rows = cur.fetchall()
        for row in rows:
            key = (row[3], row[2])
            if key not in results_cache:
                results_cache[key] = []
            results_cache[key].append({"id": row[0], "horse_name": row[1], "track": row[4]})

    for record in records:
        key = (record["race_date"], record["race_number"])
        candidates = results_cache.get(key, [])

        best_match = None
        best_score = -1
        for candidate in candidates:
            score = track_match_score(record["track"], candidate["track"])
            if score <= 0:
                continue
            if horse_name_match(record["horse_name"], candidate["horse_name"]) and score > best_score:
                best_match = candidate["id"]
                best_score = score

        if best_match:
            record["race_results_history_id"] = best_match
            matched += 1
        else:
            record["race_results_history_id"] = None
            unmatched += 1
    
    cur.close()
    conn.close()
    
    return matched, unmatched


def import_to_database(records, db_url, batch_size=50):
    import psycopg2
    
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    imported = 0
    skipped = 0
    
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        
        for record in batch:
            cur.execute("""
                SELECT id FROM sectional_times
                WHERE race_date = %s AND track = %s AND race_number = %s AND horse_name = %s
                LIMIT 1
            """, (record["race_date"], record["track"], record["race_number"], record["horse_name"]))
            
            if cur.fetchone():
                skipped += 1
                continue
            
            cur.execute("""
                INSERT INTO sectional_times (
                    race_results_history_id, track, race_date, race_number, race_name,
                    horse_name, saddle_number, distance_m, winning_time, track_config,
                    splits_json, last_200m_speed, last_200m_time,
                    last_400m_speed, last_400m_time,
                    last_600m_speed, last_600m_time,
                    last_800m_speed, last_800m_time,
                    finishing_burst, avg_speed, source
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, 'racing_qld'
                )
                ON CONFLICT (race_date, track, race_number, horse_name) DO NOTHING
            """, (
                record.get("race_results_history_id"),
                record["track"],
                record["race_date"],
                record["race_number"],
                record.get("race_name"),
                record["horse_name"],
                record.get("saddle_number"),
                record.get("distance_m"),
                record.get("winning_time"),
                record.get("track_config"),
                json.dumps(record.get("splits_json", [])),
                record.get("last_200m_speed"),
                record.get("last_200m_time"),
                record.get("last_400m_speed"),
                record.get("last_400m_time"),
                record.get("last_600m_speed"),
                record.get("last_600m_time"),
                record.get("last_800m_speed"),
                record.get("last_800m_time"),
                record.get("finishing_burst"),
                record.get("avg_speed"),
            ))
            imported += 1
        
        conn.commit()
    
    cur.close()
    conn.close()
    
    return imported, skipped


def compute_phase2_primitives(db_url, track, race_date):
    """Phase 2 (Plan: Biomechanical & Statistical Sectional Features). Delegates to backfill_lambda_svi to compute lambda, SVI, RSI, trip_cost for imported rows."""
    import psycopg2
    from backfill_lambda_targeted import (
        normalise_splits, compute_lambda, compute_svi, compute_rsi, compute_trip_cost,
    )

    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        cur.execute("""
            SELECT id, splits_json, distance_m, avg_speed, saddle_number
            FROM sectional_times
            WHERE track = %s AND race_date = %s
              AND splits_json IS NOT NULL
        """, (track, race_date))
        rows = cur.fetchall()

        for rid, splits_json, dist_m, avg_spd, saddle in rows:
            norm = normalise_splits(splits_json)
            if not norm:
                continue

            lam = compute_lambda(norm)
            svi = compute_svi(norm)
            rsi = compute_rsi(norm, dist_m)
            tc = compute_trip_cost(saddle, dist_m, avg_spd)

            if lam is None and svi is None and rsi is None and tc == 0.0:
                continue

            cur.execute("""
                UPDATE sectional_times
                SET lambda_decay = %s, svi = %s, rsi = %s, trip_cost_seconds = %s
                WHERE id = %s
            """, (lam, svi, rsi, tc, rid))

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"  [Phase2 primitives] Error for {track} {race_date}: {e}", file=sys.stderr)


def compute_and_write_daily_variant(db_url, track, race_date, going):
    """
    Track Sectional Variant (Beyer Speed Figure methodology).

    Computes how fast or slow the track is running relative to the 90-day par for the same track/going/distance band. Positive variant = track slower than par.
    """
    import psycopg2

    DISTANCE_BANDS = [1200, 1400, 1600, 1800, 2000]

    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        for band in DISTANCE_BANDS:
            lo, hi = band - 150, band + 150

            # Historical par: median over last 90 days, same track/going/distance band
            cur.execute("""
                SELECT
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY last_200m_speed),
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY last_400m_speed),
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY last_600m_speed),
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY last_800m_speed)
                FROM sectional_times
                WHERE track = %s
                  AND distance_m BETWEEN %s AND %s
                  AND track_config ILIKE %s
                  AND race_date < %s
                  AND race_date::date > %s::date - INTERVAL '90 days'
                  AND last_200m_speed IS NOT NULL
            """, (track, lo, hi, f'%{going}%', race_date, race_date))
            par = cur.fetchone()
            if not par or par[0] is None:
                continue

            # Today's actual average speeds for this band
            cur.execute("""
                SELECT
                    AVG(last_200m_speed), AVG(last_400m_speed),
                    AVG(last_600m_speed), AVG(last_800m_speed)
                FROM sectional_times
                WHERE track = %s AND race_date = %s
                  AND distance_m BETWEEN %s AND %s
                  AND last_200m_speed IS NOT NULL
            """, (track, race_date, lo, hi))
            actual = cur.fetchone()
            if not actual or actual[0] is None:
                continue

            # Compute variants (positive = track slower than par)
            v200 = round(par[0] - actual[0], 4) if par[0] and actual[0] else None
            v400 = round(par[1] - actual[1], 4) if par[1] and actual[1] else None
            v600 = round(par[2] - actual[2], 4) if par[2] and actual[2] else None
            v800 = round(par[3] - actual[3], 4) if par[3] and actual[3] else None

            if v200 is None:
                continue

            cur.execute("""
                UPDATE sectional_times
                SET variant_adjusted_200m = last_200m_speed + COALESCE(%s, 0),
                    variant_adjusted_400m = last_400m_speed + COALESCE(%s, 0),
                    variant_adjusted_600m = last_600m_speed + COALESCE(%s, 0),
                    variant_adjusted_800m = last_800m_speed + COALESCE(%s, 0)
                WHERE track = %s AND race_date = %s
                  AND distance_m BETWEEN %s AND %s
            """, (v200, v400, v600, v800, track, race_date, lo, hi))

        conn.commit()
        cur.close()
        conn.close()

    except Exception as e:
        print(f"  [Variant] Error for {track} {race_date}: {e}", file=sys.stderr)


def collect_for_date_track(date_str, track_name, db_url, verbose=True):
    track_variants = QLD_TRACKS.get(track_name)
    if not track_variants:
        if verbose:
            print(f"  Unknown QLD track: {track_name}")
        return 0, 0, 0, 0

    csv_text, matched_variant, diagnosis = download_csv(date_str, track_variants)
    if not csv_text:
        # Only an all-404 diagnosis stays quiet on the non-verbose path: 404
        # on every variant means no meeting/CSV that day, the one benign
        # case. Everything else — anti-bot (DM-H5), other HTTP errors,
        # timeouts, schema drift — prints, because a scheduled run whose
        # every failure is silent cannot be told apart from a quiet day
        # in its log (#125).
        all_404 = diagnosis and all(
            d.strip().endswith("HTTP 404") for d in diagnosis.split(";"))
        if verbose or not all_404:
            print(f"  No CSV for {track_name} on {date_str} — {diagnosis}")
        return 0, 0, 0, 0

    records = parse_rq_csv(csv_text, track_name)
    if not records:
        if verbose:
            print(f"  No records parsed from CSV for {track_name} on {date_str}")
        return 0, 0, 0, 0

    matched, unmatched = match_to_race_results(records, db_url)

    imported, skipped = import_to_database(records, db_url)

    # Phase 2: Compute biomechanical primitives (λ, SVI, RSI, trip_cost) for imported rows
    if imported > 0:
        compute_phase2_primitives(db_url, track_name, date_str)

        # Infer going from first record for variant computation
        going = records[0].get('track_config', 'Good') if records else 'Good'
        compute_and_write_daily_variant(db_url, track_name, date_str, going)

    if verbose:
        print(f"  {track_name} {date_str}: {len(records)} runners, {matched} matched, {imported} imported, {skipped} skipped")

    return len(records), matched, imported, skipped


def backfill_from_database(db_url, limit=None):
    import psycopg2
    
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    qld_track_names = list(QLD_TRACKS.keys())
    placeholders = ",".join(["%s"] * len(qld_track_names))
    
    cur.execute(f"""
        SELECT DISTINCT race_date, track 
        FROM race_results_history 
        WHERE track IN ({placeholders})
        AND race_date IS NOT NULL
        ORDER BY race_date DESC
    """, qld_track_names)
    
    date_tracks = cur.fetchall()
    cur.close()
    conn.close()
    
    already_have = set()
    conn2 = psycopg2.connect(db_url)
    cur2 = conn2.cursor()
    cur2.execute("SELECT DISTINCT race_date, track FROM sectional_times")
    for row in cur2.fetchall():
        already_have.add((row[0], row[1]))
    cur2.close()
    conn2.close()
    
    to_fetch = []
    for race_date, track in date_tracks:
        normalized = normalize_track_name(track)
        if (race_date, normalized) not in already_have and normalized in QLD_TRACKS:
            to_fetch.append((race_date, normalized))
    
    to_fetch = list(set(to_fetch))
    to_fetch.sort(key=lambda x: x[0], reverse=True)
    
    if limit:
        to_fetch = to_fetch[:limit]
    
    print(f"\n  Found {len(to_fetch)} QLD date/track combinations to fetch sectionals for")
    print(f"  (Already have {len(already_have)} date/track combinations)\n")
    
    total_runners = 0
    total_matched = 0
    total_imported = 0
    total_skipped = 0
    fetched = 0
    failed = 0
    
    for i, (race_date, track) in enumerate(to_fetch):
        runners, matched, imported, skipped = collect_for_date_track(race_date, track, db_url)
        total_runners += runners
        total_matched += matched
        total_imported += imported
        total_skipped += skipped
        
        if runners > 0:
            fetched += 1
        else:
            failed += 1
        
        if (i + 1) % 10 == 0:
            print(f"  Progress: {i+1}/{len(to_fetch)} ({fetched} fetched, {failed} no data)")
        
        time.sleep(1)
    
    print(f"\n  Backfill complete:")
    print(f"    Date/tracks processed: {len(to_fetch)}")
    print(f"    Successful: {fetched}, No data: {failed}")
    print(f"    Total runners: {total_runners}")
    print(f"    Matched to history: {total_matched}")
    print(f"    Imported: {total_imported}, Skipped: {total_skipped}")
    
    return total_imported


def main():
    parser = argparse.ArgumentParser(description="Collect sectional times from Racing Queensland")
    parser.add_argument("--date", type=str, help="Date to fetch (YYYY-MM-DD)")
    parser.add_argument("--track", type=str, help="Track name (e.g., 'Eagle Farm')")
    parser.add_argument("--backfill", action="store_true", help="Backfill last N days")
    parser.add_argument("--days", type=int, default=30, help="Number of days to backfill")
    parser.add_argument("--backfill-db", action="store_true", help="Backfill all QLD dates from race_results_history")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of date/tracks to process")
    args = parser.parse_args()
    
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("Error: DATABASE_URL not set")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("  SECTIONAL TIMES COLLECTOR")
    print("=" * 60)
    
    if args.backfill_db:
        print("\n  Mode: Backfill from database (QLD tracks)")
        backfill_from_database(db_url, args.limit)
    elif args.backfill:
        print(f"\n  Mode: Backfill last {args.days} days")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=args.days)
        
        total_imported = 0
        for day_offset in range(args.days):
            current_date = start_date + timedelta(days=day_offset)
            date_str = current_date.strftime("%Y-%m-%d")
            
            for track_name in QLD_TRACKS:
                runners, matched, imported, skipped = collect_for_date_track(date_str, track_name, db_url, verbose=False)
                if imported > 0:
                    print(f"  {date_str} {track_name}: {imported} imported ({matched} matched)")
                    total_imported += imported
                elif runners > 0:
                    # a CSV was fetched and parsed but nothing landed — say
                    # so, or a run overlapping the daily collector logs
                    # nothing at all and its zero looks like a dead source
                    print(f"  {date_str} {track_name}: {runners} runners fetched, "
                          f"0 imported ({skipped} already in sectional_times)")
                time.sleep(0.5)
        
        print(f"\n  Total imported: {total_imported}")
    elif args.date and args.track:
        print(f"\n  Mode: Single date/track ({args.date}, {args.track})")
        collect_for_date_track(args.date, args.track, db_url)
    elif args.date:
        print(f"\n  Mode: All QLD tracks for {args.date}")
        for track_name in QLD_TRACKS:
            collect_for_date_track(args.date, track_name, db_url)
            time.sleep(0.5)
    else:
        print("\n  Usage:")
        print("    --date YYYY-MM-DD [--track 'Eagle Farm']  # Fetch specific date/track")
        print("    --backfill --days 30                       # Backfill last 30 days")
        print("    --backfill-db [--limit 50]                 # Backfill all QLD dates from DB")
    
    print()


if __name__ == "__main__":
    main()
