"""Data normalization module: cleans raw API JSON into a consistent schema for the ML pipeline."""

from typing import Dict, List, Any, Optional
import re


def normalize_track_name(track: Optional[str]) -> str:
    """
    Normalize track name to consistent format.
    Handles variations like 'Sportsbet Sandown Hillside' -> 'Sandown'
    """
    if not track:
        return ""
    
    track = str(track).strip()
    
    prefixes = [
        'sportsbet ', 'tab ', 'bet365 ', 'ladbrokes ', 
        'thomas farms rc ', 'racing nsw ', 'racing vic '
    ]
    track_lower = track.lower()
    for prefix in prefixes:
        if track_lower.startswith(prefix):
            track = track[len(prefix):]
            break
    
    suffixes = [' hillside', ' lakeside', ' synthetic', ' turf', ' park']
    for suffix in suffixes:
        if track_lower.endswith(suffix):
            track = track[:-len(suffix)]
            break
    
    return track.strip().title()


def normalize_going(going: Optional[str]) -> Dict[str, Any]:
    """
    Normalize going/track condition to consistent format.

    Returns:
        Dict with 'condition' (Good/Soft/Heavy/Synthetic) and 'numeric_rating'.
    """
    if not going:
        return {'condition': 'Good', 'rating': 4, 'raw': ''}
    
    going_str = str(going).lower().strip()
    
    rating_match = re.search(r'(\d+)', going_str)
    rating = int(rating_match.group(1)) if rating_match else None
    
    condition = 'Good'
    if 'firm' in going_str:
        condition = 'Firm'
        if rating is None:
            rating = 2
    elif 'heavy' in going_str:
        condition = 'Heavy'
        if rating is None:
            rating = 9
    elif 'soft' in going_str:
        condition = 'Soft'
        if rating is None:
            rating = 6
    elif 'good' in going_str:
        condition = 'Good'
        if rating is None:
            rating = 4
    elif 'synthetic' in going_str or 'all weather' in going_str:
        condition = 'Synthetic'
        rating = 5
    
    if rating is None:
        rating = 4
    
    return {
        'condition': condition,
        'rating': rating,
        'raw': going_str
    }


def normalize_distance(distance: Any) -> int:
    """
    Normalize distance to meters as integer.
    Handles formats like '1400m', '1400', 1400, '1m 200y', etc.
    """
    if not distance:
        return 1400
    
    dist_str = str(distance).lower().strip()
    
    if dist_str.isdigit():
        return int(dist_str)
    
    match = re.search(r'(\d+)\s*m', dist_str)
    if match:
        return int(match.group(1))
    
    mile_match = re.search(r'(\d+)\s*m.*?(\d+)\s*y', dist_str)
    if mile_match:
        miles = int(mile_match.group(1))
        yards = int(mile_match.group(2))
        return int(miles * 1609 + yards * 0.9144)
    
    num_match = re.search(r'(\d+)', dist_str)
    if num_match:
        val = int(num_match.group(1))
        if val < 100:
            return int(val * 201)
        return val
    
    return 1400


def normalize_weight(weight: Any) -> float:
    """
    Normalize weight to kg as float.
    Handles formats like '58.5kg', '58.5', '9-7' (st-lb)
    """
    if not weight:
        return 56.0
    
    weight_str = str(weight).lower().strip()
    
    weight_str = weight_str.replace('kg', '').strip()
    
    try:
        return float(weight_str)
    except ValueError:
        pass
    
    st_lb_match = re.search(r'(\d+)-(\d+)', weight_str)
    if st_lb_match:
        stones = int(st_lb_match.group(1))
        pounds = int(st_lb_match.group(2))
        return round((stones * 14 + pounds) * 0.4536, 1)
    
    return 56.0


def normalize_barrier(barrier: Any) -> int:
    """Normalize barrier/draw to integer."""
    if not barrier:
        return 0
    try:
        return int(barrier)
    except (ValueError, TypeError):
        return 0


def normalize_age(age: Any) -> int:
    """
    Normalize age to integer years.
    Handles formats like '4', '4yo', '4 years'
    """
    if not age:
        return 4
    
    age_str = str(age).lower().strip()
    
    match = re.search(r'(\d+)', age_str)
    if match:
        return int(match.group(1))
    
    return 4


def is_scratched(runner: Dict) -> bool:
    """
    Determine if a runner is scratched/withdrawn.
    Checks multiple field names for maximum compatibility.
    """
    scratched = runner.get('scratched')
    if scratched is True or str(scratched).lower() == 'true':
        return True
    
    is_scratched_val = runner.get('is_scratched')
    if is_scratched_val is True or str(is_scratched_val).lower() == 'true':
        return True
    
    withdrawn = runner.get('withdrawn')
    if withdrawn is True or str(withdrawn).lower() == 'true':
        return True
    
    emergency = runner.get('emergency')
    if emergency is True or str(emergency).lower() == 'true':
        return True
    
    status = str(runner.get('status', '')).lower()
    if status in ('scratched', 'withdrawn', 'late_scratching', 'non-runner', 
                  'emergency', 'reserve', 'removed'):
        return True
    
    runner_status = str(runner.get('runner_status', '')).lower()
    if runner_status in ('scratched', 'withdrawn', 'late_scratching', 'non-runner'):
        return True
    
    return False


