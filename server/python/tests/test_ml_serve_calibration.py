"""Audit 2026-09-06 H1: the ML ensemble's OOF isotonic calibrators are fitted
at training and were never applied at serve.

retrain_v2 attaches one isotonic calibrator per base model as
``model._isotonic``; ``RacingMLModel.predict_components`` served raw
``predict_proba`` regardless. Two repairs are pinned here:

* the artifact persists the calibrators explicitly (``oof_calibrators``),
  because the attribute route silently loses CatBoost's on pickling;
* ``STRIDE_ML_APPLY_ISOTONIC`` (default OFF, byte-identical) applies them in
  the weighted-average path, and only when every base model present has one.

No booster library is needed: the base models are stubs with a
``predict_proba`` and the calibrators are stubs with a ``transform``, so this
runs in CI where xgboost/catboost are absent.
"""

from __future__ import annotations

import io
import os
import pickle
import sys

import numpy as np
import pytest

import ml_model
from ml_model import RacingMLModel


class StubModel:
    """A booster that always emits the same (inflated) score."""

    def __init__(self, p):
        self.p = p

    def predict_proba(self, X):
        n = len(X)
        return np.column_stack([np.full(n, 1 - self.p), np.full(n, self.p)])


class StubCalibrator:
    """Maps a class-weight-inflated score back toward a base rate."""

    def __init__(self, factor):
        self.factor = factor

    def transform(self, x):
        return np.asarray(x, dtype=float) * self.factor


class _NoScaler:
    """Nothing fitted: predict_components falls through to X.values / X."""


def _model(cals=None, xgb=0.5, lgb=0.4, cat=0.6):
    m = RacingMLModel.__new__(RacingMLModel)
    m.model_path = "/nonexistent/racing_ensemble_v2.pkl"
    m.xgb_model = StubModel(xgb)
    m.lgb_model = StubModel(lgb)
    m.catboost_model = StubModel(cat)
    m.scaler = _NoScaler()
    m.is_trained = True
    m.feature_importance = {}
    m.training_stats = {}
    m.oof_calibrators = dict(cals or {})
    m._isotonic_notice_printed = False
    return m


X = np.zeros((3, 4))


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.delenv("STRIDE_ML_APPLY_ISOTONIC", raising=False)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("STRIDE_ML_APPLY_ISOTONIC", "true")


FULL = {"xgb": StubCalibrator(0.2), "lgb": StubCalibrator(0.25), "cb": StubCalibrator(1 / 6)}


class TestFlagOffIsLegacy:
    def test_raw_scores_are_served_even_when_calibrators_exist(self, flag_off):
        m = _model(FULL)
        out = m.predict_components(X)
        assert out["method"] == "weighted_average"
        assert np.allclose(out["xgb"], 0.5)
        assert np.allclose(out["lightgbm"], 0.4)
        assert np.allclose(out["catboost"], 0.6)
        w = out["weights"]
        assert np.allclose(out["ensemble"], 0.5 * w["xgb"] + 0.4 * w["lgb"] + 0.6 * w["cat"])
        assert m.serve_calibration_status()["applied"] is False

    def test_predict_adjustment_reads_the_same_raw_score(self, flag_off):
        m = _model(FULL)
        status = m.serve_calibration_status()
        assert status == {
            "enabled": False, "models_present": ["xgb", "lgb", "cb"],
            "calibrators_present": ["xgb", "lgb", "cb"], "calibrators_missing": [],
            "complete": True, "applied": False,
        }


