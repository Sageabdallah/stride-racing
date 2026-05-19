#!/usr/bin/env python3
"""
Speed Ratings Engine

Calculates synthetic speed ratings from:
- Finishing margins
- Race times (when available)
- Track/distance par times
- Going adjustments
"""

import sys
import json
from typing import Dict, List, Optional, Tuple
import math

PAR_TIMES = {
    '1000': {'fast': 56.5, 'good': 57.5, 'soft': 59.0, 'heavy': 61.0},
    '1100': {'fast': 62.5, 'good': 63.5, 'soft': 65.5, 'heavy': 68.0},
    '1200': {'fast': 68.5, 'good': 69.5, 'soft': 72.0, 'heavy': 75.0},
    '1300': {'fast': 75.0, 'good': 76.5, 'soft': 79.0, 'heavy': 82.5},
    '1400': {'fast': 81.0, 'good': 82.5, 'soft': 85.5, 'heavy': 89.5},
    '1500': {'fast': 88.0, 'good': 90.0, 'soft': 93.0, 'heavy': 97.0},
    '1600': {'fast': 94.0, 'good': 96.0, 'soft': 99.5, 'heavy': 104.0},
    '1800': {'fast': 107.0, 'good': 109.5, 'soft': 113.5, 'heavy': 119.0},
    '2000': {'fast': 120.0, 'good': 123.0, 'soft': 127.5, 'heavy': 134.0},
    '2200': {'fast': 133.0, 'good': 136.5, 'soft': 141.5, 'heavy': 149.0},
    '2400': {'fast': 146.0, 'good': 150.0, 'soft': 155.5, 'heavy': 164.0},
}

TRACK_ADJUSTMENTS = {
    'randwick': 0,
    'flemington': -1,
    'moonee valley': +2,
    'caulfield': 0,
    'rosehill': +1,
    'eagle farm': +1,
    'doomben': +2,
    'warwick farm': +2,
    'sandown': +1,
    'ascot': +1,
}


def get_par_time(distance: int, going: str) -> float:
    """Get par time for a distance and going condition."""
    going_lower = going.lower() if going else 'good'
    
    if 'heavy' in going_lower:
        going_key = 'heavy'
    elif 'soft' in going_lower:
        going_key = 'soft'
    elif 'fast' in going_lower or 'firm' in going_lower:
        going_key = 'fast'
    else:
        going_key = 'good'
    
    distance_str = str(distance)
    if distance_str in PAR_TIMES:
        return PAR_TIMES[distance_str][going_key]
    
    sorted_distances = sorted([int(d) for d in PAR_TIMES.keys()])
    for i, d in enumerate(sorted_distances[:-1]):
        if d < distance < sorted_distances[i+1]:
            lower = PAR_TIMES[str(d)][going_key]
            upper = PAR_TIMES[str(sorted_distances[i+1])][going_key]
            ratio = (distance - d) / (sorted_distances[i+1] - d)
            return lower + ratio * (upper - lower)
    
    return PAR_TIMES['1600'][going_key]


def get_track_adjustment(track: str) -> float:
    """Get track-specific adjustment."""
    track_lower = track.lower()
    for track_name, adj in TRACK_ADJUSTMENTS.items():
        if track_name in track_lower:
            return adj
    return 0


def calculate_speed_rating_from_time(
    race_time: float,
    distance: int,
    going: str,
    track: str
) -> float:
    """
    Calculate speed rating from actual race time.

    Base 100 = par time for distance/going
    Each 0.1s faster = +0.5 rating points
    """
    par = get_par_time(distance, going)
    track_adj = get_track_adjustment(track)
    
    diff = par - race_time
    rating = 100 + (diff * 5) + track_adj
    
    return max(60, min(130, round(rating, 1)))


def calculate_speed_rating_from_margin(
    margin: float,
    winner_rating: float,
    distance: int
) -> float:
    """
    Calculate speed rating from beaten margin.

    Approximately 0.17s per length at most distances.
    """
    lengths_per_second = 6.0
    if distance <= 1200:
        lengths_per_second = 5.5
    elif distance >= 2000:
        lengths_per_second = 6.5
    
    seconds_behind = margin / lengths_per_second
    points_behind = seconds_behind * 5
    
    rating = winner_rating - points_behind
    return max(60, min(130, round(rating, 1)))


def parse_form_for_ratings(form_string: str) -> List[int]:
    """Extract finishing positions from form string."""
    if not form_string:
        return []
    
    positions = []
    form = form_string.replace('-', '').strip()
    
    for char in form:
        if char.isdigit():
            positions.append(int(char))
        elif char.lower() == 'x':
            positions.append(10)
        elif char.lower() == '0':
            positions.append(10)
    
    return positions[:6]


