#!/usr/bin/env python3
"""
Lightweight JSON API wrapper for the racing_system_v8.3_mc.py ML system.
Called by Express server via child_process.

Input: JSON on stdin with race/runner data
Output: JSON to stdout with ranked runners and MC probabilities
"""

import sys
import json
import os
import re
import time
import importlib.util
import numpy as np
from contextlib import redirect_stdout
from io import StringIO


def _normalized_sql_text(expr: str) -> str:
    return f"regexp_replace(lower(coalesce({expr}, '')), '[^a-z0-9]+', '', 'g')"


def _sectional_track_match_sql(st_expr: str, rrh_expr: str) -> str:
    left = _normalized_sql_text(st_expr)
    right = _normalized_sql_text(rrh_expr)
    return f"({left} = {right} OR {left} LIKE '%%' || {right} || '%%' OR {right} LIKE '%%' || {left} || '%%')"


def _sectional_horse_match_sql(st_expr: str, rrh_expr: str) -> str:
    return f"{_normalized_sql_text(st_expr)} = {_normalized_sql_text(rrh_expr)}"


def _sectional_natural_key_sql(st_expr: str = "st", rrh_expr: str = "rrh") -> str:
    return " AND ".join([
        f"{st_expr}.race_date::date = {rrh_expr}.race_date::date",
        f"{st_expr}.race_number = {rrh_expr}.race_number",
        _sectional_track_match_sql(f"{st_expr}.track", f"{rrh_expr}.track"),
        _sectional_horse_match_sql(f"{st_expr}.horse_name", f"{rrh_expr}.horse_name"),
    ])


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, PROJECT_ROOT)

MC_ENABLE_SECTIONAL_FRANKING = _env_flag("MC_ENABLE_SECTIONAL_FRANKING", True)
MC_ENABLE_JOCKEY_EFFICIENCY = _env_flag("MC_ENABLE_JOCKEY_EFFICIENCY", True)

try:
    from ml_model import get_model, RacingMLModel
    ML_MODEL_AVAILABLE = True
except ImportError:
    ML_MODEL_AVAILABLE = False

# Task 03: single shared serve-time feature builder + movement-column gate.
# serve_features/nan_contract are stdlib-only, but keep the guarded-import
# convention so mc_api still loads if the file layout changes.
try:
    from serve_features import (
        MOVEMENT_FEATURES as _SHARED_MOVEMENT_FEATURES,
        movement_features_live as _movement_features_live,
        overlay_shared_columns as _overlay_shared_columns,
        log_movement_inert_once as _log_movement_inert_once,
    )
    SERVE_FEATURES_AVAILABLE = True
except ImportError:
    SERVE_FEATURES_AVAILABLE = False
    _SHARED_MOVEMENT_FEATURES = ()
    def _movement_features_live():
        return os.environ.get("STRIDE_MOVEMENT_FEATURES_LIVE", "false").strip().lower() in ("true", "1", "yes")

try:
    from advanced_features import (
        extract_advanced_features as extract_db_features,
        get_collateral_analyzer,
        get_hot_streak_analyzer,
        parse_class_level,
        analyze_form_patterns,
        analyze_distance_suitability,
        analyze_going_suitability,
        parse_distance,
        categorize_distance,
        calculate_weighted_form_score
    )
    ADVANCED_FEATURES_AVAILABLE = True
except ImportError:
    ADVANCED_FEATURES_AVAILABLE = False

try:
    from train_ml_enhanced import EnhancedRacingMLModel
    ENHANCED_ML_AVAILABLE = True
except ImportError:
    ENHANCED_ML_AVAILABLE = False

try:
    from market_analysis import (
        calculate_steam_drift,
        detect_sharp_money,
        analyze_field_market_movements,
        get_market_adjustment,
        extract_market_features
    )
    MARKET_ANALYSIS_AVAILABLE = True
except ImportError:
    MARKET_ANALYSIS_AVAILABLE = False

try:
    from speed_ratings import (
        extract_speed_features,
        estimate_speed_rating_from_form
    )
    SPEED_RATINGS_AVAILABLE = True
except ImportError:
    SPEED_RATINGS_AVAILABLE = False

try:
    from pace_modeling import (
        classify_running_style,
        predict_race_tempo,
        calculate_pace_advantage,
        extract_pace_features,
        analyze_pace_map
    )
    PACE_MODELING_AVAILABLE = True
except ImportError:
    PACE_MODELING_AVAILABLE = False

try:
    from speed_mapping import (
        generate_speed_map,
        extract_pace_features_for_mc,
        classify_running_style_enhanced,
        predict_settling_position,
        predict_pace_scenario,
        calculate_pace_advantage_score
    )
    SPEED_MAPPING_AVAILABLE = True
except ImportError:
    SPEED_MAPPING_AVAILABLE = False

try:
    from enhanced_features import (
        extract_enhanced_features,
        calculate_class_movement,
        get_barrier_bias,
        calculate_h2h_adjustment
    )
    ENHANCED_FEATURES_AVAILABLE = True
except ImportError:
    ENHANCED_FEATURES_AVAILABLE = False

try:
    from track_bias_points import TrackBiasScorer
    TRACK_BIAS_AVAILABLE = True
    track_bias_scorer = TrackBiasScorer()
except ImportError:
    TRACK_BIAS_AVAILABLE = False

try:
    from fitness_peak import get_fitness_peak_data
    FITNESS_PEAK_AVAILABLE = True
except ImportError:
    FITNESS_PEAK_AVAILABLE = False

try:
    from sectional_quant import compute_all_quant_features
    SECTIONAL_QUANT_AVAILABLE = True
    print("[MC] Sectional quant engines loaded (SASR/Collapse/Shape/Closing/Trip)", file=sys.stderr)
except ImportError as _sq_err:
    SECTIONAL_QUANT_AVAILABLE = False
    print(f"[MC] Sectional quant engines not available: {_sq_err}", file=sys.stderr)

try:
    from realistic_simulate import (
        simulate_race_realistic,
        simulate_race_with_runners,
        calculate_form_consistency,
        simulate_race_with_sectional_profiles,
        SIMULATION_CONFIG
    )
    REALISTIC_SIM_AVAILABLE = True
    SECTIONAL_MC_AVAILABLE = True
    from mc_recalibration import MCRecalibrator
    _mc_recalibrator = MCRecalibrator()
    if _mc_recalibrator.load():
        MC_RECALIBRATION_AVAILABLE = True
        print("[MC] Recalibration model loaded", file=sys.stderr)
    else:
        MC_RECALIBRATION_AVAILABLE = False
        _mc_recalibrator = None
        print("[MC] No recalibration model found", file=sys.stderr)
except ImportError:
    REALISTIC_SIM_AVAILABLE = False
    SECTIONAL_MC_AVAILABLE = False
    MC_RECALIBRATION_AVAILABLE = False
    _mc_recalibrator = None
    SIMULATION_CONFIG = {'confidence_threshold': 0.18}

try:
    from normalize import (
        normalize_runner,
        normalize_race,
        parse_form_string
    )
    NORMALIZE_AVAILABLE = True
except ImportError:
    NORMALIZE_AVAILABLE = False

try:
    from weight_optimizer import get_optimized_weights, get_barrier_analyzer
    WEIGHT_OPTIMIZER_AVAILABLE = True
    _optimized_weights = get_optimized_weights()
    _barrier_analyzer = get_barrier_analyzer()
except ImportError:
    WEIGHT_OPTIMIZER_AVAILABLE = False
    _optimized_weights = None
    _barrier_analyzer = None

try:
    from jockey_momentum import score_jockey_momentum, score_trainer_momentum, get_elite_jockeys_dynamic
    JOCKEY_MOMENTUM_AVAILABLE = True
except ImportError:
    JOCKEY_MOMENTUM_AVAILABLE = False

try:
    from track_condition_db import TrackConditionDatabase, PaceEnergyModel
    TRACK_CONDITION_AVAILABLE = True
    _track_condition_db = TrackConditionDatabase()
    _pace_energy_model = PaceEnergyModel()
except ImportError:
    TRACK_CONDITION_AVAILABLE = False
    _track_condition_db = None
    _pace_energy_model = None

try:
    from form_franking import get_franking_for_runners
    FRANKING_AVAILABLE = True
except ImportError:
    FRANKING_AVAILABLE = False

try:
    from franking_graph import get_franking_graph
    FRANKING_GRAPH_AVAILABLE = True
    print("[MC] Advanced franking graph engine loaded", file=sys.stderr)
except ImportError:
    FRANKING_GRAPH_AVAILABLE = False

try:
    from empirical_barriers import get_empirical_barrier_advantage, build_barrier_lookup
    EMPIRICAL_BARRIERS_AVAILABLE = True
except ImportError:
    EMPIRICAL_BARRIERS_AVAILABLE = False

try:
    from speed_mapping import extract_enhanced_speed_map_features
    ENHANCED_SPEED_MAP_AVAILABLE = True
    print("[MC] Enhanced speed map (field-aware settling) loaded", file=sys.stderr)
except ImportError:
    ENHANCED_SPEED_MAP_AVAILABLE = False

try:
    from temporal_staleness import compute_staleness_features
    TEMPORAL_STALENESS_AVAILABLE = True
    print("[MC] Temporal staleness features loaded", file=sys.stderr)
except ImportError:
    TEMPORAL_STALENESS_AVAILABLE = False

try:
    from market_velocity import compute_market_velocity_features
    MARKET_VELOCITY_AVAILABLE = True
    print("[MC] Market velocity features loaded", file=sys.stderr)
except ImportError:
    MARKET_VELOCITY_AVAILABLE = False

try:
    from feature_drift_monitor import FeatureDriftMonitor
    DRIFT_MONITOR_AVAILABLE = True
    _drift_monitor = FeatureDriftMonitor()
    print("[MC] Feature drift monitor loaded", file=sys.stderr)
except ImportError:
    DRIFT_MONITOR_AVAILABLE = False
    _drift_monitor = None

try:
    from banker_detector import get_banker_detector
    BANKER_DETECTOR_AVAILABLE = True
    print("[MC] Banker detector loaded", file=sys.stderr)
except ImportError:
    BANKER_DETECTOR_AVAILABLE = False

CONFIG = {
    'odds_min': 1.01,
    'odds_max': 200.0,
    'odds_default': 5.0,
    'prob_floor': 0.02,
    'prob_ceiling': 0.60,
    'ev_min_threshold': -0.10,
    'mid_market_low': 4.0,
    'mid_market_high': 15.0,
    'debug_mode': False,
    'confidence_threshold': 0.18,
    'use_realistic_simulation': True,
    'use_mixture_noise': True,
}


def log_debug(message):
    """Log debug messages to stderr (not stdout to avoid breaking JSON)."""
    if CONFIG['debug_mode']:
        print(f"[DEBUG] {message}", file=sys.stderr)


def is_active_runner(runner: dict) -> bool:
    """
    Canonical function to determine if a runner is a valid starter.
    Returns True only for horses that are NOT scratched/withdrawn/emergency.

    Checks multiple field names for maximum compatibility:
    - scratched (bool or string)
    - is_scratched (bool)
    - status (string: 'scratched', 'withdrawn', 'emergency')
    - withdrawn (bool)
    - emergency (bool - not a valid starter)
    - runner_status (string)
    """
    scratched = runner.get('scratched')
    if scratched is True or str(scratched).lower() == 'true':
        return False
    
    is_scratched = runner.get('is_scratched')
    if is_scratched is True or str(is_scratched).lower() == 'true':
        return False
    
    withdrawn = runner.get('withdrawn')
    if withdrawn is True or str(withdrawn).lower() == 'true':
        return False
    
    emergency = runner.get('emergency')
    if emergency is True or str(emergency).lower() == 'true':
        return False
    
    status = str(runner.get('status', '')).lower()
    if status in ('scratched', 'withdrawn', 'late_scratching', 'non-runner', 
                  'emergency', 'reserve', 'removed'):
        return False
    
    runner_status = str(runner.get('runner_status', '')).lower()
    if runner_status in ('scratched', 'withdrawn', 'late_scratching', 'non-runner'):
        return False
    
    return True


def filter_active_runners(runners: list, race_info: str = "") -> tuple:
    """
    Filter runners to only include active starters.
    Returns (active_runners, scratched_log).
    """
    active = []
    scratched = []
    
    for r in runners:
        horse_name = r.get('horse', r.get('name', 'Unknown'))
        if is_active_runner(r):
            active.append(r)
        else:
            reason = 'scratched' if r.get('scratched') else 'withdrawn'
            scratched.append(f"{horse_name} ({reason})")
    
    if scratched:
        log_debug(f"{race_info} Scratched: {', '.join(scratched)}")
    
    log_debug(f"{race_info} Runners: {len(runners)} total, {len(active)} active, {len(scratched)} scratched")
    
    return active, scratched


def parse_real_odds(runner: dict):
    """
    Parse real market odds from runner data.

    Returns None when no genuine market quote is present. This should be used
    anywhere we need to know whether a real price exists, rather than silently
    fabricating a field-average fallback.
    """
    odds = None
    
    odds_array = runner.get('odds')
    if isinstance(odds_array, list) and len(odds_array) > 0:
        first_odds = odds_array[0]
        if isinstance(first_odds, dict):
            win_odds_str = first_odds.get('win_odds', first_odds.get('odds'))
            if win_odds_str:
                try:
                    odds = float(str(win_odds_str).replace('$', '').strip())
                except (ValueError, TypeError):
                    pass
        elif isinstance(first_odds, (int, float)):
            odds = float(first_odds)
    
    if not odds:
        sp = runner.get('sp')
        if sp:
            try:
                odds = float(str(sp).replace('$', '').strip())
            except (ValueError, TypeError):
                pass
    
    if not odds:
        win_odds = runner.get('win_odds')
        if win_odds:
            try:
                odds = float(str(win_odds).replace('$', '').strip())
            except (ValueError, TypeError):
                pass
    
    if not odds:
        price = runner.get('price')
        if price:
            try:
                odds = float(str(price).replace('$', '').strip())
            except (ValueError, TypeError):
                pass

    if not odds:
        market_odds = runner.get('market_odds')
        if market_odds:
            try:
                odds = float(market_odds)
            except (ValueError, TypeError):
                pass
    
    if odds:
        if odds < CONFIG['odds_min']:
            log_debug(f"Odds {odds} below minimum {CONFIG['odds_min']}, using default")
            odds = None
        elif odds > CONFIG['odds_max']:
            log_debug(f"Odds {odds} above maximum {CONFIG['odds_max']}, marking as unavailable")
            odds = None
    
    return odds


def parse_odds(runner: dict, use_default: bool = True) -> float:
    """
    Parse odds from runner data with optional fallback.

    When `use_default` is False, missing odds resolve to 0.0 so downstream
    market-aware features can stay neutral instead of treating absent markets
    as a real $5 quote.
    """
    odds = parse_real_odds(runner)
    if odds:
        return odds
    return CONFIG['odds_default'] if use_default else 0.0

racing_system_path = os.path.join(PROJECT_ROOT, 'racing_system_v8.3_mc.py')
spec = importlib.util.spec_from_file_location("racing_system", racing_system_path)
racing_system = importlib.util.module_from_spec(spec)
spec.loader.exec_module(racing_system)


def _flag_enabled(name):
    """Env-flag convention: default OFF unless explicitly true/1/yes."""
    return os.environ.get(name, "false").strip().lower() in ("true", "1", "yes")


# Audit 2026-08-07, #124 defect 5: six keys read off the MC result dicts
# ('stability', 'pace_regime', 'expected_position', 'kelly_stake',
# 'pace_position', plus the three energy inputs handed to the sectional MC)
# were never written by any producer, so stabilityScore published 0.0,
# runningStyle 'unknown', kellyStake 0.0 and the fatigue/collapse/fitness
# layer inside simulate_race_realistic ran on its defaults for every runner.
# The remapped reads are gated so the change to published output is a
# deliberate flip, validated per #124's sequencing, not a deploy side effect.
MC_FIX_DEAD_FIELDS = "STRIDE_MC_FIX_DEAD_FIELDS"

# banker_detector reads running_style_score ordinally: >=2 is leader-ish,
# 0 is a backmarker, anything else mid-pack.
_STYLE_ORDINALS = {'leader': 3, 'on_pace': 2, 'handy': 1, 'midfield': 1,
                   'backmarker': 0}


def _style_ordinal(style):
    return _STYLE_ORDINALS.get(style, 1)


def _display_kelly(win_prob, odds):
    """Fractional-Kelly stake fraction for display; 0.0 when inputs can't
    support one. Delegates to the engine's own kelly_stake so the published
    number and mc_compute_staking cannot disagree about the formula."""
    try:
        if win_prob and odds and odds > 1:
            return float(racing_system.kelly_stake(float(win_prob), float(odds)))
    except Exception:
        pass
    return 0.0

RacingModel = racing_system.RacingModel
TipGenerator = racing_system.TipGenerator
SupplementaryStats = racing_system.SupplementaryStats
Race = racing_system.Race
Runner = racing_system.Runner
load_all_races = racing_system.load_all_races
convert_races = racing_system.convert_races
simulate_race_monte_carlo = racing_system.simulate_race_monte_carlo
calculate_confidence = racing_system.calculate_confidence
get_staking_tier = racing_system.get_staking_tier
mc_selection_score = racing_system.mc_selection_score
mc_is_playable = racing_system.mc_is_playable
infer_market_odds = racing_system.infer_market_odds
MC_DEFAULTS = racing_system.MC_DEFAULTS
STAKING_CONFIG = racing_system.STAKING_CONFIG
TARGET_TRACKS = racing_system.TARGET_TRACKS

_MODEL_CACHE = None
_MODEL_CACHE_KEY = None


def _get_training_directories():
    train_dirs = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'racecards'),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'upcoming'),
    ]
    return [d for d in train_dirs if os.path.exists(d)]


def _build_model_cache_key(existing_dirs):
    stats_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        'data',
        'supplementary_stats.json',
    )
    stats_mtime = os.path.getmtime(stats_path) if os.path.exists(stats_path) else 0
    dir_state = []
    for directory in existing_dirs:
        try:
            st = os.stat(directory)
            dir_state.append((directory, int(st.st_mtime), int(st.st_size)))
        except OSError:
            dir_state.append((directory, 0, 0))
    return (tuple(dir_state), int(stats_mtime))


def get_or_build_base_model():
    """Build the MC model once per process and reuse it across races."""
    global _MODEL_CACHE, _MODEL_CACHE_KEY

    existing_dirs = _get_training_directories()
    cache_key = _build_model_cache_key(existing_dirs)
    if _MODEL_CACHE is not None and _MODEL_CACHE_KEY == cache_key:
        return _MODEL_CACHE

    stats_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        'data',
        'supplementary_stats.json',
    )
    supp_stats = SupplementaryStats(stats_path) if os.path.exists(stats_path) else None
    model = RacingModel(supp_stats, use_ml=False)

    if existing_dirs:
        train_dicts = load_all_races(existing_dirs, verbose=False)
        train_races = convert_races(train_dicts)
        if train_races:
            captured = StringIO()
            with redirect_stdout(captured):
                model.train(train_races, train_dicts)

    _MODEL_CACHE = model
    _MODEL_CACHE_KEY = cache_key
    return model

from datetime import datetime, timedelta
import math

try:
    import psycopg2
    from psycopg2.extras import Json
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False


class _SharedDbConnection:
    """Thin wrapper that keeps the hot-path DB connection alive across calls."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    @property
    def closed(self):
        return self._conn.closed

    def close(self):
        # Keep the shared runtime connection hot; callers can safely call close().
        return None

    def real_close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


_REAL_PSYCOPG2_CONNECT = psycopg2.connect if PSYCOPG2_AVAILABLE else None
_DB_CONNECTIONS = {}


def _shared_psycopg2_connect(*args, **kwargs):
    if not PSYCOPG2_AVAILABLE or _REAL_PSYCOPG2_CONNECT is None:
        raise RuntimeError("psycopg2 is not available")

    cache_key = json.dumps(
        {
            "args": [str(arg) for arg in args],
            "kwargs": {str(k): str(v) for k, v in sorted(kwargs.items())},
        },
        sort_keys=True,
    )
    cached = _DB_CONNECTIONS.get(cache_key)
    if cached is not None and getattr(cached, "closed", 1) == 0:
        # Verify the connection is still alive (Neon may have killed it server-side)
        try:
            _ping_cur = cached._conn.cursor()
            _ping_cur.execute("SELECT 1")
            _ping_cur.close()
            return cached
        except Exception:
            # Server-side disconnect — evict and reconnect below
            try:
                cached._conn.close()
            except Exception:
                pass
            _DB_CONNECTIONS.pop(cache_key, None)

    kwargs = dict(kwargs)
    kwargs.setdefault("connect_timeout", 5)
    kwargs.setdefault("application_name", "wizbet_mc_api")
    kwargs.setdefault("gssencmode", "disable")

    raw_conn = _REAL_PSYCOPG2_CONNECT(*args, **kwargs)
    raw_conn.autocommit = True
    try:
        cur = raw_conn.cursor()
        cur.execute("SET statement_timeout TO 15000")
        cur.close()
    except Exception:
        pass

    wrapped = _SharedDbConnection(raw_conn)
    _DB_CONNECTIONS[cache_key] = wrapped
    return wrapped


if PSYCOPG2_AVAILABLE and getattr(psycopg2.connect, "__name__", "") != "_shared_psycopg2_connect":
    psycopg2.connect = _shared_psycopg2_connect

def get_db_connection(db_url=None):
    """Get PostgreSQL database connection."""
    if not PSYCOPG2_AVAILABLE:
        return None
    db_url = db_url or os.environ.get('DATABASE_URL')
    if not db_url:
        return None
    try:
        return psycopg2.connect(
            db_url,
            connect_timeout=5,
            application_name='wizbet_mc_api',
            gssencmode='disable',
        )
    except Exception:
        return None


def _to_native_scalar(value):
    if isinstance(value, np.generic):
        return value.item()
    return value


def _normalize_db_row(row):
    return tuple(_to_native_scalar(value) for value in row)


def log_feature_snapshots(snapshot_rows: list):
    """
    Batch insert feature snapshots into the database.
    Non-blocking: wraps everything in try/except.
    """
    try:
        conn = get_db_connection()
        if not conn:
            return
        cur = conn.cursor()
        for row in snapshot_rows:
            cur.execute("""
                INSERT INTO feature_snapshots (track, race_number, race_date, horse_name, horse_number,
                    features_json, feature_count, model_prediction, prediction_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (track, race_number, race_date, horse_name) DO NOTHING
            """, _normalize_db_row(row))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        log_debug(f"Feature snapshot logging error: {e}")
        try:
            conn.close()
        except Exception:
            pass


# The 2026-07 coverage report (docs/12 §4c) found prediction_audit held one
# week of rows after 15 months of daily runs — failures here were visible
# only with debug_mode on. Audit failures now warn on stderr once per
# process; still non-blocking.
_AUDIT_WARNED = {"done": False}
_AUDIT_INDEX_ENSURED = {"done": False}


def _warn_audit_failure(reason: str):
    if not _AUDIT_WARNED["done"]:
        _AUDIT_WARNED["done"] = True
        print(f"[WARN] prediction_audit logging FAILED: {reason} — "
              f"training_view_v2 gains no full-field rows while this persists "
              f"(further failures this run at debug level only)",
              file=sys.stderr)


def _ensure_audit_unique_index(conn):
    """
    The upsert below names ON CONFLICT (track, race_number, race_date,
    horse_name), but the live table was created without that unique key, so
    PostgreSQL rejected every audit insert outright (root cause of the
    near-empty table — audit-smoke run, 2026-07-13). Self-heal once per
    process, mirroring store_final_probs_in_audit's ALTER pattern; the
    reviewable version is migrations/prediction_audit_unique_key.sql.
    """
    if _AUDIT_INDEX_ENSURED["done"]:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_prediction_audit_race_horse
            ON prediction_audit (track, race_number, race_date, horse_name)
        """)
        conn.commit()
        cur.close()
        _AUDIT_INDEX_ENSURED["done"] = True
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        _warn_audit_failure(
            f"could not create unique index for the audit upsert (duplicate "
            f"rows? run migrations/prediction_audit_unique_key.sql): {e}")


def log_prediction_audit(audit_rows: list):
    """
    Batch insert prediction audit entries into the database.
    Non-blocking: wraps everything in try/except.
    """
    try:
        conn = get_db_connection()
        if not conn:
            _warn_audit_failure(
                "no database connection (DATABASE_URL unset/unreachable, or psycopg2 missing)")
            return
        _ensure_audit_unique_index(conn)
        cur = conn.cursor()
        for row in audit_rows:
            cur.execute("""
                INSERT INTO prediction_audit (track, race_number, race_date, off_time, horse_name,
                    predicted_win_prob, predicted_place_prob, market_odds, confidence, edge, result_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (track, race_number, race_date, horse_name) DO UPDATE
                SET race_date = CASE WHEN prediction_audit.race_date = '' OR prediction_audit.race_date IS NULL 
                    THEN EXCLUDED.race_date ELSE prediction_audit.race_date END,
                    off_time = CASE WHEN prediction_audit.off_time IS NULL 
                    THEN EXCLUDED.off_time ELSE prediction_audit.off_time END
            """, _normalize_db_row(row))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        _warn_audit_failure(str(e))
        log_debug(f"Prediction audit logging error: {e}")
        try:
            conn.close()
        except Exception:
            pass


def log_race_schedule(track: str, race_number: int, race_date: str, off_time_str: str):
    """
    Insert a race_schedule entry for automated results collection.
    Non-blocking: wraps everything in try/except.
    Falls back to deriving race_date from off_time or today's date if empty.
    """
    try:
        conn = get_db_connection()
        if not conn:
            return
        cur = conn.cursor()
        off_time = None
        result_due_at = None
        if off_time_str:
            try:
                off_time = datetime.fromisoformat(off_time_str.replace('Z', '+00:00'))
                result_due_at = off_time + timedelta(minutes=30)
            except (ValueError, TypeError):
                pass
        if not race_date or race_date.strip() == '':
            if off_time:
                race_date = off_time.strftime('%Y-%m-%d')
            else:
                race_date = datetime.now().strftime('%Y-%m-%d')
        if not result_due_at:
            try:
                result_due_at = datetime.fromisoformat(race_date)
            except (ValueError, TypeError):
                result_due_at = datetime.now()
        cur.execute("""
            INSERT INTO race_schedule (track, race_number, race_date, off_time, result_due_at, result_status)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (track, race_number, race_date) DO NOTHING
        """, (track, race_number, race_date, off_time, result_due_at, 'pending'))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        log_debug(f"Race schedule logging error: {e}")
        try:
            conn.close()
        except Exception:
            pass

GOING_MAP = {
    'firm': 1, 'good': 2, 'soft': 3, 'heavy': 4, 'synthetic': 5
}

def parse_stats(stats_obj):
    """Parse stats object safely, returns (total, wins, seconds, thirds)."""
    if not stats_obj:
        return 0, 0, 0, 0
    if isinstance(stats_obj, str):
        return 0, 0, 0, 0
    total = int(stats_obj.get('total', 0) or 0)
    first = int(stats_obj.get('first', 0) or 0)
    second = int(stats_obj.get('second', 0) or 0)
    third = int(stats_obj.get('third', 0) or 0)
    return total, first, second, third

def calculate_strike_rate(wins, total):
    """Calculate strike rate as percentage, with Bayesian smoothing."""
    if total == 0:
        return 0.0
    # Bayesian smoothing: add 1 pseudo-win and 4 pseudo-losses (20% prior)
    smoothed_wins = wins + 1
    smoothed_total = total + 5
    return (smoothed_wins / smoothed_total) * 100

