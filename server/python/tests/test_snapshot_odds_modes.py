#!/usr/bin/env python3
"""DB-free tests for task 12P-4: STRIDE_TRAIN_ODDS_SOURCE modes in
retrain_v2.build_feature_matrix, and the odds_ablation arm construction.

Covers: legacy byte-identity (pinned against hand-computed old-rule values),
snapshot filtering with a poisoned-SP proof that SP cannot reach
_effective_odds, hybrid NaN semantics, ablation arm construction (C's row set
== B's), the coverage-floor refusal, and the band-ROI/bootstrap wiring.
"""

import os
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SERVER_PYTHON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_PYTHON))


@pytest.fixture(scope="module")
def retrain():
    # retrain_v2 guards its module level behind a DATABASE_URL check and an
    # unconditional psycopg2 import; both are faked. No database is touched.
    fake = types.ModuleType("psycopg2")
    extras = types.ModuleType("psycopg2.extras")
    fake.extras = extras
    sys.modules.setdefault("psycopg2", fake)
    sys.modules.setdefault("psycopg2.extras", extras)
    os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost/unused")
    import retrain_v2
    return retrain_v2


@pytest.fixture(scope="module")
def ablation():
    import odds_ablation
    return odds_ablation


def _training_frame():
    """Minimal frame for build_feature_matrix: one race, three runners."""
    return pd.DataFrame([
        # runner 0: sparse market_odds present -> legacy prefers it
        {"race_date": "2026-03-14", "track": "Flemington", "race_number": 5,
         "barrier": 3, "distance_m": 1400, "weight_kg": 58.0, "field_size": 8,
         "class_level": 60, "weighted_form_score": 6.5,
         "ground_suitability": 0.6, "distance_strike_rate": 0.25,
         "market_odds": 4.0, "sp_odds": 88.0, "tip_time_odds": 3.5,
         "odds_source": "snapshot", "is_winner": 1},
        # runner 1: market_odds missing -> legacy falls back to sp_odds
        {"race_date": "2026-03-14", "track": "Flemington", "race_number": 5,
         "barrier": 7, "distance_m": 1400, "weight_kg": 56.5, "field_size": 8,
         "class_level": 60, "weighted_form_score": 5.0,
         "ground_suitability": 0.5, "distance_strike_rate": 0.10,
         "market_odds": None, "sp_odds": 99.0, "tip_time_odds": 6.0,
         "odds_source": "snapshot", "is_winner": 0},
        # runner 2: no snapshot -> only SP knows its price
        {"race_date": "2026-03-14", "track": "Flemington", "race_number": 5,
         "barrier": 1, "distance_m": 1400, "weight_kg": 59.0, "field_size": 8,
         "class_level": 60, "weighted_form_score": 4.0,
         "ground_suitability": 0.4, "distance_strike_rate": 0.05,
         "market_odds": None, "sp_odds": 12.0, "tip_time_odds": None,
         "odds_source": "sp_fallback", "is_winner": 0},
    ])


# ---------------------------------------------------------------- retrain modes

def test_legacy_mode_byte_identical(retrain, monkeypatch):
    """Legacy (default) mode reproduces the old SP-filled rules exactly."""
    monkeypatch.delenv("STRIDE_TRAIN_ODDS_SOURCE", raising=False)
    df = _training_frame()
    X = retrain.build_feature_matrix(df.copy())

    # Old rules, computed by hand: market_odds.fillna(sp_odds)
    expected = [4.0, 99.0, 12.0]
    assert list(X["market_odds"]) == expected

    # The relative-market trio is derived from those same odds (overround-
    # corrected implied %): implied = 1/o, normalised across the field.
    inv = np.array([1 / 4.0, 1 / 99.0, 1 / 12.0])
    exp_fip = inv / inv.sum() * 100.0
    # compute_field_relative_market rounds to 4dp
    np.testing.assert_allclose(X["fair_implied_prob"].values, exp_fip, atol=1e-4)
    assert list(X["odds_rank"]) == [1.0, 3.0, 2.0]

    # Non-odds features are unaffected by the mode plumbing.
    assert list(X["barrier_draw"]) == [3.0, 7.0, 1.0]


def test_snapshot_filters_and_never_falls_back_to_sp(retrain, monkeypatch, capsys):
    monkeypatch.setenv("STRIDE_TRAIN_ODDS_SOURCE", "snapshot")
    df = _training_frame()

    kept = retrain.filter_snapshot_rows(df)
    assert len(kept) == 2
    assert "kept 2/3 rows" in capsys.readouterr().out

    # sp_odds is poisoned (88/99) on the kept rows; tip_time_odds is 3.5/6.0.
    # If any SP value can reach market_odds the assertion below fails.
    X = retrain.build_feature_matrix(kept.copy())
    assert list(X["market_odds"]) == [3.5, 6.0]
    assert 88.0 not in X["market_odds"].values
    assert 99.0 not in X["market_odds"].values
    # And the derivatives come from tip-time odds: 3.5 fav over 6.0.
    assert list(X["odds_rank"]) == [1.0, 2.0]


def test_snapshot_refuses_empty_and_missing_column(retrain, monkeypatch, capsys):
    monkeypatch.setenv("STRIDE_TRAIN_ODDS_SOURCE", "snapshot")

    with pytest.raises(SystemExit) as e:
        retrain.filter_snapshot_rows(pd.DataFrame({"x": [1]}))  # no odds_source
    assert e.value.code == 2

    df = _training_frame()
    df["odds_source"] = "sp_fallback"
    with pytest.raises(SystemExit) as e:
        retrain.filter_snapshot_rows(df)
    assert e.value.code == 2
    assert "refusing to train" in capsys.readouterr().out