def normalize_runner(runner: Dict, race_data: Dict) -> Dict:
    """Normalize a single runner to consistent schema."""
    normalized = {
        'horse_id': runner.get('horse_id') or runner.get('id') or hash(runner.get('horse', runner.get('name', ''))),
        'horse': runner.get('horse') or runner.get('name') or runner.get('horseName') or '',
        'number': runner.get('number') or runner.get('cloth_number') or runner.get('saddleCloth') or 0,
        'barrier': normalize_barrier(runner.get('barrier') or runner.get('draw')),
        'weight': normalize_weight(runner.get('weight') or runner.get('jockey_weight')),
        'age': normalize_age(runner.get('age')),
        'jockey': runner.get('jockey') or runner.get('jockeyName') or '',
        'jockey_id': runner.get('jockey_id') or hash(runner.get('jockey', '')),
        'trainer': runner.get('trainer') or runner.get('trainerName') or '',
        'trainer_id': runner.get('trainer_id') or hash(runner.get('trainer', '')),
        'form': runner.get('form') or runner.get('recent_form') or '',
        'stats': runner.get('stats') or {},
        'is_scratched': is_scratched(runner),
        'market_odds': None,
        'opening_odds': None,
    }
    
    for odds_field in ['sp', 'odds', 'market_odds', 'price', 'win_odds']:
        odds_val = runner.get(odds_field)
        if odds_val:
            try:
                odds = float(str(odds_val).replace('$', '').strip())
                if 1.01 < odds < 200:
                    normalized['market_odds'] = odds
                    break
            except (ValueError, TypeError):
                pass
    
    for odds_field in ['opening_odds', 'morning_price', 'morning_line']:
        odds_val = runner.get(odds_field)
        if odds_val:
            try:
                odds = float(str(odds_val).replace('$', '').strip())
                if 1.01 < odds < 200:
                    normalized['opening_odds'] = odds
                    break
            except (ValueError, TypeError):
                pass
    
    field_size = race_data.get('field_size', 0) or len(race_data.get('runners', []))
    normalized['field_size'] = field_size
    normalized['barrier_field_ratio'] = normalized['barrier'] / max(field_size, 1)
    
    return normalized


def normalize_race(race: Dict) -> Dict:
    """Normalize race data to consistent schema."""
    normalized = {
        'race_id': race.get('race_id') or race.get('id') or f"{race.get('track', '')}-R{race.get('raceNumber', 0)}",
        'track': normalize_track_name(race.get('track') or race.get('course')),
        'track_raw': race.get('track') or race.get('course') or '',
        'race_number': int(race.get('raceNumber') or race.get('race_number') or 0),
        'race_name': race.get('raceName') or race.get('race_name') or race.get('name') or '',
        'distance': normalize_distance(race.get('distance')),
        'going': normalize_going(race.get('going') or race.get('track_condition')),
        'race_class': race.get('raceClass') or race.get('class') or race.get('race_class') or '',
        'off_time': race.get('offTime') or race.get('off_time') or race.get('post_time') or '',
        'race_date': race.get('raceDate') or race.get('race_date') or race.get('date') or '',
        'prize_total': race.get('prizeTotal') or race.get('prize') or race.get('prize_money') or 0,
    }
    
    raw_runners = race.get('runners') or race.get('horses') or []
    normalized['field_size'] = len([r for r in raw_runners if not is_scratched(r)])
    
    normalized_runners = []
    for runner in raw_runners:
        norm_runner = normalize_runner(runner, normalized)
        if not norm_runner['is_scratched']:
            normalized_runners.append(norm_runner)
    
    normalized['runners'] = normalized_runners
    
    return normalized


def normalize_races(races: List[Dict]) -> List[Dict]:
    """Normalize a list of races."""
    return [normalize_race(race) for race in races]



