#!/usr/bin/env python3
"""Monte Carlo simulation engine for Australian thoroughbred racing."""

import json
import re
import argparse
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from collections import defaultdict

try:
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


DEFAULT_CONFIG = {
    'n_sims': 50000,
    'seed': None,
    
    'pace_regimes': {'slow': 0.25, 'even': 0.45, 'fast': 0.22, 'meltdown': 0.08},
    
    # NOTE: Heavy/soft INCREASE variance (more unpredictable outcomes, more upsets).
    # The prior values (0.85/0.95) were wrong — they conflated higher favorite win rate
    # on heavy (a mu-level effect from wet-trackers) with lower sigma (wrong direction).
    'base_sigma': 1.0,
    'heavy_track_sigma_mult': 1.22,   # Heavy: wider outcomes, more upsets (aligned w/ Engine C)
    'wet_track_sigma_mult': 1.10,     # Soft: moderate increase in uncertainty
    'firm_track_sigma_mult': 0.96,    # Firm: slightly tighter than Good baseline
    'synthetic_sigma_mult': 0.94,     # Synthetic: most predictable surface
    'maiden_sigma_mult': 1.25,
    'first_up_sigma_mult': 1.15,
    'young_horse_sigma_mult': 1.1,
    'limited_data_sigma_mult': 1.2,
    
    # Skew-normal distribution for Australian turf (negative skew)
    # Horses underperform ~32% of the time vs overperform ~18%
    'skew_alpha': -2.0,
    
    'race_shock_sigma': 0.15,
    
    'weight_effect_per_kg': 0.08,
    'weight_effect_enabled': True,
    
    'barrier_penalty_sprint': 0.12,
    'barrier_penalty_mile': 0.08,
    'barrier_penalty_staying': 0.04,
    
    'leader_bias_default': 0.5,
    'inside_lane_advantage': 0.1,
    
    'market_influence_cap': 0.15,
    'market_firmer_sigma_reduction': 0.9,
    'market_drifter_sigma_increase': 1.1,
    'market_prob_weight': 0.35,
    'probability_mu_scale': 1.0,
    'prob_floor': 1e-4,
    
    'kelly_fraction': 0.25,
    'max_bet_fraction': 0.05,
    'min_edge_threshold': 0.05,
    'volatility_penalty_factor': 0.5,
    
    'ci_lower': 5,
    'ci_upper': 95,
}

TRACK_LEADER_BIAS = {
    'ascot': 0.95, 'eagle farm': 0.80, 'warwick farm': 0.75, 'moonee valley': 0.70,
    'canterbury': 0.65, 'caulfield': 0.60, 'sandown': 0.55, 'belmont': 0.52,
    'doomben': 0.50, 'randwick': 0.48, 'royal randwick': 0.48, 'rosehill': 0.45,
    'kensington': 0.48, 'pinjarra': 0.35, 'flemington': 0.20, 'gold coast': 0.35,
}

# Tight-turn tracks: leaders/on-pace hold advantage, closers can't sustain runs through bends
# Aligned with pace_modeling.py TIGHT_TURNING_TRACKS bonuses
TIGHT_TURN_TRACKS = {
    'moonee valley': {'leader_boost': 0.06, 'on_pace_boost': 0.04, 'backmarker_penalty': 0.04},
    'geelong':       {'leader_boost': 0.06, 'on_pace_boost': 0.04, 'backmarker_penalty': 0.04},
    'ballarat':      {'leader_boost': 0.05, 'on_pace_boost': 0.03, 'backmarker_penalty': 0.03},
    'ipswich':       {'leader_boost': 0.05, 'on_pace_boost': 0.03, 'backmarker_penalty': 0.03},
    'caulfield':     {'leader_boost': 0.03, 'on_pace_boost': 0.02, 'backmarker_penalty': 0.02},
}

# Pace effects: [style, regime] -> effect
# Styles: leader=0, on_pace=1, midfield=2, backmarker=3
# Regimes: slow=0, even=1, fast=2, meltdown=3
PACE_EFFECTS = np.array([
    [0.30, 0.15, -0.10, -0.40],  # leader
    [0.15, 0.08, 0.00, -0.15],   # on_pace
    [0.00, 0.00, 0.05, 0.05],    # midfield
    [-0.15, -0.08, 0.15, 0.35],  # backmarker
])


def safe_float(val, default=0.0):
    if val is None: return default if default is not None else None
    try: return float(val)
    except: return default

def safe_int(val, default=0):
    if val is None: return default if default is not None else None
    try: return int(val)
    except: return default

def load_config(path):
    path = Path(path)
    if not path.exists(): return {}
    try:
        with open(path) as f: return json.load(f)
    except: return {}

def extract_running_style_hint(runner_data):
    style = runner_data.get('speed_map_hint') or runner_data.get('style')
    if style and style.lower() != 'unknown':
        return style.lower().replace(' ', '_')
    comment = runner_data.get('comment', '')
    if comment:
        c = comment.lower()
        if any(w in c for w in ['led', 'made all']): return 'leader'
        if any(w in c for w in ['settled second', 'prominent', 'handy']): return 'on_pace'
        if any(w in c for w in ['settled rear', 'back', 'last']): return 'backmarker'
    return 'midfield'

def validate_race_data(race):
    warnings = []
    track = getattr(race, 'track_name', None) or (race.get('track_name') if isinstance(race, dict) else None)
    if not track: warnings.append("Missing track name")
    runners = getattr(race, 'runners', []) or (race.get('runners', []) if isinstance(race, dict) else [])
    if len(runners) < 2: warnings.append(f"Only {len(runners)} runners")
    return warnings


@dataclass
class RunnerInput:
    horse_name: str
    barrier: int = 0
    weight_kg: float = 57.0
    jockey: str = ''
    trainer: str = ''
    age: int = 4
    sex: str = ''
    base_strength: float = 50.0
    base_p_win: Optional[float] = None
    speed_map_hint: str = 'midfield'
    cd_record: Dict = field(default_factory=dict)
    track_record: Dict = field(default_factory=dict)
    distance_record: Dict = field(default_factory=dict)
    days_since_last: Optional[int] = None
    form_string: str = ''
    margins_last5: List[float] = field(default_factory=list)
    positions_last5: List[int] = field(default_factory=list)
    market_odds_open: Optional[float] = None
    market_odds_now: Optional[float] = None
    features: Dict = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, d):
        return cls(
            horse_name=d.get('horse_name') or d.get('horse', 'Unknown'),
            barrier=safe_int(d.get('barrier') or d.get('draw'), 0),
            weight_kg=safe_float(d.get('weight_kg') or d.get('weight'), 57.0),
            jockey=d.get('jockey', ''),
            trainer=d.get('trainer', ''),
            age=safe_int(d.get('age'), 4),
            sex=d.get('sex', ''),
            base_strength=safe_float(d.get('base_strength') or d.get('total_score') or d.get('total'), 50.0),
            base_p_win=safe_float(d.get('base_p_win') or d.get('model_prob'), None),
            speed_map_hint=d.get('speed_map_hint') or d.get('style') or extract_running_style_hint(d),
            cd_record=d.get('cd_record', {}),
            track_record=d.get('track_record', {}),
            distance_record=d.get('distance_record', {}),
            days_since_last=safe_int(d.get('days_since_last'), None),
            form_string=d.get('form_string') or d.get('form', ''),
            margins_last5=d.get('margins_last5', []),
            positions_last5=d.get('positions_last5', []),
            market_odds_open=safe_float(d.get('market_odds_open'), None),
            market_odds_now=safe_float(d.get('market_odds_now') or d.get('sp') or d.get('market_odds'), None),
            features=d.get('features', {}),
        )


