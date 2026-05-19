#!/usr/bin/env python3
"""Import Track JSON Files to training_data table. Converts Racing API JSON format into training_data records. Filters out trials and incomplete entries."""

import os
import sys
import json
import glob
import argparse
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))


def parse_position(pos):
    if not pos:
        return None
    if isinstance(pos, int):
        return pos
    if isinstance(pos, str):
        pos = pos.strip().upper()
        if pos in ('DNF', 'PU', 'UR', 'F', 'BD', 'REF', 'DSQ', 'WD', 'NR', 'LR', 'FELL', ''):
            return None
        try:
            return int(pos)
        except ValueError:
            return None
    return None


def parse_float(val, default=0.0):
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val = val.replace('kg', '').replace('m', '').replace('$', '').replace(',', '').strip()
        try:
            return float(val)
        except ValueError:
            return default
    return default


def extract_strike_rate(stats_dict):
    if not stats_dict or not isinstance(stats_dict, dict):
        return 0.0
    total = int(stats_dict.get('total', 0) or 0)
    if total == 0:
        return 0.0
    wins = int(stats_dict.get('first', 0) or 0)
    return (wins / total) * 100


def calculate_form_score(form_string):
    if not form_string:
        return 0.0
    form = str(form_string).replace('-', '').replace(' ', '')
    score = 0.0
    weights = [1.0, 0.8, 0.6, 0.4, 0.2]
    for i, char in enumerate(form[:5]):
        weight = weights[i] if i < len(weights) else 0.1
        if char == '1':
            score += 10 * weight
        elif char == '2':
            score += 7 * weight
        elif char == '3':
            score += 5 * weight
        elif char == '4':
            score += 3 * weight
        elif char == '5':
            score += 2 * weight
        elif char.isdigit():
            score += 1 * weight
    return score


def get_best_odds(runner):
    sp = runner.get('sp')
    if sp:
        val = parse_float(sp, 0)
        if val > 0:
            return val
    odds_list = runner.get('odds', [])
    if odds_list and isinstance(odds_list, list):
        best = 0
        for o in odds_list:
            win = parse_float(o.get('win_odds', 0), 0)
            if win > best:
                best = win
        if best > 0:
            return best
    return 0.0


