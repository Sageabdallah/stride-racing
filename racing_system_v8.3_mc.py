#!/usr/bin/env python3
"""
Horse Racing System v8.3_mc — Monte Carlo integrated edition.

Adds Monte Carlo race simulation on top of v8.2: Plackett-Luce / Gumbel sampling
of finishing orders, uncertainty-aware Dirichlet perturbation, pace/bias scenario
variation with stability metrics, and an expanded MC tip menu (--mc_tips6).
"""

import json
import os
import re
import math
import argparse
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from collections import defaultdict, Counter

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.calibration import CalibratedClassifierCV
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


BASELINE_WIN_RATE = 0.097


def _flag_enabled(name):
    """Env-flag convention: default OFF unless explicitly true/1/yes.

    The STRIDE_MC_FIX_* flags gate the #124 engine-defect fixes (audit
    2026-08-07). Every one of them changes published probabilities or the
    gates on them, so each ships OFF and is flipped one at a time, validated
    with common-random-numbers A/B against settled results — see #124's
    sequencing. Flag off, behaviour is the shipped engine's, bug for bug.
    """
    return os.environ.get(name, "false").strip().lower() in ("true", "1", "yes")
PRIOR_STRENGTH = 20
PRIOR_STRENGTH_SMALL = 30

# Dynamic Kelly - adjusted by confidence level
KELLY_FRACTIONS = {
    'HIGH': 0.30,
    'MEDIUM': 0.20,
    'LOWER': 0.10,
}
KELLY_FRACTION_DEFAULT = 0.25
MAX_KELLY_STAKE = 0.05  # Cap at 5% bankroll per bet

# Time decay half-life in days
TIME_DECAY_HALF_LIFE = 90

FEATURE_WEIGHTS = {
    'cd_history': 0.12,
    'course_history': 0.07,
    'distance_history': 0.05,
    'recent_form': 0.14,
    'last_start': 0.10,
    'last_margin': 0.05,
    'speed_figure': 0.10,
    'track_bias': 0.08,
    'late_speed': 0.05,
    'connections': 0.06,
    'class_rating': 0.05,
    'going': 0.02,
    'first_up': 0.03,
    'beaten_margins': 0.01,
    'barrier': 0.02,
    'trajectory': 0.03,
    'weight_relief': 0.02,
}

SOFTMAX_TEMPERATURE = 12
MIN_ODDS = 2.00
MAX_ODDS = 15.0

ELITE_JOCKEYS = {
    'w pike': {'default': 0.22, 'ascot': 0.28},
    'william pike': {'default': 0.22, 'ascot': 0.28},
    'j mcdonald': {'default': 0.18, 'randwick': 0.22},
    'james mcdonald': {'default': 0.18, 'randwick': 0.22},
    'c williams': {'default': 0.16, 'flemington': 0.20},
    'craig williams': {'default': 0.16, 'flemington': 0.20},
    'd oliver': {'default': 0.16, 'flemington': 0.18},
    'h bowman': {'default': 0.17, 'randwick': 0.20},
    'k mcevoy': {'default': 0.15, 'flemington': 0.18},
    't berry': {'default': 0.15, 'randwick': 0.17},
    'd lane': {'default': 0.16, 'randwick': 0.18},
}

TRACK_PROFILES = {
    'ascot': {'leader_rate': 0.985},
    'eagle farm': {'leader_rate': 0.83},
    'warwick farm': {'leader_rate': 0.78},
    'moonee valley': {'leader_rate': 0.70},
    'canterbury': {'leader_rate': 0.65},
    'caulfield': {'leader_rate': 0.64},
    'sandown': {'leader_rate': 0.55},
    'belmont': {'leader_rate': 0.52},
    'doomben': {'leader_rate': 0.50},
    'randwick': {'leader_rate': 0.48},
    'royal randwick': {'leader_rate': 0.48},
    'rosehill': {'leader_rate': 0.45},
    'kensington': {'leader_rate': 0.48},
    'randwick kensington': {'leader_rate': 0.48},
    'pinjarra': {'leader_rate': 0.32},
    'flemington': {'leader_rate': 0.18},
    'gold coast': {'leader_rate': 0.35},
    'aquis park gold coast': {'leader_rate': 0.35},
}

BARRIER_PROFILES = {
    'sprint': {'inside_boost': 15, 'outside_penalty': -20},   # <1100m - inside crucial
    'short': {'inside_boost': 12, 'outside_penalty': -15},    # 1100-1400m
    'mile': {'inside_boost': 8, 'outside_penalty': -10},      # 1400-1800m
    'middle': {'inside_boost': 5, 'outside_penalty': -8},     # 1800-2200m
    'staying': {'inside_boost': 3, 'outside_penalty': -5},    # >2200m - less important
}

STAKING_TIERS = {
    'lock': {'min_confidence': 70, 'min_prob': 18, 'units': 3, 'stars': '⭐⭐⭐', 'label': 'LOCK'},
    'strong': {'min_confidence': 55, 'min_prob': 14, 'units': 2, 'stars': '⭐⭐', 'label': 'STRONG BET'},
    'bet': {'min_confidence': 40, 'min_prob': 10, 'units': 1, 'stars': '⭐', 'label': 'BET'},
}

TARGET_TRACKS = [
    "Flemington", "Caulfield", "Moonee Valley", "Sandown",
    "Randwick", "Royal Randwick", "Rosehill", "Warwick Farm", "Canterbury",
    "Eagle Farm", "Doomben", "Ascot", "Pinjarra", "Belmont",
    "Kensington", "Randwick Kensington", "Gold Coast", "Aquis Park Gold Coast",
    "Geelong", "Ladbrokes Geelong", "Ballarat", "Ipswich",
]

TIPS_PER_TRACK = 6

STAKING_CONFIG = {
    'default_bankroll': 100.0,
    'max_daily_units': 30,
    'unit_percent': 0.01,
    'lower_confidence_threshold': 40,
}

MC_DEFAULTS = {
    'mc_sims': 20000,
    'seed': 42,
    'mc_model': 'plackett_luce',   # 'plackett_luce' | 'gumbel' | 'dirichlet'
    'pace_mode': 'basic',          # 'off' | 'basic'
    'uncertainty': 'on',           # 'off' | 'on'
    'risk_mode': 'fractional_kelly',  # 'kelly' | 'fractional_kelly' | 'flat'
}

MC_SIM_LIMITS = {
    'min_prob': 0.001,
    'max_prob': 0.70,
    'ci_alpha': 0.10,
}

MC_STAKING_CAPS = {
    'per_track_units': 12,
}


