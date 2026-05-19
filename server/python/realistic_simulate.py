"""Realistic Monte Carlo Race Simulation — mixture noise, multi-phase energy dynamics, and sectional profile-based pace scenario simulation."""

import numpy as np
from typing import Dict, List, Tuple, Optional, Any
import math

try:
    from normalize import parse_form_string
except ImportError:
    def parse_form_string(form: str, max_runs: int = 6):
        positions = []
        for char in str(form or '').strip()[:max_runs]:
            if char.isdigit() and char != '0':
                positions.append(int(char))
            elif char == '0' or char.lower() == 'x':
                positions.append(10)
        
        if not positions:
            return {'positions': [], 'consistency': 0.5, 'trend': 0, 'form_score': 5.0}
        
        avg_pos = sum(positions) / len(positions)
        variance = sum((p - avg_pos) ** 2 for p in positions) / len(positions) if len(positions) >= 2 else 0
        consistency = max(0, 1 - variance / 25)
        
        return {
            'positions': positions,
            'consistency': round(consistency, 2),
            'trend': 0,
            'form_score': 5.0
        }



SIMULATION_CONFIG = {
    'default_sims': 20000,
    'base_sigma': 1.0,
    'min_sigma': 0.6,
    'max_sigma': 2.0,
    'downside_mixture_ratio': 0.30,
    'downside_shift': 0.8,
    'fat_tail_ratio': 0.08,
    'fat_tail_df': 4.5,
    'fat_tail_scale': 1.15,
    'confidence_threshold': 0.18,
    'elite_sigma_reduction': 0.05,
    'favorite_sigma_reduction': 0.1,
    'unknown_form_sigma_boost': 1.18,
    'very_light_form_sigma_boost': 1.10,
    # Race-type sigma multipliers (from empirical analysis of 26K results)
    'sigma_sprint_multiplier': 1.30,        # Sprints are chaotic (6.4% fav rate)
    'sigma_mile_multiplier': 1.0,           # Mile is baseline (25.8% fav rate)
    'sigma_middle_multiplier': 0.95,        # Middle distance slightly more predictable
    'sigma_staying_multiplier': 0.88,       # Staying races: form tells most
    'sigma_firm_going_multiplier': 0.96,    # Firm surfaces are usually more stable
    'sigma_good_going_multiplier': 1.0,     # Good is baseline
    'sigma_soft_going_multiplier': 1.10,    # Soft introduces extra uncertainty
    'sigma_heavy_going_multiplier': 1.22,   # Heavy materially increases outcome variance
    'sigma_unknown_going_multiplier': 1.06, # Unknown/volatile going should be wider
    'sigma_class1_multiplier': 1.18,        # Class 1: lowest predictability (8.4% fav rate)
    'sigma_maiden_multiplier': 1.25,        # Maidens: many unknown form horses
    'sigma_small_field_multiplier': 1.10,   # <=5 runners: compressed odds, less separable
    # Going-specific downside asymmetry (negative skew by condition)
    'downside_ratio_soft': 0.34,
    'downside_ratio_heavy': 0.40,
    'downside_shift_soft': 0.92,
    'downside_shift_heavy': 1.08,
}

SECTIONAL_CONFIG = {
    'early_speed_threshold_pct': 1.01,   # 101% of average = "speed horse" (calibrated from backtest)
    'hot_pace_min_speed_count': 3,       # 3+ speed horses → hot
    'genuine_pace_min_speed_count': 2,   # 2 speed horses → genuine
    'soft_pace_min_speed_count': 1,      # 1 speed horse → soft (lone speed)
    'home_speed_weight': 0.02,           # Sectional adjustment multiplier
    'default_early_median': 100.0,       # Fallback early speed for no-profile horses
    'default_home_median': 100.0,        # Fallback home speed for no-profile horses
    'default_sectional_std': 2.0,        # Fallback std for no-profile horses
    'pace_adjustments': {
        'leader':     {'hot': -0.06, 'genuine': 0.0,  'soft': 0.12, 'slow': 0.06},
        'on_pace':    {'hot': -0.02, 'genuine': 0.0,  'soft': 0.04, 'slow': 0.02},
        'handy':      {'hot': -0.01, 'genuine': 0.02, 'soft': 0.0,  'slow': 0.0},
        'midfield':   {'hot': 0.0,   'genuine': 0.0,  'soft': 0.0,  'slow': 0.0},
        'backmarker': {'hot': 0.04,  'genuine': 0.0,  'soft': -0.06, 'slow': -0.06},
    },
}

# Multi-phase race structure for sit-and-sprint tactical modeling
PHASE_CONFIG = {
    'sprint': {
        'phases': [
            {'name': 'jump_settle', 'energy_weight': 0.15},
            {'name': 'mid_race',    'energy_weight': 0.25},
            {'name': 'turn_home',   'energy_weight': 0.30},
            {'name': 'final_sprint','energy_weight': 0.30},
        ],
    },
    'mile': {
        'phases': [
            {'name': 'early',  'energy_weight': 0.15},
            {'name': 'middle', 'energy_weight': 0.20},
            {'name': 'late',   'energy_weight': 0.30},
            {'name': 'sprint', 'energy_weight': 0.35},
        ],
    },
    'middle': {
        'phases': [
            {'name': 'early',  'energy_weight': 0.10},
            {'name': 'middle', 'energy_weight': 0.20},
            {'name': 'late',   'energy_weight': 0.30},
            {'name': 'sprint', 'energy_weight': 0.40},
        ],
    },
    'staying': {
        'phases': [
            {'name': 'early',  'energy_weight': 0.10},
            {'name': 'middle', 'energy_weight': 0.15},
            {'name': 'late',   'energy_weight': 0.30},
            {'name': 'sprint', 'energy_weight': 0.45},
        ],
    },
}

# Phase-dependent running style advantages (sit-and-sprint model)
PHASE_STYLE_MATRIX = {
    'jump_settle':  {'leader': 0.06, 'on_pace': 0.03, 'handy': 0.01, 'midfield': 0.0, 'backmarker': -0.03},
    'early':        {'leader': 0.08, 'on_pace': 0.04, 'handy': 0.01, 'midfield': 0.0, 'backmarker': -0.04},
    'mid_race':     {'leader': 0.03, 'on_pace': 0.02, 'handy': 0.0,  'midfield': 0.0, 'backmarker': -0.02},
    'middle':       {'leader': 0.02, 'on_pace': 0.02, 'handy': 0.0,  'midfield': 0.0, 'backmarker': -0.01},
    'turn_home':    {'leader': -0.03,'on_pace': 0.0,  'handy': 0.02, 'midfield': 0.02,'backmarker': 0.04},
    'late':         {'leader': -0.04,'on_pace': -0.01,'handy': 0.02, 'midfield': 0.02,'backmarker': 0.04},
    'final_sprint': {'leader': -0.08,'on_pace': -0.02,'handy': 0.02, 'midfield': 0.03,'backmarker': 0.08},
    'sprint':       {'leader': -0.08,'on_pace': -0.02,'handy': 0.02, 'midfield': 0.03,'backmarker': 0.08},
}

# Energy depletion rates per phase by running style
# Leaders burn energy early (conserve less), backmarkers save for sprint
ENERGY_DEPLETION = {
    'leader':     [0.12, 0.10, 0.10, 0.08],
    'on_pace':    [0.10, 0.09, 0.09, 0.08],
    'handy':      [0.09, 0.08, 0.08, 0.08],
    'midfield':   [0.08, 0.08, 0.08, 0.08],
    'backmarker': [0.05, 0.06, 0.08, 0.12],  # Conserve early, explode late
}

