"""
Advanced feature extraction for horse racing ML: form analysis, collateral form,
distance progression, track condition weighting, and class analysis.
"""

import re
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
import math

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

def get_db_connection():
    """Get database connection using environment variable."""
    if not HAS_PSYCOPG2:
        return None
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        return None
    try:
        return psycopg2.connect(db_url)
    except Exception:
        return None



CLASS_HIERARCHY = {
    'G1': 100, 'GROUP 1': 100, 'GROUP1': 100,
    'G2': 95, 'GROUP 2': 95, 'GROUP2': 95,
    'G3': 90, 'GROUP 3': 90, 'GROUP3': 90,
    'LISTED': 85, 'LR': 85,
    'OPEN': 80, 'OPEN HCP': 80,
    'BM90': 75, 'BM85': 72, 'BM80': 70, 'BM78': 68,
    'BM75': 66, 'BM72': 64, 'BM70': 62, 'BM68': 60,
    'BM65': 58, 'BM64': 57, 'BM62': 56, 'BM60': 54,
    'BM58': 52, 'BM55': 50, 'BM54': 49, 'BM52': 48,
    'BM50': 46, 'BM48': 44, 'BM45': 42,
    'CL6': 70, 'CLASS 6': 70, 'CL5': 65, 'CLASS 5': 65,
    'CL4': 60, 'CLASS 4': 60, 'CL3': 55, 'CLASS 3': 55,
    'CL2': 50, 'CLASS 2': 50, 'CL1': 45, 'CLASS 1': 45,
    '0-58': 40, '0-54': 38, '0-52': 36, '0-50': 34,
    'MDN': 30, 'MDN-SW': 30, 'MAIDEN': 30,
    'SUPER MDN': 35, 'SUPER MDN-SW': 35,
    '2Y MDN': 28, '3Y MDN': 29, '3Y C&G MDN': 29, '3YO MDN': 29,
}

