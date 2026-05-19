#!/usr/bin/env python3
"""Backtest-ready feature extraction with NO data leakage."""

import json
import re
import argparse
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Optional, Tuple


FORM_WINDOW = 5

REST_BINS = [0, 7, 14, 21, 28, 42, 9999]
REST_LABELS = ['0-7', '8-14', '15-21', '22-28', '29-42', '43+']
REST_SWEET_SPOT = (8, 28)

DISTANCE_BANDS = {
    'sprint': (0, 1100),
    'short': (1101, 1400),
    'mile': (1401, 1700),
    'middle': (1701, 2100),
    'staying': (2101, 9999),
}

BARRIER_GROUPS = {
    'inside': (1, 4),
    'middle': (5, 8),
    'wide': (9, 99),
}

TROUBLE_KEYWORDS = [
    'held up', 'blocked', 'checked', 'hampered', 'bumped', 
    'steadied', 'crowded', 'tightened', 'squeezed', 'impeded',
    'buffeted', 'shuffled', 'no room', 'racing room'
]

WIDE_KEYWORDS = [
    'three wide', 'four wide', 'five wide', 'without cover',
    'three-wide', 'four-wide', 'widest', 'wide throughout',
    'covered ground', 'wide run', 'raced wide'
]

START_KEYWORDS = [
    'slow', 'awkward', 'missed', 'dwelt', 'slowly away',
    'tardy', 'began awkwardly', 'missed the start', 'slow to begin'
]

STRONG_FINISH_KEYWORDS = [
    'ran on', 'hit the line', 'kept coming', 'worked home',
    'finished strongly', 'strong finish', 'closing', 'rattled home',
    'stormed home', 'powered home', 'flew late', 'flashed late'
]

BAYES_ALPHA = 1  # Prior wins
BAYES_BETA = 5   # Prior starts

ODDS_BINS = [0, 2, 3, 4, 5, 7, 10, 15, 9999]
ODDS_LABELS = ['<=2', '2-3', '3-4', '4-5', '5-7', '7-10', '10-15', '>15']



def safe_int(val, default=0):
    """Safely convert to int."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def safe_float(val, default=0.0):
    """Safely convert to float."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def parse_date(date_str):
    """Parse date string to datetime."""
    if not date_str:
        return None
    for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d']:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def get_distance_band(distance):
    """Get distance band label."""
    distance = safe_int(distance)
    for band, (lo, hi) in DISTANCE_BANDS.items():
        if lo <= distance <= hi:
            return band
    return 'unknown'


def get_barrier_group(barrier):
    """Get barrier group label."""
    barrier = safe_int(barrier)
    if barrier == 0:
        return 'unknown'
    for group, (lo, hi) in BARRIER_GROUPS.items():
        if lo <= barrier <= hi:
            return group
    return 'wide'


def bayesian_rate(wins, starts, alpha=BAYES_ALPHA, beta=BAYES_BETA):
    """Calculate Bayesian-smoothed win rate."""
    return (wins + alpha) / (starts + beta)



def load_json_files(paths: List[str]) -> List[Dict]:
    """Load race data from JSON files."""
    races = []
    files_loaded = 0
    
    for path_str in paths:
        path = Path(path_str)
        
        if not path.exists():
            print(f"  Warning: Path not found: {path_str}")
            continue
        
        if path.is_file():
            files = [path]
        else:
            files = list(path.glob("**/*.json"))
        
        for filepath in files:
            if filepath.name.startswith('._'):
                continue
            if 'supplementary' in filepath.name.lower():
                continue
            
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                
                if isinstance(data, list):
                    for item in data:
                        if 'runners' in item:
                            races.append(item)
                        elif 'races' in item:
                            for race in item.get('races', []):
                                race['course'] = race.get('course') or item.get('course')
                                race['date'] = race.get('date') or item.get('date')
                                races.append(race)
                elif isinstance(data, dict):
                    if 'runners' in data:
                        races.append(data)
                    elif 'races' in data:
                        for race in data.get('races', []):
                            race['course'] = race.get('course') or data.get('course')
                            race['date'] = race.get('date') or data.get('date')
                            races.append(race)
                
                files_loaded += 1
                
            except Exception as e:
                print(f"  Warning: Error loading {filepath.name}: {e}")
                continue
    
    print(f"  Loaded {files_loaded} files, {len(races)} races")
    return races