# Position-dependent jockey tactical profiles
# Each elite jockey gets a mu bonus depending on how the horse is ridden.
# e.g. Bowman excels on backmarkers (+0.10) but is average on leaders (+0.04).
# Replaces the blunt 15% sigma reduction with nuanced tactical modeling.
JOCKEY_TACTICAL_PROFILES = {
    'bowman':   {'leader': 0.04, 'on_pace': 0.05, 'midfield': 0.06, 'handy': 0.06, 'backmarker': 0.10},
    'mcdonald': {'leader': 0.06, 'on_pace': 0.08, 'midfield': 0.05, 'handy': 0.06, 'backmarker': 0.04},
    'mcevoy':   {'leader': 0.05, 'on_pace': 0.06, 'midfield': 0.05, 'handy': 0.05, 'backmarker': 0.05},
    'zahra':    {'leader': 0.04, 'on_pace': 0.05, 'midfield': 0.06, 'handy': 0.06, 'backmarker': 0.07},
    'shinn':    {'leader': 0.05, 'on_pace': 0.06, 'midfield': 0.06, 'handy': 0.06, 'backmarker': 0.06},
    'avdulla':  {'leader': 0.05, 'on_pace': 0.06, 'midfield': 0.05, 'handy': 0.05, 'backmarker': 0.05},
    'rawiller': {'leader': 0.06, 'on_pace': 0.07, 'midfield': 0.05, 'handy': 0.05, 'backmarker': 0.04},
    'purton':   {'leader': 0.08, 'on_pace': 0.07, 'midfield': 0.06, 'handy': 0.06, 'backmarker': 0.05},
    'moreira':  {'leader': 0.07, 'on_pace': 0.07, 'midfield': 0.07, 'handy': 0.07, 'backmarker': 0.06},
    'lane':     {'leader': 0.04, 'on_pace': 0.05, 'midfield': 0.05, 'handy': 0.05, 'backmarker': 0.08},
    'melham':   {'leader': 0.05, 'on_pace': 0.05, 'midfield': 0.05, 'handy': 0.05, 'backmarker': 0.05},
    'oliver':   {'leader': 0.05, 'on_pace': 0.06, 'midfield': 0.06, 'handy': 0.06, 'backmarker': 0.06},
    'collett':  {'leader': 0.04, 'on_pace': 0.05, 'midfield': 0.05, 'handy': 0.05, 'backmarker': 0.06},
    'dee':      {'leader': 0.05, 'on_pace': 0.05, 'midfield': 0.05, 'handy': 0.05, 'backmarker': 0.05},
    'walker':   {'leader': 0.05, 'on_pace': 0.06, 'midfield': 0.05, 'handy': 0.05, 'backmarker': 0.05},
    'currie':   {'leader': 0.05, 'on_pace': 0.06, 'midfield': 0.05, 'handy': 0.05, 'backmarker': 0.05},
}

# Fallback profiles by jockey category when name not in JOCKEY_TACTICAL_PROFILES.
# Values are lower than elite profiles, reflecting reduced tactical execution reliability.
JOCKEY_CATEGORY_PROFILES = {
    'senior_metro': {  # 10%+ win rate metro jockeys not individually profiled
        'leader': 0.03, 'on_pace': 0.04, 'midfield': 0.03, 'handy': 0.03, 'backmarker': 0.03,
    },
    'senior_provincial': {  # Experienced provincial/country-based jockeys
        'leader': 0.02, 'on_pace': 0.02, 'midfield': 0.02, 'handy': 0.02, 'backmarker': 0.02,
    },
    'apprentice': {  # Apprentices: ride forward instinctively, underperform on tactical backmarker rides
        'leader': 0.01, 'on_pace': 0.00, 'midfield': -0.01, 'handy': 0.00, 'backmarker': -0.02,
    },
    'unknown': {  # No data available
        'leader': 0.0, 'on_pace': 0.0, 'midfield': 0.0, 'handy': 0.0, 'backmarker': 0.0,
    },
}

# Style-dependent mixture noise modifiers for bimodal tactical outcomes.
# Backmarkers on slow pace: trapped, no run → higher downside ratio.
# Backmarkers on hot pace: perfect setup → lower downside ratio.
# Leaders on hot pace: likely to tire → higher downside ratio.
STYLE_NOISE_MODIFIERS = {
    'leader': {
        'slow':    {'downside_ratio_mod': -0.05, 'downside_shift_mod': -0.15},
        'hot':     {'downside_ratio_mod': +0.10, 'downside_shift_mod': +0.20},
        'default': {'downside_ratio_mod':  0.00, 'downside_shift_mod':  0.00},
    },
    'on_pace': {
        'hot':     {'downside_ratio_mod': +0.06, 'downside_shift_mod': +0.12},
        'default': {'downside_ratio_mod':  0.00, 'downside_shift_mod':  0.00},
    },
    'backmarker': {
        'slow':    {'downside_ratio_mod': +0.10, 'downside_shift_mod': +0.15},
        'hot':     {'downside_ratio_mod': -0.05, 'downside_shift_mod': -0.10},
        'default': {'downside_ratio_mod': +0.05, 'downside_shift_mod': +0.08},
    },
    'midfield': {
        'default': {'downside_ratio_mod': 0.0, 'downside_shift_mod': 0.0},
    },
    'handy': {
        'default': {'downside_ratio_mod': 0.0, 'downside_shift_mod': 0.0},
    },
}

# Tight-turn tracks: leaders/on-pace hold advantage through bends
TIGHT_TURN_TRACKS = {
    'moonee valley': {'leader_boost': 0.06, 'on_pace_boost': 0.04, 'backmarker_penalty': 0.04},
    'geelong':       {'leader_boost': 0.06, 'on_pace_boost': 0.04, 'backmarker_penalty': 0.04},
    'ballarat':      {'leader_boost': 0.05, 'on_pace_boost': 0.03, 'backmarker_penalty': 0.03},
    'ipswich':       {'leader_boost': 0.05, 'on_pace_boost': 0.03, 'backmarker_penalty': 0.03},
    'caulfield':     {'leader_boost': 0.03, 'on_pace_boost': 0.02, 'backmarker_penalty': 0.02},
}


def _classify_jockey_category(jockey_name: str, runner: Optional[Dict] = None) -> str:
    """Classify jockey into a fallback category when not in JOCKEY_TACTICAL_PROFILES."""
    name_lower = jockey_name.lower().strip()

    # Apprentice indicators in AU: trailing (a), 'a' suffix, or explicit claim
    if any(ind in name_lower for ind in ('(a)', ' a)', 'apprentice')):
        return 'apprentice'

    # Use momentum data if available on the runner dict
    if runner:
        momentum = runner.get('jockey_momentum_data') or runner.get('jockey_momentum', {})
        if momentum:
            sr = momentum.get('strike_rate_30d', momentum.get('weighted_strike_rate', 0))
            rides = momentum.get('recent_rides', momentum.get('rides_30d', 0))
            if rides >= 10:
                if sr >= 15.0:
                    return 'senior_metro'
                elif sr >= 8.0:
                    return 'senior_provincial'

    return 'unknown'


def _get_distance_category(distance_m):
    """Map race distance to phase category."""
    if distance_m <= 1200:
        return 'sprint'
    elif distance_m <= 1600:
        return 'mile'
    elif distance_m <= 2200:
        return 'middle'
    return 'staying'


def _extract_numeric_track_rating(going_text: str) -> Optional[int]:
    """Extract numerical AU track rating (1-10) if present."""
    if not going_text:
        return None

    digits = ''.join(ch for ch in str(going_text) if ch.isdigit())
    if not digits:
        return None

    try:
        rating = int(digits)
    except (TypeError, ValueError):
        return None

    if 1 <= rating <= 10:
        return rating
    return None