def process_file(filepath):
    with open(filepath, 'r') as f:
        data = json.load(f)

    if not isinstance(data, list):
        print(f"  Unexpected format in {filepath}")
        return []

    records = []
    races_processed = 0
    trials_skipped = 0
    scratched_skipped = 0
    no_result_skipped = 0

    for meet in data:
        meet_date = meet.get('date', '')
        meet_course = meet.get('course', '')
        races = meet.get('races', [])

        for race in races:
            if race.get('is_trial') or race.get('is_jump_out'):
                trials_skipped += 1
                continue

            race_class_str = str(race.get('class') or '').upper()
            race_name_str = str(race.get('race_name') or '').upper()
            if 'TRIAL' in race_class_str or 'BT' in race_class_str or 'JUMP OUT' in race_name_str or 'JUMPOUT' in race_name_str:
                trials_skipped += 1
                continue

            if race.get('race_status', '').lower() != 'results':
                continue

            course = race.get('course') or meet_course
            date = race.get('date') or meet_date
            distance = race.get('distance', '')
            going = race.get('going', '')
            race_class = race.get('class', '')
            race_number = race.get('race_number', '0')
            race_id = f"{course}_{date}_R{race_number}"

            runners = race.get('runners', [])
            field_size = len([r for r in runners if not r.get('scratched')])

            for runner in runners:
                if runner.get('scratched'):
                    scratched_skipped += 1
                    continue

                actual_position = parse_position(runner.get('position'))
                if actual_position is None:
                    no_result_skipped += 1
                    continue

                starting_price = get_best_odds(runner)
                if starting_price <= 0:
                    no_result_skipped += 1
                    continue

                won = 1 if actual_position == 1 else 0
                placed = 1 if actual_position <= 3 else 0

                market_implied_prob = (100 / starting_price) if starting_price > 1 else 0
                barrier = int(parse_float(runner.get('draw') or runner.get('barrier', 0), 0))
                weight = parse_float(runner.get('weight', '55'), 55)
                horse_name = runner.get('horse', '')
                horse_number = str(runner.get('number', ''))
                jockey = runner.get('jockey', '')
                trainer = runner.get('trainer', '')

                stats = runner.get('stats', {}) or {}
                distance_strike = extract_strike_rate(stats.get('distance_stats'))
                course_strike = extract_strike_rate(stats.get('course_stats'))
                course_distance_strike = extract_strike_rate(stats.get('course_distance_stats'))
                jockey_strike = extract_strike_rate(stats.get('jockey_stats'))

                ground_strike = 0
                going_lower = going.lower() if going else ''
                if 'heavy' in going_lower:
                    ground_strike = extract_strike_rate(stats.get('ground_heavy_stats'))
                elif 'soft' in going_lower:
                    ground_strike = extract_strike_rate(stats.get('ground_soft_stats'))
                elif 'firm' in going_lower:
                    ground_strike = extract_strike_rate(stats.get('ground_firm_stats'))
                else:
                    ground_strike = extract_strike_rate(stats.get('ground_good_stats'))

                form_score = calculate_form_score(runner.get('form'))

                distance_val = parse_float(str(distance).replace('m', ''), 1400)
                is_sprint = distance_val < 1200
                barrier_penalty = (barrier - 10) * 0.05 if is_sprint and barrier > 10 else 0
                weight_penalty = max(0, (weight - 55) * 0.02)

                base_prob = market_implied_prob if market_implied_prob > 0 else (100 / max(field_size, 1))
                feature_adjustment = 1.0
                if distance_strike > 25:
                    feature_adjustment += 0.1
                if course_strike > 25:
                    feature_adjustment += 0.08
                if ground_strike > 30:
                    feature_adjustment += 0.12
                if form_score > 20:
                    feature_adjustment += 0.1
                feature_adjustment -= barrier_penalty
                feature_adjustment -= weight_penalty

                predicted_win_prob = min(60, base_prob * feature_adjustment)
                predicted_place_prob = min(90, predicted_win_prob * 2.5)

                if starting_price > 1:
                    expected_value = (predicted_win_prob / 100) * starting_price - 1
                else:
                    expected_value = 0

                confidence = 'low'
                if predicted_win_prob > 25 and starting_price <= 8:
                    confidence = 'high'
                elif predicted_win_prob > 18 and starting_price <= 12:
                    confidence = 'medium'

                record = {
                    'race_id': race_id,
                    'track': course,
                    'race_number': int(race_number),
                    'race_date': date,
                    'distance': str(distance),
                    'going': going,
                    'race_class': race_class,
                    'horse_name': horse_name,
                    'horse_number': horse_number,
                    'barrier': barrier,
                    'jockey': jockey,
                    'trainer': trainer,
                    'weight': weight,
                    'predicted_win_prob': round(predicted_win_prob, 2),
                    'predicted_place_prob': round(predicted_place_prob, 2),
                    'predicted_position': round(field_size / 2, 1),
                    'confidence': confidence,
                    'expected_value': round(expected_value, 4),
                    'market_odds': starting_price,
                    'actual_position': actual_position,
                    'won': won,
                    'placed': placed,
                    'starting_price': starting_price,
                }
                records.append(record)

            races_processed += 1

    print(f"    Races processed: {races_processed}")
    print(f"    Trials/jump-outs skipped: {trials_skipped}")
    print(f"    Scratched runners skipped: {scratched_skipped}")
    print(f"    No result/no SP skipped: {no_result_skipped}")
    print(f"    Valid records: {len(records)}")

    return records


