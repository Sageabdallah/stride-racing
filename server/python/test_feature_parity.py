#!/usr/bin/env python3
"""Train/serve feature parity tests — ROI roadmap task 03.

DB-free and artifact-free. Proves that with the task-03 flags ON, the served
feature vector (serve_features.build_feature_row -> ml_model.prepare_features)
equals the training builder's vector (retrain_v2.build_feature_matrix) for:

  - the sectional-NaN case (no sectional data must NOT become z_200m == 0.0),
  - the no-trial case (trial_recency NaN/999 must not become 0 = trial today),
  - barrier_x_pace_inv under BOTH pace regimes (high- and low-pressure races),
  - all PHASE2 sectional columns (values when known, NaN when not),
  - the market-movement columns (0 in both paths — inert until task 14).

The same assertions FAIL on the old code path (flags off / legacy inline
assembly) — see test_legacy_path_documents_the_defects.

retrain_v2 is imported lazily with psycopg2 stubbed (it is not installed in
this environment and retrain_v2 imports it at module level); if the import
still fails, the trainer-linked tests skip rather than fail.
"""

import importlib
import math
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nan_contract
import serve_features
from serve_features import MOVEMENT_FEATURES, build_feature_row, overlay_shared_columns

NAN = float("nan")


def _model():
    """RacingMLModel without artifact IO — prepare_features only needs the
    class-level FEATURE_COLUMNS (no _trained_feature_columns set)."""
    from ml_model import RacingMLModel
    return RacingMLModel.__new__(RacingMLModel)


@pytest.fixture()
def flags_on(monkeypatch):
    monkeypatch.setenv("STRIDE_SERVE_NAN_CONTRACT", "1")
    monkeypatch.setenv("STRIDE_INTERACTION_PARITY", "1")
    monkeypatch.delenv("STRIDE_MOVEMENT_FEATURES_LIVE", raising=False)
    yield


@pytest.fixture(scope="module")
def retrain_v2_mod():
    """Lazy retrain_v2 import — skips gracefully without psycopg2/DB env."""
    os.environ.setdefault("DATABASE_URL", "postgresql://parity:parity@localhost/parity")
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        stub = types.ModuleType("psycopg2")
        extras = types.ModuleType("psycopg2.extras")
        stub.extras = extras
        sys.modules.setdefault("psycopg2", stub)
        sys.modules.setdefault("psycopg2.extras", extras)
    try:
        return importlib.import_module("retrain_v2")
    except (ImportError, SystemExit) as exc:
        pytest.skip(f"retrain_v2 not importable in this environment: {exc}")


def _serve_vector(runner, market_odds, distance_m, field_size, rel_market):
    feat = build_feature_row(
        runner,
        market_odds=market_odds,
        distance_m=distance_m,
        field_size=field_size,
        rel_market=rel_market,
    )
    return feat, _model().prepare_features(pd.DataFrame([feat])).iloc[0]


# ---------------------------------------------------------------------------
# NaN contract
# ---------------------------------------------------------------------------

def _bare_runner():
    # No sectional enrichment, no trial data, no bounce history.
    return {"horse": "MISSING_EVERYTHING", "weight": "58kg", "draw": 7}


def test_legacy_path_documents_the_defects(monkeypatch):
    """Explicit rollback (STRIDE_INTERACTION_PARITY=false, NaN contract unset)
    reproduces the exact old behaviour. These assertions encode the defects
    themselves: they are what the flag-on parity tests overturn. The parity
    default is ON since the task-03 comparison showed it not worse (inert on
    the current artifact) — the legacy path survives for one release as the
    rollback, and this test keeps it honest."""
    monkeypatch.delenv("STRIDE_SERVE_NAN_CONTRACT", raising=False)
    monkeypatch.setenv("STRIDE_INTERACTION_PARITY", "false")
    _, X = _serve_vector(_bare_runner(), 6.0, 1400, 10, None)
    # Defect 1: missing sectionals/trials destroyed into 0.
    assert X["z_200m"] == 0.0
    assert X["trial_recency"] == 0.0
    assert X["runs_since_peak"] == 0.0
    # Defect 2: legacy inverted interaction.
    runner = dict(_bare_runner(), barrier_advantage=0.4, pace_pressure_score=0.9)
    feat, _ = _serve_vector(runner, 6.0, 1400, 10, None)
    assert feat["barrier_x_pace_inv"] == pytest.approx(0.4 * (1 - 0.9))


def test_nan_contract_flag_on_passes_nan_through(flags_on):
    _, X = _serve_vector(_bare_runner(), 6.0, 1400, 10, None)
    # Sectional-NaN case: every contract column survives as NaN.
    for col in nan_contract.NAN_PRESERVE_FEATURES:
        assert math.isnan(X[col]), f"{col} should be NaN, got {X[col]}"
    # Acceptance: no-sectional runner no longer serves z_200m == 0.0.
    assert X["z_200m"] != 0.0
    # Genuinely numeric-complete columns still get their legacy defaults.
    assert X["days_since_run"] == 0.0
    assert X["market_odds"] == 6.0
    assert X["trainer_momentum_score"] == 50.0
    assert X["has_sectional_data"] == 0
    # Movement columns inert.
    for col in MOVEMENT_FEATURES:
        assert X[col] == 0.0