def classify_going(going_text: str) -> str:
    """
    Map raw going text to: firm, good, soft, heavy, synthetic, unknown.

    Supports textual and numerical AU ratings (Firm 1 ... Heavy 10).
    """
    text = str(going_text or '').strip().lower()
    if not text:
        return 'unknown'

    rating = _extract_numeric_track_rating(text)
    if rating is not None:
        if rating <= 2:
            return 'firm'
        if rating <= 4:
            return 'good'
        if rating <= 7:
            return 'soft'
        return 'heavy'

    if any(k in text for k in ('synthetic', 'poly', 'tapeta', 'all weather')):
        return 'synthetic'
    if any(k in text for k in ('heavy', 'bog', 'squelchy')):
        return 'heavy'
    if any(k in text for k in ('soft', 'yield', 'rain', 'wet')):
        return 'soft'
    if any(k in text for k in ('firm', 'fast', 'hard')):
        return 'firm'
    if any(k in text for k in ('good', 'dead')):
        return 'good'

    return 'unknown'


def get_going_uncertainty_profile(going_text: str) -> Dict[str, float]:
    """
    Return condition-aware uncertainty parameters.

    Soft/heavy widen variance and increase downside skew and fat tails.
    """
    cfg = SIMULATION_CONFIG
    category = classify_going(going_text)

    profiles = {
        'firm': {
            'sigma_multiplier': cfg.get('sigma_firm_going_multiplier', 0.96),
            'downside_ratio': 0.26,
            'downside_shift': 0.70,
            'tail_ratio': 0.06,
            'tail_scale': 1.00,
        },
        'good': {
            'sigma_multiplier': cfg.get('sigma_good_going_multiplier', 1.0),
            'downside_ratio': cfg.get('downside_mixture_ratio', 0.30),
            'downside_shift': cfg.get('downside_shift', 0.8),
            'tail_ratio': cfg.get('fat_tail_ratio', 0.08),
            'tail_scale': cfg.get('fat_tail_scale', 1.15),
        },
        'soft': {
            'sigma_multiplier': cfg.get('sigma_soft_going_multiplier', 1.10),
            'downside_ratio': cfg.get('downside_ratio_soft', 0.34),
            'downside_shift': cfg.get('downside_shift_soft', 0.92),
            'tail_ratio': 0.12,
            'tail_scale': 1.24,
        },
        'heavy': {
            'sigma_multiplier': cfg.get('sigma_heavy_going_multiplier', 1.22),
            'downside_ratio': cfg.get('downside_ratio_heavy', 0.40),
            'downside_shift': cfg.get('downside_shift_heavy', 1.08),
            'tail_ratio': 0.16,
            'tail_scale': 1.34,
        },
        'synthetic': {
            'sigma_multiplier': 0.94,
            'downside_ratio': 0.24,
            'downside_shift': 0.65,
            'tail_ratio': 0.05,
            'tail_scale': 0.96,
        },
        'unknown': {
            'sigma_multiplier': cfg.get('sigma_unknown_going_multiplier', 1.06),
            'downside_ratio': max(0.32, cfg.get('downside_mixture_ratio', 0.30)),
            'downside_shift': max(0.9, cfg.get('downside_shift', 0.8)),
            'tail_ratio': max(0.10, cfg.get('fat_tail_ratio', 0.08)),
            'tail_scale': max(1.20, cfg.get('fat_tail_scale', 1.15)),
        },
    }

    profile = profiles.get(category, profiles['unknown']).copy()
    profile['going_category'] = category
    return profile


def simulate_multi_phase(mu, sigmas, running_styles, distance_m, n_sims, n, rng,
                         pace_scenario='genuine', pace_scenario_arr=None):
    """
    Multi-phase race simulation with energy dynamics.

    Each horse's performance in each phase depends on:
    1. Base ability (mu) + phase-specific noise
    2. Running style advantage for this phase
    3. Energy remaining (depleted by running style + pace pressure)
    """
    cat = _get_distance_category(distance_m)
    phases = PHASE_CONFIG[cat]['phases']

    # Pace pressure multiplier — per-sim when array provided
    pace_mult_map = {0: 1.3, 1: 1.0, 2: 0.7, 3: 0.6}  # hot/genuine/soft/slow
    pace_str_map = {'hot': 1.3, 'genuine': 1.0, 'soft': 0.7, 'slow': 0.6}

    if pace_scenario_arr is not None:
        p_mult = np.array([pace_mult_map.get(int(ps), 1.0) for ps in pace_scenario_arr],
                          dtype=np.float64)
    else:
        p_mult = np.full(n_sims, pace_str_map.get(pace_scenario, 1.0))

    style_indices = []
    for i in range(n):
        style_indices.append(running_styles[i] if i < len(running_styles) else 'midfield')

    energy = np.ones((n_sims, n))
    total_performance = np.zeros((n_sims, n))

    for phase_idx, phase in enumerate(phases):
        phase_name = phase['name']
        phase_weight = phase['energy_weight']

        phase_noise = rng.normal(0, 1, size=(n_sims, n)) * sigmas * 0.5

        style_adv = np.zeros(n)
        phase_styles = PHASE_STYLE_MATRIX.get(phase_name, {})
        for i in range(n):
            style_adv[i] = phase_styles.get(style_indices[i], 0.0)

        phase_perf = (mu + phase_noise + style_adv) * energy
        total_performance += phase_perf * phase_weight

        for i in range(n):
            style = style_indices[i]
            depletion_rates = ENERGY_DEPLETION.get(style, ENERGY_DEPLETION['midfield'])
            rate = depletion_rates[min(phase_idx, len(depletion_rates) - 1)]
            if style in ('leader', 'on_pace'):
                energy[:, i] *= (1.0 - np.minimum(rate * p_mult, 0.25))
            elif style == 'backmarker':
                energy[:, i] *= (1.0 - np.minimum(rate * (2.0 - p_mult), 0.25))
            else:
                energy[:, i] *= (1.0 - min(rate, 0.25))

    return total_performance


def _rsi_to_adj(rsi: float, style: str) -> float:
    """
    Continuous Race Shape Index to per-horse mu adjustment.

    Phase 2 enhancement (Beyer/Quirin methodology):
    RSI = first_half_time / second_half_time from horse's last run.
    RSI < 1 = hot pace horse faced last run.
    RSI > 1 = slow/favourable pace horse faced last run.

    Slopes calibrated so RSI=0.94 matches current categorical 'hot' adjustments
    and RSI=1.06 matches 'soft' adjustments:
      leader:     -1.0  (hot pace = good for leaders historically)
      on_pace:    -0.33
      handy:      -0.17
      midfield:    0.00  (neutral)
      backmarker: +0.67  (hot pace = good for backmarkers historically)

    Clipped to ±0.08 to stay within same magnitude as Engine 3 (±0.08).
    """
    slopes = {
        'leader':     -1.00,
        'on_pace':    -0.33,
        'handy':      -0.17,
        'midfield':    0.00,
        'backmarker':  0.67,
    }
    dev = rsi - 1.0
    adj = dev * slopes.get(style, 0.0)
    return float(max(-0.08, min(0.08, adj)))


def calculate_form_consistency(form: str) -> float:
    """
    Calculate form consistency from form string. Higher consistency = lower variance in simulations.

    Returns: consistency score 0-1 (1 = very consistent)
    """
    form_data = parse_form_string(form, max_runs=6)
    return form_data.get('consistency', 0.5)


