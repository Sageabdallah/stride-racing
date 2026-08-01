#!/usr/bin/env python3
"""Serve-feature liveness tests — the 15 trained-but-never-served features.

DB-free and artifact-free. Pins:

  1. Group-A parity — field_size_context / barrier_relevance_score /
     market_efficiency_flag equal the retrain_v2 one-liners (:642-652) on
     hand-computed values.
  2. Group-B parity — the pace trio (compute_race_pace) equals
     retrain_v2._compute_pace_features (:443-525 via
     _classify_running_style_from_form :387-441) on a synthetic field, with
     hand-computed expectations; a skip-guarded cross-check against the real
     retrain_v2 functions pins the duplication honest.
  3. Runtime liveness — STRIDE_SERVE_LIVE_FEATURES on: the feat dict contains
     all 15 keys; sectional keys are NaN (NEVER 0) with no prior data, real
     values when fetched.
  4. Flag-off byte-identity — with the flag off the feat dict is identical
     whether or not pace/sectionals kwargs are passed.
  5. NaN contract — prepare_features (STRIDE_SERVE_NAN_CONTRACT=1) passes the
     plumbed sectional NaNs through to the trees; zero-fill without it is why
     the two flags deploy together.
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

import serve_features
from serve_features import (
    LIVE_FEATURES,
    SECTIONAL_LIVE_FEATURES,
    build_feature_row,
    compute_race_pace,
)

NAN = float("nan")


def _model():
    """RacingMLModel without artifact IO — prepare_features only."""
    from ml_model import RacingMLModel
    return RacingMLModel.__new__(RacingMLModel)


@pytest.fixture(scope="module")
def retrain_v2_mod():
    """Lazy retrain_v2 import (psycopg2 stubbed) — skips gracefully."""
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


def _bare_runner():
    return {"horse": "MISSING_EVERYTHING", "weight": "58kg", "draw": 7}


# ---------------------------------------------------------------------------
# Group A — one-line formulas (retrain_v2.py:642-652), hand-computed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field_size,expected", [
    (4, 0.25), (8, 0.5), (16, 1.0), (20, 1.0),      # clip at 1.0
    (None, 0.625), (0, 0.625),                       # (or 10)/16
])
def test_field_size_context_parity(field_size, expected):
    assert serve_features.field_size_context_value(field_size) == pytest.approx(expected)


@pytest.mark.parametrize("distance,expected", [
    (1000, 1.0), (1200, 1.0), (1201, 0.7), (1600, 0.7),
    (1601, 0.4), (2000, 0.4), (2001, 0.2), (3200, 0.2),
    (None, 0.7),                                     # fillna(1400) -> 0.7
])
def test_barrier_relevance_parity(distance, expected):
    assert serve_features.barrier_relevance_value(distance) == pytest.approx(expected)


@pytest.mark.parametrize("class_level,expected", [
    (80, 1.0), (100, 1.0), (60, 0.7), (79, 0.7),
    (50, 0.5), (59, 0.5), (31, 0.4), (45, 0.4),
    (30, 0.2), (10, 0.2),
    (None, 0.5), (0, 0.5),                           # missing -> 50 -> 0.5
])
def test_market_efficiency_parity(class_level, expected):
    assert serve_features.market_efficiency_value(class_level) == pytest.approx(expected)


def test_group_a_matches_retrain_formulas(retrain_v2_mod):
    """Cross-check the mirrored one-liners against retrain_v2's own lambdas
    applied to a synthetic frame (the same expressions as :641-652)."""
    df = pd.DataFrame({
        "distance": [1000, 1400, 1800, 2400, np.nan],
        "field_size": [8, 16, 20, np.nan, 12],
        "class_level": [85, 65, 45, 25, np.nan],
    })
    dist_col = df["distance"].fillna(1400)
    train_brs = dist_col.apply(
        lambda d: 1.0 if d <= 1200 else 0.7 if d <= 1600 else 0.4 if d <= 2000 else 0.2)
    train_fsc = (df["field_size"].fillna(10) / 16.0).clip(upper=1.0)
    cl = df["class_level"].fillna(50)
    train_mef = cl.apply(
        lambda c: 1.0 if c >= 80 else 0.7 if c >= 60 else 0.5 if c >= 50
        else 0.2 if c <= 30 else 0.4)
    for i in range(len(df)):
        d = None if pd.isna(df["distance"][i]) else df["distance"][i]
        fs = None if pd.isna(df["field_size"][i]) else df["field_size"][i]
        c = None if pd.isna(df["class_level"][i]) else df["class_level"][i]
        assert serve_features.barrier_relevance_value(d) == pytest.approx(train_brs[i])
        assert serve_features.field_size_context_value(fs) == pytest.approx(train_fsc[i])
        assert serve_features.market_efficiency_value(c) == pytest.approx(train_mef[i])


# ---------------------------------------------------------------------------
# Group B — pace trio (retrain_v2.py:636-639 via :387-441, :443-525)
# ---------------------------------------------------------------------------

def test_style_classifier_hand_computed():
    c = serve_features._classify_running_style_from_form
    S = serve_features
    assert c("11111") == S._STYLE_LEADER          # 5 wins from front
    assert c("112") == S._STYLE_LEADER            # 2 wins, avg 1.33 <= 1.5
    assert c("22") == S._STYLE_ON_PACE            # avg 2.0
    assert c("34") == S._STYLE_MIDFIELD           # avg 3.5
    assert c("56") == S._STYLE_OFF_PACE           # avg 5.5
    assert c("78") == S._STYLE_BACKMARKER         # avg 7.5
    assert c("865-5") == S._STYLE_OFF_PACE        # dashes stripped, avg 6.0
    assert c("10") == S._STYLE_OFF_PACE         # 0 reads as 10 -> [1,10] avg 5.5
    assert c("x1f2") == S._STYLE_ON_PACE          # x/f skipped -> [1,2] avg 1.5
    # Barrier fallback: no usable form.
    assert c("", barrier=1, field_size=4) == S._STYLE_MIDFIELD   # 1 <= 4*0.25
    assert c("", barrier=8, field_size=8) == S._STYLE_OFF_PACE   # 8 > 2.0
    assert c(None, barrier=None, field_size=None) == S._STYLE_MIDFIELD


def test_compute_race_pace_high_pressure_hand_computed():
    """4-runner field, pps = 2/4 = 0.5 >= 0.4 -> (leader -0.10, closer +0.12)."""
    runners = [
        {"form": "11111"},   # LEADER
        {"form": "22"},      # ON_PACE
        {"form": "34"},      # MIDFIELD
        {"form": "78"},      # BACKMARKER
    ]
    out = compute_race_pace(runners)
    assert len(out) == 4
    for d in out:
        assert d["pace_pressure_score"] == pytest.approx(0.5)
    assert out[0]["leader_advantage"] == pytest.approx(-0.10)   # leader
    assert out[0]["closer_advantage"] == pytest.approx(0.0)
    assert out[1]["leader_advantage"] == pytest.approx(-0.10)   # on-pace
    assert out[2]["leader_advantage"] == pytest.approx(0.0)     # midfield
    assert out[2]["closer_advantage"] == pytest.approx(0.12 * 0.6)  # 0.072
    assert out[3]["closer_advantage"] == pytest.approx(0.12)    # backmarker


def test_compute_race_pace_low_pressure_hand_computed():
    """5-runner field, pps = 0/5 = 0.0 <= 0.15 -> (leader +0.15, closer -0.05)."""
    runners = [
        {"form": "89"},                          # BACKMARKER
        {"form": "56"},                          # OFF_PACE
        {"form": "45"},                          # MIDFIELD
        {"form": "", "barrier": 5, "field_size": 5},   # barrier fallback OFF_PACE
        {"form": "77"},                          # BACKMARKER
    ]
    out = compute_race_pace(runners)
    for d in out:
        assert d["pace_pressure_score"] == pytest.approx(0.0)
    assert out[0]["closer_advantage"] == pytest.approx(-0.05)   # backmarker
    assert out[1]["closer_advantage"] == pytest.approx(-0.05)   # off-pace
    assert out[2]["leader_advantage"] == pytest.approx(0.15 * 0.4)  # midfield 0.06
    assert out[3]["closer_advantage"] == pytest.approx(-0.05)   # barrier off-pace


def test_compute_race_pace_neutral_and_empty():
    # 1 on-pace of 4 -> pps 0.25 -> neutral (0, 0).
    out = compute_race_pace([{"form": "11"}, {"form": "56"},
                             {"form": "78"}, {"form": "89"}])
    assert out[0]["pace_pressure_score"] == pytest.approx(0.25)
    assert all(d["leader_advantage"] == 0.0 and d["closer_advantage"] == 0.0
               for d in out)
    # Unknown field -> training no-field default (0.5 / 0 / 0).
    assert compute_race_pace([]) == []


def test_pace_parity_against_retrain_v2(retrain_v2_mod):
    """The duplicated classifier + per-race computation must equal
    retrain_v2's own functions on a synthetic field (single source pinned by
    comparison, not import)."""
    rt = retrain_v2_mod
    battery = [
        ("11111", None, None), ("22", None, None), ("34", None, None),
        ("78", None, None), ("865-5", None, None), ("x1f2", None, None),
        ("", 1, 4), ("", 8, 8), (None, None, None), ("0", None, None),
    ]
    for form, barrier, fs in battery:
        assert serve_features._classify_running_style_from_form(
            form, barrier=barrier, field_size=fs
        ) == rt._classify_running_style_from_form(form, barrier=barrier, field_size=fs)

    field = [
        {"form_string": "11111", "barrier": 1, "field_size": 4},
        {"form_string": "22", "barrier": 2, "field_size": 4},
        {"form_string": "34", "barrier": 3, "field_size": 4},
        {"form_string": "78", "barrier": 4, "field_size": 4},
    ]
    df = pd.DataFrame({
        "race_date": ["2026-01-01"] * 4,
        "track": ["TEST"] * 4,
        "race_number": [1] * 4,
        "form_string": [r["form_string"] for r in field],
        "barrier": [r["barrier"] for r in field],
        "field_size": [4] * 4,
    })
    df.attrs["_field_composition"] = {("2026-01-01", "TEST", 1): field}
    train = rt._compute_pace_features(df)
    serve = compute_race_pace(field)
    for i in range(4):
        assert serve[i]["pace_pressure_score"] == pytest.approx(
            train["pace_pressure_score"].iloc[i])
        assert serve[i]["leader_advantage"] == pytest.approx(
            train["leader_advantage"].iloc[i])
        assert serve[i]["closer_advantage"] == pytest.approx(
            train["closer_advantage"].iloc[i])


# ---------------------------------------------------------------------------
# Runtime liveness — flag on: all 15 keys, NaN contract on sectionals
# ---------------------------------------------------------------------------

def test_liveness_flag_on_all_15_keys(monkeypatch):
    monkeypatch.setenv("STRIDE_SERVE_LIVE_FEATURES", "1")
    pace = {"pace_pressure_score": 0.5, "leader_advantage": -0.10,
            "closer_advantage": 0.0}
    sectionals = {"z_200m": 1.4, "z_400m": 0.6, "svi": 1.08,
                  "rsi": None}  # NULL in the fetched row -> NaN, not 0
    feat = build_feature_row(
        _bare_runner(), market_odds=6.0, distance_m=1400, field_size=8,
        pace=pace, sectionals=sectionals)
    for k in LIVE_FEATURES:
        assert k in feat, f"{k} missing from plumbed feat dict"
    assert len(LIVE_FEATURES) == 14  # the 15th ZERO_AT_SERVE feature,
    # ground_suitability, is zero-importance dead weight (FEATURE_PROVENANCE)
    # and deliberately not plumbed
    # Plumbed values.
    assert feat["z_200m"] == pytest.approx(1.4)
    assert feat["svi"] == pytest.approx(1.08)
    assert feat["leader_advantage"] == pytest.approx(-0.10)
    assert feat["field_size_context"] == pytest.approx(0.5)
    assert feat["barrier_relevance_score"] == pytest.approx(0.7)
    assert feat["market_efficiency_flag"] == pytest.approx(0.5)  # no class_level
    # NaN contract: unknown sectionals are NaN, NEVER 0.
    for k in SECTIONAL_LIVE_FEATURES:
        if k in ("z_200m", "z_400m", "svi"):
            continue
        assert math.isnan(feat[k]), f"{k} should be NaN, got {feat[k]}"
        assert feat[k] != 0.0


def test_liveness_flag_on_no_prior_data_all_nan(monkeypatch):
    monkeypatch.setenv("STRIDE_SERVE_LIVE_FEATURES", "1")
    feat = build_feature_row(
        _bare_runner(), market_odds=6.0, distance_m=1400, field_size=8,
        pace=None, sectionals=None)
    for k in SECTIONAL_LIVE_FEATURES:
        assert math.isnan(feat[k]), f"{k} should be NaN with no fetch, got {feat[k]}"
    # No pace computed -> training no-field default.
    assert feat["pace_pressure_score"] == 0.5
    assert feat["leader_advantage"] == 0.0
    assert feat["closer_advantage"] == 0.0


def test_liveness_prepare_features_passes_nan_to_trees(monkeypatch):
    """End-to-end NaN contract: plumbed NaN sectionals survive
    prepare_features only under STRIDE_SERVE_NAN_CONTRACT — the two flags
    deploy together; zero-fill without it is the documented legacy defect."""
    monkeypatch.setenv("STRIDE_SERVE_LIVE_FEATURES", "1")
    monkeypatch.setenv("STRIDE_SERVE_NAN_CONTRACT", "1")
    feat = build_feature_row(
        _bare_runner(), market_odds=6.0, distance_m=1400, field_size=8,
        pace=None, sectionals={"z_200m": 1.4})
    X = _model().prepare_features(pd.DataFrame([feat])).iloc[0]
    assert X["z_200m"] == pytest.approx(1.4)
    for k in SECTIONAL_LIVE_FEATURES:
        if k == "z_200m":
            continue
        assert math.isnan(X[k]), f"{k} zero-filled despite NaN contract"


# ---------------------------------------------------------------------------
# Flag-off byte-identity
# ---------------------------------------------------------------------------

def _dicts_identical(a, b):
    if set(a) != set(b):
        return False
    for k in a:
        va, vb = a[k], b[k]
        if isinstance(va, float) and isinstance(vb, float) \
                and math.isnan(va) and math.isnan(vb):
            continue
        if va != vb:
            return False
    return True


def test_flag_off_byte_identical(monkeypatch):
    monkeypatch.delenv("STRIDE_SERVE_LIVE_FEATURES", raising=False)
    monkeypatch.delenv("STRIDE_SERVE_LIVE_FEATURES_SHADOW", raising=False)
    runner = dict(_bare_runner(), form="11111", class_level=65)
    kwargs = dict(market_odds=6.0, distance_m=1400, field_size=8)
    legacy = build_feature_row(runner, **kwargs)
    with_extras = build_feature_row(
        runner, pace={"pace_pressure_score": 0.9, "leader_advantage": -0.10,
                      "closer_advantage": 0.12},
        sectionals={"z_200m": 9.9, "rsi": 9.9}, **kwargs)
    # The pace/sectionals kwargs are IGNORED with the flag off.
    assert _dicts_identical(legacy, with_extras)
    # And the legacy defaults still hold (0.5 pace/context, runner-sourced).
    assert legacy["pace_pressure_score"] == 0.5
    assert legacy["leader_advantage"] == 0.5
    assert legacy["field_size_context"] == 0.5
    assert legacy["market_efficiency_flag"] == 0.5


def test_force_live_builds_plumbed_dict_with_flag_off(monkeypatch):
    """The shadow path: force_live=True produces the plumbed dict while the
    env flag stays off (legacy remains the published result)."""
    monkeypatch.delenv("STRIDE_SERVE_LIVE_FEATURES", raising=False)
    runner = dict(_bare_runner(), form="11111")
    feat = build_feature_row(
        runner, market_odds=6.0, distance_m=1200, field_size=8,
        pace={"pace_pressure_score": 0.5, "leader_advantage": -0.10,
              "closer_advantage": 0.0},
        sectionals={"z_200m": 1.4}, force_live=True)
    assert feat["z_200m"] == pytest.approx(1.4)
    assert feat["leader_advantage"] == pytest.approx(-0.10)
    assert feat["barrier_relevance_score"] == pytest.approx(1.0)
    assert math.isnan(feat["rsi"])
