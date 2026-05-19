#!/usr/bin/env python3
"""Enhanced ML model training with temporal cross-validation, probability calibration, SHAP analysis, and regional/distance sub-models."""
import os
import sys
import json
import pickle
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any

import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

from sklearn.model_selection import TimeSeriesSplit
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    roc_auc_score, precision_score, recall_score, f1_score,
    brier_score_loss, log_loss
)

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False

try:
    import catboost as cb
    HAS_CB = True
except ImportError:
    HAS_CB = False

try:
    import shap
    HAS_SHAP = True
except ImportError:
    HAS_SHAP = False

try:
    import category_encoders as ce
    HAS_CE = True
except ImportError:
    HAS_CE = False

TRACK_REGIONS = {
    'Randwick': 'NSW', 'Warwick Farm': 'NSW', 'Rosehill': 'NSW',
    'Canterbury': 'NSW', 'Kensington': 'NSW', 'Newcastle': 'NSW',
    'Gosford': 'NSW', 'Wyong': 'NSW', 'Hawkesbury': 'NSW',
    'Flemington': 'VIC', 'Caulfield': 'VIC', 'Moonee Valley': 'VIC',
    'Sandown': 'VIC', 'Sportsbet Sandown Hillside': 'VIC',
    'Pakenham': 'VIC', 'Mornington': 'VIC', 'Cranbourne': 'VIC',
    'Eagle Farm': 'QLD', 'Doomben': 'QLD', 'Gold Coast': 'QLD',
    'Sunshine Coast': 'QLD', 'Ipswich': 'QLD',
    'Ascot': 'WA', 'Pinjarra': 'WA', 'Belmont': 'WA',
    'Morphettville': 'SA', 'Murray Bridge': 'SA',
}


def get_track_region(track: str) -> str:
    """Get region for a track, with fuzzy matching."""
    if not track:
        return 'OTHER'
    
    if track in TRACK_REGIONS:
        return TRACK_REGIONS[track]
    
    track_lower = track.lower()
    for t, region in TRACK_REGIONS.items():
        if t.lower() in track_lower or track_lower in t.lower():
            return region
    
    return 'OTHER'


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


def extract_distance_from_text(distance_str) -> int:
    """Extract numeric distance from string."""
    if not distance_str:
        return 1400
    if isinstance(distance_str, (int, float)):
        return int(distance_str)
    try:
        import re
        match = re.search(r'(\d+)', str(distance_str))
        if match:
            return int(match.group(1))
    except:
        pass
    return 1400


def calculate_form_score(form_str: str) -> float:
    """Calculate weighted form score from form string."""
    if not form_str:
        return 0
    
    form = str(form_str).replace('-', '').replace(' ', '')
    score = 0
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