def calculate_runner_sigma(
    runner: Dict,
    base_win_prob: float,
    field_size: int,
    is_elite_jockey: bool = False
) -> float:
    """
    Calculate per-runner variability (sigma) based on form consistency, market position, energy/fatigue factors, and other factors.

    Consistent horses get lower sigma (more predictable); unknown/lightly-raced horses get higher sigma.
    """
    config = SIMULATION_CONFIG
    
    sigma = config['base_sigma']
    
    form = runner.get('form', '')
    form_data = parse_form_string(form, max_runs=6)
    consistency = float(form_data.get('consistency', 0.5))
    known_runs = len(form_data.get('positions', []))

    consistency_factor = 1.0 - (consistency * 0.5)
    sigma = sigma * consistency_factor

    if known_runs == 0:
        sigma *= config.get('unknown_form_sigma_boost', 1.18)
    elif known_runs <= 2:
        sigma *= config.get('very_light_form_sigma_boost', 1.10)
    
    implied_prob = 1.0 / max(runner.get('market_odds', 5.0), 1.5)
    if implied_prob > 0.25:
        sigma *= (1.0 - config['favorite_sigma_reduction'])
    elif implied_prob < 0.05:
        sigma *= 1.2
    
    if is_elite_jockey:
        sigma *= (1.0 - config['elite_sigma_reduction'])
    
    fatigue_factor = runner.get('fatigue_factor', 1.0)
    if fatigue_factor < 0.90:
        sigma *= (1.0 + (1.0 - fatigue_factor) * 0.8)
    
    collapse_prob = runner.get('collapse_probability', 0)
    if collapse_prob > 0.2:
        sigma *= (1.0 + collapse_prob * 0.5)
    
    fitness = runner.get('fitness_factor', 1.0)
    if fitness < 0.93:
        sigma *= (1.0 + (1.0 - fitness) * 0.4)

    # Phase 2: Velocity decay penalty (Ward-Smith 1985)
    # High λ = horse decelerates sharply = more outcome variance
    # λ=0.0001→+0%, λ=0.0003→+14%, λ=0.0005→+25% (capped at +30%)
    lambda_decay = runner.get('lambda_decay')
    if lambda_decay is not None:
        lambda_factor = 1.0 + min(0.30, float(lambda_decay) * 600)
        sigma *= lambda_factor

    market_sigma_mod = runner.get('market_sigma_modifier', 1.0)
    if market_sigma_mod != 1.0:
        sigma *= market_sigma_mod

    sigma = max(config['min_sigma'], min(config['max_sigma'], sigma))

    return sigma


def generate_mixture_noise(
    n_sims: int,
    field_size: int,
    sigmas: np.ndarray,
    rng: np.random.Generator,
    going_profile: Optional[Dict[str, float]] = None,
    running_styles: Optional[List[str]] = None,
    pace_scenario_label: str = 'genuine',
) -> np.ndarray:
    """
    Generate mixture noise for race simulation.

    70% Normal(0, sigma) - standard performance
    30% Normal(-k*sigma, sigma) - underperformance events
    8%  Student-t(df) - fat-tail/upset outcomes

    When running_styles and pace_scenario_label are provided, applies style-dependent mixture modifiers for bimodal tactical outcomes (e.g. backmarkers on slow pace get higher downside probability).

    Returns: (n_sims, field_size) noise array
    """
    config = SIMULATION_CONFIG

    profile = going_profile or {}

    base_downside_ratio = float(profile.get('downside_ratio', config['downside_mixture_ratio']))
    base_downside_shift = float(profile.get('downside_shift', config['downside_shift']))
    tail_ratio = float(profile.get('tail_ratio', config.get('fat_tail_ratio', 0.08)))
    tail_df = max(2.2, float(config.get('fat_tail_df', 4.5)))
    tail_scale = float(profile.get('tail_scale', config.get('fat_tail_scale', 1.15)))

    mixture_ratios = np.full(field_size, base_downside_ratio)
    downside_shifts = np.full(field_size, base_downside_shift)

    if running_styles and len(running_styles) == field_size:
        pace_key = pace_scenario_label.lower() if pace_scenario_label else 'default'
        for i, style in enumerate(running_styles):
            style_mods = STYLE_NOISE_MODIFIERS.get(style, STYLE_NOISE_MODIFIERS.get('midfield', {}))
            if pace_key in style_mods:
                mods = style_mods[pace_key]
            elif 'hot' in pace_key and 'hot' in style_mods:
                mods = style_mods['hot']
            elif 'slow' in pace_key and 'slow' in style_mods:
                mods = style_mods['slow']
            else:
                mods = style_mods.get('default', {})
            mixture_ratios[i] += mods.get('downside_ratio_mod', 0.0)
            downside_shifts[i] += mods.get('downside_shift_mod', 0.0)

    mixture_ratios = np.clip(mixture_ratios, 0.0, 0.95)
    downside_shifts = np.maximum(downside_shifts, 0.0)
    tail_ratio = max(0.0, min(0.4, tail_ratio))

    base_noise = rng.normal(0, 1, size=(n_sims, field_size)) * sigmas
    downside_noise = rng.normal(-downside_shifts * sigmas, sigmas, size=(n_sims, field_size))
    tail_noise = rng.standard_t(df=tail_df, size=(n_sims, field_size)) * sigmas * tail_scale

    draws = rng.random(size=(n_sims, field_size))
    downside_mask = draws < mixture_ratios
    tail_mask = draws > (1.0 - tail_ratio)

    noise = np.where(downside_mask, downside_noise, base_noise)
    noise = np.where(tail_mask, tail_noise, noise)

    return noise


def get_race_type_sigma_multiplier(race_data: Dict) -> float:
    """
    Calculate race-type-specific sigma multiplier based on empirical data.

    Adjusts simulation variance based on distance, going, class, and field size.
    Based on analysis of 26,769 results.
    """
    config = SIMULATION_CONFIG
    multiplier = 1.0
    
    distance_m = race_data.get('distance_m', 1400)
    try:
        distance_m = int(distance_m)
    except (ValueError, TypeError):
        distance_m = 1400
    
    if distance_m < 1200:
        multiplier *= config.get('sigma_sprint_multiplier', 1.30)
    elif distance_m <= 1600:
        multiplier *= config.get('sigma_mile_multiplier', 1.0)
    elif distance_m <= 2000:
        multiplier *= config.get('sigma_middle_multiplier', 0.95)
    else:
        multiplier *= config.get('sigma_staying_multiplier', 0.88)
    
    going_text = race_data.get('going', race_data.get('track_condition', ''))
    going_profile = get_going_uncertainty_profile(going_text)
    multiplier *= float(going_profile.get('sigma_multiplier', 1.0))
    
    class_level = race_data.get('class_level', None)
    race_class = str(race_data.get('race_class', '')).lower()
    
    if class_level == 1 or 'cl1' in race_class or 'class 1' in race_class:
        multiplier *= config.get('sigma_class1_multiplier', 1.18)
    elif 'maiden' in race_class or 'mdn' in race_class:
        multiplier *= config.get('sigma_maiden_multiplier', 1.25)
    
    field_size = race_data.get('field_size', 10)
    try:
        field_size = int(field_size)
    except (ValueError, TypeError):
        field_size = 10
    
    if field_size <= 5:
        multiplier *= config.get('sigma_small_field_multiplier', 1.10)

    return multiplier


def compute_binomial_confidence_intervals(
    win_counts: np.ndarray,
    total_sims: int,
    confidence_level: float = 0.95
) -> Tuple[np.ndarray, np.ndarray]:
    """Wilson score confidence intervals for per-runner win probabilities."""
    if total_sims <= 0:
        n = len(win_counts)
        return np.zeros(n, dtype=np.float64), np.zeros(n, dtype=np.float64)

    z = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}.get(confidence_level, 1.96)
    z2 = z * z
    n_float = float(total_sims)

    p = win_counts.astype(np.float64) / n_float
    denom = 1.0 + z2 / n_float
    centre = (p + z2 / (2.0 * n_float)) / denom
    margin = (z / denom) * np.sqrt((p * (1.0 - p) / n_float) + (z2 / (4.0 * n_float * n_float)))
    lower = np.clip(centre - margin, 0.0, 1.0)
    upper = np.clip(centre + margin, 0.0, 1.0)
    return lower, upper


