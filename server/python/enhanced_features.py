#!/usr/bin/env python3
"""
Advanced racing features for improving ML predictions: class movement detection,
track-specific barrier bias, and head-to-head history.
"""

import os
import sys
import json
import psycopg2
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Any

def get_db_connection():
    """Get database connection from environment."""
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        return None
    try:
        return psycopg2.connect(database_url)
    except Exception as e:
        print(f"[EnhancedFeatures] DB connection error: {e}", file=sys.stderr)
        return None



CLASS_RANKINGS = {
    'group 1': 10,
    'group 2': 9,
    'group 3': 8,
    'listed': 7,
    'open': 6,
    'benchmark 90': 5.5,
    'benchmark 85': 5.3,
    'benchmark 80': 5.1,
    'benchmark 78': 5,
    'benchmark 75': 4.8,
    'benchmark 72': 4.6,
    'benchmark 70': 4.5,
    'benchmark 68': 4.3,
    'benchmark 66': 4.1,
    'benchmark 65': 4,
    'benchmark 64': 3.9,
    'benchmark 62': 3.7,
    'benchmark 60': 3.5,
    'benchmark 58': 3.3,
    'class 6': 3.2,
    'class 5': 3,
    'class 4': 2.8,
    'class 3': 2.5,
    'class 2': 2.2,
    'class 1': 2,
    'maiden special weight': 1.5,
    'maiden': 1,
    'restricted maiden': 0.8,
    '2yo maiden': 0.7,
    '3yo maiden': 0.9,
}

def parse_class_rank(race_class: str) -> float:
    """Parse race class into numeric ranking (higher = stronger class)."""
    if not race_class:
        return 3.0
    
    class_lower = race_class.lower().strip()
    
    for key, rank in CLASS_RANKINGS.items():
        if key in class_lower:
            return rank
    
    if 'benchmark' in class_lower or 'bm' in class_lower:
        import re
        match = re.search(r'(\d+)', class_lower)
        if match:
            bm = int(match.group(1))
            return 3.0 + (bm - 60) * 0.05
    
    if 'maiden' in class_lower:
        return 1.0
    if 'open' in class_lower:
        return 6.0
    if 'handicap' in class_lower:
        return 4.0
    
    return 3.0


def get_horse_recent_classes(horse_name: str, current_date: str, conn) -> List[float]:
    """
    Get class rankings from horse's recent races (last 6 runs).
    Tries training_data first, falls back to race_results_history.
    """
    if not conn:
        return []
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT race_class, race_date
            FROM training_data
            WHERE horse_name = %s AND race_date < %s
            ORDER BY race_date DESC
            LIMIT 6
        """, (horse_name, current_date))
        
        rows = cursor.fetchall()
        cursor.close()
        
        results = [parse_class_rank(row[0]) for row in rows if row[0]]
        if results:
            return results
    except Exception as e:
        print(f"[ClassMovement] Error fetching training_data for {horse_name}: {e}", file=sys.stderr)
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT race_class, race_date
            FROM race_results_history
            WHERE LOWER(horse_name) = LOWER(%s) AND race_date < %s
            ORDER BY race_date DESC
            LIMIT 6
        """, (horse_name, current_date))
        
        rows = cursor.fetchall()
        cursor.close()
        
        return [parse_class_rank(row[0]) for row in rows if row[0]]
    except Exception as e:
        print(f"[ClassMovement] Error fetching race_results_history for {horse_name}: {e}", file=sys.stderr)
        return []