class TestFlagOn:
    def test_each_component_is_calibrated_before_the_weighted_average(self, flag_on):
        m = _model(FULL)
        out = m.predict_components(X)
        assert out["method"] == "weighted_average+isotonic"
        assert np.allclose(out["xgb"], 0.1)
        assert np.allclose(out["lightgbm"], 0.1)
        assert np.allclose(out["catboost"], 0.1)
        assert np.allclose(out["ensemble"], 0.1)
        assert m.serve_calibration_status()["applied"] is True

    def test_incomplete_set_serves_raw_and_says_so_once(self, flag_on, monkeypatch):
        err = io.StringIO()
        monkeypatch.setattr(sys, "stderr", err)
        m = _model({"xgb": StubCalibrator(0.2), "lgb": StubCalibrator(0.25)})  # no cb
        out1 = m.predict_components(X)
        out2 = m.predict_components(X)
        assert out1["method"] == out2["method"] == "weighted_average"
        assert np.allclose(out1["catboost"], 0.6)
        status = m.serve_calibration_status()
        assert status["calibrators_missing"] == ["cb"]
        assert status["applied"] is False
        assert err.getvalue().count("serving RAW") == 1, "notice must print once, not per runner"

    def test_absent_model_does_not_demand_a_calibrator(self, flag_on):
        m = _model({"xgb": StubCalibrator(0.2), "lgb": StubCalibrator(0.25)})
        m.catboost_model = None
        out = m.predict_components(X)
        assert out["method"] == "weighted_average+isotonic"
        assert m.serve_calibration_status()["models_present"] == ["xgb", "lgb"]

    def test_untrained_model_is_untouched(self, flag_on):
        m = _model(FULL)
        m.is_trained = False
        assert m.predict_components(X)["method"] == "untrained"


class TestArtifactCarriesTheCalibrators:
    def test_payload_key_wins_and_attribute_route_is_the_fallback(self):
        m = _model()
        m.xgb_model._isotonic = StubCalibrator(0.5)     # survives pickling for xgb/lgb
        m.lgb_model._isotonic = StubCalibrator(0.5)
        # catboost: the attribute is what pickling drops — simulate an old artifact
        found = m._collect_oof_calibrators({})
        assert sorted(found) == ["lgb", "xgb"]
        payload_cb = StubCalibrator(0.1)
        found = m._collect_oof_calibrators({"oof_calibrators": {"cb": payload_cb}})
        assert found["cb"] is payload_cb and sorted(found) == ["cb", "lgb", "xgb"]

    def test_load_restores_calibrators_from_the_payload(self, tmp_path, flag_on):
        payload = {
            "xgb_model": StubModel(0.5), "lgb_model": StubModel(0.4), "cb_model": StubModel(0.6),
            "oof_calibrators": FULL, "feature_columns": ["a", "b"], "is_trained": True,
        }
        path = tmp_path / "racing_ensemble_v2.pkl"
        with open(path, "wb") as fh:
            pickle.dump(payload, fh)
        m = RacingMLModel.__new__(RacingMLModel)
        m.model_path = str(path)
        m.scaler = _NoScaler()
        m.oof_calibrators = {}
        m._isotonic_notice_printed = False
        assert m.load() is True
        assert sorted(m.oof_calibrators) == ["cb", "lgb", "xgb"]
        assert m.serve_calibration_status()["applied"] is True
        assert np.allclose(m.predict_components(X)["ensemble"], 0.1)

    def test_retrain_v2_save_model_persists_all_three(self, tmp_path, monkeypatch):
        # retrain_v2 sys.exit()s at import without a DATABASE_URL; save_model
        # itself never touches the database.
        monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
        retrain_v2 = pytest.importorskip("retrain_v2")
        xgb, lgb, cb = StubModel(0.5), StubModel(0.4), StubModel(0.6)
        xgb._isotonic, lgb._isotonic, cb._isotonic = FULL["xgb"], FULL["lgb"], FULL["cb"]
        out = tmp_path / "m.pkl"
        retrain_v2.save_model(xgb, lgb, cb, ["a"], {}, {}, {}, str(out))
        with open(out, "rb") as fh:
            payload = pickle.load(fh)
        assert sorted(payload["oof_calibrators"]) == ["cb", "lgb", "xgb"]
        assert isinstance(payload["oof_calibrators"]["cb"], StubCalibrator)

    def test_default_flag_is_off(self, monkeypatch):
        monkeypatch.delenv("STRIDE_ML_APPLY_ISOTONIC", raising=False)
        assert ml_model._ml_apply_isotonic_enabled() is False
