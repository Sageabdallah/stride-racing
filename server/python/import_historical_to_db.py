#!/usr/bin/env python3
"""
Processes historical JSON data and populates the training_data table
with comprehensive ML features for model training.
"""

import os
import sys
import json
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))


def parse_position(pos):
    """Parse finishing position from various formats."""
    if not pos:
        return None
    if isinstance(pos, int):
        return pos
    if isinstance(pos, str):
        pos = pos.strip().upper()
        if pos in ['DNF', 'PU', 'UR', 'F', 'BD', 'REF', 'DSQ', 'WD', 'NR']:
            return None
        try:
            return int(pos)
        except ValueError:
            return None
    return None


def parse_float(val, default=0.0):
    """Parse float from various formats."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            val = val.replace('kg', '').replace('m', '').replace('$', '').replace(',', '').strip()
            return float(val)
        except ValueError:
            return default
    return default


def extract_strike_rate(stats_dict, key='first'):
    """Extract strike rate from stats dictionary."""
    if not stats_dict or not isinstance(stats_dict, dict):
        return 0.0
    total = stats_dict.get('total', 0)
    if not total or total == 0:
        return 0.0
    wins = stats_dict.get(key, 0)
    return (wins / total) * 100 if total > 0 else 0.0


def calculate_weighted_form_score(form_string):
    """Calculate weighted form score from form string (recent runs weighted higher)."""
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


def process_race_for_training(race_data, meet_course, meet_date):
    """Process a single race and return training data records."""
    records = []
    
    race_number = race_data.get('race_number', 0)
    distance = race_data.get('distance', '') or race_data.get('distance_metres', '')
    going = race_data.get('going', '') or race_data.get('going_description', '')
    race_class = race_data.get('class', '') or race_data.get('class_level', '')
    race_id = race_data.get('race_id', '')
    
    runners = race_data.get('runners', [])
    field_size = len(runners)
    
    for runner in runners:
        if runner.get('scratched'):
            continue
        
        actual_position = parse_position(runner.get('position') or runner.get('finishing_position'))
        if actual_position is None:
            continue
        
        won = 1 if actual_position == 1 else 0
        placed = 1 if actual_position <= 3 else 0
        
        starting_price = parse_float(runner.get('sp') or runner.get('starting_price'), 0)
        
        market_implied_prob = (100 / starting_price) if starting_price > 1 else 0
        
        distance_strike = extract_strike_rate(runner.get('distance_stats'))
        course_strike = extract_strike_rate(runner.get('course_stats'))
        course_distance_strike = extract_strike_rate(runner.get('course_distance_stats'))
        jockey_strike = extract_strike_rate(runner.get('jockey_stats'))
        trainer_strike = extract_strike_rate(runner.get('trainer_stats'))
        jockey_trainer_strike = extract_strike_rate(runner.get('jockey_trainer_stats'))
        
        ground_strike = 0
        going_lower = going.lower() if going else ''
        if 'heavy' in going_lower:
            ground_strike = extract_strike_rate(runner.get('ground_heavy_stats'))
        elif 'soft' in going_lower:
            ground_strike = extract_strike_rate(runner.get('ground_soft_stats'))
        elif 'firm' in going_lower:
            ground_strike = extract_strike_rate(runner.get('ground_firm_stats'))
        else:
            ground_strike = extract_strike_rate(runner.get('ground_good_stats'))
        
        form_score = calculate_weighted_form_score(runner.get('form'))
        
        barrier = int(parse_float(runner.get('draw') or runner.get('barrier', 0) or 0, 0))
        distance_val = parse_float(str(distance).replace('m', ''), 1400)
        is_sprint = distance_val < 1200
        barrier_penalty = 0
        if is_sprint and barrier > 10:
            barrier_penalty = (barrier - 10) * 0.05
        
        weight = parse_float(runner.get('weight') or runner.get('weight_carried'), 55)
        weight_penalty = max(0, (weight - 55) * 0.02)
        
        days_since = runner.get('days_since_last_run', 0) or 0
        is_first_up = days_since > 60 if days_since else False
        is_second_up = 30 < days_since <= 60 if days_since else False
        
        base_prob = market_implied_prob if market_implied_prob > 0 else (100 / field_size)
        
        feature_adjustment = 1.0
        if distance_strike > 25:
            feature_adjustment += 0.1
        if course_strike > 25:
            feature_adjustment += 0.08
        if ground_strike > 30:
            feature_adjustment += 0.12
        if jockey_trainer_strike > 25:
            feature_adjustment += 0.08
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
            'track': meet_course,
            'race_number': race_number,
            'race_date': meet_date,
            'distance': str(distance),
            'going': going,
            'race_class': race_class,
            'horse_name': runner.get('horse', ''),
            'horse_number': str(runner.get('number', '')),
            'barrier': barrier,
            'jockey': runner.get('jockey', ''),
            'trainer': runner.get('trainer', ''),
            'weight': weight,
            'predicted_win_prob': predicted_win_prob,
            'predicted_place_prob': predicted_place_prob,
            'predicted_position': field_size / 2,
            'confidence': confidence,
            'expected_value': expected_value,
            'market_odds': starting_price,
            'actual_position': actual_position,
            'won': won,
            'placed': placed,
            'starting_price': starting_price,
            '_distance_strike': distance_strike,
            '_course_strike': course_strike,
            '_course_distance_strike': course_distance_strike,
            '_ground_strike': ground_strike,
            '_jockey_strike': jockey_strike,
            '_trainer_strike': trainer_strike,
            '_jockey_trainer_strike': jockey_trainer_strike,
            '_form_score': form_score,
            '_barrier_penalty': barrier_penalty,
            '_weight_penalty': weight_penalty,
            '_is_first_up': is_first_up,
            '_is_second_up': is_second_up,
            '_days_since_run': days_since,
            '_field_size': field_size,
        }
        
        records.append(record)
    
    return records


def get_class_level(race_class):
    """Map race class string to numeric level."""
    if not race_class:
        return 1
    rc = str(race_class).upper()
    if 'GROUP 1' in rc or 'G1' in rc:
        return 10
    elif 'GROUP 2' in rc or 'G2' in rc:
        return 9
    elif 'GROUP 3' in rc or 'G3' in rc:
        return 8
    elif 'LISTED' in rc:
        return 7
    elif 'OPEN' in rc:
        return 6
    elif 'BM' in rc or 'BENCHMARK' in rc:
        num = ''.join(c for c in rc if c.isdigit())
        if num:
            val = int(num)
            if val >= 85: return 6
            if val >= 72: return 5
            if val >= 64: return 4
            if val >= 58: return 3
            return 2
        return 3
    elif 'MAIDEN' in rc or 'MDN' in rc:
        return 1
    elif 'CLASS' in rc:
        return 3
    return 2


def import_to_database(records, db_url, batch_size=100):
    """Import records to PostgreSQL training_data table."""
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
                
                insert_query = text("""
                    INSERT INTO training_data (
                        race_id, track, race_number, race_date, distance, going, race_class,
                        horse_name, horse_number, barrier, jockey, trainer, weight,
                        predicted_win_prob, predicted_place_prob, predicted_position,
                        confidence, expected_value, market_odds,
                        actual_position, won, placed, starting_price
                    ) VALUES (
                        :race_id, :track, :race_number, :race_date, :distance, :going, :race_class,
                        :horse_name, :horse_number, :barrier, :jockey, :trainer, :weight,
                        :predicted_win_prob, :predicted_place_prob, :predicted_position,
                        :confidence, :expected_value, :market_odds,
                        :actual_position, :won, :placed, :starting_price
                    )
                """)
                
                try:
                    conn.execute(insert_query, {
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
                    print(f"  Error inserting {record['horse_name']}: {e}")
                    total_skipped += 1
            
            conn.commit()
            print(f"\r  training_data - Imported: {total_imported}, Skipped: {total_skipped}", end="", flush=True)
    
    return total_imported, total_skipped


def import_to_race_results_history(meets, db_url, batch_size=50):
    """Import race results into race_results_history table for form franking."""
    from sqlalchemy import create_engine, text
    import uuid
    
    engine = create_engine(db_url)
    
    total_imported = 0
    total_skipped = 0
    races_processed = 0
    
    with engine.connect() as conn:
        for meet in meets:
            course = meet.get('course', '')
            meet_date = meet.get('date', '')
            
            for race in meet.get('races', []):
                race_number = int(parse_float(race.get('race_number', 0), 0))
                race_id = race.get('race_id', f"{course}_{meet_date}_R{race_number}")
                distance_str = str(race.get('distance', '') or race.get('distance_metres', '') or '0')
                distance_m = int(parse_float(distance_str.replace('m', ''), 0))
                going = race.get('going', '') or race.get('going_description', '')
                race_class = race.get('class', '') or race.get('class_level', '')
                class_level = get_class_level(race_class)
                race_name = race.get('race_name', '')
                
                runners = race.get('runners', [])
                field_size = len([r for r in runners if not r.get('scratched')])
                
                opponents_by_horse = {}
                active_runners = []
                for runner in runners:
                    if runner.get('scratched'):
                        continue
                    pos = parse_position(runner.get('position') or runner.get('finishing_position'))
                    if pos is None:
                        continue
                    active_runners.append(runner)
                
                for runner in active_runners:
                    horse_name = runner.get('horse', '')
                    horse_id = runner.get('horse_id', '')
                    pos = parse_position(runner.get('position') or runner.get('finishing_position'))
                    opponents = []
                    for opp in active_runners:
                        if opp.get('horse', '') == horse_name:
                            continue
                        opp_pos = parse_position(opp.get('position') or opp.get('finishing_position'))
                        opponents.append({
                            'horse_name': opp.get('horse', ''),
                            'horse_id': opp.get('horse_id', ''),
                            'position': opp_pos,
                            'margin': parse_float(opp.get('margin'), 0),
                        })
                    opponents_by_horse[horse_name] = opponents
                
                for runner in active_runners:
                    horse_name = runner.get('horse', '')
                    horse_id = runner.get('horse_id', '')
                    pos = parse_position(runner.get('position') or runner.get('finishing_position'))
                    
                    check_query = text("""
                        SELECT id FROM race_results_history
                        WHERE track = :track
                        AND race_date = :race_date
                        AND race_number = :race_number
                        AND horse_name = :horse_name
                        LIMIT 1
                    """)
                    existing = conn.execute(check_query, {
                        'track': course,
                        'race_date': meet_date,
                        'race_number': race_number,
                        'horse_name': horse_name,
                    }).fetchone()
                    
                    if existing:
                        total_skipped += 1
                        continue
                    
                    margin = parse_float(runner.get('margin'), 0)
                    sp = parse_float(runner.get('sp') or runner.get('starting_price') or runner.get('odds_sp'), 0)
                    barrier = int(parse_float(runner.get('draw') or runner.get('barrier', 0), 0))
                    weight = parse_float(runner.get('weight') or runner.get('weight_carried'), 0)
                    form_string = runner.get('form', '')
                    jockey = runner.get('jockey', '')
                    jockey_id = runner.get('jockey_id', '')
                    
                    opponents_json = json.dumps(opponents_by_horse.get(horse_name, []))
                    
                    row_id = str(uuid.uuid4())
                    
                    insert_query = text("""
                        INSERT INTO race_results_history (
                            id, horse_id, horse_name, race_id, track, race_date,
                            distance_m, race_class, class_level, going, position,
                            margin_lengths, weight_kg, jockey, jockey_id, barrier,
                            sp_odds, field_size, race_name, race_number, form_string,
                            opponents_json
                        ) VALUES (
                            :id, :horse_id, :horse_name, :race_id, :track, :race_date,
                            :distance_m, :race_class, :class_level, :going, :position,
                            :margin_lengths, :weight_kg, :jockey, :jockey_id, :barrier,
                            :sp_odds, :field_size, :race_name, :race_number, :form_string,
                            CAST(:opponents_json AS jsonb)
                        )
                    """)
                    
                    try:
                        conn.execute(insert_query, {
                            'id': row_id,
                            'horse_id': horse_id,
                            'horse_name': horse_name,
                            'race_id': race_id,
                            'track': course,
                            'race_date': meet_date,
                            'distance_m': distance_m,
                            'race_class': race_class,
                            'class_level': class_level,
                            'going': going,
                            'position': pos,
                            'margin_lengths': margin,
                            'weight_kg': weight,
                            'jockey': jockey,
                            'jockey_id': jockey_id,
                            'barrier': barrier,
                            'sp_odds': sp,
                            'field_size': field_size,
                            'race_name': race_name,
                            'race_number': race_number,
                            'form_string': form_string,
                            'opponents_json': opponents_json,
                        })
                        total_imported += 1
                    except Exception as e:
                        conn.rollback()
                        total_skipped += 1
                
                races_processed += 1
                
                if races_processed % batch_size == 0:
                    conn.commit()
                    print(f"\r  race_results_history - Imported: {total_imported}, Skipped: {total_skipped}, Races: {races_processed}", end="", flush=True)
        
        conn.commit()
        print(f"\r  race_results_history - Imported: {total_imported}, Skipped: {total_skipped}, Races: {races_processed}", end="", flush=True)
    
    return total_imported, total_skipped


def main():
    parser = argparse.ArgumentParser(description="Import historical data to training_data table")
    parser.add_argument("--input", type=str, default="historical_data/historical_training_data.json",
                        help="Input JSON file from download_training_data.py")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of races to import (for testing)")
    args = parser.parse_args()
    
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("Error: DATABASE_URL environment variable not set")
        sys.exit(1)
    
    print("\n" + "=" * 70)
    print("  IMPORTING HISTORICAL DATA TO TRAINING DATABASE")
    print("=" * 70)
    
    if not os.path.exists(args.input):
        print(f"\nError: Input file not found: {args.input}")
        print("Run download_training_data.py first to download historical data.")
        sys.exit(1)
    
    print(f"\n  Loading: {args.input}")
    with open(args.input, 'r') as f:
        data = json.load(f)
    
    metadata = data.get('metadata', {})
    meets = data.get('meets', [])
    
    print(f"  Date range: {metadata.get('date_range_start')} to {metadata.get('date_range_end')}")
    print(f"  Total meets: {len(meets)}")
    print(f"  Total races: {metadata.get('total_races', 'N/A')}")
    
    print("\n  Processing races...")
    all_records = []
    races_processed = 0
    
    for meet in meets:
        course = meet.get('course', '')
        date = meet.get('date', '')
        
        for race in meet.get('races', []):
            records = process_race_for_training(race, course, date)
            all_records.extend(records)
            races_processed += 1
            
            if args.limit and races_processed >= args.limit:
                break
        
        if args.limit and races_processed >= args.limit:
            break
    
    print(f"  Processed {races_processed} races")
    print(f"  Total runner records: {len(all_records)}")
    
    winners = sum(1 for r in all_records if r.get('won') == 1)
    placed = sum(1 for r in all_records if r.get('placed') == 1)
    
    print(f"\n  Winners: {winners}")
    print(f"  Placed (top 3): {placed}")
    
    print("\n  Importing to training_data table...")
    imported, skipped = import_to_database(all_records, db_url)
    
    print(f"\n  training_data: {imported} imported, {skipped} skipped")
    
    print("\n  Importing to race_results_history table...")
    rr_imported, rr_skipped = import_to_race_results_history(meets, db_url)
    
    print(f"\n  race_results_history: {rr_imported} imported, {rr_skipped} skipped")
    
    print("\n\n" + "=" * 70)
    print("  IMPORT COMPLETE")
    print("=" * 70)
    print(f"\n  training_data: {imported} imported, {skipped} skipped")
    print(f"  race_results_history: {rr_imported} imported, {rr_skipped} skipped")
    print(f"\n  Next step: Train the ML model")
    print("    python3 server/python/train_ml.py")
    print()


if __name__ == '__main__':
    main()