@dataclass
class RaceInput:
    race_id: str = ''
    race_number: Optional[int] = None
    track_name: str = ''
    distance_m: int = 1200
    going: str = 'Good'
    race_class: str = ''
    field_size: int = 0
    rail_position: Optional[str] = None
    bias: Dict = field(default_factory=dict)
    runners: List[RunnerInput] = field(default_factory=list)
    
    @classmethod
    def from_dict(cls, d):
        runners = [RunnerInput.from_dict(r) for r in d.get('runners', [])]
        return cls(
            race_id=d.get('race_id', ''),
            race_number=safe_int(d.get('race_number'), None),
            track_name=d.get('track_name') or d.get('course') or d.get('track', ''),
            distance_m=safe_int(d.get('distance_m') or d.get('distance'), 1200),
            going=d.get('going') or d.get('track_condition', 'Good'),
            race_class=d.get('race_class') or d.get('class', ''),
            field_size=len(runners) if runners else safe_int(d.get('field_size'), 0),
            rail_position=d.get('rail_position'),
            bias=d.get('bias', {}),
            runners=runners,
        )


@dataclass
class RunnerResult:
    horse_name: str
    barrier: int
    p_win: float
    p_top2: float
    p_top3: float
    p_top4: float
    p_win_ci_lower: float
    p_win_ci_upper: float
    fair_odds_win: float
    market_odds: Optional[float]
    value_edge: Optional[float]
    overlay_percent: Optional[float]
    kelly_fraction: float
    recommended_stake: float
    stake_reason: str
    mean_performance: float
    std_performance: float
    avg_finish_position: float


@dataclass
class RaceResult:
    race_id: str
    race_number: Optional[int]
    track_name: str
    distance_m: int
    n_sims: int
    runners: List[RunnerResult]
    top_exactas: List[Tuple[str, str, float]] = field(default_factory=list)
    top_trifectas: List[Tuple[str, str, str, float]] = field(default_factory=list)
    pace_regime_counts: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)



class PaceSimulator:
    def __init__(self, config):
        self.config = config
        regimes = config.get('pace_regimes', {'slow': 0.25, 'even': 0.45, 'fast': 0.22, 'meltdown': 0.08})
        self.regime_names = ['slow', 'even', 'fast', 'meltdown']
        self.regime_weights = np.array([regimes.get(r, 0.25) for r in self.regime_names])
        self.regime_weights /= self.regime_weights.sum()
    
    def sample_pace_regimes(self, n_sims):
        return np.random.choice(len(self.regime_names), size=n_sims, p=self.regime_weights)
    
    def calculate_pace_effects(self, styles, pace_regimes, leader_bias=0.5):
        n_sims, n_runners = len(pace_regimes), len(styles)
        effects = np.zeros((n_sims, n_runners))
        for i, style in enumerate(styles):
            style = min(style, 3)
            for s, regime in enumerate(pace_regimes):
                base_effect = PACE_EFFECTS[style, regime]
                if style in [0, 1]: base_effect *= (0.5 + leader_bias)
                effects[s, i] = base_effect
        return effects


class TrackBiasModel:
    def __init__(self, config):
        self.config = config
    
    def get_leader_bias(self, race):
        track = race.track_name.lower() if hasattr(race, 'track_name') else ''
        if hasattr(race, 'bias') and race.bias and 'leader_bias' in race.bias:
            return race.bias['leader_bias']
        for track_key, bias in TRACK_LEADER_BIAS.items():
            if track_key in track: return bias
        return self.config.get('leader_bias_default', 0.5)
    
    def get_lane_bias(self, race):
        if hasattr(race, 'bias') and race.bias and 'lane_bias' in race.bias:
            return race.bias['lane_bias']
        return self.config.get('inside_lane_advantage', 0.1)


class MarketAdjuster:
    def __init__(self, config):
        self.config = config
        self.influence_cap = config.get('market_influence_cap', 0.15)
    
    def adjust_mu(self, mu, runner):
        odds_open = getattr(runner, 'market_odds_open', None)
        odds_now = getattr(runner, 'market_odds_now', None)
        if not odds_open or not odds_now or odds_open <= 1 or odds_now <= 1: return mu
        log_change = np.log(odds_now) - np.log(odds_open)
        adjustment = max(-self.influence_cap, min(self.influence_cap, -log_change * 0.1))
        return mu + adjustment
    
    def adjust_sigma(self, sigma, runner):
        odds_open = getattr(runner, 'market_odds_open', None)
        odds_now = getattr(runner, 'market_odds_now', None)
        if not odds_open or not odds_now: return sigma
        log_change = np.log(odds_now) - np.log(odds_open)
        if log_change < -0.1: return sigma * self.config.get('market_firmer_sigma_reduction', 0.9)
        elif log_change > 0.1: return sigma * self.config.get('market_drifter_sigma_increase', 1.1)
        return sigma



class CalibrationEngine:
    def __init__(self, config=None):
        self.config = config or {}
        self.fitted = False
        self.strength_to_mu_slope = 1.0 / 15.0
        self.strength_to_mu_intercept = -50.0 / 15.0
        self.temperature = 1.0
        self.isotonic_model = None
    
    def fit(self, historical_races, time_split_date=None):
        X, y, odds = self._extract_training_data(historical_races)
        if len(X) < 100:
            print(f"  Warning: Only {len(X)} samples for calibration")
            return None
        
        split_idx = int(len(X) * 0.8)
        train_X, train_y = X[:split_idx], y[:split_idx]
        test_X, test_y = X[split_idx:], y[split_idx:]
        test_odds = odds[split_idx:] if odds is not None else None
        
        self._fit_strength_mapping(train_X, train_y)
        self._fit_temperature(train_X, train_y)
        if SKLEARN_AVAILABLE: self._fit_isotonic(train_X, train_y)
        self.fitted = True
        
        metrics = self.evaluate(test_X, test_y, test_odds)
        return {'strength_to_mu_params': {'slope': self.strength_to_mu_slope, 'intercept': self.strength_to_mu_intercept},
                'temperature': self.temperature, 'metrics': metrics}
    
    def evaluate(self, X, y, odds=None):
        probs = self._predict_probs(X)
        eps = 1e-15
        probs_clipped = np.clip(probs, eps, 1 - eps)
        log_loss = -np.mean(y * np.log(probs_clipped) + (1 - y) * np.log(1 - probs_clipped))
        brier = np.mean((probs - y) ** 2)
        return {'log_loss': round(log_loss, 4), 'brier_score': round(brier, 4)}
    
    def strength_to_mu(self, strength):
        return strength * self.strength_to_mu_slope + self.strength_to_mu_intercept
    
    def _extract_training_data(self, races):
        strengths, outcomes, market_odds = [], [], []
        for race in races:
            for runner in race.get('runners', []):
                strength = runner.get('base_strength') or runner.get('total_score') or runner.get('total')
                position = runner.get('position', 0)
                odds = runner.get('sp') or runner.get('market_odds')
                if strength is not None and position > 0:
                    strengths.append(float(strength))
                    outcomes.append(1 if position == 1 else 0)
                    market_odds.append(float(odds) if odds else 0.0)
        return np.array(strengths), np.array(outcomes), np.array(market_odds) if any(market_odds) else None
    
    def _fit_strength_mapping(self, X, y):
        if not SKLEARN_AVAILABLE:
            winners, losers = X[y == 1], X[y == 0]
            if len(winners) > 0 and len(losers) > 0:
                diff = winners.mean() - losers.mean()
                self.strength_to_mu_slope = 1.0 / max(diff, 10)
            return
        lr = LogisticRegression(max_iter=1000)
        lr.fit(X.reshape(-1, 1), y)
        self.strength_to_mu_slope = float(lr.coef_[0][0])
        self.strength_to_mu_intercept = float(lr.intercept_[0])
    
    def _fit_temperature(self, X, y):
        best_temp, best_brier = 1.0, float('inf')
        for temp in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]:
            probs = self._strength_to_prob(X, temp)
            brier = np.mean((probs - y) ** 2)
            if brier < best_brier: best_brier, best_temp = brier, temp
        self.temperature = best_temp
    
    def _fit_isotonic(self, X, y):
        if not SKLEARN_AVAILABLE: return
        probs = self._strength_to_prob(X, self.temperature)
        self.isotonic_model = IsotonicRegression(out_of_bounds='clip')
        self.isotonic_model.fit(probs.reshape(-1, 1), y)
    
    def _strength_to_prob(self, X, temperature=1.0):
        mu = X * self.strength_to_mu_slope + self.strength_to_mu_intercept
        return 1 / (1 + np.exp(-mu / temperature))
    
    def _predict_probs(self, X):
        probs = self._strength_to_prob(X, self.temperature)
        if self.isotonic_model is not None and SKLEARN_AVAILABLE:
            probs = self.isotonic_model.predict(probs.reshape(-1, 1))
        return probs