def is_jumpout(race: Dict) -> bool:
    """Check if race is a jumpout/trial."""
    if race.get('is_jump_out') == True:
        return True
    if race.get('is_trial') == True:
        return True
    
    race_class = str(race.get('class', '') or '').upper()
    if any(x in race_class for x in ['BT', 'TRIAL', 'TRL', 'JUMP']):
        return True
    
    race_name = str(race.get('race_name', '') or '').upper()
    if any(x in race_name for x in ['TRIAL', 'JUMP OUT', 'JUMPOUT']):
        return True
    
    return False


def is_scratched(runner: Dict) -> bool:
    """Check if runner is scratched."""
    if runner.get('scratched') == True:
        return True
    
    # Position 109+ often indicates scratch
    position = safe_int(runner.get('position'))
    if position >= 100:
        return True
    
    return False



def parse_form_string(form_str: str, window: int = FORM_WINDOW) -> Dict:
    """Parse form string to extract recent performance."""
    features = {
        'recent_wins': 0,
        'recent_places': 0,
        'recent_avg_pos': None,
        'recent_best_pos': None,
        'recent_worst_pos': None,
        'starts_in_form_window': 0,
        'form_trend': None,
    }
    
    if not form_str:
        return features
    
    positions = []
    for char in str(form_str):
        if char.isdigit():
            pos = int(char)
            if pos == 0:
                pos = 10  # 0 often means 10th or worse
            positions.append(pos)
        elif char.lower() == 'x':
            positions.append(10)  # Fall/DNF
    
    if not positions:
        return features
    
    recent = positions[-window:]
    features['starts_in_form_window'] = len(recent)
    
    if recent:
        features['recent_wins'] = sum(1 for p in recent if p == 1)
        features['recent_places'] = sum(1 for p in recent if p <= 3)
        features['recent_avg_pos'] = round(sum(recent) / len(recent), 2)
        features['recent_best_pos'] = min(recent)
        features['recent_worst_pos'] = max(recent)
        
        if len(recent) >= 4:
            first_half = recent[:len(recent)//2]
            second_half = recent[len(recent)//2:]
            avg_first = sum(first_half) / len(first_half)
            avg_second = sum(second_half) / len(second_half)
            
            if avg_second < avg_first - 0.5:
                features['form_trend'] = 'improving'
            elif avg_second > avg_first + 0.5:
                features['form_trend'] = 'declining'
            else:
                features['form_trend'] = 'stable'
    
    return features



def calculate_rest_days(race_date: str, last_raced: str) -> Dict:
    """Calculate days since last race."""
    features = {
        'days_since_last': None,
        'rest_bin': None,
        'rest_sweet_spot': False,
        'first_up': False,
    }
    
    race_dt = parse_date(race_date)
    last_dt = parse_date(last_raced)
    
    if race_dt and last_dt:
        delta = (race_dt - last_dt).days
        
        # Handle negative or zero (same day = likely data issue)
        if delta <= 0:
            return features
        
        features['days_since_last'] = delta
        
        for i, (lo, hi) in enumerate(zip(REST_BINS[:-1], REST_BINS[1:])):
            if lo < delta <= hi:
                features['rest_bin'] = REST_LABELS[i]
                break
        
        if REST_SWEET_SPOT[0] <= delta <= REST_SWEET_SPOT[1]:
            features['rest_sweet_spot'] = True
        
        if delta > 60:
            features['first_up'] = True
    
    return features



def parse_comment_signals(comment: str) -> Dict:
    """Extract keyword signals from race comment."""
    features = {
        'trouble_flag': False,
        'wide_flag': False,
        'start_flag': False,
        'strong_finish_flag': False,
        'adversity_score': 0,
        'finish_score': 0,
        'forgive_flag': False,
    }
    
    if not comment:
        return features
    
    comment_lower = comment.lower()
    
    trouble_count = sum(1 for kw in TROUBLE_KEYWORDS if kw in comment_lower)
    wide_count = sum(1 for kw in WIDE_KEYWORDS if kw in comment_lower)
    start_count = sum(1 for kw in START_KEYWORDS if kw in comment_lower)
    finish_count = sum(1 for kw in STRONG_FINISH_KEYWORDS if kw in comment_lower)
    
    features['trouble_flag'] = trouble_count > 0
    features['wide_flag'] = wide_count > 0
    features['start_flag'] = start_count > 0
    features['strong_finish_flag'] = finish_count > 0
    
    features['adversity_score'] = trouble_count + wide_count + start_count
    features['finish_score'] = finish_count
    
    return features


def apply_forgive_flag(row: pd.Series) -> bool:
    """
    Apply forgive flag logic.
    Forgive = had adversity + finished strong + didn't place
    """
    if row.get('adversity_score', 0) >= 1:
        if row.get('finish_score', 0) >= 1:
            if row.get('position', 0) > 3:
                return True
    return False



def adjust_stats_to_pre_race(stats: Dict, position: int) -> Dict:
    """
    Adjust post-race stats to pre-race values.

    CRITICAL: The stats in the JSON include the current race.
    We must subtract this race to get true pre-race stats.
    """
    if not stats:
        return {}
    
    features = {}
    position = safe_int(position)
    
    stat_groups = [
        ('course', 'course_stats'),
        ('distance', 'distance_stats'),
        ('course_distance', 'course_distance_stats'),
    ]
    
    for prefix, key in stat_groups:
        group = stats.get(key, {})
        if not group:
            continue
        
        total_post = safe_int(group.get('total'))
        first_post = safe_int(group.get('first'))
        second_post = safe_int(group.get('second'))
        third_post = safe_int(group.get('third'))
        
        total_pre = max(0, total_post - 1)
        first_pre = max(0, first_post - (1 if position == 1 else 0))
        second_pre = max(0, second_post - (1 if position == 2 else 0))
        third_pre = max(0, third_post - (1 if position == 3 else 0))
        places_pre = first_pre + second_pre + third_pre
        
        features[f'{prefix}_starts_pre'] = total_pre
        features[f'{prefix}_wins_pre'] = first_pre
        features[f'{prefix}_places_pre'] = places_pre
        
        if total_pre > 0:
            features[f'{prefix}_win_rate'] = round(bayesian_rate(first_pre, total_pre), 4)
            features[f'{prefix}_place_rate'] = round(bayesian_rate(places_pre, total_pre), 4)
        else:
            features[f'{prefix}_win_rate'] = round(bayesian_rate(0, 0), 4)
            features[f'{prefix}_place_rate'] = round(bayesian_rate(0, 0), 4)
    
    return features



def extract_market_odds(runner: Dict) -> Dict:
    """Extract market odds from runner data."""
    features = {
        'sp': None,
        'best_win_odds': None,
        'avg_win_odds': None,
        'median_win_odds': None,
        'market_odds': None,
    }
    
    sp = runner.get('sp')
    if sp is not None:
        try:
            features['sp'] = float(sp)
        except (ValueError, TypeError):
            pass
    
    odds_list = runner.get('odds', [])
    if isinstance(odds_list, list) and odds_list:
        win_odds = []
        for o in odds_list:
            if isinstance(o, dict):
                wo = o.get('win_odds') or o.get('odds')
                if wo:
                    try:
                        win_odds.append(float(wo))
                    except (ValueError, TypeError):
                        pass
            elif isinstance(o, (int, float)):
                win_odds.append(float(o))
        
        if win_odds:
            features['best_win_odds'] = min(win_odds)
            features['avg_win_odds'] = round(sum(win_odds) / len(win_odds), 2)
            features['median_win_odds'] = round(sorted(win_odds)[len(win_odds)//2], 2)
    
    # Set primary market odds (prefer bookmaker odds over SP)
    if features['best_win_odds']:
        features['market_odds'] = features['best_win_odds']
    elif features['sp']:
        features['market_odds'] = features['sp']
    
    return features



class ParTimeCalculator:
    """Calculate and store par times for track/distance/class combinations."""
    
    def __init__(self):
        self.par_times = defaultdict(list)
        self.computed_pars = {}
    
    def add_race(self, track: str, distance: int, going: str, 
                 race_class: str, time_seconds: float):
        """Add a race time to the par time database."""
        if time_seconds <= 0:
            return
        
        key = (track.lower(), distance, going.lower() if going else 'unknown')
        self.par_times[key].append(time_seconds)
    
    def compute_pars(self):
        """Compute par times from collected data."""
        for key, times in self.par_times.items():
            if len(times) >= 3:  # Need minimum sample
                self.computed_pars[key] = round(sorted(times)[len(times)//2], 2)
    
    def get_par(self, track: str, distance: int, going: str) -> Optional[float]:
        """Get par time for a race."""
        key = (track.lower(), distance, going.lower() if going else 'unknown')
        
        if key in self.computed_pars:
            return self.computed_pars[key]
        
        key_no_going = (track.lower(), distance, 'unknown')
        if key_no_going in self.computed_pars:
            return self.computed_pars[key_no_going]
        
        return None


def parse_winning_time(race: Dict) -> Optional[float]:
    """Parse winning time to seconds."""
    time_str = race.get('winning_time')
    hundredths = safe_int(race.get('winning_time_hundredths'))
    
    if time_str:
        try:
            if ':' in str(time_str):
                parts = str(time_str).split(':')
                mins = int(parts[0])
                secs = float(parts[1])
                return mins * 60 + secs
            else:
                return float(time_str)
        except (ValueError, IndexError):
            pass
    
    if hundredths > 0:
        return hundredths / 100.0
    
    return None



def build_runner_table(races: List[Dict], 
                       include_jumpouts: bool = False,
                       verbose: bool = True) -> pd.DataFrame:
    """Build feature-engineered runner table from race data."""
    if verbose:
        print("\nBuilding runner table...")
    
    rows = []
    par_calc = ParTimeCalculator()
    
    stats = {
        'total_races': len(races),
        'jumpouts_skipped': 0,
        'scratched_skipped': 0,
        'runners_processed': 0,
    }
    
    for race in races:
        if is_jumpout(race) and not include_jumpouts:
            continue
        
        win_time = parse_winning_time(race)
        if win_time:
            par_calc.add_race(
                track=race.get('course', ''),
                distance=safe_int(race.get('distance')),
                going=race.get('going', ''),
                race_class=race.get('class', ''),
                time_seconds=win_time
            )
    
    par_calc.compute_pars()
    
    for race in races:
        if is_jumpout(race):
            stats['jumpouts_skipped'] += 1
            if not include_jumpouts:
                continue
        
        race_id = race.get('race_id', '')
        race_date = race.get('date', '')
        course = race.get('course', '')
        distance = safe_int(race.get('distance'))
        going = race.get('going', '')
        race_class = race.get('class', '')
        race_number = safe_int(race.get('race_number'))
        
        distance_band = get_distance_band(distance)
        
        win_time = parse_winning_time(race)
        par_time = par_calc.get_par(course, distance, going)
        
        runners = race.get('runners', [])
        active_runners = [r for r in runners if not is_scratched(r)]
        field_size = len(active_runners)
        
        for runner in runners:
            if is_scratched(runner):
                stats['scratched_skipped'] += 1
                continue
            
            stats['runners_processed'] += 1
            
            row = {
                'race_id': race_id,
                'race_date': race_date,
                'course': course,
                'distance': distance,
                'distance_band': distance_band,
                'going': going,
                'race_class': race_class,
                'race_number': race_number,
                'field_size': field_size,
                'is_jumpout': is_jumpout(race),
                
                'horse_id': runner.get('horse_id', ''),
                'horse': runner.get('horse', ''),
                'number': safe_int(runner.get('number') or runner.get('tab_no')),
                'barrier': safe_int(runner.get('draw') or runner.get('barrier')),
                'jockey': runner.get('jockey', ''),
                'jockey_id': runner.get('jockey_id', ''),
                'trainer': runner.get('trainer', ''),
                'trainer_id': runner.get('trainer_id', ''),
                'weight': safe_float(runner.get('weight')),
                
                'position': safe_int(runner.get('position')),
                'is_winner': safe_int(runner.get('position')) == 1,
                'is_placed': safe_int(runner.get('position')) <= 3,
                
                'barrier_group': get_barrier_group(safe_int(runner.get('draw') or runner.get('barrier'))),
            }
            
            form_features = parse_form_string(runner.get('form', ''))
            row.update(form_features)
            
            runner_stats = runner.get('stats', {}) or {}
            last_raced = runner_stats.get('last_raced', '')
            rest_features = calculate_rest_days(race_date, last_raced)
            row.update(rest_features)
            
            comment = runner.get('comment', '')
            comment_features = parse_comment_signals(comment)
            row.update(comment_features)
            
            position = safe_int(runner.get('position'))
            stats_features = adjust_stats_to_pre_race(runner_stats, position)
            row.update(stats_features)
            
            odds_features = extract_market_odds(runner)
            row.update(odds_features)
            
            if win_time and par_time:
                row['race_time'] = win_time
                row['par_time'] = par_time
                row['time_vs_par'] = round(win_time - par_time, 2)
                row['speed_index'] = round(par_time / win_time * 100, 2) if win_time > 0 else None
            
            rows.append(row)
    
    df = pd.DataFrame(rows)
    
    if 'adversity_score' in df.columns:
        df['forgive_flag'] = df.apply(apply_forgive_flag, axis=1)
    
    if 'market_odds' in df.columns:
        df['is_favourite'] = df.groupby('race_id')['market_odds'].transform(
            lambda x: x == x.min()
        )
    
    if verbose:
        print(f"  Total races: {stats['total_races']}")
        print(f"  Jumpouts skipped: {stats['jumpouts_skipped']}")
        print(f"  Scratched skipped: {stats['scratched_skipped']}")
        print(f"  Runners processed: {stats['runners_processed']}")
        print(f"  Final rows: {len(df)}")
    
    return df



def enrich_with_sectional_features(df: pd.DataFrame, db_url: str) -> pd.DataFrame:
    """
    Joins Phase 2 biomechanical & statistical sectional features from the
    sectional_times DB table into the runner DataFrame.

    Fetches each horse's most recent pre-race sectional row.
    The 8 columns match the Phase 2 entries in ml_model.py FEATURE_COLUMNS.
    """
    SECTIONAL_COLS = ['z_200m', 'z_400m', 'z_600m', 'z_800m',
                      'lambda_decay', 'svi', 'rsi', 'trip_cost_seconds']

    # Initialise columns with 0.0 (neutral / no penalty)
    for col in SECTIONAL_COLS:
        if col not in df.columns:
            df[col] = 0.0

    if not db_url or df.empty:
        return df

    try:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get unique (horse_name, race_date) pairs — fetch most recent sectional
        # run BEFORE race_date (avoids data leakage)
        horse_dates = df[['horse_name', 'race_date']].drop_duplicates().values.tolist() \
            if 'horse_name' in df.columns and 'race_date' in df.columns else []

        sectional_lookup = {}
        for horse_name, race_date in horse_dates:
            if not horse_name or not race_date:
                continue
            try:
                cur.execute("""
                    SELECT z_200m, z_400m, z_600m, z_800m,
                           lambda_decay, svi, rsi, trip_cost_seconds
                    FROM sectional_times
                    WHERE LOWER(horse_name) = LOWER(%s)
                      AND race_date < %s
                      AND z_200m IS NOT NULL
                    ORDER BY race_date DESC
                    LIMIT 1
                """, (horse_name, race_date))
                row = cur.fetchone()
                if row:
                    sectional_lookup[(horse_name.lower(), race_date)] = dict(row)
            except Exception:
                pass

        cur.close()
        conn.close()

        if not sectional_lookup:
            return df

        def _get_sectional(row):
            key = (str(row.get('horse_name', '')).lower(), str(row.get('race_date', '')))
            return sectional_lookup.get(key, {})

        for col in SECTIONAL_COLS:
            df[col] = df.apply(
                lambda r: _get_sectional(r).get(col, 0.0) or 0.0, axis=1
            )

    except Exception as e:
        print(f"  [Sectional enrich] DB error: {e}")

    return df



def make_sanity_report(df: pd.DataFrame, save_csv: Optional[str] = None) -> Dict:
    """Generate sanity check report from runner table."""
    report = {}
    
    print("\n" + "=" * 70)
    print("  SANITY REPORT")
    print("=" * 70)
    
    if 'is_jumpout' in df.columns:
        df_real = df[df['is_jumpout'] == False].copy()
    else:
        df_real = df.copy()
    
    print(f"\n  Dataset: {len(df_real)} runners in real races")
    
    if 'is_favourite' in df_real.columns and 'is_winner' in df_real.columns:
        fav_df = df_real[df_real['is_favourite'] == True]
        if len(fav_df) > 0:
            fav_win_rate = fav_df['is_winner'].mean() * 100
            report['favourite_win_rate'] = round(fav_win_rate, 2)
            print(f"\n  1. FAVOURITE WIN RATE: {fav_win_rate:.1f}%")
            print(f"     ({fav_df['is_winner'].sum()} wins from {len(fav_df)} favourites)")
    
    if 'market_odds' in df_real.columns and 'is_winner' in df_real.columns:
        df_with_odds = df_real[df_real['market_odds'].notna()].copy()
        if len(df_with_odds) > 0:
            df_with_odds['odds_bin'] = pd.cut(
                df_with_odds['market_odds'],
                bins=ODDS_BINS,
                labels=ODDS_LABELS,
                right=True
            )
            
            odds_summary = df_with_odds.groupby('odds_bin', observed=True).agg({
                'is_winner': ['sum', 'count', 'mean']
            }).round(3)
            odds_summary.columns = ['wins', 'runners', 'win_rate']
            odds_summary['win_rate'] = (odds_summary['win_rate'] * 100).round(1)
            
            report['odds_bins'] = odds_summary.to_dict()
            
            print("\n  2. WIN RATE BY ODDS BIN:")
            print("     " + "-" * 45)
            print(f"     {'Odds Bin':<10} {'Runners':>10} {'Wins':>8} {'Win %':>10}")
            print("     " + "-" * 45)
            for idx, row in odds_summary.iterrows():
                print(f"     {idx:<10} {int(row['runners']):>10} {int(row['wins']):>8} {row['win_rate']:>9.1f}%")
    
    if 'rest_bin' in df_real.columns and 'is_winner' in df_real.columns:
        df_with_rest = df_real[df_real['rest_bin'].notna()].copy()
        if len(df_with_rest) > 0:
            rest_summary = df_with_rest.groupby('rest_bin', observed=True).agg({
                'is_winner': ['sum', 'count', 'mean']
            }).round(3)
            rest_summary.columns = ['wins', 'runners', 'win_rate']
            rest_summary['win_rate'] = (rest_summary['win_rate'] * 100).round(1)
            
            rest_order = {label: i for i, label in enumerate(REST_LABELS)}
            rest_summary['order'] = rest_summary.index.map(lambda x: rest_order.get(x, 99))
            rest_summary = rest_summary.sort_values('order').drop('order', axis=1)
            
            report['rest_bins'] = rest_summary.to_dict()
            
            print("\n  3. WIN RATE BY REST DAYS:")
            print("     " + "-" * 45)
            print(f"     {'Rest Days':<10} {'Runners':>10} {'Wins':>8} {'Win %':>10}")
            print("     " + "-" * 45)
            for idx, row in rest_summary.iterrows():
                print(f"     {idx:<10} {int(row['runners']):>10} {int(row['wins']):>8} {row['win_rate']:>9.1f}%")
    
    if 'barrier_group' in df_real.columns and 'distance_band' in df_real.columns:
        df_with_barrier = df_real[
            (df_real['barrier_group'] != 'unknown') & 
            (df_real['distance_band'] != 'unknown')
        ].copy()
        
        if len(df_with_barrier) > 0:
            barrier_summary = df_with_barrier.groupby(
                ['distance_band', 'barrier_group'], observed=True
            ).agg({
                'is_winner': ['sum', 'count', 'mean']
            }).round(3)
            barrier_summary.columns = ['wins', 'runners', 'win_rate']
            barrier_summary['win_rate'] = (barrier_summary['win_rate'] * 100).round(1)
            
            report['barrier_by_distance'] = barrier_summary.to_dict()
            
            print("\n  4. BARRIER WIN RATE BY DISTANCE BAND:")
            print("     " + "-" * 55)
            print(f"     {'Distance':<10} {'Barrier':<10} {'Runners':>10} {'Wins':>8} {'Win %':>10}")
            print("     " + "-" * 55)
            for (dist, barrier), row in barrier_summary.iterrows():
                print(f"     {dist:<10} {barrier:<10} {int(row['runners']):>10} {int(row['wins']):>8} {row['win_rate']:>9.1f}%")
    
    print("\n  5. MISSINGNESS REPORT:")
    print("     " + "-" * 35)
    
    key_columns = [
        'market_odds', 'sp', 'barrier', 'days_since_last', 
        'course_starts_pre', 'race_time'
    ]
    
    missing_stats = {}
    for col in key_columns:
        if col in df_real.columns:
            missing_pct = df_real[col].isna().mean() * 100
            missing_stats[col] = round(missing_pct, 1)
            print(f"     {col:<20} {missing_pct:>10.1f}% missing")
    
    if 'adversity_score' in df_real.columns:
        no_comment = ((df_real['adversity_score'] == 0) & 
                      (df_real['finish_score'] == 0)).mean() * 100
        print(f"     {'comment':<20} {no_comment:>10.1f}% missing/empty")
    
    report['missingness'] = missing_stats
    
    print("\n  6. DATA LEAKAGE CHECKS:")
    print("     " + "-" * 35)
    
    leakage_ok = True
    
    if 'course_wins_pre' in df_real.columns:
        # Winners should have course_wins_pre that doesn't include this win
        winners = df_real[df_real['is_winner'] == True]
        if len(winners) > 0:
            # If properly adjusted, some winners will have 0 pre-race wins
            zero_wins_pct = (winners['course_wins_pre'] == 0).mean() * 100
            print(f"     Stats adjustment: {zero_wins_pct:.1f}% of winners had 0 pre-race course wins (expected >0%)")
            if zero_wins_pct == 0:
                print("     WARNING: Possible data leakage - check stats adjustment")
                leakage_ok = False
    
    if 'is_jumpout' in df_real.columns:
        jumpout_pct = df_real['is_jumpout'].mean() * 100
        print(f"     Jumpouts in data: {jumpout_pct:.1f}%")
        if jumpout_pct > 0:
            print("     Note: Use df[df['is_jumpout']==False] for modelling")
    
    report['leakage_check_passed'] = leakage_ok
    
    print("\n" + "=" * 70)
    
    if save_csv:
        summary_rows = []
        for section, data in report.items():
            if isinstance(data, dict):
                for k, v in data.items():
                    summary_rows.append({'section': section, 'metric': k, 'value': str(v)})
            else:
                summary_rows.append({'section': section, 'metric': 'value', 'value': str(data)})
        
        pd.DataFrame(summary_rows).to_csv(save_csv, index=False)
        print(f"\n  Report saved to: {save_csv}")
    
    return report



def get_feature_columns() -> Dict[str, List[str]]:
    """Get lists of feature columns by category."""
    return {
        'form': [
            'recent_wins', 'recent_places', 'recent_avg_pos', 
            'recent_best_pos', 'starts_in_form_window', 'form_trend'
        ],
        'rest': [
            'days_since_last', 'rest_bin', 'rest_sweet_spot', 'first_up'
        ],
        'comment': [
            'trouble_flag', 'wide_flag', 'start_flag', 'strong_finish_flag',
            'adversity_score', 'finish_score', 'forgive_flag'
        ],
        'track_distance': [
            'course_starts_pre', 'course_wins_pre', 'course_win_rate',
            'distance_starts_pre', 'distance_wins_pre', 'distance_win_rate',
            'course_distance_starts_pre', 'course_distance_wins_pre', 'course_distance_win_rate'
        ],
        'market': [
            'sp', 'best_win_odds', 'market_odds', 'is_favourite'
        ],
        'race': [
            'distance', 'distance_band', 'field_size', 'barrier', 'barrier_group'
        ],
        'speed': [
            'race_time', 'par_time', 'time_vs_par', 'speed_index'
        ]
    }


def prepare_for_model(df: pd.DataFrame, 
                      exclude_jumpouts: bool = True,
                      fill_missing: bool = True) -> pd.DataFrame:
    """Prepare DataFrame for model training."""
    df_model = df.copy()
    
    if exclude_jumpouts and 'is_jumpout' in df_model.columns:
        df_model = df_model[df_model['is_jumpout'] == False]
    
    if fill_missing:
        numeric_defaults = {
            'recent_wins': 0,
            'recent_places': 0,
            'recent_avg_pos': 5.0,
            'starts_in_form_window': 0,
            'days_since_last': 30,
            'adversity_score': 0,
            'finish_score': 0,
            'course_starts_pre': 0,
            'course_wins_pre': 0,
            'course_win_rate': 0.2,
            'distance_starts_pre': 0,
            'distance_wins_pre': 0,
            'distance_win_rate': 0.2,
        }
        
        for col, default in numeric_defaults.items():
            if col in df_model.columns:
                df_model[col] = df_model[col].fillna(default)
        
        if 'form_trend' in df_model.columns:
            df_model['form_trend'] = df_model['form_trend'].fillna('unknown')
        if 'rest_bin' in df_model.columns:
            df_model['rest_bin'] = df_model['rest_bin'].fillna('unknown')
    
    return df_model



def main():
    parser = argparse.ArgumentParser(
        description='Build feature-engineered runner table from race JSON'
    )
    parser.add_argument(
        '--input', '-i', 
        nargs='+', 
        required=True,
        help='Input JSON file(s) or directory'
    )
    parser.add_argument(
        '--out', '-o',
        default='features.csv',
        help='Output CSV file (default: features.csv)'
    )
    parser.add_argument(
        '--report', '-r',
        default=None,
        help='Save sanity report to CSV'
    )
    parser.add_argument(
        '--include-jumpouts',
        action='store_true',
        help='Include jumpouts in output (default: exclude)'
    )
    
    args = parser.parse_args()
    
    print("\n" + "=" * 70)
    print("  HORSE RACING FEATURE ENGINEERING")
    print("=" * 70)
    
    print("\n  Loading data...")
    races = load_json_files(args.input)
    
    if not races:
        print("  ERROR: No races loaded")
        return
    
    df = build_runner_table(races, include_jumpouts=args.include_jumpouts)
    
    if len(df) == 0:
        print("  ERROR: No runners processed")
        return
    
    df.to_csv(args.out, index=False)
    print(f"\n  Features saved to: {args.out}")
    
    make_sanity_report(df, save_csv=args.report)
    
    print("\n  Done!")


if __name__ == '__main__':
    main()