def estimate_speed_rating_from_form(
    form_positions: List[int],
    avg_field_size: int = 10,
    class_level: int = 50
) -> Tuple[float, float, float]:
    """
    Estimate speed ratings from form positions.

    Returns: (last_rating, best_rating, avg_rating)
    """
    _ = avg_field_size
    if not form_positions:
        return (85.0, 85.0, 85.0)
    
    base_rating = 80 + (class_level / 5)
    
    ratings = []
    for i, pos in enumerate(form_positions):
        recency_weight = 1.0 - (i * 0.1)
        
        if pos == 1:
            run_rating = base_rating + 8
        elif pos == 2:
            run_rating = base_rating + 4
        elif pos == 3:
            run_rating = base_rating + 2
        elif pos <= 5:
            run_rating = base_rating
        else:
            run_rating = base_rating - ((pos - 5) * 1.5)
        
        ratings.append(run_rating * recency_weight + base_rating * (1 - recency_weight))
    
    last_rating = ratings[0] if ratings else base_rating
    best_rating = max(ratings) if ratings else base_rating
    avg_rating = sum(ratings) / len(ratings) if ratings else base_rating
    
    return (
        round(last_rating, 1),
        round(best_rating, 1),
        round(avg_rating, 1)
    )


def calculate_late_speed(
    early_position: int,
    final_position: int,
    field_size: int
) -> float:
    """
    Calculate late speed metric (closing ability).

    Positive = strong closer
    Negative = weakened
    """
    if field_size <= 0:
        return 0.0
    
    early_pct = early_position / field_size
    final_pct = final_position / field_size
    
    late_speed = (early_pct - final_pct) * 100
    return round(late_speed, 1)


def extract_speed_features(runner: Dict, race_data: Dict) -> Dict:
    """Extract all speed-related features for ML."""
    form = runner.get('form') or runner.get('form_string', '')
    class_level = race_data.get('class_level', 50)
    distance = race_data.get('distance_numeric', 1400)
    going = race_data.get('going', 'Good')
    track = race_data.get('track', '')
    
    form_positions = parse_form_for_ratings(form)
    last_rating, best_rating, avg_rating = estimate_speed_rating_from_form(
        form_positions, 
        avg_field_size=10,
        class_level=class_level
    )
    
    improvement_trend = 0
    if len(form_positions) >= 3:
        recent_avg = sum(form_positions[:2]) / 2
        older_avg = sum(form_positions[2:4]) / 2 if len(form_positions) >= 4 else sum(form_positions[2:]) / len(form_positions[2:])
        improvement_trend = older_avg - recent_avg
    
    last_pos = runner.get('last_finishing_position') or (form_positions[0] if form_positions else None)
    last_early_pos = runner.get('last_position_in_running')
    
    late_speed = 0.0
    if last_pos and last_early_pos:
        late_speed = calculate_late_speed(last_early_pos, last_pos, 10)
    
    rating_for_distance = avg_rating
    dist_pref = runner.get('distance_preference', '')
    if dist_pref:
        if 'sprinter' in dist_pref.lower() and distance > 1400:
            rating_for_distance -= 3
        elif 'stayer' in dist_pref.lower() and distance < 1600:
            rating_for_distance -= 3
    
    return {
        'speed_rating': last_rating,
        'last_speed_rating': last_rating,
        'best_speed_rating': best_rating,
        'avg_speed_rating': avg_rating,
        'rating_for_distance': rating_for_distance,
        'improvement_trend': improvement_trend,
        'late_speed': late_speed,
        'is_strong_closer': late_speed > 10,
        'is_weakener': late_speed < -10,
        'rating_above_class': last_rating > (80 + class_level / 5),
        'rating_consistency': max(0, 10 - abs(best_rating - last_rating))
    }


def analyze_field_speed(runners: List[Dict], race_data: Dict) -> Dict:
    """Analyze speed ratings across the field."""
    ratings = []
    for runner in runners:
        features = extract_speed_features(runner, race_data)
        ratings.append({
            'name': runner.get('horse') or runner.get('name') or runner.get('horse_name', 'Unknown'),
            'speed_rating': features['speed_rating'],
            'best_rating': features['best_speed_rating'],
            'late_speed': features['late_speed']
        })
    
    ratings.sort(key=lambda x: x['best_rating'], reverse=True)
    
    all_ratings = [r['speed_rating'] for r in ratings if r['speed_rating']]
    
    return {
        'top_rated': ratings[:3] if ratings else [],
        'best_closers': sorted(ratings, key=lambda x: x['late_speed'], reverse=True)[:3],
        'field_avg_rating': sum(all_ratings) / len(all_ratings) if all_ratings else 85,
        'rating_spread': max(all_ratings) - min(all_ratings) if all_ratings else 0
    }


if __name__ == '__main__':
    test_runner = {
        'horse': 'Test Horse',
        'form': '12341',
    }
    
    test_race = {
        'distance_numeric': 1400,
        'going': 'Good',
        'track': 'Randwick',
        'class_level': 60
    }
    
    features = extract_speed_features(test_runner, test_race)
    print(json.dumps(features, indent=2))