def calculate_class_movement(horse_name: str, current_class: str, current_date: str, conn=None) -> Dict[str, Any]:
    """
    Calculate class movement factor for a horse.

    Returns dict with class_change (float, -2 to +2, positive = dropping in class),
    magnitude, and adjustment multiplier.
    """
    current_rank = parse_class_rank(current_class)
    
    if conn is None:
        conn = get_db_connection()
        should_close = True
    else:
        should_close = False
    
    recent_classes = get_horse_recent_classes(horse_name, current_date, conn)
    
    if should_close and conn:
        conn.close()
    
    if not recent_classes:
        return {
            'class_change': 0.0,
            'factor': 1.0,
            'description': 'No prior class history',
            'current_rank': current_rank,
            'avg_recent_rank': None
        }
    
    weights = [1.0, 0.8, 0.6, 0.5, 0.4, 0.3][:len(recent_classes)]
    total_weight = sum(weights)
    avg_recent = sum(c * w for c, w in zip(recent_classes, weights)) / total_weight
    
    class_change = avg_recent - current_rank
    
    if class_change > 1.5:
        factor = 1.20
        description = f"Significant drop in class (was avg {avg_recent:.1f}, now {current_rank:.1f})"
    elif class_change > 0.8:
        factor = 1.12
        description = f"Dropping in class (was avg {avg_recent:.1f}, now {current_rank:.1f})"
    elif class_change > 0.3:
        factor = 1.06
        description = f"Slight class drop"
    elif class_change < -1.5:
        factor = 0.85
        description = f"Significant rise in class (was avg {avg_recent:.1f}, now {current_rank:.1f})"
    elif class_change < -0.8:
        factor = 0.90
        description = f"Rising in class"
    elif class_change < -0.3:
        factor = 0.95
        description = f"Slight class rise"
    else:
        factor = 1.0
        description = "Racing at similar class level"
    
    return {
        'class_change': round(class_change, 2),
        'factor': factor,
        'description': description,
        'current_rank': current_rank,
        'avg_recent_rank': round(avg_recent, 2)
    }



_barrier_bias_cache = {}

def parse_distance_band(distance_str: str) -> str:
    """Parse distance into a band for barrier analysis."""
    if not distance_str:
        return 'medium'
    
    import re
    match = re.search(r'(\d+)', str(distance_str))
    if not match:
        return 'medium'
    
    meters = int(match.group(1))
    
    if meters <= 1100:
        return 'sprint'
    elif meters <= 1400:
        return 'short'
    elif meters <= 1800:
        return 'medium'
    elif meters <= 2200:
        return 'middle'
    else:
        return 'staying'