def _top_order_combos(
    ranked: np.ndarray,
    n_sims: int,
    order_size: int,
    top_k: int = 5
) -> List[Dict[str, Any]]:
    """Extract top exacta/trifecta style combinations from ranked finish matrix."""
    if ranked.size == 0 or ranked.shape[1] < order_size:
        return []

    counts: Dict[Tuple[int, ...], int] = {}
    for sim in range(n_sims):
        combo = tuple(int(x) for x in ranked[sim, -order_size:])
        counts[combo] = counts.get(combo, 0) + 1

    sorted_combos = sorted(counts.items(), key=lambda x: -x[1])[:top_k]
    out: List[Dict[str, Any]] = []
    for combo, count in sorted_combos:
        if order_size == 2:
            second, first = combo
            out.append({'first': first, 'second': second, 'prob': count / n_sims})
        elif order_size == 3:
            third, second, first = combo
            out.append({'first': first, 'second': second, 'third': third, 'prob': count / n_sims})
    return out


def simulate_race_realistic(
    runners: List[Dict],
    win_probs: np.ndarray,
    n_sims: int = None,
    seed: int = None,
    elite_jockeys: List[str] = None,
    race_data: Dict = None
) -> Dict[str, Any]:
    """
    Realistic Monte Carlo race simulation.

    Uses:
    - Latent strength scores from log(win_prob)
    - Variable sigma per horse based on consistency
    - Mixture noise (70/30 normal/downside) with going-specific skew
    - Multi-phase energy dynamics (see simulate_multi_phase)
    - Pace collapse events (stochastic fatigue failures)
    """
    config = SIMULATION_CONFIG
    n_sims = n_sims or config['default_sims']
    
    rng = np.random.default_rng(seed)
    
    n = len(runners)
    if n == 0:
        return {'error': 'No runners', 'sim_win_probs': np.array([])}
    
    probs = np.array(win_probs, dtype=np.float64)
    probs = np.maximum(probs, 1e-9)
    probs = probs / probs.sum()
    
    if elite_jockeys is None:
        elite_jockeys = [
            'mcdonald', 'bowman', 'mcevoy', 'zahra', 'shinn', 'avdulla',
            'rawiller', 'purton', 'moreira', 'lane', 'melham', 'oliver',
            'collett', 'dee', 'walker', 'currie'
        ]
    
    sigmas = np.zeros(n)
    for i, runner in enumerate(runners):
        jockey = str(runner.get('jockey', '')).lower()
        is_elite = any(ej in jockey for ej in elite_jockeys)
        sigmas[i] = calculate_runner_sigma(
            runner, 
            probs[i],
            field_size=n,
            is_elite_jockey=is_elite
        )

    going_profile = {}
    if race_data:
        going_profile = get_going_uncertainty_profile(
            race_data.get('going', race_data.get('track_condition', ''))
        )

    if race_data:
        race_type_multiplier = get_race_type_sigma_multiplier(race_data)
        sigmas *= race_type_multiplier
        sigmas = np.clip(sigmas, config['min_sigma'], config['max_sigma'] * 1.5)
    
    mu = np.log(probs + 1e-9)
    
    for i, runner in enumerate(runners):
        fatigue = runner.get('fatigue_factor', 1.0)
        if fatigue < 1.0:
            mu[i] += np.log(max(0.5, fatigue))
        
        collapse_prob = runner.get('collapse_probability', 0)
        if collapse_prob > 0.1:
            mu[i] -= collapse_prob * 0.3
        
        fitness = runner.get('fitness_factor', 1.0)
        if fitness < 1.0:
            mu[i] += np.log(max(0.7, fitness))
    
    try:
        from track_condition_db import TrackConditionDatabase
        going = ''
        if race_data:
            going = str(race_data.get('going', '') or '').strip()
        if going:
            tc_db = TrackConditionDatabase()
            horse_names = [str(r.get('horse', r.get('name', ''))) for r in runners]
            going_adjs = tc_db.batch_get_going_adjustments(horse_names, going)
            for i, name in enumerate(horse_names):
                adj = going_adjs.get(name, 1.0)
                if adj != 1.0:
                    mu[i] += np.log(adj)
    except Exception:
        pass

    noise = generate_mixture_noise(n_sims, n, sigmas, rng, going_profile=going_profile)

    scores = mu + noise
    
    for i, runner in enumerate(runners):
        cp = runner.get('collapse_probability', 0)
        if cp > 0.05:
            collapse_mask = rng.random(n_sims) < cp
            scores[collapse_mask, i] -= rng.exponential(1.5, size=collapse_mask.sum())
    
    winners = np.argmax(scores, axis=1)
    
    sim_win_counts = np.bincount(winners, minlength=n)
    sim_win_probs = sim_win_counts / n_sims
    
    ranked = np.argsort(scores, axis=1)

    top3_counts = np.zeros(n, dtype=np.int64)
    top3 = ranked[:, -3:] if n >= 3 else ranked
    for col in range(top3.shape[1]):
        np.add.at(top3_counts, top3[:, col], 1)
    sim_place_probs = top3_counts / n_sims

    sim_exactas = _top_order_combos(ranked, n_sims, order_size=2, top_k=5) if n >= 2 else []
    sim_trifectas = _top_order_combos(ranked, n_sims, order_size=3, top_k=5) if n >= 3 else []

    win_ci_lower, win_ci_upper = compute_binomial_confidence_intervals(sim_win_counts, n_sims)
    fair_odds = np.where(sim_win_probs > 0, 1.0 / sim_win_probs, np.inf)
    
    top_prob = np.max(sim_win_probs)
    second_prob = np.partition(sim_win_probs, -2)[-2] if n >= 2 else 0
    confidence = min(1.0, (top_prob - second_prob) * 2 + top_prob)
    
    is_confident = top_prob >= config['confidence_threshold']
    
    return {
        'sim_win_probs': sim_win_probs,
        'sim_place_probs': sim_place_probs,
        'sim_exactas': sim_exactas,
        'sim_trifectas': sim_trifectas,
        'win_ci_lower': win_ci_lower,
        'win_ci_upper': win_ci_upper,
        'fair_odds': fair_odds,
        'confidence': round(confidence, 3),
        'is_confident': is_confident,
        'top_pick_prob': round(top_prob, 4),
        'sigmas_used': sigmas.tolist(),
        'n_sims': n_sims,
        'going_category': going_profile.get('going_category', classify_going('')),
    }


def simulate_race_with_runners(
    runners: List[Dict],
    base_probs: Optional[np.ndarray] = None,
    n_sims: int = 20000,
    seed: int = None
) -> List[Dict]:
    """
    Full simulation that returns results merged with runner data.

    If base_probs not provided, derives from market odds.

    Returns: List of dicts with runner info + simulation results
    """
    n = len(runners)
    if n == 0:
        return []
    
    if base_probs is None:
        probs = []
        for r in runners:
            odds = r.get('market_odds') or r.get('sp') or r.get('odds') or 5.0
            try:
                odds = float(str(odds).replace('$', '').strip())
                if odds < 1.01:
                    odds = 5.0
            except (ValueError, TypeError):
                odds = 5.0
            probs.append(1.0 / odds)
        base_probs = np.array(probs)
    
    sim_results = simulate_race_realistic(runners, base_probs, n_sims, seed)
    
    if 'error' in sim_results:
        return [{'error': sim_results['error']}]
    
    results = []
    for i, runner in enumerate(runners):
        market_odds = runner.get('market_odds')
        try:
            market_odds = float(market_odds) if market_odds is not None else None
        except (TypeError, ValueError):
            market_odds = None

        result = {
            'horse': runner.get('horse', runner.get('name', f'Runner {i+1}')),
            'number': runner.get('number', i + 1),
            'barrier': runner.get('barrier', 0),
            'jockey': runner.get('jockey', ''),
            'trainer': runner.get('trainer', ''),
            'form': runner.get('form', ''),
            'market_odds': market_odds,
            'base_prob': float(base_probs[i]),
            'sim_win_prob': float(sim_results['sim_win_probs'][i]),
            'sim_place_prob': float(sim_results['sim_place_probs'][i]),
            'sigma_used': float(sim_results['sigmas_used'][i]),
            'fair_odds': float(sim_results['fair_odds'][i]) if np.isfinite(sim_results['fair_odds'][i]) else None,
            'win_ci_lower': float(sim_results['win_ci_lower'][i]),
            'win_ci_upper': float(sim_results['win_ci_upper'][i]),
            'consistency': calculate_form_consistency(runner.get('form', '')),
        }
        
        if result['market_odds'] and result['market_odds'] > 1:
            result['ev'] = (result['sim_win_prob'] * result['market_odds']) - 1
            result['value_edge_pct'] = (result['sim_win_prob'] * 100.0) - (100.0 / result['market_odds'])
        else:
            result['ev'] = None
            result['value_edge_pct'] = None
        
        results.append(result)
    
    results.sort(key=lambda x: -x['sim_win_prob'])
    
    for i, result in enumerate(results):
        result['rank'] = i + 1
        result['is_top_pick'] = i == 0
        result['is_confident_selection'] = i == 0 and sim_results['is_confident']
    
    return results