class EnhancedRacingMLModel:
    """Enhanced ML model with temporal cross-validation, probability calibration, SHAP analysis, and regional sub-models."""
    
    def __init__(self, model_dir: str = None):
        self.model_dir = model_dir or os.path.join(os.path.dirname(__file__), 'models')
        os.makedirs(self.model_dir, exist_ok=True)
        
        self.main_model = None
        self.calibrated_model = None
        self.calibration_method = 'isotonic'
        self.reliability_diagram = None
        self.regional_models = {}
        self.distance_models = {}
        self.feature_importance = {}
        self.scaler = StandardScaler()
        self.encoders = {}
        self.feature_columns = []
        self.categorical_columns = []
        self.metrics = {}
    
    def _build_race_key(self, row) -> str:
        """Build composite race key from track+date+race_number."""
        track = str(row.get('track', '')).strip()
        date = str(row.get('race_date', '')).strip()
        rnum = str(row.get('race_number', '')).strip()
        race_id = str(row.get('race_id', '')).strip()
        if race_id and race_id != '' and race_id != 'None' and race_id != 'nan':
            return race_id
        return f"{track}_{date}_R{rnum}"
    
    def _compute_field_size(self, df: pd.DataFrame) -> pd.Series:
        """Compute field size per race from data grouping."""
        df['_race_key'] = df.apply(self._build_race_key, axis=1)
        field_sizes = df.groupby('_race_key')['_race_key'].transform('count')
        return field_sizes
    
    def _compute_overround_free_prob(self, df: pd.DataFrame) -> pd.Series:
        """Remove bookmaker overround to get true implied probabilities."""
        df['_race_key'] = df.apply(self._build_race_key, axis=1)
        raw_prob = 1.0 / df['market_odds'].clip(lower=1.01)
        overround = df.groupby('_race_key')['market_odds'].transform(
            lambda x: (1.0 / x.clip(lower=1.01)).sum()
        )
        overround = overround.clip(lower=0.5)
        fair_prob = raw_prob / overround
        return fair_prob * 100
    
    def _compute_within_race_ranks(self, df: pd.DataFrame) -> pd.DataFrame:
        """Rank features within each race for relative comparison."""
        df['_race_key'] = df.apply(self._build_race_key, axis=1)
        
        df['odds_rank_in_race'] = df.groupby('_race_key')['market_odds'].rank(method='min')
        df['weight_rank_in_race'] = df.groupby('_race_key')['weight'].rank(method='min', ascending=False)
        df['barrier_rank_in_race'] = df.groupby('_race_key')['barrier'].rank(method='min')
        df['form_rank_in_race'] = df.groupby('_race_key')['weighted_form_score'].rank(method='min', ascending=False)
        
        field_sizes = df.groupby('_race_key')['_race_key'].transform('count')
        df['odds_rank_pct'] = df['odds_rank_in_race'] / field_sizes.clip(lower=1)
        df['weight_rank_pct'] = df['weight_rank_in_race'] / field_sizes.clip(lower=1)
        df['barrier_rank_pct'] = df['barrier_rank_in_race'] / field_sizes.clip(lower=1)
        df['form_rank_pct'] = df['form_rank_in_race'] / field_sizes.clip(lower=1)
        
        return df
    
    def _compute_career_stats(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute rolling career win/place rates using vectorized cumulative sums."""
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        df['_won_int'] = pd.to_numeric(df['won'], errors='coerce').fillna(0).astype(int)
        df['_placed_int'] = pd.to_numeric(df['placed'], errors='coerce').fillna(0).astype(int)
        
        g = df.groupby('horse_name')
        cum_starts = g.cumcount()
        cum_wins = g['_won_int'].cumsum() - df['_won_int']
        cum_places = g['_placed_int'].cumsum() - df['_placed_int']
        
        df['career_starts'] = cum_starts
        safe_starts = cum_starts.clip(lower=1)
        df['career_win_rate'] = (cum_wins / safe_starts) * 100
        df['career_place_rate'] = (cum_places / safe_starts) * 100
        df['is_debutant'] = (df['career_starts'] == 0).astype(int)
        df['is_experienced'] = (df['career_starts'] >= 10).astype(int)
        
        df.drop(['_won_int', '_placed_int'], axis=1, inplace=True)
        return df
    
    def _compute_form_consistency(self, df: pd.DataFrame) -> pd.Series:
        """Compute form consistency from form string (lower variance = more consistent)."""
        def _calc_consistency(form_str):
            if not form_str or str(form_str) in ('', 'nan', 'None'):
                return 5.0
            form = str(form_str).replace('-', '').replace(' ', '')
            positions = []
            for ch in form[:6]:
                if ch.isdigit():
                    positions.append(int(ch))
                elif ch == '0':
                    positions.append(10)
            if len(positions) < 2:
                return 5.0
            avg = sum(positions) / len(positions)
            variance = sum((p - avg) ** 2 for p in positions) / len(positions)
            return max(0, 10 - variance)
        
        if 'form' in df.columns:
            return df['form'].apply(_calc_consistency)
        return pd.Series([5.0] * len(df), index=df.index)
    
    def _compute_class_changes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute class drop/rise using vectorized shift per horse."""
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        prev_class = df.groupby('horse_name')['class_level'].shift(1)
        prev_class = prev_class.fillna(df['class_level'])
        df['class_change'] = prev_class - df['class_level']
        df['is_class_drop'] = (df['class_change'] > 0).astype(int)
        df['is_class_rise'] = (df['class_change'] < 0).astype(int)
        return df
    
    def _compute_days_since_win(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute days since each horse last won using vectorized operations."""
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        df['_race_dt'] = pd.to_datetime(df['race_date'], errors='coerce')
        df['_won_int2'] = pd.to_numeric(df['won'], errors='coerce').fillna(0).astype(int)
        df['_win_date'] = df['_race_dt'].where(df['_won_int2'] == 1)
        df['_win_date'] = df.groupby('horse_name')['_win_date'].ffill()
        df['_win_date'] = df.groupby('horse_name')['_win_date'].shift(1)
        df['_win_date'] = df.groupby('horse_name')['_win_date'].ffill()
        df['days_since_win'] = (df['_race_dt'] - df['_win_date']).dt.days.fillna(365).clip(upper=365)
        df['is_recent_winner'] = (df['days_since_win'] <= 30).astype(int)
        df.drop(['_race_dt', '_won_int2', '_win_date'], axis=1, inplace=True)
        return df
    
    def _compute_course_distance_stats(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute rolling course+distance specialist stats using vectorized cumulative sums."""
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        df['_track_lower'] = df['track'].fillna('').str.strip().str.lower()
        df['_cd_key'] = df['horse_name'] + '|' + df['_track_lower'] + '|' + df['distance'].fillna('').astype(str)
        df['_won_int3'] = pd.to_numeric(df['won'], errors='coerce').fillna(0).astype(int)
        
        g = df.groupby('_cd_key')
        cum_starts = g.cumcount()
        cum_wins = g['_won_int3'].cumsum() - df['_won_int3']
        
        safe_starts = cum_starts.clip(lower=1)
        df['course_distance_strike_rate'] = np.where(cum_starts > 0, (cum_wins / safe_starts) * 100, 0)
        df['is_course_distance_winner'] = (cum_wins > 0).astype(int)
        df.drop(['_track_lower', '_cd_key', '_won_int3'], axis=1, inplace=True)
        return df
    
    def _compute_jockey_trainer_rates(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute rolling jockey and trainer win rates using vectorized cumulative sums."""
        df = df.sort_values(['race_date', 'race_number']).reset_index(drop=True)
        df['_won_int4'] = pd.to_numeric(df['won'], errors='coerce').fillna(0).astype(int)
        df['_jockey_clean'] = df['jockey'].fillna('').str.strip()
        df['_trainer_clean'] = df['trainer'].fillna('').str.strip()
        
        jg = df.groupby('_jockey_clean')
        j_starts = jg.cumcount()
        j_wins = jg['_won_int4'].cumsum() - df['_won_int4']
        j_safe = j_starts.clip(lower=1)
        df['jockey_win_rate'] = np.where(j_starts > 0, (j_wins / j_safe) * 100, 12.0)
        
        tg = df.groupby('_trainer_clean')
        t_starts = tg.cumcount()
        t_wins = tg['_won_int4'].cumsum() - df['_won_int4']
        t_safe = t_starts.clip(lower=1)
        df['trainer_win_rate'] = np.where(t_starts > 0, (t_wins / t_safe) * 100, 10.0)
        
        df['is_top_jockey'] = (df['jockey_win_rate'] >= 18).astype(int)
        df['is_top_trainer'] = (df['trainer_win_rate'] >= 15).astype(int)
        df.drop(['_won_int4', '_jockey_clean', '_trainer_clean'], axis=1, inplace=True)
        return df
    
    def _compute_form_trajectory(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute form improvement/decline from recent finishing positions."""
        def _calc_trajectory(form_str):
            if not form_str or str(form_str) in ('', 'nan', 'None'):
                return 0.0, 0, 0
            form = str(form_str).replace('-', '').replace(' ', '')
            positions = []
            for ch in form[:6]:
                if ch.isdigit():
                    positions.append(int(ch))
            if len(positions) < 2:
                return 0.0, 0, 0
            recent = positions[:3] if len(positions) >= 3 else positions[:2]
            older = positions[len(recent):]
            if not older:
                older = positions[1:]
                recent = positions[:1]
            avg_recent = sum(recent) / len(recent)
            avg_older = sum(older) / len(older)
            trajectory = avg_older - avg_recent
            is_improving = 1 if trajectory > 0.5 else 0
            is_declining = 1 if trajectory < -0.5 else 0
            return trajectory, is_improving, is_declining
        
        if 'form' in df.columns:
            results = df['form'].apply(_calc_trajectory)
            df['form_trajectory'] = [r[0] for r in results]
            df['is_improving'] = [r[1] for r in results]
            df['is_declining'] = [r[2] for r in results]
        else:
            df['form_trajectory'] = 0.0
            df['is_improving'] = 0
            df['is_declining'] = 0
        return df
    
    def _compute_track_barrier_winrate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute track-specific barrier win rates using vectorized cumulative sums."""
        df = df.sort_values(['race_date', 'race_number']).reset_index(drop=True)
        df['_track_lower2'] = df['track'].fillna('').str.strip().str.lower()
        df['_dist_cat'] = df['distance_numeric'].fillna(1200).apply(categorize_distance)
        df['_barrier_group'] = np.where(df['barrier'] <= 4, 'inner',
                              np.where(df['barrier'] <= 8, 'mid', 'outer'))
        df['_tb_key'] = df['_track_lower2'] + '|' + df['_dist_cat'] + '|' + df['_barrier_group']
        df['_won_int5'] = pd.to_numeric(df['won'], errors='coerce').fillna(0).astype(int)
        
        g = df.groupby('_tb_key')
        cum_starts = g.cumcount()
        cum_wins = g['_won_int5'].cumsum() - df['_won_int5']
        safe_starts = cum_starts.clip(lower=1)
        df['track_barrier_winrate'] = np.where(cum_starts >= 5, (cum_wins / safe_starts) * 100, 10.0)
        
        df.drop(['_track_lower2', '_dist_cat', '_barrier_group', '_tb_key', '_won_int5'], axis=1, inplace=True)
        return df
    
    def _compute_last_position_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute last-start position using vectorized shift per horse."""
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        df['_pos'] = pd.to_numeric(df['actual_position'], errors='coerce')
        df['_won_int6'] = pd.to_numeric(df['won'], errors='coerce').fillna(0).astype(int)
        df['_pos'] = df['_pos'].fillna(df['_won_int6'].map({1: 1}).fillna(9))
        
        df['last_start_position'] = df.groupby('horse_name')['_pos'].shift(1).fillna(9)
        df['is_last_start_winner'] = (df['last_start_position'] == 1).astype(int)
        
        df.drop(['_pos', '_won_int6'], axis=1, inplace=True)
        return df
    
    def _compute_weight_change(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute weight change from last start per horse using vectorized shift."""
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        df['_weight_num'] = pd.to_numeric(df['weight'], errors='coerce').fillna(55)
        prev_weight = df.groupby('horse_name')['_weight_num'].shift(1)
        df['weight_change'] = (df['_weight_num'] - prev_weight).fillna(0).clip(lower=-5, upper=5)
        df['is_weight_drop'] = (df['weight_change'] < -1).astype(int)
        df['is_weight_rise'] = (df['weight_change'] > 1).astype(int)
        df.drop(['_weight_num'], axis=1, inplace=True)
        return df

    def _compute_first_up_winrate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute each horse's historical first-up win rate using cumulative sums."""
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        df['_won_fu'] = pd.to_numeric(df['won'], errors='coerce').fillna(0).astype(int)
        if 'is_first_up' in df.columns:
            df['_is_fu'] = pd.to_numeric(df['is_first_up'], errors='coerce').fillna(0).astype(int)
        elif 'days_since_run' in df.columns:
            df['_is_fu'] = (pd.to_numeric(df['days_since_run'], errors='coerce').fillna(30) >= 60).astype(int)
        else:
            df['_is_fu'] = 0
        df['_fu_win'] = (df['_is_fu'] * df['_won_fu'])

        g = df.groupby('horse_name')
        cum_fu_starts = g['_is_fu'].cumsum() - df['_is_fu']
        cum_fu_wins = g['_fu_win'].cumsum() - df['_fu_win']
        safe_fu = cum_fu_starts.clip(lower=1)
        df['horse_first_up_winrate'] = np.where(cum_fu_starts >= 2, (cum_fu_wins / safe_fu) * 100, 10.0)

        df.drop(['_won_fu', '_is_fu', '_fu_win'], axis=1, inplace=True)
        return df

    def _compute_class_weighted_margin(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute rolling class-weighted performance score per horse.
        Better finishing position in higher-class races gets more weight.
        """
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        df['_pos_num'] = pd.to_numeric(df['actual_position'], errors='coerce').fillna(9).clip(upper=15)
        df['_class_wt'] = df['class_level'].clip(lower=10, upper=100) / 100.0
        df['_perf_score'] = df['_class_wt'] * (10 - df['_pos_num'].clip(upper=10)) / 9.0

        g = df.groupby('horse_name')
        cum_perf = g['_perf_score'].cumsum() - df['_perf_score']
        cum_count = g.cumcount()
        safe_count = cum_count.clip(lower=1)
        df['class_weighted_perf'] = np.where(cum_count > 0, cum_perf / safe_count, 0.5)

        df.drop(['_pos_num', '_class_wt', '_perf_score'], axis=1, inplace=True)
        return df

    def _compute_odds_rank(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute raw favourite rank within each race (1=fav, 2=2nd fav, etc.).
        Lower odds = lower rank (rank 1 = favourite).
        """
        df['_race_key_or'] = df.apply(self._build_race_key, axis=1)
        df['_odds_clean'] = pd.to_numeric(df['market_odds'], errors='coerce').fillna(99)
        df['odds_rank'] = df.groupby('_race_key_or')['_odds_clean'].rank(method='min', ascending=True).fillna(5)
        df['is_top3_market'] = (df['odds_rank'] <= 3).astype(int)
        df.drop(['_race_key_or', '_odds_clean'], axis=1, inplace=True)
        return df

    def _compute_field_quality(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute field quality score from class level and field composition."""
        df['_race_key_fq'] = df.apply(self._build_race_key, axis=1)
        df['_raw_implied'] = 100.0 / df['market_odds'].clip(lower=1.01)
        avg_implied = df.groupby('_race_key_fq')['_raw_implied'].transform('mean')
        df['field_avg_implied_prob'] = avg_implied.fillna(10.0)
        df['horse_vs_field'] = df['_raw_implied'] - df['field_avg_implied_prob']
        df['field_quality_score'] = (df['class_level'] * df['field_size'].clip(lower=4) / 20.0).clip(upper=50)
        df.drop(['_race_key_fq', '_raw_implied'], axis=1, inplace=True)
        return df

    def _compute_distance_change(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute distance change from last start per horse."""
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        prev_dist = df.groupby('horse_name')['distance_numeric'].shift(1)
        df['distance_change'] = (df['distance_numeric'] - prev_dist).fillna(0).clip(lower=-1000, upper=1000)
        df['is_distance_drop'] = (df['distance_change'] < -100).astype(int)
        df['is_distance_rise'] = (df['distance_change'] > 100).astype(int)
        return df

    def _compute_career_lifetime_winrate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute true career lifetime win rate from training data history (leakage-free)."""
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        df['_won_lt'] = pd.to_numeric(df['won'], errors='coerce').fillna(0).astype(int)
        g = df.groupby('horse_name')
        cum_starts = g.cumcount()
        cum_wins = g['_won_lt'].cumsum() - df['_won_lt']
        safe_starts = cum_starts.clip(lower=1)
        df['career_lifetime_winrate'] = np.where(cum_starts > 0, (cum_wins / safe_starts) * 100, 10.0)
        df.drop(['_won_lt'], axis=1, inplace=True)
        return df


    def _normalize_going(self, going_str: str) -> str:
        """Normalize going/track condition into 4 categories."""
        g = str(going_str).strip().lower()
        if 'heavy' in g:
            return 'heavy'
        elif 'soft' in g:
            return 'soft'
        elif 'synth' in g or 'all weather' in g or 'polytrack' in g:
            return 'synth'
        else:
            return 'good'

    def _compute_jockey_trainer_combo(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute rolling jockey-trainer combination win rate (Bill Benter's key feature).
        Some jockey-trainer pairings have significantly higher strike rates than either alone.
        """
        df = df.sort_values(['race_date', 'race_number']).reset_index(drop=True)
        df['_jt_key'] = df['jockey'].fillna('unk') + '|' + df['trainer'].fillna('unk')
        df['_won_jt'] = pd.to_numeric(df['won'], errors='coerce').fillna(0).astype(int)

        g = df.groupby('_jt_key')
        cum_starts = g.cumcount()
        cum_wins = g['_won_jt'].cumsum() - df['_won_jt']
        safe_starts = cum_starts.clip(lower=1)
        df['jockey_trainer_combo_winrate'] = np.where(
            cum_starts >= 5, (cum_wins / safe_starts) * 100, 12.0
        )
        df['is_strong_combo'] = (df['jockey_trainer_combo_winrate'] > 15).astype(int)

        df.drop(['_jt_key', '_won_jt'], axis=1, inplace=True)
        return df

    def _compute_going_specialist(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute per-horse win rate on today's track condition.
        Identifies horses that excel on specific going.
        """
        df = df.sort_values(['race_date', 'race_number']).reset_index(drop=True)
        df['_going_cat'] = df['going'].fillna('').apply(self._normalize_going)
        df['_horse_going_key'] = df['horse_name'].fillna('unk') + '|' + df['_going_cat']
        df['_won_go'] = pd.to_numeric(df['won'], errors='coerce').fillna(0).astype(int)

        g = df.groupby('_horse_going_key')
        cum_starts = g.cumcount()
        cum_wins = g['_won_go'].cumsum() - df['_won_go']
        safe_starts = cum_starts.clip(lower=1)
        df['going_specialist_winrate'] = np.where(
            cum_starts >= 3, (cum_wins / safe_starts) * 100, 10.0
        )
        df['is_going_specialist'] = (df['going_specialist_winrate'] > 20).astype(int)

        df.drop(['_going_cat', '_horse_going_key', '_won_go'], axis=1, inplace=True)
        return df

    def _compute_pace_fit(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute pace scenario fit score per runner.
        Leaders in slow-pace races get boosted; closers in fast-pace races get boosted.
        """
        df = df.sort_values(['race_date', 'race_number']).reset_index(drop=True)
        df['_race_key_pf'] = df.apply(self._build_race_key, axis=1)

        df['_pos1'] = pd.to_numeric(df['actual_position'], errors='coerce').fillna(9)
        df['_prev_pos1'] = df.groupby('horse_name')['_pos1'].shift(1).fillna(5)
        df['_prev_pos2'] = df.groupby('horse_name')['_pos1'].shift(2).fillna(5)
        df['_prev_pos3'] = df.groupby('horse_name')['_pos1'].shift(3).fillna(5)
        df['_avg_recent_pos'] = (df['_prev_pos1'] + df['_prev_pos2'] + df['_prev_pos3']) / 3.0

        df['_is_speed_horse'] = (df['_avg_recent_pos'] <= 3).astype(int)
        speed_count = df.groupby('_race_key_pf')['_is_speed_horse'].transform('sum')
        field_count = df.groupby('_race_key_pf')['_race_key_pf'].transform('count')
        df['pace_pressure'] = (speed_count / field_count.clip(lower=1) * 100).clip(upper=100)

        is_high_pace = df['pace_pressure'] >= 40
        is_low_pace = speed_count <= 2
        is_leader = df['_avg_recent_pos'] <= 3
        is_closer = df['_avg_recent_pos'] >= 6

        df['pace_fit_score'] = 0.0
        df.loc[is_leader & is_low_pace, 'pace_fit_score'] = 15.0
        df.loc[is_closer & is_high_pace, 'pace_fit_score'] = 12.0
        df.loc[is_leader & is_high_pace, 'pace_fit_score'] = -10.0
        df.loc[is_closer & is_low_pace, 'pace_fit_score'] = -8.0

        df.drop(['_race_key_pf', '_pos1', '_prev_pos1', '_prev_pos2', '_prev_pos3',
                 '_avg_recent_pos', '_is_speed_horse'], axis=1, inplace=True)
        return df

    def _compute_form_volatility(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute standard deviation of recent finishing positions per horse.
        Low volatility = reliable. High volatility = inconsistent.
        """
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        df['_pos_fv'] = pd.to_numeric(df['actual_position'], errors='coerce').fillna(9).clip(upper=15)

        df['_p1'] = df.groupby('horse_name')['_pos_fv'].shift(1)
        df['_p2'] = df.groupby('horse_name')['_pos_fv'].shift(2)
        df['_p3'] = df.groupby('horse_name')['_pos_fv'].shift(3)
        df['_p4'] = df.groupby('horse_name')['_pos_fv'].shift(4)
        df['_p5'] = df.groupby('horse_name')['_pos_fv'].shift(5)

        positions = df[['_p1', '_p2', '_p3', '_p4', '_p5']]
        df['_count_valid'] = positions.notna().sum(axis=1)
        df['_mean_pos'] = positions.mean(axis=1)
        df['_var_pos'] = positions.var(axis=1, ddof=0)
        df['form_volatility'] = np.where(
            df['_count_valid'] >= 3, np.sqrt(df['_var_pos'].fillna(9)), 3.0
        )
        df['is_consistent_runner'] = (df['form_volatility'] < 1.5).astype(int)

        df.drop(['_pos_fv', '_p1', '_p2', '_p3', '_p4', '_p5',
                 '_count_valid', '_mean_pos', '_var_pos'], axis=1, inplace=True)
        return df

    def _compute_class_speed_proxy(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute class-adjusted speed proxy from recent performance.
        Approximates speed figures: (10-position) * (class/100).
        """
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        df['_pos_sp'] = pd.to_numeric(df['actual_position'], errors='coerce').fillna(9).clip(upper=10)
        df['_class_sp'] = df['class_level'].clip(lower=10, upper=100) / 100.0
        df['_run_score'] = df['_class_sp'] * (10 - df['_pos_sp']) / 9.0

        weights = [3.0, 2.5, 2.0, 1.5, 1.0]
        weighted_sum = pd.Series(0.0, index=df.index)
        weight_total = pd.Series(0.0, index=df.index)
        for i, w in enumerate(weights):
            shifted = df.groupby('horse_name')['_run_score'].shift(i + 1)
            valid = shifted.notna()
            weighted_sum = weighted_sum + shifted.fillna(0) * w
            weight_total = weight_total + valid.astype(float) * w

        df['class_speed_proxy'] = np.where(
            weight_total > 0, weighted_sum / weight_total, 0.5
        )

        df.drop(['_pos_sp', '_class_sp', '_run_score'], axis=1, inplace=True)
        return df

    def _compute_spell_return(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute second-up win rate and spell return pattern.
        Some horses improve dramatically second-up after a spell.
        """
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        df['_won_sp'] = pd.to_numeric(df['won'], errors='coerce').fillna(0).astype(int)

        df['_race_date_dt'] = pd.to_datetime(df['race_date'], errors='coerce')
        df['_days_gap'] = df.groupby('horse_name')['_race_date_dt'].diff().dt.days.fillna(999)
        df['_prev_gap'] = df.groupby('horse_name')['_days_gap'].shift(1).fillna(999)

        df['is_second_up'] = ((df['_prev_gap'] >= 60) & (df['_days_gap'] <= 35)).astype(int)

        df['_su_win'] = df['is_second_up'] * df['_won_sp']
        g = df.groupby('horse_name')
        cum_su_starts = g['is_second_up'].cumsum() - df['is_second_up']
        cum_su_wins = g['_su_win'].cumsum() - df['_su_win']
        safe_su = cum_su_starts.clip(lower=1)
        df['second_up_winrate'] = np.where(
            cum_su_starts >= 2, (cum_su_wins / safe_su) * 100, 10.0
        )

        df.drop(['_won_sp', '_race_date_dt', '_days_gap', '_prev_gap', '_su_win'], axis=1, inplace=True)
        return df

    def _compute_market_confidence(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute market confidence residual.
        Compares where the market ranks a horse (odds_rank) vs where form alone would rank it.
        """
        df['_form_score_mc'] = df['weighted_form_score'].fillna(0)
        df['_race_key_mc'] = df.apply(self._build_race_key, axis=1)

        df['_form_rank'] = df.groupby('_race_key_mc')['_form_score_mc'].rank(
            method='min', ascending=False
        ).fillna(5)
        df['_odds_rank_mc'] = df['odds_rank'].fillna(5)

        df['market_confidence_residual'] = (df['_form_rank'] - df['_odds_rank_mc']).clip(lower=-10, upper=10)

        df.drop(['_form_score_mc', '_race_key_mc', '_form_rank', '_odds_rank_mc'], axis=1, inplace=True)
        return df

    def _compute_early_speed_model(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Predict early speed tendency and likely settling position.
        Combines historical finishing position patterns with barrier and field data.
        """
        df = df.sort_values(['race_date', 'race_number', 'horse_name']).reset_index(drop=True)
        df['_pos_es'] = pd.to_numeric(df['actual_position'], errors='coerce').fillna(9).clip(upper=15)

        for i in range(1, 6):
            df[f'_es_p{i}'] = df.groupby('horse_name')['_pos_es'].shift(i)

        es_cols = [f'_es_p{i}' for i in range(1, 6)]
        es_positions = df[es_cols]
        df['_es_count'] = es_positions.notna().sum(axis=1)

        weights_es = [3.0, 2.5, 2.0, 1.5, 1.0]
        weighted_sum = pd.Series(0.0, index=df.index)
        weight_total = pd.Series(0.0, index=df.index)
        for i, w in enumerate(weights_es):
            col = f'_es_p{i+1}'
            valid = df[col].notna()
            weighted_sum = weighted_sum + df[col].fillna(0) * w
            weight_total = weight_total + valid.astype(float) * w

        df['early_speed_tendency'] = np.where(
            weight_total > 0, weighted_sum / weight_total, 5.0
        )

        df['early_speed_score'] = ((10.0 - df['early_speed_tendency'].clip(upper=10)) / 9.0 * 100).clip(lower=0, upper=100)

        barrier = pd.to_numeric(df['barrier'], errors='coerce').fillna(5)
        df['_barrier_speed_bonus'] = np.where(
            barrier <= 4, 10.0,
            np.where(barrier <= 8, 0.0, -10.0)
        )

        df['predicted_early_position'] = (df['early_speed_tendency'] - df['_barrier_speed_bonus'] / 20.0).clip(lower=1, upper=15)

        df['is_likely_leader'] = (df['predicted_early_position'] <= 3).astype(int)

        df.drop(es_cols + ['_pos_es', '_es_count', '_barrier_speed_bonus'], axis=1, inplace=True)
        return df

    def _compute_weight_adjusted_speed(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute weight-adjusted speed rating.
        Normalizes performance by weight carried — same form under heavier weight implies more ability.
        """
        wfa_baseline = 56.0
        weight = pd.to_numeric(df['weight'], errors='coerce').fillna(wfa_baseline)

        df['weight_advantage_kg'] = (wfa_baseline - weight).clip(lower=-5, upper=5)

        speed_proxy = df.get('class_speed_proxy', pd.Series(0.5, index=df.index))
        if 'class_speed_proxy' not in df.columns:
            speed_proxy = pd.Series(0.5, index=df.index)
        else:
            speed_proxy = df['class_speed_proxy'].fillna(0.5)

        weight_adjustment = df['weight_advantage_kg'] * 0.02
        df['weight_adjusted_speed'] = (speed_proxy + weight_adjustment).clip(lower=0, upper=1.5)

        return df

    def _compute_race_tempo_prediction(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Predict race tempo from field composition of running styles.
        Races with multiple speed horses tend to have fast early fractions.
        """
        df['_race_key_tp'] = df.apply(self._build_race_key, axis=1)

        if 'early_speed_tendency' not in df.columns:
            df['_est'] = 5.0
        else:
            df['_est'] = df['early_speed_tendency'].fillna(5.0)

        df['_is_speed_tp'] = (df['_est'] <= 3.0).astype(int)
        df['_speed_count'] = df.groupby('_race_key_tp')['_is_speed_tp'].transform('sum')
        df['_field_count_tp'] = df.groupby('_race_key_tp')['_race_key_tp'].transform('count')

        df['_speed_ratio'] = df['_speed_count'] / df['_field_count_tp'].clip(lower=1)

        df['predicted_tempo'] = np.where(
            df['_speed_count'] >= 4, 80.0,
            np.where(df['_speed_count'] >= 3, 65.0,
            np.where(df['_speed_count'] >= 2, 50.0,
            np.where(df['_speed_count'] >= 1, 35.0, 20.0)))
        )

        distance = pd.to_numeric(df['distance_numeric'], errors='coerce').fillna(1400)
        df['predicted_tempo'] = np.where(
            distance <= 1200, df['predicted_tempo'] + 10,
            np.where(distance >= 2000, df['predicted_tempo'] - 10, df['predicted_tempo'])
        ).clip(0, 100)

        is_speed = df['_est'] <= 3.0
        is_closer = df['_est'] >= 6.0
        is_fast_tempo = df['predicted_tempo'] >= 65
        is_slow_tempo = df['predicted_tempo'] <= 35

        df['tempo_fit_score'] = 0.0
        df.loc[is_speed & is_slow_tempo, 'tempo_fit_score'] = 12.0
        df.loc[is_closer & is_fast_tempo, 'tempo_fit_score'] = 10.0
        df.loc[is_speed & is_fast_tempo, 'tempo_fit_score'] = -8.0
        df.loc[is_closer & is_slow_tempo, 'tempo_fit_score'] = -6.0

        df.drop(['_race_key_tp', '_est', '_is_speed_tp', '_speed_count', 
                 '_field_count_tp', '_speed_ratio'], axis=1, inplace=True)
        return df

    def prepare_features(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
        """Prepare features with advanced encoding and engineering."""
        df = df.copy()
        
        df['distance_numeric'] = df['distance'].apply(extract_distance_from_text)
        df['is_sprint'] = (df['distance_numeric'] <= 1200).astype(int)
        df['is_staying'] = (df['distance_numeric'] >= 2000).astype(int)
        
        df['distance_category'] = df['distance_numeric'].apply(categorize_distance)
        
        if 'track' in df.columns:
            df['track_region'] = df['track'].apply(get_track_region)
        else:
            df['track_region'] = 'OTHER'
        
        if 'barrier' in df.columns:
            df['barrier'] = pd.to_numeric(df['barrier'], errors='coerce').fillna(5)
            df['barrier_penalty'] = df.apply(
                lambda row: self._calc_barrier_penalty(
                    row['barrier'], row['distance_numeric']
                ), axis=1
            )
            df['inside_draw'] = (df['barrier'] <= 4).astype(int)
            df['wide_draw'] = (df['barrier'] >= 10).astype(int)
        
        if 'weight' in df.columns:
            df['weight'] = pd.to_numeric(df['weight'], errors='coerce').fillna(55)
            df['weight_penalty'] = (df['weight'] - 55).clip(lower=0) * 0.02
            df['light_weight'] = (df['weight'] <= 54).astype(int)
            df['heavy_weight'] = (df['weight'] >= 58).astype(int)
        
        if 'market_odds' in df.columns or 'starting_price' in df.columns:
            odds_col = 'market_odds' if 'market_odds' in df.columns else 'starting_price'
            df['market_odds'] = pd.to_numeric(df[odds_col], errors='coerce').fillna(10)
            df['market_implied_prob'] = 100 / df['market_odds'].clip(lower=1.01)
            df['is_favourite'] = (df['market_odds'] <= 3).astype(int)
            df['is_outsider'] = (df['market_odds'] >= 20).astype(int)
            df['log_odds'] = np.log1p(df['market_odds'])
        
        if 'weighted_form_score' not in df.columns:
            if 'form' in df.columns:
                df['weighted_form_score'] = df['form'].apply(calculate_form_score)
            else:
                df['weighted_form_score'] = 10
        
        if 'going' in df.columns:
            df['is_wet'] = df['going'].fillna('').str.lower().str.contains(
                'soft|heavy', regex=True
            ).astype(int)
        else:
            df['is_wet'] = 0
        
        if 'race_class' in df.columns:
            df['class_level'] = df['race_class'].apply(self._parse_class_level)
        else:
            df['class_level'] = 50
        
        
        df['field_size'] = self._compute_field_size(df)
        df['field_size'] = df['field_size'].clip(lower=2, upper=24)
        df['barrier_field_ratio'] = df['barrier'] / df['field_size'].clip(lower=1)
        df['is_small_field'] = (df['field_size'] <= 8).astype(int)
        df['is_large_field'] = (df['field_size'] >= 14).astype(int)
        
        df['fair_implied_prob'] = self._compute_overround_free_prob(df)
        df['market_overround'] = df.groupby('_race_key')['market_odds'].transform(
            lambda x: (1.0 / x.clip(lower=1.01)).sum() * 100
        )
        
        df = self._compute_within_race_ranks(df)
        
        df = self._compute_career_stats(df)
        
        df['form_consistency'] = self._compute_form_consistency(df)
        
        if 'horse_age' in df.columns:
            df['horse_age'] = pd.to_numeric(df['horse_age'], errors='coerce').fillna(4)
        else:
            df['horse_age'] = 4
        df['is_young_horse'] = (df['horse_age'] <= 3).astype(int)
        df['is_prime_age'] = ((df['horse_age'] >= 4) & (df['horse_age'] <= 6)).astype(int)
        df['is_older_horse'] = (df['horse_age'] >= 7).astype(int)
        
        
        df = self._compute_class_changes(df)
        
        df = self._compute_days_since_win(df)
        
        df = self._compute_course_distance_stats(df)
        
        df = self._compute_jockey_trainer_rates(df)
        
        df = self._compute_form_trajectory(df)
        
        df['distance_numeric'] = df['distance_numeric'].fillna(1200)
        df = self._compute_track_barrier_winrate(df)
        
        df = self._compute_last_position_features(df)
        
        df['is_mile'] = ((df['distance_numeric'] >= 1400) & (df['distance_numeric'] <= 1800)).astype(int)
        df['is_middle'] = ((df['distance_numeric'] > 1800) & (df['distance_numeric'] < 2200)).astype(int)
        
        
        df = self._compute_weight_change(df)
        
        df = self._compute_first_up_winrate(df)
        
        df = self._compute_class_weighted_margin(df)
        
        df = self._compute_odds_rank(df)
        
        df = self._compute_field_quality(df)
        
        df = self._compute_distance_change(df)
        
        df = self._compute_career_lifetime_winrate(df)
        
        
        df = self._compute_jockey_trainer_combo(df)
        
        df = self._compute_going_specialist(df)
        
        df = self._compute_pace_fit(df)
        
        df = self._compute_form_volatility(df)
        
        df = self._compute_class_speed_proxy(df)
        
        df = self._compute_spell_return(df)
        
        df = self._compute_market_confidence(df)
        
        
        df = self._compute_early_speed_model(df)
        
        df = self._compute_weight_adjusted_speed(df)
        
        df = self._compute_race_tempo_prediction(df)
        
        numeric_features = [
            'barrier', 'weight', 'market_odds', 'market_implied_prob',
            'distance_numeric', 'barrier_penalty', 'weight_penalty',
            'weighted_form_score', 'class_level', 'log_odds',
            'field_size', 'barrier_field_ratio', 'fair_implied_prob',
            'market_overround',
            'odds_rank_pct', 'weight_rank_pct', 'barrier_rank_pct', 'form_rank_pct',
            'career_starts', 'career_win_rate', 'career_place_rate',
            'form_consistency', 'horse_age',
            'class_change', 'days_since_win',
            'course_distance_strike_rate',
            'jockey_win_rate', 'trainer_win_rate',
            'form_trajectory', 'track_barrier_winrate',
            'last_start_position',
            'weight_change', 'horse_first_up_winrate',
            'class_weighted_perf', 'odds_rank',
            'field_avg_implied_prob', 'horse_vs_field', 'field_quality_score',
            'distance_change', 'career_lifetime_winrate',
            'jockey_trainer_combo_winrate',
            'going_specialist_winrate',
            'pace_pressure', 'pace_fit_score',
            'form_volatility',
            'class_speed_proxy',
            'second_up_winrate',
            'market_confidence_residual',
            'early_speed_tendency', 'early_speed_score',
            'predicted_early_position',
            'weight_advantage_kg', 'weight_adjusted_speed',
            'predicted_tempo', 'tempo_fit_score'
        ]
        
        binary_features = [
            'is_sprint', 'is_staying', 'is_mile', 'is_middle',
            'inside_draw', 'wide_draw',
            'light_weight', 'heavy_weight', 'is_favourite', 'is_outsider', 'is_wet',
            'is_small_field', 'is_large_field',
            'is_debutant', 'is_experienced',
            'is_young_horse', 'is_prime_age', 'is_older_horse',
            'is_class_drop', 'is_class_rise',
            'is_recent_winner', 'is_last_start_winner',
            'is_course_distance_winner',
            'is_top_jockey', 'is_top_trainer',
            'is_improving', 'is_declining',
            'is_weight_drop', 'is_weight_rise',
            'is_top3_market',
            'is_distance_drop', 'is_distance_rise',
            'is_strong_combo',
            'is_going_specialist',
            'is_consistent_runner',
            'is_second_up',
            'is_likely_leader'
        ]
        
        selection_features = [
            'distance_strike_rate', 'course_strike_rate',
            'jockey_trainer_strike_rate', 'is_winning_combo',
            'days_since_run', 'is_first_up',
            'ml_adjustment', 'ground_suitability'
        ]
        
        for col in selection_features:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
                numeric_features.append(col)
        
        categorical_features = []
        if HAS_CE:
            if 'jockey' in df.columns:
                categorical_features.append('jockey')
            if 'trainer' in df.columns:
                categorical_features.append('trainer')
            if 'track_region' in df.columns:
                categorical_features.append('track_region')
            if 'distance_category' in df.columns:
                categorical_features.append('distance_category')
        
        all_features = numeric_features + binary_features
        for col in all_features:
            if col not in df.columns:
                df[col] = 0
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        if '_race_key' in df.columns:
            df.drop('_race_key', axis=1, inplace=True)
        
        self.categorical_columns = categorical_features
        self.feature_columns = all_features
        
        return df, all_features
    
    def _calc_barrier_penalty(self, barrier: int, distance: int) -> float:
        """Calculate barrier penalty."""
        if not barrier or barrier <= 0:
            return 0
        
        is_sprint = distance < 1200
        if is_sprint:
            if barrier > 12:
                return 0.15
            elif barrier > 10:
                return 0.08
            elif barrier > 8:
                return 0.03
        else:
            if barrier > 14:
                return 0.08
            elif barrier > 12:
                return 0.04
        return 0
    
    def _parse_class_level(self, race_class: str) -> int:
        """Parse race class to numeric level."""
        if not race_class:
            return 50
        
        race_class = str(race_class).upper()
        
        if 'G1' in race_class or 'GROUP 1' in race_class:
            return 100
        if 'G2' in race_class or 'GROUP 2' in race_class:
            return 95
        if 'G3' in race_class or 'GROUP 3' in race_class:
            return 90
        if 'LISTED' in race_class or 'LR' in race_class:
            return 85
        
        import re
        bm_match = re.search(r'BM\s*(\d+)', race_class)
        if bm_match:
            bm = int(bm_match.group(1))
            return min(80, 40 + bm // 2)
        
        class_match = re.search(r'CL(?:ASS)?\s*(\d+)', race_class)
        if class_match:
            cl = int(class_match.group(1))
            return max(30, 75 - cl * 5)
        
        if 'MDN' in race_class or 'MAIDEN' in race_class:
            return 30
        
        return 50
    
    def train_with_temporal_cv(
        self,
        df: pd.DataFrame,
        target_col: str = 'won',
        n_splits: int = 5,
        calibrate: bool = True
    ) -> Dict[str, Any]:
        """
        Train model with temporal cross-validation.
        Ensures training on past data, validation on future data.
        """
        print("Preparing features...", file=sys.stderr)
        df, feature_columns = self.prepare_features(df)
        
        if 'race_date' in df.columns:
            df['race_date'] = pd.to_datetime(df['race_date'], errors='coerce')
            df = df.sort_values('race_date').reset_index(drop=True)
        
        X = df[feature_columns].values
        y = df[target_col].values.astype(int)
        
        if HAS_CE and self.categorical_columns:
            cat_data = df[self.categorical_columns].fillna('Unknown')
            
            encoder = ce.TargetEncoder(cols=self.categorical_columns)
            cat_encoded = encoder.fit_transform(cat_data, y)
            self.encoders['target'] = encoder
            
            X = np.hstack([X, cat_encoded.values])
            feature_columns = feature_columns + [f'{c}_encoded' for c in self.categorical_columns]
        
        X_scaled = self.scaler.fit_transform(X)
        
        print(f"Training on {len(X)} samples with {len(feature_columns)} features...", file=sys.stderr)
        print(f"Target distribution: {sum(y)} winners ({100*sum(y)/len(y):.1f}%)", file=sys.stderr)
        
        effective_splits = min(n_splits, 3)
        tscv = TimeSeriesSplit(n_splits=effective_splits)
        cv_scores = {'auc': [], 'precision': [], 'recall': [], 'brier': []}
        
        if HAS_XGB:
            base_model = xgb.XGBClassifier(
                n_estimators=150,
                max_depth=6,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_weight=5,
                scale_pos_weight=len(y) / (sum(y) + 1),
                eval_metric='logloss',
                random_state=42,
                n_jobs=-1,
                tree_method='hist'
            )
        elif HAS_LGB:
            base_model = lgb.LGBMClassifier(
                n_estimators=150,
                max_depth=6,
                learning_rate=0.05,
                num_leaves=31,
                class_weight='balanced',
                random_state=42,
                verbose=-1
            )
        else:
            from sklearn.ensemble import GradientBoostingClassifier
            base_model = GradientBoostingClassifier(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.1,
                random_state=42
            )
        
        for fold, (train_idx, val_idx) in enumerate(tscv.split(X_scaled)):
            X_train, X_val = X_scaled[train_idx], X_scaled[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]
            
            base_model.fit(X_train, y_train)
            
            y_pred_proba = base_model.predict_proba(X_val)[:, 1]
            y_pred = (y_pred_proba >= 0.5).astype(int)
            
            if len(np.unique(y_val)) > 1:
                cv_scores['auc'].append(roc_auc_score(y_val, y_pred_proba))
            cv_scores['precision'].append(precision_score(y_val, y_pred, zero_division=0))
            cv_scores['recall'].append(recall_score(y_val, y_pred, zero_division=0))
            cv_scores['brier'].append(brier_score_loss(y_val, y_pred_proba))
        
        base_model.fit(X_scaled, y)
        self.main_model = base_model
        
        if calibrate:
            print("Calibrating probabilities (Isotonic + Platt)...", file=sys.stderr)
            
            calibrated_isotonic = CalibratedClassifierCV(
                base_model, method='isotonic', cv=3
            )
            calibrated_isotonic.fit(X_scaled, y)
            
            calibrated_platt = CalibratedClassifierCV(
                base_model, method='sigmoid', cv=3
            )
            calibrated_platt.fit(X_scaled, y)
            
            iso_proba = calibrated_isotonic.predict_proba(X_scaled)[:, 1]
            platt_proba = calibrated_platt.predict_proba(X_scaled)[:, 1]
            iso_brier = brier_score_loss(y, iso_proba)
            platt_brier = brier_score_loss(y, platt_proba)
            
            print(f"  Isotonic Brier: {iso_brier:.4f}", file=sys.stderr)
            print(f"  Platt Brier:    {platt_brier:.4f}", file=sys.stderr)
            
            if platt_brier < iso_brier:
                self.calibrated_model = calibrated_platt
                self.calibration_method = 'platt'
                print("  Selected: Platt scaling (better calibration)", file=sys.stderr)
            else:
                self.calibrated_model = calibrated_isotonic
                self.calibration_method = 'isotonic'
                print("  Selected: Isotonic regression (better calibration)", file=sys.stderr)
            
            try:
                from sklearn.calibration import calibration_curve
                best_proba = iso_proba if iso_brier <= platt_brier else platt_proba
                prob_true, prob_pred = calibration_curve(y, best_proba, n_bins=10, strategy='uniform')
                self.reliability_diagram = {
                    'prob_true': prob_true.tolist(),
                    'prob_pred': prob_pred.tolist(),
                    'iso_brier': float(iso_brier),
                    'platt_brier': float(platt_brier),
                    'selected_method': self.calibration_method,
                }
                print(f"  Reliability diagram: {len(prob_true)} bins generated", file=sys.stderr)
            except Exception as e:
                print(f"  Reliability diagram error: {e}", file=sys.stderr)
                self.reliability_diagram = None
        
        if HAS_SHAP and HAS_XGB:
            print("Analyzing feature importance with SHAP...", file=sys.stderr)
            try:
                explainer = shap.TreeExplainer(base_model)
                shap_values = explainer.shap_values(X_scaled[:min(1000, len(X_scaled))])
                
                importance = np.abs(shap_values).mean(axis=0)
                self.feature_importance = dict(zip(feature_columns, importance))
                
                self.feature_importance = dict(
                    sorted(self.feature_importance.items(), 
                           key=lambda x: x[1], reverse=True)
                )
            except Exception as e:
                print(f"SHAP analysis failed: {e}", file=sys.stderr)
                self.feature_importance = dict(zip(
                    feature_columns,
                    base_model.feature_importances_ if hasattr(base_model, 'feature_importances_') else [0]*len(feature_columns)
                ))
        
        self.metrics = {
            'auc_roc': float(np.mean(cv_scores['auc'])) if cv_scores['auc'] else 0,
            'precision': float(np.mean(cv_scores['precision'])),
            'recall': float(np.mean(cv_scores['recall'])),
            'brier_score': float(np.mean(cv_scores['brier'])),
            'cv_std': float(np.std(cv_scores['auc'])) if cv_scores['auc'] else 0,
            'calibration_method': getattr(self, 'calibration_method', 'isotonic'),
        }
        
        self.feature_columns = feature_columns
        
        result = {
            'success': True,
            'metrics': self.metrics,
            'feature_importance': dict(list(self.feature_importance.items())[:15]),
            'n_features': len(feature_columns),
            'n_samples': len(X),
            'n_winners': int(sum(y))
        }
        
        if hasattr(self, 'reliability_diagram') and self.reliability_diagram:
            result['reliability_diagram'] = self.reliability_diagram
        
        return result
    
    def train_regional_models(self, df: pd.DataFrame, target_col: str = 'won') -> Dict:
        """Train separate models for each region."""
        results = {}
        for region in ['NSW', 'VIC', 'QLD']:
            region_df = df[df.get('track_region', df.get('track', '').apply(get_track_region)) == region]
            if len(region_df) >= 100 and region_df[target_col].sum() >= 10:
                print(f"Training {region} model on {len(region_df)} samples...", file=sys.stderr)
                
                available_cols = [c for c in self.feature_columns if c in region_df.columns]
                if len(available_cols) < 5:
                    results[region] = {'trained': False, 'reason': 'Insufficient features'}
                    continue
                
                X = region_df[available_cols].fillna(0).values
                y = region_df[target_col].values.astype(int)
                
                from sklearn.preprocessing import StandardScaler
                regional_scaler = StandardScaler()
                X_scaled = regional_scaler.fit_transform(X)
                
                if HAS_XGB:
                    model = xgb.XGBClassifier(
                        n_estimators=100,
                        max_depth=5,
                        learning_rate=0.1,
                        scale_pos_weight=len(y) / (sum(y) + 1),
                        random_state=42,
                        n_jobs=-1
                    )
                else:
                    from sklearn.ensemble import GradientBoostingClassifier
                    model = GradientBoostingClassifier(n_estimators=100, random_state=42)
                
                model.fit(X_scaled, y)
                self.regional_models[region] = model
                
                results[region] = {
                    'n_samples': len(region_df),
                    'n_winners': int(sum(y)),
                    'trained': True
                }
            else:
                results[region] = {'trained': False, 'reason': 'Insufficient data'}
        
        return results
    
    def train_distance_models(self, df: pd.DataFrame, target_col: str = 'won') -> Dict:
        """Train separate models for each distance category."""
        df, _ = self.prepare_features(df)
        
        results = {}
        for dist_cat in ['sprint', 'mile', 'middle', 'staying']:
            dist_df = df[df['distance_category'] == dist_cat]
            if len(dist_df) >= 100 and dist_df[target_col].sum() >= 10:
                print(f"Training {dist_cat} model on {len(dist_df)} samples...", file=sys.stderr)
                
                X = dist_df[self.feature_columns].values
                y = dist_df[target_col].values.astype(int)
                X_scaled = self.scaler.transform(X)
                
                if HAS_XGB:
                    model = xgb.XGBClassifier(
                        n_estimators=100,
                        max_depth=5,
                        learning_rate=0.1,
                        scale_pos_weight=len(y) / (sum(y) + 1),
                        random_state=42,
                        n_jobs=-1
                    )
                else:
                    from sklearn.ensemble import GradientBoostingClassifier
                    model = GradientBoostingClassifier(n_estimators=100, random_state=42)
                
                model.fit(X_scaled, y)
                self.distance_models[dist_cat] = model
                
                results[dist_cat] = {
                    'n_samples': len(dist_df),
                    'n_winners': int(sum(y)),
                    'trained': True
                }
            else:
                results[dist_cat] = {'trained': False, 'reason': 'Insufficient data'}
        
        return results
    
    def predict(
        self,
        df: pd.DataFrame,
        use_calibrated: bool = True,
        use_specialized: bool = True
    ) -> pd.DataFrame:
        """
        Make predictions with optional specialized models.
        Returns DataFrame with predictions.
        """
        df, feature_columns = self.prepare_features(df)
        
        X = df[self.feature_columns].values
        X_scaled = self.scaler.transform(X)
        
        if use_calibrated and self.calibrated_model:
            base_proba = self.calibrated_model.predict_proba(X_scaled)[:, 1]
        else:
            base_proba = self.main_model.predict_proba(X_scaled)[:, 1]
        
        df['win_probability'] = base_proba
        
        if use_specialized:
            for i, row in df.iterrows():
                region = row.get('track_region', 'OTHER')
                if region in self.regional_models:
                    regional_proba = self.regional_models[region].predict_proba(
                        X_scaled[i:i+1]
                    )[0, 1]
                    df.loc[i, 'win_probability'] = 0.6 * base_proba[i] + 0.4 * regional_proba
                
                dist_cat = row.get('distance_category', 'middle')
                if dist_cat in self.distance_models:
                    dist_proba = self.distance_models[dist_cat].predict_proba(
                        X_scaled[i:i+1]
                    )[0, 1]
                    current = df.loc[i, 'win_probability']
                    df.loc[i, 'win_probability'] = 0.7 * current + 0.3 * dist_proba
        
        return df
    
    def save(self, filename: str = 'enhanced_racing_ensemble.pkl'):
        """Save all models and encoders."""
        filepath = os.path.join(self.model_dir, filename)
        
        save_data = {
            'main_model': self.main_model,
            'calibrated_model': self.calibrated_model,
            'calibration_method': self.calibration_method,
            'reliability_diagram': self.reliability_diagram,
            'regional_models': self.regional_models,
            'distance_models': self.distance_models,
            'scaler': self.scaler,
            'encoders': self.encoders,
            'feature_columns': self.feature_columns,
            'categorical_columns': self.categorical_columns,
            'feature_importance': self.feature_importance,
            'metrics': self.metrics
        }
        
        with open(filepath, 'wb') as f:
            pickle.dump(save_data, f)
        
        return filepath
    
    def load(self, filename: str = 'enhanced_racing_ensemble.pkl') -> bool:
        """Load saved models."""
        filepath = os.path.join(self.model_dir, filename)
        
        if not os.path.exists(filepath):
            return False
        
        try:
            with open(filepath, 'rb') as f:
                data = pickle.load(f)
            
            self.main_model = data.get('main_model')
            self.calibrated_model = data.get('calibrated_model')
            self.calibration_method = data.get('calibration_method', 'isotonic')
            self.reliability_diagram = data.get('reliability_diagram')
            self.regional_models = data.get('regional_models', {})
            self.distance_models = data.get('distance_models', {})
            self.scaler = data.get('scaler', StandardScaler())
            self.encoders = data.get('encoders', {})
            self.feature_columns = data.get('feature_columns', [])
            self.categorical_columns = data.get('categorical_columns', [])
            self.feature_importance = data.get('feature_importance', {})
            self.metrics = data.get('metrics', {})
            
            return True
        except Exception as e:
            print(f"Failed to load model: {e}", file=sys.stderr)
            return False


def get_dynamic_threshold(field_size: int, base_threshold: float = 0.12) -> float:
    """
    Calculate dynamic win probability threshold based on field size.
    Larger fields naturally dilute win probabilities.
    """
    if field_size <= 6:
        return base_threshold * 1.3  # Higher bar for small fields
    elif field_size <= 10:
        return base_threshold
    elif field_size <= 14:
        return base_threshold * 0.85
    else:
        return base_threshold * 0.70  # Lower bar for very large fields


def main():
    """Main training script."""
    try:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            print(json.dumps({'success': False, 'error': 'DATABASE_URL not set'}))
            return
        
        from sqlalchemy import create_engine, text
        engine = create_engine(db_url)
        
        # Primary source: runner-level outcomes (all runners, not only selected bets)
        runner_level_query = """
        WITH dedup AS (
            SELECT
                pa.*,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        LOWER(COALESCE(pa.track, '')),
                        COALESCE(pa.race_number, 0),
                        COALESCE(pa.race_date, ''),
                        LOWER(COALESCE(pa.horse_name, ''))
                    ORDER BY
                        COALESCE(pa.result_collected_at, pa.created_at) DESC,
                        pa.created_at DESC
                ) AS rn
            FROM prediction_audit pa
            WHERE COALESCE(pa.result_status, 'pending') = 'resulted'
              AND pa.actual_position IS NOT NULL
        )
        SELECT
            COALESCE(rrh.race_id, r.race_id, CONCAT(d.track, '_', d.race_date, '_R', d.race_number)) AS race_id,
            d.track,
            d.race_number,
            d.race_date,
            COALESCE(CAST(rrh.distance_m AS TEXT), r.distance, '') AS distance,
            COALESCE(r.going, rrh.going, '') AS going,
            COALESCE(r.race_class, rrh.race_class, '') AS race_class,
            d.horse_name,
            NULL::text AS horse_number,
            COALESCE(rrh.barrier, 0) AS barrier,
            COALESCE(rrh.weight_kg, 55) AS weight,
            COALESCE(rrh.jockey, '') AS jockey,
            ''::text AS trainer,
            COALESCE(d.market_odds, d.starting_price, 0) AS market_odds,
            d.starting_price,
            CASE WHEN d.won THEN 1 ELSE 0 END AS won,
            CASE WHEN d.placed THEN 1 ELSE 0 END AS placed,
            d.actual_position,
            ''::text AS form,
            0.0::double precision AS distance_strike_rate,
            0.0::double precision AS course_strike_rate,
            0.0::double precision AS weighted_form_score,
            0 AS is_first_up,
            0 AS is_second_up,
            30 AS days_since_run,
            0.0::double precision AS jockey_trainer_strike_rate,
            0 AS is_winning_combo,
            1.0::double precision AS ml_adjustment,
            0.0::double precision AS ground_suitability
        FROM dedup d
        LEFT JOIN race_results_history rrh
            ON LOWER(rrh.track) = LOWER(d.track)
           AND rrh.race_number = d.race_number
           AND rrh.race_date = d.race_date
           AND LOWER(rrh.horse_name) = LOWER(d.horse_name)
        LEFT JOIN races r
            ON LOWER(r.track) = LOWER(d.track)
           AND r.race_number = d.race_number
           AND r.race_date = d.race_date
        WHERE d.rn = 1
        ORDER BY d.race_date
        """

        legacy_query = """
        SELECT
            t.race_id,
            t.track,
            t.race_number,
            t.race_date,
            t.distance,
            t.going,
            t.race_class,
            t.horse_name,
            t.horse_number,
            t.barrier,
            t.weight,
            t.jockey,
            t.trainer,
            t.market_odds,
            t.starting_price,
            t.won,
            t.placed,
            t.actual_position,
            s.form,
            COALESCE(s.distance_strike_rate, 0) as distance_strike_rate,
            COALESCE(s.course_strike_rate, 0) as course_strike_rate,
            COALESCE(s.weighted_form_score, 0) as weighted_form_score,
            CASE WHEN s.is_first_up THEN 1 ELSE 0 END as is_first_up,
            CASE WHEN s.is_second_up THEN 1 ELSE 0 END as is_second_up,
            COALESCE(s.days_since_run, 30) as days_since_run,
            COALESCE(s.jockey_trainer_strike_rate, 0) as jockey_trainer_strike_rate,
            CASE WHEN s.is_winning_combo THEN 1 ELSE 0 END as is_winning_combo,
            COALESCE(s.ml_adjustment, 1.0) as ml_adjustment,
            COALESCE(s.ground_suitability, 0) as ground_suitability
        FROM training_data t
        LEFT JOIN selections s ON t.selection_id = s.id
        WHERE t.actual_position IS NOT NULL
        ORDER BY t.race_date
        """

        with engine.connect() as conn:
            source_table = "prediction_audit"
            try:
                df = pd.read_sql(text(runner_level_query), conn)
                tmp_won = pd.to_numeric(df.get('won', 0), errors='coerce').fillna(0).astype(int)
                if len(df) < 50 or int(tmp_won.sum()) < 5:
                    raise ValueError(
                        f"runner-level data insufficient (rows={len(df)}, winners={int(tmp_won.sum())})"
                    )
            except Exception as source_err:
                print(f"[Train] Falling back to legacy training_data source: {source_err}", file=sys.stderr)
                df = pd.read_sql(text(legacy_query), conn)
                source_table = "training_data"
        
        if len(df) < 50:
            print(json.dumps({
                'success': False,
                'error': f'Insufficient data: {len(df)} records (need 50+)'
            }))
            return
        
        df['won'] = pd.to_numeric(df['won'], errors='coerce').fillna(0).astype(int)
        df['placed'] = pd.to_numeric(df['placed'], errors='coerce').fillna(0).astype(int)
        
        winners = int(df['won'].sum())
        if winners < 5:
            print(json.dumps({
                'success': False,
                'error': f'Insufficient winners: {winners} (need 5+)'
            }))
            return
        
        if 'form' in df.columns and 'weighted_form_score' in df.columns:
            mask = df['weighted_form_score'] == 0
            df.loc[mask, 'weighted_form_score'] = df.loc[mask, 'form'].apply(calculate_form_score)
        
        print(f"Training enhanced model on {len(df)} records with {winners} winners (source={source_table})...", file=sys.stderr)
        
        model = EnhancedRacingMLModel()
        
        main_result = model.train_with_temporal_cv(
            df, target_col='won', n_splits=5, calibrate=True
        )
        
        df_prepared, _ = model.prepare_features(df)
        
        regional_result = {}
        try:
            for region in ['NSW', 'VIC', 'QLD']:
                region_df = df_prepared[df_prepared['track_region'] == region]
                if len(region_df) >= 100 and region_df['won'].sum() >= 10:
                    regional_result[region] = {
                        'n_samples': len(region_df),
                        'n_winners': int(region_df['won'].sum()),
                        'trained': False,
                        'reason': 'Skipped for simplicity'
                    }
                else:
                    regional_result[region] = {'trained': False, 'reason': 'Insufficient data'}
        except Exception as e:
            regional_result = {'error': str(e)}
        
        distance_result = {}
        try:
            for dist_cat in ['sprint', 'mile', 'middle', 'staying']:
                dist_df = df_prepared[df_prepared['distance_category'] == dist_cat]
                if len(dist_df) >= 100:
                    distance_result[dist_cat] = {
                        'n_samples': len(dist_df),
                        'n_winners': int(dist_df['won'].sum()) if 'won' in dist_df.columns else 0,
                        'trained': False,
                        'reason': 'Skipped for simplicity'
                    }
        except Exception as e:
            distance_result = {'error': str(e)}
        
        model_path = model.save()
        
        result = {
            'success': True,
            'model_path': model_path,
            'main_model': main_result,
            'regional_models': regional_result,
            'distance_models': distance_result,
            'training_stats': {
                'source_table': source_table,
                'total_records': len(df),
                'winners': winners,
                'placed': int(df['placed'].sum()),
                'date_range': {
                    'start': str(df['race_date'].min()) if len(df) > 0 else None,
                    'end': str(df['race_date'].max()) if len(df) > 0 else None
                }
            }
        }
        
        def convert_numpy(obj):
            if isinstance(obj, dict):
                return {k: convert_numpy(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_numpy(v) for v in obj]
            elif isinstance(obj, (np.floating, np.float32, np.float64)):
                return float(obj)
            elif isinstance(obj, (np.integer, np.int32, np.int64)):
                return int(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            return obj
        
        print(json.dumps(convert_numpy(result)))
        
    except Exception as e:
        import traceback
        print(json.dumps({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }))


if __name__ == '__main__':
    main()