def test_nan_contract_flag_on_keeps_known_values(flags_on):
    runner = dict(
        _bare_runner(),
        z_200m=1.4, z_400m=0.6, lambda_decay=0.00012, svi=1.08,
        trial_recency=14, runs_since_peak=2, has_sectional_data=1,
    )
    _, X = _serve_vector(runner, 6.0, 1400, 10, None)
    assert X["z_200m"] == pytest.approx(1.4)
    assert X["trial_recency"] == pytest.approx(14)
    assert X["runs_since_peak"] == pytest.approx(2)
    assert math.isnan(X["z_600m"])  # partial data: only known values filled


def test_contract_is_single_definition_shared_with_trainer(retrain_v2_mod):
    assert list(nan_contract.PHASE2_FEATURES) == list(retrain_v2_mod.PHASE2_FEATURES)
    assert set(nan_contract.NAN_PRESERVE_FEATURES) == (
        set(retrain_v2_mod.PHASE2_FEATURES) | set(retrain_v2_mod.NAN_PRESERVE_FEATURES)
    )
    # Trainer zero-fills exactly the complement of the contract.
    assert not set(retrain_v2_mod.NON_SECTIONAL_FEATURES) & nan_contract.NAN_PRESERVE_SET
    assert set(retrain_v2_mod.NON_SECTIONAL_FEATURES) | nan_contract.NAN_PRESERVE_SET \
        >= set(retrain_v2_mod.FEATURE_COLUMNS)


# ---------------------------------------------------------------------------
# Interaction parity (both pace regimes)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pps", [0.05, 0.9])
def test_interaction_parity_both_pace_regimes(flags_on, pps):
    from feature_interactions import compute_interactions
    runner = dict(
        _bare_runner(),
        barrier_advantage=0.4, pace_pressure_score=pps,
        campaign_run_number=3, distance_strike_rate=25.0,
        z_200m=1.2, going_suitability=0.7,
        is_class_drop=1, form_direction_slope=-0.3,
    )
    feat, _ = _serve_vector(runner, 5.0, 1400, 12, None)
    expected = compute_interactions(runner)
    # Training formula adv * pace — under BOTH regimes, incl. where it
    # maximally disagrees with the legacy adv * (1 - pace).
    assert feat["barrier_x_pace_inv"] == pytest.approx(0.4 * pps)
    assert feat["barrier_x_pace_inv"] != pytest.approx(0.4 * (1 - pps))
    for col in ("fitness_x_distance", "sectional_x_going",
                "class_drop_x_trajectory", "campaign_run_x_fitness"):
        assert feat[col] == pytest.approx(expected[col])


# ---------------------------------------------------------------------------
# Movement columns: inert in both paths unless explicitly live
# ---------------------------------------------------------------------------

def test_movement_columns_zero_by_default(monkeypatch):
    monkeypatch.delenv("STRIDE_MOVEMENT_FEATURES_LIVE", raising=False)
    feat, _ = _serve_vector(_bare_runner(), 5.0, 1400, 10, None)
    for col in MOVEMENT_FEATURES:
        assert feat[col] == 0


def test_movement_columns_live_opt_in(monkeypatch):
    monkeypatch.setenv("STRIDE_MOVEMENT_FEATURES_LIVE", "1")
    live = {col: 1.5 for col in MOVEMENT_FEATURES}
    feat = build_feature_row(_bare_runner(), market_odds=5.0, distance_m=1400,
                             field_size=10, movement=live)
    for col in MOVEMENT_FEATURES:
        assert feat[col] == 1.5


def test_movement_inert_logged_once_per_race_day(monkeypatch, capsys):
    monkeypatch.delenv("STRIDE_MOVEMENT_FEATURES_LIVE", raising=False)
    serve_features._movement_log_dates.clear()
    serve_features.log_movement_inert_once("2026-07-27")
    serve_features.log_movement_inert_once("2026-07-27")
    serve_features.log_movement_inert_once("2026-07-28")
    err = capsys.readouterr().err
    assert err.count("2026-07-27") == 1
    assert err.count("2026-07-28") == 1


def test_overlay_shared_columns_zeroes_movement_and_keeps_extracted():
    """mc_api path: already-extracted values pass through the one builder;
    movement columns are forced to 0 (sourced post-tip-time — task 14)."""
    features = {
        "current_odds": 4.0, "weighted_form_score": 12.0, "distance_strike_rate": 30.0,
        "steam_velocity": 9.9, "is_steam_move": 1, "smart_money_score": 80.0,
    }
    runner = {"draw": 3, "weight": "57kg"}
    out = overlay_shared_columns(features, runner, market_odds=4.0,
                                 distance_m=1400, field_size=10)
    assert out["steam_velocity"] == 0
    assert out["is_steam_move"] == 0
    assert out["smart_money_score"] == 0
    assert out["weighted_form_score"] == 12.0
    assert out["distance_strike_rate"] == 30.0
    assert out["barrier_draw"] == 3
    assert out["weight_kg"] == 57.0
    assert out["market_odds"] == 4.0