def parse_class_level(race_class: str, prize_total: Optional[float] = None) -> int:
    """
    Parse race class into a numerical level (higher = better class).
    Uses class string first, falls back to prize money.
    """
    if not race_class:
        if prize_total:
            prize = float(prize_total) if isinstance(prize_total, str) else prize_total
            if prize >= 500000: return 95
            if prize >= 200000: return 85
            if prize >= 100000: return 70
            if prize >= 75000: return 60
            if prize >= 50000: return 50
            if prize >= 35000: return 40
            return 30
        return 30
    
    race_class_upper = race_class.upper().strip()
    
    if race_class_upper in CLASS_HIERARCHY:
        return CLASS_HIERARCHY[race_class_upper]
    
    bm_match = re.search(r'BM\s*(\d+)', race_class_upper)
    if bm_match:
        bm_value = int(bm_match.group(1))
        return min(75, 40 + bm_value // 2)
    
    class_match = re.search(r'CL(?:ASS)?\s*(\d+)', race_class_upper)
    if class_match:
        class_num = int(class_match.group(1))
        return max(30, 75 - (class_num * 5))
    
    if 'GROUP' in race_class_upper or 'GRP' in race_class_upper:
        return 90
    if 'LISTED' in race_class_upper:
        return 85
    if 'OPEN' in race_class_upper:
        return 80
    if 'MDN' in race_class_upper or 'MAIDEN' in race_class_upper:
        if 'SUPER' in race_class_upper:
            return 35
        return 30
    
    if prize_total:
        prize = float(prize_total) if isinstance(prize_total, str) else prize_total
        if prize >= 100000: return 70
        if prize >= 50000: return 50
        return 40
    
    return 40


def calculate_class_change(current_class: int, previous_class: int) -> Dict[str, Any]:
    """
    Analyse class change and return adjustment factors.

    Returns dict with 'direction' (-1/0/1), 'magnitude', 'adjustment' (probability multiplier).
    """
    diff = current_class - previous_class
    
    if abs(diff) <= 3:
        return {'direction': 0, 'magnitude': 0, 'adjustment': 1.0, 'description': 'same_class'}
    
    if diff < 0:
        # Dropping in class - positive for horse
        magnitude = abs(diff)
        if magnitude >= 15:
            adjustment = 1.20  # Major class drop - big boost
        elif magnitude >= 10:
            adjustment = 1.12
        else:
            adjustment = 1.06
        return {'direction': -1, 'magnitude': magnitude, 'adjustment': adjustment, 'description': 'class_drop'}
    else:
        # Rising in class - negative for horse
        magnitude = diff
        if magnitude >= 15:
            adjustment = 0.80  # Major class rise - significant penalty
        elif magnitude >= 10:
            adjustment = 0.88
        else:
            adjustment = 0.94
        return {'direction': 1, 'magnitude': magnitude, 'adjustment': adjustment, 'description': 'class_rise'}



def parse_form_string(form: str) -> List[Optional[int]]:
    """
    Parse form string into list of positions.
    '1' = 1st, '2' = 2nd, etc. 'x' or '-' = DNF/scratched. '0' = outside top 9.
    """
    if not form:
        return []
    
    positions = []
    for char in form:
        if char.isdigit():
            positions.append(int(char) if char != '0' else 10)
        elif char.lower() in ['x', '-', 'f', 'u', 'p']:  # Fall, unseated, pulled up
            positions.append(None)
    
    return positions


def calculate_weighted_form_score(form: str, weights: List[float] = None) -> float:
    """
    Calculate weighted form score with recency bias.
    More recent runs weighted more heavily. Returns score 0-100 (higher = better form).
    """
    positions = parse_form_string(form)
    if not positions:
        return 50.0
    
    if weights is None:
        weights = [0.35, 0.25, 0.20, 0.12, 0.08]
    
    total_weight = 0.0
    weighted_score = 0.0
    
    for i, pos in enumerate(positions[:len(weights)]):
        if pos is None:
            continue
        
        weight = weights[i] if i < len(weights) else 0.05
        
        if pos == 1:
            score = 100
        elif pos == 2:
            score = 85
        elif pos == 3:
            score = 70
        elif pos <= 5:
            score = 55
        elif pos <= 7:
            score = 40
        else:
            score = 25
        
        weighted_score += score * weight
        total_weight += weight
    
    if total_weight == 0:
        return 50.0
    
    return weighted_score / total_weight


def analyze_form_patterns(form: str) -> Dict[str, Any]:
    """Analyse form for patterns indicating improvement or decline."""
    positions = parse_form_string(form)
    valid_positions = [p for p in positions if p is not None]
    
    if len(valid_positions) < 2:
        return {
            'trend': 'unknown',
            'consistency': 0.5,
            'peak_form': False,
            'improving': False,
            'declining': False,
            'adjustment': 1.0
        }
    
    recent_3 = valid_positions[:3]
    if len(recent_3) >= 2:
        improving = all(recent_3[i] <= recent_3[i+1] for i in range(len(recent_3)-1))
        declining = all(recent_3[i] >= recent_3[i+1] for i in range(len(recent_3)-1))
    else:
        improving = declining = False
    
    if len(valid_positions) >= 3:
        mean_pos = sum(valid_positions) / len(valid_positions)
        variance = sum((p - mean_pos) ** 2 for p in valid_positions) / len(valid_positions)
        consistency = 1.0 / (1.0 + math.sqrt(variance) / 3.0)
    else:
        consistency = 0.5
    
    peak_form = valid_positions[0] <= 3 if valid_positions else False
    
    adjustment = 1.0
    if improving and valid_positions[0] <= 4:
        adjustment = 1.10
    elif declining and valid_positions[0] >= 6:
        adjustment = 0.90
    elif peak_form and consistency > 0.7:
        adjustment = 1.08
    
    return {
        'trend': 'improving' if improving else ('declining' if declining else 'stable'),
        'consistency': consistency,
        'peak_form': peak_form,
        'improving': improving,
        'declining': declining,
        'adjustment': adjustment,
        'recent_positions': valid_positions[:5]
    }



def parse_distance(distance_str: str) -> int:
    """Convert distance string to meters."""
    if not distance_str:
        return 0
    
    distance_str = str(distance_str).lower().strip()
    
    match = re.search(r'(\d+)\s*m', distance_str)
    if match:
        return int(match.group(1))
    
    match = re.search(r'^(\d+)$', distance_str)
    if match:
        return int(match.group(1))
    
    match = re.search(r'(\d+)\s*f', distance_str)
    if match:
        return int(float(match.group(1)) * 201)  # 1 furlong = ~201m
    
    if 'mile' in distance_str:
        match = re.search(r'(\d+\.?\d*)', distance_str)
        if match:
            return int(float(match.group(1)) * 1609)
    
    return 0


def categorize_distance(distance_m: int) -> str:
    """Categorize distance into sprint/middle/staying."""
    if distance_m <= 1200:
        return 'sprint'
    elif distance_m <= 1600:
        return 'mile'
    elif distance_m <= 2000:
        return 'middle'
    else:
        return 'staying'


def analyze_distance_suitability(
    race_distance: int,
    distance_stats: Dict,
    form_distances: List[int] = None,
    min_winning_distance: Optional[int] = None,
    max_winning_distance: Optional[int] = None
) -> Dict[str, Any]:
    """Analyse horse's suitability for the race distance."""
    result = {
        'distance_category': categorize_distance(race_distance),
        'proven_at_distance': False,
        'stepping_up': False,
        'stepping_down': False,
        'distance_adjustment': 1.0,
        'strike_rate_at_distance': None
    }
    
    if distance_stats and distance_stats.get('total', 0) > 0:
        total = distance_stats['total']
        firsts = distance_stats.get('first', 0)
        places = firsts + distance_stats.get('second', 0) + distance_stats.get('third', 0)
        
        result['strike_rate_at_distance'] = (firsts / total) * 100 if total > 0 else 0
        result['place_rate_at_distance'] = (places / total) * 100 if total > 0 else 0
        
        if firsts > 0:
            result['proven_at_distance'] = True
            result['distance_adjustment'] = 1.10
        elif places > 0:
            result['distance_adjustment'] = 1.05
    
    if form_distances and len(form_distances) >= 2:
        recent_avg = sum(form_distances[:3]) / min(3, len(form_distances))
        
        step_diff = race_distance - recent_avg
        step_percent = step_diff / recent_avg if recent_avg > 0 else 0
        
        if step_percent > 0.15:  # Stepping up more than 15%
            result['stepping_up'] = True
            result['step_change'] = step_percent
            # Penalize big step-ups unless proven at distance
            if not result['proven_at_distance']:
                result['distance_adjustment'] *= 0.92
        elif step_percent < -0.15:  # Stepping down
            result['stepping_down'] = True
            result['step_change'] = step_percent
            # Slight penalty for dropping back significantly
            result['distance_adjustment'] *= 0.96
    
    if min_winning_distance and max_winning_distance:
        if min_winning_distance <= race_distance <= max_winning_distance:
            result['in_winning_range'] = True
            result['distance_adjustment'] *= 1.08
        else:
            result['in_winning_range'] = False
    
    return result



GOING_LEVELS = {
    'firm': 1, 'good to firm': 2, 'good': 3, 'good to soft': 4,
    'soft': 5, 'heavy': 6, 'heavy 8': 7, 'heavy 9': 8, 'heavy 10': 9,
    'synthetic': 3, 'all weather': 3, 'aw': 3
}

def parse_going(going_str: str) -> Tuple[str, int]:
    """Parse going string to category and wetness level."""
    if not going_str:
        return 'good', 3
    
    going_lower = going_str.lower().strip()
    
    if going_lower in GOING_LEVELS:
        return going_lower, GOING_LEVELS[going_lower]
    
    if 'heavy' in going_lower:
        match = re.search(r'heavy\s*(\d+)', going_lower)
        if match:
            level = int(match.group(1))
            return f'heavy {level}', 6 + min(3, level - 7)
        return 'heavy', 6
    
    if 'soft' in going_lower:
        return 'soft', 5
    
    if 'firm' in going_lower:
        if 'good' in going_lower:
            return 'good to firm', 2
        return 'firm', 1
    
    if 'good' in going_lower:
        if 'soft' in going_lower:
            return 'good to soft', 4
        return 'good', 3
    
    return 'good', 3


def analyze_going_suitability(
    race_going: str,
    ground_stats: Dict[str, Optional[Dict]],
    form_going_results: List[Tuple[str, int]] = None
) -> Dict[str, Any]:
    """Analyse horse's suitability for today's track conditions."""
    race_going_cat, race_wetness = parse_going(race_going)
    
    result = {
        'race_going': race_going_cat,
        'wetness_level': race_wetness,
        'is_wet_track': race_wetness >= 5,
        'proven_on_going': False,
        'going_specialist': None,
        'going_adjustment': 1.0
    }
    
    wet_wins = 0
    wet_runs = 0
    dry_wins = 0
    dry_runs = 0
    
    if ground_stats:
        for going_type, stats in ground_stats.items():
            if stats is None or not isinstance(stats, dict):
                continue
            
            runs = stats.get('total', 0)
            wins = stats.get('first', 0)
            
            if 'soft' in going_type or 'heavy' in going_type:
                wet_runs += runs
                wet_wins += wins
            elif 'good' in going_type or 'firm' in going_type:
                dry_runs += runs
                dry_wins += wins
    
    if wet_runs > 0:
        wet_strike_rate = wet_wins / wet_runs
        if wet_strike_rate >= 0.25:
            result['wet_track_specialist'] = True
            if result['is_wet_track']:
                result['going_adjustment'] *= 1.15  # Big boost on wet
    
    if dry_runs > 0:
        dry_strike_rate = dry_wins / dry_runs
        if dry_strike_rate >= 0.25:
            result['dry_track_specialist'] = True
            if not result['is_wet_track']:
                result['going_adjustment'] *= 1.10
    
    # Penalty for wrong conditions
    if result.get('wet_track_specialist') and not result['is_wet_track']:
        result['going_adjustment'] *= 0.95
    if result.get('dry_track_specialist') and result['is_wet_track']:
        result['going_adjustment'] *= 0.90
    
    if form_going_results:
        matching_going = [pos for going, pos in form_going_results 
                         if abs(parse_going(going)[1] - race_wetness) <= 1]
        if matching_going:
            avg_pos = sum(matching_going) / len(matching_going)
            if avg_pos <= 3:
                result['proven_on_going'] = True
                result['going_adjustment'] *= 1.08
    
    return result



class CollateralFormAnalyzer:
    """Analyses collateral form by tracking race results and boosting horses that have run close to subsequent winners."""
    
    def __init__(self):
        self.race_results_cache = {}
        self.form_franking_cache = {}
    
    def load_historical_results(self, days_back: int = 60) -> bool:
        """Load recent race results from database."""
        conn = get_db_connection()
        if not conn:
            return False
        
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("""
                SELECT 
                    race_id, track, race_date, race_number, race_class,
                    horse_name, actual_position, starting_price
                FROM training_data
                WHERE actual_position IS NOT NULL
                AND race_date >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY race_date, track, race_number, actual_position
            """, (days_back,))
            
            results = cursor.fetchall()
            
            for row in results:
                race_key = f"{row['track']}_{row['race_date']}_{row['race_number']}"
                if race_key not in self.race_results_cache:
                    self.race_results_cache[race_key] = {
                        'track': row['track'],
                        'date': row['race_date'],
                        'race_number': row['race_number'],
                        'class': row['race_class'],
                        'runners': []
                    }
                
                self.race_results_cache[race_key]['runners'].append({
                    'horse': row['horse_name'],
                    'position': row['actual_position'],
                    'sp': row['starting_price']
                })
            
            cursor.close()
            conn.close()
            return True
            
        except Exception as e:
            if conn:
                conn.close()
            return False
    
    def find_subsequent_winners(self, horse_name: str) -> List[Dict]:
        """Find races where this horse ran, then check if horses it beat went on to win at higher classes."""
        subsequent_winners = []
        
        for race_key, race_data in self.race_results_cache.items():
            horse_result = None
            other_runners = []
            
            for runner in race_data['runners']:
                if runner['horse'].lower() == horse_name.lower():
                    horse_result = runner
                else:
                    other_runners.append(runner)
            
            if not horse_result:
                continue
            
            for other in other_runners:
                if other['position'] and horse_result['position']:
                    margin = other['position'] - horse_result['position']
                    
                    if 0 < margin <= 3:
                        won_later = self._check_subsequent_win(
                            other['horse'],
                            race_data['date'],
                            race_data['class']
                        )
                        
                        if won_later:
                            subsequent_winners.append({
                                'beaten_horse': other['horse'],
                                'margin': margin,
                                'original_race': race_key,
                                'subsequent_win': won_later
                            })
        
        return subsequent_winners
    
    def _check_subsequent_win(self, horse_name: str, after_date: str, 
                              original_class: str) -> Optional[Dict]:
        """Check if horse won after the given date."""
        _ = original_class
        for race_key, race_data in self.race_results_cache.items():
            if race_data['date'] <= after_date:
                continue
            
            for runner in race_data['runners']:
                if runner['horse'].lower() == horse_name.lower():
                    if runner['position'] == 1:
                        return {
                            'track': race_data['track'],
                            'date': race_data['date'],
                            'class': race_data['class']
                        }
        
        return None
    
    def calculate_collateral_boost(self, horse_name: str) -> Dict[str, Any]:
        """
        Calculate form franking boost for a horse.

        If horses that this horse beat went on to win, it franks the form and boosts this horse's rating.
        """
        if not self.race_results_cache:
            self.load_historical_results()
        
        result = {
            'franked_form': False,
            'franking_boost': 1.0,
            'evidence': []
        }
        
        for race_key, race_data in self.race_results_cache.items():
            horse_result = None
            beaten_runners = []
            
            for runner in race_data['runners']:
                if runner['horse'].lower() == horse_name.lower():
                    horse_result = runner
                else:
                    beaten_runners.append(runner)
            
            if not horse_result or not horse_result['position']:
                continue
            
            for beaten in beaten_runners:
                if not beaten['position']:
                    continue
                if beaten['position'] > horse_result['position']:
                    later_win = self._check_subsequent_win(
                        beaten['horse'],
                        race_data['date'],
                        race_data['class']
                    )
                    
                    if later_win:
                        margin = beaten['position'] - horse_result['position']
                        boost = 1.05 + (0.02 * (4 - min(3, margin)))  # Closer = more boost
                        
                        result['franked_form'] = True
                        result['evidence'].append({
                            'beaten': beaten['horse'],
                            'by_lengths': margin,
                            'later_won_at': later_win
                        })
                        result['franking_boost'] = max(result['franking_boost'], boost)
        
        return result



class HotStreakAnalyzer:
    """Analyze recent jockey and trainer form."""
    
    def __init__(self):
        self.jockey_stats = {}
        self.trainer_stats = {}
        self.combo_stats = {}
    
    def load_recent_stats(self, days_back: int = 14) -> bool:
        """Load recent statistics from database."""
        conn = get_db_connection()
        if not conn:
            return False
        
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("""
                SELECT 
                    jockey, trainer, won, placed
                FROM training_data
                WHERE race_date >= CURRENT_DATE - INTERVAL '%s days'
                AND jockey IS NOT NULL
            """, (days_back,))
            
            for row in cursor.fetchall():
                jockey = row['jockey']
                trainer = row['trainer']
                won = row['won'] == 1
                placed = row['placed'] == 1
                
                if jockey:
                    if jockey not in self.jockey_stats:
                        self.jockey_stats[jockey] = {'rides': 0, 'wins': 0, 'places': 0}
                    self.jockey_stats[jockey]['rides'] += 1
                    if won:
                        self.jockey_stats[jockey]['wins'] += 1
                    if placed:
                        self.jockey_stats[jockey]['places'] += 1
                
                if trainer:
                    if trainer not in self.trainer_stats:
                        self.trainer_stats[trainer] = {'runs': 0, 'wins': 0, 'places': 0}
                    self.trainer_stats[trainer]['runs'] += 1
                    if won:
                        self.trainer_stats[trainer]['wins'] += 1
                    if placed:
                        self.trainer_stats[trainer]['places'] += 1
                
                if jockey and trainer:
                    combo = f"{jockey}|{trainer}"
                    if combo not in self.combo_stats:
                        self.combo_stats[combo] = {'runs': 0, 'wins': 0, 'places': 0}
                    self.combo_stats[combo]['runs'] += 1
                    if won:
                        self.combo_stats[combo]['wins'] += 1
                    if placed:
                        self.combo_stats[combo]['places'] += 1
            
            cursor.close()
            conn.close()
            return True
            
        except Exception:
            if conn:
                conn.close()
            return False
    
    def get_hot_streak_adjustment(self, jockey: str, trainer: str) -> Dict[str, Any]:
        """Calculate hot streak adjustment for jockey/trainer."""
        if not self.jockey_stats:
            self.load_recent_stats()
        
        result = {
            'jockey_hot': False,
            'trainer_hot': False,
            'combo_hot': False,
            'adjustment': 1.0,
            'jockey_strike_rate': None,
            'trainer_strike_rate': None
        }
        
        if jockey and jockey in self.jockey_stats:
            stats = self.jockey_stats[jockey]
            if stats['rides'] >= 5:
                sr = stats['wins'] / stats['rides']
                result['jockey_strike_rate'] = sr * 100
                if sr >= 0.20:
                    result['jockey_hot'] = True
                    result['adjustment'] *= 1.08
                elif sr >= 0.15:
                    result['adjustment'] *= 1.04
        
        if trainer and trainer in self.trainer_stats:
            stats = self.trainer_stats[trainer]
            if stats['runs'] >= 5:
                sr = stats['wins'] / stats['runs']
                result['trainer_strike_rate'] = sr * 100
                if sr >= 0.20:
                    result['trainer_hot'] = True
                    result['adjustment'] *= 1.06
                elif sr >= 0.15:
                    result['adjustment'] *= 1.03
        
        if jockey and trainer:
            combo = f"{jockey}|{trainer}"
            if combo in self.combo_stats:
                stats = self.combo_stats[combo]
                if stats['runs'] >= 3:
                    sr = stats['wins'] / stats['runs']
                    if sr >= 0.30:
                        result['combo_hot'] = True
                        result['adjustment'] *= 1.10
        
        return result



_collateral_analyzer = None
_hot_streak_analyzer = None

def get_collateral_analyzer() -> CollateralFormAnalyzer:
    global _collateral_analyzer
    if _collateral_analyzer is None:
        _collateral_analyzer = CollateralFormAnalyzer()
    return _collateral_analyzer

def get_hot_streak_analyzer() -> HotStreakAnalyzer:
    global _hot_streak_analyzer
    if _hot_streak_analyzer is None:
        _hot_streak_analyzer = HotStreakAnalyzer()
    return _hot_streak_analyzer


def extract_advanced_features(
    runner: Dict,
    race: Dict,
    historical_form: Optional[List[Dict]] = None
) -> Dict[str, Any]:
    """
    Extract all advanced features for a runner.

    Args:
        runner: Runner data from API
        race: Race data from API
        historical_form: Optional list of past race results

    Returns:
        Dict containing all advanced features and adjustments
    """
    features = {
        'adjustments': {},
        'features': {},
        'combined_adjustment': 1.0
    }
    
    form = runner.get('form', '')
    form_analysis = analyze_form_patterns(form)
    features['features']['form'] = form_analysis
    features['features']['weighted_form_score'] = calculate_weighted_form_score(form)
    features['adjustments']['form'] = form_analysis['adjustment']
    
    race_class = race.get('class', '')
    prize_total = race.get('prize_total')
    current_class_level = parse_class_level(race_class, prize_total)
    features['features']['class_level'] = current_class_level
    
    if historical_form and len(historical_form) > 0:
        prev_class = historical_form[0].get('race_class', '')
        prev_prize = historical_form[0].get('prize_total')
        prev_class_level = parse_class_level(prev_class, prev_prize)
        class_change = calculate_class_change(current_class_level, prev_class_level)
        features['features']['class_change'] = class_change
        features['adjustments']['class'] = class_change['adjustment']
    else:
        features['adjustments']['class'] = 1.0
    
    race_distance = parse_distance(race.get('distance', ''))
    distance_stats = runner.get('stats', {}).get('distance_stats', {})
    
    form_distances = None
    if historical_form:
        form_distances = [parse_distance(h.get('distance', '')) 
                         for h in historical_form if h.get('distance')]
    
    min_win_dist = runner.get('stats', {}).get('min_winning_distance')
    max_win_dist = runner.get('stats', {}).get('max_winning_distance')
    
    distance_analysis = analyze_distance_suitability(
        race_distance, distance_stats, form_distances,
        min_win_dist, max_win_dist
    )
    features['features']['distance'] = distance_analysis
    features['adjustments']['distance'] = distance_analysis['distance_adjustment']
    
    race_going = race.get('going', 'Good')
    ground_stats = {
        'ground_soft_stats': runner.get('stats', {}).get('ground_soft_stats'),
        'ground_heavy_stats': runner.get('stats', {}).get('ground_heavy_stats'),
        'ground_good_stats': runner.get('stats', {}).get('ground_good_stats'),
        'ground_firm_stats': runner.get('stats', {}).get('ground_firm_stats'),
    }
    
    going_analysis = analyze_going_suitability(race_going, ground_stats)
    features['features']['going'] = going_analysis
    features['adjustments']['going'] = going_analysis['going_adjustment']
    
    try:
        horse_name = runner.get('horse', '')
        collateral_analyzer = get_collateral_analyzer()
        collateral_result = collateral_analyzer.calculate_collateral_boost(horse_name)
        features['features']['collateral_form'] = collateral_result
        features['adjustments']['collateral'] = collateral_result['franking_boost']
    except Exception:
        features['adjustments']['collateral'] = 1.0
    
    try:
        jockey = runner.get('jockey', '')
        trainer = runner.get('trainer', '')
        hot_streak_analyzer = get_hot_streak_analyzer()
        hot_streak = hot_streak_analyzer.get_hot_streak_adjustment(jockey, trainer)
        features['features']['hot_streak'] = hot_streak
        features['adjustments']['hot_streak'] = hot_streak['adjustment']
    except Exception:
        features['adjustments']['hot_streak'] = 1.0
    
    combined = 1.0
    for adj_name, adj_value in features['adjustments'].items():
        combined *= adj_value
    
    combined = max(0.70, min(1.50, combined))
    features['combined_adjustment'] = combined
    
    return features


__all__ = [
    'extract_advanced_features',
    'parse_class_level',
    'calculate_weighted_form_score',
    'analyze_form_patterns',
    'parse_distance',
    'categorize_distance',
    'analyze_distance_suitability',
    'analyze_going_suitability',
    'CollateralFormAnalyzer',
    'HotStreakAnalyzer',
]