def filter_confident_selections(
    race_results: List[Dict],
    min_win_prob: float = None,
    min_ev: float = 0.0
) -> List[Dict]:
    """
    Filter race results to only confident selections.

    Args:
        race_results: Output from simulate_race_with_runners
        min_win_prob: Minimum simulated win probability (default from config)
        min_ev: Minimum expected value (default 0 = positive EV)

    Returns: Filtered list of selections meeting criteria
    """
    if min_win_prob is None:
        min_win_prob = SIMULATION_CONFIG['confidence_threshold']
    
    confident = []
    for result in race_results:
        if result.get('sim_win_prob', 0) >= min_win_prob:
            ev = result.get('ev')
            if ev is not None and ev >= min_ev:
                confident.append(result)
    
    return confident


def get_top_pick_with_confidence(race_results: List[Dict]) -> Optional[Dict]:
    """
    Get the top pick only if it meets confidence threshold.

    Returns: Top pick dict or None if no confident selection
    """
    if not race_results:
        return None
    
    top = race_results[0]
    if top.get('is_confident_selection'):
        return top
    
    return None



def simulate_race_with_sectional_profiles(
    runners: List[Dict],
    win_probs: np.ndarray,
    pace_profiles: List[Dict],
    running_profiles: List[Dict],
    race_distance_m: int = 1400,
    n_sims: int = None,
    seed: int = None,
    elite_jockeys: List[str] = None,
    speed_threshold_pct: float = None,
    use_strict_speed_class: bool = False,
    race_data: Dict = None,
    quant_features: Dict = None,
) -> Dict[str, Any]:
    """
    Enhanced Monte Carlo simulation with sectional-profile-based pace scenarios and 5-engine quantitative adjustment system.

    Quant engines applied:
      Engine 1 (SASR): mu shift from Rowlands/Ward-Smith sectional rating upgrade/downgrade
      Engine 2 (Pace Collapse): stochastic per-sim penalty when field has high speed density
      Engine 3 (Race Shape): RSI-based continuous correction
      Engine 4 (Closing Rank): closing speed percentile modulated by SVI
      Engine 5 (Trip Efficiency): unlucky loser mu boost from wide-draw barrier cost
    """
    config = SIMULATION_CONFIG
    sec_cfg = SECTIONAL_CONFIG
    n_sims = n_sims or config['default_sims']

    rng = np.random.default_rng(seed)

    n = len(runners)
    if n == 0:
        return {'error': 'No runners', 'sim_win_probs': np.array([])}

    has_any_profile = any(pp.get('has_profile', False) for pp in pace_profiles)
    if not has_any_profile:
        base_result = simulate_race_realistic(runners, win_probs, n_sims, seed, elite_jockeys)
        base_result['pace_scenario_distribution'] = {'hot': 0.0, 'genuine': 0.0, 'soft': 0.0, 'slow': 0.0}
        base_result['sectional_enhanced'] = False
        return base_result

    probs = np.array(win_probs, dtype=np.float64)
    probs = np.maximum(probs, 1e-9)
    probs = probs / probs.sum()

    if elite_jockeys is None:
        elite_jockeys = [
            'mcdonald', 'bowman', 'mcevoy', 'zahra', 'shinn', 'avdulla',
            'rawiller', 'purton', 'moreira', 'lane', 'melham', 'oliver',
            'collett', 'dee', 'walker', 'currie'
        ]

    sigmas = np.zeros(n)
    for i, runner in enumerate(runners):
        jockey = str(runner.get('jockey', '')).lower()
        is_elite = any(ej in jockey for ej in elite_jockeys)
        sigmas[i] = calculate_runner_sigma(runner, probs[i], field_size=n, is_elite_jockey=is_elite)

    going_profile = {}
    if race_data:
        going_profile = get_going_uncertainty_profile(
            race_data.get('going', race_data.get('track_condition', ''))
        )

    if race_data:
        race_type_multiplier = get_race_type_sigma_multiplier(race_data)
        sigmas *= race_type_multiplier
        sigmas = np.clip(sigmas, config['min_sigma'], config['max_sigma'] * 1.5)

    mu = np.log(probs + 1e-9)

    for i, runner in enumerate(runners):
        fatigue = runner.get('fatigue_factor', 1.0)
        if fatigue < 1.0:
            mu[i] += np.log(max(0.5, fatigue))
        collapse_prob = runner.get('collapse_probability', 0)
        if collapse_prob > 0.1:
            mu[i] -= collapse_prob * 0.3
        fitness = runner.get('fitness_factor', 1.0)
        if fitness < 1.0:
            mu[i] += np.log(max(0.7, fitness))

    try:
        from track_condition_db import TrackConditionDatabase
        going = ''
        if race_data:
            going = str(race_data.get('going', '') or '').strip()
        if going:
            tc_db = TrackConditionDatabase()
            horse_names = [str(r.get('horse', r.get('name', ''))) for r in runners]
            going_adjs = tc_db.batch_get_going_adjustments(horse_names, going)
            for i, name in enumerate(horse_names):
                adj = going_adjs.get(name, 1.0)
                if adj != 1.0:
                    mu[i] += np.log(adj)
    except Exception:
        pass

    # Jockey tactical mu bonus (position-dependent, replaces blunt sigma)
    # Elite jockeys get a mu boost based on the horse's running style.
    # Non-elite jockeys get a smaller category-based fallback adjustment.
    for i, runner in enumerate(runners):
        jockey = str(runner.get('jockey', '')).lower()
        style = running_profiles[i].get('running_style_class', 'midfield') if i < len(running_profiles) else 'midfield'
        matched = False
        for jockey_key, profile in JOCKEY_TACTICAL_PROFILES.items():
            if jockey_key in jockey:
                mu[i] += profile.get(style, 0.0)
                matched = True
                break
        if not matched:
            category = _classify_jockey_category(jockey, runner)
            cat_profile = JOCKEY_CATEGORY_PROFILES.get(category, JOCKEY_CATEGORY_PROFILES['unknown'])
            mu[i] += cat_profile.get(style, 0.0)
            # Apprentice sigma expansion: higher variance from inconsistent race-riding
            if category == 'apprentice':
                sigmas[i] *= 1.08

    # Tight-turn track geometry: leaders hold advantage through tight bends
    track_name = ''
    if race_data:
        track_name = (race_data.get('track', '') or race_data.get('course', '') or '').lower()
    tight_turn_profile = None
    for tk, tp in TIGHT_TURN_TRACKS.items():
        if tk in track_name:
            tight_turn_profile = tp
            break
    if tight_turn_profile is not None:
        for i in range(n):
            style = running_profiles[i].get('running_style_class', 'midfield') if i < len(running_profiles) else 'midfield'
            if style == 'leader':
                mu[i] += tight_turn_profile['leader_boost']
            elif style == 'on_pace':
                mu[i] += tight_turn_profile['on_pace_boost']
            elif style == 'backmarker':
                mu[i] -= tight_turn_profile['backmarker_penalty']

    # QUANT ENGINE MU ADJUSTMENTS (Engines 1, 3, 5 - applied to mu)
    _quant_applied = False
    _quant_summary = {}
    _per_horse_quant = quant_features.get('per_horse', {}) if quant_features else {}
    _field_quant = quant_features.get('field_level', {}) if quant_features else {}

    if _per_horse_quant:
        _quant_applied = True
        total_quant_shift = np.zeros(n)

        for i, runner in enumerate(runners):
            h_name = runner.get('horse', '')
            hq = _per_horse_quant.get(h_name, {})

            sasr_shift = 0.0
            shape_shift = 0.0
            unlucky_shift = 0.0

            sasr_data = hq.get('sasr', {})
            if sasr_data.get('sasr_mu_shift', 0) != 0:
                sasr_shift = sasr_data['sasr_mu_shift']

            shape_data = hq.get('shape_fit', {})
            if shape_data.get('shape_mu_shift', 0) != 0:
                shape_shift = shape_data['shape_mu_shift']

            trip_data = hq.get('trip_efficiency', {})
            if trip_data.get('unlucky_mu_boost', 0) > 0:
                unlucky_shift = trip_data['unlucky_mu_boost']

            combined = sasr_shift + shape_shift + unlucky_shift
            combined = max(-0.25, min(0.25, combined))
            total_quant_shift[i] = combined

            if abs(combined) > 0.001:
                _quant_summary[h_name] = {
                    'sasr': round(sasr_shift, 4),
                    'shape': round(shape_shift, 4),
                    'unlucky': round(unlucky_shift, 4),
                    'total': round(combined, 4),
                }

        mu += total_quant_shift

    # Step 1: Build vectorised sectional parameter arrays
    # (Moved before multi-phase so pace scenario feeds into energy model)
    early_medians = np.zeros(n)
    early_stds = np.zeros(n)
    home_medians = np.zeros(n)
    home_stds = np.zeros(n)
    has_profile_mask = np.zeros(n, dtype=bool)

    for i, pp in enumerate(pace_profiles):
        if pp.get('has_profile', False):
            has_profile_mask[i] = True
            early_medians[i] = pp.get('early_median', sec_cfg['default_early_median'])
            early_stds[i] = max(pp.get('early_std', sec_cfg['default_sectional_std']), 0.1)
            home_medians[i] = pp.get('home_median', sec_cfg['default_home_median'])
            home_stds[i] = max(pp.get('home_std', sec_cfg['default_sectional_std']), 0.1)
        else:
            early_medians[i] = sec_cfg['default_early_median']
            early_stds[i] = sec_cfg['default_sectional_std']
            home_medians[i] = sec_cfg['default_home_median']
            home_stds[i] = sec_cfg['default_sectional_std']

    sampled_early = rng.normal(early_medians, early_stds, size=(n_sims, n))
    sampled_home = rng.normal(home_medians, home_stds, size=(n_sims, n))

    running_styles = np.array([
        rp.get('running_style_class', 'midfield') for rp in running_profiles
    ])

    speed_style_mask = np.isin(running_styles, ['leader', 'on_pace'])

    threshold_pct = speed_threshold_pct if speed_threshold_pct is not None else sec_cfg['early_speed_threshold_pct']
    overall_avg_early = early_medians.copy()
    threshold = overall_avg_early * threshold_pct

    if use_strict_speed_class:
        is_speed_horse = (sampled_early > threshold)
    else:
        is_speed_horse = (sampled_early > threshold) | speed_style_mask
    speed_count = np.sum(is_speed_horse, axis=1)

    PACE_HOT = 0
    PACE_GENUINE = 1
    PACE_SOFT = 2
    PACE_SLOW = 3

    pace_scenario = np.full(n_sims, PACE_SLOW, dtype=np.int8)
    pace_scenario = np.where(speed_count >= sec_cfg['soft_pace_min_speed_count'], PACE_SOFT, pace_scenario)
    pace_scenario = np.where(speed_count >= sec_cfg['genuine_pace_min_speed_count'], PACE_GENUINE, pace_scenario)
    pace_scenario = np.where(speed_count >= sec_cfg['hot_pace_min_speed_count'], PACE_HOT, pace_scenario)

    # Multi-phase simulation (energy dynamics + style advantages)
    # Replaces single-stage noise = generate_mixture_noise(); scores = mu + noise
    # with 4-phase simulation where each phase has independent noise,
    # running-style advantages, and energy depletion modulated by pace.
    _running_style_list = [
        rp.get('running_style_class', 'midfield') for rp in running_profiles
    ]

    scores = simulate_multi_phase(
        mu, sigmas, _running_style_list, race_distance_m,
        n_sims, n, rng,
        pace_scenario_arr=pace_scenario
    )

    # Add asymmetric residual shock layer so sectional MC keeps realistic
    # downside skew + fat-tail surprises after phase dynamics.
    residual_sigmas = sigmas * 0.35
    # Determine dominant pace label for style-dependent mixture noise
    _pace_names_map = {0: 'hot', 1: 'genuine', 2: 'soft', 3: 'slow'}
    _dominant_pace_idx = int(np.bincount(pace_scenario.astype(int)).argmax())
    _dominant_pace_label = _pace_names_map.get(_dominant_pace_idx, 'genuine')
    scores += generate_mixture_noise(
        n_sims, n, residual_sigmas, rng, going_profile=going_profile,
        running_styles=list(running_styles),
        pace_scenario_label=_dominant_pace_label
    )

    # Stochastic collapse penalty (per-horse)
    for i, runner in enumerate(runners):
        cp = runner.get('collapse_probability', 0)
        if cp > 0.05:
            collapse_mask = rng.random(n_sims) < cp
            scores[collapse_mask, i] -= rng.exponential(1.5, size=collapse_mask.sum())

    # QUANT ENGINE 2: Pace Collapse - stochastic per-sim adjustment
    _collapse_data = _field_quant.get('pace_collapse', {})
    _collapse_prob = _collapse_data.get('collapse_probability', 0)
    _per_horse_collapse = _collapse_data.get('per_horse_collapse_adj', {})

    if _collapse_prob > 0.05 and _per_horse_collapse:
        _quant_applied = True
        collapse_occurs = rng.random(n_sims) < _collapse_prob

        n_collapse_sims = int(np.sum(collapse_occurs))
        if n_collapse_sims > 0:
            for i, runner in enumerate(runners):
                h_name = runner.get('horse', '')
                adj = _per_horse_collapse.get(h_name, 0)
                if abs(adj) > 0.001:
                    if adj < 0:
                        penalty = rng.exponential(abs(adj), size=n_collapse_sims)
                        scores[collapse_occurs, i] -= penalty
                    else:
                        scores[collapse_occurs, i] += adj

    pace_names = ['hot', 'genuine', 'soft', 'slow']
    pace_dist = {}
    for idx, name in enumerate(pace_names):
        pace_dist[name] = float(np.sum(pace_scenario == idx)) / n_sims

    # Step 3: Apply pace-preference adjustments (vectorised)
    # Weight reduced 50% — multi-phase simulation already models per-phase
    # style advantages and energy depletion. This retains the empirical
    # categorical pace-scenario calibration at half strength.
    style_to_key = {
        'leader': 'leader',
        'on_pace': 'on_pace',
        'handy': 'handy',
        'midfield': 'midfield',
        'backmarker': 'backmarker',
    }
    pace_adj_table = sec_cfg['pace_adjustments']

    adj_matrix = np.zeros((4, n), dtype=np.float64)
    for i in range(n):
        style_key = style_to_key.get(running_styles[i], 'midfield')
        style_adjs = pace_adj_table[style_key]
        for pace_idx, pace_name in enumerate(pace_names):
            adj_matrix[pace_idx, i] = style_adjs[pace_name]

    pace_adjustments = adj_matrix[pace_scenario]

    scores += pace_adjustments * 0.5

    # Phase 2: Continuous RSI-based correction (Beyer/Quirin Race Shape Index)
    # Each horse's last-run RSI tells us what pace shape they ran in historically.
    # This layers on top of the categorical system (both signals are valid).
    rsi_adjustments = np.zeros(n, dtype=np.float64)
    for i, runner in enumerate(runners):
        horse_rsi = runner.get('rsi')
        if horse_rsi is not None:
            style_key = style_to_key.get(str(running_styles[i]) if i < len(running_styles) else '', 'midfield')
            rsi_adjustments[i] = _rsi_to_adj(float(horse_rsi), style_key)
    scores += rsi_adjustments

    # Step 4: Sectional-enhanced finishing strength + Engine 4 (Closing Rank)
    sectional_adj = np.zeros((n_sims, n), dtype=np.float64)
    if np.any(has_profile_mask):
        base_home_weight = sec_cfg['home_speed_weight']

        closing_multipliers = np.ones(n, dtype=np.float64)
        if _per_horse_quant:
            for i, runner in enumerate(runners):
                h_name = runner.get('horse', '')
                hq = _per_horse_quant.get(h_name, {})
                cr_data = hq.get('closing_rank', {})
                cr_mult = cr_data.get('closing_rank_multiplier', 1.0)
                closing_multipliers[i] = cr_mult

        per_horse_home_weight = base_home_weight * closing_multipliers

        sectional_adj[:, has_profile_mask] = (
            sampled_home[:, has_profile_mask] - 100.0
        ) * per_horse_home_weight[has_profile_mask]

    scores += sectional_adj

    # Determine winners & compute output metrics (same as simulate_race_realistic)
    winners = np.argmax(scores, axis=1)

    sim_win_counts = np.bincount(winners, minlength=n)
    sim_win_probs = sim_win_counts / n_sims

    top3_counts = np.zeros(n, dtype=np.int64)
    ranked = np.argsort(scores, axis=1)
    top3 = ranked[:, -3:] if n >= 3 else ranked
    for col in range(top3.shape[1]):
        np.add.at(top3_counts, top3[:, col], 1)
    sim_place_probs = top3_counts / n_sims

    sim_exactas = _top_order_combos(ranked, n_sims, order_size=2, top_k=5) if n >= 2 else []
    sim_trifectas = _top_order_combos(ranked, n_sims, order_size=3, top_k=5) if n >= 3 else []
    win_ci_lower, win_ci_upper = compute_binomial_confidence_intervals(sim_win_counts, n_sims)
    fair_odds = np.where(sim_win_probs > 0, 1.0 / sim_win_probs, np.inf)

    top_prob = float(np.max(sim_win_probs))
    second_prob = float(np.partition(sim_win_probs, -2)[-2]) if n >= 2 else 0.0
    confidence = min(1.0, (top_prob - second_prob) * 2 + top_prob)
    is_confident = top_prob >= config['confidence_threshold']

    return {
        'sim_win_probs': sim_win_probs,
        'sim_place_probs': sim_place_probs,
        'sim_exactas': sim_exactas,
        'sim_trifectas': sim_trifectas,
        'win_ci_lower': win_ci_lower,
        'win_ci_upper': win_ci_upper,
        'fair_odds': fair_odds,
        'confidence': round(confidence, 3),
        'is_confident': is_confident,
        'top_pick_prob': round(top_prob, 4),
        'sigmas_used': sigmas.tolist(),
        'n_sims': n_sims,
        'pace_scenario_distribution': pace_dist,
        'going_category': going_profile.get('going_category', classify_going('')),
        'sectional_enhanced': True,
        'quant_applied': _quant_applied,
        'quant_summary': _quant_summary if _quant_applied else {},
        'collapse_probability': _collapse_prob,
    }