def parse_form_string(form: str, max_runs: int = 6) -> Dict[str, Any]:
    """
    Parse form string into structured data.

    Input: '1231x4' (most recent first)
    Output: {'positions': [...], 'avg_position': N, 'form_score': N, ...}
    """
    if not form:
        return {
            'positions': [],
            'avg_position': 5.0,
            'best_position': 99,
            'wins_in_form': 0,
            'consistency': 0.5,
            'trend': 0,
            'form_score': 5.0
        }
    
    positions = []
    for char in str(form).strip()[:max_runs]:
        if char.isdigit() and char != '0':
            positions.append(int(char))
        elif char == '0' or char.lower() == 'x':
            positions.append(10)
        elif char.lower() in ['f', 'u', 'r']:
            positions.append(12)
    
    if not positions:
        return {
            'positions': [],
            'avg_position': 5.0,
            'best_position': 99,
            'wins_in_form': 0,
            'consistency': 0.5,
            'trend': 0,
            'form_score': 5.0
        }
    
    avg_pos = sum(positions) / len(positions)
    best_pos = min(positions)
    wins = sum(1 for p in positions if p == 1)
    
    if len(positions) >= 2:
        variance = sum((p - avg_pos) ** 2 for p in positions) / len(positions)
        consistency = max(0, 1 - variance / 25)
    else:
        consistency = 0.5
    
    trend = 0
    if len(positions) >= 4:
        mid = len(positions) // 2
        recent_avg = sum(positions[:mid]) / mid
        older_avg = sum(positions[mid:]) / (len(positions) - mid)
        trend = (older_avg - recent_avg)
    elif len(positions) >= 2:
        trend = positions[-1] - positions[0]
    
    weights = [1.0, 0.85, 0.70, 0.55, 0.40, 0.30][:len(positions)]
    weighted_sum = sum((10 - min(p, 10)) * w for p, w in zip(positions, weights))
    total_weight = sum(weights)
    form_score = weighted_sum / total_weight if total_weight > 0 else 5.0
    
    decay_weights = [1.0, 0.63, 0.40, 0.25, 0.16, 0.10][:len(positions)]
    decay_weighted_sum = sum((10 - min(p, 10)) * w for p, w in zip(positions, decay_weights))
    decay_total_weight = sum(decay_weights)
    decay_weighted_score = decay_weighted_sum / decay_total_weight if decay_total_weight > 0 else 5.0
    
    trajectory = 'stable'
    trajectory_score = 0.0
    if len(positions) >= 3:
        last3 = positions[:3]
        if last3[0] < last3[1] and last3[1] < last3[2]:
            trajectory = 'improving'
            trajectory_score = 0.15
        elif last3[0] > last3[1] and last3[1] > last3[2]:
            trajectory = 'declining'
            trajectory_score = -0.10
    
    return {
        'positions': positions,
        'avg_position': round(avg_pos, 2),
        'best_position': best_pos,
        'wins_in_form': wins,
        'consistency': round(consistency, 2),
        'trend': round(trend, 2),
        'form_score': round(form_score, 2),
        'decay_weighted_score': round(decay_weighted_score, 2),
        'trajectory': trajectory,
        'trajectory_score': trajectory_score
    }



def extract_stats_rate(stats_obj: Any, stat_name: str = None) -> Dict[str, float]:
    """
    Extract win/place rate from a stats object.

    Returns: {'total': N, 'win_rate': %, 'place_rate': %}
    """
    _ = stat_name
    if not stats_obj or isinstance(stats_obj, str):
        return {'total': 0, 'win_rate': 0.0, 'place_rate': 0.0}
    
    total = int(stats_obj.get('total', 0) or 0)
    first = int(stats_obj.get('first', 0) or 0)
    second = int(stats_obj.get('second', 0) or 0)
    third = int(stats_obj.get('third', 0) or 0)
    
    if total == 0:
        return {'total': 0, 'win_rate': 0.0, 'place_rate': 0.0}
    
    # Bayesian smoothing: add pseudo-counts (20% prior)
    smoothed_total = total + 5
    win_rate = (first + 1) / smoothed_total * 100
    place_rate = (first + second + third + 1) / smoothed_total * 100
    
    return {
        'total': total,
        'win_rate': round(win_rate, 1),
        'place_rate': round(place_rate, 1)
    }


def extract_going_suitability(stats: Dict, going_condition: str) -> Dict[str, float]:
    """Extract suitability for current going condition."""
    going_stats_map = {
        'firm': 'ground_firm_stats',
        'good': 'ground_good_stats',
        'soft': 'ground_soft_stats',
        'heavy': 'ground_heavy_stats',
        'synthetic': 'ground_aw_stats'
    }
    
    going_lower = going_condition.lower()
    stats_field = None
    for key, field in going_stats_map.items():
        if key in going_lower:
            stats_field = field
            break
    
    if not stats_field:
        stats_field = 'ground_good_stats'
    
    ground_stats = stats.get(stats_field, {})
    return extract_stats_rate(ground_stats, stats_field)


def extract_distance_suitability(stats: Dict, distance_m: int) -> Dict[str, float]:
    """Extract suitability for current distance."""
    distance_stats = stats.get('distance_stats', {})
    return extract_stats_rate(distance_stats, 'distance')


def extract_course_suitability(stats: Dict) -> Dict[str, float]:
    """Extract course-specific stats."""
    course_stats = stats.get('course_stats', {})
    return extract_stats_rate(course_stats, 'course')


def extract_course_distance_suitability(stats: Dict) -> Dict[str, float]:
    """Extract course+distance specific stats."""
    cd_stats = stats.get('course_distance_stats', {})
    return extract_stats_rate(cd_stats, 'course_distance')