class MonteCarloEngine:
    def __init__(self, config=None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.pace_simulator = PaceSimulator(self.config)
        self.track_bias_model = TrackBiasModel(self.config)
        self.market_adjuster = MarketAdjuster(self.config)
        self.calibration_params = None

    def _coerce_prob(self, prob):
        if prob is None:
            return None
        try:
            prob = float(prob)
        except (TypeError, ValueError):
            return None
        if prob <= 0:
            return None
        if prob > 1:
            prob = prob / 100.0
        floor = self.config.get('prob_floor', 1e-4)
        return min(max(prob, floor), 1 - floor)

    def _softmax(self, values):
        values = np.array(values, dtype=float)
        values = values - values.max()
        exp_vals = np.exp(values)
        total = exp_vals.sum()
        if total <= 0:
            return np.full_like(exp_vals, 1.0 / len(exp_vals))
        return exp_vals / total

    def _build_prior_probs(self, runners, fallback_mus):
        probs = []
        has_any = False
        market_weight = self.config.get('market_prob_weight', 0.35)

        for runner in runners:
            base_p = self._coerce_prob(getattr(runner, 'base_p_win', None))
            market_p = None
            odds = getattr(runner, 'market_odds_now', None)
            if odds and odds > 1:
                market_p = self._coerce_prob(1.0 / odds)
            if base_p is not None and market_p is not None:
                prob = (1 - market_weight) * base_p + market_weight * market_p
            else:
                prob = base_p if base_p is not None else market_p
            if prob is not None:
                has_any = True
            probs.append(prob)

        if not has_any:
            return None

        if any(p is None for p in probs):
            fallback = self._softmax(fallback_mus)
            for i, p in enumerate(probs):
                if p is None:
                    probs[i] = fallback[i]

        probs = np.array(probs, dtype=float)
        total = probs.sum()
        if total > 0:
            probs = probs / total
        return probs

    def _probs_to_mu(self, probs):
        floor = self.config.get('prob_floor', 1e-4)
        probs = np.clip(probs, floor, 1 - floor)
        log_p = np.log(probs)
        log_p -= log_p.mean()
        return log_p * self.config.get('probability_mu_scale', 1.0)
    
    def simulate_race(self, race, n_sims=None, seed=None, with_exotics=False):
        n_sims = n_sims or self.config['n_sims']
        if seed is not None: np.random.seed(seed)
        
        warnings = validate_race_data(race)
        n_runners = len(race.runners)
        if n_runners < 2: raise ValueError("Need at least 2 runners")
        
        dist_bucket = self._get_distance_bucket(race.distance_m)
        going_lower = race.going.lower()
        is_heavy = 'heavy' in going_lower
        is_wet = is_heavy or any(w in going_lower for w in ('soft', 'wet', 'yield'))
        is_firm = any(w in going_lower for w in ('firm', 'fast', 'hard'))
        is_synthetic = any(w in going_lower for w in ('synthetic', 'poly', 'tapeta'))
        is_maiden = 'maiden' in race.race_class.lower() if race.race_class else False
        
        mus, sigmas = self._calculate_base_params(race, dist_bucket, is_wet, is_heavy, is_firm, is_synthetic, is_maiden)
        pace_regimes = self.pace_simulator.sample_pace_regimes(n_sims)
        pace_regime_counts = dict(zip(*np.unique(pace_regimes, return_counts=True)))
        styles = np.array([self._style_to_index(r.speed_map_hint) for r in race.runners])
        leader_bias = self.track_bias_model.get_leader_bias(race)
        lane_bias = self.track_bias_model.get_lane_bias(race)

        performances, finish_positions = self._run_simulation(
            n_sims, n_runners, mus, sigmas, styles,
            np.array([r.barrier for r in race.runners]),
            pace_regimes, leader_bias, lane_bias, dist_bucket, is_heavy,
            going=race.going, track_name=race.track_name
        )
        
        p_wins = (finish_positions == 1).mean(axis=0)
        p_top2 = (finish_positions <= 2).mean(axis=0)
        p_top3 = (finish_positions <= 3).mean(axis=0)
        p_top4 = (finish_positions <= 4).mean(axis=0)
        avg_positions = finish_positions.mean(axis=0)
        ci_lowers, ci_uppers = self._bootstrap_confidence_intervals(finish_positions)
        
        runner_results = []
        for i, runner in enumerate(race.runners):
            p_win = p_wins[i]
            fair_odds = 1 / p_win if p_win > 0.001 else 999.0
            market_odds = runner.market_odds_now
            value_edge = (p_win * market_odds - 1) if market_odds and market_odds > 1 else None
            overlay_pct = (p_win - (1 / market_odds)) * 100 if market_odds and market_odds > 1 else None
            kelly_frac, stake, reason = self._calculate_kelly_stake(p_win, market_odds, ci_lowers[i], ci_uppers[i])
            
            runner_results.append(RunnerResult(
                horse_name=runner.horse_name, barrier=runner.barrier,
                p_win=round(p_win, 4), p_top2=round(p_top2[i], 4),
                p_top3=round(p_top3[i], 4), p_top4=round(p_top4[i], 4),
                p_win_ci_lower=round(ci_lowers[i], 4), p_win_ci_upper=round(ci_uppers[i], 4),
                fair_odds_win=round(fair_odds, 2), market_odds=market_odds,
                value_edge=round(value_edge, 4) if value_edge else None,
                overlay_percent=round(overlay_pct, 2) if overlay_pct else None,
                kelly_fraction=round(kelly_frac, 4), recommended_stake=round(stake, 4),
                stake_reason=reason,
                mean_performance=round(performances[:, i].mean(), 3),
                std_performance=round(performances[:, i].std(), 3),
                avg_finish_position=round(avg_positions[i], 2),
            ))
        
        runner_results.sort(key=lambda x: -x.p_win)
        
        top_exactas, top_trifectas = [], []
        if with_exotics:
            top_exactas = self._calculate_exactas(finish_positions, race.runners)
            top_trifectas = self._calculate_trifectas(finish_positions, race.runners)
        
        return RaceResult(
            race_id=race.race_id, race_number=race.race_number,
            track_name=race.track_name, distance_m=race.distance_m,
            n_sims=n_sims, runners=runner_results, top_exactas=top_exactas,
            top_trifectas=top_trifectas,
            pace_regime_counts={k: int(v) for k, v in pace_regime_counts.items()},
            warnings=warnings,
        )
    
    def simulate_meeting(self, races, n_sims=None, seed=None, with_exotics=False):
        return [self.simulate_race(race, n_sims, seed + i if seed else None, with_exotics)
                for i, race in enumerate(races)]
    
    def _calculate_base_params(self, race, dist_bucket, is_wet, is_heavy, is_firm, is_synthetic, is_maiden):
        n_runners = len(race.runners)
        mus, sigmas = np.zeros(n_runners), np.zeros(n_runners)

        base_strengths = np.array([r.base_strength for r in race.runners], dtype=float)
        strength_mus = (base_strengths - 50) / 15
        strength_mus = strength_mus - strength_mus.mean()
        prior_probs = self._build_prior_probs(race.runners, strength_mus)
        prior_mus = self._probs_to_mu(prior_probs) if prior_probs is not None else None

        for i, runner in enumerate(race.runners):
            mu = prior_mus[i] if prior_mus is not None else strength_mus[i]
            if runner.market_odds_now:
                mu = self.market_adjuster.adjust_mu(mu, runner)

            sigma = self.config['base_sigma']
            if is_heavy:
                sigma *= self.config['heavy_track_sigma_mult']
            elif is_wet:
                sigma *= self.config['wet_track_sigma_mult']
            elif is_firm:
                sigma *= self.config['firm_track_sigma_mult']
            elif is_synthetic:
                sigma *= self.config['synthetic_sigma_mult']
            if is_maiden: sigma *= self.config['maiden_sigma_mult']
            if runner.days_since_last and runner.days_since_last > 60:
                sigma *= self.config['first_up_sigma_mult']
            if runner.age and runner.age <= 3:
                sigma *= self.config['young_horse_sigma_mult']
            total_runs = sum([runner.cd_record.get('runs', 0), runner.track_record.get('runs', 0),
                             runner.distance_record.get('runs', 0)])
            if total_runs < 10: sigma *= self.config['limited_data_sigma_mult']
            if runner.market_odds_now and runner.market_odds_open:
                sigma = self.market_adjuster.adjust_sigma(sigma, runner)
            
            mus[i], sigmas[i] = mu, sigma

        try:
            from server.python.track_condition_db import TrackConditionDatabase
            going = race.going if hasattr(race, 'going') else ''
            if going:
                tc_db = TrackConditionDatabase()
                horse_names = [r.horse_name if hasattr(r, 'horse_name') else getattr(r, 'name', '') for r in race.runners]
                going_adjs = tc_db.batch_get_going_adjustments(horse_names, going)
                for i, name in enumerate(horse_names):
                    adj = going_adjs.get(name, 1.0)
                    if adj != 1.0:
                        mus[i] += np.log(adj)
        except Exception:
            pass  # Graceful fallback if DB or import unavailable

        return mus, sigmas
    
    def _sample_skew_normal(self, n_sims, n_runners, alpha=-2.0):
        """Sample from skew-normal distribution. alpha<0 gives negative skew.

        Australian turf: horses underperform ~32% vs overperform ~18%.
        Skew-normal with alpha=-2.0 naturally captures this asymmetry.
        """
        delta = alpha / np.sqrt(1 + alpha**2)
        u0 = np.abs(np.random.randn(n_sims, n_runners))
        u1 = np.random.randn(n_sims, n_runners)
        z = delta * u0 + np.sqrt(1 - delta**2) * u1
        # Center the distribution (skew-normal has nonzero mean)
        z -= delta * np.sqrt(2 / np.pi)
        return z

    def _run_simulation(self, n_sims, n_runners, mus, sigmas, styles, barriers,
                        pace_regimes, leader_bias, lane_bias, dist_bucket, is_heavy,
                        going='Good', track_name=''):
        # Base performance with skew-normal noise (negative skew for Australian turf)
        alpha = self.config.get('skew_alpha', -2.0)
        shocks = self._sample_skew_normal(n_sims, n_runners, alpha)
        performances = mus + sigmas * shocks

        # Race-level shared shock removed (audit fix #4).
        # A constant additive shift to all runners has zero effect on relative
        # rankings (argsort) but inflates std_performance and widens confidence
        # intervals, reducing Kelly stakes on every bet.

        performances += self.pace_simulator.calculate_pace_effects(styles, pace_regimes, leader_bias)

        performances += self._calculate_barrier_effects(barriers, n_runners, dist_bucket, lane_bias, going)

        tight_turn_adj = self._calculate_tight_turn_effects(track_name, styles, n_runners)
        if tight_turn_adj is not None:
            performances += tight_turn_adj

        tie_breaker = np.random.randn(n_sims, n_runners) * 1e-10
        performances_tb = performances + tie_breaker
        finish_positions = np.zeros_like(performances, dtype=int)
        for s in range(n_sims):
            finish_positions[s] = n_runners - np.argsort(np.argsort(performances_tb[s]))

        return performances, finish_positions
    
    def _calculate_barrier_effects(self, barriers, n_runners, dist_bucket, lane_bias, going='Good'):
        optimal_barrier = 2
        base_penalty = {'sprint': 0.12, 'short': 0.10, 'mile': 0.08, 'middle': 0.05, 'staying': 0.03}.get(dist_bucket, 0.08)

        # Going-condition modifier: heavy/soft churns inside rail, reducing inside advantage
        going_lower = going.lower() if going else ''
        if 'heavy' in going_lower:
            barrier_going_scale = 0.55
        elif any(w in going_lower for w in ('soft', 'wet', 'yield')):
            barrier_going_scale = 0.75
        elif any(w in going_lower for w in ('firm', 'fast', 'hard')):
            barrier_going_scale = 1.10
        elif any(w in going_lower for w in ('synthetic', 'poly', 'tapeta')):
            barrier_going_scale = 0.85
        else:
            barrier_going_scale = 1.0

        penalty = base_penalty * barrier_going_scale
        effects = -np.abs(barriers - optimal_barrier) * penalty
        effects += (0.5 - barriers / n_runners) * lane_bias * barrier_going_scale
        return effects

    def _calculate_tight_turn_effects(self, track_name, styles, n_runners):
        """Apply tight-turn geometry advantage for front-runners at specific tracks."""
        if not track_name:
            return None
        track_lower = track_name.lower()
        profile = None
        for track_key, tp in TIGHT_TURN_TRACKS.items():
            if track_key in track_lower:
                profile = tp
                break
        if profile is None:
            return None

        effects = np.zeros(n_runners)
        for i, style in enumerate(styles):
            if style == 0:
                effects[i] = profile['leader_boost']
            elif style == 1:
                effects[i] = profile['on_pace_boost']
            elif style == 3:
                effects[i] = -profile['backmarker_penalty']
        return effects
    
    def _bootstrap_confidence_intervals(self, finish_positions, n_bootstrap=1000):
        n_sims, n_runners = finish_positions.shape
        win_probs_bootstrap = np.zeros((n_bootstrap, n_runners))
        for b in range(n_bootstrap):
            indices = np.random.randint(0, n_sims, n_sims)
            win_probs_bootstrap[b] = (finish_positions[indices] == 1).mean(axis=0)
        return (np.percentile(win_probs_bootstrap, self.config['ci_lower'], axis=0),
                np.percentile(win_probs_bootstrap, self.config['ci_upper'], axis=0))
    
    def _calculate_kelly_stake(self, p_win, odds, ci_lower, ci_upper):
        if not odds or odds <= 1: return 0.0, 0.0, "No market odds"
        edge = p_win * odds - 1
        if edge < self.config['min_edge_threshold']:
            return 0.0, 0.0, f"Edge {edge:.1%} below threshold"
        kelly = (p_win * (odds - 1) - (1 - p_win)) / (odds - 1)
        if kelly <= 0: return 0.0, 0.0, "Negative Kelly"
        frac_kelly = kelly * self.config['kelly_fraction']
        vol_penalty = max(0.3, min(1.0, 1 - (ci_upper - ci_lower) * self.config['volatility_penalty_factor']))
        frac_kelly *= vol_penalty
        final_stake = min(frac_kelly, self.config['max_bet_fraction'])
        reason = f"Kelly={kelly:.1%}, Frac={frac_kelly:.1%}, Edge={edge:.1%}"
        return kelly, final_stake, reason
    
    def _calculate_exactas(self, finish_positions, runners, top_n=10):
        n_sims = finish_positions.shape[0]
        exacta_counts = defaultdict(int)
        for s in range(n_sims):
            first_idx = np.where(finish_positions[s] == 1)[0][0]
            second_idx = np.where(finish_positions[s] == 2)[0][0]
            exacta_counts[(first_idx, second_idx)] += 1
        exactas = [(runners[f].horse_name, runners[s].horse_name, round(c / n_sims, 4))
                   for (f, s), c in exacta_counts.items()]
        exactas.sort(key=lambda x: -x[2])
        return exactas[:top_n]
    
    def _calculate_trifectas(self, finish_positions, runners, top_n=10):
        n_sims = finish_positions.shape[0]
        trifecta_counts = defaultdict(int)
        for s in range(n_sims):
            first = np.where(finish_positions[s] == 1)[0][0]
            second = np.where(finish_positions[s] == 2)[0][0]
            third = np.where(finish_positions[s] == 3)[0][0]
            trifecta_counts[(first, second, third)] += 1
        trifectas = [(runners[f].horse_name, runners[s].horse_name, runners[t].horse_name, round(c / n_sims, 4))
                     for (f, s, t), c in trifecta_counts.items()]
        trifectas.sort(key=lambda x: -x[3])
        return trifectas[:top_n]
    
    def _get_distance_bucket(self, distance_m):
        if distance_m < 1100: return 'sprint'
        elif distance_m < 1400: return 'short'
        elif distance_m < 1800: return 'mile'
        elif distance_m < 2200: return 'middle'
        return 'staying'
    
    def _style_to_index(self, style):
        return {'leader': 0, 'on_pace': 1, 'midfield': 2, 'backmarker': 3, 'unknown': 2}.get(
            style.lower().replace(' ', '_'), 2)



def _zscore(values):
    arr = np.array(values, dtype=float)
    std = arr.std()
    if std < 1e-9:
        return np.zeros_like(arr)
    return (arr - arr.mean()) / (std + 1e-9)


def calculate_advanced_ranking(runners):
    """
    Build a stat-weighted composite ranking for projected finish order.
    Uses win probability, top-3 rate, expected finish, and stability/uncertainty.
    """
    if not runners:
        return []

    n = len(runners)
    win = np.array([r.p_win for r in runners], dtype=float)
    top3 = np.array([r.p_top3 for r in runners], dtype=float)
    avg_pos = np.array([r.avg_finish_position for r in runners], dtype=float)
    mean_perf = np.array([r.mean_performance for r in runners], dtype=float)
    std_perf = np.array([r.std_performance for r in runners], dtype=float)
    ci_width = np.array([r.p_win_ci_upper - r.p_win_ci_lower for r in runners], dtype=float)

    avg_pos_score = 1.0 - (avg_pos - 1.0) / max(1, n - 1)
    stability_z = _zscore(mean_perf) - 0.7 * _zscore(std_perf)
    uncertainty_z = _zscore(ci_width)

    score = (
        0.55 * win +
        0.20 * top3 +
        0.15 * avg_pos_score +
        0.05 * stability_z -
        0.05 * uncertainty_z
    )

    ranked = []
    for i, runner in enumerate(runners):
        ranked.append({
            'runner': runner,
            'score': float(score[i]),
            'stability_z': float(stability_z[i]),
            'ci_width': float(ci_width[i]),
            'avg_pos_score': float(avg_pos_score[i]),
        })
    ranked.sort(key=lambda x: -x['score'])
    return ranked


def format_race_result(result):
    race_label = f"{result.track_name}"
    if result.race_number:
        race_label += f" R{result.race_number}"
    advanced = calculate_advanced_ranking(result.runners)
    lines = [
        "", "=" * 95,
        f"  MONTE CARLO SIMULATION: {race_label} ({result.distance_m}m) | {result.n_sims:,} sims",
        "=" * 95, "",
        "  ADVANCED WINNER ORDER (stat-weighted composite)",
        "  " + "-" * 91,
        f"  {'#':<3} {'Horse':<22} {'Score':>6} {'Win%':>6} {'Top3%':>7} {'AvgPos':>7} {'Stab':>6} {'CIw':>6}",
    ]
    for i, item in enumerate(advanced, 1):
        r = item['runner']
        lines.append(
            f"  {i:<3} {r.horse_name[:22]:<22} {item['score']:>6.3f} "
            f"{r.p_win*100:>5.1f}% {r.p_top3*100:>6.1f}% "
            f"{r.avg_finish_position:>7.2f} {item['stability_z']:>6.2f} {item['ci_width']*100:>6.1f}"
        )
    lines.extend([
        "",
        f"  {'Horse':<22} {'Win%':>7} {'95% CI':>15} {'Place%':>8} {'Fair':>7} {'Mkt':>7} {'Edge':>8} {'Stake':>7}",
        "  " + "-" * 91,
    ])
    for r in result.runners:
        ci_str = f"[{r.p_win_ci_lower*100:.1f}-{r.p_win_ci_upper*100:.1f}]"
        mkt_str = f"${r.market_odds:.2f}" if r.market_odds else "N/A"
        edge_str = f"{r.value_edge*100:+.1f}%" if r.value_edge else "N/A"
        stake_str = f"{r.recommended_stake*100:.1f}%" if r.recommended_stake > 0 else "-"
        lines.append(f"  {r.horse_name[:22]:<22} {r.p_win*100:>6.1f}% {ci_str:>15} "
                    f"{r.p_top3*100:>7.1f}% ${r.fair_odds_win:>6.2f} {mkt_str:>7} {edge_str:>8} {stake_str:>7}")
    
    if result.pace_regime_counts:
        pace_str = ", ".join([f"{['slow','even','fast','melt'][int(k)]}: {v/result.n_sims*100:.0f}%"
                             for k, v in sorted(result.pace_regime_counts.items())])
        lines.extend(["  " + "-" * 91, f"  Pace: {pace_str}"])
    lines.append("")
    return "\n".join(lines)

def format_exotics(result):
    lines = ["  EXOTICS", "  " + "-" * 50]
    if result.top_exactas:
        lines.append("  Exactas:")
        for f, s, p in result.top_exactas[:5]:
            lines.append(f"    {f[:15]} / {s[:15]}: {p*100:.2f}%")
    if result.top_trifectas:
        lines.append("  Trifectas:")
        for f, s, t, p in result.top_trifectas[:5]:
            lines.append(f"    {f[:12]} / {s[:12]} / {t[:12]}: {p*100:.2f}%")
    return "\n".join(lines)

def result_to_dict(result):
    advanced = calculate_advanced_ranking(result.runners)
    return {
        'race_id': result.race_id, 'race_number': result.race_number, 'track_name': result.track_name,
        'distance_m': result.distance_m, 'n_sims': result.n_sims,
        'runners': [asdict(r) for r in result.runners],
        'advanced_order': [{
            'horse': item['runner'].horse_name,
            'score': item['score'],
            'win_prob': item['runner'].p_win,
            'top3_prob': item['runner'].p_top3,
            'avg_finish_position': item['runner'].avg_finish_position,
            'stability_z': item['stability_z'],
            'ci_width': item['ci_width'],
        } for item in advanced],
        'top_exactas': [{'first': e[0], 'second': e[1], 'prob': e[2]} for e in result.top_exactas],
        'top_trifectas': [{'first': t[0], 'second': t[1], 'third': t[2], 'prob': t[3]} for t in result.top_trifectas],
        'pace_regime_counts': result.pace_regime_counts, 'warnings': result.warnings,
    }



def calculate_mix_score(runner, prob_weight=1.0, edge_weight=0.7, uncertainty_penalty=0.9):
    """
    Composite score balancing win probability, value edge, and uncertainty.

    mix_score = win_prob + edge_weight × value_edge − uncertainty_penalty × ci_width

    Higher = better betting candidate.
    """
    ci_width = runner.p_win_ci_upper - runner.p_win_ci_lower
    edge = runner.value_edge if runner.value_edge else 0
    
    return (prob_weight * runner.p_win 
            + edge_weight * edge 
            - uncertainty_penalty * ci_width)


def format_runner_line(runner, label=""):
    """Format a single runner as a compact betting line."""
    ci_str = f"[{runner.p_win_ci_lower*100:.1f}-{runner.p_win_ci_upper*100:.1f}]"
    fair_str = f"${runner.fair_odds_win:.2f}"
    mkt_str = f"${runner.market_odds:.2f}" if runner.market_odds else "N/A"
    edge_str = f"{runner.value_edge*100:+.1f}%" if runner.value_edge else "N/A"
    stake_str = f"{runner.recommended_stake*100:.1f}%" if runner.recommended_stake > 0 else "-"
    
    if not label:
        label = "BET" if runner.recommended_stake > 0 else "WATCH"
    
    return (f"[{label:5}] {runner.horse_name[:18]:<18} | "
            f"Win {runner.p_win*100:>5.1f}% | CI {ci_str:<13} | "
            f"Fair {fair_str:>6} | Mkt {mkt_str:>6} | "
            f"Edge {edge_str:>7} | Stake {stake_str:>5}")


def summarize_best_bets(result, top_n=3):
    """Generate compact betting summary for a single race."""
    runners = result.runners
    if not runners:
        return "  No runners to summarize"
    
    race_label = f"{result.track_name}"
    if result.race_number:
        race_label += f" R{result.race_number}"
    lines = [
        "",
        "┌" + "─" * 98 + "┐",
        f"│  {race_label.upper()} ({result.distance_m}m) - BETTING SUMMARY".ljust(98) + " │",
        "└" + "─" * 98 + "┘",
    ]
    
    favourites = sorted(runners, key=lambda r: -r.p_win)[:top_n]
    
    lines.append("")
    lines.append("  ◆ BEST FAVOURITES (highest win probability)")
    lines.append("  " + "─" * 96)
    for r in favourites:
        lines.append("  " + format_runner_line(r))
    
    value_runners = [r for r in runners if r.value_edge and r.value_edge > 0]
    value_runners = sorted(value_runners, key=lambda r: -r.value_edge)[:top_n]
    
    lines.append("")
    lines.append("  ◆ BEST VALUE (market underpricing)")
    lines.append("  " + "─" * 96)
    if value_runners:
        for r in value_runners:
            lines.append("  " + format_runner_line(r))
    else:
        lines.append("    No positive-edge runners found")
    
    scoreable = [r for r in runners if r.market_odds and r.market_odds > 1]
    if scoreable:
        scored = [(r, calculate_mix_score(r)) for r in scoreable]
        scored.sort(key=lambda x: -x[1])
        mix_runners = [r for r, _ in scored[:top_n]]
    else:
        mix_runners = favourites[:top_n]
    
    lines.append("")
    lines.append("  ◆ BEST MIX (probability + value - uncertainty)")
    lines.append("  " + "─" * 96)
    for r in mix_runners:
        score = calculate_mix_score(r) if r.market_odds else 0
        label = "BET" if r.recommended_stake > 0 else "WATCH"
        lines.append("  " + format_runner_line(r, label) + f" | Mix {score:.3f}")

    advanced = calculate_advanced_ranking(runners)[:top_n]
    lines.append("")
    lines.append("  ◆ ADVANCED ORDER (stat-weighted projected finish)")
    lines.append("  " + "─" * 96)
    for i, item in enumerate(advanced, 1):
        r = item['runner']
        lines.append(
            f"  #{i} {r.horse_name[:18]:<18} | Score {item['score']:.3f} | "
            f"Win {r.p_win*100:>5.1f}% | Top3 {r.p_top3*100:>5.1f}% | "
            f"AvgPos {r.avg_finish_position:>4.2f}"
        )
    
    bets = [r for r in runners if r.recommended_stake > 0]
    if bets:
        bets.sort(key=lambda r: -r.recommended_stake)
        total_stake = sum(r.recommended_stake for r in bets)
        
        lines.append("")
        lines.append("  ★ RECOMMENDED BETS")
        lines.append("  " + "─" * 96)
        for r in bets:
            lines.append("  " + format_runner_line(r, "BET"))
        lines.append(f"  " + "─" * 96)
        lines.append(f"    Total stake: {total_stake*100:.1f}% of bankroll across {len(bets)} bet(s)")
    else:
        lines.append("")
        lines.append("  ★ NO RECOMMENDED BETS (no runners meet edge/Kelly thresholds)")
    
    lines.append("")
    return "\n".join(lines)


def summarize_meeting(results, show_no_bets=False):
    """Generate one-line-per-race meeting summary."""
    lines = [
        "",
        "╔" + "═" * 100 + "╗",
        "║  MEETING SUMMARY - BEST BETS BY RACE".ljust(100) + "  ║",
        "╚" + "═" * 100 + "╝",
        "",
        f"  {'Race':<20} {'MIX PICK':<35} {'VALUE PICK':<35} {'Total Stake':>10}",
        "  " + "─" * 100,
    ]
    
    total_bets = 0
    total_stake = 0
    
    for result in results:
        runners = result.runners
        if not runners:
            continue
        
        race_label = f"{result.track_name[:12]}"
        if result.race_number:
            race_label += f" R{result.race_number}"
        race_label += f" ({result.distance_m}m)"
        
        scoreable = [r for r in runners if r.market_odds and r.market_odds > 1]
        if scoreable:
            scored = [(r, calculate_mix_score(r)) for r in scoreable]
            scored.sort(key=lambda x: -x[1])
            mix_pick = scored[0][0]
            mix_str = f"{mix_pick.horse_name[:15]} ({mix_pick.p_win*100:.0f}%, "
            mix_str += f"{mix_pick.value_edge*100:+.0f}%, " if mix_pick.value_edge else "N/A, "
            mix_str += f"{mix_pick.recommended_stake*100:.1f}%)" if mix_pick.recommended_stake > 0 else "WATCH)"
        else:
            mix_pick = runners[0]
            mix_str = f"{mix_pick.horse_name[:15]} ({mix_pick.p_win*100:.0f}%, N/A)"
        
        value_runners = [r for r in runners if r.value_edge and r.value_edge > 0]
        if value_runners:
            value_pick = max(value_runners, key=lambda r: r.value_edge)
            value_str = f"{value_pick.horse_name[:15]} ({value_pick.p_win*100:.0f}%, "
            value_str += f"{value_pick.value_edge*100:+.0f}%, "
            value_str += f"{value_pick.recommended_stake*100:.1f}%)" if value_pick.recommended_stake > 0 else "WATCH)"
        else:
            value_str = "None"
        
        race_stake = sum(r.recommended_stake for r in runners if r.recommended_stake > 0)
        race_bets = len([r for r in runners if r.recommended_stake > 0])
        stake_str = f"{race_stake*100:.1f}%" if race_stake > 0 else "-"
        
        if race_bets > 0 or show_no_bets:
            lines.append(f"  {race_label:<20} {mix_str:<35} {value_str:<35} {stake_str:>10}")
            total_bets += race_bets
            total_stake += race_stake
    
    lines.append("  " + "─" * 100)
    lines.append(f"  {'TOTAL':<20} {'':<35} {'':<35} {total_stake*100:.1f}%")
    lines.append(f"  {total_bets} bet(s) across {len(results)} race(s)")
    lines.append("")
    
    return "\n".join(lines)


def get_all_bets(results):
    """
    Extract all recommended bets across a meeting.

    Returns list of dicts with race info and runner details.
    """
    all_bets = []
    for result in results:
        for runner in result.runners:
            if runner.recommended_stake > 0:
                all_bets.append({
                    'track': result.track_name,
                    'race_number': result.race_number,
                    'distance': result.distance_m,
                    'horse': runner.horse_name,
                    'win_prob': runner.p_win,
                    'fair_odds': runner.fair_odds_win,
                    'market_odds': runner.market_odds,
                    'edge': runner.value_edge,
                    'stake': runner.recommended_stake,
                    'ci_lower': runner.p_win_ci_lower,
                    'ci_upper': runner.p_win_ci_upper,
                    'mix_score': calculate_mix_score(runner),
                })
    
    all_bets.sort(key=lambda x: -x['mix_score'])
    return all_bets


def print_bet_slip(results):
    """Print a clean betting slip for all recommended bets."""
    bets = get_all_bets(results)
    
    if not bets:
        print("\n  ╔═══════════════════════════════════════════════════╗")
        print("  ║  NO BETS TODAY - No runners meet edge thresholds  ║")
        print("  ╚═══════════════════════════════════════════════════╝\n")
        return
    
    lines = [
        "",
        "╔" + "═" * 90 + "╗",
        "║  TODAY'S BETTING SLIP".ljust(90) + "  ║",
        "╠" + "═" * 90 + "╣",
    ]
    
    total_stake = 0
    for i, bet in enumerate(bets, 1):
        ci_str = f"[{bet['ci_lower']*100:.0f}-{bet['ci_upper']*100:.0f}]"
        track_label = bet['track'][:12]
        if bet.get('race_number'):
            track_label = f"{track_label} R{bet['race_number']}"
        line = (f"  {i}. {bet['horse'][:20]:<20} @ {track_label:<12} | "
                f"{bet['win_prob']*100:>4.0f}% {ci_str:<9} | "
                f"${bet['market_odds']:.2f} → ${bet['fair_odds']:.2f} | "
                f"Edge {bet['edge']*100:>+5.1f}% | "
                f"Stake {bet['stake']*100:>4.1f}%")
        lines.append("║" + line.ljust(90) + "║")
        total_stake += bet['stake']
    
    lines.append("╠" + "═" * 90 + "╣")
    summary = f"  TOTAL: {len(bets)} bet(s) | {total_stake*100:.1f}% of bankroll"
    lines.append("║" + summary.ljust(90) + "║")
    lines.append("╚" + "═" * 90 + "╝")
    lines.append("")
    
    print("\n".join(lines))



class IntegratedAnalyzer:
    """Combines v8.2 factor-based scoring with Monte Carlo simulation."""
    
    def __init__(self, v82_model=None, mc_config=None, n_sims=25000):
        self.v82_model = v82_model
        self.mc_engine = MonteCarloEngine(mc_config)
        self.n_sims = n_sims
    
    def analyze_race(self, race, race_dict=None, seed=None, with_exotics=False):
        results = {'race': {'course': race.course, 'race_number': race.race_number, 'distance': race.distance},
                   'v82_analysis': None, 'mc_result': None, 'combined': []}
        
        v82_analysis = self.v82_model.analyze(race) if self.v82_model else None
        results['v82_analysis'] = v82_analysis
        
        mc_input = self._convert_to_mc_input(race, v82_analysis) if v82_analysis else self._race_to_mc_input(race)
        
        race_input = RaceInput.from_dict(mc_input)
        mc_result = self.mc_engine.simulate_race(race_input, self.n_sims, seed, with_exotics)
        results['mc_result'] = mc_result
        
        results['combined'] = self._combine_results(v82_analysis, mc_result)
        return results
    
    def _convert_to_mc_input(self, race, v82_analysis):
        runners = [{'horse_name': r.get('horse'), 'barrier': r.get('barrier', 0),
                   'weight_kg': r.get('weight', 57), 'jockey': r.get('jockey', ''),
                   'base_strength': r.get('total', 50),
                   'base_p_win': r.get('model_prob', 0) / 100 if r.get('model_prob') else None,
                   'speed_map_hint': r.get('style', 'midfield'),
                   'market_odds_now': r.get('sp') or r.get('market_odds'),
                   'features': r.get('features', {})} for r in v82_analysis]
        return {'race_id': race.race_id, 'race_number': race.race_number,
                'track_name': race.course, 'distance_m': race.distance,
                'going': race.going, 'race_class': str(race.class_level), 'runners': runners}
    
    def _race_to_mc_input(self, race):
        runners = [{'horse_name': r.horse, 'barrier': r.barrier, 'weight_kg': r.weight,
                   'jockey': r.jockey, 'base_strength': 50, 'market_odds_now': r.sp}
                  for r in race.runners]
        return {'race_id': race.race_id, 'race_number': race.race_number,
                'track_name': race.course, 'distance_m': race.distance,
                'going': race.going, 'race_class': str(race.class_level), 'runners': runners}
    
    def _combine_results(self, v82_analysis, mc_result):
        mc_lookup = {r.horse_name.lower(): r for r in mc_result.runners}
        combined = []
        
        source = v82_analysis if v82_analysis else [{'horse': r.horse_name} for r in mc_result.runners]
        for v82 in source:
            horse_name = (v82.get('horse', '') if isinstance(v82, dict) else v82).lower()
            mc = mc_lookup.get(horse_name)
            
            v82_prob = v82.get('model_prob') if isinstance(v82, dict) else None
            mc_prob = mc.p_win if mc else None
            
            entry = {
                'horse': v82.get('horse') if isinstance(v82, dict) else horse_name,
                'v82_prob': v82_prob,
                'mc_p_win': mc_prob,
                'mc_p_place': mc.p_top3 if mc else None,
                'mc_fair_odds': mc.fair_odds_win if mc else None,
                'mc_ci': f"[{mc.p_win_ci_lower:.1%}-{mc.p_win_ci_upper:.1%}]" if mc else None,
                'combined_prob': self._blend_probs(v82_prob, mc_prob),
                'market_odds': mc.market_odds if mc else None,
                'value_edge': mc.value_edge if mc else None,
                'mc_kelly_stake': mc.recommended_stake if mc else 0,
            }
            combined.append(entry)
        
        combined.sort(key=lambda x: -(x.get('combined_prob') or x.get('mc_p_win') or 0))
        return combined
    
    def _blend_probs(self, v82_prob, mc_prob, v82_weight=0.4):
        if v82_prob is None and mc_prob is None: return None
        if v82_prob is None: return mc_prob
        if mc_prob is None: return v82_prob / 100 if v82_prob > 1 else v82_prob
        v82_dec = v82_prob / 100 if v82_prob > 1 else v82_prob
        return v82_weight * v82_dec + (1 - v82_weight) * mc_prob


def format_combined_results(results):
    lines = ["", "=" * 100,
             f"  {results['race']['course']} R{results['race']['race_number']} - {results['race']['distance']}m",
             "=" * 100, "",
             f"  {'Horse':<20} {'v8.2%':>7} {'MC%':>7} {'CI':>15} {'Blend%':>7} {'Fair':>7} {'Mkt':>7} {'Edge':>7} {'Kelly':>7}",
             "  " + "-" * 96]
    
    for r in results['combined']:
        v82_str = f"{r.get('v82_prob', 0):.1f}%" if r.get('v82_prob') else "N/A"
        mc_str = f"{r.get('mc_p_win', 0)*100:.1f}%" if r.get('mc_p_win') else "N/A"
        blend_str = f"{r.get('combined_prob', 0)*100:.1f}%" if r.get('combined_prob') else "N/A"
        fair_str = f"${r.get('mc_fair_odds', 0):.2f}" if r.get('mc_fair_odds') else "N/A"
        mkt_str = f"${r.get('market_odds', 0):.2f}" if r.get('market_odds') else "N/A"
        edge_str = f"{r.get('value_edge', 0)*100:+.1f}%" if r.get('value_edge') else "N/A"
        kelly_str = f"{r.get('mc_kelly_stake', 0)*100:.1f}%" if r.get('mc_kelly_stake') else "-"
        lines.append(f"  {r.get('horse', '')[:20]:<20} {v82_str:>7} {mc_str:>7} {r.get('mc_ci', 'N/A'):>15} "
                    f"{blend_str:>7} {fair_str:>7} {mkt_str:>7} {edge_str:>7} {kelly_str:>7}")
    lines.append("")
    return "\n".join(lines)



def main():
    parser = argparse.ArgumentParser(description="Monte Carlo Horse Racing Simulation")
    parser.add_argument("--input", "-i", type=str, help="Input JSON file (race or meeting)")
    parser.add_argument("--n_sims", "-n", type=int, default=50000, help="Number of simulations")
    parser.add_argument("--seed", "-s", type=int, help="Random seed")
    parser.add_argument("--with_exotics", action="store_true", help="Calculate exacta/trifecta")
    parser.add_argument("--out", "-o", type=str, help="Output JSON file")
    parser.add_argument("--config", "-c", type=str, help="Config JSON file")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress output")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show full MC tables (default: summary only)")
    parser.add_argument("--slip", action="store_true", help="Show betting slip format")
    
    parser.add_argument("--integrated", action="store_true", help="Use integrated v8.2 + MC analysis")
    parser.add_argument("--train_dir", type=str, help="Training data directory (for integrated mode)")
    parser.add_argument("--data_dir", type=str, help="Racecard data directory (for integrated mode)")
    parser.add_argument("--stats", type=str, help="Supplementary stats JSON")
    parser.add_argument("--date", type=str, help="Filter races to date YYYY-MM-DD (integrated mode)")
    parser.add_argument("--tracks", type=str, help="Filter races to tracks (comma-separated, integrated mode)")
    parser.add_argument("--races", type=str, help="Filter races to race numbers (comma-separated, integrated mode)")
    parser.add_argument("--max_races", type=int, default=15, help="Max races to analyze (integrated mode, 0=all)")
    args = parser.parse_args()
    
    config = DEFAULT_CONFIG.copy()
    if args.config: config.update(load_config(args.config))
    config['n_sims'] = args.n_sims
    
    if args.integrated:
        try:
            import sys
            import importlib.util
            
            spec = importlib.util.spec_from_file_location("racing_system_v8_3_mc", "racing_system_v8.3_mc.py")
            racing_system = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(racing_system)
            
            RacingModel = racing_system.RacingModel
            SupplementaryStats = racing_system.SupplementaryStats
            load_all_races = racing_system.load_all_races
            convert_races = racing_system.convert_races
            TARGET_TRACKS = racing_system.TARGET_TRACKS
            safe_parse_date = racing_system.safe_parse_date
            
        except ImportError as e:
            print(f"Error: Could not import racing_system_v8.3_mc module: {e}")
            print("Make sure racing_system_v8.3_mc.py is in the same directory")
            return
        
        print("\n" + "=" * 80)
        print("  INTEGRATED ANALYSIS (v8.2 + Monte Carlo)")
        print("=" * 80)
        
        supp_stats = SupplementaryStats(args.stats) if args.stats else None
        
        print(f"\n[Loading training data from {args.train_dir}]")
        train_dicts = load_all_races([args.train_dir])
        train_races = convert_races(train_dicts)
        print(f"  ✓ {len(train_races)} races")
        
        v82_model = RacingModel(supp_stats)
        v82_model.train(train_races, train_dicts)
        
        print(f"\n[Loading racecards from {args.data_dir}]")
        predict_dicts = load_all_races([args.data_dir])
        predict_races = convert_races(predict_dicts)
        
        today = datetime.now().date()
        predict_races = [r for r in predict_races if r.date_dt and r.date_dt.date() >= today]
        target_lower = [t.lower() for t in TARGET_TRACKS]
        predict_races = [r for r in predict_races if any(t in r.course.lower() for t in target_lower)]
        
        if args.date:
            try:
                target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
            except ValueError:
                print(f"Error: invalid --date {args.date!r}, expected YYYY-MM-DD")
                return
            predict_races = [r for r in predict_races if r.date_dt and r.date_dt.date() == target_date]
        
        if args.tracks:
            track_filters = [t.strip().lower() for t in args.tracks.split(",") if t.strip()]
            if track_filters:
                predict_races = [
                    r for r in predict_races
                    if any(tf in (r.course or "").lower() for tf in track_filters)
                ]
        
        if args.races:
            try:
                race_numbers = {int(x.strip()) for x in args.races.split(",") if x.strip()}
            except ValueError:
                print(f"Error: invalid --races {args.races!r}, expected comma-separated numbers")
                return
            if race_numbers:
                predict_races = [r for r in predict_races if r.race_number in race_numbers]
        
        print(f"  ✓ {len(predict_races)} upcoming races at target tracks")
        
        analyzer = IntegratedAnalyzer(v82_model, config, args.n_sims)
        
        all_results = []
        mc_results = []
        races_to_run = predict_races
        if args.max_races and args.max_races > 0:
            races_to_run = predict_races[:args.max_races]
        for i, race in enumerate(races_to_run):
            result = analyzer.analyze_race(race, seed=args.seed + i if args.seed else None,
                                          with_exotics=args.with_exotics)
            all_results.append(result)
            if result['mc_result']:
                mc_results.append(result['mc_result'])
            
            if not args.quiet:
                if args.verbose:
                    print(format_combined_results(result))
                    if args.with_exotics and result['mc_result']:
                        print(format_exotics(result['mc_result']))
                else:
                    if result['mc_result']:
                        print(summarize_best_bets(result['mc_result']))
        
        if not args.quiet and mc_results:
            print(summarize_meeting(mc_results))
            if args.slip:
                print_bet_slip(mc_results)
        
        if args.out:
            output = [{'race': r['race'], 'combined': r['combined']} for r in all_results]
            with open(args.out, 'w') as f: json.dump(output, f, indent=2)
            print(f"\nSaved to {args.out}")
    
    elif args.input:
        with open(args.input) as f: data = json.load(f)
        
        engine = MonteCarloEngine(config)
        
        if isinstance(data, list):
            races = [RaceInput.from_dict(r) for r in data]
            results = engine.simulate_meeting(races, args.n_sims, args.seed, args.with_exotics)
        else:
            race = RaceInput.from_dict(data)
            results = [engine.simulate_race(race, args.n_sims, args.seed, args.with_exotics)]
        
        if not args.quiet:
            for result in results:
                if args.verbose:
                    print(format_race_result(result))
                    if args.with_exotics: print(format_exotics(result))
                else:
                    print(summarize_best_bets(result))
            
            if len(results) > 1:
                print(summarize_meeting(results))
            
            if args.slip:
                print_bet_slip(results)
        
        if args.out:
            output = [result_to_dict(r) for r in results]
            if len(output) == 1: output = output[0]
            with open(args.out, 'w') as f: json.dump(output, f, indent=2)
            print(f"\nSaved to {args.out}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