def build_barrier_bias_table(conn) -> Dict[str, Dict[str, Dict[int, Dict[str, float]]]]:
    """
    Build barrier bias lookup table from training data.

    Returns nested dict: track -> distance_band -> barrier -> {wins, runs, win_rate, bias}.
    """
    if not conn:
        return {}
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT track, distance, barrier, 
                   COUNT(*) as runs,
                   SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) as wins
            FROM training_data
            WHERE barrier IS NOT NULL AND barrier > 0 AND barrier <= 20
            GROUP BY track, distance, barrier
            ORDER BY track, distance, barrier
        """)
        
        rows = cursor.fetchall()
        cursor.close()
        
        raw_data = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {'wins': 0, 'runs': 0})))
        
        for track, distance, barrier, runs, wins in rows:
            dist_band = parse_distance_band(distance)
            raw_data[track][dist_band][barrier]['wins'] += wins or 0
            raw_data[track][dist_band][barrier]['runs'] += runs or 0
        
        bias_table = {}
        for track, dist_bands in raw_data.items():
            bias_table[track] = {}
            for dist_band, barriers in dist_bands.items():
                total_runs = sum(b['runs'] for b in barriers.values())
                total_wins = sum(b['wins'] for b in barriers.values())
                
                if total_runs < 50:
                    continue
                
                expected_rate = total_wins / total_runs if total_runs > 0 else 0.1
                
                bias_table[track][dist_band] = {}
                for barrier, data in barriers.items():
                    if data['runs'] < 10:
                        continue
                    
                    actual_rate = data['wins'] / data['runs'] if data['runs'] > 0 else 0
                    
                    bias = actual_rate / expected_rate if expected_rate > 0 else 1.0
                    bias = max(0.5, min(1.8, bias))
                    
                    bias_table[track][dist_band][barrier] = {
                        'wins': data['wins'],
                        'runs': data['runs'],
                        'win_rate': round(actual_rate * 100, 1),
                        'expected_rate': round(expected_rate * 100, 1),
                        'bias': round(bias, 3)
                    }
        
        return bias_table
    
    except Exception as e:
        print(f"[BarrierBias] Error building table: {e}", file=sys.stderr)
        return {}


def get_barrier_bias(track: str, distance: str, barrier: int, conn=None) -> Dict[str, Any]:
    """
    Get barrier bias factor for a specific position.

    Returns dict with bias multiplier (0.7 to 1.5, >1 = favourable).
    """
    global _barrier_bias_cache
    
    if not _barrier_bias_cache:
        if conn is None:
            conn = get_db_connection()
            should_close = True
        else:
            should_close = False
        
        if conn:
            _barrier_bias_cache = build_barrier_bias_table(conn)
            if should_close:
                conn.close()
    
    dist_band = parse_distance_band(distance)
    
    if track in _barrier_bias_cache:
        track_data = _barrier_bias_cache[track]
    else:
        for cached_track in _barrier_bias_cache:
            if track.lower() in cached_track.lower() or cached_track.lower() in track.lower():
                track_data = _barrier_bias_cache[cached_track]
                break
        else:
            track_data = {}
    
    if dist_band not in track_data:
        for alt_band in ['medium', 'short', 'sprint']:
            if alt_band in track_data:
                dist_band = alt_band
                break
    
    if dist_band in track_data and barrier in track_data[dist_band]:
        stats = track_data[dist_band][barrier]
        bias = stats['bias']
        
        if bias > 1.3:
            description = f"Strong barrier advantage at {track} ({dist_band}, barrier {barrier} wins {stats['win_rate']}% vs {stats['expected_rate']}% expected)"
        elif bias > 1.1:
            description = f"Slight barrier advantage"
        elif bias < 0.7:
            description = f"Poor barrier historically ({stats['win_rate']}% vs {stats['expected_rate']}% expected)"
        elif bias < 0.9:
            description = f"Slight barrier disadvantage"
        else:
            description = "Neutral barrier draw"
        
        return {
            'bias': bias,
            'factor': bias,
            'description': description,
            'stats': stats,
            'track': track,
            'distance_band': dist_band,
            'barrier': barrier
        }
    
    if barrier <= 4:
        bias = 1.05
        description = "Inside barrier (generally favourable)"
    elif barrier <= 8:
        bias = 1.0
        description = "Mid barrier (neutral)"
    elif barrier <= 12:
        bias = 0.95
        description = "Wide barrier (slight disadvantage)"
    else:
        bias = 0.88
        description = "Very wide barrier (significant disadvantage)"
    
    return {
        'bias': bias,
        'factor': bias,
        'description': description + " (using default model)",
        'stats': None,
        'track': track,
        'distance_band': dist_band,
        'barrier': barrier
    }



def get_head_to_head_history(horse_names: List[str], current_date: str, conn=None) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """
    Analyse head-to-head matchups between horses in a race.

    Returns nested dict: horse1 -> horse2 -> {wins, losses, meetings, advantage}.
    """
    if len(horse_names) < 2:
        return {}
    
    if conn is None:
        conn = get_db_connection()
        should_close = True
    else:
        should_close = False
    
    if not conn:
        return {}
    
    try:
        cursor = conn.cursor()
        
        placeholders = ','.join(['%s'] * len(horse_names))
        cursor.execute(f"""
            SELECT race_id, horse_name, actual_position
            FROM training_data
            WHERE horse_name IN ({placeholders})
              AND race_date < %s
              AND actual_position IS NOT NULL
            ORDER BY race_id, actual_position
        """, (*horse_names, current_date))
        
        rows = cursor.fetchall()
        cursor.close()
        
        if should_close:
            conn.close()
        
        races = defaultdict(list)
        for race_id, horse_name, position in rows:
            races[race_id].append((horse_name, position))
        
        h2h = defaultdict(lambda: defaultdict(lambda: {'wins': 0, 'losses': 0, 'meetings': 0}))
        
        for race_id, horses_in_race in races.items():
            race_horses = [(h, p) for h, p in horses_in_race if h in horse_names]
            
            if len(race_horses) < 2:
                continue
            
            for i, (horse1, pos1) in enumerate(race_horses):
                for horse2, pos2 in race_horses[i+1:]:
                    h2h[horse1][horse2]['meetings'] += 1
                    h2h[horse2][horse1]['meetings'] += 1
                    
                    if pos1 < pos2:
                        h2h[horse1][horse2]['wins'] += 1
                        h2h[horse2][horse1]['losses'] += 1
                    elif pos2 < pos1:
                        h2h[horse2][horse1]['wins'] += 1
                        h2h[horse1][horse2]['losses'] += 1
        
        for horse1 in h2h:
            for horse2 in h2h[horse1]:
                record = h2h[horse1][horse2]
                meetings = record['meetings']
                if meetings >= 2:
                    win_rate = record['wins'] / meetings
                    if win_rate >= 0.75 and meetings >= 3:
                        record['advantage'] = 'strong'
                        record['factor'] = 1.10
                    elif win_rate >= 0.6:
                        record['advantage'] = 'slight'
                        record['factor'] = 1.05
                    elif win_rate <= 0.25 and meetings >= 3:
                        record['advantage'] = 'strong_against'
                        record['factor'] = 0.92
                    elif win_rate <= 0.4:
                        record['advantage'] = 'slight_against'
                        record['factor'] = 0.96
                    else:
                        record['advantage'] = 'neutral'
                        record['factor'] = 1.0
                else:
                    record['advantage'] = 'insufficient_data'
                    record['factor'] = 1.0
        
        return dict(h2h)
    
    except Exception as e:
        print(f"[HeadToHead] Error analyzing history: {e}", file=sys.stderr)
        if should_close and conn:
            conn.close()
        return {}


def calculate_h2h_adjustment(horse_name: str, all_horses: List[str], current_date: str, conn=None) -> Dict[str, Any]:
    """
    Calculate overall head-to-head adjustment for a horse against the field.

    Returns dict with factor (win probability multiplier) and summary.
    """
    h2h_data = get_head_to_head_history(all_horses, current_date, conn)
    
    if horse_name not in h2h_data:
        return {
            'factor': 1.0,
            'significant_matchups': [],
            'description': 'No head-to-head history with rivals',
            'matchup_count': 0
        }
    
    horse_matchups = h2h_data[horse_name]
    
    significant = []
    factors = []
    
    for opponent, record in horse_matchups.items():
        if record['meetings'] >= 2:
            significant.append({
                'opponent': opponent,
                'record': f"{record['wins']}-{record['losses']} ({record['meetings']} meetings)",
                'advantage': record['advantage'],
                'factor': record['factor']
            })
            factors.append(record['factor'])
    
    if not factors:
        return {
            'factor': 1.0,
            'significant_matchups': [],
            'description': 'No significant head-to-head history',
            'matchup_count': 0
        }
    
    avg_factor = sum(factors) / len(factors)
    
    avg_factor = max(0.85, min(1.15, avg_factor))
    
    strong_over = sum(1 for m in significant if m['advantage'] == 'strong')
    strong_under = sum(1 for m in significant if m['advantage'] == 'strong_against')
    
    if strong_over >= 2:
        description = f"Dominates {strong_over} rivals in head-to-head"
    elif strong_over == 1:
        opponent = next(m['opponent'] for m in significant if m['advantage'] == 'strong')
        description = f"Strong head-to-head record vs {opponent}"
    elif strong_under >= 2:
        description = f"Poor head-to-head record vs {strong_under} rivals"
    elif strong_under == 1:
        opponent = next(m['opponent'] for m in significant if m['advantage'] == 'strong_against')
        description = f"Poor record vs {opponent}"
    elif len(significant) > 0:
        description = f"Mixed head-to-head form with {len(significant)} rivals"
    else:
        description = "Limited head-to-head data"
    
    return {
        'factor': round(avg_factor, 3),
        'significant_matchups': significant,
        'description': description,
        'matchup_count': len(significant)
    }



def extract_enhanced_features(horse_data: Dict, race_data: Dict, all_horses: List[Dict], conn=None) -> Dict[str, Any]:
    """
    Extract all enhanced features for a horse.

    Args:
        horse_data: dict with horse_name, barrier, etc.
        race_data: dict with track, distance, etc.
    """
    horse_name = horse_data.get('horse_name', horse_data.get('horseName', ''))
    barrier = horse_data.get('barrier', horse_data.get('barrierDraw', 0))
    if isinstance(barrier, str):
        try:
            barrier = int(barrier)
        except:
            barrier = 8
    
    track = race_data.get('track', '')
    distance = race_data.get('distance', '')
    race_class = race_data.get('race_class', race_data.get('raceClass', ''))
    race_date = race_data.get('race_date', race_data.get('raceDate', ''))
    
    all_horse_names = [
        h.get('horse_name', h.get('horseName', '')) 
        for h in all_horses
    ]
    
    if conn is None:
        conn = get_db_connection()
        should_close = True
        if conn is None:
            print(f"[EnhancedFeatures] WARNING: DB connection failed for {horse_name} - all enhanced factors will be neutral", file=sys.stderr)
    else:
        should_close = False
    
    class_result = calculate_class_movement(horse_name, race_class, race_date, conn)
    barrier_result = get_barrier_bias(track, distance, barrier, conn)
    h2h_result = calculate_h2h_adjustment(horse_name, all_horse_names, race_date, conn)
    
    if should_close and conn:
        conn.close()
    
    combined_factor = class_result['factor'] * barrier_result['factor'] * h2h_result['factor']
    combined_factor = max(0.70, min(1.40, combined_factor))
    
    explanations = []
    if class_result['factor'] != 1.0:
        explanations.append(class_result['description'])
    if barrier_result['factor'] != 1.0 and barrier_result['stats']:
        explanations.append(barrier_result['description'])
    if h2h_result['factor'] != 1.0:
        explanations.append(h2h_result['description'])
    
    return {
        'class_movement': class_result,
        'barrier_bias': barrier_result,
        'head_to_head': h2h_result,
        'combined_factor': round(combined_factor, 3),
        'explanations': explanations,
        'summary': ' | '.join(explanations) if explanations else 'Standard profile'
    }


def refresh_barrier_cache():
    """Force refresh of barrier bias cache."""
    global _barrier_bias_cache
    _barrier_bias_cache = {}
    conn = get_db_connection()
    if conn:
        _barrier_bias_cache = build_barrier_bias_table(conn)
        conn.close()
    return len(_barrier_bias_cache)


if __name__ == '__main__':
    print("Testing Enhanced Features...")
    
    conn = get_db_connection()
    if conn:
        print("\n1. Class Movement Detection:")
        class_test = calculate_class_movement('Verry Elleegant', 'Group 1', '2026-01-26', conn)
        print(f"   {class_test}")
        
        print("\n2. Barrier Bias Analysis:")
        bias_test = get_barrier_bias('Randwick', '1600m', 3, conn)
        print(f"   {bias_test}")
        
        print("\n3. Head-to-Head History:")
        h2h_test = calculate_h2h_adjustment('Horse A', ['Horse A', 'Horse B', 'Horse C'], '2026-01-26', conn)
        print(f"   {h2h_test}")
        
        conn.close()
        print("\nAll tests completed!")
    else:
        print("No database connection available")