def get_last_start_market_differential(horse_name: str, limit: int = 3) -> dict:
    """
    Calculate how a horse performed vs market expectation in recent starts.

    A horse at $3.50 (28.6% implied) finishing 4th in 10-runner (expected ~10%)
    was 'disappointing'. A $21 horse (4.8%) running 4th was 'overperforming'.

    Returns dict with last_start_differential, avg_differential_3runs, market_trend, last_start_odds.
    """
    result = {
        'last_start_differential': 0.0,
        'avg_differential_3runs': 0.0,
        'market_trend': 'stable',
        'last_start_odds': 0.0,
    }
    
    conn = get_db_connection()
    if not conn:
        return result
    
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT position, sp_odds, field_size, race_date
            FROM race_results_history
            WHERE LOWER(REPLACE(horse_name, '''', '')) = LOWER(REPLACE(%s, '''', ''))
              AND position IS NOT NULL 
              AND sp_odds IS NOT NULL 
              AND sp_odds > 1.0
              AND field_size IS NOT NULL
              AND field_size > 0
            ORDER BY race_date DESC
            LIMIT %s
        """, (horse_name, limit))
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception:
        try:
            conn.close()
        except:
            pass
        return result
    
    if not rows:
        return result
    
    differentials = []
    odds_list = []
    
    for pos, odds, field_size, race_date in rows:
        implied_prob = 1.0 / float(odds)
        expected_position = 1.0 / implied_prob
        actual_position = float(pos)
        
        differential = (expected_position - actual_position) / float(field_size)
        differentials.append(differential)
        odds_list.append(float(odds))
    
    result['last_start_differential'] = round(differentials[0], 4)
    result['avg_differential_3runs'] = round(sum(differentials) / len(differentials), 4)
    result['last_start_odds'] = odds_list[0]
    
    if len(odds_list) >= 2:
        if odds_list[0] < odds_list[-1] * 0.85:
            result['market_trend'] = 'shortening'
        elif odds_list[0] > odds_list[-1] * 1.15:
            result['market_trend'] = 'drifting'
    
    return result


def extract_ml_features(runner: dict, race_data: dict, all_runners: list = None) -> dict:
    """
    Extract comprehensive features from Racing API data for ML model.
    Returns a feature dictionary covering track condition, distance, course, form, jockey, trainer, barrier, and market signals.
    """
    features = {}
    stats = runner.get('stats', {}) or {}
    going = str(race_data.get('going', '')).lower()
    distance_str = str(race_data.get('distance', '0'))
    
    going_stats_map = {
        'firm': 'ground_firm_stats',
        'good': 'ground_good_stats', 
        'soft': 'ground_soft_stats',
        'heavy': 'ground_heavy_stats',
        'synthetic': 'ground_aw_stats',
    }
    
    ground_score = 0.0
    ground_runs = 0
    for going_key, stats_field in going_stats_map.items():
        if going_key in going:
            ground_stats = stats.get(stats_field)
            if ground_stats:
                total, wins, seconds, thirds = parse_stats(ground_stats)
                ground_runs = total
                if total > 0:
                    ground_score = calculate_strike_rate(wins, total)
    
    features['ground_suitability'] = ground_score
    features['ground_experience'] = min(ground_runs / 5, 1.0)
    is_wet_going = 'soft' in going or 'heavy' in going
    features['is_wet_tracker'] = 1 if is_wet_going and ground_score > 20 else 0
    
    distance_stats = stats.get('distance_stats')
    total, wins, seconds, thirds = parse_stats(distance_stats)
    features['distance_strike_rate'] = calculate_strike_rate(wins, total)
    features['distance_experience'] = min(total / 5, 1.0)
    features['distance_placed_rate'] = calculate_strike_rate(wins + seconds + thirds, total) if total > 0 else 0
    
    course_stats = stats.get('course_stats')
    total, wins, seconds, thirds = parse_stats(course_stats)
    features['course_strike_rate'] = calculate_strike_rate(wins, total)
    features['course_experience'] = min(total / 5, 1.0)
    
    cd_stats = stats.get('course_distance_stats')
    total, wins, seconds, thirds = parse_stats(cd_stats)
    features['course_distance_strike_rate'] = calculate_strike_rate(wins, total)
    features['is_course_distance_winner'] = 1 if wins > 0 else 0
    
    form_str = str(runner.get('form', '')).strip()
    form_score = 0.0
    form_positions = []
    
    for i, char in enumerate(form_str[:6]):
        if char.isdigit():
            pos = int(char)
            form_positions.append(pos)
            # Recency weight: most recent = 1.0, 6th run = 0.5
            weight = 1.0 - (i * 0.1)
            # Score: 1st = 10pts, 2nd = 6pts, 3rd = 4pts, 4th = 2pts, else 0
            if pos == 1:
                form_score += 10 * weight
            elif pos == 2:
                form_score += 6 * weight
            elif pos == 3:
                form_score += 4 * weight
            elif pos <= 5:
                form_score += 2 * weight
    
    features['weighted_form_score'] = form_score
    if len(form_positions) >= 2:
        avg_pos = sum(form_positions) / len(form_positions)
        variance = sum((p - avg_pos) ** 2 for p in form_positions) / len(form_positions)
        features['form_consistency'] = max(0, 10 - variance)
    else:
        features['form_consistency'] = 5.0
    features['recent_winner'] = 1 if form_str and form_str[0] == '1' else 0
    features['last_start_position'] = int(form_str[0]) if form_str and form_str[0].isdigit() else 9
    
    # Fallback: if neither available, assume mid-range (28 days) not worst-case
    last_raced_str = stats.get('last_raced', '')
    days_since_run = 28  # Sensible default instead of 999 (false first-up)
    is_first_up = 0
    is_second_up = 0
    
    # Check form string for spell indicator 'x' (e.g., "12x34" means break between 2nd and 3rd runs)
    form_has_spell = False
    if form_str:
        # 'x' at position 0 means first-up (coming off a spell)
        # 'x' at position 1 means second-up (one run back from spell)
        first_x_pos = -1
        for idx, ch in enumerate(form_str[:6]):
            if ch.lower() == 'x':
                first_x_pos = idx
                break
        if first_x_pos == 0:
            is_first_up = 1
            form_has_spell = True
            days_since_run = 90  # Estimate for first-up from form
        elif first_x_pos == 1:
            is_second_up = 1
            form_has_spell = True
            days_since_run = 28  # Second-up, recent racing
    
    # Cross-validate/override with actual last_raced date when available
    if last_raced_str:
        try:
            last_raced = datetime.fromisoformat(last_raced_str.replace('Z', '+00:00'))
            race_date_str = race_data.get('date', '')
            if race_date_str:
                race_date = datetime.fromisoformat(race_date_str.replace('Z', '+00:00'))
                days_since_run = max(0, (race_date - last_raced).days)
                
                if days_since_run > 60:
                    is_first_up = 1
                elif 21 <= days_since_run <= 60:
                    if not form_has_spell:
                        is_second_up = 1
                else:
                    if days_since_run < 21:
                        is_first_up = 0
                        is_second_up = 0
        except (ValueError, TypeError):
            pass
    
    features['days_since_run'] = min(days_since_run, 365)
    features['is_first_up'] = is_first_up
    features['is_second_up'] = is_second_up
    features['freshness_score'] = 1.0 if 14 <= days_since_run <= 35 else 0.8 if 7 <= days_since_run <= 60 else 0.5
    
    weight = parse_weight(runner.get('weight', 0))
    features['weight_carried'] = weight
    features['is_light_weight'] = 1 if weight <= 54 else 0
    features['is_heavy_weight'] = 1 if weight >= 59 else 0
    
    race_class = str(race_data.get('class', '')).upper()
    prize_total = float(race_data.get('prize_total', 0) or 0)
    
    class_level = 1
    if 'GROUP 1' in race_class or 'G1' in race_class:
        class_level = 10
    elif 'GROUP 2' in race_class or 'G2' in race_class:
        class_level = 9
    elif 'GROUP 3' in race_class or 'G3' in race_class:
        class_level = 8
    elif 'LISTED' in race_class:
        class_level = 7
    elif 'OPEN' in race_class:
        class_level = 6
    elif 'BM' in race_class or 'BENCHMARK' in race_class:
        for num in ['100', '90', '85', '80', '78', '75', '72', '70', '68', '65', '62', '60', '58']:
            if num in race_class:
                class_level = max(2, int(int(num) / 15))
                break
    elif 'MDN' in race_class or 'MAIDEN' in race_class:
        class_level = 2
    
    features['race_class_level'] = class_level
    features['prize_money_indicator'] = min(prize_total / 500000, 1.0)
    
    jockey_stats = stats.get('jockey_stats')
    total, wins, seconds, thirds = parse_stats(jockey_stats)
    features['jockey_trainer_strike_rate'] = calculate_strike_rate(wins, total)
    features['jockey_trainer_experience'] = min(total / 10, 1.0)
    features['is_winning_combo'] = 1 if total >= 3 and (wins / max(total, 1)) > 0.25 else 0
    
    age_raw = runner.get('age', 0)
    age = 0
    if age_raw:
        try:
            if isinstance(age_raw, int):
                age = age_raw
            else:
                age_match = re.search(r'\d+', str(age_raw))
                if age_match:
                    age = int(age_match.group())
        except (ValueError, TypeError):
            age = 0
    sex = str(runner.get('sex', '')).lower()
    features['horse_age'] = age
    features['is_young'] = 1 if age <= 3 else 0
    features['is_mature'] = 1 if 4 <= age <= 6 else 0
    features['is_mare_filly'] = 1 if sex in ('mare', 'filly') else 0
    
    barrier = int(runner.get('draw', runner.get('barrier', 0)) or 0)
    distance_m = 0
    try:
        distance_m = int(distance_str.replace('m', '').strip())
    except ValueError:
        pass
    
    if all_runners:
        field_size = len([r for r in all_runners if is_active_runner(r)])
    elif race_data.get('runners'):
        field_size = len([r for r in race_data.get('runners', []) if is_active_runner(r)])
    else:
        field_size = 12
    if field_size == 0:
        field_size = 12
    
    # Barrier/field ratio (0.0-1.0, higher = wider barrier relative to field)
    barrier_field_ratio = barrier / field_size if barrier > 0 else 0.5
    
    # Wide barriers hurt more in sprints - using relative position
    barrier_penalty = 0
    if barrier_field_ratio > 0.7:  # Outer 30% of field
        if distance_m <= 1200:
            barrier_penalty = 0.15  # 15% penalty for wide barrier in sprint
        elif distance_m <= 1600:
            barrier_penalty = 0.10
        else:
            barrier_penalty = 0.05  # Less impact in staying races
    elif barrier_field_ratio > 0.5:  # Middle-wide
        if distance_m <= 1200:
            barrier_penalty = 0.08
        elif distance_m <= 1600:
            barrier_penalty = 0.05
    
    # Inside barrier bonus (relative to field)
    barrier_bonus = 0
    if barrier_field_ratio <= 0.25 and barrier > 0:  # Inner 25% of field
        if distance_m <= 1400:
            barrier_bonus = 0.08  # Good barrier in sprint/mile
        else:
            barrier_bonus = 0.04
    
    features['barrier_draw'] = barrier
    features['barrier'] = barrier
    features['barrier_field_ratio'] = round(barrier_field_ratio, 3)
    features['field_size'] = field_size
    features['barrier_penalty'] = barrier_penalty
    features['barrier_bonus'] = barrier_bonus
    features['is_wide_barrier'] = 1 if barrier_field_ratio > 0.7 else 0
    features['is_good_barrier'] = 1 if 0 < barrier_field_ratio <= 0.35 else 0
    features['inside_draw'] = 1 if barrier <= 4 else 0
    features['wide_draw'] = 1 if barrier >= 10 else 0
    features['is_small_field'] = 1 if field_size <= 8 else 0
    features['is_large_field'] = 1 if field_size >= 14 else 0
    
    if EMPIRICAL_BARRIERS_AVAILABLE:
        track_name = race_data.get('track', '')
        barrier_num = parse_int(runner.get('barrier', runner.get('draw', 0)))
        dist_m = parse_int(race_data.get('distance_m', race_data.get('distance', 1400)))
        emp_barrier = get_empirical_barrier_advantage(track_name, barrier_num, dist_m)
        features['empirical_barrier_advantage'] = emp_barrier['advantage_score']
    else:
        features['empirical_barrier_advantage'] = 0.0
    
    current_odds = parse_odds(runner, use_default=False)
    features['market_odds'] = current_odds
    features['market_implied_prob'] = 100 / max(current_odds, 1.01)
    features['log_odds'] = float(np.log1p(current_odds)) if current_odds > 0 else 0
    features['is_favourite'] = 1 if current_odds <= 3 else 0
    features['is_outsider'] = 1 if current_odds >= 20 else 0
    
    active_runners = [r for r in (all_runners or []) if is_active_runner(r)]
    n_runners = len(active_runners) if active_runners else field_size
    
    if active_runners and current_odds > 1:
        all_implied = []
        for r in active_runners:
            r_odds = parse_odds(r, use_default=False)
            if r_odds > 1:
                all_implied.append(1.0 / r_odds)
        overround = sum(all_implied) if all_implied else 1.0
        overround = max(overround, 0.5)
        raw_prob = 1.0 / current_odds
        features['fair_implied_prob'] = (raw_prob / overround) * 100
        features['market_overround'] = overround * 100
    else:
        features['fair_implied_prob'] = (1.0 / max(current_odds, 1.01)) * 100
        features['market_overround'] = 115.0
    
    if active_runners and n_runners > 1:
        all_odds = sorted([parse_odds(r, use_default=False) for r in active_runners if parse_odds(r, use_default=False) > 1])
        odds_rank = 1
        for o in all_odds:
            if o < current_odds:
                odds_rank += 1
        features['odds_rank_pct'] = odds_rank / n_runners
        features['odds_rank'] = odds_rank
        
        all_implied_probs = [100.0 / max(parse_odds(r, use_default=False), 1.01) for r in active_runners if parse_odds(r, use_default=False) > 1]
        features['field_avg_implied_prob'] = sum(all_implied_probs) / max(len(all_implied_probs), 1)
        
        all_weights = sorted([parse_weight(r.get('weight', 56)) for r in active_runners], reverse=True)
        runner_weight = parse_weight(runner.get('weight', 56))
        weight_rank = 1
        for w in all_weights:
            if w > runner_weight:
                weight_rank += 1
        features['weight_rank_pct'] = weight_rank / n_runners
        
        all_barriers = sorted([int(r.get('draw', r.get('barrier', 0)) or 0) for r in active_runners])
        barrier_rank = 1
        for b in all_barriers:
            if b < barrier:
                barrier_rank += 1
        features['barrier_rank_pct'] = barrier_rank / n_runners
        
        all_form_scores = []
        for r in active_runners:
            r_form = str(r.get('form', '')).strip()
            r_score = 0.0
            for i, ch in enumerate(r_form[:6]):
                if ch.isdigit():
                    pos = int(ch)
                    w = 1.0 - (i * 0.1)
                    if pos == 1: r_score += 10 * w
                    elif pos == 2: r_score += 6 * w
                    elif pos == 3: r_score += 4 * w
                    elif pos <= 5: r_score += 2 * w
            all_form_scores.append(r_score)
        all_form_sorted = sorted(all_form_scores, reverse=True)
        form_rank = 1
        my_form = features.get('weighted_form_score', 0)
        for fs in all_form_sorted:
            if fs > my_form:
                form_rank += 1
        features['form_rank_pct'] = form_rank / n_runners
        
        speed_horse_count = 0
        for r in active_runners:
            r_form = str(r.get('form', '')).strip()[:3]
            if '1' in r_form or r_form.startswith('2'):
                speed_horse_count += 1
        features['pace_pressure'] = (speed_horse_count / max(n_runners, 1)) * 100
        
        my_form_str = str(runner.get('form', '')).strip()
        my_early_positions = []
        for ch in my_form_str[:5]:
            if ch.isdigit():
                my_early_positions.append(int(ch))
        avg_pos = sum(my_early_positions) / max(len(my_early_positions), 1) if my_early_positions else 5
        is_leader = avg_pos <= 3
        is_closer = avg_pos >= 6
        is_high_pace = features['pace_pressure'] >= 40
        is_low_pace = speed_horse_count <= 2
        
        if is_leader and is_low_pace:
            features['pace_fit_score'] = 15.0
        elif is_closer and is_high_pace:
            features['pace_fit_score'] = 12.0
        elif is_leader and is_high_pace:
            features['pace_fit_score'] = -10.0
        elif is_closer and is_low_pace:
            features['pace_fit_score'] = -8.0
        else:
            features['pace_fit_score'] = 0.0
        
        features['market_confidence_residual'] = float(np.clip(form_rank - odds_rank, -10, 10))
        
        try:
            distance_str_tp = str(race_data.get('distance', '1400'))
            distance_m_tp = int(distance_str_tp.replace('m', '').strip())
        except (ValueError, TypeError):
            distance_m_tp = 1400
        
        predicted_tempo = 50.0
        if speed_horse_count >= 4:
            predicted_tempo = 80.0
        elif speed_horse_count >= 3:
            predicted_tempo = 65.0
        elif speed_horse_count >= 2:
            predicted_tempo = 50.0
        elif speed_horse_count >= 1:
            predicted_tempo = 35.0
        else:
            predicted_tempo = 20.0
        if distance_m_tp <= 1200:
            predicted_tempo = min(predicted_tempo + 10, 100)
        elif distance_m_tp >= 2000:
            predicted_tempo = max(predicted_tempo - 10, 0)
        features['predicted_tempo'] = predicted_tempo
        
        is_fast_tempo = predicted_tempo >= 65
        is_slow_tempo = predicted_tempo <= 35
        if is_leader and is_slow_tempo:
            features['tempo_fit_score'] = 12.0
        elif is_closer and is_fast_tempo:
            features['tempo_fit_score'] = 10.0
        elif is_leader and is_fast_tempo:
            features['tempo_fit_score'] = -8.0
        elif is_closer and is_slow_tempo:
            features['tempo_fit_score'] = -6.0
        else:
            features['tempo_fit_score'] = 0.0
    else:
        features['odds_rank_pct'] = 0.5
        features['weight_rank_pct'] = 0.5
        features['barrier_rank_pct'] = 0.5
        features['form_rank_pct'] = 0.5
        features['pace_pressure'] = 30.0
        features['pace_fit_score'] = 0.0
        features['market_confidence_residual'] = 0.0
        features['predicted_tempo'] = 50.0
        features['tempo_fit_score'] = 0.0
    
    overall_stats = stats.get('overall_stats', stats.get('career_stats', {}))
    total_starts, career_wins, career_seconds, career_thirds = parse_stats(overall_stats)
    features['career_starts'] = total_starts
    features['career_win_rate'] = (career_wins / max(total_starts, 1)) * 100
    features['career_place_rate'] = ((career_wins + career_seconds + career_thirds) / max(total_starts, 1)) * 100
    features['is_debutant'] = 1 if total_starts == 0 else 0
    features['is_experienced'] = 1 if total_starts >= 10 else 0
    
    race_class_str = str(race_data.get('class', race_data.get('raceClass', race_data.get('race_class', '')))).lower()
    race_class_rank = 3.0
    try:
        from enhanced_features import parse_class_rank as _parse_class_rank
        race_class_rank = _parse_class_rank(race_class_str)
    except Exception:
        if 'group 1' in race_class_str:
            race_class_rank = 10.0
        elif 'group 2' in race_class_str:
            race_class_rank = 9.0
        elif 'group 3' in race_class_str:
            race_class_rank = 8.0
        elif 'listed' in race_class_str:
            race_class_rank = 7.0
    
    features['race_class_rank'] = race_class_rank
    class_fitness_penalty = 1.0
    if race_class_rank >= 7.0:
        career_win_rate = features['career_win_rate']
        career_place_rate = features['career_place_rate']
        has_any_win = career_wins > 0
        has_recent_top4 = any(
            ch.isdigit() and int(ch) <= 4
            for ch in form_str[:6]
        ) if form_str else False
        
        if not has_any_win and total_starts <= 3:
            class_fitness_penalty = 0.75
        elif career_win_rate < 10 and not has_recent_top4:
            class_fitness_penalty = 0.82
        elif career_win_rate < 10 and total_starts >= 5:
            class_fitness_penalty = 0.88
        elif not has_any_win:
            class_fitness_penalty = 0.85
    
    features['class_fitness_penalty'] = class_fitness_penalty
    
    features['weight'] = weight
    features['weight_penalty'] = (weight - 55) * 0.02 if weight > 55 else 0
    features['light_weight'] = features.get('is_light_weight', 0)
    features['heavy_weight'] = features.get('is_heavy_weight', 0)
    features['class_level'] = features.get('race_class_level', 50)
    features['is_wet'] = 1 if is_wet_going else 0
    features['is_young_horse'] = 1 if age <= 3 else 0
    features['is_prime_age'] = 1 if 4 <= age <= 6 else 0
    features['is_older_horse'] = 1 if age >= 7 else 0
    
    features['class_change'] = 0
    features['is_class_drop'] = 0
    features['is_class_rise'] = 0
    
    form_chars = [ch for ch in form_str[:10] if ch.isdigit()]
    has_recent_win = '1' in form_str[:3] if form_str else False
    features['days_since_win'] = 30 if has_recent_win else 180
    features['is_recent_winner'] = 1 if has_recent_win else 0
    
    cd_stats = stats.get('course_distance_stats', {})
    cd_total, cd_wins, _, _ = parse_stats(cd_stats)
    features['course_distance_strike_rate'] = (cd_wins / max(cd_total, 1)) * 100 if cd_total > 0 else 0
    features['is_course_distance_winner'] = 1 if cd_wins > 0 else 0
    
    jockey_overall = stats.get('jockey_overall_stats', stats.get('jockey_stats', {}))
    j_total, j_wins, _, _ = parse_stats(jockey_overall)
    features['jockey_win_rate'] = (j_wins / max(j_total, 1)) * 100 if j_total > 0 else 12.0
    features['is_top_jockey'] = 1 if features['jockey_win_rate'] >= 18 else 0
    
    trainer_overall = stats.get('trainer_overall_stats', stats.get('trainer_stats', {}))
    t_total, t_wins, _, _ = parse_stats(trainer_overall)
    features['trainer_win_rate'] = (t_wins / max(t_total, 1)) * 100 if t_total > 0 else 10.0
    features['is_top_trainer'] = 1 if features['trainer_win_rate'] >= 15 else 0
    
    if len(form_positions) >= 3:
        recent_avg = sum(form_positions[:2]) / 2
        older_avg = sum(form_positions[2:]) / len(form_positions[2:])
        trajectory = older_avg - recent_avg
        features['form_trajectory'] = trajectory
        features['is_improving'] = 1 if trajectory > 0.5 else 0
        features['is_declining'] = 1 if trajectory < -0.5 else 0
    else:
        features['form_trajectory'] = 0.0
        features['is_improving'] = 0
        features['is_declining'] = 0
    
    features['track_barrier_winrate'] = 10.0
    
    features['last_start_position'] = int(form_str[0]) if form_str and form_str[0].isdigit() else 9
    features['is_last_start_winner'] = 1 if form_str and form_str[0] == '1' else 0
    
    features['is_mile'] = 1 if 1400 <= distance_m <= 1800 else 0
    features['is_middle'] = 1 if 1800 < distance_m < 2200 else 0
    
    horse_name = runner.get('horse', runner.get('name', ''))
    # Task 03: market-movement columns are 0 in training and in the shared
    # serve builder — real values are sourced post-tip-time (see task 14).
    # Only populate them when STRIDE_MOVEMENT_FEATURES_LIVE=1.
    if horse_name and _movement_features_live():
        market_diff = get_last_start_market_differential(horse_name)
        features['last_start_market_diff'] = market_diff['last_start_differential']
        features['avg_market_diff_3runs'] = market_diff['avg_differential_3runs']
        features['market_trend_shortening'] = 1 if market_diff['market_trend'] == 'shortening' else 0
        features['market_trend_drifting'] = 1 if market_diff['market_trend'] == 'drifting' else 0
    else:
        features['last_start_market_diff'] = 0.0
        features['avg_market_diff_3runs'] = 0.0
        features['market_trend_shortening'] = 0
        features['market_trend_drifting'] = 0
    
    
    last_weight = runner.get('last_start_weight', None)
    if last_weight and weight > 0:
        features['weight_change'] = float(np.clip(weight - parse_weight(last_weight), -5, 5))
    else:
        features['weight_change'] = 0.0
    features['is_weight_drop'] = 1 if features['weight_change'] < -1 else 0
    features['is_weight_rise'] = 1 if features['weight_change'] > 1 else 0
    
    first_up_stats = stats.get('first_up_stats', {})
    fu_total, fu_wins, _, _ = parse_stats(first_up_stats)
    if fu_total >= 2:
        features['horse_first_up_winrate'] = (fu_wins / max(fu_total, 1)) * 100
    else:
        features['horse_first_up_winrate'] = 10.0
    
    career_total, career_wins, career_seconds, career_thirds = parse_stats(stats.get('career', stats.get('overall', {})))
    class_level_val = features.get('race_class_level', features.get('class_level', 50))
    if career_total > 0:
        place_rate = (career_wins + career_seconds + career_thirds) / max(career_total, 1)
        features['class_weighted_perf'] = (class_level_val / 100.0) * place_rate
    else:
        features['class_weighted_perf'] = 0.5
    
    features['odds_rank'] = features.get('odds_rank', 5)
    features['is_top3_market'] = 1 if features['odds_rank'] <= 3 else 0
    
    field_size_val = features.get('field_size', 10)
    class_val = features.get('class_level', features.get('race_class_level', 50))
    features['field_quality_score'] = min(50, (class_val * max(4, field_size_val)) / 20.0)
    features['field_avg_implied_prob'] = features.get('field_avg_implied_prob', 100.0 / max(field_size_val, 1))
    features['horse_vs_field'] = features.get('market_implied_prob', 10) - features['field_avg_implied_prob']
    
    last_distance = runner.get('last_start_distance', None)
    if last_distance:
        try:
            last_dist_m = extract_distance(str(last_distance))
            features['distance_change'] = float(np.clip(distance_m - last_dist_m, -1000, 1000))
        except Exception:
            features['distance_change'] = 0.0
    else:
        features['distance_change'] = 0.0
    features['is_distance_drop'] = 1 if features['distance_change'] < -100 else 0
    features['is_distance_rise'] = 1 if features['distance_change'] > 100 else 0
    
    if career_total > 0:
        features['career_lifetime_winrate'] = (career_wins / max(career_total, 1)) * 100
    else:
        features['career_lifetime_winrate'] = 10.0
    
    
    jt_combo_stats = stats.get('jockey_trainer_combo_stats', {})
    jt_total, jt_wins, _, _ = parse_stats(jt_combo_stats)
    if jt_total >= 5:
        features['jockey_trainer_combo_winrate'] = (jt_wins / max(jt_total, 1)) * 100
    else:
        features['jockey_trainer_combo_winrate'] = 12.0
    features['is_strong_combo'] = 1 if features['jockey_trainer_combo_winrate'] > 15 else 0
    
    going_str = str(race_data.get('going', race_data.get('track_condition', ''))).strip().lower()
    going_cat = 'good'
    if 'heavy' in going_str:
        going_cat = 'heavy'
    elif 'soft' in going_str:
        going_cat = 'soft'
    elif 'synth' in going_str or 'all weather' in going_str:
        going_cat = 'synth'
    
    going_stats_key = f'{going_cat}_stats'
    going_stats = stats.get(going_stats_key, stats.get('track_condition_stats', {}))
    go_total, go_wins, _, _ = parse_stats(going_stats)
    if go_total >= 3:
        features['going_specialist_winrate'] = (go_wins / max(go_total, 1)) * 100
    else:
        features['going_specialist_winrate'] = 10.0
    features['is_going_specialist'] = 1 if features['going_specialist_winrate'] > 20 else 0
    
    features['pace_pressure'] = features.get('pace_pressure', 30.0)
    features['pace_fit_score'] = features.get('pace_fit_score', 0.0)
    
    if len(form_positions) >= 3:
        form_std = float(np.std(form_positions[:6]))
        features['form_volatility'] = form_std
    else:
        features['form_volatility'] = 3.0
    features['is_consistent_runner'] = 1 if features['form_volatility'] < 1.5 else 0
    
    features['class_speed_proxy'] = features.get('class_weighted_perf', 0.5)
    
    second_up_stats = stats.get('second_up_stats', {})
    su_total, su_wins, _, _ = parse_stats(second_up_stats)
    features['is_second_up'] = 0
    if 'is_first_up' in features and features.get('is_first_up', 0) == 0:
        prev_spell = runner.get('last_spell_days', 0)
        if prev_spell and int(prev_spell) >= 60:
            days_since = features.get('days_since_run', 999)
            if days_since <= 35:
                features['is_second_up'] = 1
    if su_total >= 2:
        features['second_up_winrate'] = (su_wins / max(su_total, 1)) * 100
    else:
        features['second_up_winrate'] = 10.0
    
    features['market_confidence_residual'] = features.get('market_confidence_residual', 0.0)
    
    
    form_str_es = str(runner.get('form', '')).strip()
    es_positions = []
    for ch in form_str_es[:5]:
        if ch.isdigit():
            es_positions.append(int(ch) if ch != '0' else 10)
    if len(es_positions) >= 2:
        es_weights = [3.0, 2.5, 2.0, 1.5, 1.0]
        es_wsum = sum(p * es_weights[i] for i, p in enumerate(es_positions[:5]))
        es_wtotal = sum(es_weights[i] for i in range(min(len(es_positions), 5)))
        features['early_speed_tendency'] = es_wsum / max(es_wtotal, 1)
    else:
        features['early_speed_tendency'] = 5.0
    features['early_speed_score'] = float(np.clip((10.0 - min(features['early_speed_tendency'], 10)) / 9.0 * 100, 0, 100))
    barrier_val = float(runner.get('barrier', 5))
    barrier_bonus = 10.0 if barrier_val <= 4 else (0.0 if barrier_val <= 8 else -10.0)
    features['predicted_early_position'] = float(np.clip(features['early_speed_tendency'] - barrier_bonus / 20.0, 1, 15))
    features['is_likely_leader'] = 1 if features['predicted_early_position'] <= 3 else 0
    
    wfa_baseline = 56.0
    weight_val = parse_weight(runner.get('weight', wfa_baseline)) or wfa_baseline
    features['weight_advantage_kg'] = float(np.clip(wfa_baseline - weight_val, -5, 5))
    speed_proxy = features.get('class_speed_proxy', features.get('class_weighted_perf', 0.5))
    features['weight_adjusted_speed'] = float(np.clip(speed_proxy + features['weight_advantage_kg'] * 0.02, 0, 1.5))
    
    features['predicted_tempo'] = features.get('predicted_tempo', 50.0)
    features['tempo_fit_score'] = features.get('tempo_fit_score', 0.0)
    
    return features



def extract_speed_and_pace_features(runner: dict, all_runners: list, race_data: dict) -> dict:
    """
    Extract speed figures and pace modeling features.
    Analyzes running style, early/mid/late pace profiles, and field pace scenario.
    """
    features = {}
    
    form_str = str(runner.get('form', '')).strip()
    distance_str = str(race_data.get('distance', '0'))
    
    try:
        distance_m = int(distance_str.replace('m', '').strip())
    except ValueError:
        distance_m = 1400
    
    early_speed_score = 0
    late_speed_score = 0
    position_changes = []
    
    form_details = runner.get('form_details', [])
    if form_details and isinstance(form_details, list):
        for run in form_details[:5]:
            if isinstance(run, dict):
                pos_800 = run.get('position_800m', 0)
                pos_400 = run.get('position_400m', 0)
                final_pos = run.get('finish_position', 0)
                if pos_800 and final_pos:
                    position_changes.append(pos_800 - final_pos)
                if pos_800 and pos_800 <= 3:
                    early_speed_score += 2
                if final_pos and pos_800 and final_pos < pos_800:
                    late_speed_score += 2
    
    if not form_details and form_str:
        for i, char in enumerate(form_str[:5]):
            if char == '1':
                early_speed_score += 1
                late_speed_score += 1
            elif char in '23':
                late_speed_score += 1
    
    running_style_score = 1
    if early_speed_score > late_speed_score + 2:
        running_style_score = 3
    elif early_speed_score > late_speed_score:
        running_style_score = 2
    elif late_speed_score > early_speed_score + 2:
        running_style_score = 0
    
    features['running_style_score'] = running_style_score
    features['early_speed_score'] = min(early_speed_score, 10)
    features['late_speed_score'] = min(late_speed_score, 10)
    
    field_speed_count = 0
    for r in all_runners:
        r_form = str(r.get('form', ''))[:3]
        if '1' in r_form or r_form.startswith('2'):
            field_speed_count += 1
    
    # High pace = bad for leaders, good for closers
    is_high_pace_expected = field_speed_count >= len(all_runners) * 0.4
    is_slow_pace_expected = field_speed_count <= 2
    
    features['field_speed_count'] = field_speed_count
    features['is_high_pace_expected'] = 1 if is_high_pace_expected else 0
    features['is_slow_pace_expected'] = 1 if is_slow_pace_expected else 0
    
    # Pace advantage: leaders love slow pace, closers love hot pace
    pace_advantage = 0
    if running_style_score >= 2 and is_slow_pace_expected:
        pace_advantage = 0.15  # Leader in slow pace = advantage
    elif running_style_score <= 1 and is_high_pace_expected:
        pace_advantage = 0.12  # Closer in hot pace = advantage
    elif running_style_score >= 2 and is_high_pace_expected:
        pace_advantage = -0.10  # Leader in hot pace = tough
    
    features['pace_advantage'] = pace_advantage
    
    prize = float(race_data.get('prize_total', 0) or 0)
    class_str = str(race_data.get('class', '')).upper()
    
    base_speed = 85
    
    if 'GROUP 1' in class_str or 'G1' in class_str:
        base_speed = 115
    elif 'GROUP 2' in class_str or 'G2' in class_str:
        base_speed = 108
    elif 'GROUP 3' in class_str or 'G3' in class_str:
        base_speed = 102
    elif 'LISTED' in class_str:
        base_speed = 98
    elif prize > 200000:
        base_speed = 95
    elif prize > 100000:
        base_speed = 90
    
    if form_str.startswith('1'):
        base_speed += 5
    elif form_str.startswith('2') or form_str.startswith('3'):
        base_speed += 2
    
    features['estimated_speed_figure'] = base_speed
    
    stats = runner.get('stats', {}) or {}
    distance_stats = stats.get('distance_stats', {})
    total, wins, seconds, thirds = parse_stats(distance_stats)
    
    is_sprint = distance_m <= 1200
    is_mile = 1200 < distance_m <= 1600
    is_staying = distance_m > 2000
    
    features['is_sprint_race'] = 1 if is_sprint else 0
    features['is_staying_race'] = 1 if is_staying else 0
    features['is_mile_race'] = 1 if is_mile else 0
    
    if is_sprint and running_style_score >= 2:
        features['distance_style_match'] = 1
    elif is_staying and running_style_score <= 1:
        features['distance_style_match'] = 1
    else:
        features['distance_style_match'] = 0
    
    return features


def extract_class_movement_features(runner: dict, race_data: dict) -> dict:
    """
    Extract class movement analysis features.
    Tracks horses stepping up/down in class, weight-for-age adjustments.
    """
    features = {}
    
    race_class = str(race_data.get('class', '')).upper()
    prize_total = float(race_data.get('prize_total', 0) or 0)
    stats = runner.get('stats', {}) or {}
    
    last_class = stats.get('last_race_class', '')
    current_class_level = _parse_class_level(race_class)
    last_class_level = _parse_class_level(str(last_class).upper()) if last_class else current_class_level
    
    class_movement = current_class_level - last_class_level
    
    features['current_class_level'] = current_class_level
    features['class_movement'] = class_movement
    features['is_class_rise'] = 1 if class_movement > 0 else 0
    features['is_class_drop'] = 1 if class_movement < 0 else 0
    
    career_wins = 0
    overall_stats = stats.get('overall_stats')
    if overall_stats:
        _, wins, _, _ = parse_stats(overall_stats)
        career_wins = wins
    
    is_stakes_race = current_class_level >= 7
    is_first_stakes = is_stakes_race and career_wins < 3
    features['is_first_time_stakes'] = 1 if is_first_stakes else 0
    
    weight = parse_weight(runner.get('weight', 0))
    age_raw = runner.get('age', 0)
    age = 0
    if age_raw:
        try:
            if isinstance(age_raw, int):
                age = age_raw
            else:
                age_match = re.search(r'\d+', str(age_raw))
                if age_match:
                    age = int(age_match.group())
        except (ValueError, TypeError):
            age = 0
    sex = str(runner.get('sex', '')).lower()
    
    wfa_penalty = 0
    if age <= 3 and weight >= 57:
        wfa_penalty = 0.08
    elif age == 4 and weight >= 59:
        wfa_penalty = 0.05
    
    if sex in ('mare', 'filly') and weight < 55:
        wfa_penalty -= 0.05
    
    features['wfa_penalty'] = wfa_penalty
    
    features['proven_at_class'] = 1 if career_wins >= 2 and current_class_level <= 6 else 0
    
    return features


def _parse_class_level(class_str: str) -> int:
    """Convert class string to numeric level (1-10)."""
    if 'GROUP 1' in class_str or 'G1' in class_str:
        return 10
    elif 'GROUP 2' in class_str or 'G2' in class_str:
        return 9
    elif 'GROUP 3' in class_str or 'G3' in class_str:
        return 8
    elif 'LISTED' in class_str:
        return 7
    elif 'OPEN' in class_str:
        return 6
    elif 'BM' in class_str or 'BENCHMARK' in class_str:
        for num in ['100', '90', '85', '80', '78', '75', '72', '70', '68', '65', '62', '60', '58']:
            if num in class_str:
                return max(2, int(int(num) / 15))
    elif 'MDN' in class_str or 'MAIDEN' in class_str:
        return 2
    elif 'CL1' in class_str or 'CLASS 1' in class_str:
        return 3
    elif 'CL2' in class_str or 'CLASS 2' in class_str:
        return 4
    elif 'CL3' in class_str or 'CLASS 3' in class_str:
        return 5
    return 3


def extract_trainer_jockey_signals(runner: dict, race_data: dict) -> dict:
    """
    Extract trainer/jockey intent signals.
    Detects gear changes, jockey upgrades, stable form patterns.
    """
    features = {}
    stats = runner.get('stats', {}) or {}
    
    gear = str(runner.get('gear', runner.get('headgear', ''))).upper()
    last_gear = str(stats.get('last_race_gear', '')).upper()
    
    is_blinkers_first_time = 'BLINKERS' in gear and 'BLINKERS' not in last_gear
    has_blinkers = 'BLINKERS' in gear
    has_visor = 'VISOR' in gear
    has_tongue_tie = 'TONGUE' in gear
    has_ear_muffs = 'EAR' in gear
    
    features['is_blinkers_first_time'] = 1 if is_blinkers_first_time else 0
    features['has_blinkers'] = 1 if has_blinkers else 0
    features['has_tongue_tie'] = 1 if has_tongue_tie else 0
    features['gear_change_indicator'] = 1 if is_blinkers_first_time else 0
    
    jockey = str(runner.get('jockey', '')).strip()
    
    elite_jockeys = [
        'mcdonald', 'bowman', 'mcevoy', 'zahra', 'shinn', 'avdulla',
        'rawiller', 'waller', 'purton', 'moreira', 'lane', 'melham',
        'collett', 'dee', 'oliver', 'walker', 'currie', 'baster'
    ]
    
    jockey_lower = jockey.lower()
    is_elite_jockey = any(ej in jockey_lower for ej in elite_jockeys)
    features['is_elite_jockey'] = 1 if is_elite_jockey else 0
    
    last_jockey = str(stats.get('last_race_jockey', '')).strip().lower()
    is_jockey_change = last_jockey and jockey_lower != last_jockey
    is_jockey_upgrade = is_jockey_change and is_elite_jockey
    
    features['is_jockey_change'] = 1 if is_jockey_change else 0
    features['is_jockey_upgrade'] = 1 if is_jockey_upgrade else 0
    
    trainer = str(runner.get('trainer', '')).strip()
    
    elite_trainers = [
        'waller', 'weir', 'waterhouse', 'maher', 'cummings', 'lees',
        'williams', 'moody', 'freedman', 'hawkes', 'hayes', 'price',
        'snowden', 'baker', 'forsman', 'mcevoy', 'pride', 'kavanagh'
    ]
    
    trainer_lower = trainer.lower()
    is_elite_trainer = any(et in trainer_lower for et in elite_trainers)
    features['is_elite_trainer'] = 1 if is_elite_trainer else 0
    
    features['is_elite_connection'] = 1 if is_elite_jockey and is_elite_trainer else 0
    
    days_since_win = int(stats.get('days_since_win', 999) or 999)
    features['days_since_win'] = min(days_since_win, 365)
    features['is_recent_winner'] = 1 if days_since_win <= 30 else 0
    
    return features


def extract_track_bias_features(runner: dict, race_data: dict) -> dict:
    """
    Extract track bias detection features.
    Analyzes barrier draw success patterns, rail position, pace bias.
    """
    features = {}
    
    track = str(race_data.get('track', race_data.get('course', ''))).lower()
    barrier = int(runner.get('draw', runner.get('barrier', 0)) or 0)
    distance_str = str(race_data.get('distance', '0'))
    
    try:
        distance_m = int(distance_str.replace('m', '').strip())
    except ValueError:
        distance_m = 1400
    
    track_barrier_bias = {
        'flemington': 0.05,
        'randwick': -0.02,
        'caulfield': 0.08,
        'rosehill': 0.03,
        'moonee valley': 0.12,
        'geelong': 0.11,
        'doomben': 0.06,
        'eagle farm': 0.02,
        'morphettville': 0.04,
        'sandown': 0.05,
        'ascot': -0.02,
        'warwick farm': 0.07,
        'newcastle': 0.03,
    }
    
    track_bias = 0
    for track_name, bias in track_barrier_bias.items():
        if track_name in track:
            track_bias = bias
            break
    
    features['track_bias_score'] = track_bias
    
    barrier_advantage = 0
    if barrier <= 4:
        barrier_advantage = track_bias * 1.5
    elif barrier <= 8:
        barrier_advantage = 0
    else:
        barrier_advantage = -track_bias * 2 if track_bias > 0 else abs(track_bias) * 0.5
    
    if distance_m <= 1200:
        barrier_advantage *= 1.5
    elif distance_m >= 2000:
        barrier_advantage *= 0.5
    
    features['barrier_advantage'] = barrier_advantage
    
    going = str(race_data.get('going', '')).lower()
    rail_out = 0
    if 'rail out' in going:
        import re
        match = re.search(r'rail out (\d+)', going)
        if match:
            rail_out = int(match.group(1))
    
    features['rail_out_meters'] = rail_out
    
    if rail_out >= 6 and barrier >= 8:
        features['rail_out_advantage'] = 0.05
    elif rail_out >= 6 and barrier <= 3:
        features['rail_out_advantage'] = -0.03
    else:
        features['rail_out_advantage'] = 0
    
    track_pace_bias = {
        'caulfield': 0.1,
        'moonee valley': 0.15,
        'randwick': -0.05,
        'flemington': 0.0,
        'doomben': 0.08,
        'eagle farm': -0.03,
    }
    
    pace_bias = 0
    for track_name, bias in track_pace_bias.items():
        if track_name in track:
            pace_bias = bias
            break
    
    features['track_pace_bias'] = pace_bias
    
    return features


def extract_form_pattern_features(runner: dict, race_data: dict) -> dict:
    """
    Extract advanced form pattern recognition features.
    Detects sequential improvement, winning margins, racing style shifts.
    """
    features = {}
    
    form_str = str(runner.get('form', '')).strip()
    form_details = runner.get('form_details', [])
    
    positions = []
    for char in form_str[:6]:
        if char.isdigit():
            positions.append(int(char))
        elif char == '0':
            positions.append(10)
    
    is_improving = False
    improvement_score = 0
    
    if len(positions) >= 3:
        recent = positions[:3]
        if recent[0] < recent[1] < recent[2]:
            is_improving = True
            improvement_score = 2
        elif recent[0] < recent[1]:
            is_improving = True
            improvement_score = 1
        elif recent[0] > recent[1] > recent[2]:
            improvement_score = -2
        elif recent[0] > recent[1]:
            improvement_score = -1
    
    features['is_improving'] = 1 if is_improving else 0
    features['improvement_score'] = improvement_score
    
    if len(positions) >= 4:
        avg_pos = sum(positions) / len(positions)
        variance = sum((p - avg_pos) ** 2 for p in positions) / len(positions)
        features['form_consistency_score'] = max(0, 10 - variance)
        features['average_position'] = avg_pos
    else:
        features['form_consistency_score'] = 5
        features['average_position'] = 5
    
    margins = []
    if form_details and isinstance(form_details, list):
        for run in form_details[:5]:
            if isinstance(run, dict):
                margin = run.get('winning_margin', run.get('margin', 0))
                if margin:
                    try:
                        margins.append(float(margin))
                    except (ValueError, TypeError):
                        pass
    
    if margins:
        avg_margin = sum(margins) / len(margins)
        features['avg_winning_margin'] = avg_margin
        features['has_dominant_win'] = 1 if any(m >= 3.0 for m in margins) else 0
    else:
        features['avg_winning_margin'] = 0
        features['has_dominant_win'] = 0
    
    win_positions = [i for i, p in enumerate(positions) if p == 1]
    
    if len(win_positions) >= 2:
        features['is_in_form_cycle'] = 1
        features['wins_in_form'] = len(win_positions)
    elif len(win_positions) == 1 and win_positions[0] <= 1:
        features['is_in_form_cycle'] = 1
        features['wins_in_form'] = 1
    else:
        features['is_in_form_cycle'] = 0
        features['wins_in_form'] = 0
    
    ratings = []
    if form_details and isinstance(form_details, list):
        for run in form_details[:5]:
            if isinstance(run, dict):
                rating = run.get('or', run.get('rating', 0))
                if rating:
                    try:
                        ratings.append(int(rating))
                    except (ValueError, TypeError):
                        pass
    
    if len(ratings) >= 2:
        rating_change = ratings[0] - ratings[-1]
        features['rating_progression'] = rating_change
        features['is_rating_rise'] = 1 if rating_change > 3 else 0
    else:
        features['rating_progression'] = 0
        features['is_rating_rise'] = 0
    
    return features


def extract_market_intelligence_features(runner: dict, all_runners: list, race_data: dict) -> dict:
    """
    Extract market intelligence features.
    Analyzes odds movements, multi-bookmaker consensus, value deviation.
    """
    features = {}
    
    current_odds = parse_odds(runner, use_default=False)
    features['current_odds'] = current_odds
    
    opening_odds = runner.get('opening_odds', runner.get('morning_price', current_odds))
    if opening_odds:
        try:
            opening_odds = float(str(opening_odds).replace('$', '').strip())
        except (ValueError, TypeError):
            opening_odds = current_odds
    else:
        opening_odds = current_odds
    
    features['opening_odds'] = opening_odds
    
    # Task 03: gated by STRIDE_MOVEMENT_FEATURES_LIVE — served as 0 by default
    # (0 in training; real movement is post-tip-time data, see task 14).
    if opening_odds > 0 and current_odds > 0 and _movement_features_live():
        odds_movement = (opening_odds - current_odds) / opening_odds * 100
        features['odds_movement_pct'] = odds_movement
        
        is_steam_move = odds_movement > 20
        is_drift = odds_movement < -20
        
        features['is_steam_move'] = 1 if is_steam_move else 0
        features['is_drift'] = 1 if is_drift else 0
    else:
        features['odds_movement_pct'] = 0
        features['is_steam_move'] = 0
        features['is_drift'] = 0
    
    odds_array = runner.get('odds', [])
    bookmaker_odds = []
    
    if isinstance(odds_array, list):
        for bookie in odds_array:
            if isinstance(bookie, dict):
                win_odds = bookie.get('win_odds', bookie.get('odds'))
                if win_odds:
                    try:
                        bookmaker_odds.append(float(str(win_odds).replace('$', '').strip()))
                    except (ValueError, TypeError):
                        pass
            elif isinstance(bookie, (int, float)):
                bookmaker_odds.append(float(bookie))
    
    if len(bookmaker_odds) >= 2:
        avg_odds = sum(bookmaker_odds) / len(bookmaker_odds)
        odds_variance = sum((o - avg_odds) ** 2 for o in bookmaker_odds) / len(bookmaker_odds)
        
        features['bookmaker_consensus'] = 1 if odds_variance < 0.5 else 0
        features['avg_bookmaker_odds'] = avg_odds
        features['odds_variance'] = odds_variance
    else:
        features['bookmaker_consensus'] = 0
        features['avg_bookmaker_odds'] = current_odds
        features['odds_variance'] = 0
    
    all_implied_probs = []
    for r in all_runners:
        r_odds = parse_odds(r, use_default=False)
        if r_odds > 1:
            all_implied_probs.append(1 / r_odds)
    
    if all_implied_probs:
        avg_prob = sum(all_implied_probs) / len(all_implied_probs)
        horse_prob = 1 / current_odds if current_odds > 1 else 0
        
        features['field_avg_probability'] = avg_prob * 100
        features['probability_vs_field'] = (horse_prob - avg_prob) * 100
    else:
        features['field_avg_probability'] = 10
        features['probability_vs_field'] = 0
    
    _field_real_odds = [parse_odds(r, use_default=False) for r in all_runners]
    _field_real_odds = [o for o in _field_real_odds if o > 1]
    min_odds_in_field = min(_field_real_odds) if _field_real_odds else current_odds
    
    is_favorite = current_odds <= min_odds_in_field * 1.1
    is_short_favorite = is_favorite and current_odds < 3.0
    is_long_odds = current_odds > 10.0
    
    features['is_favorite'] = 1 if is_favorite else 0
    features['is_short_favorite'] = 1 if is_short_favorite else 0
    features['is_long_odds'] = 1 if is_long_odds else 0
    
    overround = sum(all_implied_probs) if all_implied_probs else 1.0
    features['market_overround'] = overround * 100
    
    return features



def analyze_beaten_margins(runner: dict) -> dict:
    """
    Parse beaten margin strings and identify unlucky losers.
    Margin format: SHD=0.05L, HD=0.1L, NK=0.2L, numeric+L=lengths
    """
    result = {
        'avg_beaten_margin': 0,
        'narrow_defeat_count': 0,
        'is_unlucky_loser': False,
        'margin_consistency': 0.5,
        'due_for_win_adjustment': 1.0,
        'has_dominant_win': False
    }
    
    form_string = runner.get('form', '') or ''
    form_details = runner.get('formDetails', []) or []
    
    def parse_margin(margin_str):
        if not margin_str:
            return 0
        margin_str = str(margin_str).upper().strip()
        if margin_str in ['W', 'WON', '1ST', 'WINNER']:
            return 0
        if margin_str in ['SHD', 'SHORT HEAD']:
            return 0.05
        if margin_str in ['HD', 'HEAD']:
            return 0.1
        if margin_str in ['NK', 'NECK']:
            return 0.2
        if margin_str in ['HNK', 'HALF NECK']:
            return 0.25
        try:
            clean = margin_str.replace('L', '').replace('LEN', '').strip()
            return float(clean)
        except:
            return 2.0
    
    margins = []
    winning_margins = []
    
    for detail in form_details[:6]:
        pos = detail.get('position', detail.get('pos', 99))
        margin = detail.get('beaten_margin', detail.get('margin', ''))
        
        if pos == 1:
            win_margin = detail.get('winning_margin', detail.get('margin', 0))
            try:
                wm = float(str(win_margin).replace('L', '').replace('LEN', '').strip())
                winning_margins.append(wm)
            except:
                pass
        elif pos and pos <= 4:
            m = parse_margin(margin)
            margins.append(m)
    
    if margins:
        result['avg_beaten_margin'] = sum(margins) / len(margins)
        result['narrow_defeat_count'] = sum(1 for m in margins if m < 1.0)
        result['margin_consistency'] = 1.0 - min(1.0, (max(margins) - min(margins)) / 5.0) if len(margins) > 1 else 0.5
    
    if result['narrow_defeat_count'] >= 2:
        result['is_unlucky_loser'] = True
        result['due_for_win_adjustment'] = 1.08
    
    if winning_margins and max(winning_margins) >= 3.0:
        result['has_dominant_win'] = True
    
    return result


def analyze_class_weighted_margins(runner: dict, current_class: str = '', current_race_class_level: int = 50) -> dict:
    """
    Analyze beaten margins with class weighting.

    A horse beaten 2L at Group 1 (class 100) is better than beaten 2L at BM58 (class 52).
    Weights margins based on class level differential.
    """
    result = {
        'class_weighted_margin_score': 0.0,
        'class_drop_with_margin_value': 0.0,
        'class_transition_adjustment': 1.0,
        'best_form_class': 0,
        'form_at_higher_class': False,
        'class_margin_insight': '',
        'margin_class_details': []
    }
    
    form_details = runner.get('formDetails', []) or []
    if not form_details:
        return result
    
    def get_class_level(class_str, prize=None):
        if ADVANCED_FEATURES_AVAILABLE:
            return parse_class_level(class_str, prize)
        return 50
    
    margin_class_data = []
    best_class_seen = 0
    
    for detail in form_details[:6]:
        pos = detail.get('position', detail.get('pos', 99))
        margin_str = detail.get('beaten_margin', detail.get('margin', ''))
        race_class = detail.get('race_class', detail.get('class', ''))
        race_name = detail.get('race_name', detail.get('race', ''))
        prize = detail.get('prize', detail.get('prize_money', 0))
        
        class_level = get_class_level(race_class or race_name, prize)
        best_class_seen = max(best_class_seen, class_level)
        
        if pos == 1:
            margin = 0
        else:
            margin = 2.0
            if margin_str:
                margin_str_upper = str(margin_str).upper().strip()
                if margin_str_upper in ['W', 'WON', '1ST', 'WINNER']:
                    margin = 0
                elif margin_str_upper in ['SHD', 'SHORT HEAD']:
                    margin = 0.05
                elif margin_str_upper in ['HD', 'HEAD']:
                    margin = 0.1
                elif margin_str_upper in ['NK', 'NECK']:
                    margin = 0.2
                else:
                    try:
                        clean = margin_str_upper.replace('L', '').replace('LEN', '').strip()
                        margin = float(clean)
                    except:
                        margin = 2.0
        
        margin_class_data.append({
            'position': pos,
            'margin': margin,
            'class_level': class_level,
            'race_class': race_class,
            'class_diff_to_today': class_level - current_race_class_level
        })
    
    result['best_form_class'] = best_class_seen
    result['form_at_higher_class'] = best_class_seen > current_race_class_level + 5
    result['margin_class_details'] = margin_class_data
    
    # Higher class = margin worth more (beaten 2L at G1 is better than 2L at BM58)
    weighted_scores = []
    for run in margin_class_data:
        if run['position'] <= 4:  # Only count placed runs
            class_multiplier = run['class_level'] / 50.0  # Normalize around BM60
            if run['margin'] <= 2.0:
                # Score: smaller margin = higher score, weighted by class
                margin_score = max(0, (2.0 - run['margin'])) * class_multiplier
                weighted_scores.append(margin_score)
    
    if weighted_scores:
        result['class_weighted_margin_score'] = sum(weighted_scores) / len(weighted_scores)
    
    class_drop_value = 0.0
    margin_at_higher_class = []
    
    for run in margin_class_data[:3]:  # Recent 3 runs
        if run['class_diff_to_today'] >= 5:  # Was at higher class
            if run['margin'] <= 3.0:  # And was close up
                # The more class drop + smaller margin = more value
                class_value = run['class_diff_to_today'] / 20.0  # e.g., 10 level drop = 0.5
                margin_value = max(0, (3.0 - run['margin'])) / 3.0  # e.g., 1L = 0.67
                class_drop_value += class_value * margin_value
                margin_at_higher_class.append({
                    'margin': run['margin'],
                    'class': run['race_class'],
                    'class_drop': run['class_diff_to_today']
                })
    
    result['class_drop_with_margin_value'] = min(0.5, class_drop_value)  # Cap at 0.5
    
    if result['class_drop_with_margin_value'] > 0.1:
        result['class_transition_adjustment'] = 1.0 + (result['class_drop_with_margin_value'] * 0.3)
    
    if margin_at_higher_class:
        best = margin_at_higher_class[0]
        margin_text = 'narrowly beaten' if best['margin'] < 1.0 else f"beaten {best['margin']:.1f}L"
        result['class_margin_insight'] = f"{margin_text} at {best['class']} level, drops {best['class_drop']} points in grade"
    elif result['form_at_higher_class']:
        result['class_margin_insight'] = f"Has contested class {best_class_seen}, drops to {current_race_class_level} level"
    
    return result


def analyze_market_movement(runner: dict, field_runners: list = None) -> dict:
    """
    Analyze market movement for insights generation.
    Wraps market_analysis module functions for form insights.
    """
    result = {
        'steam_drift_pct': 0.0,
        'movement_category': 'steady',
        'is_sharp_money': False,
        'is_longshot_steamer': False,
        'opening_odds': None,
        'current_odds': None,
        'market_insight': '',
        'market_adjustment': 1.0
    }
    
    opening = runner.get('opening_odds') or runner.get('openingOdds')
    current = runner.get('win_odds') or runner.get('odds') or runner.get('winOdds')
    
    if opening is None:
        opening = current
    
    if opening is None or current is None:
        return result
    
    try:
        opening = float(opening)
        current = float(current)
    except (ValueError, TypeError):
        return result
    
    result['opening_odds'] = opening
    result['current_odds'] = current
    
    if opening <= 1.0 or current <= 1.0:
        return result
    
    if MARKET_ANALYSIS_AVAILABLE:
        pct_change, category = calculate_steam_drift(opening, current)
        result['steam_drift_pct'] = pct_change
        result['movement_category'] = category
        
        field_avg = 0.0
        if field_runners:
            try:
                movements = []
                for r in field_runners:
                    o = r.get('opening_odds') or r.get('openingOdds') or r.get('win_odds')
                    c = r.get('win_odds') or r.get('odds') or r.get('winOdds')
                    if o and c:
                        o, c = float(o), float(c)
                        if o > 1 and c > 1:
                            movements.append(((o - c) / o) * 100)
                field_avg = sum(movements) / len(movements) if movements else 0
            except:
                pass
        
        result['is_sharp_money'] = detect_sharp_money(opening, current, field_avg)
        
        if current >= 10.0 and pct_change >= 15:
            result['is_longshot_steamer'] = True
    else:
        pct_change = ((opening - current) / opening) * 100
        result['steam_drift_pct'] = round(pct_change, 2)
        
        if pct_change >= 25:
            result['movement_category'] = 'strong_steam'
        elif pct_change >= 10:
            result['movement_category'] = 'steam'
        elif pct_change <= -25:
            result['movement_category'] = 'strong_drift'
        elif pct_change <= -10:
            result['movement_category'] = 'drift'
        else:
            result['movement_category'] = 'steady'
        
        if pct_change >= 15:
            result['is_sharp_money'] = True
    
    if result['movement_category'] == 'strong_steam':
        result['market_adjustment'] = 1.12
    elif result['movement_category'] == 'steam':
        result['market_adjustment'] = 1.06
    elif result['movement_category'] == 'strong_drift':
        result['market_adjustment'] = 0.90
    elif result['movement_category'] == 'drift':
        result['market_adjustment'] = 0.95
    
    if result['is_sharp_money']:
        result['market_adjustment'] *= 1.05
    
    if result['movement_category'] == 'strong_steam':
        result['market_insight'] = f"Strong market support - odds shortened from ${opening:.2f} to ${current:.2f} ({result['steam_drift_pct']:.0f}%)"
    elif result['movement_category'] == 'steam':
        result['market_insight'] = f"Firming in betting - ${opening:.2f} to ${current:.2f}"
    elif result['movement_category'] == 'strong_drift':
        result['market_insight'] = f"Drifting notably - opened ${opening:.2f}, now ${current:.2f}"
    elif result['movement_category'] == 'drift':
        result['market_insight'] = f"Easing in market - ${opening:.2f} to ${current:.2f}"
    
    if result['is_sharp_money'] and result['movement_category'] in ['steam', 'strong_steam']:
        result['market_insight'] += " - sharp money detected"
    
    if result['is_longshot_steamer']:
        result['market_insight'] = f"Significant plunge at big odds - ${opening:.2f} to ${current:.2f}"
    
    return result


_sectional_cache = {}
_sectional_cache_time = 0

def get_stored_sectionals(horse_name: str, limit: int = 5) -> list:
    """Look up stored sectional times from the database for a horse."""
    global _sectional_cache, _sectional_cache_time
    
    now = time.time()
    if now - _sectional_cache_time > 1800:
        _sectional_cache = {}
        _sectional_cache_time = now
    
    cache_key = horse_name.lower().strip()
    if cache_key in _sectional_cache:
        return _sectional_cache[cache_key]
    
    try:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            return []
        
        conn = get_db_connection(db_url)
        if not conn:
            return []
        cur = conn.cursor()
        cur.execute("""
            SELECT race_date, track, race_number, distance_m,
                   last_200m_speed, last_200m_time,
                   last_400m_speed, last_400m_time,
                   last_600m_speed, last_600m_time,
                   last_800m_speed, last_800m_time,
                   finishing_burst, avg_speed, splits_json, track_config
            FROM sectional_times
            WHERE LOWER(horse_name) = LOWER(%s)
            ORDER BY race_date DESC
            LIMIT %s
        """, (horse_name.strip(), limit))
        
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        results = []
        for row in rows:
            results.append({
                'race_date': row[0],
                'track': row[1],
                'race_number': row[2],
                'distance_m': row[3],
                'last_200m_speed': row[4],
                'last_200m_time': row[5],
                'last_400m_speed': row[6],
                'last_400m_time': row[7],
                'last_600m_speed': row[8],
                'last_600m_time': row[9],
                'last_800m_speed': row[10],
                'last_800m_time': row[11],
                'finishing_burst': row[12],
                'avg_speed': row[13],
                'splits_json': row[14],
                'track_config': row[15],
            })
        
        _sectional_cache[cache_key] = results
        return results
    except Exception as e:
        print(f"[Sectionals] DB lookup error for {horse_name}: {e}", file=sys.stderr)
        return []


import statistics as _statistics

_rowlands_par_cache = {}
_rowlands_par_cache_time = 0

def _normalize_going(track_config):
    if not track_config or str(track_config).strip() == '' or str(track_config).lower() == 'unknown':
        return 'unknown'
    tc = str(track_config)
    if 'Good' in tc or 'good' in tc:
        return 'good'
    if 'Soft' in tc or 'soft' in tc:
        return 'soft'
    if 'Heavy' in tc or 'heavy' in tc:
        return 'heavy'
    if 'Synthetic' in tc or 'synthetic' in tc or 'Poly' in tc or 'poly' in tc:
        return 'synthetic'
    return 'unknown'

def build_rowlands_par_table():
    global _rowlands_par_cache, _rowlands_par_cache_time

    now = time.time()
    if _rowlands_par_cache and now - _rowlands_par_cache_time < 3600:
        return _rowlands_par_cache

    try:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            return {}

        conn = get_db_connection(db_url)
        if not conn:
            return {}
        cur = conn.cursor()
        cur.execute("""
            SELECT track, distance_m, track_config, last_200m_speed, avg_speed
            FROM sectional_times
            WHERE last_200m_speed > 0 AND avg_speed > 0
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        from collections import defaultdict
        groups_full = defaultdict(list)
        groups_track_dist = defaultdict(list)
        groups_dist = defaultdict(list)

        for row in rows:
            track, distance_m, track_config, last_200m_speed, avg_speed = row
            finishing_speed_pct = (last_200m_speed / avg_speed) * 100
            going = _normalize_going(track_config)
            groups_full[(track, distance_m, going)].append(finishing_speed_pct)
            groups_track_dist[(track, distance_m)].append(finishing_speed_pct)
            groups_dist[distance_m].append(finishing_speed_pct)

        par_table = {}

        for key, values in groups_full.items():
            if len(values) >= 5:
                par_table[key] = _statistics.median(values)

        for key, values in groups_full.items():
            if key not in par_table:
                track, distance_m, going = key
                td_key = (track, distance_m)
                if td_key in groups_track_dist and len(groups_track_dist[td_key]) >= 5:
                    par_table[key] = _statistics.median(groups_track_dist[td_key])
                elif distance_m in groups_dist and len(groups_dist[distance_m]) >= 5:
                    par_table[key] = _statistics.median(groups_dist[distance_m])

        for td_key, values in groups_track_dist.items():
            if len(values) >= 5:
                track, distance_m = td_key
                sentinel = (track, distance_m, '__track_dist__')
                par_table[sentinel] = _statistics.median(values)

        for d_key, values in groups_dist.items():
            if len(values) >= 5:
                sentinel = ('__any__', d_key, '__dist__')
                par_table[sentinel] = _statistics.median(values)

        _rowlands_par_cache = par_table
        _rowlands_par_cache_time = now
        return par_table
    except Exception as e:
        print(f"[Rowlands] Par table build error: {e}", file=sys.stderr)
        return _rowlands_par_cache if _rowlands_par_cache else {}


def _lookup_par(par_table, track, distance_m, going_normalized):
    key = (track, distance_m, going_normalized)
    if key in par_table:
        return par_table[key]
    td_key = (track, distance_m, '__track_dist__')
    if td_key in par_table:
        return par_table[td_key]
    d_key = ('__any__', distance_m, '__dist__')
    if d_key in par_table:
        return par_table[d_key]
    return 100.0


def calculate_rowlands_adjustment(horse_name, track, distance_m, track_config):
    result = {
        'rowlands_finishing_speed_pct': 100.0,
        'rowlands_par': 100.0,
        'rowlands_deviation': 0.0,
        'rowlands_adjustment_lbs': 0.0,
        'rowlands_runs_analyzed': 0,
    }

    if not horse_name:
        return result

    stored = get_stored_sectionals(horse_name, limit=10)
    if not stored:
        return result

    recency_weights = [0.30, 0.22, 0.16, 0.12, 0.08, 0.05, 0.03, 0.02, 0.01, 0.01]

    valid_records = []
    for sec in stored:
        l200 = sec.get('last_200m_speed')
        avg_s = sec.get('avg_speed')
        if l200 and avg_s and l200 > 0 and avg_s > 0:
            valid_records.append((l200 / avg_s) * 100)

    if not valid_records:
        return result

    weighted_sum = 0.0
    weight_total = 0.0
    for i, fsp in enumerate(valid_records):
        w = recency_weights[i] if i < len(recency_weights) else 0.01
        weighted_sum += fsp * w
        weight_total += w

    horse_avg_fsp = weighted_sum / weight_total if weight_total > 0 else 100.0

    par_table = build_rowlands_par_table()
    going_normalized = _normalize_going(track_config)
    par = _lookup_par(par_table, track, distance_m, going_normalized)

    deviation = par - horse_avg_fsp

    try:
        distance_m_val = int(distance_m) if distance_m else 1200
    except (ValueError, TypeError):
        distance_m_val = 1200

    raw_adjustment = 1.25 * (200 / max(distance_m_val, 200)) * (deviation ** 2)

    if deviation > 0:
        adjustment = -raw_adjustment
    else:
        adjustment = raw_adjustment

    result['rowlands_finishing_speed_pct'] = round(horse_avg_fsp, 3)
    result['rowlands_par'] = round(par, 3)
    result['rowlands_deviation'] = round(deviation, 3)
    result['rowlands_adjustment_lbs'] = round(adjustment, 3)
    result['rowlands_runs_analyzed'] = len(valid_records)

    return result


_pace_profile_cache = {}
_pace_profile_cache_time = 0


def _parse_splits_to_sections(splits_json_data):
    try:
        if not splits_json_data:
            return []
        data = splits_json_data
        if isinstance(data, str):
            data = json.loads(data)

        sections = []
        if isinstance(data, dict):
            for key, val in data.items():
                if not isinstance(val, dict):
                    continue
                try:
                    mark = int(str(key).replace('m', '').strip())
                except (ValueError, TypeError):
                    continue
                speed = val.get('speed_ms')
                t = val.get('time')
                pos = val.get('position')
                if speed is not None:
                    try:
                        speed = float(speed)
                    except (ValueError, TypeError):
                        speed = 0.0
                else:
                    speed = 0.0
                if t is not None:
                    try:
                        t = float(t)
                    except (ValueError, TypeError):
                        t = 0.0
                else:
                    t = 0.0
                if pos is not None:
                    try:
                        pos = int(pos)
                    except (ValueError, TypeError):
                        pos = None
                sections.append({'mark_m': mark, 'speed_ms': speed, 'time_s': t, 'position': pos})
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                mark = item.get('mark', 0)
                try:
                    mark = int(mark)
                except (ValueError, TypeError):
                    mark = 0
                speed = item.get('speed_ms', 0.0)
                try:
                    speed = float(speed)
                except (ValueError, TypeError):
                    speed = 0.0
                t = item.get('split_time', 0.0)
                try:
                    t = float(t)
                except (ValueError, TypeError):
                    t = 0.0
                sections.append({'mark_m': mark, 'speed_ms': speed, 'time_s': t, 'position': None})

        sections.sort(key=lambda s: s['mark_m'])
        return sections
    except Exception:
        return []


def _normalize_pace_phases(sections, distance_m):
    if not sections or len(sections) < 3:
        return {'early': {'speed_pct': 100.0, 'position': None}, 'mid': {'speed_pct': 100.0, 'position': None}, 'home': {'speed_pct': 100.0, 'position': None}}

    speeds = [s['speed_ms'] for s in sections if s['speed_ms'] > 0]
    if not speeds:
        return {'early': {'speed_pct': 100.0, 'position': None}, 'mid': {'speed_pct': 100.0, 'position': None}, 'home': {'speed_pct': 100.0, 'position': None}}

    overall_avg = sum(speeds) / len(speeds)
    if overall_avg <= 0:
        return {'early': {'speed_pct': 100.0, 'position': None}, 'mid': {'speed_pct': 100.0, 'position': None}, 'home': {'speed_pct': 100.0, 'position': None}}

    effective_dist = distance_m if distance_m and distance_m > 0 else max(s['mark_m'] for s in sections if s['mark_m'] is not None)
    if not effective_dist or effective_dist <= 0:
        effective_dist = len(sections) * 200

    early_boundary = effective_dist / 3
    home_boundary = effective_dist * 2 / 3

    early_sections = []
    mid_sections = []
    home_sections = []
    for s in sections:
        mark = s.get('mark_m', 0) or 0
        if mark < early_boundary:
            early_sections.append(s)
        elif mark < home_boundary:
            mid_sections.append(s)
        else:
            home_sections.append(s)

    if not early_sections:
        early_sections = [sections[0]]
    if not home_sections:
        home_sections = [sections[-1]]
    if not mid_sections and len(sections) >= 3:
        mid_sections = [sections[len(sections) // 2]]

    phase_slices = {
        'early': early_sections,
        'mid': mid_sections,
        'home': home_sections,
    }

    result = {}
    for phase, slc in phase_slices.items():
        if not slc:
            result[phase] = {'speed_pct': 100.0, 'position': None}
            continue
        speed_pcts = [(s['speed_ms'] / overall_avg * 100) for s in slc if s['speed_ms'] > 0]
        avg_speed_pct = sum(speed_pcts) / len(speed_pcts) if speed_pcts else 100.0
        positions = [s['position'] for s in slc if s['position'] is not None]
        avg_pos = sum(positions) / len(positions) if positions else None
        result[phase] = {'speed_pct': avg_speed_pct, 'position': avg_pos}

    return result


def build_horse_pace_profile(horse_name, limit=10):
    global _pace_profile_cache, _pace_profile_cache_time

    default_profile = {
        'has_profile': False,
        'runs_analyzed': 0,
        'early_median': 100.0,
        'mid_median': 100.0,
        'home_median': 100.0,
        'early_std': 0.0,
        'mid_std': 0.0,
        'home_std': 0.0,
        'consistency_score': 50.0,
        'red_zone_threshold': 999.0,
        'red_zone_penalty': 0.0,
        'preferred_position_early': None,
        'preferred_position_mid': None,
        'position_pace_correlation': None,
        'optimal_early_position': None,
    }

    if not horse_name:
        return default_profile

    cache_key = horse_name.lower().strip()

    now = time.time()
    if now - _pace_profile_cache_time > 3600:
        _pace_profile_cache = {}
        _pace_profile_cache_time = now

    if cache_key in _pace_profile_cache:
        return _pace_profile_cache[cache_key]

    try:
        stored = get_stored_sectionals(horse_name, limit=limit)
        if not stored:
            _pace_profile_cache[cache_key] = default_profile
            return default_profile

        early_speed_pcts = []
        mid_speed_pcts = []
        home_speed_pcts = []
        early_positions = []
        mid_positions = []
        home_positions = []
        run_efforts = []

        for sec in stored:
            splits_raw = sec.get('splits_json')
            if not splits_raw:
                continue
            sections = _parse_splits_to_sections(splits_raw)
            if len(sections) < 3:
                continue

            valid_speeds = [s['speed_ms'] for s in sections if s['speed_ms'] > 0]
            if not valid_speeds:
                continue
            run_avg = sum(valid_speeds) / len(valid_speeds)
            if run_avg <= 0:
                continue

            dist = sec.get('distance_m', 0)
            try:
                dist = int(dist) if dist else 0
            except (ValueError, TypeError):
                dist = 0

            phases = _normalize_pace_phases(sections, dist)

            early_speed_pcts.append(phases['early']['speed_pct'])
            mid_speed_pcts.append(phases['mid']['speed_pct'])
            home_speed_pcts.append(phases['home']['speed_pct'])

            if phases['early']['position'] is not None:
                early_positions.append(phases['early']['position'])
            if phases['mid']['position'] is not None:
                mid_positions.append(phases['mid']['position'])
            if phases['home']['position'] is not None:
                home_positions.append(phases['home']['position'])

            effective_dist = dist if dist and dist > 0 else max((s['mark_m'] for s in sections if s.get('mark_m') is not None), default=len(sections) * 200)
            home_boundary = effective_dist * 2 / 3
            early_mid_sections = [s for s in sections if (s.get('mark_m', 0) or 0) < home_boundary]
            if not early_mid_sections:
                early_mid_sections = sections[:-1] if len(sections) > 1 else sections
            early_mid_speed_pcts = [(s['speed_ms'] / run_avg * 100) for s in early_mid_sections if s['speed_ms'] > 0]
            max_early_mid = max(early_mid_speed_pcts) if early_mid_speed_pcts else 100.0

            run_efforts.append({
                'max_section_speed_pct': max_early_mid,
                'finishing_speed_pct': phases['home']['speed_pct'],
                'early_position': phases['early']['position'],
            })

        if len(early_speed_pcts) < 3:
            _pace_profile_cache[cache_key] = default_profile
            return default_profile

        early_med = _statistics.median(early_speed_pcts)
        mid_med = _statistics.median(mid_speed_pcts)
        home_med = _statistics.median(home_speed_pcts)

        early_s = _statistics.pstdev(early_speed_pcts) if len(early_speed_pcts) >= 2 else 0.0
        mid_s = _statistics.pstdev(mid_speed_pcts) if len(mid_speed_pcts) >= 2 else 0.0
        home_s = _statistics.pstdev(home_speed_pcts) if len(home_speed_pcts) >= 2 else 0.0

        consistency = 100.0 - (early_s + mid_s + home_s) / 3.0
        consistency = max(0.0, min(100.0, consistency))

        pref_pos_early = _statistics.median(early_positions) if early_positions else None
        pref_pos_mid = _statistics.median(mid_positions) if mid_positions else None

        red_zone_threshold = 999.0
        red_zone_penalty = 0.0
        if len(run_efforts) >= 4:
            sorted_efforts = sorted(run_efforts, key=lambda r: r['max_section_speed_pct'])
            mid_idx = len(sorted_efforts) // 2
            bottom_half = sorted_efforts[:mid_idx]
            top_half = sorted_efforts[mid_idx:]
            if top_half and bottom_half:
                top_home_avg = sum(r['finishing_speed_pct'] for r in top_half) / len(top_half)
                bottom_home_avg = sum(r['finishing_speed_pct'] for r in bottom_half) / len(bottom_half)
                if bottom_home_avg - top_home_avg > 1.0:
                    red_zone_threshold = top_half[0]['max_section_speed_pct']
                    red_zone_penalty = bottom_home_avg - top_home_avg

        pos_pace_corr = None
        optimal_early_pos = None
        valid_pos_runs = [r for r in run_efforts if r['early_position'] is not None]
        if len(valid_pos_runs) >= 3:
            x_vals = [r['early_position'] for r in valid_pos_runs]
            y_vals = [r['finishing_speed_pct'] for r in valid_pos_runs]
            mean_x = sum(x_vals) / len(x_vals)
            mean_y = sum(y_vals) / len(y_vals)
            numerator = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x_vals, y_vals))
            denom_x = sum((xi - mean_x) ** 2 for xi in x_vals) ** 0.5
            denom_y = sum((yi - mean_y) ** 2 for yi in y_vals) ** 0.5
            pos_pace_corr = numerator / (denom_x * denom_y) if denom_x * denom_y > 0 else 0.0

            best_run = max(valid_pos_runs, key=lambda r: r['finishing_speed_pct'])
            optimal_early_pos = best_run['early_position']

        profile = {
            'has_profile': True,
            'runs_analyzed': len(early_speed_pcts),
            'early_median': round(early_med, 3),
            'mid_median': round(mid_med, 3),
            'home_median': round(home_med, 3),
            'early_std': round(early_s, 3),
            'mid_std': round(mid_s, 3),
            'home_std': round(home_s, 3),
            'consistency_score': round(consistency, 2),
            'red_zone_threshold': round(red_zone_threshold, 3),
            'red_zone_penalty': round(red_zone_penalty, 3),
            'preferred_position_early': round(pref_pos_early, 1) if pref_pos_early is not None else None,
            'preferred_position_mid': round(pref_pos_mid, 1) if pref_pos_mid is not None else None,
            'position_pace_correlation': round(pos_pace_corr, 3) if pos_pace_corr is not None else None,
            'optimal_early_position': round(optimal_early_pos, 1) if optimal_early_pos is not None else None,
        }

        _pace_profile_cache[cache_key] = profile
        return profile
    except Exception as e:
        print(f"[PaceProfile] Error building profile for {horse_name}: {e}", file=sys.stderr)
        _pace_profile_cache[cache_key] = default_profile
        return default_profile


def calculate_pace_scenario_impact(horse_name, pace_pressure_rating, speed_horse_count, distance_m):
    default_result = {
        'pace_profile_consistency': 50.0,
        'pace_red_zone_margin': 10.0,
        'pace_scenario_compatibility': 50.0,
        'pace_position_impact': 0.0,
        'pace_deviation_penalty': 0.0,
        'pace_profile_runs': 0,
    }

    if not horse_name:
        return default_result

    try:
        profile = build_horse_pace_profile(horse_name)
        if not profile.get('has_profile'):
            return default_result

        try:
            pace_pressure_rating = float(pace_pressure_rating)
        except (ValueError, TypeError):
            pace_pressure_rating = 5.0

        expected_early_pct = 95.0 + pace_pressure_rating * 1.5

        pace_deviation = abs(expected_early_pct - profile['early_median'])
        pace_compatibility = max(0.0, 100.0 - pace_deviation * 8.0)

        red_zone_margin = profile['red_zone_threshold'] - expected_early_pct
        red_zone_risk = max(0.0, -red_zone_margin) * profile['red_zone_penalty'] / 100.0

        position_impact = 0.0
        pos_corr = profile.get('position_pace_correlation')
        pref_early = profile.get('preferred_position_early')
        if pos_corr is not None and pref_early is not None:
            if pace_pressure_rating >= 7:
                if pos_corr < -0.2:
                    position_impact = abs(pos_corr) * 0.05
                elif pos_corr > 0.2:
                    position_impact = -pos_corr * 0.05
            elif pace_pressure_rating <= 3:
                if pos_corr > 0.2:
                    position_impact = pos_corr * 0.05
                elif pos_corr < -0.2:
                    position_impact = pos_corr * 0.03

        deviation_penalty = -pace_deviation * 0.003 - red_zone_risk * 0.5
        deviation_penalty = max(-0.15, min(0.10, deviation_penalty))

        return {
            'pace_profile_consistency': profile['consistency_score'],
            'pace_red_zone_margin': round(red_zone_margin, 3),
            'pace_scenario_compatibility': round(pace_compatibility, 2),
            'pace_position_impact': round(position_impact, 4),
            'pace_deviation_penalty': round(deviation_penalty, 4),
            'pace_profile_runs': profile['runs_analyzed'],
        }
    except Exception as e:
        print(f"[PaceImpact] Error for {horse_name}: {e}", file=sys.stderr)
        return default_result


_section_par_cache = {}
_section_par_cache_time = 0
_section_par_failed = False

def build_section_par_table():
    global _section_par_cache, _section_par_cache_time, _section_par_failed

    now = time.time()
    if _section_par_cache and now - _section_par_cache_time < 3600:
        return _section_par_cache

    # If we already failed this session, don't retry every call
    if _section_par_failed:
        return _section_par_cache if _section_par_cache else {}

    try:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            return {}

        rows = None
        for attempt in range(3):
            try:
                conn = psycopg2.connect(
                    db_url,
                    connect_timeout=30,
                    application_name='wizbet_mc_api',
                    gssencmode='disable',
                )
                cur = conn.cursor()
                cur.execute("SET statement_timeout = '30s'")
                cur.execute("""
                    SELECT track, distance_m, track_config, splits_json
                    FROM sectional_times
                    WHERE splits_json IS NOT NULL AND splits_json != '{}' AND splits_json != '[]'
                """)
                rows = cur.fetchall()
                cur.close()
                conn.close()
                break
            except Exception as retry_err:
                print(f"[SectionPar] Attempt {attempt+1}/3 failed: {retry_err}", file=sys.stderr)
                try:
                    if hasattr(conn, 'real_close'):
                        conn.real_close()
                    else:
                        conn.close()
                except Exception:
                    pass
                if attempt < 2:
                    time.sleep(5)
        if rows is None:
            print("[SectionPar] All 3 retries failed — skipping sectional pars for this session", file=sys.stderr)
            _section_par_failed = True
            return _section_par_cache if _section_par_cache else {}

        grouped = {}
        td_grouped = {}
        d_grouped = {}

        for row in rows:
            trk = row[0] or ''
            dist = row[1] or 0
            tc = row[2] or ''
            raw_splits = row[3]

            sections = _parse_splits_to_sections(raw_splits)
            if not sections:
                continue

            going = _normalize_going(tc)

            for sec in sections:
                mark = sec.get('mark_m')
                t = sec.get('time_s')
                if mark is None or t is None or t <= 0:
                    continue

                key_full = (trk, dist, going)
                grouped.setdefault(key_full, {})
                grouped[key_full].setdefault(mark, [])
                grouped[key_full][mark].append(t)

                key_td = (trk, dist, '__td__')
                td_grouped.setdefault(key_td, {})
                td_grouped[key_td].setdefault(mark, [])
                td_grouped[key_td][mark].append(t)

                key_d = ('__any__', dist, '__d__')
                d_grouped.setdefault(key_d, {})
                d_grouped[key_d].setdefault(mark, [])
                d_grouped[key_d][mark].append(t)

        par_table = {}
        for key, marks in grouped.items():
            par_table[key] = {}
            for mark, times in marks.items():
                if len(times) >= 5:
                    par_table[key][mark] = _statistics.median(times)

        for key, marks in td_grouped.items():
            if key not in par_table:
                par_table[key] = {}
            for mark, times in marks.items():
                if mark not in par_table[key] and len(times) >= 5:
                    par_table[key][mark] = _statistics.median(times)

        for key, marks in d_grouped.items():
            if key not in par_table:
                par_table[key] = {}
            for mark, times in marks.items():
                if mark not in par_table[key] and len(times) >= 5:
                    par_table[key][mark] = _statistics.median(times)

        _section_par_cache = par_table
        _section_par_cache_time = now
        return par_table
    except Exception as e:
        print(f"[SectionPar] Par table build error: {e}", file=sys.stderr)
        return _section_par_cache if _section_par_cache else {}


def calculate_par_deviations(sections, track, distance_m, track_config):
    default_result = {'early_benchmark': 0.0, 'late_benchmark': 0.0, 'overall_benchmark': 0.0, 'sections_matched': 0}
    try:
        if not sections:
            return default_result

        par_table = build_section_par_table()
        if not par_table:
            return default_result

        going = _normalize_going(track_config)
        pars = par_table.get((track, distance_m, going))
        if not pars:
            pars = par_table.get((track, distance_m, '__td__'))
        if not pars:
            pars = par_table.get(('__any__', distance_m, '__d__'))
        if not pars:
            return default_result

        half_dist = distance_m / 2.0 if distance_m > 0 else 0
        early_devs = []
        late_devs = []

        for sec in sections:
            mark = sec.get('mark_m')
            t = sec.get('time_s')
            if mark is None or t is None or t <= 0:
                continue
            par_time = pars.get(mark)
            if par_time is None:
                continue
            deviation = par_time - t
            if mark < half_dist:
                early_devs.append(deviation)
            else:
                late_devs.append(deviation)

        if not early_devs and not late_devs:
            return default_result

        early_benchmark = sum(early_devs) / len(early_devs) if early_devs else 0.0
        late_benchmark = sum(late_devs) / len(late_devs) if late_devs else 0.0

        total_weight = 0.0
        weighted_sum = 0.0
        for d in early_devs:
            weighted_sum += d * 1.0
            total_weight += 1.0
        for d in late_devs:
            weighted_sum += d * 1.2
            total_weight += 1.2
        overall_benchmark = weighted_sum / total_weight if total_weight > 0 else 0.0

        return {
            'early_benchmark': round(early_benchmark, 4),
            'late_benchmark': round(late_benchmark, 4),
            'overall_benchmark': round(overall_benchmark, 4),
            'sections_matched': len(early_devs) + len(late_devs),
        }
    except Exception:
        return default_result


_benchmark_cache = {}
_benchmark_cache_time = 0

def calculate_horse_benchmarks(horse_name, limit=10):
    global _benchmark_cache, _benchmark_cache_time

    default_result = {'early_benchmark': 0.0, 'late_benchmark': 0.0, 'overall_benchmark': 0.0, 'benchmark_trend': 0, 'benchmark_runs': 0}

    if not horse_name:
        return default_result

    now = time.time()
    if now - _benchmark_cache_time > 3600:
        _benchmark_cache = {}
        _benchmark_cache_time = now

    cache_key = horse_name.lower().strip()
    if cache_key in _benchmark_cache:
        return _benchmark_cache[cache_key]

    try:
        stored = get_stored_sectionals(horse_name, limit=limit)
        if not stored:
            _benchmark_cache[cache_key] = default_result
            return default_result

        recency_weights = [0.30, 0.22, 0.16, 0.12, 0.08, 0.05, 0.03, 0.02, 0.01, 0.01]
        run_benchmarks = []

        for rec in stored:
            splits_raw = rec.get('splits_json')
            if not splits_raw:
                continue
            sections = _parse_splits_to_sections(splits_raw)
            if not sections:
                continue
            trk = rec.get('track', '')
            dist = rec.get('distance_m', 0)
            tc = rec.get('track_config', '')
            devs = calculate_par_deviations(sections, trk, dist, tc)
            if devs['sections_matched'] > 0:
                run_benchmarks.append(devs)

        if not run_benchmarks:
            _benchmark_cache[cache_key] = default_result
            return default_result

        total_weight = 0.0
        w_early = 0.0
        w_late = 0.0
        w_overall = 0.0

        for i, bm in enumerate(run_benchmarks):
            w = recency_weights[i] if i < len(recency_weights) else 0.01
            w_early += bm['early_benchmark'] * w
            w_late += bm['late_benchmark'] * w
            w_overall += bm['overall_benchmark'] * w
            total_weight += w

        if total_weight > 0:
            w_early /= total_weight
            w_late /= total_weight
            w_overall /= total_weight

        benchmark_trend = 0
        if len(run_benchmarks) >= 3:
            recent = run_benchmarks[:2]
            older = run_benchmarks[2:]
            recent_avg = sum(r['overall_benchmark'] for r in recent) / len(recent)
            older_avg = sum(r['overall_benchmark'] for r in older) / len(older)
            diff = recent_avg - older_avg
            if diff > 0.05:
                benchmark_trend = 1
            elif diff < -0.05:
                benchmark_trend = -1

        result = {
            'early_benchmark': round(w_early, 4),
            'late_benchmark': round(w_late, 4),
            'overall_benchmark': round(w_overall, 4),
            'benchmark_trend': benchmark_trend,
            'benchmark_runs': len(run_benchmarks),
        }
        _benchmark_cache[cache_key] = result
        return result
    except Exception:
        _benchmark_cache[cache_key] = default_result
        return default_result


_peak_ability_cache = {}
_peak_ability_cache_time = 0

_RECENCY_WEIGHTS = [0.30, 0.22, 0.16, 0.12, 0.08, 0.05, 0.03, 0.02, 0.01, 0.01]


def _classify_section(from_finish):
    if from_finish <= 200:
        return 'final_200m'
    elif from_finish <= 400:
        return '2nd_last_200m'
    elif from_finish <= 600:
        return '3rd_last_200m'
    elif from_finish <= 800:
        return '4th_last_200m'
    elif from_finish <= 1000:
        return '5th_last_200m'
    else:
        return 'early_section'


def build_peak_ability_profile(horse_name):
    global _peak_ability_cache, _peak_ability_cache_time

    default_result = {
        'speed_ceiling': {},
        'avg_peak_speed': {},
        'peak_vs_avg_gap': 0.0,
        'peak_closing_speed': 0.0,
        'career_best_peak': 0.0,
        'peak_runs': 0,
    }

    if not horse_name:
        return default_result

    now = time.time()
    if now - _peak_ability_cache_time > 3600:
        _peak_ability_cache = {}
        _peak_ability_cache_time = now

    cache_key = horse_name.lower().strip()
    if cache_key in _peak_ability_cache:
        return _peak_ability_cache[cache_key]

    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            _peak_ability_cache[cache_key] = default_result
            return default_result
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT distance_m, splits_json, race_date
            FROM sectional_times
            WHERE LOWER(horse_name) = %s AND source = 'nsw_pidata'
            ORDER BY race_date DESC
            LIMIT 20
        """, (cache_key,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        conn = None

        if not rows:
            _peak_ability_cache[cache_key] = default_result
            return default_result

        valid_runs = []
        for row in rows:
            splits = row.get('splits_json')
            if not splits:
                continue
            if isinstance(splits, str):
                try:
                    splits = json.loads(splits)
                except (json.JSONDecodeError, ValueError):
                    continue
            if not isinstance(splits, dict):
                continue
            has_peak = any(
                isinstance(v, dict) and 'peak_speed_ms' in v
                for v in splits.values()
            )
            if has_peak:
                valid_runs.append(row)

        if not valid_runs:
            _peak_ability_cache[cache_key] = default_result
            return default_result

        section_peaks = {}
        run_gaps = []
        all_peaks = []

        for run_idx, row in enumerate(valid_runs):
            distance_m = int(row.get('distance_m') or 0)
            splits = row['splits_json']
            run_section_gaps = []

            for mark_key, mark_data in splits.items():
                if not isinstance(mark_data, dict):
                    continue
                peak_spd = mark_data.get('peak_speed_ms')
                actual_spd = mark_data.get('speed_ms')
                if peak_spd is None:
                    continue

                mark_str = mark_key.replace('m', '').strip()
                try:
                    mark_m = int(mark_str)
                except (ValueError, TypeError):
                    continue

                from_finish = distance_m - mark_m
                if from_finish < 0:
                    from_finish = 0
                section = _classify_section(from_finish)

                weight = _RECENCY_WEIGHTS[run_idx] if run_idx < len(_RECENCY_WEIGHTS) else 0.01
                if section not in section_peaks:
                    section_peaks[section] = []
                section_peaks[section].append((float(peak_spd), weight))
                all_peaks.append(float(peak_spd))

                if actual_spd is not None and peak_spd is not None:
                    gap = float(peak_spd) - float(actual_spd)
                    run_section_gaps.append(gap)

            if run_section_gaps:
                run_gaps.append(sum(run_section_gaps) / len(run_section_gaps))

        speed_ceiling = {}
        avg_peak_speed = {}
        for section, peaks_list in section_peaks.items():
            speeds = [p[0] for p in peaks_list]
            weights = [p[1] for p in peaks_list]

            top3 = sorted(speeds, reverse=True)[:3]
            speed_ceiling[section] = round(sum(top3) / len(top3), 3) if top3 else 0.0

            if speeds and sum(weights) > 0:
                weighted_sum = sum(s * w for s, w in zip(speeds, weights))
                avg_peak_speed[section] = round(weighted_sum / sum(weights), 3)
            else:
                avg_peak_speed[section] = round(sum(speeds) / len(speeds), 3) if speeds else 0.0

        peak_vs_avg_gap = round(sum(run_gaps) / len(run_gaps), 3) if run_gaps else 0.0
        peak_closing_speed = speed_ceiling.get('final_200m', 0.0)
        career_best_peak = round(max(all_peaks), 3) if all_peaks else 0.0

        result = {
            'speed_ceiling': speed_ceiling,
            'avg_peak_speed': avg_peak_speed,
            'peak_vs_avg_gap': peak_vs_avg_gap,
            'peak_closing_speed': peak_closing_speed,
            'career_best_peak': career_best_peak,
            'peak_runs': len(valid_runs),
        }
        _peak_ability_cache[cache_key] = result
        return result

    except Exception as e:
        log_debug(f"build_peak_ability_profile error for {horse_name}: {e}")
        _peak_ability_cache[cache_key] = default_result
        return default_result
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def detect_hidden_form(horse_name, recent_n=5):
    default_result = {
        'hidden_form_score': 0,
        'compromised_run_count': 0,
        'peak_speed_trend': 0,
        'best_unrealized_peak': 0.0,
    }

    if not horse_name:
        return default_result

    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return default_result
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        normalized = horse_name.lower().strip()

        cur.execute("""
            SELECT st.distance_m, st.splits_json, st.race_date, st.track, st.race_number
            FROM sectional_times st
            WHERE LOWER(st.horse_name) = %s AND st.source = 'nsw_pidata'
            ORDER BY st.race_date DESC
            LIMIT %s
        """, (normalized, recent_n))
        recent_runs = cur.fetchall()

        if not recent_runs:
            cur.close()
            conn.close()
            return default_result

        compromised_count = 0
        run_avg_peaks = []
        hidden_score_components = []
        best_unrealized = 0.0

        for run in recent_runs:
            splits = run.get('splits_json')
            if not splits:
                continue
            if isinstance(splits, str):
                try:
                    splits = json.loads(splits)
                except (json.JSONDecodeError, ValueError):
                    continue
            if not isinstance(splits, dict):
                continue

            distance_m = int(run.get('distance_m') or 0)
            race_date = run.get('race_date', '')
            track = run.get('track', '')

            cur.execute("""
                SELECT position, field_size
                FROM race_results_history
                WHERE LOWER(horse_name) = %s
                  AND race_date = %s
                  AND LOWER(track) = LOWER(%s)
                LIMIT 1
            """, (normalized, race_date, track))
            result_row = cur.fetchone()

            finish_pos = None
            field_size = None
            if result_row:
                fp = result_row.get('position') or result_row.get('finish_position')
                fs = result_row.get('field_size')
                try:
                    finish_pos = int(fp) if fp is not None else None
                    field_size = int(fs) if fs is not None else None
                except (ValueError, TypeError):
                    pass

            section_peaks = []
            section_gaps = []
            max_peak_this_run = 0
            for mark_key, mark_data in splits.items():
                if not isinstance(mark_data, dict):
                    continue
                peak_spd = mark_data.get('peak_speed_ms')
                actual_spd = mark_data.get('speed_ms')
                if peak_spd is None:
                    continue
                peak_val = float(peak_spd)
                section_peaks.append(peak_val)
                if peak_val > max_peak_this_run:
                    max_peak_this_run = peak_val
                if actual_spd is not None:
                    gap = peak_val - float(actual_spd)
                    section_gaps.append(gap)

            if not section_peaks:
                continue

            avg_peak = sum(section_peaks) / len(section_peaks)
            run_avg_peaks.append(avg_peak)

            avg_gap = sum(section_gaps) / len(section_gaps) if section_gaps else 0
            high_gap_sections = sum(1 for g in section_gaps if g > 3.0)
            is_compromised = avg_gap > 1.5 or high_gap_sections >= 2
            if is_compromised:
                compromised_count += 1

            finished_poorly = False
            if finish_pos is not None and field_size is not None and field_size > 0:
                finish_pct = finish_pos / field_size
                finished_poorly = finish_pct > 0.5

            if finished_poorly:
                ability_vs_result = avg_gap * (finish_pct - 0.3) * 10
                hidden_score_components.append(max(0, ability_vs_result))
                if max_peak_this_run > best_unrealized:
                    best_unrealized = max_peak_this_run

            if is_compromised and finished_poorly:
                hidden_score_components.append(avg_gap * 3)

        cur.close()
        conn.close()
        conn = None

        peak_speed_trend = 0
        if len(run_avg_peaks) >= 3:
            recent_avg = sum(run_avg_peaks[:2]) / 2
            older_avg = sum(run_avg_peaks[2:min(5, len(run_avg_peaks))]) / len(run_avg_peaks[2:min(5, len(run_avg_peaks))])
            diff = recent_avg - older_avg
            if diff > 0.3:
                peak_speed_trend = 1
            elif diff < -0.3:
                peak_speed_trend = -1

        hidden_form_score = 0
        if hidden_score_components:
            raw = sum(hidden_score_components)
            hidden_form_score = min(100, int(raw * 5))
        hidden_form_score = min(100, hidden_form_score + compromised_count * 10)

        result = {
            'hidden_form_score': hidden_form_score,
            'compromised_run_count': compromised_count,
            'peak_speed_trend': peak_speed_trend,
            'best_unrealized_peak': round(best_unrealized, 3),
        }
        return result

    except Exception as e:
        log_debug(f"detect_hidden_form error for {horse_name}: {e}")
        return default_result
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def get_peak_ability_features(horse_name):
    profile = build_peak_ability_profile(horse_name)
    hidden = detect_hidden_form(horse_name)

    return {
        'peak_ability_ceiling': profile.get('speed_ceiling', {}).get('final_200m', 0.0),
        'peak_vs_average_gap': profile.get('peak_vs_avg_gap', 0.0),
        'hidden_form_score': hidden.get('hidden_form_score', 0),
        'peak_speed_trend': hidden.get('peak_speed_trend', 0),
        'peak_closing_speed': profile.get('peak_closing_speed', 0.0),
        'compromised_runs': hidden.get('compromised_run_count', 0),
    }


_decel_profile_cache = {}
_decel_profile_cache_time = 0
_distance_suitability_cache = {}
_distance_suitability_cache_time = 0

def build_deceleration_profile(horse_name):
    global _decel_profile_cache, _decel_profile_cache_time

    default_result = {
        'distance_profiles': {},
        'overall_decel_rate': 0.0,
        'decel_trend_across_distances': 0.0,
        'total_runs_with_splits': 0,
    }

    if not horse_name:
        return default_result

    now = time.time()
    if now - _decel_profile_cache_time > 3600:
        _decel_profile_cache = {}
        _decel_profile_cache_time = now

    cache_key = horse_name.lower().strip()
    if cache_key in _decel_profile_cache:
        return _decel_profile_cache[cache_key]

    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            _decel_profile_cache[cache_key] = default_result
            return default_result
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT distance_m, splits_json, race_date, source
            FROM sectional_times
            WHERE LOWER(horse_name) = %s
            AND splits_json IS NOT NULL
            ORDER BY race_date DESC
            LIMIT 15
        """, (cache_key,))
        rows = cur.fetchall()
        cur.close()

        if not rows:
            _decel_profile_cache[cache_key] = default_result
            return default_result

        distance_decel_rates = {}
        total_runs = 0

        for row in rows:
            splits = row.get('splits_json')
            if not splits:
                continue
            if isinstance(splits, str):
                try:
                    splits = json.loads(splits)
                except (json.JSONDecodeError, ValueError):
                    continue

            distance_m = int(row.get('distance_m') or 0)
            source = str(row.get('source', '')).lower()
            if distance_m < 800:
                continue

            section_speeds = []

            if isinstance(splits, dict):
                mark_keys = []
                for k in splits.keys():
                    try:
                        mark_val = int(k.replace('m', ''))
                        mark_keys.append((mark_val, k))
                    except (ValueError, TypeError):
                        continue
                mark_keys.sort(key=lambda x: x[0])
                for mark_val, k in mark_keys:
                    mark_data = splits[k]
                    if isinstance(mark_data, dict):
                        spd = mark_data.get('speed_ms')
                        if spd is not None:
                            try:
                                section_speeds.append((mark_val, float(spd)))
                            except (ValueError, TypeError):
                                continue

            elif isinstance(splits, list):
                if 'racing_qld' in source:
                    for item in splits:
                        if not isinstance(item, dict):
                            continue
                        mark = item.get('mark')
                        spd = item.get('speed_ms')
                        if mark is not None and spd is not None:
                            try:
                                section_speeds.append((int(mark), float(spd)))
                            except (ValueError, TypeError):
                                continue
                    section_speeds.sort(key=lambda x: x[0])
                elif 'racing_com' in source:
                    com_sections = []
                    for item in splits:
                        if not isinstance(item, dict):
                            continue
                        dist_str = str(item.get('distance', ''))
                        spd = item.get('avgSpeed')
                        if not dist_str or spd is None:
                            continue
                        try:
                            spd_val = float(spd)
                        except (ValueError, TypeError):
                            continue
                        if 'FINISH' in dist_str.upper():
                            parts = dist_str.split('-')
                            try:
                                from_out = int(parts[0].replace('m', ''))
                            except (ValueError, TypeError):
                                from_out = 200
                            effective_mark = distance_m - from_out
                            com_sections.append((effective_mark, spd_val))
                        else:
                            parts = dist_str.split('-')
                            if len(parts) == 2:
                                try:
                                    from_out = int(parts[0].replace('m', ''))
                                    effective_mark = distance_m - from_out
                                    com_sections.append((effective_mark, spd_val))
                                except (ValueError, TypeError):
                                    continue
                    com_sections.sort(key=lambda x: x[0])
                    section_speeds = com_sections
                else:
                    for item in splits:
                        if not isinstance(item, dict):
                            continue
                        mark = item.get('mark')
                        spd = item.get('speed_ms')
                        if mark is not None and spd is not None:
                            try:
                                section_speeds.append((int(mark), float(spd)))
                            except (ValueError, TypeError):
                                continue
                    section_speeds.sort(key=lambda x: x[0])

            if len(section_speeds) < 4:
                continue

            is_nsw_pidata = isinstance(splits, dict) or source == 'nsw_pidata'
            if is_nsw_pidata and len(section_speeds) >= 4:
                closing_speed = section_speeds[-2][1]
                mid_late_speed = section_speeds[-3][1]
                early_late_speed = section_speeds[-4][1]
            else:
                closing_speed = section_speeds[-1][1]
                mid_late_speed = section_speeds[-2][1]
                early_late_speed = section_speeds[-3][1]

            if early_late_speed <= 0:
                continue

            decel_rate = (closing_speed - early_late_speed) / early_late_speed

            if distance_m not in distance_decel_rates:
                distance_decel_rates[distance_m] = []
            distance_decel_rates[distance_m].append({
                'decel_rate': decel_rate,
                'final_600_speed': early_late_speed,
            })
            total_runs += 1

        if total_runs == 0:
            _decel_profile_cache[cache_key] = default_result
            return default_result

        distance_profiles = {}
        all_weighted_decels = []
        dist_decel_pairs = []

        for dist_m, runs in sorted(distance_decel_rates.items()):
            decels = [r['decel_rate'] for r in runs]
            final_speeds = [r['final_600_speed'] for r in runs]
            decels_sorted = sorted(decels)
            median_idx = len(decels_sorted) // 2
            median_decel = decels_sorted[median_idx] if decels_sorted else 0.0
            best_decel = max(decels) if decels else 0.0
            worst_decel = min(decels) if decels else 0.0
            consistency = 1.0 - min(1.0, (max(decels) - min(decels)) if len(decels) > 1 else 0.0)
            avg_f600 = sum(final_speeds) / len(final_speeds) if final_speeds else 0.0

            distance_profiles[dist_m] = {
                'median_decel': round(median_decel, 4),
                'best_decel': round(best_decel, 4),
                'worst_decel': round(worst_decel, 4),
                'consistency': round(consistency, 4),
                'run_count': len(runs),
                'avg_final_600_speed': round(avg_f600, 3),
            }

            weight = len(runs)
            all_weighted_decels.append((median_decel, weight))
            dist_decel_pairs.append((dist_m, median_decel))

        total_weight = sum(w for _, w in all_weighted_decels)
        overall_decel = sum(d * w for d, w in all_weighted_decels) / total_weight if total_weight > 0 else 0.0

        decel_trend = 0.0
        if len(dist_decel_pairs) >= 2:
            dists = [p[0] for p in dist_decel_pairs]
            decels = [p[1] for p in dist_decel_pairs]
            n = len(dists)
            mean_d = sum(dists) / n
            mean_dec = sum(decels) / n
            num = sum((dists[i] - mean_d) * (decels[i] - mean_dec) for i in range(n))
            den = sum((dists[i] - mean_d) ** 2 for i in range(n))
            if den > 0:
                decel_trend = num / den

        result = {
            'distance_profiles': distance_profiles,
            'overall_decel_rate': round(overall_decel, 4),
            'decel_trend_across_distances': round(decel_trend, 6),
            'total_runs_with_splits': total_runs,
        }
        _decel_profile_cache[cache_key] = result
        return result

    except Exception as e:
        log_debug(f"build_deceleration_profile error for {horse_name}: {e}")
        _decel_profile_cache[cache_key] = default_result
        return default_result
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def predict_distance_suitability(horse_name, target_distance_m):
    global _distance_suitability_cache, _distance_suitability_cache_time

    default_result = {
        'decel_rate_current_dist': 0.0,
        'decel_trend_across_distances': 0.0,
        'distance_ceiling': 2400,
        'stamina_score': 50.0,
        'step_up_candidate': 0.0,
        'distance_limited': 0.0,
    }

    if not horse_name or not target_distance_m:
        return default_result

    now = time.time()
    if now - _distance_suitability_cache_time > 3600:
        _distance_suitability_cache = {}
        _distance_suitability_cache_time = now

    cache_key = (horse_name.lower().strip(), target_distance_m)
    if cache_key in _distance_suitability_cache:
        return _distance_suitability_cache[cache_key]

    try:
        profile = build_deceleration_profile(horse_name)
        dist_profiles = profile.get('distance_profiles', {})
        decel_trend = profile.get('decel_trend_across_distances', 0.0)

        if not dist_profiles:
            _distance_suitability_cache[cache_key] = default_result
            return default_result

        sorted_dists = sorted(dist_profiles.keys())

        if target_distance_m in dist_profiles:
            decel_at_target = dist_profiles[target_distance_m]['median_decel']
        else:
            if len(sorted_dists) == 1:
                known_dist = sorted_dists[0]
                known_decel = dist_profiles[known_dist]['median_decel']
                decel_at_target = known_decel + decel_trend * (target_distance_m - known_dist)
            else:
                below = [d for d in sorted_dists if d <= target_distance_m]
                above = [d for d in sorted_dists if d > target_distance_m]
                if below and above:
                    d_low = below[-1]
                    d_high = above[0]
                    dec_low = dist_profiles[d_low]['median_decel']
                    dec_high = dist_profiles[d_high]['median_decel']
                    ratio = (target_distance_m - d_low) / (d_high - d_low) if d_high != d_low else 0.5
                    decel_at_target = dec_low + ratio * (dec_high - dec_low)
                elif below:
                    d_low = below[-1]
                    dec_low = dist_profiles[d_low]['median_decel']
                    decel_at_target = dec_low + decel_trend * (target_distance_m - d_low)
                else:
                    d_high = above[0]
                    dec_high = dist_profiles[d_high]['median_decel']
                    decel_at_target = dec_high + decel_trend * (target_distance_m - d_high)

        distance_ceiling = 2400
        for test_dist in range(800, 3200, 200):
            if test_dist in dist_profiles:
                test_decel = dist_profiles[test_dist]['median_decel']
            else:
                nearest = min(sorted_dists, key=lambda d: abs(d - test_dist))
                test_decel = dist_profiles[nearest]['median_decel'] + decel_trend * (test_dist - nearest)
            if test_decel < -0.08:
                distance_ceiling = test_dist
                break

        if decel_at_target >= 0:
            stamina_score = min(100.0, 70.0 + decel_at_target * 500)
        elif decel_at_target >= -0.04:
            stamina_score = 50.0 + (decel_at_target + 0.04) * 500
        elif decel_at_target >= -0.08:
            stamina_score = 25.0 + (decel_at_target + 0.08) * 625
        else:
            stamina_score = max(0.0, 25.0 + (decel_at_target + 0.08) * 300)

        max_raced_dist = max(sorted_dists)
        step_up_candidate = 0.0
        if target_distance_m > max_raced_dist:
            max_decel = dist_profiles[max_raced_dist]['median_decel']
            if max_decel > -0.04:
                step_up_candidate = 1.0

        distance_limited = 0.0
        if decel_at_target < -0.08:
            distance_limited = 1.0
        elif target_distance_m > distance_ceiling:
            distance_limited = 1.0

        result = {
            'decel_rate_current_dist': round(decel_at_target, 4),
            'decel_trend_across_distances': round(decel_trend, 6),
            'distance_ceiling': distance_ceiling,
            'stamina_score': round(stamina_score, 1),
            'step_up_candidate': step_up_candidate,
            'distance_limited': distance_limited,
        }
        _distance_suitability_cache[cache_key] = result
        return result

    except Exception as e:
        log_debug(f"predict_distance_suitability error for {horse_name}: {e}")
        _distance_suitability_cache[cache_key] = default_result
        return default_result


def get_deceleration_features(horse_name, target_distance_m):
    try:
        suitability = predict_distance_suitability(horse_name, target_distance_m)
        return {
            'decel_rate_current_dist': suitability.get('decel_rate_current_dist', 0.0),
            'decel_trend_across_distances': suitability.get('decel_trend_across_distances', 0.0),
            'distance_ceiling': suitability.get('distance_ceiling', 2400),
            'stamina_score': suitability.get('stamina_score', 50.0),
            'step_up_candidate': suitability.get('step_up_candidate', 0.0),
            'distance_limited': suitability.get('distance_limited', 0.0),
        }
    except Exception as e:
        log_debug(f"get_deceleration_features error: {e}")
        return {
            'decel_rate_current_dist': 0.0,
            'decel_trend_across_distances': 0.0,
            'distance_ceiling': 2400,
            'stamina_score': 50.0,
            'step_up_candidate': 0.0,
            'distance_limited': 0.0,
        }


_speed_map_cache = {}
_speed_map_cache_time = 0.0


_jockey_efficiency_cache = {}
_jockey_efficiency_cache_time = 0.0


def build_jockey_efficiency_rating(jockey_name):
    global _jockey_efficiency_cache, _jockey_efficiency_cache_time

    default_result = {
        'efficiency_score': 50.0,
        'avg_pace_deviation': 0.0,
        'cook_rate': 15.0,
        'cold_rate': 15.0,
        'finishing_efficiency': 0.0,
        'rides_analyzed': 0,
        'avg_early_pct': 100.0,
        'avg_mid_pct': 100.0,
        'avg_home_pct': 100.0,
    }

    if not jockey_name or not jockey_name.strip():
        return default_result

    cache_key = jockey_name.lower().strip()

    now = time.time()
    if now - _jockey_efficiency_cache_time > 3600:
        _jockey_efficiency_cache = {}
        _jockey_efficiency_cache_time = now

    if cache_key in _jockey_efficiency_cache:
        return _jockey_efficiency_cache[cache_key]

    try:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            return default_result

        conn = get_db_connection(db_url)
        if not conn:
            return default_result
        cur = conn.cursor()

        cur.execute(f"""
            SELECT st.horse_name, st.splits_json, st.distance_m, rrh.race_date
            FROM race_results_history rrh
            JOIN LATERAL (
                SELECT st.horse_name, st.splits_json, st.distance_m, st.created_at
                FROM sectional_times st
                WHERE st.splits_json IS NOT NULL
                  AND (
                    st.race_results_history_id = rrh.id
                    OR ({_sectional_natural_key_sql("st", "rrh")})
                  )
                ORDER BY
                  CASE WHEN st.race_results_history_id = rrh.id THEN 0 ELSE 1 END,
                  st.created_at DESC NULLS LAST
                LIMIT 1
            ) st ON TRUE
            WHERE LOWER(rrh.jockey) = LOWER(%s)
            ORDER BY rrh.race_date DESC
            LIMIT 100
        """, (jockey_name.strip(),))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            _jockey_efficiency_cache[cache_key] = default_result
            return default_result

        efficient_rides = 0
        cooked_rides = 0
        cold_rides = 0
        total_deviation = 0.0
        finishing_devs = []
        all_early_pcts = []
        all_mid_pcts = []
        all_home_pcts = []
        rides_analyzed = 0

        for row in rows:
            horse_name, splits_json, distance_m, race_date = row
            if not horse_name or not splits_json:
                continue

            sections = _parse_splits_to_sections(splits_json)
            if len(sections) < 3:
                continue

            try:
                dist = int(distance_m) if distance_m else 0
            except (ValueError, TypeError):
                dist = 0

            phases = _normalize_pace_phases(sections, dist)
            actual_early = phases['early']['speed_pct']
            actual_mid = phases['mid']['speed_pct']
            actual_home = phases['home']['speed_pct']

            profile = build_horse_pace_profile(horse_name)
            if not profile.get('has_profile'):
                continue

            optimal_early = profile['early_median']
            optimal_mid = profile['mid_median']
            optimal_home = profile['home_median']

            early_dev = abs(actual_early - optimal_early)
            mid_dev = abs(actual_mid - optimal_mid)
            home_dev = abs(actual_home - optimal_home)
            avg_dev = (early_dev + mid_dev + home_dev) / 3.0

            rides_analyzed += 1
            total_deviation += avg_dev
            finishing_devs.append(actual_home - optimal_home)
            all_early_pcts.append(actual_early)
            all_mid_pcts.append(actual_mid)
            all_home_pcts.append(actual_home)

            if early_dev <= 2.0 and mid_dev <= 2.0 and home_dev <= 2.0:
                efficient_rides += 1

            if actual_early - optimal_early > 3.0:
                cooked_rides += 1

            if optimal_early - actual_early > 3.0:
                cold_rides += 1

        if rides_analyzed < 10:
            _jockey_efficiency_cache[cache_key] = default_result
            return default_result

        result = {
            'efficiency_score': round((efficient_rides / rides_analyzed) * 100, 2),
            'avg_pace_deviation': round(total_deviation / rides_analyzed, 3),
            'cook_rate': round((cooked_rides / rides_analyzed) * 100, 2),
            'cold_rate': round((cold_rides / rides_analyzed) * 100, 2),
            'finishing_efficiency': round(sum(finishing_devs) / len(finishing_devs), 3) if finishing_devs else 0.0,
            'rides_analyzed': rides_analyzed,
            'avg_early_pct': round(sum(all_early_pcts) / len(all_early_pcts), 3) if all_early_pcts else 100.0,
            'avg_mid_pct': round(sum(all_mid_pcts) / len(all_mid_pcts), 3) if all_mid_pcts else 100.0,
            'avg_home_pct': round(sum(all_home_pcts) / len(all_home_pcts), 3) if all_home_pcts else 100.0,
        }

        _jockey_efficiency_cache[cache_key] = result
        return result

    except Exception as e:
        log_debug(f"build_jockey_efficiency_rating error for {jockey_name}: {e}")
        _jockey_efficiency_cache[cache_key] = default_result
        return default_result


def get_jockey_efficiency_features(jockey_name, horse_name):
    defaults = {
        'jockey_efficiency_score': 50.0,
        'jockey_pace_match': 50.0,
        'jockey_cook_rate': 15.0,
        'jockey_finishing_efficiency': 0.0,
    }
    try:
        rating = build_jockey_efficiency_rating(jockey_name)
        horse_profile = build_horse_pace_profile(horse_name)

        result = {
            'jockey_efficiency_score': rating.get('efficiency_score', 50.0),
            'jockey_cook_rate': rating.get('cook_rate', 15.0),
            'jockey_finishing_efficiency': rating.get('finishing_efficiency', 0.0),
        }

        if horse_profile.get('has_profile') and rating.get('rides_analyzed', 0) >= 10:
            jockey_early = rating.get('avg_early_pct', 100.0)
            jockey_mid = rating.get('avg_mid_pct', 100.0)
            jockey_home = rating.get('avg_home_pct', 100.0)

            horse_early = horse_profile['early_median']
            horse_mid = horse_profile['mid_median']
            horse_home = horse_profile['home_median']

            early_diff = abs(jockey_early - horse_early)
            mid_diff = abs(jockey_mid - horse_mid)
            home_diff = abs(jockey_home - horse_home)

            avg_diff = (early_diff + mid_diff + home_diff) / 3.0
            pace_match = max(0.0, min(100.0, 100.0 - avg_diff * 10.0))
            result['jockey_pace_match'] = round(pace_match, 2)
        else:
            result['jockey_pace_match'] = 50.0

        return result
    except Exception as e:
        log_debug(f"get_jockey_efficiency_features error: {e}")
        return defaults


_sectional_franking_cache = {}
_sectional_franking_cache_time = 0.0


def calculate_sectional_franking_value(horse_name, limit=10):
    global _sectional_franking_cache, _sectional_franking_cache_time

    default_result = {
        'sectional_franking_value': 0.0,
        'franking_events_count': 0,
        'best_sectional_frank': 0.0,
    }

    if not horse_name or not horse_name.strip():
        return default_result

    cache_key = horse_name.lower().strip()

    now = time.time()
    if now - _sectional_franking_cache_time > 3600:
        _sectional_franking_cache = {}
        _sectional_franking_cache_time = now

    if cache_key in _sectional_franking_cache:
        return _sectional_franking_cache[cache_key]

    try:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            return default_result

        conn = get_db_connection(db_url)
        if not conn:
            return default_result
        cur = conn.cursor()

        cur.execute("""
            SELECT id, race_id, race_date, opponents_json, position, margin_lengths, horse_id
            FROM race_results_history
            WHERE LOWER(horse_name) = LOWER(%s)
              AND position IS NOT NULL
            ORDER BY race_date DESC
            LIMIT %s
        """, (horse_name.strip(), limit))
        horse_races = cur.fetchall()

        if not horse_races or len(horse_races) < 3:
            cur.close()
            conn.close()
            _sectional_franking_cache[cache_key] = default_result
            return default_result

        total_franking_value = 0.0
        franking_events = 0
        best_frank = 0.0
        individual_values = []

        for race_row in horse_races:
            rrh_id, race_id, race_date, opponents_json, horse_pos, horse_margin, horse_id = race_row
            race_date_str = str(race_date)[:10] if race_date else '2020-01-01'

            opponents = []
            if opponents_json:
                if isinstance(opponents_json, str):
                    try:
                        opponents = json.loads(opponents_json)
                    except (json.JSONDecodeError, TypeError):
                        opponents = []
                elif isinstance(opponents_json, list):
                    opponents = opponents_json

            if not opponents:
                cur.execute("""
                    SELECT horse_name, horse_id, id, position, margin_lengths
                    FROM race_results_history
                    WHERE race_id = %s AND horse_id != %s AND position IS NOT NULL
                """, (race_id, horse_id))
                opp_rows = cur.fetchall()
                for orow in opp_rows:
                    opponents.append({
                        'horse_name': orow[0],
                        'horse_id': orow[1],
                        'rrh_id': orow[2],
                        'position': orow[3],
                        'margin': orow[4],
                    })

            opp_ids = [o.get('horse_id') for o in opponents if o.get('horse_id')]
            opp_names_only = [o.get('horse_name') for o in opponents if o.get('horse_name') and not o.get('horse_id')]

            opp_wins_by_id = {}
            if opp_ids:
                cur.execute("""
                    SELECT r1.horse_id, r1.position, COALESCE(r2.margin_lengths, 0.0), r1.race_date
                    FROM race_results_history r1
                    LEFT JOIN race_results_history r2 
                        ON r1.race_id = r2.race_id AND r2.position = 2
                    WHERE r1.horse_id = ANY(%s) AND r1.race_date > %s
                      AND r1.position = 1
                      AND COALESCE(r2.margin_lengths, 0.0) >= 0.3
                    ORDER BY r1.race_date ASC
                """, (opp_ids, race_date_str))
                for row in cur.fetchall():
                    opp_wins_by_id.setdefault(row[0], []).append((row[1], row[2], row[3]))

            opp_wins_by_name = {}
            if opp_names_only:
                cur.execute("""
                    SELECT r1.horse_name, r1.position, COALESCE(r2.margin_lengths, 0.0), r1.race_date
                    FROM race_results_history r1
                    LEFT JOIN race_results_history r2 
                        ON r1.race_id = r2.race_id AND r2.position = 2
                    WHERE LOWER(r1.horse_name) = ANY(%s) AND r1.race_date > %s
                      AND r1.position = 1
                      AND COALESCE(r2.margin_lengths, 0.0) >= 0.3
                    ORDER BY r1.race_date ASC
                """, ([n.lower() for n in opp_names_only], race_date_str))
                for row in cur.fetchall():
                    opp_wins_by_name.setdefault(row[0].lower(), []).append((row[1], row[2], row[3]))

            sect_by_rrh_id = {}
            sect_by_horse_race = {}
            opp_rrh_ids = [o.get('rrh_id') for o in opponents if o.get('rrh_id')]
            opp_horse_race_pairs = [(o.get('horse_id'), race_id) for o in opponents if o.get('horse_id') and not o.get('rrh_id')]

            if opp_rrh_ids:
                cur.execute(f"""
                    SELECT rrh.id, st.splits_json, st.distance_m
                    FROM race_results_history rrh
                    JOIN LATERAL (
                        SELECT st.splits_json, st.distance_m, st.created_at
                        FROM sectional_times st
                        WHERE st.race_results_history_id = rrh.id
                           OR ({_sectional_natural_key_sql("st", "rrh")})
                        ORDER BY
                          CASE WHEN st.race_results_history_id = rrh.id THEN 0 ELSE 1 END,
                          st.created_at DESC NULLS LAST
                        LIMIT 1
                    ) st ON TRUE
                    WHERE rrh.id = ANY(%s)
                """, (opp_rrh_ids,))
                for row in cur.fetchall():
                    sect_by_rrh_id[row[0]] = (row[1], row[2])

            if opp_horse_race_pairs:
                pair_horse_ids = [p[0] for p in opp_horse_race_pairs]
                cur.execute(f"""
                    SELECT rrh.horse_id, st.splits_json, st.distance_m
                    FROM race_results_history rrh
                    JOIN LATERAL (
                        SELECT st.splits_json, st.distance_m, st.created_at
                        FROM sectional_times st
                        WHERE st.race_results_history_id = rrh.id
                           OR ({_sectional_natural_key_sql("st", "rrh")})
                        ORDER BY
                          CASE WHEN st.race_results_history_id = rrh.id THEN 0 ELSE 1 END,
                          st.created_at DESC NULLS LAST
                        LIMIT 1
                    ) st ON TRUE
                    WHERE rrh.horse_id = ANY(%s) AND rrh.race_id = %s
                """, (pair_horse_ids, race_id))
                for row in cur.fetchall():
                    sect_by_horse_race[row[0]] = (row[1], row[2])

            for opp in opponents:
                opp_name = opp.get('horse_name', '')
                opp_id = opp.get('horse_id', '')
                if not opp_name and not opp_id:
                    continue

                if opp_id:
                    impressive_wins = opp_wins_by_id.get(opp_id, [])[:3]
                elif opp_name:
                    impressive_wins = opp_wins_by_name.get(opp_name.lower(), [])[:3]
                else:
                    continue

                if not impressive_wins:
                    continue

                opp_rrh_id = opp.get('rrh_id')
                if opp_rrh_id and opp_rrh_id in sect_by_rrh_id:
                    sect_row = sect_by_rrh_id[opp_rrh_id]
                elif opp_id and opp_id in sect_by_horse_race:
                    sect_row = sect_by_horse_race[opp_id]
                else:
                    sect_row = None

                opp_profile_name = opp_name if opp_name else ''
                opp_profile = build_horse_pace_profile(opp_profile_name) if opp_profile_name else None

                effort_ratio = 1.0
                if sect_row and sect_row[0] and opp_profile and opp_profile.get('has_profile'):
                    sections = _parse_splits_to_sections(sect_row[0])
                    if len(sections) >= 3:
                        try:
                            dist = int(sect_row[1]) if sect_row[1] else 0
                        except (ValueError, TypeError):
                            dist = 0
                        phases = _normalize_pace_phases(sections, dist)
                        actual_home = phases['home']['speed_pct']
                        typical_home = opp_profile['home_median']

                        if typical_home > 0:
                            home_ratio = actual_home / typical_home
                            if home_ratio < 0.98:
                                effort_ratio = 1.0 + (0.98 - home_ratio) * 10.0
                            elif home_ratio > 1.02:
                                effort_ratio = max(0.5, 1.0 - (home_ratio - 1.02) * 5.0)

                for win in impressive_wins:
                    win_margin = float(win[1]) if win[1] else 0.0
                    margin_boost = min(3.0, win_margin)
                    frank_value = margin_boost * effort_ratio
                    total_franking_value += frank_value
                    individual_values.append(frank_value)
                    franking_events += 1
                    if frank_value > best_frank:
                        best_frank = frank_value

        cur.close()
        conn.close()

        sectional_franking_value = max(-10.0, min(20.0, total_franking_value))

        result = {
            'sectional_franking_value': round(sectional_franking_value, 3),
            'franking_events_count': franking_events,
            'best_sectional_frank': round(best_frank, 3),
        }

        _sectional_franking_cache[cache_key] = result
        return result

    except Exception as e:
        log_debug(f"calculate_sectional_franking_value error for {horse_name}: {e}")
        _sectional_franking_cache[cache_key] = default_result
        return default_result


def get_sectional_franking_features(horse_name):
    defaults = {
        'sectional_franking_value': 0.0,
        'franking_events_count': 0,
        'best_sectional_frank': 0.0,
    }
    try:
        result = calculate_sectional_franking_value(horse_name)
        return {
            'sectional_franking_value': result.get('sectional_franking_value', 0.0),
            'franking_events_count': result.get('franking_events_count', 0),
            'best_sectional_frank': result.get('best_sectional_frank', 0.0),
        }
    except Exception as e:
        log_debug(f"get_sectional_franking_features error: {e}")
        return defaults


def build_horse_running_profile(horse_name, limit=15):
    global _speed_map_cache, _speed_map_cache_time

    default_result = {
        'running_style_class': 'unknown',
        'avg_early_position_pct': 50.0,
        'position_consistency': 0.0,
        'on_pace_rate': 0.0,
        'leader_rate': 0.0,
        'backmarker_rate': 0.0,
        'avg_200m_position': None,
        'avg_400m_position': None,
        'avg_600m_position': None,
        'settling_speed_sec': 0.0,
        'position_runs': 0,
    }

    if not horse_name:
        return default_result

    now = time.time()
    if now - _speed_map_cache_time > 3600:
        _speed_map_cache = {}
        _speed_map_cache_time = now

    cache_key = ('profile', horse_name.lower().strip())
    if cache_key in _speed_map_cache:
        return _speed_map_cache[cache_key]

    try:
        import psycopg2.extras
        conn = get_db_connection()
        if not conn:
            _speed_map_cache[cache_key] = default_result
            return default_result
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT st.splits_json, st.track, st.distance_m, st.race_date, st.race_number,
                   (SELECT COUNT(*) FROM sectional_times st2
                    WHERE st2.track = st.track AND st2.race_date = st.race_date
                    AND st2.race_number = st.race_number) as field_size
            FROM sectional_times st
            WHERE LOWER(st.horse_name) = LOWER(%s)
            AND st.splits_json IS NOT NULL
            AND st.splits_json::text LIKE '%%position%%'
            ORDER BY st.race_date DESC
            LIMIT %s
        """, (horse_name, limit))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            _speed_map_cache[cache_key] = default_result
            return default_result

        pos_at_200 = []
        pos_at_400 = []
        pos_at_600 = []
        early_pcts = []
        settling_speeds = []

        for row in rows:
            sections = _parse_splits_to_sections(row['splits_json'])
            if not sections:
                continue
            fs = row.get('field_size', 0) or 0
            if fs < 2:
                fs = max(max(s.get('position') or 0 for s in sections), 2)

            dist_m = row.get('distance_m', 0) or 0
            pos_by_mark = {}
            time_by_mark = {}
            for sec in sections:
                p = sec.get('position')
                m = sec.get('mark_m', 0)
                t = sec.get('time_s', 0)
                if p is not None and p > 0:
                    pos_by_mark[m] = p
                if t and t > 0:
                    time_by_mark[m] = t

            def _nearest_mark(pos_by_mark, target, max_delta=150):
                best_mark = None
                best_dist = max_delta + 1
                for m in pos_by_mark:
                    d = abs(m - target)
                    if d < best_dist:
                        best_dist = d
                        best_mark = m
                return pos_by_mark[best_mark] if best_mark is not None and best_dist <= max_delta else None

            best_200 = _nearest_mark(pos_by_mark, 200)
            best_400 = _nearest_mark(pos_by_mark, 400)
            best_600 = _nearest_mark(pos_by_mark, 600)

            if best_200 is not None:
                pos_at_200.append(best_200)
                pct = (best_200 / fs) * 100 if fs > 0 else 50
                early_pcts.append(pct)
            if best_400 is not None:
                pos_at_400.append(best_400)
            if best_600 is not None:
                pos_at_600.append(best_600)

            early_time_marks = [m for m in time_by_mark if m <= 400 and time_by_mark[m] > 0]
            if early_time_marks:
                total_early = sum(time_by_mark[m] for m in early_time_marks)
                settling_speeds.append(total_early)

        if not pos_at_200:
            _speed_map_cache[cache_key] = default_result
            return default_result

        n_runs = len(pos_at_200)
        avg_200 = sum(pos_at_200) / n_runs
        avg_400 = sum(pos_at_400) / len(pos_at_400) if pos_at_400 else None
        avg_600 = sum(pos_at_600) / len(pos_at_600) if pos_at_600 else None
        avg_early_pct = sum(early_pcts) / len(early_pcts) if early_pcts else 50.0

        import statistics
        pos_std = statistics.stdev(pos_at_200) if len(pos_at_200) >= 2 else 0.0
        position_consistency = max(0, 100 - pos_std * 20)

        leader_count = sum(1 for p in pos_at_200 if p <= 2)
        on_pace_count = sum(1 for p in pos_at_200 if p <= 3)
        back_count = sum(1 for p, ep in zip(pos_at_200, early_pcts) if ep > 65)

        leader_rate = leader_count / n_runs * 100
        on_pace_rate = on_pace_count / n_runs * 100
        backmarker_rate = back_count / n_runs * 100

        if leader_rate >= 40 and avg_early_pct <= 25:
            style = 'leader'
        elif on_pace_rate >= 40 or avg_early_pct <= 30:
            style = 'on_pace'
        elif avg_early_pct <= 45:
            style = 'handy'
        elif avg_early_pct <= 60:
            style = 'midfield'
        else:
            style = 'backmarker'

        avg_settling = sum(settling_speeds) / len(settling_speeds) if settling_speeds else 0.0

        result = {
            'running_style_class': style,
            'avg_early_position_pct': round(avg_early_pct, 1),
            'position_consistency': round(position_consistency, 1),
            'on_pace_rate': round(on_pace_rate, 1),
            'leader_rate': round(leader_rate, 1),
            'backmarker_rate': round(backmarker_rate, 1),
            'avg_200m_position': round(avg_200, 1),
            'avg_400m_position': round(avg_400, 1) if avg_400 is not None else None,
            'avg_600m_position': round(avg_600, 1) if avg_600 is not None else None,
            'settling_speed_sec': round(avg_settling, 3),
            'position_runs': n_runs,
        }
        _speed_map_cache[cache_key] = result
        return result
    except Exception as e:
        log_debug(f"build_horse_running_profile error: {e}")
        _speed_map_cache[cache_key] = default_result
        return default_result


def predict_race_pace_from_sectionals(runners_profiles, race_distance_m, track='', track_config=''):
    default_result = {
        'predicted_pace': 'genuine',
        'pace_pressure': 5,
        'early_pace_vs_par': 0.0,
        'confirmed_speed_count': 0,
        'confirmed_speed_horses': [],
        'lone_speed': False,
        'contested_lead': False,
    }
    if not runners_profiles:
        return default_result

    par_table = build_section_par_table()
    going = _normalize_going(track_config)
    par_first_400 = None
    if par_table:
        pars = par_table.get((track, race_distance_m, going))
        if not pars:
            pars = par_table.get((track, race_distance_m, '__td__'))
        if not pars:
            pars = par_table.get(('__any__', race_distance_m, '__d__'))
        if pars:
            t200 = pars.get(200, 0)
            t400 = pars.get(400, 0)
            if not t200:
                t200 = pars.get(100, 0)
            if not t400:
                t400 = pars.get(300, 0)
            if t200 and t400:
                par_first_400 = t200 + t400

    speed_horses = []
    settling_times = []

    for rp in runners_profiles:
        horse_name = rp.get('horse_name', '')
        profile = rp.get('profile', {})
        style = profile.get('running_style_class', 'unknown')
        leader_rate = profile.get('leader_rate', 0)
        on_pace_rate = profile.get('on_pace_rate', 0)
        settling = profile.get('settling_speed_sec', 0)
        early_pct = profile.get('avg_early_position_pct', 50)

        if style == 'leader' or leader_rate >= 40:
            speed_horses.append(horse_name)
            if settling > 0:
                settling_times.append(settling)
        elif style == 'on_pace' and (on_pace_rate >= 50 or early_pct <= 30):
            speed_horses.append(horse_name)
            if settling > 0:
                settling_times.append(settling)

    confirmed_count = len(speed_horses)
    avg_settling = sum(settling_times) / len(settling_times) if settling_times else 0

    early_vs_par = 0.0
    if par_first_400 and avg_settling > 0:
        early_vs_par = par_first_400 - avg_settling

    if confirmed_count >= 3:
        pace = 'hot'
        pressure = min(10, 7 + confirmed_count - 3)
    elif confirmed_count == 2:
        pace = 'genuine'
        pressure = 6
        if early_vs_par > 0.5:
            pace = 'hot'
            pressure = 7
    elif confirmed_count == 1:
        pace = 'soft'
        pressure = 3
    else:
        pace = 'slow'
        pressure = 2

    result = {
        'predicted_pace': pace,
        'pace_pressure': pressure,
        'early_pace_vs_par': round(early_vs_par, 3),
        'confirmed_speed_count': confirmed_count,
        'confirmed_speed_horses': speed_horses,
        'lone_speed': confirmed_count == 1,
        'contested_lead': confirmed_count >= 2,
    }
    return result


def calculate_pace_outcome_score(horse_name, race_pace_prediction):
    cache_key = ('pace_outcome', horse_name.lower().strip(), race_pace_prediction.get('predicted_pace', 'genuine'))
    if cache_key in _speed_map_cache:
        return _speed_map_cache[cache_key]

    default_result = {
        'pace_advantage_score': 0.0,
        'historical_fast_pace_sr': 0.0,
        'historical_slow_pace_sr': 0.0,
        'pace_preference': 'neutral',
        'outcome_runs': 0,
    }

    try:
        import psycopg2.extras
        conn = get_db_connection()
        if not conn:
            return default_result
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(f"""
            SELECT st.splits_json, COALESCE(st.track, rrh.track) AS track, st.distance_m, st.track_config,
                   rrh.race_date, rrh.race_number, rrh.position as finish_pos, rrh.field_size
            FROM race_results_history rrh
            JOIN LATERAL (
                SELECT st.splits_json, st.track, st.distance_m, st.track_config, st.created_at
                FROM sectional_times st
                WHERE st.splits_json IS NOT NULL
                  AND {_sectional_horse_match_sql("st.horse_name", "rrh.horse_name")}
                  AND (
                    st.race_results_history_id = rrh.id
                    OR ({_sectional_natural_key_sql("st", "rrh")})
                  )
                ORDER BY
                  CASE WHEN st.race_results_history_id = rrh.id THEN 0 ELSE 1 END,
                  st.created_at DESC NULLS LAST
                LIMIT 1
            ) st ON TRUE
            WHERE LOWER(rrh.horse_name) = LOWER(%s)
            ORDER BY rrh.race_date DESC, st.created_at DESC NULLS LAST
            LIMIT 15
        """, (horse_name,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows or len(rows) < 2:
            _speed_map_cache[cache_key] = default_result
            return default_result

        par_table = build_section_par_table()

        fast_pace_results = []
        slow_pace_results = []

        for row in rows:
            sections = _parse_splits_to_sections(row['splits_json'])
            if not sections or len(sections) < 2:
                continue

            trk = row.get('track', '')
            dist = row.get('distance_m', 0)
            tc = row.get('track_config', '')
            finish = row.get('finish_pos')
            fs = row.get('field_size', 0)

            if finish is None:
                continue
            try:
                finish = int(finish)
                fs = int(fs) if fs else 0
            except (ValueError, TypeError):
                continue

            early_sections = [s for s in sections if s.get('mark_m', 0) <= 400 and s.get('time_s', 0) > 0]
            race_early_time = sum(s['time_s'] for s in early_sections)
            if race_early_time <= 0:
                continue

            going = _normalize_going(tc)
            par_early = None
            if par_table:
                pars = par_table.get((trk, dist, going))
                if not pars:
                    pars = par_table.get((trk, dist, '__td__'))
                if not pars:
                    pars = par_table.get(('__any__', dist, '__d__'))
                if pars:
                    par_marks = [m for m in pars.keys() if m <= 400]
                    if par_marks:
                        par_early = sum(pars[m] for m in par_marks)

            if par_early is None:
                continue

            pace_diff = par_early - race_early_time
            won = 1 if finish == 1 else 0
            placed = 1 if finish <= 3 else 0

            if pace_diff > 0.3:
                fast_pace_results.append({'won': won, 'placed': placed, 'finish': finish, 'fs': fs})
            elif pace_diff < -0.3:
                slow_pace_results.append({'won': won, 'placed': placed, 'finish': finish, 'fs': fs})

        fast_sr = 0.0
        slow_sr = 0.0
        if fast_pace_results:
            fast_sr = sum(r['placed'] for r in fast_pace_results) / len(fast_pace_results) * 100
        if slow_pace_results:
            slow_sr = sum(r['placed'] for r in slow_pace_results) / len(slow_pace_results) * 100

        predicted_pace = race_pace_prediction.get('predicted_pace', 'genuine')
        if predicted_pace in ('hot',):
            advantage = (fast_sr - 33.3) / 100 * 0.15
        elif predicted_pace in ('soft', 'slow'):
            advantage = (slow_sr - 33.3) / 100 * 0.15
        else:
            advantage = ((fast_sr + slow_sr) / 2 - 33.3) / 100 * 0.08

        advantage = max(-0.15, min(0.15, advantage))

        if fast_sr > slow_sr + 20:
            preference = 'fast_pace'
        elif slow_sr > fast_sr + 20:
            preference = 'slow_pace'
        else:
            preference = 'neutral'

        total_runs = len(fast_pace_results) + len(slow_pace_results)

        result = {
            'pace_advantage_score': round(advantage, 4),
            'historical_fast_pace_sr': round(fast_sr, 1),
            'historical_slow_pace_sr': round(slow_sr, 1),
            'pace_preference': preference,
            'outcome_runs': total_runs,
        }
        _speed_map_cache[cache_key] = result
        return result
    except Exception as e:
        log_debug(f"calculate_pace_outcome_score error: {e}")
        return default_result


def get_speed_map_features(horse_name, all_runners_profiles, race_pace_prediction):
    _ = all_runners_profiles
    profile = build_horse_running_profile(horse_name)
    pace_outcome = calculate_pace_outcome_score(horse_name, race_pace_prediction)

    style = profile.get('running_style_class', 'unknown')
    style_score_map = {'leader': 4, 'on_pace': 3, 'handy': 2, 'midfield': 1, 'backmarker': 0, 'unknown': 1}
    style_score = style_score_map.get(style, 1)

    predicted_pace = race_pace_prediction.get('predicted_pace', 'genuine')
    is_lone_speed = race_pace_prediction.get('lone_speed', False)

    on_pace_prob = profile.get('on_pace_rate', 0) / 100.0

    running_style_adj = 0.0
    if style == 'leader':
        if is_lone_speed and horse_name in race_pace_prediction.get('confirmed_speed_horses', []):
            running_style_adj = 0.12
        elif predicted_pace == 'hot':
            running_style_adj = -0.06
        elif predicted_pace in ('soft', 'slow'):
            running_style_adj = 0.08
        else:
            running_style_adj = 0.04
    elif style == 'on_pace':
        if predicted_pace == 'hot':
            running_style_adj = -0.02
        elif predicted_pace in ('soft', 'slow'):
            running_style_adj = 0.06
        else:
            running_style_adj = 0.04
    elif style == 'handy':
        running_style_adj = 0.02
    elif style == 'midfield':
        running_style_adj = 0.0
    elif style == 'backmarker':
        if predicted_pace == 'hot':
            running_style_adj = 0.04
        else:
            running_style_adj = -0.06

    consistency_bonus = 0.0
    if profile.get('position_consistency', 0) > 80:
        consistency_bonus = 0.02
    elif profile.get('position_consistency', 0) < 30:
        consistency_bonus = -0.01

    return {
        'speed_map_running_style': style_score,
        'speed_map_early_position_pct': profile.get('avg_early_position_pct', 50),
        'speed_map_position_consistency': profile.get('position_consistency', 0),
        'speed_map_on_pace_rate': on_pace_prob * 100,
        'speed_map_leader_rate': profile.get('leader_rate', 0),
        'speed_map_backmarker_rate': profile.get('backmarker_rate', 0),
        'speed_map_settling_speed': profile.get('settling_speed_sec', 0),
        'speed_map_pace_advantage': pace_outcome.get('pace_advantage_score', 0),
        'speed_map_pace_preference': pace_outcome.get('pace_preference', 'neutral'),
        'speed_map_style_adjustment': round(running_style_adj + consistency_bonus, 4),
        'speed_map_position_runs': profile.get('position_runs', 0),
    }


def analyze_sectional_times(runner: dict, track: str = '', distance_m: int = 0, track_config: str = '') -> dict:
    """
    Enhanced sectional times analysis with distance-weighted scoring, late sectional strength relative to race averages, and finishing burst detection.
    Uses stored database sectionals when available (Racing Queensland data).
    Returns sectional speed rating and finishing speed analysis.
    """
    result = {
        'sectional_speed_rating': 85,
        'has_superior_finishing_speed': False,
        'early_speed_sectional': 0,
        'is_natural_leader': False,
        'last_600_avg': 0,
        'late_speed_score': 0,
        'finishing_burst_detected': False,
        'sectional_trend': 'stable',
        'relative_sectional_strength': 0,
        'last_200_acceleration': 0,
    }
    
    form_details = runner.get('formDetails', []) or []
    
    last_600_times = []
    last_400_times = []
    last_200_times = []
    early_sectionals = []
    race_distances = []
    
    recency_weights = [0.35, 0.25, 0.20, 0.12, 0.08]
    
    for i, detail in enumerate(form_details[:5]):
        sectionals = detail.get('sectionals', {}) or {}
        
        l600 = sectionals.get('last_600', sectionals.get('L600', 0))
        if l600:
            try:
                last_600_times.append((float(l600), recency_weights[i] if i < len(recency_weights) else 0.05))
            except:
                pass
        
        l400 = sectionals.get('last_400', sectionals.get('L400', 0))
        if l400:
            try:
                last_400_times.append((float(l400), recency_weights[i] if i < len(recency_weights) else 0.05))
            except:
                pass
        
        l200 = sectionals.get('last_200', sectionals.get('L200', 0))
        if l200:
            try:
                last_200_times.append((float(l200), recency_weights[i] if i < len(recency_weights) else 0.05))
            except:
                pass
        
        f400 = sectionals.get('first_400', sectionals.get('F400', 0))
        if f400:
            try:
                early_sectionals.append(float(f400))
            except:
                pass
        
        dist = detail.get('distance', 0)
        if dist:
            try:
                race_distances.append(int(str(dist).replace('m', '').strip()))
            except:
                pass
    
    if last_600_times:
        weighted_sum = sum(t * w for t, w in last_600_times)
        weight_total = sum(w for _, w in last_600_times)
        avg_600 = weighted_sum / weight_total if weight_total > 0 else last_600_times[0][0]
        result['last_600_avg'] = avg_600
        
        base_time = 35.5
        if race_distances:
            avg_dist = sum(race_distances) / len(race_distances)
            if avg_dist <= 1200:
                base_time = 34.5
            elif avg_dist >= 2000:
                base_time = 36.5
        
        time_diff = base_time - avg_600
        result['sectional_speed_rating'] = min(100, max(60, 85 + time_diff * 10))
        
        raw_times = [t for t, _ in last_600_times]
        best_600 = min(raw_times)
        if best_600 < avg_600 * 0.97:
            result['has_superior_finishing_speed'] = True
        
        result['late_speed_score'] = min(10, max(0, (base_time - avg_600) * 3))
        
        if len(raw_times) >= 3:
            recent_avg = sum(raw_times[:2]) / 2
            older_avg = sum(raw_times[2:]) / len(raw_times[2:])
            if recent_avg < older_avg * 0.98:
                result['sectional_trend'] = 'improving'
                result['relative_sectional_strength'] = (older_avg - recent_avg) / older_avg * 100
            elif recent_avg > older_avg * 1.02:
                result['sectional_trend'] = 'declining'
                result['relative_sectional_strength'] = -((recent_avg - older_avg) / older_avg * 100)
    
    if last_400_times and last_600_times:
        avg_400 = sum(t for t, _ in last_400_times) / len(last_400_times)
        avg_600_raw = sum(t for t, _ in last_600_times) / len(last_600_times)
        first_200_of_600 = avg_600_raw - avg_400
        if avg_400 > 0 and first_200_of_600 > 0:
            acceleration_ratio = first_200_of_600 / (avg_400 / 2)
            if acceleration_ratio > 1.1:
                result['finishing_burst_detected'] = True
    
    if last_200_times:
        avg_200 = sum(t for t, _ in last_200_times) / len(last_200_times)
        if avg_200 < 11.5:
            result['finishing_burst_detected'] = True
            result['last_200_acceleration'] = max(0, (12.0 - avg_200) * 5)
    
    if early_sectionals:
        avg_early = sum(early_sectionals) / len(early_sectionals)
        result['early_speed_sectional'] = avg_early
        if avg_early < 23.5:
            result['is_natural_leader'] = True
    
    horse_name = runner.get('horse', '') or runner.get('horseName', '')
    if horse_name:
        stored = get_stored_sectionals(horse_name, limit=5)
        if stored:
            result['has_db_sectionals'] = True
            result['db_sectional_count'] = len(stored)
            
            recency_w = [0.35, 0.25, 0.20, 0.12, 0.08]
            
            speeds_600 = []
            speeds_200 = []
            bursts = []
            avg_speeds = []
            
            for i, sec in enumerate(stored):
                w = recency_w[i] if i < len(recency_w) else 0.05
                if sec.get('last_600m_speed') and sec['last_600m_speed'] > 0:
                    speeds_600.append((sec['last_600m_speed'], w))
                if sec.get('last_200m_speed') and sec['last_200m_speed'] > 0:
                    speeds_200.append((sec['last_200m_speed'], w))
                if sec.get('finishing_burst') is not None:
                    bursts.append((sec['finishing_burst'], w))
                if sec.get('avg_speed') and sec['avg_speed'] > 0:
                    avg_speeds.append((sec['avg_speed'], w))
            
            if speeds_600:
                w_sum = sum(s * w for s, w in speeds_600)
                w_total = sum(w for _, w in speeds_600)
                avg_600_speed = w_sum / w_total
                result['db_last_600m_speed'] = round(avg_600_speed, 3)
                speed_rating = min(100, max(60, 50 + avg_600_speed * 2.5))
                if not last_600_times:
                    result['sectional_speed_rating'] = speed_rating
            
            if speeds_200:
                w_sum = sum(s * w for s, w in speeds_200)
                w_total = sum(w for _, w in speeds_200)
                avg_200_speed = w_sum / w_total
                result['db_last_200m_speed'] = round(avg_200_speed, 3)
                if avg_200_speed > 18.0:
                    result['has_superior_finishing_speed'] = True
            
            if bursts:
                w_sum = sum(b * w for b, w in bursts)
                w_total = sum(w for _, w in bursts)
                avg_burst = w_sum / w_total
                result['db_finishing_burst'] = round(avg_burst, 3)
                if avg_burst > 0:
                    result['finishing_burst_detected'] = True
            
            if avg_speeds:
                w_sum = sum(s * w for s, w in avg_speeds)
                w_total = sum(w for _, w in avg_speeds)
                result['db_avg_speed'] = round(w_sum / w_total, 3)
            
            if len(stored) >= 3:
                recent_speeds = [s.get('avg_speed', 0) for s in stored[:2] if s.get('avg_speed')]
                older_speeds = [s.get('avg_speed', 0) for s in stored[2:] if s.get('avg_speed')]
                if recent_speeds and older_speeds:
                    recent_avg = sum(recent_speeds) / len(recent_speeds)
                    older_avg = sum(older_speeds) / len(older_speeds)
                    if recent_avg > older_avg * 1.02:
                        result['sectional_trend'] = 'improving'
                    elif recent_avg < older_avg * 0.98:
                        result['sectional_trend'] = 'declining'
    
    if horse_name and track and distance_m:
        rowlands = calculate_rowlands_adjustment(horse_name, track, distance_m, track_config)
        result['rowlands_finishing_speed_pct'] = rowlands.get('rowlands_finishing_speed_pct', 100.0)
        result['rowlands_par'] = rowlands.get('rowlands_par', 100.0)
        result['rowlands_deviation'] = rowlands.get('rowlands_deviation', 0.0)
        result['rowlands_adjustment'] = rowlands.get('rowlands_adjustment_lbs', 0.0)
        result['rowlands_runs_analyzed'] = rowlands.get('rowlands_runs_analyzed', 0)

    if horse_name:
        pace_profile = build_horse_pace_profile(horse_name)
        result['pace_profile_consistency'] = pace_profile.get('consistency_score', 50.0)
        result['pace_profile_early_median'] = pace_profile.get('early_median', 100.0)
        result['pace_profile_home_median'] = pace_profile.get('home_median', 100.0)
        result['pace_red_zone_threshold'] = pace_profile.get('red_zone_threshold', 999.0)
        result['pace_red_zone_penalty'] = pace_profile.get('red_zone_penalty', 0.0)
        result['pace_position_correlation'] = pace_profile.get('position_pace_correlation')

    if horse_name:
        benchmarks = calculate_horse_benchmarks(horse_name)
        result['early_benchmark'] = benchmarks.get('early_benchmark', 0.0)
        result['late_benchmark'] = benchmarks.get('late_benchmark', 0.0)
        result['overall_benchmark'] = benchmarks.get('overall_benchmark', 0.0)
        result['benchmark_trend'] = benchmarks.get('benchmark_trend', 0)

    return result


def predict_race_shape(all_runners: list, race_data: dict) -> dict:
    """
    Predict pace scenario based on field composition.
    Returns pace scenario and advantages for each running style.
    """
    result = {
        'pace_scenario': 'genuine',
        'speed_horse_count': 0,
        'leader_advantage': 0,
        'closer_advantage': 0,
        'likely_leader': None,
        'pace_pressure_rating': 5
    }
    
    speed_horses = []
    early_speed_ratings = []
    
    for runner in all_runners:
        early_speed = 50
        
        form = runner.get('form', '') or ''
        barrier = runner.get('barrier', runner.get('draw', 10))
        try:
            barrier = int(barrier)
        except:
            barrier = 10
        
        if form and form[0] == '1':
            early_speed += 20
        
        if barrier <= 4:
            early_speed += 10
        elif barrier >= 12:
            early_speed -= 10
        
        style = runner.get('running_style', '').lower()
        if 'leader' in style or 'front' in style:
            early_speed += 25
        elif 'stalker' in style or 'on-pace' in style or 'on pace' in style:
            early_speed += 10
        elif 'closer' in style or 'back' in style:
            early_speed -= 15
        
        early_speed_ratings.append({
            'horse': runner.get('horseName', 'Unknown'),
            'early_speed': early_speed,
            'barrier': barrier
        })
        
        if early_speed >= 70:
            speed_horses.append(runner.get('horseName', 'Unknown'))
    
    result['speed_horse_count'] = len(speed_horses)
    
    if len(speed_horses) >= 3:
        result['pace_scenario'] = 'hot'
        result['leader_advantage'] = -0.10
        result['closer_advantage'] = 0.12
        result['pace_pressure_rating'] = 8
    elif len(speed_horses) == 2:
        result['pace_scenario'] = 'genuine'
        result['leader_advantage'] = 0
        result['closer_advantage'] = 0.03
        result['pace_pressure_rating'] = 6
    elif len(speed_horses) == 1:
        result['pace_scenario'] = 'soft'
        result['leader_advantage'] = 0.15
        result['closer_advantage'] = -0.08
        result['pace_pressure_rating'] = 3
        result['likely_leader'] = speed_horses[0] if speed_horses else None
    else:
        result['pace_scenario'] = 'messy'
        result['leader_advantage'] = 0.05
        result['closer_advantage'] = -0.05
        result['pace_pressure_rating'] = 4
    
    return result


def calculate_weight_adjusted_rating(runner: dict, race_data: dict) -> dict:
    """
    Calculate weight-adjusted performance rating.
    Uses distance-based weight factors.
    """
    result = {
        'weight_adjusted_rating': 85,
        'weight_advantage': 0,
        'weight_carried': 0,
        'benchmark_weight': 56
    }
    
    weight_str = runner.get('weight', runner.get('wgt', '56'))
    try:
        weight = float(str(weight_str).replace('kg', '').strip())
    except:
        weight = 56
    
    result['weight_carried'] = weight
    
    sex = runner.get('sex', runner.get('sexCode', 'C')).upper()
    age = runner.get('age', 4)
    try:
        age = int(age)
    except:
        age = 4
    
    if sex in ['F', 'M', 'FILLY', 'MARE']:
        benchmark = 55
    else:
        benchmark = 57
    
    if age == 3:
        benchmark -= 3.0
    
    result['benchmark_weight'] = benchmark
    
    distance_str = race_data.get('distance', '1400')
    try:
        distance = int(str(distance_str).replace('m', '').strip())
    except:
        distance = 1400
    
    # Distance-based weight factors (points per kg above/below benchmark)
    if distance <= 1200:
        weight_factor = 0.35  # Sprints - weight matters less
    elif distance <= 1600:
        weight_factor = 0.55  # Mile
    elif distance <= 2000:
        weight_factor = 0.75  # Middle distance
    else:
        weight_factor = 1.0   # Staying - weight critical
    
    weight_diff = benchmark - weight  # Positive = carrying less = advantage
    result['weight_advantage'] = weight_diff * weight_factor
    result['weight_adjusted_rating'] = 85 + result['weight_advantage']
    
    return result


def analyze_breeding(runner: dict, race_data: dict) -> dict:
    """Analyze sire/dam breeding for distance and track condition suitability."""
    result = {
        'breeding_distance_match': 1.0,
        'wet_track_breeding_bonus': 1.0,
        'is_speed_bred': False,
        'is_staying_bred': False,
        'has_wet_track_bloodline': False
    }
    
    SPEED_SIRES = ['snitzel', 'i am invincible', 'exceed and excel', 'written tycoon', 
                   'zoustar', 'rubick', 'capitalist', 'spirit of boom', 'better than ready',
                   'star witness', 'sebring', 'deep field', 'trapeze artist']
    
    STAYING_SIRES = ['galileo', 'ocean park', 'savabeel', 'pierro', 'camelot',
                     'dundeel', 'melbourne cup winner', 'reliable man', 'pour moi',
                     'teofilo', 'sea the stars', 'frankel', 'caulfield cup winner']
    
    WET_TRACK_SIRES = ['so you think', 'street cry', 'fastnet rock', 'savabeel',
                       'ocean park', 'dundeel', 'shamardal', 'redoute\'s choice',
                       'lonhro', 'teofilo']
    
    sire = (runner.get('sire', '') or '').lower()
    dam_sire = (runner.get('damSire', runner.get('dam_sire', '')) or '').lower()
    
    distance_str = race_data.get('distance', '1400')
    try:
        distance = int(str(distance_str).replace('m', '').strip())
    except:
        distance = 1400
    
    is_speed = any(s in sire for s in SPEED_SIRES) or any(s in dam_sire for s in SPEED_SIRES)
    is_staying = any(s in sire for s in STAYING_SIRES) or any(s in dam_sire for s in STAYING_SIRES)
    has_wet_blood = any(s in sire for s in WET_TRACK_SIRES) or any(s in dam_sire for s in WET_TRACK_SIRES)
    
    result['is_speed_bred'] = is_speed
    result['is_staying_bred'] = is_staying
    result['has_wet_track_bloodline'] = has_wet_blood
    
    if is_speed and distance <= 1200:
        result['breeding_distance_match'] = 1.06
    elif is_speed and distance >= 2000:
        result['breeding_distance_match'] = 0.94
    elif is_staying and distance >= 2000:
        result['breeding_distance_match'] = 1.08
    elif is_staying and distance <= 1200:
        result['breeding_distance_match'] = 0.94
    
    going = (race_data.get('going', race_data.get('trackCondition', '')) or '').lower()
    is_wet = 'soft' in going or 'heavy' in going or 'wet' in going
    
    if has_wet_blood and is_wet:
        result['wet_track_breeding_bonus'] = 1.06
    
    return result


def analyze_trainer_intent(runner: dict, race_data: dict) -> dict:
    """Analyze trainer patterns to detect strategic intent signals."""
    result = {
        'is_first_up_specialist': False,
        'is_second_up_improver': False,
        'in_optimal_freshness_window': False,
        'trainer_targets_track': False,
        'is_first_up': False,
        'is_second_up': False,
        'trainer_intent_adjustment': 1.0,
        'days_since_last_run': 0
    }
    
    last_raced = runner.get('last_raced', runner.get('lastRaced', ''))
    days_since = runner.get('days_since_last_run', 0)
    form_str = str(runner.get('form', '')).strip()
    
    if form_str and not days_since:
        for idx, ch in enumerate(form_str[:6]):
            if ch.lower() == 'x':
                if idx == 0:
                    days_since = 90
                elif idx == 1:
                    days_since = 28
                break
    
    if last_raced and not days_since:
        try:
            from datetime import datetime
            race_date_str = race_data.get('date', race_data.get('raceDate', ''))
            if race_date_str:
                race_date = datetime.strptime(str(race_date_str)[:10], '%Y-%m-%d')
            else:
                race_date = datetime.now()
            if isinstance(last_raced, str):
                last_date = datetime.strptime(last_raced[:10], '%Y-%m-%d')
                days_since = max(0, (race_date - last_date).days)
        except:
            days_since = 0
    
    result['days_since_last_run'] = days_since
    
    if days_since >= 60:
        result['is_first_up'] = True
    elif days_since >= 21 and days_since <= 45:
        result['is_second_up'] = True
    
    if 21 <= days_since <= 35:
        result['in_optimal_freshness_window'] = True
        result['trainer_intent_adjustment'] = 1.04
    
    trainer_stats = runner.get('trainer_stats', runner.get('trainerStats', {})) or {}
    
    first_up_stats = trainer_stats.get('first_up', trainer_stats.get('firstUp', {})) or {}
    first_up_runs = first_up_stats.get('runs', first_up_stats.get('total', 0))
    first_up_wins = first_up_stats.get('wins', 0)
    
    if first_up_runs >= 20 and first_up_wins / first_up_runs >= 0.25:
        result['is_first_up_specialist'] = True
    
    second_up_stats = trainer_stats.get('second_up', trainer_stats.get('secondUp', {})) or {}
    second_up_runs = second_up_stats.get('runs', second_up_stats.get('total', 0))
    second_up_wins = second_up_stats.get('wins', 0)
    
    if second_up_runs >= 15 and second_up_wins / second_up_runs >= 0.30:
        result['is_second_up_improver'] = True
    
    track = race_data.get('track', race_data.get('course', ''))
    course_stats = trainer_stats.get('course', trainer_stats.get('track', {})) or {}
    
    course_runs = course_stats.get('runs', course_stats.get('total', 0))
    course_wins = course_stats.get('wins', 0)
    
    if course_runs >= 20 and course_wins / course_runs >= 0.20:
        result['trainer_targets_track'] = True
    
    return result


def get_race_shape_advantage(runner: dict, race_shape: dict) -> float:
    """Calculate race shape advantage for a specific runner."""
    advantage = 0
    
    style = (runner.get('running_style', '') or '').lower()
    
    if 'leader' in style or 'front' in style:
        advantage = race_shape.get('leader_advantage', 0)
    elif 'closer' in style or 'back' in style:
        advantage = race_shape.get('closer_advantage', 0)
    elif 'stalker' in style or 'on-pace' in style:
        advantage = race_shape.get('leader_advantage', 0) * 0.5
    
    return advantage


def extract_all_sophisticated_features(runner: dict, all_runners: list, race_data: dict, franking_data: dict = None) -> dict:
    """
    Master function to extract all sophisticated prediction features.
    Combines collateral form, jockey/trainer hot streaks, distance progression, track condition suitability, and market intelligence.
    """
    features = {}
    
    base_features = extract_ml_features(runner, race_data, all_runners)
    features.update(base_features)
    
    pace_features = extract_speed_and_pace_features(runner, all_runners, race_data)
    features.update(pace_features)
    
    class_features = extract_class_movement_features(runner, race_data)
    features.update(class_features)
    
    signal_features = extract_trainer_jockey_signals(runner, race_data)
    features.update(signal_features)
    
    bias_features = extract_track_bias_features(runner, race_data)
    features.update(bias_features)
    
    form_features = extract_form_pattern_features(runner, race_data)
    features.update(form_features)
    
    market_features = extract_market_intelligence_features(runner, all_runners, race_data)
    features.update(market_features)
    
    if ADVANCED_FEATURES_AVAILABLE:
        try:
            advanced = extract_db_features(runner, race_data)
            
            features['collateral_form_boost'] = 1.0
            features['has_franked_form'] = 0
            
            hot_streak = advanced.get('features', {}).get('hot_streak', {})
            features['jockey_hot_streak'] = 1 if hot_streak.get('jockey_hot') else 0
            features['trainer_hot_streak'] = 1 if hot_streak.get('trainer_hot') else 0
            features['combo_hot_streak'] = 1 if hot_streak.get('combo_hot') else 0
            features['hot_streak_adjustment'] = hot_streak.get('adjustment', 1.0)
            
            form_patterns = advanced.get('features', {}).get('form', {})
            features['is_improving_form'] = 1 if form_patterns.get('improving') else 0
            features['is_declining_form'] = 1 if form_patterns.get('declining') else 0
            if 'consistency' in form_patterns:
                features['form_consistency'] = form_patterns['consistency']
            features['peak_form'] = 1 if form_patterns.get('peak_form') else 0
            
            distance_analysis = advanced.get('features', {}).get('distance', {})
            features['proven_at_distance'] = 1 if distance_analysis.get('proven_at_distance') else 0
            features['stepping_up_distance'] = 1 if distance_analysis.get('stepping_up') else 0
            features['stepping_down_distance'] = 1 if distance_analysis.get('stepping_down') else 0
            features['distance_adjustment'] = distance_analysis.get('distance_adjustment', 1.0)
            
            going_analysis = advanced.get('features', {}).get('going', {})
            features['wet_track_specialist'] = 1 if going_analysis.get('wet_track_specialist') else 0
            features['dry_track_specialist'] = 1 if going_analysis.get('dry_track_specialist') else 0
            features['going_adjustment'] = going_analysis.get('going_adjustment', 1.0)
            
            features['advanced_combined_adjustment'] = advanced.get('combined_adjustment', 1.0)
            
            class_level = advanced.get('features', {}).get('class_level', 50)
            features['parsed_class_level'] = class_level
            
        except Exception as e:
            log_debug(f"Advanced feature extraction error: {e}")
            features['advanced_combined_adjustment'] = 1.0
    else:
        features['advanced_combined_adjustment'] = 1.0
    
    horse_id = runner.get('horse_id') or runner.get('horseid') or runner.get('horseId')
    if FRANKING_AVAILABLE and horse_id and franking_data:
        frank = franking_data.get(horse_id, {})
        features['franking_elo'] = frank.get('global_elo', 50.0)
        features['franking_score'] = frank.get('franking_score', 50.0)
        features['franking_confidence'] = frank.get('franking_confidence', 0.1)
        features['anti_franked'] = 1 if frank.get('anti_franked') else 0
        features['field_strength_avg'] = frank.get('field_strength_avg', 50.0)
        features['form_quality_trend'] = frank.get('form_quality_trend', 0.0)
        features['best_adjusted_margin'] = frank.get('best_adjusted_margin', 0.0)
        features['collateral_advantage'] = frank.get('collateral_advantage', 0.0)
        features['has_franked_form'] = 1 if frank.get('franking_score', 50.0) > 55 else 0
    else:
        features['franking_elo'] = 50.0
        features['franking_score'] = 50.0
        features['franking_confidence'] = 0.1
        features['anti_franked'] = 0
        features['field_strength_avg'] = 50.0
        features['form_quality_trend'] = 0.0
        features['best_adjusted_margin'] = 0.0
        features['collateral_advantage'] = 0.0
    
    if FRANKING_GRAPH_AVAILABLE and horse_id:
        try:
            _fg = get_franking_graph()
            _gp = _fg.compute_full_franking_profile(horse_id)
            features['graph_franking_score'] = _gp.get('franking_score', 0.0)
            features['graph_franking_momentum'] = _gp.get('franking_momentum', 0.0)
            features['graph_franking_confidence'] = _gp.get('franking_confidence', 0.0)
            features['graph_franking_depth'] = _gp.get('franking_depth', 0)
            features['graph_franking_independence'] = _gp.get('franking_independence', 0)
            features['pagerank_authority'] = _gp.get('pagerank_authority', 0.0)
            features['personalised_pagerank'] = _gp.get('personalised_pagerank', 0.0)
            features['community_strength'] = _gp.get('community_strength', 0.5)
            features['community_id'] = _gp.get('community_id', -1)
            features['form_stability'] = _gp.get('form_stability', 0.5)
            features['bridge_score'] = _gp.get('bridge_score', 0.0)
            features['market_validated'] = 1 if _gp.get('market_validated', False) else 0
            features['market_agreement_score'] = _gp.get('market_agreement_score', 0.5)
            features['excuse_adjusted_anti_franks'] = _gp.get('excuse_adjusted_anti_franks', 0)
            features['collapsed_race_exposure'] = _gp.get('collapsed_race_exposure', 0.0)
            features['graph_anti_franked'] = 1 if _gp.get('anti_franked', False) else 0
            features['graph_anti_frank_ratio'] = _gp.get('anti_frank_ratio', 0.5)
        except Exception as _gfe:
            log_debug(f"Graph franking feature error: {_gfe}")
    if 'graph_franking_score' not in features:
        features['graph_franking_score'] = 0.0
        features['graph_franking_momentum'] = 0.0
        features['graph_franking_confidence'] = 0.0
        features['graph_franking_depth'] = 0
        features['graph_franking_independence'] = 0
        features['pagerank_authority'] = 0.0
        features['personalised_pagerank'] = 0.0
        features['community_strength'] = 0.5
        features['community_id'] = -1
        features['form_stability'] = 0.5
        features['bridge_score'] = 0.0
        features['market_validated'] = 0
        features['market_agreement_score'] = 0.5
        features['excuse_adjusted_anti_franks'] = 0
        features['collapsed_race_exposure'] = 0.0
        features['graph_anti_franked'] = 0
        features['graph_anti_frank_ratio'] = 0.5
    
    try:
        if MC_ENABLE_SECTIONAL_FRANKING and horse_name_for_pace:
            sect_frank = get_sectional_franking_features(horse_name_for_pace)
            features['sectional_franking_value'] = sect_frank.get('sectional_franking_value', 0.0)
            features['franking_events_count'] = sect_frank.get('franking_events_count', 0)
            features['best_sectional_frank'] = sect_frank.get('best_sectional_frank', 0.0)
        else:
            features['sectional_franking_value'] = 0.0
            features['franking_events_count'] = 0
            features['best_sectional_frank'] = 0.0
    except Exception:
        features['sectional_franking_value'] = 0.0
        features['franking_events_count'] = 0
        features['best_sectional_frank'] = 0.0

    if MARKET_ANALYSIS_AVAILABLE:
        try:
            market_features = extract_market_features(runner, all_runners)
            features['opening_odds'] = market_features.get('opening_odds')
            features['steam_drift_pct'] = market_features.get('steam_drift_pct', 0)
            features['market_move_category'] = market_features.get('market_move_category', 'unknown')
            features['is_sharp_money'] = 1 if market_features.get('is_sharp_money') else 0
            features['market_adjustment'] = market_features.get('market_adjustment', 1.0)
            features['is_favorite_steamer'] = 1 if market_features.get('is_favorite_steamer') else 0
            features['is_longshot_steamer'] = 1 if market_features.get('is_longshot_steamer') else 0
        except Exception as e:
            log_debug(f"Market analysis error: {e}")
            features['market_adjustment'] = 1.0
    else:
        features['market_adjustment'] = 1.0
    
    if SPEED_RATINGS_AVAILABLE:
        try:
            speed_features = extract_speed_features(runner, race_data)
            features['speed_rating'] = speed_features.get('speed_rating', 85)
            features['last_speed_rating'] = speed_features.get('last_speed_rating', 85)
            features['best_speed_rating'] = speed_features.get('best_speed_rating', 85)
            features['improvement_trend'] = speed_features.get('improvement_trend', 0)
            features['late_speed'] = speed_features.get('late_speed', 0)
            features['is_strong_closer'] = 1 if speed_features.get('is_strong_closer') else 0
            features['rating_above_class'] = 1 if speed_features.get('rating_above_class') else 0
        except Exception as e:
            log_debug(f"Speed rating error: {e}")
            features['speed_rating'] = 85
    else:
        features['speed_rating'] = 85
    
    if PACE_MODELING_AVAILABLE:
        try:
            pace_features = extract_pace_features(runner, all_runners, race_data)
            features['enhanced_running_style'] = pace_features.get('running_style', 'midfield')
            features['predicted_pace'] = pace_features.get('predicted_pace', 'moderate')
            features['expected_pace_advantage'] = pace_features.get('pace_advantage', 0)
            features['is_lone_speed'] = 1 if pace_features.get('is_lone_speed') else 0
            features['suits_pace_scenario'] = 1 if pace_features.get('suits_pace_scenario') else 0
            features['is_pace_disadvantage'] = 1 if pace_features.get('is_pace_disadvantage') else 0
        except Exception as e:
            log_debug(f"Pace modeling error: {e}")
            features['expected_pace_advantage'] = 0
    else:
        features['expected_pace_advantage'] = 0
    
    if SPEED_MAPPING_AVAILABLE:
        try:
            speed_map_features = extract_pace_features_for_mc(runner, all_runners, race_data)
            features['running_style'] = speed_map_features.get('running_style', 'midfield')
            features['style_confidence'] = speed_map_features.get('style_confidence', 0.5)
            features['pace_scenario'] = speed_map_features.get('pace_scenario', 'moderate')
            features['pace_advantage'] = speed_map_features.get('pace_advantage', 0)
            features['is_lone_speed'] = 1 if speed_map_features.get('is_lone_speed', False) else 0
            features['has_covered_run'] = 1 if speed_map_features.get('has_covered_run', False) else 0
            features['is_wide_no_cover'] = 1 if speed_map_features.get('is_wide_no_cover', False) else 0
            features['settling_position'] = speed_map_features.get('settling_position', 0)
            features['expected_section'] = speed_map_features.get('expected_section', 'midfield')
            features['rail_position'] = speed_map_features.get('rail_position', 'one_off')
            features['pace_flags'] = speed_map_features.get('pace_flags', [])
            features['pace_pressure'] = speed_map_features.get('pace_pressure', 5)
            features['leader_count'] = speed_map_features.get('leader_count', 0)
            features['speed_map_recommendation'] = speed_map_features.get('speed_map_recommendation', '')
        except Exception as e:
            log_debug(f"Speed mapping error: {e}")
            features['pace_advantage'] = 0
            features['pace_flags'] = []
    else:
        features['pace_advantage'] = features.get('expected_pace_advantage', 0)
        features['pace_flags'] = []
    
    try:
        margin_features = analyze_beaten_margins(runner)
        features['avg_beaten_margin'] = margin_features.get('avg_beaten_margin', 0)
        features['narrow_defeat_count'] = margin_features.get('narrow_defeat_count', 0)
        features['is_unlucky_loser'] = 1 if margin_features.get('is_unlucky_loser') else 0
        features['due_for_win_adjustment'] = margin_features.get('due_for_win_adjustment', 1.0)
        features['margin_has_dominant_win'] = 1 if margin_features.get('has_dominant_win') else 0
        
        current_race_class = race_data.get('race_class', race_data.get('class', ''))
        current_class_level = 50
        if ADVANCED_FEATURES_AVAILABLE:
            current_class_level = parse_class_level(current_race_class, race_data.get('prize', 0))
        
        class_margin_features = analyze_class_weighted_margins(runner, current_race_class, current_class_level)
        features['class_weighted_margin_score'] = class_margin_features.get('class_weighted_margin_score', 0)
        features['class_drop_with_margin_value'] = class_margin_features.get('class_drop_with_margin_value', 0)
        features['class_transition_adjustment'] = class_margin_features.get('class_transition_adjustment', 1.0)
        features['form_at_higher_class'] = 1 if class_margin_features.get('form_at_higher_class') else 0
        features['best_form_class'] = class_margin_features.get('best_form_class', 0)
        features['class_margin_insight'] = class_margin_features.get('class_margin_insight', '')
        
        market_movement = analyze_market_movement(runner, all_runners)
        features['steam_drift_pct'] = market_movement.get('steam_drift_pct', 0)
        features['market_movement_category'] = market_movement.get('movement_category', 'steady')
        features['is_sharp_money'] = 1 if market_movement.get('is_sharp_money') else 0
        features['is_longshot_steamer'] = 1 if market_movement.get('is_longshot_steamer') else 0
        features['market_insight'] = market_movement.get('market_insight', '')
        features['market_movement_adjustment'] = market_movement.get('market_adjustment', 1.0)
        
        race_track = race_data.get('track', race_data.get('venue', ''))
        race_distance_m = 0
        try:
            race_distance_m = int(str(race_data.get('distance', race_data.get('distance_m', 0))).replace('m', '').strip())
        except (ValueError, TypeError):
            pass
        race_track_config = race_data.get('going', race_data.get('track_config', race_data.get('trackCondition', '')))
        sectional_features = analyze_sectional_times(runner, track=race_track, distance_m=race_distance_m, track_config=race_track_config)
        features['sectional_speed_rating'] = sectional_features.get('sectional_speed_rating', 85)
        features['has_superior_finishing_speed'] = 1 if sectional_features.get('has_superior_finishing_speed') else 0
        features['is_natural_leader'] = 1 if sectional_features.get('is_natural_leader') else 0
        features['late_speed_score'] = sectional_features.get('late_speed_score', 0)
        features['rowlands_finishing_speed_pct'] = sectional_features.get('rowlands_finishing_speed_pct', 100)
        features['rowlands_deviation'] = sectional_features.get('rowlands_deviation', 0)
        features['rowlands_adjustment'] = sectional_features.get('rowlands_adjustment', 0)
        
        race_shape = predict_race_shape(all_runners, race_data)
        features['pace_scenario'] = race_shape.get('pace_scenario', 'genuine')
        features['speed_horse_count'] = race_shape.get('speed_horse_count', 0)
        features['pace_pressure_rating'] = race_shape.get('pace_pressure_rating', 5)
        features['race_shape_advantage'] = get_race_shape_advantage(runner, race_shape)
        
        horse_name_for_pace = str(runner.get('horseName', runner.get('horse', ''))).strip()
        if horse_name_for_pace:
            pace_impact = calculate_pace_scenario_impact(
                horse_name_for_pace,
                race_shape.get('pace_pressure_rating', 5),
                race_shape.get('speed_horse_count', 0),
                race_distance_m
            )
            features['pace_profile_consistency'] = pace_impact.get('pace_profile_consistency', 50)
            features['pace_red_zone_margin'] = pace_impact.get('pace_red_zone_margin', 10)
            features['pace_scenario_compatibility'] = pace_impact.get('pace_scenario_compatibility', 50)
            features['pace_position_impact'] = pace_impact.get('pace_position_impact', 0)
            features['pace_deviation_penalty'] = pace_impact.get('pace_deviation_penalty', 0)

        features['early_benchmark'] = sectional_features.get('early_benchmark', 0)
        features['late_benchmark'] = sectional_features.get('late_benchmark', 0)
        features['overall_benchmark'] = sectional_features.get('overall_benchmark', 0)
        features['benchmark_trend'] = sectional_features.get('benchmark_trend', 0)

        try:
            peak_feats = get_peak_ability_features(horse_name_for_pace or '')
            features['peak_ability_ceiling'] = peak_feats.get('peak_ability_ceiling', 0)
            features['peak_vs_average_gap'] = peak_feats.get('peak_vs_average_gap', 0)
            features['hidden_form_score'] = peak_feats.get('hidden_form_score', 0)
            features['peak_speed_trend'] = peak_feats.get('peak_speed_trend', 0)
            features['peak_closing_speed'] = peak_feats.get('peak_closing_speed', 0)
            features['compromised_runs'] = peak_feats.get('compromised_runs', 0)
        except Exception:
            features['peak_ability_ceiling'] = 0
            features['peak_vs_average_gap'] = 0
            features['hidden_form_score'] = 0
            features['peak_speed_trend'] = 0
            features['peak_closing_speed'] = 0
            features['compromised_runs'] = 0

        try:
            decel_feats = get_deceleration_features(horse_name_for_pace or '', race_distance_m)
            features['decel_rate_current_dist'] = decel_feats.get('decel_rate_current_dist', 0.0)
            features['decel_trend_across_distances'] = decel_feats.get('decel_trend_across_distances', 0.0)
            features['distance_ceiling'] = decel_feats.get('distance_ceiling', 2400)
            features['stamina_score'] = decel_feats.get('stamina_score', 50.0)
            features['step_up_candidate'] = decel_feats.get('step_up_candidate', 0.0)
            features['distance_limited'] = decel_feats.get('distance_limited', 0.0)
        except Exception:
            features['decel_rate_current_dist'] = 0.0
            features['decel_trend_across_distances'] = 0.0
            features['distance_ceiling'] = 2400
            features['stamina_score'] = 50.0
            features['step_up_candidate'] = 0.0
            features['distance_limited'] = 0.0

        try:
            if MC_ENABLE_JOCKEY_EFFICIENCY:
                jockey_name_for_eff = runner.get('jockey', '') or runner.get('jockey_name', '')
                jockey_eff = get_jockey_efficiency_features(jockey_name_for_eff, horse_name_for_pace or '')
                features['jockey_efficiency_score'] = jockey_eff.get('jockey_efficiency_score', 50.0)
                features['jockey_pace_match'] = jockey_eff.get('jockey_pace_match', 50.0)
                features['jockey_cook_rate'] = jockey_eff.get('jockey_cook_rate', 15.0)
                features['jockey_finishing_efficiency'] = jockey_eff.get('jockey_finishing_efficiency', 0.0)
            else:
                features['jockey_efficiency_score'] = 50.0
                features['jockey_pace_match'] = 50.0
                features['jockey_cook_rate'] = 15.0
                features['jockey_finishing_efficiency'] = 0.0
        except Exception:
            features['jockey_efficiency_score'] = 50.0
            features['jockey_pace_match'] = 50.0
            features['jockey_cook_rate'] = 15.0
            features['jockey_finishing_efficiency'] = 0.0

        if horse_name_for_pace and all_runners:
            try:
                runners_profiles = []
                for r in all_runners:
                    if not is_active_runner(r):
                        continue
                    hn = str(r.get('horseName', r.get('horse', ''))).strip()
                    if hn:
                        rp = build_horse_running_profile(hn)
                        runners_profiles.append({'horse_name': hn, 'profile': rp})
                race_pace = predict_race_pace_from_sectionals(
                    runners_profiles, race_distance_m,
                    track=race_track, track_config=race_track_config
                )
                sm_features = get_speed_map_features(horse_name_for_pace, runners_profiles, race_pace)
                features['speed_map_running_style'] = sm_features.get('speed_map_running_style', 1)
                features['speed_map_early_position_pct'] = sm_features.get('speed_map_early_position_pct', 50)
                features['speed_map_position_consistency'] = sm_features.get('speed_map_position_consistency', 0)
                features['speed_map_on_pace_rate'] = sm_features.get('speed_map_on_pace_rate', 0)
                features['speed_map_leader_rate'] = sm_features.get('speed_map_leader_rate', 0)
                features['speed_map_backmarker_rate'] = sm_features.get('speed_map_backmarker_rate', 0)
                features['speed_map_settling_speed'] = sm_features.get('speed_map_settling_speed', 0)
                features['speed_map_pace_advantage'] = sm_features.get('speed_map_pace_advantage', 0)
                features['speed_map_style_adjustment'] = sm_features.get('speed_map_style_adjustment', 0)
                features['speed_map_position_runs'] = sm_features.get('speed_map_position_runs', 0)
                features['speed_map_predicted_pace'] = race_pace.get('predicted_pace', 'genuine')
                features['speed_map_pace_pressure'] = race_pace.get('pace_pressure', 5)
                features['speed_map_early_vs_par'] = race_pace.get('early_pace_vs_par', 0)
                features['speed_map_lone_speed'] = 1 if race_pace.get('lone_speed') else 0
                features['speed_map_contested_lead'] = 1 if race_pace.get('contested_lead') else 0
            except Exception as e:
                log_debug(f"Speed map feature extraction error: {e}")

        weight_features = calculate_weight_adjusted_rating(runner, race_data)
        features['weight_adjusted_rating'] = weight_features.get('weight_adjusted_rating', 85)
        features['weight_advantage'] = weight_features.get('weight_advantage', 0)
        
        breeding_features = analyze_breeding(runner, race_data)
        features['breeding_distance_match'] = breeding_features.get('breeding_distance_match', 1.0)
        features['wet_track_breeding_bonus'] = breeding_features.get('wet_track_breeding_bonus', 1.0)
        features['is_speed_bred'] = 1 if breeding_features.get('is_speed_bred') else 0
        features['is_staying_bred'] = 1 if breeding_features.get('is_staying_bred') else 0
        features['has_wet_track_bloodline'] = 1 if breeding_features.get('has_wet_track_bloodline') else 0
        
        trainer_intent = analyze_trainer_intent(runner, race_data)
        features['is_first_up_specialist'] = 1 if trainer_intent.get('is_first_up_specialist') else 0
        features['is_second_up_improver'] = 1 if trainer_intent.get('is_second_up_improver') else 0
        features['in_optimal_freshness_window'] = 1 if trainer_intent.get('in_optimal_freshness_window') else 0
        features['trainer_targets_track'] = 1 if trainer_intent.get('trainer_targets_track') else 0
        features['is_first_up'] = 1 if trainer_intent.get('is_first_up') else 0
        features['is_second_up'] = 1 if trainer_intent.get('is_second_up') else 0
        features['days_since_last_run'] = trainer_intent.get('days_since_last_run', 0)
        
    except Exception as e:
        log_debug(f"New sophisticated feature extraction error: {e}")
    
    
    if JOCKEY_MOMENTUM_AVAILABLE:
        try:
            jockey_name = str(runner.get('jockey', '')).strip()
            trainer_name = str(runner.get('trainer', '')).strip()
            track = str(race_data.get('track', race_data.get('course', ''))).strip()
            race_date = str(race_data.get('date', race_data.get('race_date', ''))).strip()
            
            if jockey_name:
                jm = score_jockey_momentum(jockey_name, track, race_date)
                features['jockey_momentum_score'] = jm.get('momentum_score', 50)
                features['jockey_is_hot_streak'] = 1 if jm.get('is_hot_streak') else 0
                features['jockey_is_cold_streak'] = 1 if jm.get('is_cold_streak') else 0
                features['jockey_strike_rate_30d'] = jm.get('strike_rate_30d', 0)
                features['jockey_momentum_adjustment'] = jm.get('momentum_adjustment', 1.0)
                features['jockey_momentum_insight'] = jm.get('momentum_insight', '')
            
            if trainer_name:
                tm = score_trainer_momentum(trainer_name, track, race_date)
                features['trainer_momentum_score'] = tm.get('momentum_score', 50)
                features['trainer_is_hot_streak'] = 1 if tm.get('is_hot_streak') else 0
                features['trainer_momentum_adjustment'] = tm.get('momentum_adjustment', 1.0)
        except Exception as e:
            log_debug(f"Jockey momentum error: {e}")
    
    if WEIGHT_OPTIMIZER_AVAILABLE and _barrier_analyzer:
        try:
            barrier = int(runner.get('draw', runner.get('barrier', 0)) or 0)
            distance_str = str(race_data.get('distance', '0'))
            try:
                distance_m = int(distance_str.replace('m', '').strip())
            except ValueError:
                distance_m = 1400
            
            if all_runners:
                field_sz = len([r for r in all_runners if is_active_runner(r)])
            else:
                field_sz = int(race_data.get('field_size', 12) or 12)
            
            track = str(race_data.get('track', race_data.get('course', ''))).strip()
            
            barrier_result = _barrier_analyzer.get_barrier_score(barrier, field_sz, distance_m, track)
            features['data_barrier_score'] = barrier_result.get('barrier_score', 50)
            features['data_barrier_win_rate'] = barrier_result.get('barrier_win_rate', 0)
            features['data_barrier_adjustment'] = barrier_result.get('barrier_adjustment', 1.0)
            features['data_barrier_is_advantageous'] = 1 if barrier_result.get('is_advantageous') else 0
            
            leader_rate = _barrier_analyzer.get_track_leader_rate(track)
            features['track_leader_rate'] = leader_rate
        except Exception as e:
            log_debug(f"Barrier analyzer error: {e}")
    
    if TRACK_CONDITION_AVAILABLE and _track_condition_db:
        try:
            horse_name = str(runner.get('horseName', runner.get('horse', ''))).strip()
            going = str(race_data.get('going', '')).strip()
            track = str(race_data.get('track', race_data.get('course', ''))).strip()
            
            if horse_name and going:
                going_result = _track_condition_db.get_going_suitability_score(horse_name, going, track)
                features['going_suitability_score'] = going_result.get('suitability_score', 50)
                features['going_suitability_adjustment'] = going_result.get('suitability_adjustment', 1.0)
                features['is_going_specialist'] = 1 if going_result.get('is_going_specialist') else 0
                features['going_insight'] = going_result.get('going_insight', '')
        except Exception as e:
            log_debug(f"Track condition DB error: {e}")
    
    if TRACK_CONDITION_AVAILABLE and _pace_energy_model:
        try:
            running_style = features.get('running_style', features.get('enhanced_running_style', 'midfield'))
            distance_str = str(race_data.get('distance', '0'))
            try:
                distance_m = int(distance_str.replace('m', '').strip())
            except ValueError:
                distance_m = 1400
            
            pace_pressure = features.get('pace_pressure_rating', features.get('pace_pressure', 5))
            days_since = features.get('days_since_last_run', 30)
            
            energy = _pace_energy_model.calculate_energy_curve(running_style, distance_m, pace_pressure / 10.0, days_since)
            features['energy_at_800m'] = energy.get('energy_at_800m', 80)
            features['energy_at_400m'] = energy.get('energy_at_400m', 65)
            features['energy_at_finish'] = energy.get('energy_at_finish', 50)
            features['fatigue_factor'] = energy.get('fatigue_factor', 0.95)
            features['collapse_probability'] = energy.get('collapse_probability', 0.05)
            
            fitness = _pace_energy_model.get_fitness_factor(days_since)
            features['fitness_factor'] = fitness
        except Exception as e:
            log_debug(f"Pace energy modeling error: {e}")
    
    
    if ENHANCED_SPEED_MAP_AVAILABLE:
        try:
            esm_features = extract_enhanced_speed_map_features(runner, all_runners, race_data)
            features['predicted_settling_pos'] = esm_features.get('predicted_settling_pos', 0)
            features['settling_percentile'] = esm_features.get('settling_percentile', 50.0)
            features['pace_pressure_index'] = esm_features.get('pace_pressure_index', 30.0)
            features['settling_difficulty'] = esm_features.get('settling_difficulty', 0.5)
            features['settling_pace_interaction'] = esm_features.get('settling_pace_interaction', 0.0)
            features['leader_congestion'] = esm_features.get('leader_congestion', 0)
            features['speed_horse_ratio'] = esm_features.get('speed_horse_ratio', 0.3)
            features['is_congested_speed'] = 1 if esm_features.get('is_congested_speed') else 0
            features['settling_vs_barrier'] = esm_features.get('settling_vs_barrier', 0.0)
        except Exception as e:
            log_debug(f"Enhanced speed map error: {e}")
    
    if TEMPORAL_STALENESS_AVAILABLE:
        try:
            staleness = compute_staleness_features(runner, race_data)
            features['days_since_last_normalized'] = staleness.get('days_since_last_normalized', 0.5)
            features['prep_run_x_days_since'] = staleness.get('prep_run_x_days_since', 0.0)
            features['run_spacing_quality'] = staleness.get('run_spacing_quality', 0.5)
            features['freshness_zone'] = staleness.get('freshness_zone', 'moderate')
            features['empirical_freshness_score'] = staleness.get('freshness_score', 0.7)
            features['is_quick_backup'] = 1 if staleness.get('is_quick_backup') else 0
            features['is_long_absence'] = 1 if staleness.get('is_long_absence') else 0
            features['distance_change_staleness'] = staleness.get('distance_change_staleness', 0.0)
            features['class_x_spell'] = staleness.get('class_x_spell', 0.0)
            features['trainer_freshness_profile'] = staleness.get('trainer_freshness_profile', 'standard')
        except Exception as e:
            log_debug(f"Temporal staleness error: {e}")
    
    # Task 03: real movement only under STRIDE_MOVEMENT_FEATURES_LIVE
    # (default OFF — both inference paths serve 0, matching training).
    if MARKET_VELOCITY_AVAILABLE and _movement_features_live():
        try:
            mv_features = compute_market_velocity_features(runner, all_runners)
            features['steam_velocity'] = mv_features.get('steam_velocity', 0.0)
            features['drift_velocity'] = mv_features.get('drift_velocity', 0.0)
            features['price_momentum'] = mv_features.get('price_momentum', 'steady')
            features['late_move_indicator'] = 1 if mv_features.get('late_move_indicator') else 0
            features['market_confidence'] = mv_features.get('market_confidence', 0.5)
            features['relative_move'] = mv_features.get('relative_move', 0.0)
            features['smart_money_score'] = mv_features.get('smart_money_score', 40.0)
            features['market_sigma_modifier'] = mv_features.get('market_sigma_modifier', 1.0)
            features['is_insider_signal'] = 1 if mv_features.get('is_insider_signal') else 0
            features['field_market_agreement'] = mv_features.get('field_market_agreement', 0.5)
        except Exception as e:
            log_debug(f"Market velocity error: {e}")
    
    # Task 03: run the ONE shared feature builder over the shared columns so
    # this path serves exactly what run_tips_pipeline serves. The builder
    # explicitly zeroes the market-movement columns — sourced post-tip-time,
    # see task 14 — unless STRIDE_MOVEMENT_FEATURES_LIVE=1. Extracted values
    # already in `features` win over raw runner keys inside the overlay.
    if SERVE_FEATURES_AVAILABLE:
        try:
            _sf_dist_str = str(race_data.get('distance', '0'))
            try:
                _sf_distance_m = int(_sf_dist_str.replace('m', '').strip())
            except ValueError:
                _sf_distance_m = 1400
            _sf_field_size = len(all_runners) if all_runners else features.get('field_size', 0)
            _log_movement_inert_once(race_data.get('raceDate', race_data.get('date')))
            _overlay_shared_columns(
                features, runner,
                market_odds=features.get('current_odds'),
                distance_m=_sf_distance_m,
                field_size=_sf_field_size,
            )
        except Exception as e:
            log_debug(f"Shared feature builder error: {e}")
    
    return features


def calculate_sophisticated_adjustment(features: dict) -> float:
    """
    Calculate comprehensive probability adjustment using all sophisticated features.
    Uses optimized weights from training data when available, falls back to defaults.
    Returns a multiplier (0.5 - 2.0) for more dramatic adjustments.
    """
    adjustment = 1.0
    
    w = _optimized_weights if WEIGHT_OPTIMIZER_AVAILABLE and _optimized_weights else {}
    
    pace_adv = features.get('pace_advantage', 0)
    pace_w = w.get('pace_advantage_weight', 1.0)
    adjustment += pace_adv * pace_w
    
    if features.get('distance_style_match', 0):
        adjustment += 0.05
    
    class_drop_w = w.get('class_drop_weight', 0.08)
    if features.get('is_class_drop', 0):
        adjustment += class_drop_w
    elif features.get('is_class_rise', 0) and not features.get('is_improving', 0):
        adjustment -= class_drop_w
    
    if features.get('is_first_time_stakes', 0):
        adjustment -= 0.10
    
    adjustment -= features.get('wfa_penalty', 0)
    
    if features.get('is_blinkers_first_time', 0):
        adjustment += 0.12
    
    if features.get('is_jockey_upgrade', 0):
        adjustment += 0.08
    
    elite_w = w.get('elite_connection_weight', 0.10)
    if features.get('is_elite_connection', 0):
        adjustment += elite_w
    elif features.get('is_elite_jockey', 0) or features.get('is_elite_trainer', 0):
        adjustment += elite_w * 0.5
    
    if features.get('jockey_momentum_adjustment', 1.0) != 1.0:
        jm_adj = features['jockey_momentum_adjustment']
        jockey_w = w.get('jockey_streak_weight_single', 0.08)
        adjustment += (jm_adj - 1.0) * (jockey_w / 0.08) * 10
    elif features.get('jockey_is_hot_streak', 0):
        jockey_w = w.get('jockey_streak_weight_single', 0.08)
        adjustment += jockey_w * 1.5
    elif features.get('jockey_is_cold_streak', 0):
        adjustment -= 0.06
    
    if features.get('trainer_momentum_adjustment', 1.0) != 1.0:
        tm_adj = features['trainer_momentum_adjustment']
        adjustment += (tm_adj - 1.0) * 8
    
    barrier_w = w.get('barrier_advantage_weight', 1.0)
    if features.get('data_barrier_adjustment', 1.0) != 1.0:
        data_barrier_adj = features['data_barrier_adjustment']
        adjustment *= (1.0 + (data_barrier_adj - 1.0) * barrier_w)
    else:
        barrier_adv = features.get('barrier_advantage', 0)
        adjustment += barrier_adv * barrier_w
    
    rail_adv = features.get('rail_out_advantage', 0)
    adjustment += rail_adv
    
    form_w_strong = w.get('form_improvement_weight_strong', 0.12)
    form_w_moderate = w.get('form_improvement_weight_moderate', 0.05)
    improvement = features.get('improvement_score', 0)
    if improvement >= 2:
        adjustment += form_w_strong
    elif improvement == 1:
        adjustment += form_w_moderate
    elif improvement <= -2:
        adjustment -= 0.10
    
    if features.get('is_in_form_cycle', 0):
        adjustment += 0.05
    
    if features.get('has_dominant_win', 0):
        adjustment += 0.08
    
    if features.get('is_rating_rise', 0):
        adjustment += 0.05
    
    market_w = w.get('market_steam_weight', 0.10)
    if features.get('is_steam_move', 0):
        adjustment += market_w
    elif features.get('is_drift', 0):
        adjustment -= market_w * 0.8
    
    if features.get('bookmaker_consensus', 0):
        adjustment += 0.03
    
    if features.get('is_short_favorite', 0):
        adjustment -= 0.05
    
    franking_score = features.get('franking_score', 50.0)
    franking_conf = features.get('franking_confidence', 0.1)
    franking_elo = features.get('franking_elo', 50.0)
    field_str = features.get('field_strength_avg', 50.0)
    coll_adv = features.get('collateral_advantage', 0.0)
    fq_trend = features.get('form_quality_trend', 0.0)
    best_margin = features.get('best_adjusted_margin', 0.0)

    if franking_score > 65 and franking_conf > 0.5:
        adjustment += 0.12
    if features.get('anti_franked', 0):
        adjustment -= 0.08
    if franking_elo > 80 and field_str > 80:
        adjustment += 0.06
    if coll_adv > 10:
        adjustment += 0.04
    if fq_trend > 1.0:
        adjustment += 0.03
    if best_margin > 2.0:
        adjustment += 0.03

    sect_frank_val = features.get('sectional_franking_value', 0.0)
    sect_frank_count = features.get('franking_events_count', 0)
    best_sect_frank = features.get('best_sectional_frank', 0.0)

    if sect_frank_val > 10 and sect_frank_count >= 2:
        adjustment += 0.06
    elif sect_frank_val > 5:
        adjustment += 0.03
    elif sect_frank_val < -5:
        adjustment -= 0.03
    if best_sect_frank > 15:
        adjustment += 0.03

    _gfs = features.get('graph_franking_score', 0.0)
    _gfc = features.get('graph_franking_confidence', 0.0)
    _pr = features.get('pagerank_authority', 0.0)
    _cs = features.get('community_strength', 0.5)
    _fs = features.get('form_stability', 0.5)
    _cre = features.get('collapsed_race_exposure', 0.0)

    # mu_shift from graph franking (capped at ±0.15 combined)
    graph_mu_shift = (_gfs * 0.10) + (_pr * 0.05) + ((_cs - 0.5) * 0.06)
    graph_mu_shift = max(-0.15, min(0.15, graph_mu_shift)) * _gfc
    adjustment += graph_mu_shift

    # Form stability tightens distribution (applied later via sigma)
    # Market validated franking gets a confidence bonus
    if features.get('market_validated', 0):
        adjustment += 0.04 * _gfc

    # Collapsed race exposure penalises unreliable form
    if _cre > 0.3:
        adjustment -= 0.05 * _cre

    # Graph anti-franking (excuse-aware) overrides legacy if stronger signal
    if features.get('graph_anti_franked', 0) and features.get('graph_anti_frank_ratio', 0.5) > 0.65:
        adjustment -= 0.06

    # Jockey/trainer hot streaks (legacy - supplemented by momentum above)
    jockey_combo_w = w.get('jockey_streak_weight_combo', 0.12)
    jockey_single_w = w.get('jockey_streak_weight_single', 0.08)
    if not JOCKEY_MOMENTUM_AVAILABLE:
        if features.get('combo_hot_streak', 0):
            adjustment += jockey_combo_w
        elif features.get('jockey_hot_streak', 0):
            adjustment += jockey_single_w
        elif features.get('trainer_hot_streak', 0):
            adjustment += 0.06
    
    if features.get('is_improving_form', 0) and features.get('peak_form', 0):
        adjustment += 0.10
    elif features.get('is_declining_form', 0):
        adjustment -= 0.08
    
    consistency = features.get('form_consistency', 0.5)
    if consistency > 0.8:
        adjustment += 0.05
    
    if features.get('proven_at_distance', 0):
        adjustment += 0.08
    
    if features.get('stepping_up_distance', 0) and not features.get('proven_at_distance', 0):
        adjustment -= 0.05
    
    if features.get('going_suitability_adjustment', 1.0) != 1.0:
        adjustment *= features['going_suitability_adjustment']
    elif features.get('wet_track_specialist', 0) and features.get('is_wet_track', 0):
        adjustment += 0.12
    elif features.get('dry_track_specialist', 0) and not features.get('is_wet_track', 0):
        adjustment += 0.08
    
    going_adj = features.get('going_adjustment', 1.0)
    if going_adj != 1.0:
        adjustment *= going_adj
    
    market_adj = features.get('market_adjustment', 1.0)
    if market_adj != 1.0:
        adjustment *= market_adj
    
    if features.get('is_sharp_money', 0):
        adjustment += 0.08
    
    if features.get('is_longshot_steamer', 0):
        # Scaled boost instead of flat +10% — require meaningful steam
        steam_pct = abs(features.get('steam_drift_pct', 0) or 0)
        adjustment += min(0.04, steam_pct * 0.001)
    
    sectional_w = w.get('sectional_speed_weight', 0.08)
    if features.get('rating_above_class', 0):
        adjustment += sectional_w
    
    if features.get('is_strong_closer', 0) and features.get('suits_pace_scenario', 0):
        adjustment += 0.10
    
    improvement_trend = features.get('improvement_trend', 0)
    if improvement_trend > 0:
        adjustment += min(0.10, improvement_trend * 0.03)
    
    expected_pace_adv = features.get('expected_pace_advantage', 0)
    if expected_pace_adv != 0:
        adjustment += expected_pace_adv
    
    if features.get('is_lone_speed', 0):
        adjustment += 0.12
    
    if features.get('is_pace_disadvantage', 0):
        adjustment -= 0.08
    
    fatigue = features.get('fatigue_factor', 1.0)
    if fatigue < 0.90:
        adjustment -= (1.0 - fatigue) * 0.5
    
    collapse_prob = features.get('collapse_probability', 0)
    if collapse_prob > 0.3:
        adjustment -= collapse_prob * 0.15
    
    fitness = features.get('fitness_factor', 1.0)
    if fitness < 0.93:
        adjustment -= (1.0 - fitness) * 0.3
    
    
    if features.get('has_superior_finishing_speed', 0):
        adjustment += sectional_w
    
    if features.get('finishing_burst_detected', 0):
        adjustment += 0.06
    
    sectional_trend = features.get('sectional_trend', 'stable')
    if sectional_trend == 'improving':
        adjustment += 0.04
    elif sectional_trend == 'declining':
        adjustment -= 0.04
    
    l200_accel = features.get('last_200_acceleration', 0)
    if l200_accel > 2:
        adjustment += min(0.08, l200_accel * 0.02)
    
    rowlands_adj = features.get('rowlands_adjustment', 0)
    if rowlands_adj != 0:
        adjustment += min(0.12, max(-0.12, rowlands_adj * 0.015))
    
    pace_dev_penalty = features.get('pace_deviation_penalty', 0)
    if pace_dev_penalty != 0:
        adjustment += min(0.10, max(-0.15, pace_dev_penalty))

    pace_pos_impact = features.get('pace_position_impact', 0)
    if pace_pos_impact != 0:
        adjustment += min(0.08, max(-0.08, pace_pos_impact))

    pace_consistency = features.get('pace_profile_consistency', 50)
    if pace_consistency > 80:
        adjustment += 0.02
    elif pace_consistency < 30:
        adjustment -= 0.02

    overall_bench = features.get('overall_benchmark', 0)
    if overall_bench != 0:
        adjustment += min(0.10, max(-0.10, overall_bench * 0.08))

    late_bench = features.get('late_benchmark', 0)
    if late_bench > 0.3:
        adjustment += min(0.06, late_bench * 0.03)
    elif late_bench < -0.3:
        adjustment += max(-0.06, late_bench * 0.03)

    bench_trend = features.get('benchmark_trend', 0)
    if bench_trend == 1:
        adjustment += 0.03
    elif bench_trend == -1:
        adjustment -= 0.03

    sm_style_adj = features.get('speed_map_style_adjustment', 0)
    if sm_style_adj != 0:
        adjustment += max(-0.15, min(0.15, sm_style_adj))

    sm_pace_adv = features.get('speed_map_pace_advantage', 0)
    if sm_pace_adv != 0:
        adjustment += max(-0.12, min(0.12, sm_pace_adv))

    sm_on_pace = features.get('speed_map_on_pace_rate', 0)
    if sm_on_pace > 70:
        adjustment += 0.03
    elif sm_on_pace < 20 and features.get('speed_map_backmarker_rate', 0) > 60:
        adjustment -= 0.04

    hidden_form = features.get('hidden_form_score', 0)
    if hidden_form > 60:
        adjustment += min(0.10, hidden_form * 0.0015)
    elif hidden_form > 30:
        adjustment += min(0.05, hidden_form * 0.001)

    peak_trend = features.get('peak_speed_trend', 0)
    if peak_trend == 1:
        adjustment += 0.03
    elif peak_trend == -1:
        adjustment -= 0.02

    compromised = features.get('compromised_runs', 0)
    if compromised >= 3:
        adjustment += 0.06
    elif compromised >= 2:
        adjustment += 0.04

    if features.get('step_up_candidate', 0) == 1.0:
        adjustment += 0.04
    if features.get('distance_limited', 0) == 1.0:
        adjustment -= 0.06
    stamina_sc = features.get('stamina_score', 50.0)
    if stamina_sc > 80:
        adjustment += 0.03
    elif stamina_sc < 30:
        adjustment -= 0.04
    if features.get('decel_rate_current_dist', 0) > 0.02:
        adjustment += 0.02

    jockey_eff = features.get('jockey_efficiency_score', 50.0)
    jockey_match = features.get('jockey_pace_match', 50.0)
    jockey_cook = features.get('jockey_cook_rate', 15.0)
    jockey_finish_eff = features.get('jockey_finishing_efficiency', 0.0)

    if jockey_eff > 70 and jockey_match > 65:
        adjustment += 0.04
    elif jockey_eff < 30:
        adjustment -= 0.03
    if jockey_cook > 40:
        adjustment -= 0.03
    if jockey_finish_eff > 1.5:
        adjustment += 0.02
    elif jockey_finish_eff < -1.5:
        adjustment -= 0.02

    if features.get('is_unlucky_loser', 0):
        adjustment += 0.06
    
    race_shape_adv = features.get('race_shape_advantage', 0)
    if race_shape_adv != 0:
        adjustment += race_shape_adv
    
    breeding_match = features.get('breeding_distance_match', 1.0)
    adjustment *= breeding_match
    
    wet_breeding = features.get('wet_track_breeding_bonus', 1.0)
    if wet_breeding != 1.0:
        adjustment *= wet_breeding
    
    if features.get('is_first_up_specialist', 0) and features.get('is_first_up', 0):
        adjustment += 0.10
    
    if features.get('is_second_up_improver', 0) and features.get('is_second_up', 0):
        adjustment += 0.08
    
    if features.get('in_optimal_freshness_window', 0):
        adjustment += 0.04
    
    if features.get('trainer_targets_track', 0):
        adjustment += 0.05
    
    weight_adv = features.get('weight_advantage', 0)
    if abs(weight_adv) > 1.0:
        adjustment += min(0.08, max(-0.08, weight_adv * 0.02))
    
    if features.get('margin_has_dominant_win', 0):
        adjustment += 0.06
    
    class_transition_adj = features.get('class_transition_adjustment', 1.0)
    if class_transition_adj != 1.0:
        adjustment *= class_transition_adj
    
    if features.get('form_at_higher_class', 0):
        class_margin_score = features.get('class_weighted_margin_score', 0)
        if class_margin_score > 0.5:
            adjustment += 0.10
        elif class_margin_score > 0.3:
            adjustment += 0.06
    
    class_drop_value = features.get('class_drop_with_margin_value', 0)
    if class_drop_value > 0.2:
        adjustment += 0.12
    elif class_drop_value > 0.1:
        adjustment += 0.06
    
    market_movement_adj = features.get('market_movement_adjustment', 1.0)
    if market_movement_adj != 1.0:
        adjustment *= market_movement_adj
    
    field_size = features.get('field_size', 12)
    if field_size <= 6:
        adjustment += 0.08
    elif field_size >= 16:
        adjustment -= 0.04
    
    if features.get('is_debutant', 0):
        adjustment -= 0.06
    career_win_rate = features.get('career_win_rate', 0)
    if career_win_rate > 25:
        adjustment += 0.08
    elif career_win_rate > 15:
        adjustment += 0.04
    
    
    settling_interaction = features.get('settling_pace_interaction', 0.0)
    if settling_interaction != 0:
        adjustment += max(-0.10, min(0.10, settling_interaction))
    
    if features.get('is_congested_speed', 0):
        adjustment -= 0.06
    
    freshness_score = features.get('empirical_freshness_score', 0.7)
    if freshness_score > 0.85:
        adjustment += 0.04
    elif freshness_score < 0.4:
        adjustment -= 0.06
    
    if features.get('is_quick_backup', 0):
        adjustment -= 0.03
    if features.get('is_long_absence', 0):
        adjustment -= 0.05
    
    run_spacing = features.get('run_spacing_quality', 0.5)
    if run_spacing > 0.8:
        adjustment += 0.03
    elif run_spacing < 0.3:
        adjustment -= 0.03
    
    smart_money = features.get('smart_money_score', 40.0)
    if smart_money > 75:
        adjustment += 0.08
    elif smart_money > 60:
        adjustment += 0.04
    elif smart_money < 20:
        adjustment -= 0.04
    
    if features.get('is_insider_signal', 0):
        adjustment += 0.06
    
    if features.get('late_move_indicator', 0):
        adjustment += 0.03
    
    # === ODDS-AWARE DAMPENING: compress adjustments toward 1.0 for longshots ===
    market_odds = features.get('market_odds', 0) or features.get('win_odds', 0) or 0
    if market_odds > 30:
        adjustment = 1.0 + (adjustment - 1.0) * 0.40  # 60% compression for extreme longshots
    elif market_odds > 15:
        adjustment = 1.0 + (adjustment - 1.0) * 0.65  # 35% compression
    elif market_odds > 10:
        adjustment = 1.0 + (adjustment - 1.0) * 0.80  # 20% compression

    # === COMBINE WITH BASE ADJUSTMENTS ===
    base_adjustment = _calculate_rule_based_adjustment(features)

    final_adjustment = (adjustment * 0.6) + (base_adjustment * 0.4)
    
    return max(0.60, min(1.40, final_adjustment))  # Tightened from [0.50, 2.00]


def calculate_ml_probability_adjustment(features: dict, include_stages: bool = False):
    """
    Calculate probability adjustment using trained ML ensemble model.
    Falls back to rule-based adjustments if model is not trained.

    Returns a multiplier (0.7 - 1.5) to adjust base probability.
    """
    if ML_MODEL_AVAILABLE:
        try:
            model = get_model()
            if model.is_trained:
                if include_stages:
                    ml_adjustment, stages = model.predict_adjustment_with_stages(features)
                else:
                    ml_adjustment = model.predict_adjustment(features)
                log_debug(f"ML Model adjustment: {ml_adjustment:.3f}")
                return (ml_adjustment, stages) if include_stages else ml_adjustment
        except Exception as e:
            log_debug(f"ML model error, using rule-based: {e}")
    
    fallback = _calculate_rule_based_adjustment(features)
    return (fallback, {"method": "rule_based"}) if include_stages else fallback


def _calculate_rule_based_adjustment(features: dict) -> float:
    """
    Rule-based probability adjustment (fallback when ML model not trained).
    Returns a multiplier (0.7 - 1.3) to adjust base probability.
    """
    adjustment = 1.0
    
    ground_suit = features.get('ground_suitability', 0)
    if ground_suit > 35:
        adjustment += 0.15
    elif ground_suit > 25:
        adjustment += 0.08
    elif features.get('is_wet_tracker', 0) and ground_suit < 10:
        adjustment -= 0.10
    
    if features.get('distance_strike_rate', 0) > 30:
        adjustment += 0.12
    elif features.get('distance_strike_rate', 0) > 20:
        adjustment += 0.06
    
    if features.get('is_course_distance_winner', 0):
        adjustment += 0.10
    elif features.get('course_strike_rate', 0) > 25:
        adjustment += 0.05
    
    if features.get('weighted_form_score', 0) > 20:
        adjustment += 0.10
    elif features.get('weighted_form_score', 0) > 12:
        adjustment += 0.05
    
    if features.get('recent_winner', 0):
        adjustment += 0.08
    
    if features.get('is_first_up', 0):
        adjustment -= 0.05
    if features.get('is_second_up', 0):
        adjustment += 0.03
    
    if features.get('freshness_score', 0) >= 1.0:
        adjustment += 0.05
    
    if features.get('is_winning_combo', 0):
        adjustment += 0.08
    elif features.get('jockey_trainer_strike_rate', 0) > 30:
        adjustment += 0.05
    
    adjustment -= features.get('barrier_penalty', 0)
    
    if features.get('is_heavy_weight', 0):
        adjustment -= 0.03
    
    return max(0.70, min(1.30, adjustment))


def get_prediction_explanation(features: dict) -> list:
    """
    Get human-readable explanation for why a horse was selected.
    Uses SHAP-like feature importance from the trained model.
    """
    explanations = []
    
    if ML_MODEL_AVAILABLE:
        try:
            model = get_model()
            if model.is_trained:
                contributions = model.explain_prediction(features)
                for feature, contrib in contributions.items():
                    readable = _feature_to_explanation(feature, features.get(feature, 0), contrib)
                    if readable:
                        explanations.append(readable)
        except Exception:
            pass
    
    if not explanations:
        explanations = _get_rule_based_explanations(features)
    
    return explanations[:5]


def _feature_to_explanation(feature: str, value: float, contribution: float) -> str:
    """Convert feature contribution to human-readable text."""
    feature_names = {
        'distance_strike_rate': f'Strong distance record ({value:.0f}% wins)',
        'course_strike_rate': f'Good course form ({value:.0f}% strike rate)',
        'weighted_form_score': f'Recent good form (score: {value:.1f})',
        'is_winning_combo': 'Winning jockey-trainer combination',
        'jockey_trainer_strike_rate': f'Good jockey-trainer combo ({value:.0f}%)',
        'freshness_score': 'Well-rested and fresh',
        'is_second_up': 'Second-up after break (historically improves)',
        'ground_suitability': f'Suits track conditions ({value:.0f}% on similar going)',
        'course_distance_wins': f'Course-distance winner ({int(value)} wins)',
        'recent_winner': 'Won last start',
        'is_good_barrier': 'Favourable barrier draw',
        'pace_advantage': 'Pace scenario favours this runner',
        'is_class_drop': 'Dropping in class (easier competition)',
        'is_improving': 'Form on the improve (trending up)',
        'is_blinkers_first_time': 'Blinkers applied first time (positive signal)',
        'is_elite_jockey': 'Top-tier jockey booking',
        'is_elite_trainer': 'Elite trainer connection',
        'is_elite_connection': 'Elite jockey-trainer combination',
        'is_steam_move': 'Strong market support (odds shortening)',
        'has_dominant_win': 'Has won by large margin recently',
        'is_in_form_cycle': 'Currently in winning form cycle',
        'barrier_advantage': 'Track bias favours this barrier',
        'is_jockey_upgrade': 'Upgraded to better jockey',
    }
    return feature_names.get(feature, '')


def _get_rule_based_explanations(features: dict) -> list:
    """Generate explanations from rule-based feature analysis."""
    explanations = []
    
    if features.get('distance_strike_rate', 0) > 25:
        explanations.append(f"Distance specialist ({features['distance_strike_rate']:.0f}% strike rate)")
    
    if features.get('course_strike_rate', 0) > 20:
        explanations.append(f"Good at this track ({features['course_strike_rate']:.0f}% wins)")
    
    if features.get('weighted_form_score', 0) > 15:
        explanations.append(f"Strong recent form (score: {features['weighted_form_score']:.1f})")
    
    if features.get('is_winning_combo', 0):
        sr = features.get('jockey_trainer_strike_rate', 0)
        explanations.append(f"Winning jockey-trainer combo ({sr:.0f}%)")
    
    if features.get('recent_winner', 0):
        explanations.append("Won last start")
    
    if features.get('is_second_up', 0):
        explanations.append("Second-up after break")
    
    if features.get('is_good_barrier', 0):
        explanations.append(f"Good barrier ({features.get('barrier_draw', 0)})")
    
    if features.get('ground_suitability', 0) > 30:
        explanations.append(f"Suits going ({features['ground_suitability']:.0f}% on similar)")
    
    if features.get('freshness_score', 0) >= 0.8:
        explanations.append("Well-rested and fresh")
    
    if features.get('is_blinkers_first_time', 0):
        explanations.append("Blinkers first time (trainer intent)")
    
    if features.get('is_improving', 0):
        explanations.append("Form on the improve")
    
    if features.get('improvement_score', 0) >= 2:
        explanations.append("Strong sequential improvement pattern")
    
    if features.get('is_class_drop', 0):
        explanations.append("Dropping in class")
    
    if features.get('is_elite_connection', 0):
        explanations.append("Elite jockey-trainer combination")
    elif features.get('is_elite_jockey', 0):
        explanations.append("Top-tier jockey engaged")
    elif features.get('is_elite_trainer', 0):
        explanations.append("Elite trainer stable")
    
    if features.get('is_jockey_upgrade', 0):
        explanations.append("Jockey upgrade (positive signal)")
    
    if features.get('is_steam_move', 0):
        explanations.append(f"Market support (odds shortened {features.get('odds_movement_pct', 0):.0f}%)")
    
    if features.get('pace_advantage', 0) > 0.1:
        explanations.append("Pace scenario suits running style")
    
    if features.get('has_dominant_win', 0):
        explanations.append("Has dominant win in form")
    
    if features.get('barrier_advantage', 0) > 0.05:
        explanations.append("Track bias favours barrier")
    
    if features.get('is_in_form_cycle', 0):
        wins = features.get('wins_in_form', 0)
        if wins > 1:
            explanations.append(f"Hot form cycle ({wins} wins in form)")
    
    return explanations


def parse_weight(weight_str):
    """Parse weight from string like '59kg' to float 59.0."""
    if not weight_str:
        return 0.0
    if isinstance(weight_str, (int, float)):
        return float(weight_str)
    weight_str = str(weight_str)
    weight_str = weight_str.lower().replace('kg', '').strip()
    try:
        return float(weight_str)
    except (ValueError, TypeError):
        return 0.0


def parse_int(value, default=0):
    """Parse integer from string or number."""
    if not value:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (ValueError, TypeError):
        return default


def create_balanced_shortlist(results: list, top_n: int = 6) -> list:
    """
    Create a diversified shortlist avoiding the extremes-only problem.

    Strategy: top 2 by EV, top 2 by win probability, top 2 from mid-market band with best EV, remaining slots filled by overall selection score.
    """
    if len(results) <= top_n:
        return results
    
    for r in results:
        odds = r.get('marketOdds', 5.0)
        win_prob = r.get('winPercentage', 0) / 100
        r['_ev'] = (win_prob * odds) - 1 if odds > 0 else -1
        r['_in_mid_market'] = CONFIG['mid_market_low'] <= odds <= CONFIG['mid_market_high']
    
    by_ev = sorted([r for r in results if r['_ev'] > CONFIG['ev_min_threshold']], 
                   key=lambda x: -x['_ev'])
    by_prob = sorted(results, key=lambda x: -x.get('winPercentage', 0))
    by_mid_market_ev = sorted([r for r in results if r['_in_mid_market'] and r['_ev'] > 0],
                              key=lambda x: -x['_ev'])
    
    shortlist = []
    seen_horses = set()
    
    def add_if_new(runner, source):
        horse = runner.get('horse')
        if horse not in seen_horses:
            seen_horses.add(horse)
            runner['_source'] = source
            shortlist.append(runner)
            return True
        return False
    
    for r in by_ev[:2]:
        add_if_new(r, 'top_ev')
    
    for r in by_prob[:2]:
        add_if_new(r, 'top_prob')
    
    for r in by_mid_market_ev[:2]:
        add_if_new(r, 'mid_market_ev')
    
    remaining = top_n - len(shortlist)
    if remaining > 0:
        by_score = sorted(results, key=lambda x: -x.get('selectionScore', 0))
        for r in by_score:
            if add_if_new(r, 'selection_score'):
                remaining -= 1
                if remaining <= 0:
                    break
    
    for r in shortlist:
        r.pop('_ev', None)
        r.pop('_in_mid_market', None)
        r.pop('_source', None)
    
    for r in results:
        r.pop('_ev', None)
        r.pop('_in_mid_market', None)
        r.pop('_source', None)
    
    return shortlist[:top_n]


def validate_final_tips(results: list, original_runners: list) -> list:
    """
    Final validation to ensure no scratched horses appear in tips.
    Re-ranks if any invalid tips are found.
    """
    active_names = set()
    for r in original_runners:
        if is_active_runner(r):
            active_names.add(r.get('horse', r.get('name', '')).lower())
    
    valid_tips = []
    removed = []
    for tip in results:
        horse_name = tip.get('horse', '').lower()
        if horse_name in active_names:
            valid_tips.append(tip)
        else:
            removed.append(tip.get('horse', 'Unknown'))
    
    if removed:
        print(f"[VALIDATION] Removed {len(removed)} invalid tips: {', '.join(removed)}", file=sys.stderr)
    
    return valid_tips


def create_race_from_api_data(race_data, runners_data):
    """Create a Race object from API data format with scratching filter."""
    race_info = f"{race_data.get('track', race_data.get('course', ''))} R{race_data.get('raceNumber', race_data.get('race_number', ''))}"
    
    # CRITICAL: Filter out scratched runners FIRST
    active_runners, scratched_list = filter_active_runners(runners_data, race_info)
    
    if scratched_list:
        print(f"[SCRATCH FILTER] {race_info}: Removed {len(scratched_list)} scratched: {', '.join(scratched_list)}", file=sys.stderr)
    
    runners = []
    for r in active_runners:
        # Preserve genuine market prices only; do not fabricate SP in the race object.
        odds = parse_real_odds(r) or 0.0
        horse_name = r.get('horse', r.get('name', 'Unknown'))
        
        log_debug(f"{horse_name}: parsed odds = {odds}")
            
        runner_dict = {
            'horse': horse_name,
            'horse_id': r.get('horse_id', ''),
            'tab_no': parse_int(r.get('number', r.get('tab_no', 0))),
            'number': parse_int(r.get('number', r.get('tab_no', 0))),
            'draw': parse_int(r.get('barrier', r.get('draw', 0))),
            'barrier': parse_int(r.get('barrier', r.get('draw', 0))),
            'weight': parse_weight(r.get('weight', 0)),
            'jockey': r.get('jockey', ''),
            'jockey_id': r.get('jockey_id', ''),
            'trainer': r.get('trainer', ''),
            'trainer_id': r.get('trainer_id', ''),
            'form': r.get('form', ''),
            'age': r.get('age', 0),
            'sex': r.get('sex', ''),
            'sp': odds,
            'sire': r.get('sire', ''),
            'dam_sire': r.get('dam_sire', r.get('dam', '')),
            'last_raced': r.get('last_raced', ''),
            # LLM/luckless pre-MC adjustment (4C)
            'llm_mu_adjustment': r.get('llm_mu_adjustment', 0),
            # CRITICAL: Include stats for ML feature extraction
            'stats': {
                'ground_heavy_stats': r.get('ground_heavy_stats') or r.get('stats', {}).get('ground_heavy_stats'),
                'ground_soft_stats': r.get('ground_soft_stats') or r.get('stats', {}).get('ground_soft_stats'),
                'ground_firm_stats': r.get('ground_firm_stats') or r.get('stats', {}).get('ground_firm_stats'),
                'ground_good_stats': r.get('ground_good_stats') or r.get('stats', {}).get('ground_good_stats'),
                'distance_stats': r.get('distance_stats') or r.get('stats', {}).get('distance_stats'),
                'course_stats': r.get('course_stats') or r.get('stats', {}).get('course_stats'),
                'course_distance_stats': r.get('course_distance_stats') or r.get('stats', {}).get('course_distance_stats'),
                'jockey_stats': r.get('jockey_stats') or r.get('stats', {}).get('jockey_stats'),
            },
        }
        runner = Runner.from_dict(runner_dict)
        runners.append(runner)
    
    race_dict = {
        'race_id': race_data.get('race_id', ''),
        'course': race_data.get('track', race_data.get('course', '')),
        'track': race_data.get('track', race_data.get('course', '')),
        'date': race_data.get('date', ''),
        'race_number': race_data.get('raceNumber', race_data.get('race_number', 0)),
        'number': race_data.get('raceNumber', race_data.get('race_number', 0)),
        'race_name': race_data.get('race_name', race_data.get('raceName', '')),
        'distance': race_data.get('distance', 0),
        'going': race_data.get('going', ''),
        'class': race_data.get('class', race_data.get('class_level', '')),
        'runners': [],  # Don't pass Runner objects - from_dict expects dicts
    }
    
    race = Race.from_dict(race_dict)
    race.runners = runners
    return race


def generate_form_analyst_insights(runner_data: dict, race_data: dict, enhanced_features: dict, mc_result: dict) -> list:
    """
    Generate plain English form analyst insights for a selection.
    Returns a list of 4-6 insight strings that everyday punters can understand.
    """
    insights = []
    
    horse_name = runner_data.get('horse', runner_data.get('name', 'This horse'))
    form = str(runner_data.get('form', '') or '')
    barrier = runner_data.get('barrier', 0)
    jockey = runner_data.get('jockey', '')
    trainer = runner_data.get('trainer', '')
    track = race_data.get('track', race_data.get('course', ''))
    distance = str(race_data.get('distance', ''))
    
    if form:
        form_chars = [c for c in form if c.isdigit()]
        if form_chars:
            recent_positions = [int(c) for c in form_chars[:5]]
            
            wins = sum(1 for p in recent_positions if p == 1)
            if wins >= 2:
                insights.append(f"Strong recent form with {wins} wins from last {len(recent_positions)} starts - in peak form")
            elif wins == 1:
                last_win_pos = next((i for i, p in enumerate(recent_positions) if p == 1), -1)
                if last_win_pos == 0:
                    insights.append("Coming off a win last start - confidence should be high")
                elif last_win_pos < 3:
                    insights.append("Won recently and appears to be racing in good form")
            
            placings = sum(1 for p in recent_positions if p <= 3)
            if placings >= 3 and wins == 0:
                insights.append(f"Consistent performer with {placings} placings from {len(recent_positions)} runs - keeps hitting the frame")
            
            if len(recent_positions) >= 3:
                if recent_positions[0] < recent_positions[1] < recent_positions[2]:
                    insights.append("Form trending upward with each run better than the last")
                elif recent_positions[0] == 2 and recent_positions[1] > 3:
                    insights.append("Showed sharp improvement last start with a close second")
            
            if recent_positions[0] > 6 and any(p <= 3 for p in recent_positions[1:3]):
                insights.append("Disappointed last start after solid previous form - worth monitoring the market")
    
    distance_sr = enhanced_features.get('distance_strike_rate', 0)
    course_sr = enhanced_features.get('course_strike_rate', 0)
    is_cd_winner = enhanced_features.get('is_course_distance_winner', 0)
    
    if is_cd_winner:
        insights.append(f"Proven winner at {track} over this distance - course and distance form is gold")
    elif course_sr > 20:
        insights.append(f"Strong record at {track} with {int(course_sr)}% strike rate - knows the track well")
    elif distance_sr > 20:
        insights.append(f"Likes this distance with a {int(distance_sr)}% strike rate - suited by the trip")
    elif distance_sr > 0 or course_sr > 0:
        if distance_sr > course_sr:
            insights.append("Distance specialist who should be suited by this trip")
        elif course_sr > 0:
            insights.append("Has shown ability at this track previously")
    
    is_elite_jockey = enhanced_features.get('is_elite_jockey', 0)
    is_elite_trainer = enhanced_features.get('is_elite_trainer', 0)
    is_winning_combo = enhanced_features.get('is_winning_combo', 0)
    jt_strike_rate = enhanced_features.get('jockey_trainer_strike_rate', 0)
    
    if is_winning_combo:
        insights.append(f"{jockey} and {trainer} have won together before - a proven partnership")
    elif is_elite_jockey and is_elite_trainer:
        insights.append(f"Elite jockey {jockey} paired with top trainer {trainer} - power combination")
    elif is_elite_jockey:
        insights.append(f"Top jockey {jockey} in the saddle - adds tactical nous to the ride")
    elif is_elite_trainer:
        insights.append(f"Trained by {trainer} who has a strong record in these types of races")
    elif jt_strike_rate > 15:
        insights.append(f"Jockey-trainer combo clicking at {int(jt_strike_rate)}% - working well together")
    
    barrier_field_ratio = enhanced_features.get('barrier_field_ratio', 0.5)
    barrier_advantage = enhanced_features.get('barrier_advantage', 0)
    
    if barrier and int(barrier) <= 4:
        insights.append(f"Drawn barrier {barrier} - ideally placed to secure a good position early")
    elif barrier and int(barrier) <= 8:
        if barrier_advantage > 0:
            insights.append(f"Barrier {barrier} is a neutral draw - can work into a good spot")
    elif barrier and int(barrier) > 10:
        insights.append(f"Wide barrier {barrier} means extra work early - needs luck or a fast start")
    elif barrier_field_ratio > 0.8:
        insights.append("Drawn wide in this field - will need a positive ride to overcome the draw")
    elif barrier_field_ratio < 0.3:
        insights.append("Advantageous inside barrier draw - should save ground throughout")
    
    is_first_up = enhanced_features.get('is_first_up', 0)
    is_second_up = enhanced_features.get('is_second_up', 0)
    days_since_run = enhanced_features.get('days_since_run', 999)
    is_class_drop = enhanced_features.get('is_class_drop', 0)
    is_class_rise = enhanced_features.get('is_class_rise', 0)
    is_improving = enhanced_features.get('is_improving', 0)
    
    if is_first_up:
        insights.append("First-up after a spell - watch for market support and stable confidence")
    elif is_second_up:
        if is_improving:
            insights.append("Second-up specialist pattern - often peak when second-up after a break")
        else:
            insights.append("Second-up today - the run under the belt should have this one fitter")
    
    if is_class_drop:
        insights.append("Dropping in grade from tougher company - should find this easier")
    elif is_class_rise:
        insights.append("Taking on stronger opposition today - needs to lift to be competitive")
    
    if days_since_run and days_since_run < 14 and days_since_run > 0:
        insights.append(f"Quick back-up in {days_since_run} days suggests stable confidence is high")
    
    class_margin_insight = enhanced_features.get('class_margin_insight', '')
    class_drop_value = enhanced_features.get('class_drop_with_margin_value', 0)
    form_at_higher_class = enhanced_features.get('form_at_higher_class', 0)
    
    if class_margin_insight and class_drop_value > 0.1:
        insights.append(f"Key class angle: {class_margin_insight}")
    elif form_at_higher_class:
        best_form_class = enhanced_features.get('best_form_class', 0)
        if best_form_class > 70:
            insights.append("Has tested at stakes level - drops back to easier grade today")
        elif best_form_class > 60:
            insights.append("Ran well at stronger benchmark level - should appreciate this drop in class")
    
    market_insight = enhanced_features.get('market_insight', '')
    market_category = enhanced_features.get('market_movement_category', 'steady')
    is_sharp_money = enhanced_features.get('is_sharp_money', 0)
    is_longshot_steamer = enhanced_features.get('is_longshot_steamer', 0)
    
    if market_insight:
        if is_longshot_steamer:
            insights.append(f"Money angle: {market_insight}")
        elif is_sharp_money and market_category in ['steam', 'strong_steam']:
            insights.append(f"Betting watch: {market_insight}")
        elif market_category == 'strong_steam':
            insights.append(market_insight)
        elif market_category == 'strong_drift':
            insights.append(market_insight)
    
    ground_suitability = enhanced_features.get('ground_suitability', 0)
    if ground_suitability > 30:
        insights.append("Track conditions suit based on previous form on similar ground")
    elif ground_suitability < -10:
        insights.append("Track conditions may not suit - has struggled on similar ground before")
    
    pace_advantage = enhanced_features.get('pace_advantage', 0)
    if _flag_enabled(MC_FIX_DEAD_FIELDS):
        # 'pace_regime' never exists on MC results (#124 defect 5); the
        # engine's per-runner style key is 'style'.
        running_style = mc_result.get('style') or enhanced_features.get('running_style', '')
    else:
        running_style = mc_result.get('pace_regime', enhanced_features.get('running_style', ''))
    pace_flags = enhanced_features.get('pace_flags', [])
    pace_scenario = enhanced_features.get('pace_scenario', '')
    is_lone_speed = enhanced_features.get('is_lone_speed', False)
    has_covered_run = enhanced_features.get('has_covered_run', False)
    is_wide_no_cover = enhanced_features.get('is_wide_no_cover', False)
    settling_position = enhanced_features.get('settling_position', 0)
    expected_section = enhanced_features.get('expected_section', '')
    
    if is_lone_speed or 'LONE LEADER' in pace_flags:
        insights.append("Speed map: Only confirmed leader - should control tempo and be hard to catch")
    
    if 'BOX SEAT' in pace_flags:
        insights.append("Maps to sit in the box seat with cover - ideal position to pounce")
    
    if has_covered_run and 'BOX SEAT' not in pace_flags and 'COVERED RUN' in pace_flags:
        insights.append(f"Should get a covered run settling {expected_section} on the fence")
    
    if is_wide_no_cover or 'WIDE NO COVER' in pace_flags:
        insights.append("Likely to race wide without cover - will need to do extra work")
    
    if 'TIGHT TRACK LEADER' in pace_flags:
        insights.append("Front-runner on tight track - significant tactical advantage")
    
    if 'INSIDE SPEED' in pace_flags and 'LONE LEADER' not in pace_flags:
        insights.append("Drawn inside with natural speed - should cross and find the rail")
    
    if 'WIDE SPEED' in pace_flags:
        insights.append("Speed horse drawn wide - must use petrol early to find position")
    
    if pace_advantage >= 0.12:
        scenario_label = pace_scenario.replace('_', ' ') if pace_scenario else 'predicted'
        insights.append(f"Strong pace advantage: {scenario_label} tempo suits running style perfectly")
    elif pace_advantage >= 0.08:
        insights.append("Pace scenario favours this runner's pattern")
    elif pace_advantage <= -0.10:
        scenario_label = pace_scenario.replace('_', ' ') if pace_scenario else 'expected'
        insights.append(f"Pace concern: {scenario_label} tempo against this runner's style")
    
    if not pace_flags and pace_advantage > 0.08:
        if running_style == 'leader':
            insights.append("Should be in the firing line from the jump - likely to press forward")
        elif running_style == 'stalker' or running_style == 'on_pace':
            insights.append("Pace looks to suit sitting just off the speed and pouncing late")
        elif running_style == 'closer' or running_style == 'backmarker':
            insights.append("Should get a nice tempo to run at from back in the field")
    
    _raw_wp = mc_result.get('win_prob_sim_raw')
    _cal_wp = mc_result.get('win_prob_sim', 0)
    if _raw_wp is not None:
        _recal_shift = (_cal_wp - _raw_wp) * 100
        if _recal_shift > 2:
            insights.append("Probability recalibration lifted this runner's chances - model sees hidden value the raw simulation missed")
        elif _recal_shift < -5:
            insights.append("Recalibration engine reduced probability estimate - raw simulation may overstate this runner's chances")

    if mc_result.get('sectional_enhanced'):
        insights.append("Sectional pace profiling active - using real speed data across early/mid/late race phases")

    _pace_dist = mc_result.get('pace_scenario_distribution', {})
    if _pace_dist:
        _dominant = max(_pace_dist, key=_pace_dist.get, default=None)
        if _dominant and _pace_dist.get(_dominant, 0) > 0.4:
            _tempo_label = _dominant.replace('_', ' ')
            insights.append(f"Race pace scenario projects as {_tempo_label} tempo")

    if len(insights) > 6:
        insights = insights[:6]
    
    if len(insights) < 2:
        if mc_result.get('win_percentage', 0) > 15:
            insights.append("Model rates this horse as a genuine winning chance today")
        else:
            insights.append("Will need things to fall right but can't be dismissed entirely")
    
    return insights


def _attach_prediction_stages(
    result, ml_stage_values, mc_result, base_win_prob,
    ml_adjustment, combined_adjustment, win_prob,
):
    """Attach non-fatal audit stages to one otherwise-complete race result."""
    from prediction_stages import record_stage

    stages = {}
    for stage_name, component_name in (
        ('base_xgb', 'xgb'),
        ('base_lightgbm', 'lightgbm'),
        ('base_catboost', 'catboost'),
        ('ensemble', 'ensemble'),
    ):
        record_stage(stages, stage_name, ml_stage_values.get(component_name))
    record_stage(stages, 'mc_raw', mc_result.get(
        'win_prob_sim_raw', mc_result.get('win_prob_sim', 0)))
    record_stage(stages, 'mc_recalibrated', mc_result.get('win_prob_sim', 0))
    record_stage(stages, 'sectional_blend', base_win_prob / 100.0)
    record_stage(stages, 'ml_adjustment', ml_adjustment)
    record_stage(stages, 'combined_adjustment', combined_adjustment)
    record_stage(stages, 'wrapper_pre_calibration', win_prob / 100.0)
    record_stage(stages, 'selection_score', result['selectionScore'])
    result['predictionStages'] = stages
    return result


def run_simulation(race, runners, mc_sims=10000, seed=42):
    """Run Monte Carlo simulation on a single race."""
    try:
        model = get_or_build_base_model()
        
        race_obj = create_race_from_api_data(race, runners)
        if not race_obj.is_valid:
            return {'error': 'Invalid race data', 'results': []}
        
        analysis = model.analyze(race_obj)
        if not analysis:
            return {'error': 'Analysis failed', 'results': []}
        
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
            race_obj, analysis, model,
            mc_sims=mc_sims,
            seed=seed,
            mc_model='plackett_luce',
            pace_mode='basic',
            uncertainty='on'
        )
        
        if not mc_results:
            return {'error': 'Monte Carlo simulation failed', 'results': []}

        _llm_adj_count = 0
        for mc_r in mc_results:
            horse_name = mc_r.get('horse', '')
            for r in runners:
                r_name = r.get('horse', r.get('name', ''))
                if r_name and r_name.lower().strip() == horse_name.lower().strip():
                    llm_adj = r.get('llm_mu_adjustment', 0)
                    if llm_adj and abs(llm_adj) > 0.001:
                        old_prob = mc_r.get('win_prob_sim', 0)
                        mc_r['win_prob_sim'] = min(0.60, old_prob + llm_adj)
                        mc_r['_llm_mu_applied'] = round(llm_adj, 4)
                        _llm_adj_count += 1
                    break
        if _llm_adj_count > 0:
            total_wp = sum(mc_r.get('win_prob_sim', 0) for mc_r in mc_results)
            if total_wp > 0:
                for mc_r in mc_results:
                    mc_r['win_prob_sim'] = mc_r['win_prob_sim'] / total_wp
            print(f"[MC] LLM/luckless adjustments applied to {_llm_adj_count} runners", file=sys.stderr)

        if MC_RECALIBRATION_AVAILABLE and _mc_recalibrator:
            raw_win_probs = np.array([mc_r.get('win_prob_sim', 0) for mc_r in mc_results])
            cal_win_probs = _mc_recalibrator.transform_race(raw_win_probs)
            raw_place_probs = np.array([mc_r.get('top3_prob_sim', 0) for mc_r in mc_results])
            cal_place_probs = _mc_recalibrator.transform(raw_place_probs)
            cal_place_probs = np.clip(cal_place_probs, 0.01, 0.99)
            for idx_r, mc_r in enumerate(mc_results):
                mc_r['win_prob_sim_raw'] = mc_r['win_prob_sim']
                mc_r['win_prob_sim'] = float(cal_win_probs[idx_r])
                mc_r['top3_prob_sim_raw'] = mc_r['top3_prob_sim']
                mc_r['top3_prob_sim'] = float(cal_place_probs[idx_r])
            print(f"[MC] Recalibration applied to {len(mc_results)} runners", file=sys.stderr)

        runner_lookup = {r.get('horse', r.get('name', '')): r for r in runners}

        _sectional_mc_results = None
        _pace_scenario_dist = None
        if SECTIONAL_MC_AVAILABLE:
            try:
                _race_distance_str = str(race.get('distance', '1400'))
                try:
                    _race_dist_m = int(_race_distance_str.replace('m', '').strip())
                except (ValueError, TypeError):
                    _race_dist_m = 1400

                _pace_profiles = []
                _running_profiles = []
                _mc_runners = []
                for idx, (a, mc_r) in enumerate(zip(analysis, mc_results)):
                    h_name = a.get('horse', 'Unknown')
                    pp = build_horse_pace_profile(h_name)
                    rp = build_horse_running_profile(h_name)
                    _pace_profiles.append(pp)
                    _running_profiles.append(rp)
                    _runner_src = runner_lookup.get(h_name, {})
                    _real_market_odds = parse_real_odds(_runner_src)
                    _fallback_market_odds = a.get('model_odds')
                    if (not _fallback_market_odds or _fallback_market_odds <= 1) and a.get('model_prob_dec'):
                        try:
                            _fair = 1 / a.get('model_prob_dec') if a.get('model_prob_dec') > 0 else None
                            _fallback_market_odds = _fair if _fair and _fair > 1 else None
                        except Exception:
                            _fallback_market_odds = None
                    _fatigue, _collapse, _fitness = 1.0, 0, 1.0
                    if (_flag_enabled(MC_FIX_DEAD_FIELDS)
                            and TRACK_CONDITION_AVAILABLE and _pace_energy_model):
                        # mc_r never carries these (#124 defect 5), so the
                        # energy layer inside simulate_race_realistic ran on
                        # its defaults for every runner. Computed here from
                        # the same model with the same fallbacks the
                        # enhanced-features path uses when the DB features
                        # are absent (pressure 5/10, 30 days since run).
                        try:
                            _energy = _pace_energy_model.calculate_energy_curve(
                                rp.get('running_style_class', 'midfield'),
                                _race_dist_m, 0.5, 30)
                            _fatigue = _energy.get('fatigue_factor', 1.0)
                            _collapse = _energy.get('collapse_probability', 0)
                            _fitness = _pace_energy_model.get_fitness_factor(30)
                        except Exception:
                            pass
                    _mc_runners.append({
                        'horse': h_name,
                        'form': a.get('form', ''),
                        'market_odds': _real_market_odds or _fallback_market_odds or 0.0,
                        'jockey': a.get('jockey', ''),
                        'fatigue_factor': mc_r.get('fatigue_factor', _fatigue),
                        'collapse_probability': mc_r.get('collapse_probability', _collapse),
                        'fitness_factor': mc_r.get('fitness_factor', _fitness),
                    })

                _base_probs = np.array([mc_r.get('win_prob_sim', 0.05) for mc_r in mc_results])
                _base_probs = np.maximum(_base_probs, 1e-6)

                _sim_race_data = {
                    'distance_m': _race_dist_m,
                    'going': race.get('going', race.get('track_condition', '')),
                    'race_class': race.get('class', race.get('raceClass', race.get('race_class', ''))),
                    'field_size': len(_mc_runners),
                }

                _quant_features = None
                if SECTIONAL_QUANT_AVAILABLE:
                    try:
                        _race_track = race.get('track', race.get('course', ''))
                        _predicted_pace = 'genuine'
                        _pace_dist_check = {}
                        _quant_runners = []
                        for idx_q, (a_q, rp_q, pp_q) in enumerate(zip(analysis, _running_profiles, _pace_profiles)):
                            _quant_runners.append({
                                'horse_name': a_q.get('horse', 'Unknown'),
                                'running_style': rp_q.get('running_style_class', 'midfield'),
                                'barrier': int(a_q.get('barrier', 0) or 0),
                                'early_speed_pct': pp_q.get('early_median', 100.0),
                                'jockey': a_q.get('jockey', ''),
                                'form': a_q.get('form', ''),
                            })
                        _quant_race_data = {
                            'track': _race_track,
                            'distance_m': _race_dist_m,
                            'track_config': race.get('going', race.get('track_condition', '')),
                            'predicted_pace_scenario': _predicted_pace,
                            'race_date': race.get('raceDate', race.get('date', '')),
                            'race_number': int(race.get('raceNumber', race.get('race_number', 0)) or 0),
                        }
                        _quant_features = compute_all_quant_features(_quant_runners, _quant_race_data)
                        _qph = _quant_features.get('per_horse', {})
                        _qfl = _quant_features.get('field_level', {})
                        _collapse_p = _qfl.get('pace_collapse', {}).get('collapse_probability', 0)
                        _n_sasr = sum(1 for h in _qph.values() if abs(h.get('sasr', {}).get('sasr_mu_shift', 0)) > 0.001)
                        _n_shape = sum(1 for h in _qph.values() if abs(h.get('shape_fit', {}).get('shape_mu_shift', 0)) > 0.001)
                        _n_trip = sum(1 for h in _qph.values() if h.get('trip_efficiency', {}).get('unlucky_mu_boost', 0) > 0.001)
                        _n_close = sum(1 for h in _qph.values() if h.get('closing_rank', {}).get('runs_ranked', 0) > 0)
                        print(f"[QuantEngines] SASR:{_n_sasr} Shape:{_n_shape} Trip:{_n_trip} Close:{_n_close} Collapse:{_collapse_p:.2f}", file=sys.stderr)
                    except Exception as qe:
                        print(f"[QuantEngines] Error: {qe}", file=sys.stderr)
                        _quant_features = None

                _sectional_mc_results = simulate_race_with_sectional_profiles(
                    _mc_runners, _base_probs, _pace_profiles, _running_profiles,
                    race_distance_m=_race_dist_m, n_sims=mc_sims, seed=seed,
                    use_strict_speed_class=True,
                    race_data=_sim_race_data,
                    quant_features=_quant_features,
                )

                if _sectional_mc_results and _sectional_mc_results.get('sim_win_probs') is not None:
                    _pace_scenario_dist = _sectional_mc_results.get('pace_scenario_distribution', {})
                    _quant_applied = _sectional_mc_results.get('quant_applied', False)
                    _enhanced = bool(_sectional_mc_results.get('sectional_enhanced'))
                    _quant_log = f"[SectionalMC] Pace: {_pace_scenario_dist}"
                    if _quant_applied:
                        _quant_log += f" | Quant: {len(_sectional_mc_results.get('quant_summary', {}))} horses adjusted"
                    if not _enhanced:
                        _quant_log += " | fallback mode (no sectional profiles)"
                    print(_quant_log, file=sys.stderr)
                else:
                    _sectional_mc_results = None
            except Exception as smc_err:
                print(f"[SectionalMC] Error: {smc_err}", file=sys.stderr)
                _sectional_mc_results = None

        _sectional_exactas = _sectional_mc_results.get('sim_exactas', []) if _sectional_mc_results else []
        _sectional_trifectas = _sectional_mc_results.get('sim_trifectas', []) if _sectional_mc_results else []
        _sectional_going_category = _sectional_mc_results.get('going_category') if _sectional_mc_results else None

        _franking_data = {}
        if FRANKING_AVAILABLE:
            try:
                _horse_ids = []
                for r in runners:
                    hid = r.get('horse_id') or r.get('horseid') or r.get('horseId')
                    if hid:
                        _horse_ids.append(hid)
                if _horse_ids:
                    _franking_data = get_franking_for_runners(_horse_ids)
            except Exception as fk_err:
                log_debug(f"Franking batch lookup error: {fk_err}")
                _franking_data = {}
        
        results = []
        _snapshot_rows = []
        _audit_rows = []
        _race_track = race.get('track', race.get('course', ''))
        _race_number = int(race.get('raceNumber', race.get('race_number', 0)) or 0)
        _race_date = race.get('raceDate', race.get('date', ''))
        _off_time_str = race.get('offTime', race.get('off_time', ''))
        _ml_model_active = False
        if ML_MODEL_AVAILABLE:
            try:
                _m = get_model()
                _ml_model_active = _m.is_trained if _m else False
            except:
                pass

        for i, (base, mc) in enumerate(zip(analysis, mc_results)):
            horse_name = base.get('horse', 'Unknown')
            original_runner = runner_lookup.get(horse_name, {})
            real_market_odds = parse_real_odds(original_runner)
            market_odds = infer_market_odds(base, field_odds)
            if not market_odds:
                market_odds = real_market_odds or base.get('market_odds') or base.get('sp')
            if not market_odds and base.get('model_odds') and base.get('model_odds') > 1:
                market_odds = base.get('model_odds')
            if not market_odds and base.get('model_prob_dec'):
                try:
                    _fair = 1 / base.get('model_prob_dec') if base.get('model_prob_dec') > 0 else None
                    market_odds = _fair if _fair and _fair > 1 else None
                except Exception:
                    market_odds = None
            
            enhanced_features = extract_all_sophisticated_features(original_runner, runners, race, franking_data=_franking_data)
            
            enhanced_factors = {'combined_factor': 1.0}
            if ENHANCED_FEATURES_AVAILABLE:
                try:
                    horse_data = {
                        'horse_name': horse_name,
                        'barrier': base.get('barrier', 0),
                    }
                    race_data = {
                        'track': race.get('track', race.get('course', '')),
                        'distance': str(race.get('distance', '')),
                        'race_class': race.get('raceClass', race.get('class', '')),
                        'race_date': race.get('raceDate', race.get('date', '')),
                    }
                    all_horses_data = [{'horse_name': r.get('horse', r.get('horseName', ''))} for r in runners]
                    enhanced_factors = extract_enhanced_features(horse_data, race_data, all_horses_data)
                    if enhanced_factors.get('combined_factor', 1.0) != 1.0:
                        print(f"[Enhanced] {horse_name}: factor={enhanced_factors.get('combined_factor')}", file=sys.stderr)
                except Exception as ef_err:
                    print(f"[Enhanced] Error for {horse_name}: {ef_err}", file=sys.stderr)
            
            track_bias_data = {'total_points': 0, 'track_fit': 'neutral', 'breakdown': {}, 'descriptions': {}, 'summary': ''}
            if TRACK_BIAS_AVAILABLE and track_bias_scorer:
                try:
                    track_name = race.get('track', race.get('course', ''))
                    barrier = int(base.get('barrier', 0) or 0)
                    distance = str(race.get('distance', '1400m'))
                    if _flag_enabled(MC_FIX_DEAD_FIELDS):
                        # 'pace_regime' never exists on MC results (#124
                        # defect 5): the track-bias scorer received '' for
                        # every runner. 'style' is the engine's key.
                        running_style = mc.get('style', '')
                    else:
                        running_style = mc.get('pace_regime', '')
                    jockey = base.get('jockey', '')
                    trainer = base.get('trainer', '')
                    
                    track_bias_data = track_bias_scorer.calculate_total_points(
                        track=track_name,
                        barrier=barrier,
                        distance=distance,
                        running_style=running_style,
                        jockey=jockey,
                        trainer=trainer
                    )
                except Exception as tb_err:
                    print(f"[TrackBias] Error for {horse_name}: {tb_err}", file=sys.stderr)
            
            fitness_data = {}
            if FITNESS_PEAK_AVAILABLE:
                try:
                    _race_date_for_fitness = race.get('raceDate', race.get('date', ''))
                    fitness_data = get_fitness_peak_data(horse_name, _race_date_for_fitness)
                except Exception as fp_err:
                    print(f"[FitnessPeak] Error for {horse_name}: {fp_err}", file=sys.stderr)
            
            ml_adjustment, _ml_stage_values = calculate_ml_probability_adjustment(
                enhanced_features, include_stages=True)
            
            sophisticated_adj = calculate_sophisticated_adjustment(enhanced_features)
            
            enhanced_factor = enhanced_factors.get('combined_factor', 1.0)
            
            _wfs = enhanced_features.get('weighted_form_score', 0)
            _market_implied = (100.0 / market_odds) if market_odds and market_odds > 1 else 50.0
            _class_penalty = enhanced_features.get('class_fitness_penalty', 1.0)
            if _wfs < 5 or _market_implied < 8 or market_odds > 20:
                enhanced_factor = min(enhanced_factor, 1.10)
            if _class_penalty < 1.0:
                enhanced_factor = enhanced_factor * _class_penalty
            
            fitness_adjustment = 1.0
            if fitness_data:
                frs = fitness_data.get('fitness_readiness_score', 0.5)
                if frs >= 0.9:
                    fitness_adjustment = 1.12
                elif frs >= 0.8:
                    fitness_adjustment = 1.06
                elif frs >= 0.7:
                    fitness_adjustment = 1.02
                elif frs >= 0.5:
                    fitness_adjustment = 1.0
                elif frs >= 0.3:
                    fitness_adjustment = 0.95
                else:
                    fitness_adjustment = 0.90
                if fitness_data.get('improving_and_placed'):
                    fitness_adjustment = min(fitness_adjustment * 1.05, 1.20)
                if fitness_data.get('is_at_peak_run') and fitness_data.get('peak_run_winrate', 0) > 20:
                    fitness_adjustment = min(fitness_adjustment * 1.03, 1.20)
            
            combined_adjustment = (ml_adjustment * 0.55) + (sophisticated_adj * 0.22) + (enhanced_factor * 0.13) + (fitness_adjustment * 0.10)

            _m_odds = market_odds if market_odds else (enhanced_features.get('market_odds', 0) or 0)
            if _m_odds > 20:
                combined_adjustment = min(combined_adjustment, 1.15)
            elif _m_odds > 10:
                combined_adjustment = min(combined_adjustment, 1.25)

            base_win_prob = mc.get('win_prob_sim', 0) * 100
            base_place_prob = mc.get('top3_prob_sim', 0) * 100

            if _sectional_mc_results is not None:
                sec_win = _sectional_mc_results['sim_win_probs'][i] * 100
                sec_place = _sectional_mc_results['sim_place_probs'][i] * 100
                base_win_prob = base_win_prob * 0.70 + sec_win * 0.30
                base_place_prob = base_place_prob * 0.70 + sec_place * 0.30

            adjusted_win_prob = base_win_prob * combined_adjustment
            
            adjusted_win_prob = min(adjusted_win_prob, 60.0)
            adjusted_win_prob = max(adjusted_win_prob, 1.0)
            
            win_prob = adjusted_win_prob
            place_prob = base_place_prob * combined_adjustment
            place_prob = min(place_prob, 90.0)
            
            if real_market_odds and win_prob > 0:
                ev = (win_prob / 100 * real_market_odds) - 1
            else:
                ev = 0

            ci_lower_pct = mc.get('ci_lower', 0) * 100
            ci_upper_pct = mc.get('ci_upper', 0) * 100
            sectional_ci_lower = None
            sectional_ci_upper = None
            if _sectional_mc_results is not None:
                _sec_ci_lo = _sectional_mc_results.get('win_ci_lower')
                _sec_ci_hi = _sectional_mc_results.get('win_ci_upper')
                if _sec_ci_lo is not None and _sec_ci_hi is not None:
                    try:
                        sectional_ci_lower = float(_sec_ci_lo[i]) * 100
                        sectional_ci_upper = float(_sec_ci_hi[i]) * 100
                        ci_lower_pct = (ci_lower_pct * 0.70) + (sectional_ci_lower * 0.30)
                        ci_upper_pct = (ci_upper_pct * 0.70) + (sectional_ci_upper * 0.30)
                    except Exception:
                        sectional_ci_lower = None
                        sectional_ci_upper = None
            
            try:
                _horse_number = str(base.get('number', i + 1))
                _features_serializable = {}
                for fk, fv in enhanced_features.items():
                    if isinstance(fv, (int, float, str, bool, type(None))):
                        _features_serializable[fk] = fv
                    else:
                        _features_serializable[fk] = str(fv)
                _snapshot_rows.append((
                    _race_track, _race_number, _race_date, horse_name, _horse_number,
                    Json(_features_serializable), len(_features_serializable),
                    round(win_prob, 4), 'inference'
                ))
            except Exception:
                pass

            conf = calculate_confidence(base, analysis)
            conf_score = conf['score']
            
            if enhanced_features.get('is_course_distance_winner'):
                conf_score += 0.1
            if enhanced_features.get('is_winning_combo'):
                conf_score += 0.05
            if enhanced_features.get('ground_suitability', 0) > 30:
                conf_score += 0.05
            
            staking = get_staking_tier(conf_score, base.get('model_prob', 0))

            if _flag_enabled(MC_FIX_DEAD_FIELDS):
                # The engine writes stability_score (already 0-100), style
                # and expected_pos; the keys read before #124's defect-5 fix
                # never existed, so these published 0.0 / 'unknown' on every
                # runner. kelly_stake had no producer at all — computed here
                # from the served win probability and the real market price.
                _stability_out = round(mc.get('stability_score', 0), 1)
                _style_out = mc.get('style', 'unknown')
                _expected_pos_out = round(mc.get('expected_pos', 0), 2)
                _kelly_out = round(
                    _display_kelly(win_prob / 100.0, real_market_odds) * 100, 2)
            else:
                _stability_out = round(mc.get('stability', 0) * 100, 1)
                _style_out = mc.get('pace_regime', 'unknown')
                _expected_pos_out = round(mc.get('expected_position', 0), 2)
                _kelly_out = round(mc.get('kelly_stake', 0) * 100, 2)

            result = {
                'horse': horse_name,
                'number': base.get('number', i + 1),
                'barrier': base.get('barrier', 0),
                'jockey': base.get('jockey', ''),
                'trainer': base.get('trainer', ''),
                'weight': base.get('weight', 0),
                'form': base.get('form', ''),
                'age': base.get('age', 0),
                'sire': base.get('sire', ''),
                'dam': base.get('dam', ''),
                'marketOdds': real_market_odds,
                'inferredMarketOdds': round(market_odds, 2) if (market_odds and not real_market_odds) else None,
                'hasRealMarketOdds': bool(real_market_odds and real_market_odds > 1),
                'winPercentage': round(win_prob, 2),
                'placePercentage': round(place_prob, 2),
                'expectedValue': round(ev * 100, 2),
                'edge': round((win_prob - (100 / real_market_odds)) if real_market_odds else 0, 2),
                'confidence': staking['label'],
                'confidenceScore': round(conf_score, 2),
                'stabilityScore': _stability_out,
                'runningStyle': _style_out,
                'ciLower': round(ci_lower_pct, 2),
                'ciUpper': round(ci_upper_pct, 2),
                'sectionalMcCiLower': round(sectional_ci_lower, 2) if sectional_ci_lower is not None else None,
                'sectionalMcCiUpper': round(sectional_ci_upper, 2) if sectional_ci_upper is not None else None,
                'expectedPosition': _expected_pos_out,
                'valueRating': 'Excellent' if ev > 0.3 else 'Good' if ev > 0.1 else 'Fair' if ev > 0 else 'Poor',
                'kellyStake': _kelly_out,
                'fairOdds': round((100.0 / win_prob), 2) if win_prob > 0 else None,
                'marketImpliedWinPct': round((100.0 / real_market_odds), 2) if real_market_odds else None,
                'valueEdgePct': round((win_prob - (100 / real_market_odds)) if real_market_odds else 0, 2),
                '_rawWinProb': float(win_prob),
                '_rawPlaceProb': float(place_prob),
                'selectionScore': float(mc_selection_score({**base, **mc, 'market_odds': market_odds,
                    'late_benchmark': enhanced_features.get('late_benchmark', 0),
                    'overall_benchmark': enhanced_features.get('overall_benchmark', 0),
                    'benchmark_trend': enhanced_features.get('benchmark_trend', 0)})),
                'isPlayable': bool(mc_is_playable({**base, **mc, 'market_odds': market_odds,
                    'late_benchmark': enhanced_features.get('late_benchmark', 0),
                    'overall_benchmark': enhanced_features.get('overall_benchmark', 0)})),
                'mlAdjustment': round(combined_adjustment, 3),
                'groundSuitability': round(enhanced_features.get('ground_suitability', 0), 1),
                'distanceStrikeRate': round(enhanced_features.get('distance_strike_rate', 0), 1),
                'courseStrikeRate': round(enhanced_features.get('course_strike_rate', 0), 1),
                'weightedFormScore': round(enhanced_features.get('weighted_form_score', 0), 1),
                'isFirstUp': enhanced_features.get('is_first_up', 0),
                'isSecondUp': enhanced_features.get('is_second_up', 0),
                'daysSinceRun': enhanced_features.get('days_since_run', 999),
                'isCourseDistanceWinner': enhanced_features.get('is_course_distance_winner', 0),
                'isWinningCombo': enhanced_features.get('is_winning_combo', 0),
                'jockeyTrainerStrikeRate': round(enhanced_features.get('jockey_trainer_strike_rate', 0), 1),
                'paceAdvantage': round(enhanced_features.get('pace_advantage', 0), 2),
                'runningStyleScore': enhanced_features.get('running_style_score', 1),
                'isImproving': enhanced_features.get('is_improving', 0),
                'improvementScore': enhanced_features.get('improvement_score', 0),
                'isClassDrop': enhanced_features.get('is_class_drop', 0),
                'isClassRise': enhanced_features.get('is_class_rise', 0),
                'isBlinkersFirstTime': enhanced_features.get('is_blinkers_first_time', 0),
                'isEliteJockey': enhanced_features.get('is_elite_jockey', 0),
                'isEliteTrainer': enhanced_features.get('is_elite_trainer', 0),
                'isSteamMove': enhanced_features.get('is_steam_move', 0),
                'oddsMovementPct': round(enhanced_features.get('odds_movement_pct', 0), 1),
                'barrierAdvantage': round(enhanced_features.get('barrier_advantage', 0), 2),
                'hasBlinkersFirstTime': enhanced_features.get('is_blinkers_first_time', 0),
                'barrierFieldRatio': enhanced_features.get('barrier_field_ratio', 0.5),
                'formConsistency': round(enhanced_features.get('form_consistency', 0.5), 2),
                'enhancedFactor': enhanced_factors.get('combined_factor', 1.0),
                'classMovement': enhanced_factors.get('class_movement', {}),
                'barrierBias': enhanced_factors.get('barrier_bias', {}),
                'headToHead': enhanced_factors.get('head_to_head', {}),
                'enhancedExplanations': enhanced_factors.get('explanations', []),
                'trackBiasPoints': track_bias_data.get('total_points', 0),
                'trackBiasFit': track_bias_data.get('track_fit', 'neutral'),
                'trackBiasBreakdown': track_bias_data.get('breakdown', {}),
                'trackBiasDescriptions': track_bias_data.get('descriptions', {}),
                'trackBiasSummary': track_bias_data.get('summary', ''),
                'formAnalystInsights': generate_form_analyst_insights(
                    runner_data=original_runner,
                    race_data=race,
                    enhanced_features=enhanced_features,
                    mc_result=mc
                ),
                'fitnessData': {
                    'runsThisPrep': fitness_data.get('runs_this_prep', 0),
                    'runLabel': fitness_data.get('run_label', ''),
                    'isAtPeakRun': fitness_data.get('is_at_peak_run', False),
                    'peakRunNumber': fitness_data.get('peak_run_number', 0),
                    'peakRunWinrate': fitness_data.get('peak_run_winrate', 0),
                    'thisRunWinrate': fitness_data.get('this_run_winrate', 0),
                    'fitnessReadinessScore': fitness_data.get('fitness_readiness_score', 0.5),
                    'prepFormTrajectory': fitness_data.get('prep_form_trajectory', ''),
                    'marginImprovementTrend': fitness_data.get('margin_improvement_trend', 0),
                    'placedLastStart': fitness_data.get('placed_last_start', False),
                    'improvingAndPlaced': fitness_data.get('improving_and_placed', False),
                    'runSpacingQuality': fitness_data.get('run_spacing_quality', 1.0),
                    'spellLengthDays': fitness_data.get('spell_length_days', 0),
                    'careerAvgPrepLength': fitness_data.get('career_avg_prep_length', 0),
                    'positionsThisPrep': fitness_data.get('positions_this_prep', []),
                    'patternFlags': fitness_data.get('pattern_flags', {}),
                    'description': fitness_data.get('description', ''),
                } if fitness_data else None,
                'fitnessAdjustment': round(fitness_adjustment, 3),
                'paceScenarioDistribution': _pace_scenario_dist if _pace_scenario_dist else None,
                'sectionalMcEnhanced': _sectional_mc_results is not None,
                'recalibrationApplied': MC_RECALIBRATION_AVAILABLE and _mc_recalibrator is not None,
                'rawWinProb': round(mc.get('win_prob_sim_raw', mc.get('win_prob_sim', 0)) * 100, 2),
                'calibratedWinProb': round(mc.get('win_prob_sim', 0) * 100, 2),
                'recalibrationShift': round((mc.get('win_prob_sim', 0) - mc.get('win_prob_sim_raw', mc.get('win_prob_sim', 0))) * 100, 2),
                'sectionalMcWinProb': round(_sectional_mc_results['sim_win_probs'][i] * 100, 2) if _sectional_mc_results is not None else None,
                'sectionalMcPlaceProb': round(_sectional_mc_results['sim_place_probs'][i] * 100, 2) if _sectional_mc_results is not None else None,
                'sectionalMcFairOdds': round((1.0 / max(_sectional_mc_results['sim_win_probs'][i], 1e-9)), 2) if _sectional_mc_results is not None else None,
                'blendedBaseProb': round(base_win_prob, 2),
                'paceScenario': _pace_scenario_dist if _pace_scenario_dist else None,
                'mlModelActive': bool(_ml_model_active),
                'enhancedMlActive': ENHANCED_ML_AVAILABLE,
                'mlAdjustmentBreakdown': {
                    'mlWeight': round(ml_adjustment * 0.55, 3),
                    'sophisticatedWeight': round(sophisticated_adj * 0.22, 3),
                    'enhancedWeight': round(enhanced_factor * 0.13, 3),
                    'fitnessWeight': round(fitness_adjustment * 0.10, 3),
                    'combined': round(combined_adjustment, 3),
                },
                'franking_elo': enhanced_features.get('franking_elo', 0),
                'pagerank_authority': enhanced_features.get('pagerank_authority', 0.0),
                'community_strength': enhanced_features.get('community_strength', 0.5),
                'form_stability': enhanced_features.get('form_stability', 0.5),
                'graph_franking_score': enhanced_features.get('graph_franking_score', 0.0),
                'graph_franking_depth': enhanced_features.get('graph_franking_depth', 0),
                'graph_franking_independence': enhanced_features.get('graph_franking_independence', 0),
                'market_validated_franking': enhanced_features.get('market_validated', 0),
                'bridge_score': enhanced_features.get('bridge_score', 0.0),
                'classAvg': enhanced_features.get('class_avg', 0),
                'isImproving': enhanced_features.get('is_improving', 0),
                'form_trajectory': enhanced_features.get('form_trajectory', 0),
                'goingSuitability': enhanced_features.get('going_suitability', 0),
                'distanceSR': enhanced_features.get('distance_sr', 0),
                'courseSR': enhanced_features.get('course_sr', 0),
                'running_style_score': (_style_ordinal(mc.get('style'))
                                        if _flag_enabled(MC_FIX_DEAD_FIELDS)
                                        else mc.get('pace_position', 1)),
                'ml_ranks': enhanced_features.get('ml_ranks', None),
            }
            _attach_prediction_stages(
                result,
                _ml_stage_values,
                mc,
                base_win_prob,
                ml_adjustment,
                combined_adjustment,
                win_prob,
            )
            results.append(result)

            try:
                _edge = round((win_prob - (100 / real_market_odds)) if real_market_odds else 0, 2)
                _audit_rows.append((
                    _race_track, _race_number, _race_date,
                    _off_time_str if _off_time_str else None,
                    horse_name,
                    round(win_prob, 4), round(place_prob, 4),
                    round(real_market_odds, 2) if real_market_odds else None,
                    staking['label'], _edge, 'pending'
                ))
            except Exception:
                pass

        if results:
            _raw_win_total = sum(max(0.0, float(r.get('_rawWinProb', 0.0))) for r in results)
            if _raw_win_total > 0:
                for r in results:
                    _norm_win = max(0.0, float(r.get('_rawWinProb', 0.0))) * 100.0 / _raw_win_total
                    r['winPercentage'] = round(_norm_win, 2)
                    r['fairOdds'] = round((100.0 / _norm_win), 2) if _norm_win > 0 else None

            _raw_place_total = sum(max(0.0, float(r.get('_rawPlaceProb', 0.0))) for r in results)
            if _raw_place_total > 0:
                _place_sum_target = 300.0 if len(results) >= 3 else (200.0 if len(results) == 2 else 100.0)
                for r in results:
                    _norm_place = max(0.0, float(r.get('_rawPlaceProb', 0.0))) * _place_sum_target / _raw_place_total
                    r['placePercentage'] = round(min(100.0, _norm_place), 2)

            # Task 05: shared overround-corrected market probability for the
            # standalone edge (STRIDE_SHARED_MARKET_PROB, default OFF — the
            # legacy raw-implied edge below is byte-identical when off).
            _shared_market_prob = os.environ.get("STRIDE_SHARED_MARKET_PROB", "false").strip().lower() in ("true", "1", "yes")
            _mkt_overround = 1.0
            if _shared_market_prob:
                try:
                    from market_prob import overround as _mp_overround, true_market_prob_pct as _mp_true_market
                    _mkt_overround = _mp_overround([r.get('marketOdds') for r in results])
                except Exception:
                    _shared_market_prob = False

            for r in results:
                _m_odds = r.get('marketOdds')
                try:
                    _m_odds = float(_m_odds) if _m_odds else None
                except (TypeError, ValueError):
                    _m_odds = None
                r['marketOdds'] = _m_odds

                if _m_odds and _m_odds > 1:
                    if _shared_market_prob:
                        _implied = _mp_true_market(_m_odds, _mkt_overround)
                    else:
                        _implied = 100.0 / _m_odds
                    _edge = r['winPercentage'] - _implied
                    _ev = ((r['winPercentage'] / 100.0) * _m_odds) - 1.0
                    r['marketImpliedWinPct'] = round(_implied, 2)
                    r['edge'] = round(_edge, 2)
                    r['valueEdgePct'] = round(_edge, 2)
                    r['expectedValue'] = round(_ev * 100, 2)
                    r['valueRating'] = 'Excellent' if _ev > 0.3 else 'Good' if _ev > 0.1 else 'Fair' if _ev > 0 else 'Poor'
                else:
                    r['marketImpliedWinPct'] = None
                    r['edge'] = 0
                    r['valueEdgePct'] = 0
                    r['expectedValue'] = 0

                r.pop('_rawWinProb', None)
                r.pop('_rawPlaceProb', None)

        try:
            if _snapshot_rows:
                log_feature_snapshots(_snapshot_rows)
            if _audit_rows:
                log_prediction_audit(_audit_rows)
            if _race_track and _race_number:
                log_race_schedule(_race_track, _race_number, _race_date, _off_time_str)
        except Exception as log_err:
            log_debug(f"Batch logging error: {log_err}")

        if BANKER_DETECTOR_AVAILABLE and results:
            try:
                _bd = get_banker_detector()
                _race_class = race.get('raceClass', race.get('race_class', race.get('class', '')))
                _banker_race_info = {
                    'raceClass': _race_class,
                    'field_size': len(results),
                    'track': _race_track,
                    'distance': race.get('distance', 0),
                    'going': race.get('going', race.get('track_condition', '')),
                    'race_name': race.get('raceName', race.get('name', '')),
                }
                results = _bd.detect_bankers(results, _banker_race_info)
                _n_bankers = sum(1 for r in results if r.get('banker_flag'))
                if _n_bankers > 0:
                    _tiers = [r.get('banker_tier', '') for r in results if r.get('banker_flag')]
                    print(f"[Banker] Detected {_n_bankers} banker(s): {', '.join(_tiers)}", file=sys.stderr)
            except Exception as bd_err:
                print(f"[Banker] Error: {bd_err}", file=sys.stderr)

        results.sort(key=lambda x: -x['selectionScore'])
        
        # Top pick is "confident" if win% >= threshold and has positive EV
        confidence_threshold = CONFIG.get('confidence_threshold', 0.18) * 100
        if results:
            top_win_pct = results[0].get('winPercentage', 0)
            second_win_pct = results[1].get('winPercentage', 0) if len(results) > 1 else 0
            top_ev = results[0].get('expectedValue', 0)
            
            # Confident if: top pick has good win%, clear margin over second, and positive EV
            is_confident = bool(
                top_win_pct >= confidence_threshold and 
                (top_win_pct - second_win_pct) >= 5 and  # At least 5% margin
                top_ev >= 0
            )
            results[0]['isConfidentSelection'] = is_confident
            
            for i in range(1, len(results)):
                results[i]['isConfidentSelection'] = False
        
        return {
            'success': True,
            'results': results,
            'track': race.get('track', race.get('course', '')),
            'raceNumber': race.get('raceNumber', race.get('race_number', 0)),
            'distance': race.get('distance', 0),
            'mc_sims': mc_sims,
            'seed': int(seed),
            'paceScenarioDistribution': _pace_scenario_dist if _pace_scenario_dist else None,
            'sectionalMcExactas': _sectional_exactas,
            'sectionalMcTrifectas': _sectional_trifectas,
            'sectionalMcGoingCategory': _sectional_going_category,
            'isConfidentRace': results[0].get('isConfidentSelection', False) if results else False,
        }
        
    except Exception as e:
        return {
            'error': str(e),
            'results': [],
        }


def run_batch_simulation(races_data, mc_sims=10000):
    """Run Monte Carlo simulation on multiple races."""
    try:
        model = get_or_build_base_model()
        
        predict_races = []
        for race_data in races_data:
            runners = race_data.get('runners', [])
            if runners:
                race_obj = create_race_from_api_data(race_data, runners)
                if race_obj.is_valid:
                    predict_races.append(race_obj)
        
        if not predict_races:
            return {'error': 'No valid races found', 'tips': {}}
        
        mc_config = {
            'mc_sims': mc_sims,
            'seed': 42,
            'mc_model': 'plackett_luce',
            'pace_mode': 'basic',
            'uncertainty': 'on',
        }
        
        generator = TipGenerator(model, STAKING_CONFIG['default_bankroll'], mc_config=mc_config)
        all_tips = generator.generate_mc_tips_for_date(predict_races, include_past=False)
        
        formatted_tips = {}
        for track, tips in all_tips.items():
            formatted_tips[track] = []
            for tip in tips:
                if _flag_enabled(MC_FIX_DEAD_FIELDS):
                    # _create_mc_tip nests the engine values under 'mc' and
                    # 'staking'; the flat keys read before #124's defect-5
                    # fix never existed. staking.kelly_pct is already a
                    # percentage (mc_compute_staking multiplies by 100).
                    _tip_mc = tip.get('mc', {})
                    _tip_stability = _tip_mc.get('stability_score', 0)
                    _tip_style = tip.get('style') or 'unknown'
                    _tip_expected_pos = _tip_mc.get('expected_pos', 0)
                    _tip_kelly = tip.get('staking', {}).get('kelly_pct', 0)
                else:
                    _tip_stability = tip.get('stability', 0) * 100
                    _tip_style = tip.get('pace_regime', 'unknown')
                    _tip_expected_pos = tip.get('expected_position', 0)
                    _tip_kelly = tip.get('kelly_stake', 0) * 100
                formatted_tip = {
                    'horse': tip.get('horse', ''),
                    'track': track,
                    'raceNumber': tip.get('race_number', 0),
                    'distance': tip.get('distance', ''),
                    'jockey': tip.get('jockey', ''),
                    'trainer': tip.get('trainer', ''),
                    'barrier': tip.get('barrier', ''),
                    'form': tip.get('form', ''),
                    'marketOdds': tip.get('market_odds', 0),
                    'winPercentage': tip.get('win_prob_sim_pct', 0),
                    'placePercentage': tip.get('top3_prob_sim_pct', 0),
                    'expectedValue': tip.get('ev', 0) * 100 if tip.get('ev') else 0,
                    'edge': tip.get('edge', 0),
                    'confidence': tip.get('staking', {}).get('label', 'BET'),
                    'stabilityScore': _tip_stability,
                    'runningStyle': _tip_style,
                    'ciLower': tip.get('ci_lower', 0) * 100,
                    'ciUpper': tip.get('ci_upper', 0) * 100,
                    'expectedPosition': _tip_expected_pos,
                    'valueRating': 'Excellent' if (tip.get('ev') or 0) > 0.3 else 'Good' if (tip.get('ev') or 0) > 0.1 else 'Fair',
                    'kellyStake': _tip_kelly,
                    'selectionScore': tip.get('selection_score', 0),
                }
                formatted_tips[track].append(formatted_tip)
        
        return {
            'success': True,
            'tips': formatted_tips,
            'totalTips': sum(len(t) for t in formatted_tips.values()),
            'tracksProcessed': len(formatted_tips),
        }
        
    except Exception as e:
        return {
            'error': str(e),
            'tips': {},
        }


def main():
    """Main entry point - read JSON from stdin, output JSON to stdout."""
    try:
        input_data = json.loads(sys.stdin.read())
        
        mode = input_data.get('mode', 'single')
        mc_sims = input_data.get('mc_sims', input_data.get('iterations', 10000))
        
        if mode == 'batch':
            races = input_data.get('races', [])
            result = run_batch_simulation(races, mc_sims)
        else:
            race_data = {
                'track': input_data.get('track', ''),
                'course': input_data.get('track', ''),
                'race_number': input_data.get('race', input_data.get('raceNumber', 1)),
                'distance': input_data.get('distance', '1400m'),
                'going': input_data.get('going', 'Good'),
                'race_name': input_data.get('race_name', ''),
                'race_id': input_data.get('race_id', ''),
                'date': input_data.get('date', input_data.get('raceDate', '')),
                'raceClass': input_data.get('raceClass', input_data.get('race_class', '')),
                'raceDate': input_data.get('raceDate', input_data.get('date', '')),
            }
            runners = input_data.get('runners', [])
            seed = input_data.get('seed', 42)
            result = run_simulation(race_data, runners, mc_sims, seed)
        
        print(json.dumps(result))
        
    except Exception as e:
        print(json.dumps({
            'error': str(e),
            'results': [],
        }))
        sys.exit(1)


if __name__ == '__main__':
    main()