def test_hybrid_nan_semantics(retrain, monkeypatch):
    monkeypatch.setenv("STRIDE_TRAIN_ODDS_SOURCE", "snapshot_hybrid")
    df = _training_frame()
    X = retrain.build_feature_matrix(df.copy())

    # All rows kept; snapshot rows carry tip-time odds.
    assert len(X) == 3
    assert X["market_odds"].iloc[0] == 3.5
    assert X["market_odds"].iloc[1] == 6.0

    # The sp_fallback row is missing-odds: NaN (never 0, never SP 12.0),
    # and the relative-market trio is NaN there too.
    assert np.isnan(X["market_odds"].iloc[2])
    assert np.isnan(X["fair_implied_prob"].iloc[2])
    assert np.isnan(X["odds_rank"].iloc[2])
    assert np.isnan(X["odds_rank_pct"].iloc[2])

    # Other NON_SECTIONAL features are still 0-filled in hybrid mode — the
    # NaN carve-out is exactly market_odds + the trio.
    assert X["campaign_run_number"].iloc[2] == 0


def test_unknown_mode_falls_back_to_legacy(retrain, monkeypatch, capsys):
    monkeypatch.setenv("STRIDE_TRAIN_ODDS_SOURCE", "nonsense")
    assert retrain._train_odds_source() == "legacy"
    assert "unknown STRIDE_TRAIN_ODDS_SOURCE" in capsys.readouterr().out


# ---------------------------------------------------------------- ablation arms

def _backtest_frame():
    """Backtest-shaped frame: 4 rows, 2 with snapshot odds (SP poisoned)."""
    return pd.DataFrame([
        {"race_date": "2026-06-01", "track": "Flemington", "race_number": 1,
         "horse_name": "A", "won": 1, "market_odds": None, "starting_price": 50.0,
         "tip_time_odds": 4.0, "odds_source": "snapshot"},
        {"race_date": "2026-06-01", "track": "Flemington", "race_number": 1,
         "horse_name": "B", "won": 0, "market_odds": 7.0, "starting_price": 60.0,
         "tip_time_odds": 6.5, "odds_source": "snapshot"},
        {"race_date": "2026-06-01", "track": "Flemington", "race_number": 1,
         "horse_name": "C", "won": 0, "market_odds": None, "starting_price": 9.0,
         "tip_time_odds": None, "odds_source": "sp_fallback"},
        {"race_date": "2026-06-02", "track": "Flemington", "race_number": 2,
         "horse_name": "D", "won": 0, "market_odds": 3.0, "starting_price": 70.0,
         "tip_time_odds": None, "odds_source": "racecard"},
    ])


def test_ablation_arm_construction(ablation):
    arms = ablation.build_ablation_arms(_backtest_frame())
    assert set(arms) == {"legacy-full", "snapshot", "legacy-restricted"}

    a, b, c = arms["legacy-full"], arms["snapshot"], arms["legacy-restricted"]

    # A: all rows, legacy odds = market_odds.fillna(starting_price).
    assert len(a) == 4
    assert list(a["market_odds"]) == [50.0, 7.0, 9.0, 3.0]

    # B: snapshot rows only, tip-time odds outright — poisoned SP cannot reach.
    assert len(b) == 2
    assert list(b["market_odds"]) == [4.0, 6.5]
    assert 50.0 not in b["market_odds"].values
    assert 60.0 not in b["market_odds"].values

    # C: EXACTLY B's row set, legacy odds restored — the control.
    assert list(c.index) == list(b.index)
    assert list(c["horse_name"]) == list(b["horse_name"])
    assert list(c["market_odds"]) == [50.0, 7.0]


def test_refusal_below_floor(ablation, capsys):
    df = _backtest_frame()  # 2 snapshot rows
    with pytest.raises(SystemExit) as e:
        ablation.refuse_below_floor(df, min_rows=2000)
    assert e.value.code == 2
    assert "REFUSAL" in capsys.readouterr().err

    assert ablation.refuse_below_floor(df, min_rows=2) == 2


def test_band_roi_uses_harness_bootstrap(ablation):
    # 100 synthetic bets: proba >= band on all, 25% strike at avg odds 5.0.
    rng = np.random.default_rng(7)
    n = 100
    y = (rng.random(n) < 0.25).astype(int)
    p = np.full(n, 0.20)
    o = np.full(n, 5.0)
    captured = [(y[:50], p[:50], o[:50], 0.0), (y[50:], p[50:], o[50:], 0.0)]

    out = ablation.band_roi_from_bets(captured, band=0.10,
                                      bootstrap_n=200, bootstrap_seed=42)
    assert out["band"] == 0.10
    assert out["n_bets"] == 100
    # Cross-check ROI against the harness's own pnl math.
    import walk_forward_backtest as wfb
    pnl = wfb.compute_bet_pnl(y, o, stake=100.0, commission_rate=0.0)
    assert out["net_roi_pct"] == round(float(pnl.sum()) / (100 * 100.0) * 100.0, 2)
    # CI comes from bootstrap_roi_ci (seeded, reproducible).
    ci = out["roi_ci95_bootstrap"]
    assert ci["n_boot"] == 200
    assert ci["seed"] == 42
    assert ci["ci_95"][0] <= ci["ci_95"][1]

    # Reproducible: same seed, same CI.
    out2 = ablation.band_roi_from_bets(captured, band=0.10,
                                       bootstrap_n=200, bootstrap_seed=42)
    assert out2["roi_ci95_bootstrap"]["ci_95"] == ci["ci_95"]

    # Below the band: no bets.
    assert ablation.band_roi_from_bets(captured, band=0.90)["n_bets"] == 0