def safe_float(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except:
        return default


def safe_int(val, default=0):
    try:
        return int(val) if val is not None else default
    except:
        return default


def safe_parse_date(date_str):
    if not date_str:
        return None
    if isinstance(date_str, datetime):
        return date_str
    try:
        s = str(date_str).strip()
    except Exception:
        return None
    if not s:
        return None
    if len(s) >= 10 and s[4] == '-' and s[7] == '-':
        try:
            return datetime.strptime(s[:10], '%Y-%m-%d')
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        pass
    for fmt in ['%Y-%m-%d', '%d/%m/%Y']:
        try:
            return datetime.strptime(date_str, fmt)
        except:
            continue
    return None


def days_between(date_earlier, date_later):
    """Calculate days between two dates."""
    d1 = safe_parse_date(date_earlier)
    d2 = safe_parse_date(date_later)
    if not d1 or not d2:
        return None
    try:
        return (d2.date() - d1.date()).days
    except Exception:
        return None


def time_weight(days_ago, half_life_days=TIME_DECAY_HALF_LIFE):
    """
    Calculate time decay weight using exponential decay.
    Inspired by Elo ratings — recent form weighs more heavily.
    Weight halves every half_life_days.
    """
    try:
        d = max(0.0, float(days_ago))
    except Exception:
        d = 0.0
    if half_life_days <= 0:
        return 1.0
    return math.exp(-math.log(2.0) * (d / float(half_life_days)))


def layoff_score_gaussian(days_since_last_run, peak_days=30, sigma=20):
    """
    Calculate fitness score based on days since last run using Gaussian curve.
    Backed by veterinary studies on muscle recovery.
    Peak fitness around 30 days, with decay on either side.
    """
    if days_since_last_run is None:
        return 50
    days = max(0, days_since_last_run)
    # Gaussian centered at peak_days
    score = 50 + 30 * math.exp(-((days - peak_days) ** 2) / (2 * sigma ** 2))
    # Additional penalties
    if days < 14:
        score -= 10  # Fatigue risk
    if days > 90:
        score -= 15  # Rust
    if days > 120:
        score -= 10  # Extended layoff
    return max(20, min(80, score))


def parse_going_rating(going):
    """
    Parse going description into numeric scale (0-10).
    0 = Good/Firm/Fast, 10 = Heavy.
    Allows for linear interpolation between conditions.
    """
    if not going:
        return 0.0
    g = str(going).strip().lower()
    if not g:
        return 0.0
    # Try to parse specific ratings like "Good 3" or "Soft 7"
    m = re.search(r'(good|soft|heavy)\s*([0-9]+)', g)
    if m:
        label = m.group(1)
        try:
            num = float(m.group(2))
        except Exception:
            num = None
        if num is not None:
            if label == 'good':
                # Good 1-4 maps to 0-1
                rating = max(0.0, min(4.0, num)) / 4.0
                return max(0.0, min(10.0, rating))
            if label == 'soft':
                # Soft 5-7 maps to 5-7
                return max(5.0, min(7.0, num))
            if label == 'heavy':
                # Heavy 8-10 maps to 8-10
                return max(8.0, min(10.0, num))
    # Fallback keyword matching
    if 'heavy' in g:
        return 9.0
    if 'soft' in g:
        return 6.0
    if any(w in g for w in ['wet', 'slow']):
        return 6.0
    if any(w in g for w in ['good', 'firm', 'fast']):
        return 0.0
    return 0.0


def bayesian_rate(wins, runs, prior_strength=PRIOR_STRENGTH, base=BASELINE_WIN_RATE):
    """Calculate Bayesian-adjusted win rate to prevent overfitting on small samples."""
    if runs == 0:
        return base
    alpha = prior_strength * base + wins
    beta = prior_strength * (1 - base) + (runs - wins)
    return alpha / (alpha + beta)


def confidence_score(trials, prior_strength=PRIOR_STRENGTH):
    """Calculate confidence in estimate based on sample size."""
    return trials / (trials + prior_strength)


def expected_value(prob, odds):
    """Calculate expected value of a bet."""
    if odds <= 1 or prob <= 0:
        return -1.0
    return prob * odds - 1.0


def kelly_stake(prob, odds, fraction=KELLY_FRACTION_DEFAULT, max_stake=MAX_KELLY_STAKE):
    """
    Calculate Kelly criterion stake with fraction and cap.
    Cap at max_stake to avoid ruin (from Kelly's original paper).
    """
    if odds <= 1 or prob <= 0 or prob >= 1:
        return 0
    edge = prob * (odds - 1) - (1 - prob)
    if edge <= 0:
        return 0
    kelly = (edge / (odds - 1)) * fraction
    return max(0, min(max_stake, kelly))


def dynamic_kelly_stake(prob, odds, confidence_level):
    """Calculate Kelly stake with dynamic fraction based on confidence."""
    fraction = KELLY_FRACTIONS.get(confidence_level, KELLY_FRACTION_DEFAULT)
    return kelly_stake(prob, odds, fraction, MAX_KELLY_STAKE)


def wilson_interval(successes, n, alpha=0.10):
    """Wilson score interval for a Bernoulli proportion."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.6448536269514722  # approx for 90% (two-sided)
    phat = successes / n
    denom = 1 + z ** 2 / n
    centre = phat + z ** 2 / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z ** 2 / (4 * n)) / n)
    lower = (centre - margin) / denom
    upper = (centre + margin) / denom
    return max(0.0, lower), min(1.0, upper)


def soft_clip_prob(p, pmin=MC_SIM_LIMITS['min_prob'], pmax=MC_SIM_LIMITS['max_prob']):
    return max(pmin, min(pmax, p))


def scenario_adjustments(styles, leader_rate, pace_regime):
    """
    Compute pace/bias adjustment per runner given styles and scenario.
    pace_regime: 'slow' | 'even' | 'fast' | 'melt'
    """
    n = len(styles)
    adj = np.zeros(n)
    leader_bias = (leader_rate - 0.5) * 0.35  # damped
    for i, style in enumerate(styles):
        s = style or 'midfield'
        s_lower = s.lower()
        if 'leader' in s_lower:
            base = 0.04
        elif 'on_pace' in s_lower or 'on pace' in s_lower:
            base = 0.02
        elif 'backmarker' in s_lower or 'back' in s_lower:
            base = -0.02
        else:
            base = 0.0
        
        # Pace regime impacts
        if pace_regime == 'slow':
            base += 0.04 if 'leader' in s_lower else (0.01 if 'on_pace' in s_lower else -0.03)
        elif pace_regime == 'fast':
            base += -0.02 if 'leader' in s_lower else (0.02 if 'mid' in s_lower else 0.01)
        elif pace_regime == 'melt':
            base += -0.05 if 'leader' in s_lower else (0.04 if 'back' in s_lower else 0.02)
        
        adj[i] = base + leader_bias
    return adj


def stability_from_positions(std_pos, ci_width):
    """Map position volatility and CI width to a 0-100 stability score."""
    return float(max(0, min(100, 100 - (std_pos - 0.6) * 40 - ci_width * 320)))


def get_distance_bucket(distance):
    """Categorize distance into buckets."""
    if distance < 1100:
        return 'sprint'
    elif distance < 1400:
        return 'short'
    elif distance < 1800:
        return 'mile'
    elif distance < 2200:
        return 'middle'
    return 'staying'


def get_field_size_tertile(field_size):
    """Categorize field size into tertiles."""
    if field_size <= 8:
        return 'small'
    elif field_size <= 12:
        return 'medium'
    return 'large'


def extract_running_style(comment):
    """Extract running style from race comment."""
    if not comment:
        return 'unknown'
    c = comment.lower()
    if any(w in c for w in ['led', 'made all', 'led throughout']):
        return 'leader'
    if any(w in c for w in ['settled second', 'prominent', 'handy', 'stalked pace', 'tracked leader']):
        return 'on_pace'
    if any(w in c for w in ['midfield', 'mid-division']):
        return 'midfield'
    if any(w in c for w in ['settled rear', 'back', 'last', 'held up']):
        return 'backmarker'
    return 'midfield'


def extract_late_speed(comment):
    """Extract late speed indicator from race comment."""
    if not comment:
        return 'neutral'
    c = comment.lower()
    if any(w in c for w in ['ran on', 'kept finding', 'strong finish', 'finished well', 'closing']):
        return 'ran_on'
    if any(w in c for w in ['faded', 'weakened', 'tired', 'dropped out']):
        return 'faded'
    return 'neutral'


def parse_class_level(class_str):
    """Parse race class into numeric level (0-100)."""
    if not class_str:
        return 50
    c = class_str.lower()
    if 'group 1' in c or 'g1' in c:
        return 100
    elif 'group 2' in c or 'g2' in c:
        return 90
    elif 'group 3' in c or 'g3' in c:
        return 80
    elif 'listed' in c:
        return 75
    elif 'bm' in c or 'benchmark' in c:
        nums = re.findall(r'\d+', c)
        if nums:
            return min(70, 30 + int(nums[0]) // 2)
    elif 'maiden' in c:
        return 30
    elif 'class' in c:
        nums = re.findall(r'\d+', c)
        if nums:
            return min(70, 40 + int(nums[0]) * 5)
    return 50


def is_trial_race(race_dict):
    """Check if race is a trial/jump-out."""
    if race_dict.get('is_trial') or race_dict.get('is_jump_out'):
        return True
    race_class = str(race_dict.get('class', '') or '').upper()
    if 'TRL' in race_class or '-BT' in race_class or race_class == 'BT':
        return True
    race_name = str(race_dict.get('race_name', '') or '').lower()
    return 'trial' in race_name or 'jump out' in race_name


def calculate_trajectory(figures, n_recent=5):
    """
    Calculate improvement trajectory using linear regression on recent speed figures.
    Returns slope * 10 as trajectory score.
    Positive = improving, Negative = declining.
    """
    if not figures or len(figures) < 3:
        return 0.0
    recent = figures[-n_recent:]
    if len(recent) < 3:
        return 0.0
    try:
        x = np.arange(len(recent))
        slope = np.polyfit(x, recent, 1)[0]
        return slope * 10  # Scale for scoring
    except:
        return 0.0


def calculate_weight_relief(current_weight, last_weight):
    """
    Calculate weight relief score.
    ~0.3 lengths improvement per kg dropped (empirical).
    """
    if not current_weight or not last_weight:
        return 50
    diff = last_weight - current_weight
    if diff >= 3:
        return 70  # Significant drop
    elif diff >= 2:
        return 65
    elif diff >= 1:
        return 58
    elif diff <= -3:
        return 35  # Significant rise
    elif diff <= -2:
        return 40
    elif diff <= -1:
        return 45
    return 50



@dataclass
class Runner:
    horse: str
    number: int = 0
    horse_id: str = ''
    age: int = 0
    sex: str = ''
    weight: float = 0.0
    barrier: int = 0
    jockey: str = ''
    jockey_id: str = ''
    trainer: str = ''
    trainer_id: str = ''
    sire: str = ''
    sire_id: str = ''
    dam_sire: str = ''
    position: int = 0
    margin: float = 0.0
    sp: float = 0.0
    comment: str = ''
    form: str = ''
    
    @classmethod
    def from_dict(cls, d):
        age = 0
        if d.get('age'):
            nums = re.findall(r'\d+', str(d['age']))
            if nums:
                age = int(nums[0])
        
        number = safe_int(d.get('tab_no') or d.get('number') or d.get('saddle_cloth'), 0)
        barrier = safe_int(d.get('draw') or d.get('barrier'), 0)
        
        sp = 0.0
        try:
            sp = float(d.get('sp') or d.get('fixed_odds') or d.get('win_odds') or d.get('price') or 0)
            if not sp and d.get('odds'):
                odds_list = d.get('odds', [])
                if isinstance(odds_list, list) and odds_list:
                    first = odds_list[0]
                    if isinstance(first, dict):
                        sp = float(first.get('win_odds') or first.get('odds') or 0)
        except:
            pass
        
        return cls(
            horse=d.get('horse', ''),
            number=number,
            horse_id=d.get('horse_id', ''),
            age=age,
            sex=d.get('sex', ''),
            weight=safe_float(d.get('weight'), 0.0),
            barrier=barrier,
            jockey=d.get('jockey', ''),
            jockey_id=d.get('jockey_id', ''),
            trainer=d.get('trainer', ''),
            trainer_id=d.get('trainer_id', ''),
            sire=d.get('sire', ''),
            sire_id=d.get('sire_id', ''),
            dam_sire=d.get('dam_sire') or d.get('damsire', ''),
            position=safe_int(d.get('position'), 0),
            margin=safe_float(d.get('margin'), 0.0),
            sp=sp,
            comment=d.get('comment', '') or '',
            form=d.get('form', '') or '',
        )


@dataclass
class Race:
    race_id: str = ''
    course: str = ''
    date: str = ''
    date_dt: Optional[datetime] = None
    race_number: int = 0
    race_name: str = ''
    distance: int = 0
    going: str = ''
    class_level: int = 0
    prize: int = 0
    off_time: str = ''
    runners: List[Runner] = field(default_factory=list)
    
    @property
    def is_valid(self):
        return len(self.runners) >= 2 and self.distance > 0
    
    @property
    def going_rating(self):
        return parse_going_rating(self.going)
    
    @property
    def is_wet(self):
        going = self.going.lower() if self.going else ''
        if any(w in going for w in ['heavy', 'soft', 'wet', 'slow']):
            return True
        return self.going_rating >= 5.0
    
    @property
    def field_size(self):
        return len(self.runners)
    
    @property
    def distance_bucket(self):
        return get_distance_bucket(self.distance)
    
    @property
    def field_tertile(self):
        return get_field_size_tertile(self.field_size)
    
    @classmethod
    def from_dict(cls, d):
        distance = 0
        try:
            dist_str = str(d.get('distance', '0'))
            nums = re.findall(r'\d+', dist_str.replace(',', ''))
            if nums:
                distance = int(nums[0])
        except:
            pass
        
        runners = [Runner.from_dict(r) for r in d.get('runners', [])
                   if not r.get('scratched')]

        off_time = d.get('off_time', '') or d.get('time', '') or ''
        date_raw = d.get('date') or d.get('meet_date') or (str(off_time)[:10] if off_time else '')
        date_dt = safe_parse_date(date_raw)
        date_str = date_dt.date().isoformat() if date_dt else (str(date_raw) if date_raw else '')
        
        return cls(
            race_id=d.get('race_id', ''),
            course=d.get('course', ''),
            date=date_str,
            date_dt=date_dt,
            race_number=safe_int(d.get('race_number') or d.get('number'), 0),
            race_name=d.get('race_name', ''),
            distance=distance,
            going=d.get('going', ''),
            class_level=parse_class_level(str(d.get('class', '') or d.get('race_class', ''))),
            prize=safe_int(d.get('prize'), 0),
            off_time=off_time,
            runners=runners,
        )



class SupplementaryStats:
    def __init__(self, filepath=None):
        self.jockey_stats = {}
        self.trainer_stats = {}
        self.jockey_trainer_combos = {}
        self.sire_stats = {}
        self.track_bias = {}
        self.horse_stats = {}
        self.loaded = False
        if filepath:
            self.load(filepath)
    
    def load(self, filepath):
        path = Path(filepath)
        if not path.exists():
            print(f"  ⚠ Stats file not found: {filepath}")
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self.jockey_stats = data.get('jockey_stats', {})
            self.trainer_stats = data.get('trainer_stats', {})
            self.jockey_trainer_combos = data.get('jockey_trainer_combos', {})
            self.sire_stats = data.get('sire_stats', {})
            self.track_bias = data.get('track_bias', {})
            self.horse_stats = data.get('horse_stats', {})
            self.loaded = True
            meta = data.get('_metadata', {})
            print(f"  ✓ Loaded stats: {meta.get('unique_jockeys', 0)} jockeys, "
                  f"{meta.get('unique_trainers', 0)} trainers, "
                  f"{meta.get('horses_with_2plus_runs', 0)} horses")
        except Exception as e:
            print(f"  ⚠ Could not load stats: {e}")
    
    def get_jockey_course_rate(self, jockey_id, course):
        jockey = self.jockey_stats.get(jockey_id, {})
        if not jockey:
            return BASELINE_WIN_RATE, 0.0
        course_stats = jockey.get('course_stats', {}).get(course.lower(), {})
        wins, runs = course_stats.get('wins', 0), course_stats.get('runs', 0)
        if runs == 0:
            wins, runs = jockey.get('total_wins', 0), jockey.get('total_runs', 0)
        return bayesian_rate(wins, runs), confidence_score(runs)
    
    def get_trainer_course_rate(self, trainer_id, course):
        trainer = self.trainer_stats.get(trainer_id, {})
        if not trainer:
            return BASELINE_WIN_RATE, 0.0
        course_stats = trainer.get('course_stats', {}).get(course.lower(), {})
        wins, runs = course_stats.get('wins', 0), course_stats.get('runs', 0)
        if runs == 0:
            wins, runs = trainer.get('total_wins', 0), trainer.get('total_runs', 0)
        return bayesian_rate(wins, runs), confidence_score(runs)
    
    def get_combo_rate(self, jockey_id, trainer_id):
        combo = self.jockey_trainer_combos.get(f"{jockey_id}_{trainer_id}", {})
        if not combo:
            combo = self.jockey_trainer_combos.get(f"{jockey_id}|{trainer_id}", {})
        if not combo:
            return BASELINE_WIN_RATE, 0.0
        wins, runs = combo.get('wins', 0), combo.get('runs', 0)
        return bayesian_rate(wins, runs, PRIOR_STRENGTH_SMALL), confidence_score(runs, PRIOR_STRENGTH_SMALL)
    
    def get_sire_going_rate(self, sire_id, is_wet):
        sire = self.sire_stats.get(sire_id, {})
        if not sire:
            return BASELINE_WIN_RATE, 0.0
        going_key = 'wet' if is_wet else 'dry'
        going_stats = sire.get('going_stats', {}).get(going_key, {})
        wins, runs = going_stats.get('wins', 0), going_stats.get('runs', 0)
        return bayesian_rate(wins, runs, PRIOR_STRENGTH_SMALL), confidence_score(runs, PRIOR_STRENGTH_SMALL)
    
    def get_track_leader_rate(self, course):
        bias = self.track_bias.get(course.lower(), {})
        return bias.get('leader_win_rate') if bias else None
    
    def get_horse_stats(self, horse_id):
        return self.horse_stats.get(horse_id, {})



def load_all_races(paths, include_trials=False, verbose=True):
    """Load races from JSON files with validation."""
    races = []
    trials_skipped = 0
    files_found = 0
    invalid_skipped = 0
    
    for path_str in paths:
        path = Path(path_str)
        if not path.exists():
            if verbose:
                print(f"  ⚠ Path not found: {path_str}")
            continue
        
        files = [path] if path.is_file() else list(path.glob("**/*.json"))
        
        for filepath in files:
            if filepath.name.startswith('._') or 'supplementary' in filepath.name.lower():
                continue
            files_found += 1
            
            try:
                with open(filepath) as f:
                    data = json.load(f)
                
                if isinstance(data, list):
                    for item in data:
                        if 'runners' in item:
                            dist = item.get('distance', 0)
                            if isinstance(dist, str):
                                nums = re.findall(r'\d+', dist.replace(',', ''))
                                dist = int(nums[0]) if nums else 0
                            if dist and dist < 400:
                                invalid_skipped += 1
                                continue
                            if include_trials or not is_trial_race(item):
                                races.append(item)
                            else:
                                trials_skipped += 1
                        elif 'races' in item:
                            for r in item['races']:
                                r['course'] = r.get('course') or item.get('course')
                                r['date'] = r.get('date') or item.get('date')
                                if include_trials or not is_trial_race(r):
                                    races.append(r)
                                else:
                                    trials_skipped += 1
                elif isinstance(data, dict):
                    if 'runners' in data:
                        if include_trials or not is_trial_race(data):
                            races.append(data)
                        else:
                            trials_skipped += 1
                    elif 'races' in data:
                        for r in data['races']:
                            r['course'] = r.get('course') or data.get('course')
                            r['date'] = r.get('date') or data.get('date')
                            if include_trials or not is_trial_race(r):
                                races.append(r)
                            else:
                                trials_skipped += 1
            except Exception as e:
                if verbose:
                    print(f"  ⚠ Error reading {filepath.name}: {e}")
                continue
    
    if verbose:
        print(f"  Found {files_found} JSON files")
        if trials_skipped > 0:
            print(f"  Filtered {trials_skipped} trials")
        if invalid_skipped > 0:
            print(f"  Filtered {invalid_skipped} invalid races")
    return races


def convert_races(race_dicts):
    """Convert race dictionaries to Race objects."""
    races = []
    for rd in race_dicts:
        race = Race.from_dict(rd)
        if race.is_valid:
            races.append(race)
    return races



class RacingModel:
    def __init__(self, supplementary_stats=None, use_ml=False):
        self.horse_history = {}
        self.cd_stats = {}
        self.course_stats = {}
        self.distance_stats = {}
        self.jockey_stats = {}
        self.trainer_stats = {}
        self.combo_stats = {}
        self.going_stats = {}
        self.speed_figures = {}
        self.barrier_stats = {}  # Now includes distance/field size buckets
        self.style_stats = {}
        self.calculated_leader_rates = {}
        self.supp_stats = supplementary_stats or SupplementaryStats()
        self.trained = False
        
        # ML model support
        self.use_ml = use_ml and SKLEARN_AVAILABLE
        self.ml_model = None
        self.ml_scaler = None
        self.feature_names = list(FEATURE_WEIGHTS.keys())
        
        if use_ml and not SKLEARN_AVAILABLE:
            print("  ⚠ sklearn not available, falling back to weighted scoring")
    
    def _get_horse_key(self, runner):
        return runner.horse_id if runner.horse_id else runner.horse.lower().strip()
    
    def _calculate_speed_figure(self, runner, class_level):
        """Calculate speed figure with late speed adjustment."""
        if runner.position == 0:
            return 0
        base = 60
        if runner.position == 1:
            base += 10
        elif runner.position == 2:
            base += 5 - (runner.margin * 0.5)
        elif runner.position == 3:
            base += 2 - (runner.margin * 0.5)
        else:
            base -= runner.margin * 0.8
        
        # Class adjustment
        base += (class_level - 50) * 0.3
        
        # Weight adjustment
        if runner.weight > 57:
            base += (runner.weight - 57) * 0.5
        
        # Late speed adjustment (from comment analysis)
        late = extract_late_speed(runner.comment)
        if late == 'ran_on':
            base += 5  # Closers on firm going bonus
        elif late == 'faded':
            base -= 3
        
        return max(0, min(100, base))
    
    def train(self, races, race_dicts=None, cutoff_date=None):
        """Train model on historical races with time-weighted stats."""
        print(f"  Training on {len(races)} races...")
        race_pairs = list(zip(races, race_dicts or [None] * len(races)))
        
        cutoff_day = None
        if cutoff_date:
            if isinstance(cutoff_date, datetime):
                cutoff_day = cutoff_date.date()
            elif hasattr(cutoff_date, 'year') and not isinstance(cutoff_date, str):
                cutoff_day = cutoff_date
            else:
                cutoff_dt = safe_parse_date(cutoff_date)
                cutoff_day = cutoff_dt.date() if cutoff_dt else None
        
        def race_day(r):
            if getattr(r, 'date_dt', None):
                return r.date_dt.date()
            dt = safe_parse_date(getattr(r, 'date', None))
            return dt.date() if dt else datetime.min.date()
        
        race_pairs.sort(key=lambda x: race_day(x[0]))
        
        ml_features = []
        ml_labels = []
        
        for race, _ in race_pairs:
            if cutoff_day and race_day(race) >= cutoff_day:
                continue
            self._update_from_race(race, cutoff_day)
        
        for course, stats in self.style_stats.items():
            if stats['total'] >= 20:
                self.calculated_leader_rates[course] = stats['leader_wins'] / stats['total']
        
        if self.use_ml:
            self._train_ml_model(races, race_dicts, cutoff_day)
        
        self.trained = True
        print(f"  ✓ Tracking {len(self.horse_history)} horses")
    
    def _train_ml_model(self, races, race_dicts, cutoff_day):
        """Train sklearn LogisticRegression model on historical data."""
        if not SKLEARN_AVAILABLE:
            return
        
        print("  Training ML model...")
        X, y = [], []
        
        for race in races:
            if not race.is_valid:
                continue
            if cutoff_day:
                race_date = race.date_dt.date() if race.date_dt else None
                if race_date and race_date >= cutoff_day:
                    continue
            
            for runner in race.runners:
                if runner.position == 0:
                    continue
                features = self._get_features(runner, race)
                feature_vec = [features.get(f, 50) for f in self.feature_names]
                X.append(feature_vec)
                y.append(1 if runner.position == 1 else 0)
        
        if len(X) < 100:
            print("  ⚠ Insufficient data for ML model")
            return
        
        X = np.array(X)
        y = np.array(y)
        
        self.ml_scaler = StandardScaler()
        X_scaled = self.ml_scaler.fit_transform(X)
        
        base_model = LogisticRegression(max_iter=1000, class_weight='balanced')
        self.ml_model = CalibratedClassifierCV(base_model, cv=5, method='isotonic')
        self.ml_model.fit(X_scaled, y)
        
        print(f"  ✓ ML model trained on {len(X)} samples")
    
    def _update_from_race(self, race, reference_date=None):
        """Update stats from a single race with time weighting."""
        course_lower = race.course.lower()
        dist_bucket = get_distance_bucket(race.distance)
        field_tertile = get_field_size_tertile(race.field_size)
        
        barrier_key = (course_lower, dist_bucket, field_tertile)
        if barrier_key not in self.barrier_stats:
            self.barrier_stats[barrier_key] = {
                'inside': {'runs': 0, 'wins': 0, 'weighted_wins': 0.0, 'weighted_runs': 0.0},
                'middle': {'runs': 0, 'wins': 0, 'weighted_wins': 0.0, 'weighted_runs': 0.0},
                'outside': {'runs': 0, 'wins': 0, 'weighted_wins': 0.0, 'weighted_runs': 0.0}
            }
        
        if course_lower not in self.style_stats:
            self.style_stats[course_lower] = {'leader_wins': 0, 'total': 0}
        
        field_size = len([r for r in race.runners if r.position > 0])
        
        race_date = race.date_dt.date() if race.date_dt else None
        if reference_date and race_date:
            days_ago = (reference_date - race_date).days
        else:
            days_ago = 0
        tw = time_weight(days_ago)
        
        for runner in race.runners:
            if runner.position == 0:
                continue
            
            horse_key = self._get_horse_key(runner)
            is_winner = runner.position == 1
            is_placed = runner.position <= 3
            style = extract_running_style(runner.comment)
            late_speed = extract_late_speed(runner.comment)
            
            if horse_key not in self.horse_history:
                self.horse_history[horse_key] = {
                    'runs': [],
                    'classes': [],
                    'styles': [],
                    'late_speeds': [],
                    'weights': [],
                    'last_race_date': None
                }
            
            self.horse_history[horse_key]['runs'].append({
                'position': runner.position,
                'margin': runner.margin,
                'date': race.date,
                'course': course_lower,
                'distance': race.distance,
                'class': race.class_level,
                'going': race.going,
                'weight': runner.weight,
                'barrier': runner.barrier,
                'sp': runner.sp,
                'comment': runner.comment,
                'style': style,
                'late_speed': late_speed,
                'field_size': field_size,
            })
            self.horse_history[horse_key]['classes'].append(race.class_level)
            self.horse_history[horse_key]['styles'].append(style)
            self.horse_history[horse_key]['late_speeds'].append(late_speed)
            self.horse_history[horse_key]['weights'].append(runner.weight)
            self.horse_history[horse_key]['last_race_date'] = race.date
            
            sf = self._calculate_speed_figure(runner, race.class_level)
            if horse_key not in self.speed_figures:
                self.speed_figures[horse_key] = []
            self.speed_figures[horse_key].append(sf)
            
            cd_key = (horse_key, course_lower, dist_bucket)
            if cd_key not in self.cd_stats:
                self.cd_stats[cd_key] = {
                    'runs': 0, 'wins': 0, 'places': 0,
                    'weighted_runs': 0.0, 'weighted_wins': 0.0
                }
            self.cd_stats[cd_key]['runs'] += 1
            self.cd_stats[cd_key]['weighted_runs'] += tw
            if is_winner:
                self.cd_stats[cd_key]['wins'] += 1
                self.cd_stats[cd_key]['weighted_wins'] += tw
            if is_placed:
                self.cd_stats[cd_key]['places'] += 1
            
            course_key = (horse_key, course_lower)
            if course_key not in self.course_stats:
                self.course_stats[course_key] = {'runs': 0, 'wins': 0, 'weighted_runs': 0.0, 'weighted_wins': 0.0}
            self.course_stats[course_key]['runs'] += 1
            self.course_stats[course_key]['weighted_runs'] += tw
            if is_winner:
                self.course_stats[course_key]['wins'] += 1
                self.course_stats[course_key]['weighted_wins'] += tw
            
            dist_key = (horse_key, dist_bucket)
            if dist_key not in self.distance_stats:
                self.distance_stats[dist_key] = {'runs': 0, 'wins': 0}
            self.distance_stats[dist_key]['runs'] += 1
            if is_winner:
                self.distance_stats[dist_key]['wins'] += 1
            
            jockey_key = runner.jockey_id or (runner.jockey.lower() if runner.jockey else '')
            if jockey_key:
                jk_key = (jockey_key, course_lower)
                if jk_key not in self.jockey_stats:
                    self.jockey_stats[jk_key] = {'runs': 0, 'wins': 0, 'recent': []}
                self.jockey_stats[jk_key]['runs'] += 1
                if is_winner:
                    self.jockey_stats[jk_key]['wins'] += 1
                self.jockey_stats[jk_key]['recent'].append(1 if is_winner else 0)
                self.jockey_stats[jk_key]['recent'] = self.jockey_stats[jk_key]['recent'][-20:]
            
            trainer_key = runner.trainer_id or (runner.trainer.lower() if runner.trainer else '')
            if trainer_key:
                tr_key = (trainer_key, course_lower)
                if tr_key not in self.trainer_stats:
                    self.trainer_stats[tr_key] = {'runs': 0, 'wins': 0}
                self.trainer_stats[tr_key]['runs'] += 1
                if is_winner:
                    self.trainer_stats[tr_key]['wins'] += 1
            
            if jockey_key and trainer_key:
                combo_key = (trainer_key, jockey_key, course_lower)
                if combo_key not in self.combo_stats:
                    self.combo_stats[combo_key] = {'runs': 0, 'wins': 0}
                self.combo_stats[combo_key]['runs'] += 1
                if is_winner:
                    self.combo_stats[combo_key]['wins'] += 1
            
            sire_key = runner.sire_id or (runner.sire.lower() if runner.sire else '')
            if sire_key:
                if sire_key not in self.going_stats:
                    self.going_stats[sire_key] = {
                        'wet_runs': 0, 'wet_wins': 0,
                        'dry_runs': 0, 'dry_wins': 0
                    }
                if race.is_wet:
                    self.going_stats[sire_key]['wet_runs'] += 1
                    if is_winner:
                        self.going_stats[sire_key]['wet_wins'] += 1
                else:
                    self.going_stats[sire_key]['dry_runs'] += 1
                    if is_winner:
                        self.going_stats[sire_key]['dry_wins'] += 1
            
            if runner.barrier and field_size > 0:
                barrier_pct = runner.barrier / field_size
                zone = 'inside' if barrier_pct <= 0.33 else ('outside' if barrier_pct >= 0.67 else 'middle')
                self.barrier_stats[barrier_key][zone]['runs'] += 1
                self.barrier_stats[barrier_key][zone]['weighted_runs'] += tw
                if is_winner:
                    self.barrier_stats[barrier_key][zone]['wins'] += 1
                    self.barrier_stats[barrier_key][zone]['weighted_wins'] += tw
            
            if is_winner:
                self.style_stats[course_lower]['total'] += 1
                if style in ['leader', 'on_pace']:
                    self.style_stats[course_lower]['leader_wins'] += 1
    
    
    def _score_cd_history(self, runner, race):
        """Score course & distance history with time-weighted stats."""
        horse_key = self._get_horse_key(runner)
        cd_key = (horse_key, race.course.lower(), get_distance_bucket(race.distance))
        stats = self.cd_stats.get(cd_key, {'runs': 0, 'wins': 0, 'places': 0, 'weighted_runs': 0, 'weighted_wins': 0})
        
        if self.supp_stats.loaded and runner.horse_id:
            horse_stats = self.supp_stats.get_horse_stats(runner.horse_id)
            course_stats = horse_stats.get('course_stats', {}).get(race.course.lower(), {})
            if course_stats.get('runs', 0) > stats['runs']:
                stats = course_stats
        
        if stats['runs'] == 0:
            return 45
        
        if stats.get('weighted_runs', 0) > 0:
            weighted_rate = stats['weighted_wins'] / stats['weighted_runs']
            if weighted_rate > 0.3:
                return 88
            elif weighted_rate > 0.2:
                return 78
            elif weighted_rate > 0.1:
                return 62
        
        if stats.get('wins', 0) >= 2:
            return 88
        elif stats.get('wins', 0) == 1:
            return 78
        elif stats.get('places', 0) > 0:
            return 58 + (bayesian_rate(stats.get('places', 0), stats['runs']) - BASELINE_WIN_RATE) * 100
        return 42
    
    def _score_course_history(self, runner, race):
        """Score course history."""
        stats = self.course_stats.get(
            (self._get_horse_key(runner), race.course.lower()),
            {'runs': 0, 'wins': 0}
        )
        if stats['runs'] == 0:
            return 55
        if stats['wins'] >= 2:
            return 80
        elif stats['wins'] == 1:
            return 70
        elif stats['runs'] >= 3:
            return 35
        return 50
    
    def _score_distance_history(self, runner, race):
        """Score distance history."""
        stats = self.distance_stats.get(
            (self._get_horse_key(runner), get_distance_bucket(race.distance)),
            {'runs': 0, 'wins': 0}
        )
        if stats['runs'] == 0:
            return 50
        if stats['wins'] >= 2:
            return 75
        elif stats['wins'] == 1:
            return 68
        return 48
    
    def _score_recent_form(self, runner, race):
        """Score recent form with time decay weighting."""
        hist = self.horse_history.get(self._get_horse_key(runner), {})
        runs = hist.get('runs', [])
        
        if not runs and runner.form:
            form_positions = []
            for char in runner.form.replace('-', ''):
                if char.isdigit():
                    form_positions.append(int(char))
                elif char.lower() == 'x':
                    form_positions.append(10)
            if form_positions:
                score = 50
                for i, pos in enumerate(reversed(form_positions[-5:])):
                    w = [4, 3, 2.5, 2, 1.5][min(i, 4)]
                    if pos == 1:
                        score += 12 * w
                    elif pos == 2:
                        score += 8 * w
                    elif pos == 3:
                        score += 5 * w
                    elif pos <= 5:
                        score += 2 * w
                    else:
                        score -= 2 * w
                return min(100, max(0, score))
        
        if not runs:
            return 45
        
        recent_runs = [r for r in runs[-5:] if r.get('position')]
        if not recent_runs:
            return 45
        
        score = 50
        weights = [4, 3, 2.5, 2, 1.5]
        recent_pos = []
        
        for i, r in enumerate(reversed(recent_runs)):
            pos = r.get('position')
            recent_pos.append(pos)
            w = weights[i] if i < len(weights) else 1
            
            gap = days_between(r.get('date'), race.date)
            if gap is not None:
                w *= time_weight(gap, TIME_DECAY_HALF_LIFE)
            
            if pos == 1:
                score += 12 * w
            elif pos == 2:
                score += 8 * w
            elif pos == 3:
                score += 5 * w
            elif pos <= 5:
                score += 2 * w
            else:
                score -= 2 * w
        
        recent_pos = list(reversed(recent_pos))
        if len(recent_pos) >= 3 and recent_pos[-3] > recent_pos[-2] > recent_pos[-1]:
            score += 10
        
        return min(100, max(0, score))
    
    def _score_last_start(self, runner, race):
        """Score last start performance."""
        hist = self.horse_history.get(self._get_horse_key(runner), {})
        runs = hist.get('runs', [])
        if not runs:
            return 45
        last = runs[-1]
        pos, margin = last.get('position', 0), last.get('margin', 0)
        if pos == 1:
            return 78
        elif pos == 2:
            return 82 if margin < 0.5 else 68
        elif pos == 3:
            return 70 if margin < 0.5 else 62
        elif pos <= 5:
            return 55
        return 40
    
    def _score_last_margin(self, runner, race):
        """Score last margin beaten."""
        hist = self.horse_history.get(self._get_horse_key(runner), {})
        runs = hist.get('runs', [])
        if not runs:
            return 50
        last = runs[-1]
        pos, margin = last.get('position', 0), last.get('margin', 99)
        if pos == 1:
            return 75
        if margin < 0.5:
            return 75
        elif margin < 1.0:
            return 65
        elif margin < 2.0:
            return 55
        elif margin < 4.0:
            return 45
        return 35
    
    def _score_speed_figure(self, runner, race):
        """Score based on speed figures."""
        figures = self.speed_figures.get(self._get_horse_key(runner), [])
        if not figures:
            return 50
        recent = figures[-3:]
        return min(100, max(0, max(recent) * 0.6 + sum(recent) / len(recent) * 0.4))
    
    def _score_track_bias(self, runner, race):
        """Score track bias suitability."""
        course_lower = race.course.lower()
        
        leader_rate = self.calculated_leader_rates.get(course_lower)
        if leader_rate is None and self.supp_stats.loaded:
            lr = self.supp_stats.get_track_leader_rate(course_lower)
            if lr is not None:
                leader_rate = lr / 100
        if leader_rate is None:
            profile = TRACK_PROFILES.get(course_lower, {})
            leader_rate = profile.get('leader_rate')
        if leader_rate is None:
            return 50
        
        hist = self.horse_history.get(self._get_horse_key(runner), {})
        styles = hist.get('styles', [])
        if not styles:
            return 50
        
        style_counts = defaultdict(int)
        for s in styles[-5:]:
            style_counts[s] += 1
        dominant = max(style_counts, key=style_counts.get) if style_counts else 'unknown'
        
        if dominant in ['leader', 'on_pace']:
            if leader_rate >= 0.90:
                return 90
            elif leader_rate >= 0.75:
                return 75
            elif leader_rate >= 0.60:
                return 65
            elif leader_rate >= 0.45:
                return 55
            elif leader_rate >= 0.30:
                return 40
            return 25
        elif dominant in ['backmarker', 'midfield']:
            if leader_rate <= 0.25:
                return 85
            elif leader_rate <= 0.35:
                return 75
            elif leader_rate <= 0.50:
                return 60
            elif leader_rate <= 0.70:
                return 45
            return 20
        return 50
    
    def _score_late_speed(self, runner, race):
        """Score late speed tendency."""
        hist = self.horse_history.get(self._get_horse_key(runner), {})
        late_speeds = hist.get('late_speeds', [])
        if not late_speeds:
            return 50
        
        recent = late_speeds[-3:]
        ran_on = sum(1 for s in recent if s == 'ran_on')
        faded = sum(1 for s in recent if s == 'faded')
        
        score = 50
        if ran_on >= 2:
            score = 70
        elif ran_on >= 1:
            score = 60
        if faded >= 2:
            score = 30
        elif faded >= 1:
            score -= 15
        
        return max(20, min(80, score))
    
    def _score_connections(self, runner, race):
        """Score jockey/trainer connections."""
        score = 50
        course_lower = race.course.lower()
        jockey_key = runner.jockey_id or (runner.jockey.lower() if runner.jockey else '')
        trainer_key = runner.trainer_id or (runner.trainer.lower() if runner.trainer else '')
        
        used_supp = False
        if self.supp_stats.loaded:
            if runner.jockey_id:
                jk_rate, jk_conf = self.supp_stats.get_jockey_course_rate(runner.jockey_id, course_lower)
                if jk_conf > 0.3:
                    used_supp = True
                    if jk_rate > 0.18:
                        score += 18
                    elif jk_rate > 0.14:
                        score += 12
                    elif jk_rate > 0.10:
                        score += 6
            if runner.trainer_id:
                tr_rate, tr_conf = self.supp_stats.get_trainer_course_rate(runner.trainer_id, course_lower)
                if tr_conf > 0.3:
                    if tr_rate > 0.18:
                        score += 14
                    elif tr_rate > 0.14:
                        score += 8
            if runner.jockey_id and runner.trainer_id:
                combo_rate, combo_conf = self.supp_stats.get_combo_rate(runner.jockey_id, runner.trainer_id)
                if combo_conf > 0.2 and combo_rate > 0.20:
                    score += 10
        
        if not used_supp and jockey_key:
            jk_stats = self.jockey_stats.get((jockey_key, course_lower), {'runs': 0, 'wins': 0, 'recent': []})
            if jk_stats['runs'] >= 10:
                jk_rate = bayesian_rate(jk_stats['wins'], jk_stats['runs'], 25)
                if jk_rate > 0.18:
                    score += 18
                elif jk_rate > 0.12:
                    score += 10
            recent = jk_stats.get('recent', [])[-10:]
            if recent and len(recent) >= 5 and sum(recent) / len(recent) > 0.20:
                score += 10
            
            for name, rates in ELITE_JOCKEYS.items():
                if name in jockey_key:
                    rate = rates.get(course_lower, rates.get('default', 0.15))
                    if rate > 0.20:
                        score += 12
                    elif rate > 0.15:
                        score += 8
                    break
        
        if not used_supp and trainer_key:
            tr_stats = self.trainer_stats.get((trainer_key, course_lower), {'runs': 0, 'wins': 0})
            if tr_stats['runs'] >= 10:
                tr_rate = bayesian_rate(tr_stats['wins'], tr_stats['runs'], 25)
                if tr_rate > 0.20:
                    score += 14
                elif tr_rate > 0.14:
                    score += 7
        
        if not used_supp and jockey_key and trainer_key:
            combo = self.combo_stats.get((trainer_key, jockey_key, course_lower), {'runs': 0, 'wins': 0})
            if combo['runs'] >= 5 and bayesian_rate(combo['wins'], combo['runs'], 15) > 0.25:
                score += 10
        
        return min(100, max(0, score))
    
    def _score_class_rating(self, runner, race):
        """Score class level comparison."""
        hist = self.horse_history.get(self._get_horse_key(runner), {})
        classes = hist.get('classes', [])
        if not classes:
            return 50
        
        avg_class = sum(classes[-5:]) / len(classes[-5:])
        diff = avg_class - race.class_level
        
        if diff > 12:
            return 80
        elif diff > 6:
            return 70
        elif diff > 0:
            return 60
        elif diff > -6:
            return 50
        elif diff > -12:
            return 40
        return 30
    
    def _score_going(self, runner, race):
        """Score going suitability based on sire stats."""
        sire_key = runner.sire_id or (runner.sire.lower() if runner.sire else '')
        if not sire_key:
            return 50
        
        rating = race.going_rating
        wet_w = min(1.0, max(0.0, (rating - 3.0) / 7.0))
        dry_w = 1.0 - wet_w
        
        wet_rate = BASELINE_WIN_RATE
        dry_rate = BASELINE_WIN_RATE
        conf = 0.0
        
        if self.supp_stats.loaded and runner.sire_id:
            wet_rate, wet_conf = self.supp_stats.get_sire_going_rate(runner.sire_id, True)
            dry_rate, dry_conf = self.supp_stats.get_sire_going_rate(runner.sire_id, False)
            conf = max(wet_conf, dry_conf)
        else:
            stats = self.going_stats.get(sire_key, {})
            if stats.get('wet_runs', 0) >= 10:
                wet_rate = bayesian_rate(stats.get('wet_wins', 0), stats.get('wet_runs', 0), 20)
                conf = max(conf, confidence_score(stats.get('wet_runs', 0), 20))
            if stats.get('dry_runs', 0) >= 10:
                dry_rate = bayesian_rate(stats.get('dry_wins', 0), stats.get('dry_runs', 0), 20)
                conf = max(conf, confidence_score(stats.get('dry_runs', 0), 20))
        
        blended = wet_w * wet_rate + dry_w * dry_rate
        
        if conf > 0.2:
            if blended > 0.15:
                return 75
            elif blended > 0.11:
                return 62
            elif blended > 0.10:
                return 60
            elif blended < 0.06:
                return 35
            elif blended < 0.08:
                return 42
        return 50
    
    def _score_first_up(self, runner, race):
        """Score fitness based on days since last run using Gaussian model."""
        hist = self.horse_history.get(self._get_horse_key(runner), {})
        runs = hist.get('runs', [])
        if not runs:
            return 50
        
        last_date = runs[-1].get('date')
        gap = days_between(last_date, race.date)
        
        return layoff_score_gaussian(gap)
    
    def _score_beaten_margins(self, runner, race):
        """Score average beaten margins."""
        hist = self.horse_history.get(self._get_horse_key(runner), {})
        runs = hist.get('runs', [])
        if not runs:
            return 50
        
        margins = [r['margin'] for r in runs[-5:] if r.get('margin') is not None]
        if not margins:
            return 50
        
        avg = sum(margins) / len(margins)
        if avg <= 0.5:
            return 75
        elif avg <= 1.5:
            return 65
        elif avg <= 3.0:
            return 55
        elif avg <= 5.0:
            return 45
        return 35
    
    def _score_barrier(self, runner, race):
        """
        Score barrier position with distance/field size adjustments.
        Inside barriers crucial for sprints, less important for staying races.
        """
        if not runner.barrier or race.field_size == 0:
            return 50
        
        dist_bucket = race.distance_bucket
        field_tertile = race.field_tertile
        
        profile = BARRIER_PROFILES.get(dist_bucket, {'inside_boost': 8, 'outside_penalty': -10})
        
        field_mult = 1.0
        if field_tertile == 'large':
            field_mult = 1.3
        elif field_tertile == 'small':
            field_mult = 0.7
        
        pct = runner.barrier / race.field_size
        
        if pct <= 0.25:
            adj = profile['inside_boost'] * field_mult
            return min(80, 50 + adj)
        elif pct <= 0.5:
            return 52
        elif pct <= 0.75:
            return 48
        else:
            adj = profile['outside_penalty'] * field_mult
            return max(25, 50 + adj)
    
    def _score_trajectory(self, runner, race):
        """Score horse's improvement trajectory using linear regression on recent speed figures."""
        figures = self.speed_figures.get(self._get_horse_key(runner), [])
        if len(figures) < 3:
            return 50
        
        trajectory = calculate_trajectory(figures)
        
        if trajectory > 3:
            return 75
        elif trajectory > 1:
            return 65
        elif trajectory > 0:
            return 55
        elif trajectory > -1:
            return 50
        elif trajectory > -3:
            return 42
        return 35
    
    def _score_weight_relief(self, runner, race):
        """
        Score weight changes from last run.
        ~0.3 lengths improvement per kg dropped.
        """
        hist = self.horse_history.get(self._get_horse_key(runner), {})
        weights = hist.get('weights', [])
        
        if not weights or not runner.weight:
            return 50
        
        last_weight = weights[-1] if weights else None
        return calculate_weight_relief(runner.weight, last_weight)
    
    def _get_features(self, runner, race):
        """Get all feature scores for a runner."""
        return {
            'cd_history': self._score_cd_history(runner, race),
            'course_history': self._score_course_history(runner, race),
            'distance_history': self._score_distance_history(runner, race),
            'recent_form': self._score_recent_form(runner, race),
            'last_start': self._score_last_start(runner, race),
            'last_margin': self._score_last_margin(runner, race),
            'speed_figure': self._score_speed_figure(runner, race),
            'track_bias': self._score_track_bias(runner, race),
            'late_speed': self._score_late_speed(runner, race),
            'connections': self._score_connections(runner, race),
            'class_rating': self._score_class_rating(runner, race),
            'going': self._score_going(runner, race),
            'first_up': self._score_first_up(runner, race),
            'beaten_margins': self._score_beaten_margins(runner, race),
            'barrier': self._score_barrier(runner, race),
            'trajectory': self._score_trajectory(runner, race),
            'weight_relief': self._score_weight_relief(runner, race),
        }
    
    def _get_runner_style(self, runner):
        """Get runner's dominant style."""
        hist = self.horse_history.get(self._get_horse_key(runner), {})
        styles = hist.get('styles', [])
        if not styles:
            return 'unknown'
        counts = defaultdict(int)
        for s in styles[-5:]:
            counts[s] += 1
        return max(counts, key=counts.get) if counts else 'unknown'
    
    def _get_track_bias_adjustment(self, runner, race):
        """Calculate track bias probability adjustment."""
        course_lower = race.course.lower()
        leader_rate = self.calculated_leader_rates.get(course_lower)
        
        if leader_rate is None and self.supp_stats.loaded:
            lr = self.supp_stats.get_track_leader_rate(course_lower)
            if lr is not None:
                leader_rate = lr / 100
        if leader_rate is None:
            profile = TRACK_PROFILES.get(course_lower, {})
            leader_rate = profile.get('leader_rate', 0.5)
        
        style = self._get_runner_style(runner)
        normalized = max(-1, min(1, (leader_rate - 0.5) / 0.5))
        max_boost = 0.08
        
        if style in ['leader', 'on_pace']:
            return normalized * max_boost
        elif style in ['backmarker', 'midfield']:
            return -normalized * max_boost
        return 0.0
    
    def _calculate_field_strength(self, race, analysis):
        """
        Calculate field strength for relative rating normalisation.
        Returns average total score of field.
        """
        if not analysis:
            return 50
        totals = [r['total'] for r in analysis]
        return sum(totals) / len(totals)
    
    def analyze(self, race):
        """Analyze a race and return probability estimates for each runner."""
        if not race.is_valid:
            return []
        
        runner_data = []
        for runner in race.runners:
            features = self._get_features(runner, race)
            
            if self.use_ml and self.ml_model is not None:
                total = sum(features[k] * FEATURE_WEIGHTS.get(k, 0) for k in features)
            else:
                total = sum(features[k] * FEATURE_WEIGHTS.get(k, 0) for k in features)
            
            bias_adj = self._get_track_bias_adjustment(runner, race)
            runner_data.append({
                'runner': runner,
                'features': features,
                'total': total,
                'bias_adj': bias_adj,
                'style': self._get_runner_style(runner)
            })
        
        field_avg = sum(d['total'] for d in runner_data) / len(runner_data) if runner_data else 50
        
        if self.use_ml and self.ml_model is not None:
            X = np.array([[d['features'].get(f, 50) for f in self.feature_names] for d in runner_data])
            X_scaled = self.ml_scaler.transform(X)
            base_probs = self.ml_model.predict_proba(X_scaled)[:, 1]
            base_probs = base_probs / base_probs.sum()
        else:
            totals = np.array([d['total'] for d in runner_data])
            exp_s = np.exp((totals - totals.max()) / SOFTMAX_TEMPERATURE)
            base_probs = exp_s / exp_s.sum()
        
        results = []
        for i, data in enumerate(runner_data):
            runner = data['runner']
            base_prob = base_probs[i]
            adj = min(0.08, max(-0.08, data['bias_adj']))
            model_prob = max(0.01, min(0.99, base_prob + adj))
            
            market_prob = 1 / runner.sp if runner.sp > 1 else None
            value_edge = (model_prob - market_prob) * 100 if market_prob else None
            ev = expected_value(model_prob, runner.sp) if runner.sp > 1 else None
            
            relative_rating = data['total'] / field_avg if field_avg > 0 else 1.0
            
            results.append({
                'horse': runner.horse,
                'number': runner.number,
                'horse_id': runner.horse_id,
                'runner_obj': runner,
                'barrier': runner.barrier,
                'jockey': runner.jockey[:15] if runner.jockey else '',
                'trainer': runner.trainer[:15] if runner.trainer else '',
                'weight': runner.weight,
                'features': data['features'],
                'total': round(data['total'], 1),
                'relative_rating': round(relative_rating, 2),
                'field_strength': round(field_avg, 1),
                'style': data['style'],
                'model_prob': round(model_prob * 100, 2),  # stored as percent for display
                'model_prob_dec': model_prob,               # decimal for math
                'market_prob': round(market_prob * 100, 2) if market_prob else None,
                'model_odds': round(1 / model_prob, 2) if model_prob > 0 else 100,
                'market_odds': runner.sp,
                'sp': runner.sp,
                'value_edge': round(value_edge, 2) if value_edge else None,
                'bias_adj': round(adj * 100, 1),
                'ev': round(ev, 4) if ev is not None else None,
                'kelly': round(kelly_stake(model_prob, runner.sp) * 100, 2) if runner.sp > 1 else 0,
            })
        
        results.sort(key=lambda x: -x['model_prob'])
        return results



def get_leader_rate_estimate(model, race):
    """Best-effort leader-rate estimate with course profile fallback."""
    course_lower = race.course.lower()
    leader_rate = getattr(model, 'calculated_leader_rates', {}).get(course_lower)
    if leader_rate is None and getattr(model, 'supp_stats', None) and model.supp_stats.loaded:
        lr = model.supp_stats.get_track_leader_rate(course_lower)
        leader_rate = lr / 100 if lr is not None else None
    if leader_rate is None:
        leader_rate = TRACK_PROFILES.get(course_lower, {}).get('leader_rate', 0.5)
    return max(0.1, min(0.95, leader_rate))


def infer_market_odds(pick, field_odds):
    """Infer market odds when SP missing using model_odds or median of field."""
    if pick.get('sp') and pick['sp'] > 1:
        return pick['sp']
    if pick.get('market_odds') and pick['market_odds'] > 1:
        return pick['market_odds']
    if pick.get('model_odds') and pick['model_odds'] > 1:
        return pick['model_odds']
    if pick.get('model_prob_dec'):
        fair = 1 / pick['model_prob_dec'] if pick['model_prob_dec'] > 0 else None
        if fair and fair > 1:
            return fair
    if field_odds:
        median_odds = np.median(field_odds)
        return float(median_odds) if median_odds and median_odds > 1 else None
    return None


def simulate_race_monte_carlo(race, analysis, model, mc_sims=20000, seed=42,
                              mc_model='plackett_luce', pace_mode='basic', uncertainty='on'):
    """
    Monte Carlo finishing-order simulator (Plackett-Luce via Gumbel-max).
    Uses model probabilities (after bias adj) as priors, then perturbs by
    evidence-driven Dirichlet noise plus pace/bias scenario variation.
    """
    if not race.is_valid or len(analysis) < 2:
        return []
    
    rng = np.random.default_rng(seed)
    n = len(analysis)
    
    base_probs = np.array([soft_clip_prob(a['model_prob'] / 100.0) for a in analysis], dtype=float)
    base_probs = base_probs / base_probs.sum()
    
    # Evidence: number of historical runs
    evidence = []
    for a in analysis:
        runner = a.get('runner_obj')
        hist = model.horse_history.get(model._get_horse_key(runner), {}) if runner else {}
        evidence.append(len(hist.get('runs', [])))
    evidence = np.array(evidence, dtype=float)
    conc = np.maximum(6.0, 12.0 + 1.3 * evidence)

    # Going-aware Dirichlet concentration: wet/heavy widens uncertainty (lower conc = more dispersed)
    going_lower = getattr(race, 'going', '').lower() if hasattr(race, 'going') else ''
    if 'heavy' in going_lower:
        conc *= 0.72   # Heavy: materially wider uncertainty
    elif any(w in going_lower for w in ('soft', 'wet', 'yield')):
        conc *= 0.82   # Soft: moderately wider
    elif any(w in going_lower for w in ('synthetic', 'poly', 'tapeta')):
        conc *= 1.06   # Synthetic: slightly tighter
    elif any(w in going_lower for w in ('firm', 'fast', 'hard')):
        conc *= 1.04   # Firm: slightly tighter

    alpha = np.maximum(0.2, base_probs * conc)
    
    styles = [a.get('style', 'midfield') or 'midfield' for a in analysis]
    leader_rate_base = get_leader_rate_estimate(model, race)
    pace_pressure = sum(1 for s in styles if s in ['leader', 'on_pace']) / max(1, n)
    
    finish_positions = np.zeros((mc_sims, n), dtype=np.int16)
    scenario_ids = np.zeros(mc_sims, dtype=np.int8)
    pace_labels = ['slow', 'even', 'fast', 'melt']
    
    for s in range(mc_sims):
        leader_rate_s = float(np.clip(rng.normal(leader_rate_base, 0.07), 0.12, 0.98))
        pressure_s = float(np.clip(pace_pressure + rng.normal(0, 0.05), 0.0, 1.0))
        
        if pace_mode == 'off':
            pace_regime = 'even'
        else:
            if pressure_s > 0.62:
                pace_regime = 'fast' if leader_rate_s > 0.40 else 'melt'
            elif pressure_s < 0.34:
                pace_regime = 'slow'
            else:
                pace_regime = 'even'
        
        scenario_ids[s] = pace_labels.index(pace_regime)
        
        if uncertainty == 'on':
            sampled_probs = rng.dirichlet(alpha)
        else:
            sampled_probs = base_probs
        
        # Optional additional Dirichlet noise for "dirichlet" model
        if mc_model == 'dirichlet':
            sampled_probs = rng.dirichlet(np.maximum(0.25, sampled_probs * (10 + evidence)))
        
        logits = np.log(np.maximum(sampled_probs, 1e-9))
        logits += scenario_adjustments(styles, leader_rate_s, pace_regime)
        
        noise = rng.gumbel(size=n)
        order = np.argsort(-(logits + noise))
        finish_positions[s, order] = np.arange(1, n + 1)
    
    win_probs = (finish_positions == 1).mean(axis=0)
    top2_probs = (finish_positions <= 2).mean(axis=0)
    top3_probs = (finish_positions <= 3).mean(axis=0)
    exp_pos = finish_positions.mean(axis=0)
    std_pos = finish_positions.std(axis=0)
    
    scenario_win = np.zeros((len(pace_labels), n))
    sampled_regimes = np.zeros(len(pace_labels), dtype=bool)
    for idx, label in enumerate(pace_labels):
        mask = scenario_ids == idx
        if mask.any():
            sampled_regimes[idx] = True
            scenario_win[idx] = (finish_positions[mask] == 1).mean(axis=0)
    if _flag_enabled("STRIDE_MC_FIX_SCENARIO_RANGES"):
        # #124 defect 3: a regime this race's pace logic never samples
        # (pace_mode='basic' typically reaches 1-2 of the 4) left its zero
        # row in the spread, so any runner above ~28.6% win probability
        # carried a phantom range equal to its own maximum and scenario
        # stability hard-zeroed — which gates mc_is_playable. Spread over
        # regimes actually simulated only; a single sampled regime means
        # no observed scenario variation, not maximal.
        _sampled_win = scenario_win[sampled_regimes]
        if sampled_regimes.sum() >= 2:
            scenario_ranges = _sampled_win.max(axis=0) - _sampled_win.min(axis=0)
        else:
            scenario_ranges = np.zeros(n)
    else:
        scenario_ranges = scenario_win.max(axis=0) - scenario_win.min(axis=0)
    
    results = []
    for i, base in enumerate(analysis):
        lower, upper = wilson_interval((finish_positions[:, i] == 1).sum(), mc_sims, alpha=MC_SIM_LIMITS['ci_alpha'])
        ci_width = upper - lower
        stability = stability_from_positions(std_pos[i], ci_width)
        scen_stability = max(0.0, min(100.0, 100 - scenario_ranges[i] * 350))
        stability_score = 0.7 * stability + 0.3 * scen_stability
        
        results.append({
            'horse': base['horse'],
            'number': base.get('number', 0),
            'horse_id': base.get('horse_id', ''),
            'runner_obj': base.get('runner_obj'),
            'model_prob': base.get('model_prob', 0),
            'value_edge': base.get('value_edge'),
            'ev': base.get('ev'),
            'market_odds': base.get('market_odds'),
            'model_odds': base.get('model_odds'),
            'relative_rating': base.get('relative_rating', 1.0),
            'field_strength': base.get('field_strength'),
            'style': base.get('style', 'midfield'),
            'win_prob_sim': float(win_probs[i]),
            'top2_prob_sim': float(top2_probs[i]),
            'top3_prob_sim': float(top3_probs[i]),
            'expected_pos': float(exp_pos[i]),
            'std_pos': float(std_pos[i]),
            'ci_lower': float(lower),
            'ci_upper': float(upper),
            'ci_width': float(ci_width),
            'stability_score': float(stability_score),
            'scenario_sensitivity': float(scenario_ranges[i]),
            'pace_splits': {label: float(p[i]) for label, p in zip(pace_labels, scenario_win)},
        })
    return results


def mc_selection_score(candidate):
    """
    Composite selection score: edge-dominant with sectional superiority overlay.

    Weights edge/value highest because profitable betting = finding overlay vs market.
    Champion horses with elite sectionals get a sectional superiority multiplier
    so they are not penalised for short prices when their ability justifies it.
    """
    win = candidate.get('win_prob_sim', 0.0)
    top3 = candidate.get('top3_prob_sim', 0.0)
    edge_raw = (candidate.get('value_edge') or 0) / 100.0
    edge = max(0.0, edge_raw)
    stability = max(0.0, min(1.0, candidate.get('stability_score', 0) / 100.0))
    rel = candidate.get('relative_rating', 1.0)
    rel_scaled = max(0.0, min(1.0, (rel - 0.8) / 0.7))
    
    ev = candidate.get('ev', 0) or 0
    ev_scaled = max(0.0, min(1.0, ev / 1.0))  # Require EV=1.0 to max out (was 0.5)

    score = (
        0.30 * edge +       # 30% edge (was 35%)
        0.08 * ev_scaled +  # 8% EV (was 20% — EV no longer dominates longshots)
        0.30 * win +        # 30% win probability (was 20% — realistic chance matters)
        0.12 * top3 +       # 12% place probability (was 10%)
        0.12 * stability +  # 12% stability (was 8%)
        0.08 * rel_scaled   # 8% relative rating (was 7%)
    )
    
    late_bench = candidate.get('late_benchmark', 0) or 0
    overall_bench = candidate.get('overall_benchmark', 0) or 0
    bench_trend = candidate.get('benchmark_trend', 0) or 0
    
    if (late_bench > 0.5 or overall_bench > 0.4) and win >= 0.08:
        sect_quality = max(late_bench, overall_bench)
        sect_multiplier = 1.0 + min(0.15, sect_quality * 0.10)  # Reduced from 0.25/0.15
        if bench_trend == 1:
            sect_multiplier += 0.03  # Reduced from 0.05
        score *= sect_multiplier
    elif late_bench < -0.3 and overall_bench < -0.2:
        score *= 0.90
    
    return score


def mc_is_playable(candidate):
    """
    Check hard filters: edge-first with sectional quality override for champions.
    Longshots ($30+) require genuine edge, not just positive EV.
    """
    win = candidate.get('win_prob_sim', 0.0)
    edge_pct = candidate.get('value_edge')
    ev = candidate.get('ev')
    stability = candidate.get('stability_score', 0.0)
    market_odds = candidate.get('market_odds', 0) or 0

    edge_ok = edge_pct is not None and edge_pct >= 2.0
    ev_ok = ev is not None and ev >= 0.03

    # Odds-tiered playability — longshots need stronger evidence
    if market_odds > 30:
        # Extreme longshots: must have genuine edge AND 8%+ win prob AND high stability
        playable = (win >= 0.08) and edge_ok and (stability >= 55)
    elif market_odds > 15:
        playable = (win >= 0.10) and (edge_ok or ev_ok) and (stability >= 50)
    else:
        playable = (win >= 0.10) and (edge_ok or ev_ok) and (stability >= 45)

    if not playable and win >= 0.30 and stability >= 60:
        late_bench = candidate.get('late_benchmark', 0) or 0
        overall_bench = candidate.get('overall_benchmark', 0) or 0
        if late_bench > 0.5 or overall_bench > 0.4:
            playable = True

    return playable


def mc_compute_staking(candidate, risk_mode, base_unit, bankroll, daily_remaining, track_remaining):
    """Determine stake units and Kelly overlay with caps."""
    win = candidate.get('win_prob_sim', 0.0)
    odds = candidate.get('market_odds')
    stability = candidate.get('stability_score', 0.0)
    
    units = 0
    status = 'WATCH'
    kelly_pct = 0.0
    
    if odds and odds > 1 and win >= 0.10 and stability >= 45:
        units = 1
        if win >= 0.18 and stability >= 60:
            units = 3
        elif win >= 0.14 and stability >= 55:
            units = 2
        status = 'BET'
        
        if risk_mode == 'flat':
            kelly_pct = 0.0
        else:
            frac = 1.0 if risk_mode == 'kelly' else 0.5
            kelly_pct = kelly_stake(win, odds, fraction=frac, max_stake=MAX_KELLY_STAKE)
        # Avoid heavy favs without EV
        if odds < 2.0 and (candidate.get('ev') is None or candidate.get('ev') <= 0):
            status = 'WATCH'
            units = 0
            kelly_pct = 0.0
    else:
        # Allow strong watch if missing odds but clear edge/stability
        if odds is None and win >= 0.22 and stability >= 60:
            status = 'WATCH'
    
    # Apply caps
    units = min(units, max(0, daily_remaining))
    units = min(units, max(0, track_remaining))
    stake = units * base_unit if status != 'LIMIT' else 0
    
    if status == 'BET' and kelly_pct > 0:
        stake = max(stake, kelly_pct * bankroll)
    
    if units == 0 and status == 'BET':
        status = 'LIMIT'
    if daily_remaining <= 0 or track_remaining <= 0:
        status = 'LIMIT'
    
    return {
        'units': units,
        'stake': stake,
        'kelly_pct': kelly_pct * 100,
        'status': status,
        'mode': risk_mode,
    }


def calculate_confidence(pick, all_picks):
    """Calculate confidence score for a selection."""
    conf = {'score': 0, 'reasons': [], 'warnings': [], 'level': 'LOWER'}
    
    model_prob = pick.get('model_prob', 0)
    value_edge = pick.get('value_edge')
    ev = pick.get('ev')
    features = pick.get('features', {})
    relative_rating = pick.get('relative_rating', 1.0)
    
    if model_prob >= 25:
        conf['score'] += 45
        conf['reasons'].append(f"Excellent {model_prob:.1f}% prob")
    elif model_prob >= 20:
        conf['score'] += 38
        conf['reasons'].append(f"Strong {model_prob:.1f}% prob")
    elif model_prob >= 16:
        conf['score'] += 30
        conf['reasons'].append(f"Good {model_prob:.1f}% prob")
    elif model_prob >= 12:
        conf['score'] += 22
    elif model_prob >= 10:
        conf['score'] += 15
    else:
        conf['warnings'].append(f"Low prob ({model_prob:.1f}%)")
    
    if value_edge is not None:
        if value_edge >= 10:
            conf['score'] += 20
            conf['reasons'].append(f"Big value +{value_edge:.1f}%")
        elif value_edge >= 6:
            conf['score'] += 15
            conf['reasons'].append(f"Good value +{value_edge:.1f}%")
        elif value_edge >= 3:
            conf['score'] += 10
        elif value_edge >= 0:
            conf['score'] += 5
        else:
            conf['warnings'].append(f"Unders {value_edge:.1f}%")
    
    if ev is not None:
        if ev >= 0.15:
            conf['score'] += 10
            conf['reasons'].append(f"Strong EV +{ev:.2f}")
        elif ev >= 0.05:
            conf['score'] += 5
            conf['reasons'].append(f"Positive EV +{ev:.2f}")
        elif ev < 0:
            conf['warnings'].append(f"Negative EV {ev:.2f}")
    
    if len(all_picks) >= 2:
        gap = model_prob - all_picks[1].get('model_prob', 0)
        if gap >= 8:
            conf['score'] += 20
            conf['reasons'].append(f"Dominates (+{gap:.1f}%)")
        elif gap >= 5:
            conf['score'] += 15
            conf['reasons'].append(f"Clear leader (+{gap:.1f}%)")
        elif gap >= 3:
            conf['score'] += 10
        elif gap >= 1:
            conf['score'] += 5
        else:
            conf['warnings'].append("Tight race")
    
    strong_count = sum(1 for v in features.values() if v >= 65)
    if strong_count >= 5:
        conf['score'] += 15
        conf['reasons'].append(f"{strong_count} factors strong")
    elif strong_count >= 3:
        conf['score'] += 10
    elif strong_count >= 2:
        conf['score'] += 5
    
    if relative_rating >= 1.15:
        conf['score'] += 5
        conf['reasons'].append(f"Stands out in field")
    
    if pick.get('market_odds', 0) > 25:
        conf['score'] -= 15
        conf['warnings'].append("Outsider")
    
    if conf['score'] >= 70:
        conf['level'] = 'HIGH'
    elif conf['score'] >= 40:
        conf['level'] = 'MEDIUM'
    else:
        conf['level'] = 'LOWER'
    
    return conf


def get_staking_tier(confidence, model_prob):
    """Determine staking tier based on confidence and probability."""
    for tier_name in ['lock', 'strong', 'bet']:
        tier = STAKING_TIERS[tier_name]
        if confidence >= tier['min_confidence'] and model_prob >= tier['min_prob']:
            return {
                'tier': tier_name,
                'units': tier['units'],
                'stars': tier['stars'],
                'label': tier['label'],
                'status': 'BET'
            }
    return {
        'tier': 'pass',
        'units': 0,
        'stars': '',
        'label': 'WATCH',
        'status': 'WATCH'
    }


def calculate_selection_score(pick, all_picks, race):
    """Calculate comprehensive selection score."""
    score, reasons, warnings = 0, [], []
    
    model_prob = pick.get('model_prob', 0)
    value_edge = pick.get('value_edge')
    ev = pick.get('ev')
    features = pick.get('features', {})
    style = pick.get('style', '')
    bias_adj = pick.get('bias_adj', 0)
    relative_rating = pick.get('relative_rating', 1.0)
    
    if model_prob >= 35:
        score += 35
        reasons.append(f"Dominant {model_prob:.1f}% probability")
    elif model_prob >= 25:
        score += 30
        reasons.append(f"Excellent {model_prob:.1f}% win probability")
    elif model_prob >= 20:
        score += 25
        reasons.append(f"Strong {model_prob:.1f}% probability")
    elif model_prob >= 16:
        score += 20
        reasons.append(f"Good {model_prob:.1f}% chance")
    elif model_prob >= 12:
        score += 15
        reasons.append(f"Solid {model_prob:.1f}% probability")
    elif model_prob >= 10:
        score += 10
    else:
        score += 5
        warnings.append(f"Low probability ({model_prob:.1f}%)")
    
    if value_edge is not None:
        if value_edge >= 15:
            score += 25
            reasons.append(f"Huge value +{value_edge:.1f}%")
        elif value_edge >= 10:
            score += 20
            reasons.append(f"Strong value +{value_edge:.1f}%")
        elif value_edge >= 5:
            score += 15
            reasons.append(f"Good value +{value_edge:.1f}%")
        elif value_edge >= 0:
            score += 10
            reasons.append("Fair value")
        elif value_edge >= -5:
            score += 5
            warnings.append(f"Under value ({value_edge:.1f}%)")
        else:
            warnings.append(f"Under market ({value_edge:.1f}%)")
    else:
        score += 8
    
    if ev is not None:
        if ev >= 0.10:
            score += 5
            reasons.append(f"Excellent EV +{ev:.2f}")
        elif ev >= 0.05:
            score += 3
        elif ev < 0:
            warnings.append(f"Negative EV ({ev:.2f})")
    
    strong_factors = [k for k, v in features.items() if v >= 70]
    weak_factors = [k for k, v in features.items() if v < 40]
    
    if len(strong_factors) >= 6:
        score += 20
        reasons.append(f"{len(strong_factors)} strong factors")
    elif len(strong_factors) >= 4:
        score += 15
        reasons.append(f"Strengths: {', '.join(strong_factors[:2])}")
    elif len(strong_factors) >= 2:
        score += 10
    else:
        score += 5
        warnings.append("Limited form")
    
    if len(weak_factors) >= 3:
        score -= 5
    
    if len(all_picks) >= 2:
        gap = model_prob - all_picks[1].get('model_prob', 0)
        if gap >= 12:
            score += 10
            reasons.append(f"Dominates +{gap:.1f}%")
        elif gap >= 8:
            score += 8
            reasons.append(f"Clear margin +{gap:.1f}%")
        elif gap >= 5:
            score += 6
        elif gap >= 2:
            score += 4
        else:
            score += 2
            warnings.append("Competitive race")
    
    track_score = 5
    if abs(bias_adj) >= 5:
        track_score += 3
        reasons.append(f"Track suits {style.replace('_', ' ')}")
    if features.get('cd_history', 50) >= 80:
        track_score += 2
        reasons.append("Proven CD record")
    score += track_score
    
    if relative_rating >= 1.2:
        score += 5
        reasons.append("Stands out in weak field")
    elif relative_rating <= 0.85:
        score -= 3
        warnings.append("Strong field")
    
    if features.get('trajectory', 50) >= 70:
        score += 3
        reasons.append("Improving form")
    
    if score >= 70:
        confidence_level = "HIGH"
    elif score >= STAKING_CONFIG['lower_confidence_threshold']:
        confidence_level = "MEDIUM"
    else:
        confidence_level = "LOWER"
    
    return {
        'score': score,
        'reasons': reasons[:4],
        'warnings': warnings[:2],
        'confidence_level': confidence_level
    }



class TipGenerator:
    def __init__(self, model, bankroll=STAKING_CONFIG['default_bankroll'], mc_config=None):
        self.model = model
        self.bankroll = bankroll
        self.base_unit = bankroll * STAKING_CONFIG['unit_percent']
        self.daily_units_used = 0
        self.filtered_counts = {'trials': 0, 'past': 0}
        self.mc_config = {**MC_DEFAULTS, **(mc_config or {})}
    
    def generate_tips_for_date(self, races, target_tracks=None, include_past=True):
        """Generate tips for a set of races."""
        if target_tracks is None:
            target_tracks = TARGET_TRACKS
        
        target_tracks_lower = [t.lower() for t in target_tracks]
        races_by_track = defaultdict(list)
        now = datetime.now()
        
        for race in races:
            if not race.is_valid:
                continue
            
            course_lower = race.course.lower()
            matched = False
            
            for target in target_tracks_lower:
                if target in course_lower or course_lower in target:
                    matched = True
                    break
                if set(target.split()) & set(course_lower.split()):
                    matched = True
                    break
            
            if matched:
                if self._is_trial(race):
                    self.filtered_counts['trials'] += 1
                    continue
                if not include_past and self._has_race_passed(race, now):
                    self.filtered_counts['past'] += 1
                    continue
                races_by_track[race.course].append(race)
        
        all_tips = {}
        for track, track_races in races_by_track.items():
            all_tips[track] = self._generate_tips_for_track(track, track_races)
        
        return all_tips

    def generate_mc_tips_for_date(self, races, target_tracks=None, include_past=True):
        """Generate Monte Carlo tips (up to 6 per track)."""
        if target_tracks is None:
            target_tracks = TARGET_TRACKS
        
        target_tracks_lower = [t.lower() for t in target_tracks]
        races_by_track = defaultdict(list)
        now = datetime.now()
        
        for race in races:
            if not race.is_valid:
                continue
            
            course_lower = race.course.lower()
            matched = False
            for target in target_tracks_lower:
                if target in course_lower or course_lower in target:
                    matched = True
                    break
                if set(target.split()) & set(course_lower.split()):
                    matched = True
                    break
            if not matched:
                continue
            
            if self._is_trial(race):
                self.filtered_counts['trials'] += 1
                continue
            if not include_past and self._has_race_passed(race, now):
                self.filtered_counts['past'] += 1
                continue
            races_by_track[race.course].append(race)
        
        all_tips = {}
        for track, track_races in races_by_track.items():
            all_tips[track] = self._generate_mc_tips_for_track(track, track_races)
        return all_tips
    
    def _is_trial(self, race):
        race_name = (race.race_name or '').lower()
        return 'trial' in race_name or 'jump out' in race_name
    
    def _has_race_passed(self, race, now):
        if getattr(race, 'date_dt', None):
            return race.date_dt.date() < now.date()
        dt = safe_parse_date(getattr(race, 'date', None))
        return dt.date() < now.date() if dt else False
    
    def _generate_tips_for_track(self, track, races):
        """Generate tips for a single track."""
        candidates = []
        
        for race in races:
            analysis = self.model.analyze(race)
            if not analysis:
                continue
            
            top = analysis[0]
            selection = calculate_selection_score(top, analysis, race)
            candidates.append({
                'race': race,
                'pick': top,
                'analysis': analysis,
                'selection': selection
            })
        
        candidates.sort(key=lambda x: -x['selection']['score'])
        
        tips = []
        for i, candidate in enumerate(candidates[:TIPS_PER_TRACK]):
            tips.append(self._create_tip(candidate, i + 1, len(candidates)))
        
        return tips

    def _generate_mc_tips_for_track(self, track, races):
        """Monte Carlo powered tips for a single track."""
        candidates = []
        for race in races:
            analysis = self.model.analyze(race)
            if not analysis:
                continue
            
            field_odds = []
            for a in analysis:
                for od in [a.get('sp'), a.get('market_odds'), a.get('model_odds')]:
                    if od and od > 1:
                        field_odds.append(od)
                mpd = a.get('model_prob_dec')
                if mpd and mpd > 0:
                    fair = 1 / mpd
                    if fair > 1:
                        field_odds.append(fair)
            mc_results = simulate_race_monte_carlo(
                race, analysis, self.model,
                mc_sims=self.mc_config.get('mc_sims', MC_DEFAULTS['mc_sims']),
                seed=self.mc_config.get('seed', MC_DEFAULTS['seed']) + safe_int(race.race_number, 0),
                mc_model=self.mc_config.get('mc_model', MC_DEFAULTS['mc_model']),
                pace_mode=self.mc_config.get('pace_mode', MC_DEFAULTS['pace_mode']),
                uncertainty=self.mc_config.get('uncertainty', MC_DEFAULTS['uncertainty'])
            )
            if not mc_results:
                continue
            
            merged = []
            for base, mc in zip(analysis, mc_results):
                market_odds = infer_market_odds(base, field_odds)
                if not market_odds:
                    market_odds = base.get('market_odds') or base.get('sp')
                if not market_odds and base.get('model_prob_dec'):
                    fair = 1 / base['model_prob_dec']
                    market_odds = fair if fair > 1 else None
                candidate = {**base, **mc}
                candidate['market_odds'] = market_odds
                candidate['win_prob_sim_pct'] = candidate['win_prob_sim'] * 100
                candidate['top3_prob_sim_pct'] = candidate['top3_prob_sim'] * 100
                candidate['selection_score'] = mc_selection_score(candidate)
                candidate['playable'] = mc_is_playable(candidate)
                if market_odds and market_odds < 2.2 and (candidate.get('ev') is None or candidate.get('ev') <= 0):
                    candidate['selection_score'] *= 0.85  # de-emphasize skinny unders
                merged.append(candidate)
            
            playable = [m for m in merged if m['playable']]
            if playable:
                playable.sort(key=lambda x: -x['selection_score'])
                best = playable[0]
            else:
                merged.sort(key=lambda x: -x['selection_score'])
                best = merged[0] if merged else None
            
            if best:
                best['race'] = race
                best['analysis'] = analysis
                candidates.append(best)
        
        candidates.sort(key=lambda x: -x.get('selection_score', 0))
        tips = []
        track_units_used = 0
        for i, cand in enumerate(candidates[:TIPS_PER_TRACK]):
            tip, used_units = self._create_mc_tip(cand, i + 1, len(candidates), track_units_used)
            track_units_used += used_units
            tips.append(tip)
        return tips
    
    def _create_tip(self, candidate, rank, total):
        """Create a tip from a candidate."""
        race = candidate['race']
        pick = candidate['pick']
        selection = candidate['selection']
        
        model_prob = pick.get('model_prob', 0)
        fair_odds = round(100 / model_prob, 2) if model_prob > 0 else 0
        
        conf = calculate_confidence(pick, candidate['analysis'])
        staking = get_staking_tier(conf['score'], model_prob)
        
        if staking['units'] > 0:
            remaining = STAKING_CONFIG['max_daily_units'] - self.daily_units_used
            if staking['units'] > remaining:
                if remaining > 0:
                    staking['units'] = remaining
                    staking['status'] = 'CAPPED'
                else:
                    staking['units'] = 0
                    staking['status'] = 'LIMIT'
            self.daily_units_used += staking['units']
        
        kelly_fraction = KELLY_FRACTIONS.get(conf['level'], KELLY_FRACTION_DEFAULT)
        kelly_bet = kelly_stake(model_prob / 100, pick.get('sp', 2.0), kelly_fraction, MAX_KELLY_STAKE)
        staking['stake'] = staking['units'] * self.base_unit
        staking['kelly_stake'] = kelly_bet * self.bankroll
        
        reasons = selection['reasons'][:4]
        while len(reasons) < 4:
            if len(reasons) == 0:
                reasons.append(f"Ranked #{rank} of {total}")
            elif len(reasons) == 1:
                reasons.append(f"Style: {pick.get('style', 'unknown').replace('_', ' ').title()}")
            elif len(reasons) == 2:
                features = pick.get('features', {})
                if features:
                    best = max(features.items(), key=lambda x: x[1])
                    reasons.append(f"Best: {best[0].replace('_', ' ').title()} ({best[1]:.0f})")
                else:
                    reasons.append(f"Score: {selection['score']}/100")
            else:
                reasons.append(f"Confidence: {selection['confidence_level']}")
        
        return {
            'rank': rank,
            'race': race,
            'race_number': race.race_number,
            'race_name': race.race_name,
            'race_time': race.off_time,
            'distance': race.distance,
            'horse': pick['horse'],
            'number': pick.get('number', 0),
            'horse_id': pick.get('horse_id', ''),
            'barrier': pick.get('barrier', 0),
            'jockey': pick.get('jockey', ''),
            'trainer': pick.get('trainer', ''),
            'model_prob': model_prob,
            'market_odds': pick.get('market_odds', 0),
            'sp': pick.get('sp', 0),
            'fair_odds': fair_odds,
            'value_edge': pick.get('value_edge'),
            'ev': pick.get('ev'),
            'kelly': pick.get('kelly', 0),
            'selection_score': selection['score'],
            'confidence_score': conf['score'],
            'confidence_level': selection['confidence_level'],
            'reasons': reasons,
            'warnings': selection['warnings'],
            'staking': staking,
            'features': pick.get('features', {}),
            'style': pick.get('style', ''),
            'bias_adj': pick.get('bias_adj', 0),
            'relative_rating': pick.get('relative_rating', 1.0),
            'field_strength': pick.get('field_strength', 50),
        }

    def _create_mc_tip(self, candidate, rank, total, track_units_used):
        """Create Monte Carlo tip with expanded analytics."""
        race = candidate['race']
        daily_remaining = STAKING_CONFIG['max_daily_units'] - self.daily_units_used
        track_remaining = MC_STAKING_CAPS['per_track_units'] - track_units_used
        
        staking = mc_compute_staking(
            candidate,
            self.mc_config.get('risk_mode', MC_DEFAULTS['risk_mode']),
            self.base_unit,
            self.bankroll,
            daily_remaining,
            track_remaining
        )
        used_units = staking['units'] if staking['status'] in ['BET', 'CAPPED'] else 0
        self.daily_units_used += used_units
        
        fair_odds = round(100 / candidate['model_prob'], 2) if candidate.get('model_prob') else 0
        
        reasons, warnings = self._build_mc_reasons(candidate, rank, total)
        if candidate.get('std_pos', 0) > 2.2:
            warnings.append("High variance; needs tempo to suit")
        if candidate.get('scenario_sensitivity', 0) > 0.08:
            warnings.append("Reliant on pace/bias scenario")
        if candidate.get('market_odds') is None:
            warnings.append("Missing market odds; treating as WATCH unless strong edge")
        if candidate.get('playable') is False:
            warnings.append("Fails base filters (prob/edge/stability)")
        
        tip = {
            'rank': rank,
            'race': race,
            'race_number': race.race_number,
            'race_name': race.race_name,
            'race_time': race.off_time,
            'distance': race.distance,
            'horse': candidate['horse'],
            'number': candidate.get('number', 0),
            'horse_id': candidate.get('horse_id', ''),
            'barrier': candidate.get('barrier', 0),
            'jockey': candidate.get('jockey', ''),
            'trainer': candidate.get('trainer', ''),
            'model_prob': candidate.get('model_prob', 0),
            'market_odds': candidate.get('market_odds') or candidate.get('sp') or fair_odds,
            'sp': candidate.get('market_odds') or candidate.get('sp') or fair_odds,
            'fair_odds': fair_odds,
            'value_edge': candidate.get('value_edge'),
            'ev': candidate.get('ev'),
            'kelly': candidate.get('kelly', 0),
            'selection_score': candidate.get('selection_score', 0) * 100,
            'confidence_score': candidate.get('stability_score', 0),
            'confidence_level': 'HIGH' if candidate.get('stability_score', 0) >= 70 else (
                'MEDIUM' if candidate.get('stability_score', 0) >= 50 else 'LOWER'
            ),
            'risk_mode': self.mc_config.get('risk_mode', MC_DEFAULTS['risk_mode']),
            'reasons': reasons[:4],
            'warnings': warnings[:2],
            'staking': staking,
            'features': candidate.get('features', {}),
            'style': candidate.get('style', ''),
            'bias_adj': candidate.get('bias_adj', 0),
            'relative_rating': candidate.get('relative_rating', 1.0),
            'field_strength': candidate.get('field_strength', 50),
            'mc': {
                'win_prob_sim': candidate.get('win_prob_sim', 0),
                'top2_prob_sim': candidate.get('top2_prob_sim', 0),
                'top3_prob_sim': candidate.get('top3_prob_sim', 0),
                'expected_pos': candidate.get('expected_pos', 0),
                'std_pos': candidate.get('std_pos', 0),
                'ci_lower': candidate.get('ci_lower', 0),
                'ci_upper': candidate.get('ci_upper', 0),
                'stability_score': candidate.get('stability_score', 0),
                'scenario_sensitivity': candidate.get('scenario_sensitivity', 0),
                'pace_splits': candidate.get('pace_splits', {}),
            }
        }
        return tip, used_units

    def _build_mc_reasons(self, candidate, rank, total):
        reasons = []
        win = candidate.get('win_prob_sim', 0) * 100
        base = candidate.get('model_prob', 0)
        edge = candidate.get('value_edge')
        ev = candidate.get('ev')
        stability = candidate.get('stability_score', 0)
        ci_lower = candidate.get('ci_lower', 0) * 100
        ci_upper = candidate.get('ci_upper', 0) * 100
        
        reasons.append(f"Base {base:.1f}% → MC {win:.1f}% ({'+' if edge else ''}{edge or 0:.1f}% edge)")
        reasons.append(f"Robustness: stability {stability:.0f}/100 | CI {ci_lower:.1f}-{ci_upper:.1f}%")
        
        pace_splits = candidate.get('pace_splits', {})
        if pace_splits:
            best_scenario = max(pace_splits.items(), key=lambda x: x[1])
            reasons.append(f"Pace/bias: best in {best_scenario[0]} ({best_scenario[1]*100:.1f}%)")
        else:
            reasons.append("Pace/bias: neutral scenario")
        
        features = candidate.get('features', {})
        if features:
            best_feat = max(features.items(), key=lambda x: x[1])
            reasons.append(f"Feature strength: {best_feat[0].replace('_', ' ').title()} ({best_feat[1]:.0f})")
        else:
            reasons.append(f"Ranked #{rank} of {total}")
        
        warnings = []
        if ev is not None and ev < 0:
            warnings.append(f"Negative EV {ev:.2f}")
        if edge is not None and edge < 0:
            warnings.append(f"Market shorter than fair ({edge:.1f}%)")
        
        return reasons, warnings



def format_track_tips_mc(track, tips, date):
    """Format Monte Carlo tips for a track."""
    lines = [
        "",
        "=" * 92,
        f"  TRACK: {track.upper():<30} DATE: {date}",
        "=" * 92
    ]
    
    if not tips:
        lines.append("  No races available.")
        return "\n".join(lines)
    
    for tip in tips:
        lines.append(format_single_tip_mc(tip))
    
    lines.extend(["", "-" * 92])
    total_units = sum(t['staking']['units'] for t in tips if t['staking']['status'] in ['BET', 'CAPPED'])
    bet_tips = [t for t in tips if t['staking']['status'] in ['BET', 'CAPPED']]
    lines.append(f"  SUMMARY: {len(tips)} tips | {len(bet_tips)} bets ({total_units:.0f}u)")
    
    return "\n".join(lines)


def format_single_tip_mc(tip):
    """Format a single Monte Carlo tip."""
    lines = []
    staking = tip['staking']
    mc = tip['mc']
    conf_icon = {'HIGH': '🟢', 'MEDIUM': '🟡', 'LOWER': '🟠'}.get(tip['confidence_level'], '⚪')
    
    if staking['status'] == 'BET':
        stake_str = f"{staking['units']}u (${staking['stake']:.0f})"
    elif staking['status'] == 'CAPPED':
        stake_str = f"{staking['units']}u CAPPED"
    elif staking['status'] == 'LIMIT':
        stake_str = "LIMIT (caps reached)"
    else:
        stake_str = "👁 WATCH"
    
    number_str = f"#{tip['number']} " if tip.get('number') else ""
    sp_str = f"(SP ${tip['sp']:.2f})" if tip.get('sp') else "(SP N/A)"
    
    lines.append(f"\n  TIP {tip['rank']}: Race {tip['race_number']} - {number_str}{tip['horse']} {sp_str}")
    lines.append(f"  {'-' * 88}")
    
    lines.append("  BASE MODEL")
    lines.append(f"  - Model: {tip['model_prob']:.1f}% | Fair: ${tip['fair_odds']:.2f} | Edge: {tip['value_edge'] or 0:+.1f}%")
    if tip.get('ev') is not None:
        lines.append(f"  - EV: {tip['ev']:+.3f}")
    lines.append("")
    
    lines.append("  MONTE CARLO")
    lines.append(f"  - Win: {mc['win_prob_sim']*100:5.1f}% | Top2: {mc['top2_prob_sim']*100:5.1f}% | Top3: {mc['top3_prob_sim']*100:5.1f}%")
    lines.append(f"  - E[pos]: {mc['expected_pos']:.2f} | Std(pos): {mc['std_pos']:.2f} | CI: [{mc['ci_lower']*100:.1f}-{mc['ci_upper']*100:.1f}]%")
    
    scen_sens = mc.get('scenario_sensitivity', 0) * 100
    lines.append("  ROBUSTNESS")
    lines.append(f"  - Stability: {mc['stability_score']:.0f}/100 | Scenario swing: {scen_sens:.1f} pp")
    if mc.get('pace_splits'):
        best = max(mc['pace_splits'].items(), key=lambda x: x[1])
        lines.append(f"  - Pace/bias best: {best[0]} ({best[1]*100:.1f}%)")
    lines.append("")
    
    lines.append("  STAKING")
    lines.append(f"  - {conf_icon} Confidence: {tip['confidence_level']} | Selection: {tip['selection_score']:.1f}")
    lines.append(f"  - Mode: {staking.get('mode', tip.get('risk_mode', 'fractional_kelly'))} | {stake_str}")
    if staking.get('kelly_pct', 0) > 0:
        lines.append(f"  - Kelly stake: {staking['kelly_pct']:.2f}% of bankroll cap")
    lines.append("")
    
    lines.append("  WHY")
    for i, reason in enumerate(tip['reasons'][:4], 1):
        lines.append(f"  {i}. {reason}")
    
    if tip.get('warnings'):
        lines.append("")
        lines.append("  WARNINGS")
        for w in tip['warnings'][:2]:
            lines.append(f"  - ⚠ {w}")
    
    lines.append("")
    return "\n".join(lines)


def format_betting_summary_mc(all_tips, bankroll, base_unit):
    """Betting slip summary for Monte Carlo tips."""
    lines = [
        "",
        "=" * 92,
        "  📋 MC BETTING SLIP SUMMARY",
        "=" * 92,
        "",
        f"  {'Track':<20} {'Race':<6} {'Horse':<22} {'SP':>7} {'Win%':>7} {'Edge':>8} {'Units':>6}",
        "  " + "-" * 80
    ]
    
    total_units, total_bets = 0, 0
    for track, tips in sorted(all_tips.items()):
        for tip in tips:
            staking = tip['staking']
            if staking['status'] in ['BET', 'CAPPED']:
                total_units += staking['units']
                total_bets += 1
            horse_label = f"#{tip.get('number')} {tip['horse']}" if tip.get('number') else tip['horse']
            sp_label = f"${tip['sp']:.2f}" if tip.get('sp') else "N/A"
            win_label = f"{tip['mc']['win_prob_sim']*100:>6.1f}%"
            edge_label = f"{tip.get('value_edge') or 0:+.1f}%"
            units_label = f"{staking['units']:.0f}u" if staking['status'] in ['BET', 'CAPPED'] else '👁'
            lines.append(f"  {track[:18]:<20} R{tip['race_number']:<5} {horse_label[:22]:<22} {sp_label:>7} {win_label:>7} {edge_label:>8} {units_label:>6}")
    
    lines.append("  " + "-" * 80)
    lines.append(f"  TOTAL: {total_bets} bet(s) | {total_units:.0f} unit(s) | Base unit ${base_unit:.2f}")
    lines.append("")
    return "\n".join(lines)

def format_track_tips(track, tips, date):
    """Format tips for a track."""
    lines = [
        "",
        "=" * 80,
        f"  TRACK: {track.upper():<30} DATE: {date}",
        "=" * 80
    ]
    
    if not tips:
        lines.append("  No races available.")
        return "\n".join(lines)
    
    for tip in tips:
        lines.append(format_single_tip(tip))
    
    lines.extend(["", "-" * 80])
    total_units = sum(t['staking']['units'] for t in tips)
    bet_tips = [t for t in tips if t['staking']['status'] in ['BET', 'CAPPED']]
    lines.append(f"  SUMMARY: {len(tips)} tips | {len(bet_tips)} bets ({total_units:.0f}u)")
    
    return "\n".join(lines)


def format_single_tip(tip):
    """Format a single tip."""
    lines = []
    staking = tip['staking']
    conf_icon = {'HIGH': '🟢', 'MEDIUM': '🟡', 'LOWER': '🟠'}.get(tip['confidence_level'], '⚪')
    
    if staking['status'] == 'BET':
        stake_str = f"{staking['stars']} {staking['units']}u (${staking['stake']:.0f})"
    elif staking['status'] == 'CAPPED':
        stake_str = f"{staking['stars']} {staking['units']}u CAPPED"
    else:
        stake_str = "👁 WATCH"
    
    number_str = f"#{tip['number']} " if tip.get('number') else ""
    sp_str = f" (SP ${tip['sp']:.2f})" if tip.get('sp') else ""
    
    lines.append(f"\n  TIP {tip['rank']}: Race {tip['race_number']} - {number_str}{tip['horse']}{sp_str}")
    lines.append(f"  {'-' * 70}")
    
    lines.append("  **BASE TIP**")
    lines.append(f"  - Race: {(tip['race_name'] or '')[:45]}")
    lines.append(f"  - Distance: {tip['distance']}m | Barrier: {tip['barrier']} | Jockey: {tip['jockey'][:20]}")
    lines.append("")
    
    lines.append("  **KEY METRICS**")
    lines.append(f"  - Model: {tip['model_prob']:.1f}% | Fair: ${tip['fair_odds']:.2f}")
    lines.append(f"  - SP: ${tip['sp']:.2f}" if tip.get('sp') else "  - SP: N/A")
    if tip['value_edge'] is not None:
        lines.append(f"  - Edge: {'+' if tip['value_edge'] > 0 else ''}{tip['value_edge']:.1f}%")
    if tip.get('ev') is not None:
        lines.append(f"  - EV: {'+' if tip['ev'] > 0 else ''}{tip['ev']:.3f}")
    if tip.get('kelly', 0) > 0:
        lines.append(f"  - Kelly: {tip['kelly']:.1f}%")
    
    lines.append(f"  - Field Strength: {tip.get('field_strength', 50):.1f} | Relative: {tip.get('relative_rating', 1.0):.2f}x")
    lines.append("")
    
    lines.append("  **WHY**")
    for i, reason in enumerate(tip['reasons'][:4], 1):
        lines.append(f"  {i}. {reason}")
    lines.append("")
    
    lines.append("  **STAKING**")
    lines.append(f"  - {conf_icon} Confidence: {tip['confidence_level']} (Score: {tip['selection_score']}/100)")
    lines.append(f"  - 💰 {stake_str}")
    if tip.get('warnings'):
        for w in tip['warnings'][:1]:
            lines.append(f"  - ⚠ {w}")
    lines.append("")
    
    return "\n".join(lines)


def format_betting_summary(all_tips, bankroll, base_unit):
    """Format betting summary."""
    lines = [
        "",
        "=" * 80,
        "  📋 BETTING SLIP SUMMARY",
        "=" * 80,
        "",
        f"  {'Track':<20} {'Race':<6} {'Horse':<22} {'SP':>7} {'EV':>8} {'Units':>6}",
        "  " + "-" * 72
    ]
    
    total_units, total_bets = 0, 0
    
    for track, tips in sorted(all_tips.items()):
        for tip in tips:
            staking = tip['staking']
            status = staking['stars'] if staking['status'] in ['BET', 'CAPPED'] else "👁"
            
            if staking['status'] in ['BET', 'CAPPED']:
                total_units += staking['units']
                total_bets += 1
            
            horse_label = f"#{tip.get('number')} {tip['horse']}" if tip.get('number') else tip['horse']
            sp_label = f"${tip['sp']:.2f}" if tip.get('sp') else "N/A"
            ev_label = f"{tip['ev']:+.3f}" if tip.get('ev') else "N/A"
            
            lines.append(
                f"  {track[:20]:<20} R{tip['race_number']:<5} "
                f"{horse_label[:22]:<22} {sp_label:>7} {ev_label:>8} "
                f"{staking['units']:>5.0f} {status}"
            )
    
    lines.extend([
        "  " + "-" * 72,
        f"  {'TOTAL':<20} {'':<6} {'':<22} {'':<7} {'':<8} {total_units:>5.0f}  ({total_bets} bets)",
        "",
        f"  Bankroll: ${bankroll:.0f} | Unit: ${base_unit:.0f} | Stake: ${total_units * base_unit:.0f}",
        "=" * 80
    ])
    
    return "\n".join(lines)



def run_backtest(races, race_dicts, training_days=60, supp_stats=None, use_ml=False):
    """
    Run enhanced backtest with additional metrics: Brier score (probability calibration),
    Precision@top3, and stratified ROI by odds bucket.
    """
    print(f"\n  Running backtest on {len(races)} races...")
    
    race_pairs = list(zip(races, race_dicts))
    
    def race_day(r):
        if getattr(r, 'date_dt', None):
            return r.date_dt.date()
        dt = safe_parse_date(getattr(r, 'date', None))
        return dt.date() if dt else None
    
    race_pairs.sort(key=lambda x: race_day(x[0]) or datetime.min.date())
    dates = sorted(set(race_day(r) for r, _ in race_pairs if race_day(r)))
    
    if len(dates) < training_days + 30:
        print(f"  Need more data: have {len(dates)} days, need {training_days + 30}")
        return {}
    
    test_dates = dates[training_days:]
    print(f"  Testing {test_dates[0].isoformat()} to {test_dates[-1].isoformat()} ({len(test_dates)} days)")
    
    model = None
    daily_results = defaultdict(lambda: {'predictions': 0, 'wins': 0})
    betting_results = []
    
    brier_scores = []
    top3_correct = 0
    top3_total = 0
    odds_buckets = {
        'short': {'bets': 0, 'wins': 0, 'profit': 0},
        'medium': {'bets': 0, 'wins': 0, 'profit': 0},
        'long': {'bets': 0, 'wins': 0, 'profit': 0},
        'outsider': {'bets': 0, 'wins': 0, 'profit': 0},
    }
    
    for i, test_date in enumerate(test_dates):
        train_pairs = [(r, d) for r, d in race_pairs if (race_day(r) and race_day(r) < test_date)]
        test_pairs = [(r, d) for r, d in race_pairs if (race_day(r) and race_day(r) == test_date)]
        
        if not test_pairs:
            continue
        
        if model is None or i % 30 == 0:
            train_races = [r for r, _ in train_pairs]
            train_dicts = [d for _, d in train_pairs]
            model = RacingModel(supp_stats, use_ml=use_ml)
            model.train(train_races, train_dicts, cutoff_date=test_date)
        
        for race, _ in test_pairs:
            if not race.is_valid:
                continue
            
            analysis = model.analyze(race)
            if not analysis:
                continue
            
            top = analysis[0]
            winner = next((r for r in race.runners if r.position == 1), None)
            if not winner:
                continue
            
            is_win = top['horse'] == winner.horse
            daily_results[test_date]['predictions'] += 1
            if is_win:
                daily_results[test_date]['wins'] += 1
            
            for pred in analysis:
                actual = 1 if pred['horse'] == winner.horse else 0
                prob = pred['model_prob'] / 100
                brier_scores.append((prob - actual) ** 2)
            
            top3_horses = [p['horse'] for p in analysis[:3]]
            if winner.horse in top3_horses:
                top3_correct += 1
            top3_total += 1
            
            for pred in analysis:
                edge = pred.get('value_edge')
                odds = pred.get('market_odds', 0)
                
                if edge and edge >= 5 and MIN_ODDS <= odds <= MAX_ODDS:
                    won = pred['horse'] == winner.horse
                    profit = (odds - 1) if won else -1
                    
                    betting_results.append({
                        'date': test_date,
                        'horse': pred['horse'],
                        'odds': odds,
                        'edge': edge,
                        'won': won,
                        'profit': profit,
                        'prob': pred['model_prob']
                    })
                    
                    if odds < 3.0:
                        bucket = 'short'
                    elif odds < 6.0:
                        bucket = 'medium'
                    elif odds < 15.0:
                        bucket = 'long'
                    else:
                        bucket = 'outsider'
                    
                    odds_buckets[bucket]['bets'] += 1
                    if won:
                        odds_buckets[bucket]['wins'] += 1
                    odds_buckets[bucket]['profit'] += profit
        
        if (i + 1) % 30 == 0:
            wins = sum(daily_results[d]['wins'] for d in daily_results)
            total = sum(daily_results[d]['predictions'] for d in daily_results)
            print(f"    Day {i+1}: {wins}/{total} = {wins/total*100 if total else 0:.1f}%")
    
    top_wins = sum(daily_results[d]['wins'] for d in daily_results)
    top_total = sum(daily_results[d]['predictions'] for d in daily_results)
    bet_wins = sum(1 for b in betting_results if b['won'])
    bet_profit = sum(b['profit'] for b in betting_results)
    
    brier = sum(brier_scores) / len(brier_scores) if brier_scores else 1.0
    precision_top3 = top3_correct / top3_total * 100 if top3_total else 0
    
    return {
        'top_pick': {
            'total': top_total,
            'wins': top_wins,
            'strike_rate': top_wins / top_total * 100 if top_total else 0
        },
        'betting': {
            'bets': len(betting_results),
            'wins': bet_wins,
            'strike_rate': bet_wins / len(betting_results) * 100 if betting_results else 0,
            'profit': bet_profit,
            'roi': bet_profit / len(betting_results) * 100 if betting_results else 0
        },
        'calibration': {
            'brier_score': brier,
            'precision_top3': precision_top3,
        },
        'odds_buckets': odds_buckets,
    }


def print_backtest_results(results):
    """Print formatted backtest results."""
    print("\n" + "=" * 70)
    print("  BACKTEST RESULTS")
    print("=" * 70)
    
    tp = results.get('top_pick', {})
    print(f"\n  Top Pick: {tp.get('wins', 0)}/{tp.get('total', 0)} = {tp.get('strike_rate', 0):.1f}%")
    
    bet = results.get('betting', {})
    print(f"\n  Betting ({bet.get('bets', 0)} bets):")
    print(f"    Strike Rate: {bet.get('strike_rate', 0):.1f}%")
    print(f"    Profit: {bet.get('profit', 0):+.2f}u")
    print(f"    ROI: {bet.get('roi', 0):+.1f}%")
    
    cal = results.get('calibration', {})
    print(f"\n  Calibration:")
    print(f"    Brier Score: {cal.get('brier_score', 1.0):.4f} (lower is better, <0.25 good)")
    print(f"    Precision@Top3: {cal.get('precision_top3', 0):.1f}%")
    
    buckets = results.get('odds_buckets', {})
    print(f"\n  ROI by Odds Bucket:")
    for bucket, stats in buckets.items():
        if stats['bets'] > 0:
            roi = stats['profit'] / stats['bets'] * 100
            sr = stats['wins'] / stats['bets'] * 100
            print(f"    {bucket.title():>10}: {stats['bets']:>4} bets, {sr:>5.1f}% SR, {roi:>+6.1f}% ROI")



def main():
    parser = argparse.ArgumentParser(description="Horse Racing System v8.3_mc - Monte Carlo Integrated")
    parser.add_argument("--data_dir", type=str, required=True, help="Racecard data directory")
    parser.add_argument("--train_dir", type=str, help="Historical data directory (for training)")
    parser.add_argument("--stats", type=str, help="Supplementary stats JSON file")
    parser.add_argument("--date", type=str, help="Date for tips (YYYY-MM-DD)")
    parser.add_argument("--tracks", type=str, help="Comma-separated tracks")
    parser.add_argument("--bankroll", type=float, default=1000, help="Bankroll size")
    parser.add_argument("--tips", action="store_true", help="Generate tips (all qualifying)")
    parser.add_argument("--tips6", action="store_true", help="Generate 6 tips per track")
    parser.add_argument("--mc_tips6", action="store_true", help="Generate Monte Carlo tips (6 per track)")
    parser.add_argument("--mc_sims", type=int, default=MC_DEFAULTS['mc_sims'], help="Monte Carlo simulations per race")
    parser.add_argument("--seed", type=int, default=MC_DEFAULTS['seed'], help="RNG seed for reproducibility")
    parser.add_argument("--mc_model", type=str, choices=["dirichlet", "gumbel", "plackett_luce"], default=MC_DEFAULTS['mc_model'])
    parser.add_argument("--pace_mode", type=str, choices=["off", "basic"], default=MC_DEFAULTS['pace_mode'])
    parser.add_argument("--uncertainty", type=str, choices=["off", "on"], default=MC_DEFAULTS['uncertainty'])
    parser.add_argument("--risk_mode", type=str, choices=["kelly", "fractional_kelly", "flat"], default=MC_DEFAULTS['risk_mode'])
    parser.add_argument("--backtest", action="store_true", help="Run backtest")
    parser.add_argument("--training_days", type=int, default=60, help="Training window")
    parser.add_argument("--use_ml", action="store_true", help="Use ML model (requires sklearn)")
    args = parser.parse_args()
    
    print("""
╔════════════════════════════════════════════════════════════════════════════════╗
║                    HORSE RACING SYSTEM v8.3_mc - MONTE CARLO                   ║
╠════════════════════════════════════════════════════════════════════════════════╣
║  NEW: Monte Carlo finishing-order simulation, pace/bias scenarios,             ║
║       stability + confidence intervals, MC tip menus with staking caps         ║
╚════════════════════════════════════════════════════════════════════════════════╝
    """)
    
    if args.use_ml:
        if SKLEARN_AVAILABLE:
            print("  ✓ ML mode enabled (sklearn available)")
        else:
            print("  ⚠ sklearn not available, falling back to weighted scoring")
    
    supp_stats = None
    if args.stats:
        print(f"[Loading Stats: {args.stats}]")
        supp_stats = SupplementaryStats(args.stats)
    
    train_dir = args.train_dir or args.data_dir
    print(f"\n[Loading training data from {train_dir}]")
    train_dicts = load_all_races([train_dir])
    train_races = convert_races(train_dicts)
    print(f"  ✓ {len(train_races)} historical races for training")
    
    print(f"\n[Loading racecard data from {args.data_dir}]")
    predict_dicts = load_all_races([args.data_dir])
    predict_races = convert_races(predict_dicts)
    
    if not args.backtest:
        today_day = datetime.now().date()
        future_races = []
        for r in predict_races:
            if getattr(r, 'date_dt', None):
                if r.date_dt.date() >= today_day:
                    future_races.append(r)
            else:
                dt = safe_parse_date(getattr(r, 'date', None))
                if dt and dt.date() >= today_day:
                    future_races.append(r)
        if future_races:
            predict_races = future_races
            print(f"  ✓ {len(predict_races)} upcoming races for prediction")
        else:
            print(f"  ✓ {len(predict_races)} races loaded")
            dates = sorted(set(r.date for r in predict_races if r.date))
            if dates:
                print(f"  ⚠ No future races found. Dates in data: {dates[0]} to {dates[-1]}")
    
    if not predict_races:
        print("  ✗ No races found!")
        return
    
    track_counts = Counter(r.course for r in predict_races)
    print("\n  Tracks in prediction data:")
    for track, count in sorted(track_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"    {track}: {count} races")
    
    base_unit = args.bankroll * STAKING_CONFIG['unit_percent']
    
    if args.backtest:
        results = run_backtest(
            train_races, train_dicts, args.training_days,
            supp_stats, use_ml=args.use_ml
        )
        print_backtest_results(results)
    
    elif args.mc_tips6 or args.tips6 or args.tips:
        def race_day(race):
            if getattr(race, 'date_dt', None):
                return race.date_dt.date()
            dt = safe_parse_date(getattr(race, 'date', None))
            return dt.date() if dt else None

        tip_races = predict_races
        target_day = None
        target_date = None
        if args.date:
            target_dt = safe_parse_date(args.date)
            target_day = target_dt.date() if target_dt else None
            if target_day:
                tip_races = [r for r in tip_races if race_day(r) == target_day]
                if not tip_races:
                    available_days = sorted(set(race_day(r) for r in predict_races if race_day(r)))
                    if target_day.weekday() == 5:
                        next_sat = next((d for d in available_days
                                         if d >= target_day and d.weekday() == 5), None)
                        if not next_sat:
                            next_sat = next((d for d in available_days if d.weekday() == 5), None)
                        if next_sat:
                            target_day = next_sat
                            tip_races = [r for r in predict_races if race_day(r) == target_day]
                            print(f"  ⚠ No races on {args.date}; using next Saturday in data: {target_day}")
                    if not tip_races:
                        if available_days:
                            print(f"  ✗ No races on {args.date}. Available dates: "
                                  f"{available_days[0]} to {available_days[-1]}")
                        else:
                            print(f"  ✗ No races on {args.date}. No dated races found.")
                        return
        
        target_tracks = [t.strip() for t in args.tracks.split(',')] if args.tracks else None
        
        if args.date:
            if not target_day:
                target_dt = safe_parse_date(args.date)
                target_day = target_dt.date() if target_dt else None
            target_date = target_day.isoformat() if target_day else args.date
        else:
            date_days = sorted(set(race_day(r) for r in tip_races if race_day(r)))
            target_day = date_days[0] if date_days else None
            target_date = target_day.isoformat() if target_day else "Unknown"
            if target_day:
                tip_races = [r for r in tip_races if race_day(r) == target_day]
        
        model = RacingModel(supp_stats, use_ml=args.use_ml)
        model.train(train_races, train_dicts, cutoff_date=target_day or target_date)
        
        print(f"\n[Generating Tips for {target_date}]")
        
        if args.mc_tips6:
            mc_config = {
                'mc_sims': args.mc_sims,
                'seed': args.seed,
                'mc_model': args.mc_model,
                'pace_mode': args.pace_mode,
                'uncertainty': args.uncertainty,
                'risk_mode': args.risk_mode,
            }
            generator = TipGenerator(model, args.bankroll, mc_config=mc_config)
            all_tips = generator.generate_mc_tips_for_date(tip_races, target_tracks, include_past=False)
            if not all_tips:
                print("\n  ✗ No MC tips generated - no races at target tracks")
                return
            for track, tips in sorted(all_tips.items()):
                print(format_track_tips_mc(track, tips, target_date))
            print(format_betting_summary_mc(all_tips, args.bankroll, base_unit))
            print(f"\n  ✓ {sum(len(tips) for tips in all_tips.values())} MC tips across {len(all_tips)} tracks")
        
        elif args.tips6:
            generator = TipGenerator(model, args.bankroll)
            all_tips = generator.generate_tips_for_date(tip_races, target_tracks, include_past=False)
            
            if not all_tips:
                print("\n  ✗ No tips generated - no races at target tracks")
                return
            
            for track, tips in sorted(all_tips.items()):
                print(format_track_tips(track, tips, target_date))
            
            print(format_betting_summary(all_tips, args.bankroll, base_unit))
            print(f"\n  ✓ {sum(len(tips) for tips in all_tips.values())} tips across {len(all_tips)} tracks")
        
        else:
            tips = []
            for race in tip_races:
                if not race.is_valid:
                    continue
                analysis = model.analyze(race)
                if not analysis:
                    continue
                top = analysis[0]
                conf = calculate_confidence(top, analysis)
                staking = get_staking_tier(conf['score'], top.get('model_prob', 0))
                if staking['units'] > 0:
                    tips.append({
                        'race': race,
                        'pick': top,
                        'confidence': conf,
                        'staking': staking
                    })
            
            print(f"\n  ✓ {len(tips)} qualifying tips")
            for tip in tips:
                race = tip['race']
                pick = tip['pick']
                print(f"\n  {race.course} R{race.race_number}: {pick['horse']} @ ${pick['sp']:.2f}")
                print(f"    Model: {pick['model_prob']:.1f}% | {tip['staking']['label']}")


if __name__ == "__main__":
    main()
