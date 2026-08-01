#!/usr/bin/env python3
"""
Lockstep guard for the task-12 feature pruning: retrain_v2.FEATURE_COLUMNS and
ml_model.RacingMLModel.FEATURE_COLUMNS must stay identical (set AND order), no
pruned name may reappear in either list, and NAN_PRESERVE lists must agree.

retrain_v2 guards its module level behind a DATABASE_URL check and an
unconditional psycopg2 import, so both are faked here — the test never touches
a database.
"""

import os
import sys
import types

import pytest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# The 45 features removed by the task-12 pruning
# (docs/research/TASK12_FEATURE_DECISIONS.md). Keeping this list inline (not
# imported from the doc) so the test fails if either list regresses.
DROPPED = {
    # 41 DEAD_BOTH_SIDES (importance 0.000000 in racing_ensemble_v2.pkl)
    "running_style_score", "pace_advantage", "is_high_pace_expected",
    "distance_style_match", "is_blinkers_first_time", "is_elite_jockey",
    "is_elite_trainer", "is_jockey_upgrade", "barrier_advantage",
    "track_bias_score", "is_steam_move", "is_drift", "odds_movement_pct",
    "last_start_market_diff", "avg_market_diff_3runs",
    "market_trend_shortening", "market_trend_drifting",
    "empirical_barrier_advantage", "predicted_settling_pos",
    "settling_percentile", "pace_pressure_index", "settling_difficulty",
    "settling_pace_interaction", "is_congested_speed", "speed_horse_ratio",
    "days_since_last_normalized", "prep_run_x_days_since",
    "run_spacing_quality", "empirical_freshness_score", "is_quick_backup",
    "is_long_absence", "distance_change_staleness", "class_x_spell",
    "steam_velocity", "drift_velocity", "late_move_indicator",
    "market_confidence", "relative_move", "smart_money_score",
    "is_insider_signal", "field_market_agreement",
    # 4 CONSTANT_AT_TRAIN (zero variance at train)
    "barrier_x_pace_inv", "is_first_time_stakes", "td_barrier_style_edge",
    "trainer_momentum_score",
}


@pytest.fixture(scope="module")
def feature_lists():
    # Satisfy retrain_v2's module-level DATABASE_URL check and psycopg2 import
    # without a database.
    fake = types.ModuleType("psycopg2")
    extras = types.ModuleType("psycopg2.extras")
    fake.extras = extras
    sys.modules.setdefault("psycopg2", fake)
    sys.modules.setdefault("psycopg2.extras", extras)
    os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost/unused")

    import retrain_v2
    from ml_model import RacingMLModel
    return (
        list(retrain_v2.FEATURE_COLUMNS),
        list(RacingMLModel.FEATURE_COLUMNS),
        set(getattr(retrain_v2, "NAN_PRESERVE_FEATURES", [])),
        set(getattr(RacingMLModel, "NAN_PRESERVE_FEATURES", [])),
    )


def test_feature_columns_set_equal(feature_lists):
    retrain_cols, model_cols, _, _ = feature_lists
    assert set(retrain_cols) == set(model_cols)


def test_feature_columns_order_equal(feature_lists):
    retrain_cols, model_cols, _, _ = feature_lists
    assert retrain_cols == model_cols


def test_no_dropped_name_in_either_list(feature_lists):
    retrain_cols, model_cols, _, _ = feature_lists
    assert not (DROPPED & set(retrain_cols))
    assert not (DROPPED & set(model_cols))


def test_nan_preserve_sets_equal(feature_lists):
    _, _, retrain_np, model_np = feature_lists
    # ml_model currently defines no NAN_PRESERVE list (nothing to prune there);
    # retrain's list must at minimum be free of dropped names, and if ml_model
    # ever gains one the two must agree.
    assert not (DROPPED & retrain_np)
    if model_np:
        assert retrain_np == model_np