def import_to_database(records, db_url, batch_size=200):
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    total_imported = 0
    total_skipped = 0

    with engine.connect() as conn:
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]

            for record in batch:
                check_query = text("""
                    SELECT id FROM training_data 
                    WHERE track = :track 
                    AND race_number = :race_number 
                    AND race_date = :race_date 
                    AND horse_name = :horse_name
                    LIMIT 1
                """)
                existing = conn.execute(check_query, {
                    'track': record['track'],
                    'race_number': record['race_number'],
                    'race_date': record['race_date'],
                    'horse_name': record['horse_name'],
                }).fetchone()

                if existing:
                    total_skipped += 1
                    continue

                record_id = str(uuid.uuid4())

                insert_query = text("""
                    INSERT INTO training_data (
                        id, race_id, track, race_number, race_date, distance, going, race_class,
                        horse_name, horse_number, barrier, jockey, trainer, weight,
                        predicted_win_prob, predicted_place_prob, predicted_position,
                        confidence, expected_value, market_odds,
                        actual_position, won, placed, starting_price
                    ) VALUES (
                        :id, :race_id, :track, :race_number, :race_date, :distance, :going, :race_class,
                        :horse_name, :horse_number, :barrier, :jockey, :trainer, :weight,
                        :predicted_win_prob, :predicted_place_prob, :predicted_position,
                        :confidence, :expected_value, :market_odds,
                        :actual_position, :won, :placed, :starting_price
                    )
                """)

                try:
                    conn.execute(insert_query, {
                        'id': record_id,
                        'race_id': record.get('race_id', ''),
                        'track': record['track'],
                        'race_number': record['race_number'],
                        'race_date': record['race_date'],
                        'distance': record.get('distance', ''),
                        'going': record.get('going', ''),
                        'race_class': record.get('race_class', ''),
                        'horse_name': record['horse_name'],
                        'horse_number': record.get('horse_number', ''),
                        'barrier': record.get('barrier'),
                        'jockey': record.get('jockey', ''),
                        'trainer': record.get('trainer', ''),
                        'weight': record.get('weight'),
                        'predicted_win_prob': record.get('predicted_win_prob'),
                        'predicted_place_prob': record.get('predicted_place_prob'),
                        'predicted_position': record.get('predicted_position'),
                        'confidence': record.get('confidence', 'low'),
                        'expected_value': record.get('expected_value'),
                        'market_odds': record.get('market_odds'),
                        'actual_position': record.get('actual_position'),
                        'won': record.get('won', 0),
                        'placed': record.get('placed', 0),
                        'starting_price': record.get('starting_price'),
                    })
                    total_imported += 1
                except Exception as e:
                    print(f"    Error inserting {record['horse_name']}: {e}")
                    total_skipped += 1

            conn.commit()
            print(f"\r    Progress: {total_imported} imported, {total_skipped} skipped", end="", flush=True)

    return total_imported, total_skipped


def main():
    parser = argparse.ArgumentParser(description="Import track JSON files to training_data")
    parser.add_argument("--dir", type=str, default="historical_data/track_imports",
                        help="Directory containing JSON files")
    parser.add_argument("--file", type=str, default=None,
                        help="Single JSON file to import")
    args = parser.parse_args()

    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("Error: DATABASE_URL not set")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("  IMPORTING TRACK JSON FILES TO TRAINING DATABASE")
    print("=" * 70)

    if args.file:
        files = [args.file]
    else:
        files = sorted(glob.glob(os.path.join(args.dir, '*.json')))

    if not files:
        print(f"\n  No JSON files found in {args.dir}")
        sys.exit(1)

    print(f"\n  Found {len(files)} file(s) to process")

    all_records = []

    for filepath in files:
        filename = os.path.basename(filepath)
        print(f"\n  Processing: {filename}")
        records = process_file(filepath)
        all_records.extend(records)

    winners = sum(1 for r in all_records if r.get('won') == 1)
    placed = sum(1 for r in all_records if r.get('placed') == 1)
    unique_tracks = len(set(r['track'] for r in all_records))
    unique_dates = len(set(r['race_date'] for r in all_records))

    print(f"\n\n  SUMMARY BEFORE IMPORT:")
    print(f"  Total records: {len(all_records)}")
    print(f"  Winners: {winners}")
    print(f"  Placed (top 3): {placed}")
    print(f"  Unique tracks: {unique_tracks}")
    print(f"  Unique dates: {unique_dates}")

    print(f"\n  Importing to database...")
    imported, skipped = import_to_database(all_records, db_url)

    print(f"\n\n" + "=" * 70)
    print("  IMPORT COMPLETE")
    print("=" * 70)
    print(f"  Records imported: {imported}")
    print(f"  Records skipped (duplicates): {skipped}")
    print(f"  Total processed: {imported + skipped}")
    print()


if __name__ == '__main__':
    main()