def convert_mc_results_to_realistic(
    mc_results: List[Dict],
    runners: List[Dict],
    race_data: Dict
) -> List[Dict]:
    """
    Take existing MC simulation results and enhance with realistic simulation.

    This allows gradual migration: run existing MC, then overlay realistic simulation on top for comparison or replacement.
    """
    n = len(mc_results)
    if n == 0:
        return []
    
    base_probs = np.array([
        r.get('win_prob_sim', 0.1) for r in mc_results
    ])
    
    enhanced_runners = []
    for i, mc in enumerate(mc_results):
        runner = runners[i] if i < len(runners) else {}
        enhanced_runners.append({
            'horse': mc.get('horse', runner.get('horse', f'Runner {i}')),
            'form': mc.get('form', runner.get('form', '')),
            'market_odds': mc.get('sp') or mc.get('market_odds') or runner.get('market_odds'),
            'jockey': mc.get('jockey', runner.get('jockey', '')),
            'barrier': mc.get('barrier', runner.get('barrier', 0)),
            'number': mc.get('number', i + 1),
        })
    
    results = simulate_race_with_runners(
        enhanced_runners,
        base_probs,
        n_sims=20000
    )
    
    return results



def simulate_meeting(
    races: List[Dict],
    confidence_filter: bool = True
) -> Dict[str, Any]:
    """
    Simulate all races at a meeting.

    Args:
        races: List of race dicts, each with 'runners' and race info
        confidence_filter: If True, only return confident selections
    """
    meeting_results = []
    confident_picks = []
    
    for race in races:
        runners = race.get('runners', [])
        if not runners:
            continue
        
        race_results = simulate_race_with_runners(runners)
        
        race_summary = {
            'race_number': race.get('race_number', 0),
            'race_name': race.get('race_name', ''),
            'track': race.get('track', ''),
            'results': race_results,
            'top_pick': race_results[0] if race_results else None,
            'is_confident': race_results[0].get('is_confident_selection', False) if race_results else False
        }
        
        meeting_results.append(race_summary)
        
        if confidence_filter:
            top = get_top_pick_with_confidence(race_results)
            if top:
                top['race_number'] = race.get('race_number', 0)
                top['track'] = race.get('track', '')
                confident_picks.append(top)
    
    return {
        'races': meeting_results,
        'confident_picks': confident_picks,
        'total_races': len(meeting_results),
        'confident_count': len(confident_picks),
        'confidence_rate': len(confident_picks) / max(len(meeting_results), 1)
    }



if __name__ == '__main__':
    test_runners = [
        {'horse': 'Favorite', 'form': '1121', 'market_odds': 2.5, 'jockey': 'J McDonald'},
        {'horse': 'Second Choice', 'form': '2312', 'market_odds': 4.0, 'jockey': 'H Bowman'},
        {'horse': 'Outsider', 'form': '5463', 'market_odds': 12.0, 'jockey': 'A Nobody'},
        {'horse': 'Longshot', 'form': '8795', 'market_odds': 25.0, 'jockey': 'B Smith'},
    ]
    
    results = simulate_race_with_runners(test_runners, seed=42)
    
    print("Realistic Monte Carlo Simulation Results")
    print("=" * 60)
    for r in results:
        print(f"{r['rank']}. {r['horse']}")
        print(f"   Market: ${r['market_odds']:.2f} | Sim Win: {r['sim_win_prob']*100:.1f}%")
        print(f"   Form Consistency: {r['consistency']:.2f} | Sigma: {r['sigma_used']:.2f}")
        print(f"   EV: {r['ev']*100:.1f}%" if r['ev'] else "   EV: N/A")
        print(f"   Confident Selection: {'YES' if r.get('is_confident_selection') else 'no'}")
        print()

