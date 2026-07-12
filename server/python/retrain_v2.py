#!/usr/bin/env python3
"""
Phase 2 re-training script for the Race-Analytics ensemble model.

Loads data from the `training_view_v2` materialized view, maps columns to
FEATURE_COLUMNS expected by RacingMLModel, and trains XGBoost/LightGBM/CatBoost
with walk-forward temporal CV and isotonic probability calibration.

Walk-forward CV parameters:
  - Minimum training window : 60 days of data
  - Purge gap               : 14 days (anti-leakage)
  - Test window             : 14 days
  - Roll forward in 14-day steps
"""

import argparse
import os
import sys
import pickle
import warnings
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

ENV_PATH = os.path.normpath(
    os.path.join(SCRIPT_DIR, os.pardir, os.pardir, ".env")
)
EXPLICIT_ENV = r"c:\Users\sagea\OneDrive\Desktop\Race-Analytics\Race-Analytics\.env"


def _load_env(path: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env


_env = _load_env(ENV_PATH)
if not _env.get("DATABASE_URL"):
    _env = _load_env(EXPLICIT_ENV)

DATABASE_URL = _env.get("DATABASE_URL") or os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    print(
        f"ERROR: DATABASE_URL not found. Checked {ENV_PATH} and {EXPLICIT_ENV}.",
        file=sys.stderr,
    )
    sys.exit(1)

import psycopg2
import psycopg2.extras

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False
    print("WARNING: xgboost not available", file=sys.stderr)

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False
    print("WARNING: lightgbm not available", file=sys.stderr)

try:
    from catboost import CatBoostClassifier
    HAS_CB = True
except ImportError:
    HAS_CB = False
    print("WARNING: catboost not available", file=sys.stderr)

from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.calibration import CalibratedClassifierCV
from sklearn.isotonic import IsotonicRegression


# Phase 2 sectional feature names (as they appear in FEATURE_COLUMNS)
PHASE2_FEATURES = [
    "z_200m",
    "z_400m",
    "z_600m",
    "z_800m",
    "lambda_decay",
    "svi",
    "rsi",
    "trip_cost_seconds",
    "sectional_trajectory",  # Phase 3: slope of last_200m_time, NaN when missing
    "sectional_rank_at_distance",  # Phase 4: within-race closing rank at distance, NaN when missing
]

# Features that preserve NaN (like PHASE2) but are NOT ablated with sectionals
NAN_PRESERVE_FEATURES = [
    "runs_since_peak",   # NaN when <5 prior runs; tree models exploit missingness
    "trial_recency",     # 999 = no trial; keep NaN rather than corrupt with 0 (0 = trial today)
]

# Mapping: training_view_v2 column  ->  FEATURE_COLUMNS name
VIEW_TO_FEATURE_MAP = {
    # Phase 2 sectional features (prior_ prefix in view)
    "prior_z_200m": "z_200m",
    "prior_z_400m": "z_400m",
    "prior_z_600m": "z_600m",
    "prior_z_800m": "z_800m",
    "prior_lambda_decay": "lambda_decay",
    "prior_svi": "svi",
    "prior_rsi": "rsi",
    "prior_trip_cost_seconds": "trip_cost_seconds",
    # Structural / race-level features
    "barrier": "barrier_draw",
    "distance_m": "distance",
    "weight_kg": "weight_kg",
    "field_size": "field_size",
    "class_level": "class_level",
    # Odds — sp_odds is the primary odds column in the view;
    # market_odds is sparsely populated so we fill from sp_odds
    "sp_odds": "market_odds",
    # Pass-through features that exist in the view under the same name
    "weighted_form_score": "weighted_form_score",
    "ground_suitability": "ground_suitability",
    "distance_strike_rate": "distance_strike_rate",
}

# Full FEATURE_COLUMNS list as defined in RacingMLModel (77 total)
FEATURE_COLUMNS = [
    "distance_strike_rate",
    "course_strike_rate",
    "weighted_form_score",
    "is_first_up",
    "is_second_up",
    "days_since_run",
    "jockey_trainer_strike_rate",
    "is_winning_combo",
    "barrier_draw",
    "weight_kg",
    "ground_suitability",
    "market_odds",
    "running_style_score",
    "pace_advantage",
    "is_high_pace_expected",
    "distance_style_match",
    "class_movement",
    "is_class_drop",
    "is_class_rise",
    "is_first_time_stakes",
    "is_blinkers_first_time",
    "is_elite_jockey",
    "is_elite_trainer",
    "is_jockey_upgrade",
    "barrier_advantage",
    "track_bias_score",
    "is_improving",
    "improvement_score",
    "is_in_form_cycle",
    "has_dominant_win",
    "is_steam_move",
    "is_drift",
    "odds_movement_pct",
    "last_start_market_diff",
    "avg_market_diff_3runs",
    "market_trend_shortening",
    "market_trend_drifting",
    "empirical_barrier_advantage",
    "predicted_settling_pos",
    "settling_percentile",
    "pace_pressure_index",
    "settling_difficulty",
    "settling_pace_interaction",
    "is_congested_speed",
    "speed_horse_ratio",
    "days_since_last_normalized",
    "prep_run_x_days_since",
    "run_spacing_quality",
    "empirical_freshness_score",
    "is_quick_backup",
    "is_long_absence",
    "distance_change_staleness",
    "class_x_spell",
    "steam_velocity",
    "drift_velocity",
    "late_move_indicator",
    "market_confidence",
    "relative_move",
    "smart_money_score",
    "is_insider_signal",
    "field_market_agreement",
    "z_200m",
    "z_400m",
    "z_600m",
    "z_800m",
    "lambda_decay",
    "svi",
    "rsi",
    "trip_cost_seconds",
    "distance",
    "field_size",
    "class_level",
    "form_direction_slope",
    "speed_rating_trajectory",
    "sectional_trajectory",
    "campaign_run_number",
    "weight_change",
    "jockey_booking_change",
    "fresh_x_trajectory",
    "first_up_win_rate",
    "second_up_win_rate",
    "consistency_score",
    "trainer_momentum_score",
    "going_suitability",
    "fitness_x_distance",
    "barrier_x_pace_inv",
    "sectional_x_going",
    "class_drop_x_trajectory",
    "campaign_run_x_fitness",
    "pace_pressure_score",
    "leader_advantage",
    "closer_advantage",
    "barrier_relevance_score",
    "field_size_context",
    "market_efficiency_flag",
    "td_pace_bias",
    "td_upset_rate",
    "td_barrier_style_edge",
    "td_closing_speed_bias",
    # RED (<20% coverage) features excluded from this retrain:
    #   dist_sectional_slope (13.7%), dist_sectional_recency_weighted (13.4%),
    #   sectional_result_divergence (1.2%), first_at_distance_sectional_quality (4.5%),
    #   step_up_x_dist_slope (1.7%)
    # YELLOW features retained with NaN preservation:
    "distance_direction_flag",
    "sectional_rank_at_distance",
    # Data availability signal
    "has_sectional_data",
    "is_bounce_candidate",
    "bounce_severity",
    "runs_since_peak",
    # Phase 2: Barrier trial features
    "trial_recency",           # days since most recent trial (999 = no trial; NaN-preserved)
    "trial_count_60d",         # trials in 60 days before race
    "trial_x_experience",      # best trial position percentile × (1/career_starts)
    "trainer_trial_pattern",   # post-trial win rate ÷ own first-up rate × credibility; 1.0 = neutral
    "trial_quality_score",     # quality_raw × field_multiplier × volume_factor; co-trialist strength
    # Phase 5: within-race relative market position (relative_market.py;
    # names/semantics match mc_api.extract_ml_features for train/serve parity)
    "fair_implied_prob",
    "odds_rank",
    "odds_rank_pct",
]

# Non-sectional features — fill NaN with 0 for these (exclude both PHASE2 and NAN_PRESERVE)
NON_SECTIONAL_FEATURES = [f for f in FEATURE_COLUMNS if f not in PHASE2_FEATURES and f not in NAN_PRESERVE_FEATURES]

MODELS_DIR = os.path.join(SCRIPT_DIR, "models")
os.makedirs(MODELS_DIR, exist_ok=True)

OUTPUT_MODEL_PATH = os.path.join(MODELS_DIR, "racing_ensemble_v2.pkl")



def load_training_data() -> pd.DataFrame:
    """
    Load all rows from training_view_v2 and return a DataFrame with columns
    aligned to FEATURE_COLUMNS plus the target column `is_winner`.
    """
    print("[1] Connecting to database and loading training_view_v2 ...")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT
            race_date,
            is_winner,
            position,
            margin_lengths,
            -- Identity (for form feature computation)
            horse_name,
            track,
            jockey,
            race_number,
            -- Structural features
            barrier,
            distance_m,
            weight_kg,
            field_size,
            class_level,
            -- Form data (for pace feature backfill)
            form_string,
            -- Odds (sp_odds is the most populated)
            sp_odds,
            market_odds,
            -- Existing engineered features
            weighted_form_score,
            ground_suitability,
            distance_strike_rate,
            -- Phase 2 sectional features (NaN-safe: keep NULL as-is)
            prior_z_200m,
            prior_z_400m,
            prior_z_600m,
            prior_z_800m,
            prior_lambda_decay,
            prior_svi,
            prior_rsi,
            prior_trip_cost_seconds
        FROM training_view_v2
        WHERE is_winner IS NOT NULL
        ORDER BY race_date
        """
    )
    rows = cur.fetchall()
    cur.close()

    df = pd.DataFrame(rows)
    print(f"    Loaded {len(df):,} rows from training_view_v2")

    try:
        from form_feature_builder import batch_compute_form_features
        print("\n[1b] Computing form features from race_results_history ...")
        form_df = batch_compute_form_features(df, conn)
        # Store form features on the main df for use in build_feature_matrix
        df["_form_features"] = [form_df.iloc[i].to_dict() for i in range(len(form_df))]
    except Exception as e:
        print(f"    WARNING: Form feature computation failed: {e}")
        import traceback
        traceback.print_exc()
        df["_form_features"] = [{}] * len(df)

    conn.close()

    # Load full field composition for pace feature backfill (separate connection)
    print("\n[1c] Loading field composition for pace features ...")
    try:
        conn2 = psycopg2.connect(DATABASE_URL)
        cur2 = conn2.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur2.execute("""
            SELECT race_date, track, race_number, horse_name, form_string, barrier, field_size
            FROM race_results_history
            WHERE race_date >= %s AND race_date <= %s
        """, (str(df["race_date"].min()), str(df["race_date"].max())))
        field_rows = cur2.fetchall()
        cur2.close()
        conn2.close()

        from collections import defaultdict
        field_comp = defaultdict(list)
        for row in field_rows:
            key = (str(row["race_date"]), str(row["track"]), int(row["race_number"]))
            field_comp[key].append(row)

        df.attrs["_field_composition"] = dict(field_comp)
        print(f"    Loaded {len(field_rows):,} runners across {len(field_comp):,} races")
    except Exception as e:
        print(f"    WARNING: Field composition load failed: {e}")
        import traceback
        traceback.print_exc()
        df.attrs["_field_composition"] = {}

    return df



_STYLE_LEADER = 4
_STYLE_ON_PACE = 3
_STYLE_MIDFIELD = 2
_STYLE_OFF_PACE = 1
_STYLE_BACKMARKER = 0
_EXCLUDED_CHARS = set("xfdwXFDW")


def _classify_running_style_from_form(form_string, barrier=None, field_size=None):
    """
    Classify running style from form_string.
    Returns style code (0-4) matching speed_mapping.RunningStyle.

    Character handling:
      Digits 1-9: finishing position
      0: treated as 10th+
    """
    if not form_string or not isinstance(form_string, str):
        if barrier is not None and field_size and field_size > 0:
            try:
                b = int(barrier)
            except (ValueError, TypeError):
                return _STYLE_MIDFIELD
            if b <= field_size * 0.25:
                return _STYLE_MIDFIELD
            return _STYLE_OFF_PACE
        return _STYLE_MIDFIELD

    cleaned = form_string.replace("-", "").replace(" ", "")
    positions = []
    for ch in cleaned[-6:]:
        if ch in _EXCLUDED_CHARS:
            continue
        if ch.isdigit():
            pos = int(ch)
            if pos == 0:
                pos = 10
            positions.append(pos)
        if len(positions) >= 5:
            break

    if not positions:
        if barrier is not None and field_size and field_size > 0:
            try:
                b = int(barrier)
            except (ValueError, TypeError):
                return _STYLE_MIDFIELD
            if b <= field_size * 0.25:
                return _STYLE_MIDFIELD
            return _STYLE_OFF_PACE
        return _STYLE_MIDFIELD

    avg_pos = sum(positions) / len(positions)
    wins_from_front = sum(1 for p in positions if p == 1)

    if wins_from_front >= 2 or (len(positions) >= 3 and avg_pos <= 1.5):
        return _STYLE_LEADER
    elif avg_pos <= 2.5:
        return _STYLE_ON_PACE
    elif avg_pos <= 4.5:
        return _STYLE_MIDFIELD
    elif avg_pos <= 6:
        return _STYLE_OFF_PACE
    else:
        return _STYLE_BACKMARKER


def _compute_pace_features(df: pd.DataFrame) -> dict:
    """
    Compute pace_pressure_score, leader_advantage, closer_advantage for each
    training row using field composition from race_results_history.

    Returns dict of {col_name: Series}.
    """
    field_comp = df.attrs.get("_field_composition", {})

    pace_pressure = np.full(len(df), 0.5)
    leader_adv = np.zeros(len(df))
    closer_adv = np.zeros(len(df))

    if not field_comp:
        print("  WARNING: No field composition data — pace features default to 0.5/0.0")
        return {"pace_pressure_score": pd.Series(pace_pressure, index=df.index),
                "leader_advantage": pd.Series(leader_adv, index=df.index),
                "closer_advantage": pd.Series(closer_adv, index=df.index)}

    race_pace = {}
    for key, runners in field_comp.items():
        try:
            fs = len(runners) or 1
            speed_count = sum(
                1 for r in runners
                if _classify_running_style_from_form(
                    r.get("form_string"),
                    barrier=r.get("barrier"),
                    field_size=r.get("field_size") or fs,
                ) >= _STYLE_ON_PACE
            )
            pps = speed_count / fs
            if pps >= 0.4:
                race_pace[key] = (pps, -0.10, 0.12)
            elif pps <= 0.15:
                race_pace[key] = (pps, 0.15, -0.05)
            else:
                race_pace[key] = (pps, 0.0, 0.0)
        except Exception:
            race_pace[key] = (0.5, 0.0, 0.0)

    print(f"  Race pace computed for {len(race_pace):,} races")

    rd_arr = df["race_date"].astype(str).values
    track_arr = df["track"].astype(str).str.strip().values
    rnum_arr = pd.to_numeric(df["race_number"], errors="coerce").fillna(0).astype(int).values
    form_arr = df["form_string"].values
    barrier_arr = df["barrier"].values
    fs_arr = df["field_size"].values

    matched = 0
    for i in range(len(df)):
        key = (rd_arr[i], track_arr[i], int(rnum_arr[i]))
        rp = race_pace.get(key)
        if rp is None:
            continue

        pps, l_adv, c_adv = rp
        pace_pressure[i] = pps

        _form = form_arr[i]
        _bar = barrier_arr[i]
        _fs = fs_arr[i]
        style = _classify_running_style_from_form(
            _form if pd.notna(_form) else None,
            barrier=_bar if pd.notna(_bar) else None,
            field_size=_fs if pd.notna(_fs) else None,
        )
        matched += 1

        if style >= _STYLE_ON_PACE:
            leader_adv[i] = l_adv
        elif style <= _STYLE_OFF_PACE:
            closer_adv[i] = c_adv
        else:
            if pps >= 0.4:
                closer_adv[i] = c_adv * 0.6
            elif pps <= 0.15:
                leader_adv[i] = l_adv * 0.4

    pps_std = np.std(pace_pressure)
    print(f"  Pace features computed: {matched:,}/{len(df):,} rows matched to field data")
    print(f"  pace_pressure_score std: {pps_std:.4f} ({'sufficient variation' if pps_std >= 0.05 else 'low variation'})")

    return {"pace_pressure_score": pd.Series(pace_pressure, index=df.index),
            "leader_advantage": pd.Series(leader_adv, index=df.index),
            "closer_advantage": pd.Series(closer_adv, index=df.index)}


def build_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map view columns to FEATURE_COLUMNS and return a DataFrame ready for
    model training. Non-sectional features are filled with 0.
    Sectional (Phase 2) features keep NaN so tree models exploit missingness.
    """
    out = pd.DataFrame(index=df.index)


    # market_odds: prefer view's market_odds (sparse) else use sp_odds
    if "market_odds" in df.columns and "sp_odds" in df.columns:
        df["_effective_odds"] = pd.to_numeric(
            df["market_odds"], errors="coerce"
        ).fillna(pd.to_numeric(df["sp_odds"], errors="coerce"))
    elif "sp_odds" in df.columns:
        df["_effective_odds"] = pd.to_numeric(df["sp_odds"], errors="coerce")
    else:
        df["_effective_odds"] = np.nan

    for view_col, feat_col in VIEW_TO_FEATURE_MAP.items():
        if view_col == "sp_odds":
            # Already handled via _effective_odds above
            continue
        if view_col in df.columns:
            out[feat_col] = pd.to_numeric(df[view_col], errors="coerce")

    out["market_odds"] = df["_effective_odds"]

    # Phase 5: within-race relative market position (favourite ladder).
    # Grouped by the same (race_date, track, race_number) key as pace features.
    try:
        from relative_market import add_relative_market_features
        out = add_relative_market_features(out, df, odds_col="_effective_odds")
    except Exception as _rm_err:
        print(f"  WARNING: relative market features failed: {_rm_err}")
        for _c in ("fair_implied_prob", "odds_rank", "odds_rank_pct"):
            out[_c] = 0.0

    for col in FEATURE_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan

    if "_form_features" in df.columns:
        form_records = [
            d if isinstance(d, dict) else {}
            for d in df["_form_features"]
        ]
        form_df = pd.DataFrame(form_records, index=df.index)
        form_cols_present = [c for c in form_df.columns if c in out.columns]
        if form_cols_present:
            form_df = form_df[form_cols_present]
            out.update(form_df)
            print(f"  Form features applied: {sorted(form_cols_present)}")

    _td_profiles = {}
    _td_profile_path = os.path.join(SCRIPT_DIR, "intelligence", "track_distance_profiles.json")
    if os.path.exists(_td_profile_path):
        try:
            import json as _json
            with open(_td_profile_path) as _f:
                _td_profiles = _json.load(_f)
            print(f"  Track-distance profiles loaded: {len(_td_profiles)} entries")
        except Exception as _e:
            print(f"  WARNING: Track-distance profiles load failed: {_e}")

    if _td_profiles:
        from track_profiler import lookup_profile as _td_lookup
        _tracks = df["track"].astype(str).str.strip().values
        _dists = pd.to_numeric(df.get("distance_m", pd.Series(dtype=float)), errors="coerce").fillna(0).astype(int).values
        _barriers_raw = df["barrier"].values if "barrier" in df.columns else [None] * len(df)
        td_features = []
        for i in range(len(df)):
            try:
                b = int(_barriers_raw[i]) if pd.notna(_barriers_raw[i]) else None
            except (ValueError, TypeError):
                b = None
            td_features.append(_td_lookup(_td_profiles, _tracks[i], int(_dists[i]),
                                          barrier=b, running_style=None))
        td_df = pd.DataFrame(td_features, index=df.index)
        for col in ["td_pace_bias", "td_upset_rate", "td_barrier_style_edge", "td_closing_speed_bias"]:
            if col in td_df.columns:
                out[col] = td_df[col]
    else:
        out["td_pace_bias"] = 0.5
        out["td_upset_rate"] = 0.2
        out["td_barrier_style_edge"] = 0
        out["td_closing_speed_bias"] = 0

    _pace = _compute_pace_features(df)
    out["pace_pressure_score"] = _pace["pace_pressure_score"]
    out["leader_advantage"] = _pace["leader_advantage"]
    out["closer_advantage"] = _pace["closer_advantage"]

    dist_col = out["distance"].fillna(1400)
    out["barrier_relevance_score"] = dist_col.apply(
        lambda d: 1.0 if d <= 1200 else 0.7 if d <= 1600 else 0.4 if d <= 2000 else 0.2
    )

    out["field_size_context"] = (out["field_size"].fillna(10) / 16.0).clip(upper=1.0)

    cl = out["class_level"].fillna(50)
    out["market_efficiency_flag"] = cl.apply(
        lambda c: 1.0 if c >= 80 else 0.7 if c >= 60 else 0.5 if c >= 50
        else 0.2 if c <= 30 else 0.4
    )

    # fitness_x_distance: campaign_run_number proxy peaks at run 3
    crn = out["campaign_run_number"].fillna(1)
    fitness_proxy = (1 - (crn - 3).abs() * 0.15).clip(lower=0)
    out["fitness_x_distance"] = fitness_proxy * out["distance_strike_rate"].fillna(0)

    # barrier_x_pace_inv: use computed pace_pressure_score if it has meaningful variation
    _pps = out["pace_pressure_score"]
    if _pps.std() >= 0.05:
        out["barrier_x_pace_inv"] = out["barrier_advantage"].fillna(0) * _pps
    else:
        out["barrier_x_pace_inv"] = out["barrier_advantage"].fillna(0) * 0.5

    # sectional_x_going: z_200m * going_suitability
    out["sectional_x_going"] = out["z_200m"].fillna(0) * out["going_suitability"].fillna(0.5)

    # class_drop_x_trajectory: is_class_drop * form_direction_slope
    out["class_drop_x_trajectory"] = (
        out["is_class_drop"].fillna(0) * out["form_direction_slope"].fillna(0)
    )

    # campaign_run_x_fitness: decays after run 5
    campaign_freshness = (1 - (crn - 5).clip(lower=0) * 0.2).clip(lower=0)
    out["campaign_run_x_fitness"] = crn * campaign_freshness

    # step_up_x_dist_slope: excluded (RED coverage) — depends on dist_sectional_slope (13.7%)

    for col in NON_SECTIONAL_FEATURES:
        if col in out.columns:
            out[col] = out[col].fillna(0)

    # Phase 2 sectional columns + NAN_PRESERVE_FEATURES intentionally keep NaN (tree models)

    if "has_sectional_data" in out.columns:
        out["has_sectional_data"] = out["has_sectional_data"].clip(0, 1).round().astype(int)

    out = out[[c for c in FEATURE_COLUMNS if c in out.columns]]

    return out



class DateWindowSplitter:
    """
    Walk-forward CV with date-based windows.

    min_train_days: Minimum calendar days required for the initial training window.
    purge_gap_days: Gap between training end and test start to prevent leakage.
    """

    def __init__(
        self,
        min_train_days: int = 60,
        purge_gap_days: int = 14,
        test_window_days: int = 14,
        step_days: int = 14,
    ):
        self.min_train_days = min_train_days
        self.purge_gap_days = purge_gap_days
        self.test_window_days = test_window_days
        self.step_days = step_days

    def split(
        self, dates: pd.Series
    ) -> List[Tuple[np.ndarray, np.ndarray, Dict]]:
        """
        Return list of (train_idx, test_idx, metadata) tuples.
        dates must be pd.DatetimeIndex-compatible and already sorted ascending.
        """
        dates = pd.to_datetime(dates).reset_index(drop=True)
        min_date = dates.min()
        max_date = dates.max()

        folds = []
        fold_num = 0

        first_test_start = min_date + timedelta(
            days=self.min_train_days + self.purge_gap_days
        )
        test_start = first_test_start

        while test_start <= max_date:
            test_end = test_start + timedelta(days=self.test_window_days)
            train_cutoff = test_start - timedelta(days=self.purge_gap_days)

            train_mask = dates < train_cutoff
            test_mask = (dates >= test_start) & (dates < test_end)

            train_idx = np.where(train_mask)[0]
            test_idx = np.where(test_mask)[0]

            if len(train_idx) < 50 or len(test_idx) < 5:
                test_start += timedelta(days=self.step_days)
                continue

            fold_num += 1
            meta = {
                "fold": fold_num,
                "train_start": str(dates.iloc[train_idx[0]].date()),
                "train_end": str(dates.iloc[train_idx[-1]].date()),
                "train_size": len(train_idx),
                "test_start": str(test_start.date()),
                "test_end": str(test_end.date()),
                "test_size": len(test_idx),
                "gap_days": self.purge_gap_days,
            }
            folds.append((train_idx, test_idx, meta))
            test_start += timedelta(days=self.step_days)

        return folds



def _get_xgb_params() -> Dict:
    return {
        "n_estimators": 200,
        "max_depth": 6,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_weight": 5,
        "scale_pos_weight": 9,   # ~10:1 class imbalance
        "tree_method": "hist",
        "random_state": 42,
        "verbosity": 0,
        "eval_metric": "auc",
    }


def _get_lgb_params() -> Dict:
    return {
        "n_estimators": 200,
        "max_depth": 6,
        "learning_rate": 0.05,
        "num_leaves": 63,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_samples": 20,
        "is_unbalance": True,
        "random_state": 42,
        "verbose": -1,
    }


def _get_catboost_params() -> Dict:
    return {
        "iterations": 200,
        "depth": 6,
        "learning_rate": 0.05,
        "auto_class_weights": "Balanced",
        "random_seed": 42,
        "verbose": 0,
        "allow_writing_files": False,
    }


def train_single_fold(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    feature_cols: List[str],
) -> Tuple[Any, Any, Any, Dict[str, float], Dict[str, list]]:
    """
    Train XGBoost, LightGBM, CatBoost independently on one fold.
    Apply isotonic calibration on the validation set.
    Returns (xgb_model, lgb_model, cb_model, per_model_auc_dict, oof_raw).
    oof_raw contains raw (pre-isotonic) predictions for OOF calibrator fitting.
    """
    X_tr = X_train[feature_cols].copy()
    X_vl = X_val[feature_cols].copy()

    per_model_auc: Dict[str, float] = {}
    raw_xgb: list = []
    raw_lgb: list = []
    raw_cb: list = []

    xgb_model = None
    if HAS_XGB:
        xgb_model = xgb.XGBClassifier(**_get_xgb_params())
        xgb_model.fit(X_tr, y_train, eval_set=[(X_vl, y_val)], verbose=False)
        raw_xgb = xgb_model.predict_proba(X_vl)[:, 1]
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(raw_xgb, y_val.values)
        xgb_model._isotonic = iso
        cal_val = iso.transform(raw_xgb)
        try:
            per_model_auc["xgb"] = float(roc_auc_score(y_val, cal_val))
        except Exception:
            per_model_auc["xgb"] = float("nan")
        raw_xgb = list(raw_xgb)

    lgb_model = None
    if HAS_LGB:
        lgb_model = lgb.LGBMClassifier(**_get_lgb_params())
        lgb_model.fit(
            X_tr, y_train,
            eval_set=[(X_vl, y_val)],
            callbacks=[lgb.early_stopping(20, verbose=False),
                       lgb.log_evaluation(-1)],
        )
        raw_lgb = lgb_model.predict_proba(X_vl)[:, 1]
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(raw_lgb, y_val.values)
        lgb_model._isotonic = iso
        cal_val = iso.transform(raw_lgb)
        try:
            per_model_auc["lgb"] = float(roc_auc_score(y_val, cal_val))
        except Exception:
            per_model_auc["lgb"] = float("nan")
        raw_lgb = list(raw_lgb)

    cb_model = None
    if HAS_CB:
        cb_model = CatBoostClassifier(**_get_catboost_params())
        cb_model.fit(X_tr, y_train, eval_set=(X_vl, y_val))
        raw_cb = cb_model.predict_proba(X_vl)[:, 1]
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(raw_cb, y_val.values)
        cb_model._isotonic = iso
        cal_val = iso.transform(raw_cb)
        try:
            per_model_auc["cb"] = float(roc_auc_score(y_val, cal_val))
        except Exception:
            per_model_auc["cb"] = float("nan")
        raw_cb = list(raw_cb)

    oof_raw = {
        "xgb": raw_xgb,
        "lgb": raw_lgb,
        "cb":  raw_cb,
        "labels": list(y_val.values),
    }
    return xgb_model, lgb_model, cb_model, per_model_auc, oof_raw


def predict_ensemble(
    xgb_model, lgb_model, cb_model,
    X: pd.DataFrame,
    feature_cols: List[str],
) -> np.ndarray:
    """
    Compute calibrated ensemble probability using equal weights.
    Falls back gracefully when a model is None.
    """
    X_sub = X[feature_cols]
    preds = []

    if xgb_model is not None:
        raw = xgb_model.predict_proba(X_sub)[:, 1]
        if hasattr(xgb_model, "_isotonic"):
            raw = xgb_model._isotonic.transform(raw)
        preds.append(raw)

    if lgb_model is not None:
        raw = lgb_model.predict_proba(X_sub)[:, 1]
        if hasattr(lgb_model, "_isotonic"):
            raw = lgb_model._isotonic.transform(raw)
        preds.append(raw)

    if cb_model is not None:
        raw = cb_model.predict_proba(X_sub)[:, 1]
        if hasattr(cb_model, "_isotonic"):
            raw = cb_model._isotonic.transform(raw)
        preds.append(raw)

    if not preds:
        return np.zeros(len(X_sub))

    return np.mean(preds, axis=0)



def aggregate_feature_importance(
    xgb_model, lgb_model, cb_model, feature_cols: List[str]
) -> Dict[str, float]:
    importance: Dict[str, float] = {}

    def _add(imp_arr, weight=1.0):
        if imp_arr is None:
            return
        total = max(sum(imp_arr), 1e-9)
        for name, val in zip(feature_cols, imp_arr):
            importance[name] = importance.get(name, 0.0) + (val / total) * weight

    if xgb_model is not None:
        _add(xgb_model.feature_importances_)
    if lgb_model is not None:
        _add(lgb_model.feature_importances_)
    if cb_model is not None:
        _add(cb_model.feature_importances_)

    n_models = sum([xgb_model is not None, lgb_model is not None, cb_model is not None])
    if n_models > 0:
        importance = {k: v / n_models for k, v in importance.items()}

    return importance



def run_walk_forward_cv(
    X: pd.DataFrame,
    y: pd.Series,
    dates: pd.Series,
    feature_cols: List[str],
    label: str = "full",
    collect_oof_calibrators: bool = False,
) -> Dict[str, Any]:
    """
    Run full walk-forward CV for the given feature set.
    Returns aggregated metrics and per-fold results.
    """
    splitter = DateWindowSplitter(
        min_train_days=60,
        purge_gap_days=14,
        test_window_days=14,
        step_days=14,
    )
    folds = splitter.split(dates)

    print(f"\n  [{label}] {len(folds)} folds generated")
    print(
        f"  {'Fold':<5} {'Train start':<12} {'Train end':<12} "
        f"{'N_train':>8} {'N_test':>7} {'AUC':>7} {'Brier':>7} "
        f"{'xgb AUC':>9} {'lgb AUC':>9} {'cb AUC':>8}"
    )
    print("  " + "-" * 95)

    fold_results = []
    all_auc = []
    all_brier = []

    # OOF accumulators — only populated when collect_oof_calibrators=True
    oof_xgb, oof_lgb, oof_cb, oof_labels = [], [], [], []

    for train_idx, test_idx, meta in folds:
        X_train = X.iloc[train_idx]
        y_train = y.iloc[train_idx]
        X_test = X.iloc[test_idx]
        y_test = y.iloc[test_idx]

        if y_train.sum() < 5 or y_test.sum() < 2:
            continue

        try:
            xgb_m, lgb_m, cb_m, per_auc, oof_raw = train_single_fold(
                X_train, y_train, X_test, y_test, feature_cols
            )
        except Exception as exc:
            print(f"  ERROR fold {meta['fold']}: {exc}", file=sys.stderr)
            continue

        if collect_oof_calibrators:
            oof_xgb.extend(oof_raw["xgb"])
            oof_lgb.extend(oof_raw["lgb"])
            oof_cb.extend(oof_raw["cb"])
            oof_labels.extend(oof_raw["labels"])

        ens_proba = predict_ensemble(xgb_m, lgb_m, cb_m, X_test, feature_cols)

        try:
            fold_auc = float(roc_auc_score(y_test, ens_proba))
        except Exception:
            fold_auc = float("nan")

        try:
            fold_brier = float(brier_score_loss(y_test, ens_proba))
        except Exception:
            fold_brier = float("nan")

        if not np.isnan(fold_auc):
            all_auc.append(fold_auc)
        if not np.isnan(fold_brier):
            all_brier.append(fold_brier)

        fold_entry = {
            "fold": meta["fold"],
            "train_start": meta["train_start"],
            "train_end": meta["train_end"],
            "train_size": meta["train_size"],
            "test_start": meta["test_start"],
            "test_end": meta["test_end"],
            "test_size": meta["test_size"],
            "auc": fold_auc,
            "brier": fold_brier,
            "per_model_auc": per_auc,
        }
        fold_results.append(fold_entry)

        xgb_auc_str = f"{per_auc.get('xgb', float('nan')):.4f}" if "xgb" in per_auc else "  n/a "
        lgb_auc_str = f"{per_auc.get('lgb', float('nan')):.4f}" if "lgb" in per_auc else "  n/a "
        cb_auc_str  = f"{per_auc.get('cb',  float('nan')):.4f}" if "cb"  in per_auc else " n/a"
        auc_str   = f"{fold_auc:.4f}" if not np.isnan(fold_auc) else "  n/a"
        brier_str = f"{fold_brier:.4f}" if not np.isnan(fold_brier) else "  n/a"

        print(
            f"  {meta['fold']:<5} {meta['train_start']:<12} {meta['train_end']:<12} "
            f"{meta['train_size']:>8} {meta['test_size']:>7} "
            f"{auc_str:>7} {brier_str:>7} "
            f"{xgb_auc_str:>9} {lgb_auc_str:>9} {cb_auc_str:>8}"
        )

    mean_auc   = float(np.mean(all_auc))   if all_auc   else float("nan")
    mean_brier = float(np.mean(all_brier)) if all_brier else float("nan")
    std_auc    = float(np.std(all_auc))    if len(all_auc) > 1 else 0.0

    print(
        f"\n  [{label}] SUMMARY:  "
        f"mean AUC={mean_auc:.4f} (±{std_auc:.4f})  "
        f"mean Brier={mean_brier:.4f}  "
        f"folds={len(fold_results)}"
    )

    results = {
        "label": label,
        "n_folds": len(fold_results),
        "mean_auc": mean_auc,
        "std_auc": std_auc,
        "mean_brier": mean_brier,
        "folds": fold_results,
    }

    if collect_oof_calibrators and oof_labels:
        oof_calibrators = {}
        for key, preds in [("xgb", oof_xgb), ("lgb", oof_lgb), ("cb", oof_cb)]:
            if preds:
                iso = IsotonicRegression(out_of_bounds="clip")
                iso.fit(preds, oof_labels)
                oof_calibrators[key] = iso
        results["oof_calibrators"] = oof_calibrators
        print(f"  OOF calibrators fitted: {list(oof_calibrators.keys())} ({len(oof_labels)} predictions)")

    return results



def run_ablation(
    X: pd.DataFrame,
    y: pd.Series,
    dates: pd.Series,
) -> Dict[str, Any]:
    """Compare AUC with all 77 features vs without the 8 Phase 2 sectional features."""
    print("\n" + "=" * 70)
    print("ABLATION STUDY: with vs without Phase 2 sectional features")
    print("=" * 70)

    all_features = [c for c in FEATURE_COLUMNS if c in X.columns]
    base_features = [c for c in all_features if c not in PHASE2_FEATURES]

    print(f"\n  Full feature set : {len(all_features)} features")
    print(f"  Base feature set : {len(base_features)} features (Phase 2 excluded)")

    results_full = run_walk_forward_cv(X, y, dates, all_features, label="WITH Phase2")
    results_base = run_walk_forward_cv(X, y, dates, base_features, label="WITHOUT Phase2")

    delta = results_full["mean_auc"] - results_base["mean_auc"]
    print("\n  ABLATION RESULT:")
    print(f"    AUC WITH    Phase 2 : {results_full['mean_auc']:.4f}")
    print(f"    AUC WITHOUT Phase 2 : {results_base['mean_auc']:.4f}")
    print(f"    Delta (improvement) : {delta:+.4f}")

    return {
        "with_phase2": results_full,
        "without_phase2": results_base,
        "delta_auc": delta,
    }



def train_final_model(
    X: pd.DataFrame,
    y: pd.Series,
    feature_cols: List[str],
    oof_calibrators: dict = None,
) -> Tuple[Any, Any, Any, Dict[str, float]]:
    """
    Train XGBoost, LightGBM, CatBoost on the full dataset.
    Uses a held-out 10% split for early stopping only.
    Isotonic calibrators come from OOF predictions (if provided) to avoid
    the leak where X_cal is used for both early stopping and calibration fitting.
    """
    n = len(X)
    split_idx = int(n * 0.9)

    X_train_all = X.iloc[:split_idx][feature_cols]
    y_train_all = y.iloc[:split_idx]
    X_cal = X.iloc[split_idx:][feature_cols]
    y_cal = y.iloc[split_idx:]

    print(f"\n  Final model: train={split_idx} rows, calibration={n - split_idx} rows")

    xgb_model = lgb_model = cb_model = None

    if HAS_XGB:
        print("  Training final XGBoost ...")
        xgb_model = xgb.XGBClassifier(**_get_xgb_params())
        xgb_model.fit(
            X_train_all, y_train_all,
            eval_set=[(X_cal, y_cal)],
            verbose=False,
        )
        # Assign OOF-fitted isotonic calibrator (no leak — X_cal used only for early stopping)
        if oof_calibrators and "xgb" in oof_calibrators:
            xgb_model._isotonic = oof_calibrators["xgb"]

    if HAS_LGB:
        print("  Training final LightGBM ...")
        lgb_model = lgb.LGBMClassifier(**_get_lgb_params())
        lgb_model.fit(
            X_train_all, y_train_all,
            eval_set=[(X_cal, y_cal)],
            callbacks=[lgb.early_stopping(20, verbose=False),
                       lgb.log_evaluation(-1)],
        )
        if oof_calibrators and "lgb" in oof_calibrators:
            lgb_model._isotonic = oof_calibrators["lgb"]

    if HAS_CB:
        print("  Training final CatBoost ...")
        cb_model = CatBoostClassifier(**_get_catboost_params())
        cb_model.fit(X_train_all, y_train_all, eval_set=(X_cal, y_cal))
        if oof_calibrators and "cb" in oof_calibrators:
            cb_model._isotonic = oof_calibrators["cb"]

    importance = aggregate_feature_importance(
        xgb_model, lgb_model, cb_model, feature_cols
    )

    return xgb_model, lgb_model, cb_model, importance



def save_model(
    xgb_model,
    lgb_model,
    cb_model,
    feature_cols: List[str],
    importance: Dict[str, float],
    cv_results: Dict[str, Any],
    ablation_results: Dict[str, Any],
    output_path: str,
):
    payload = {
        "xgb_model": xgb_model,
        "lgb_model": lgb_model,
        "cb_model": cb_model,
        "feature_columns": feature_cols,
        "feature_importance": importance,
        "cv_results": cv_results,
        "ablation_results": ablation_results,
        "trained_at": datetime.now().isoformat(),
        "version": "v2",
    }
    with open(output_path, "wb") as fh:
        pickle.dump(payload, fh)
    print(f"\n  Model saved to: {output_path}")



def print_top_features(importance: Dict[str, float], n: int = 25):
    print(f"\n{'='*70}")
    print(f"TOP {n} FEATURES BY MEAN IMPORTANCE (final model)")
    print(f"{'='*70}")
    sorted_imp = sorted(importance.items(), key=lambda x: x[1], reverse=True)
    print(f"  {'Rank':<6} {'Feature':<35} {'Importance':>12}")
    print("  " + "-" * 55)
    for rank, (feat, imp) in enumerate(sorted_imp[:n], 1):
        marker = " *" if feat in PHASE2_FEATURES else ""
        print(f"  {rank:<6} {feat:<35} {imp:>12.6f}{marker}")
    print("  (* = Phase 2 sectional feature)")


def print_cv_summary(cv_results: Dict[str, Any]):
    print(f"\n{'='*70}")
    print("WALK-FORWARD CV SUMMARY (full feature set, 14-day windows)")
    print(f"{'='*70}")
    print(f"  Total folds   : {cv_results['n_folds']}")
    print(f"  Mean AUC      : {cv_results['mean_auc']:.4f}  (std={cv_results['std_auc']:.4f})")
    print(f"  Mean Brier    : {cv_results['mean_brier']:.4f}")

    if cv_results["folds"]:
        aucs = [f["auc"] for f in cv_results["folds"] if not np.isnan(f["auc"])]
        if aucs:
            print(f"  AUC range     : [{min(aucs):.4f}, {max(aucs):.4f}]")



_PHASE4_NAN_FEATURES = ["dist_sectional_slope", "sectional_rank_at_distance"]
_PHASE4_ZERO_FEATURES = [
    "distance_direction_flag",
    "dist_sectional_recency_weighted",
    "sectional_result_divergence",
    "first_at_distance_sectional_quality",
]
_PHASE4_INTERACTION = ["step_up_x_dist_slope"]
_ALL_PHASE4_FEATURES = _PHASE4_NAN_FEATURES + _PHASE4_ZERO_FEATURES + _PHASE4_INTERACTION


def run_coverage_audit(X: pd.DataFrame, df_raw: pd.DataFrame) -> dict:
    """
    Print a coverage report for Phase 4 distance-change features and
    form_string availability. Returns a summary dict.
    """
    import json as _json

    print("\n" + "=" * 70)
    print("  *** AUDIT ONLY — NO MODEL TRAINED ***")
    print("  Phase 4 Distance-Change Feature Coverage Audit")
    print("=" * 70)

    total = len(X)
    dates = pd.to_datetime(df_raw["race_date"])
    date_min, date_max = dates.min(), dates.max()
    print(f"\n  Training rows : {total:,}")
    print(f"  Date range    : {date_min.date()} → {date_max.date()}")

    results = {"total_rows": total, "date_range": [str(date_min.date()), str(date_max.date())],
               "features": {}}

    print(f"\n  {'Feature':<42} {'Non-NaN':>8} {'Non-NaN%':>8} {'Non-Zero':>9} {'NonZero%':>9}  Status")
    print("  " + "-" * 90)

    for feat in _ALL_PHASE4_FEATURES:
        if feat not in X.columns:
            print(f"  {feat:<42} {'MISSING':>8}")
            results["features"][feat] = {"status": "MISSING"}
            continue

        col = X[feat]
        non_nan = int(col.notna().sum())
        non_nan_pct = non_nan / total * 100 if total else 0
        non_zero = int((col.fillna(0) != 0).sum())
        non_zero_pct = non_zero / total * 100 if total else 0

        if feat in _PHASE4_NAN_FEATURES:
            pct = non_nan_pct
        else:
            pct = non_zero_pct

        if pct > 40:
            status = "GREEN"
        elif pct >= 20:
            status = "YELLOW"
        else:
            status = "RED"

        print(f"  {feat:<42} {non_nan:>8,} {non_nan_pct:>7.1f}% {non_zero:>9,} {non_zero_pct:>8.1f}%  {status}")

        mask = col.notna() & (col != 0) if feat not in _PHASE4_NAN_FEATURES else col.notna()
        active_dates = dates[mask]
        date_range = [str(active_dates.min().date()), str(active_dates.max().date())] if len(active_dates) > 0 else None

        results["features"][feat] = {
            "non_nan": non_nan, "non_nan_pct": round(non_nan_pct, 1),
            "non_zero": non_zero, "non_zero_pct": round(non_zero_pct, 1),
            "status": status, "active_date_range": date_range,
        }

    print(f"\n  Phase 2 Sectional Coverage (for context):")
    for feat in PHASE2_FEATURES[:4]:
        if feat in X.columns:
            non_nan = int(X[feat].notna().sum())
            print(f"    {feat:<38} {non_nan:>8,} ({non_nan / total * 100:.1f}%)")

    print(f"\n  form_string Coverage (gates running style backfill):")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(form_string) as has_form,
                COUNT(CASE WHEN LENGTH(form_string) >= 3 THEN 1 END) as has_usable_form
            FROM race_results_history
            WHERE race_date >= %s AND race_date <= %s
        """, (str(date_min.date()), str(date_max.date())))
        row = cur.fetchone()
        cur.close()
        conn.close()

        total_rrh, has_form, has_usable = row
        form_pct = has_form / total_rrh * 100 if total_rrh else 0
        usable_pct = has_usable / total_rrh * 100 if total_rrh else 0
        form_gate = "PASS" if usable_pct >= 30 else "FAIL (< 30%)"

        print(f"    race_results_history rows : {total_rrh:,}")
        print(f"    Has form_string           : {has_form:,} ({form_pct:.1f}%)")
        print(f"    Usable (len >= 3)         : {has_usable:,} ({usable_pct:.1f}%)")
        print(f"    Task 2 gate               : {form_gate}")

        results["form_string"] = {
            "total": total_rrh, "has_form": has_form, "has_usable": has_usable,
            "usable_pct": round(usable_pct, 1), "gate": form_gate,
        }
    except Exception as e:
        print(f"    WARNING: Could not check form_string coverage: {e}")
        results["form_string"] = {"error": str(e)}

    print(f"\n  Decision Rules:")
    print(f"    GREEN  (>40%) → Include in retrain")
    print(f"    YELLOW (20-40%) → Include with NaN preservation, monitor")
    print(f"    RED    (<20%) → Exclude from FEATURE_COLUMNS (both retrain_v2.py AND ml_model.py)")

    red_features = [f for f, v in results["features"].items()
                    if v.get("status") == "RED"]
    if red_features:
        print(f"\n  ⚠ RED features to exclude: {red_features}")
    else:
        print(f"\n  All Phase 4 features at YELLOW or GREEN — proceed with retrain.")

    artifact_dir = os.path.join(SCRIPT_DIR, os.pardir, os.pardir, "data", "research")
    os.makedirs(artifact_dir, exist_ok=True)
    artifact_path = os.path.join(artifact_dir, "phase4_coverage_audit.json")
    try:
        with open(artifact_path, "w") as f:
            _json.dump(results, f, indent=2, default=str)
        print(f"\n  Artifact written: {os.path.relpath(artifact_path, SCRIPT_DIR)}")
    except Exception as e:
        print(f"  WARNING: Could not write artifact: {e}")

    print("\n" + "=" * 70)
    print("  *** AUDIT ONLY — NO MODEL TRAINED ***")
    print("=" * 70)

    return results



def parse_args():
    parser = argparse.ArgumentParser(description="Retrain the v2 ensemble model from training_view_v2.")
    parser.add_argument(
        "--output-path",
        default=OUTPUT_MODEL_PATH,
        help="Where to save the trained model artifact. Defaults to the canonical v2 model path.",
    )
    parser.add_argument(
        "--coverage-audit",
        action="store_true",
        help="Run Phase 4 feature coverage audit only (no training). Reports non-NaN/non-zero "
             "rates for distance-change sectional features and form_string coverage.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    output_model_path = os.path.abspath(args.output_path)
    t0 = time.time()
    print("=" * 70)
    print("  RACE-ANALYTICS  —  Phase 2 Re-Training  (retrain_v2.py)")
    print("=" * 70)
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Output : {output_model_path}")

    df_raw = load_training_data()

    n_total = len(df_raw)
    n_winners = int(df_raw["is_winner"].astype(int).sum())
    print(f"\n  Total rows   : {n_total:,}")
    print(f"  Winners      : {n_winners:,}  ({n_winners/n_total*100:.1f}%)")
    n_phase2 = int(df_raw["prior_z_200m"].notna().sum())
    print(f"  Rows w/ Phase 2 sectionals : {n_phase2}")

    print("\n[2] Building feature matrix ...")
    X = build_feature_matrix(df_raw)
    y = df_raw["is_winner"].astype(int).reset_index(drop=True)
    dates = pd.to_datetime(df_raw["race_date"]).reset_index(drop=True)
    X = X.reset_index(drop=True)

    feature_cols = [c for c in FEATURE_COLUMNS if c in X.columns]
    print(f"  Feature matrix shape : {X.shape}")
    print(f"  Features used        : {len(feature_cols)}")
    print(f"  Phase 2 present      : {[c for c in PHASE2_FEATURES if c in X.columns]}")
    non_zero = {
        c: int((X[c] != 0).sum()) if c not in PHASE2_FEATURES else int(X[c].notna().sum())
        for c in feature_cols
        if c in ["barrier_draw", "weight_kg", "market_odds", "field_size",
                  "distance", "class_level"] + PHASE2_FEATURES
    }
    print("\n  Key feature non-null / non-zero counts:")
    for k, v in non_zero.items():
        print(f"    {k:<35} {v:>8,}")

    if args.coverage_audit:
        audit = run_coverage_audit(X, df_raw)
        elapsed = time.time() - t0
        print(f"\n  Coverage audit completed in {elapsed:.1f}s")
        return

    print("\n[3] Walk-Forward Temporal Cross-Validation ...")
    print("  Parameters: min_train=60d, purge=14d, test=14d, step=14d")
    cv_results = run_walk_forward_cv(X, y, dates, feature_cols, label="CV-full",
                                     collect_oof_calibrators=True)
    print_cv_summary(cv_results)

    print("\n[4] Ablation study ...")
    ablation_results = run_ablation(X, y, dates)

    print("\n[5] Training final model on ALL data ...")
    oof_calibrators = cv_results.get("oof_calibrators", {})
    xgb_final, lgb_final, cb_final, final_importance = train_final_model(
        X, y, feature_cols, oof_calibrators=oof_calibrators
    )
    print_top_features(final_importance, n=25)

    print(f"\n[6] Saving model to {output_model_path} ...")
    save_model(
        xgb_final,
        lgb_final,
        cb_final,
        feature_cols,
        final_importance,
        cv_results,
        ablation_results,
        output_model_path,
    )

    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print("  TRAINING COMPLETE")
    print("=" * 70)
    print(f"  Elapsed       : {elapsed:.1f}s")
    print(f"  CV Mean AUC   : {cv_results['mean_auc']:.4f}  (std={cv_results['std_auc']:.4f})")
    print(f"  CV Mean Brier : {cv_results['mean_brier']:.4f}")
    abl = ablation_results
    print(f"  AUC delta (Phase 2 impact) : {abl['delta_auc']:+.4f}")
    print(f"    WITH Phase 2    : {abl['with_phase2']['mean_auc']:.4f}")
    print(f"    WITHOUT Phase 2 : {abl['without_phase2']['mean_auc']:.4f}")
    print(f"  Model saved to  : {output_model_path}")
    print()


if __name__ == "__main__":
    main()
