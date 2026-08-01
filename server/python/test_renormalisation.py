#!/usr/bin/env python3
"""DB-free unit tests for task 05: per-race renormalisation
(STRIDE_RENORMALISE_FIELD) and the calibrator coverage gate."""

import copy
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fit_calibrator
import run_tips_pipeline as rtp
from market_prob import overround, renormalise_probs


def _field():
    """Three-runner field whose post-anchor probabilities sum well over 100."""
    return [
        {"horse": "Alpha", "winPercentage": 60.0, "marketOdds": 2.0,
         "selectionScore": 40.0},
        {"horse": "Bravo", "winPercentage": 40.0, "marketOdds": 4.0,
         "selectionScore": 30.0},
        {"horse": "Charlie", "winPercentage": 30.0, "marketOdds": 8.0,
         "selectionScore": 20.0},
    ]


def _legacy_expected(horse):
    """Replicate the legacy anchor math for one runner (no calibrator artifact)."""
    odds = horse["marketOdds"]
    ov = overround([h["marketOdds"] for h in _field()])
    raw = horse["winPercentage"]
    true_market = (100.0 / odds) / ov
    mw = 0.80 if odds <= 3 else 0.70 if odds <= 6 else 0.50 if odds <= 10 \
        else 0.45 if odds <= 15 else 0.40 if odds <= 30 else 0.30
    calibrated = mw * raw + (1 - mw) * true_market
    return round(calibrated, 2), round(true_market, 2), round(calibrated - true_market, 2)


@pytest.fixture
def renorm_off(monkeypatch):
    monkeypatch.delenv("STRIDE_RENORMALISE_FIELD", raising=False)
    yield


@pytest.fixture
def renorm_on(monkeypatch):
    monkeypatch.setenv("STRIDE_RENORMALISE_FIELD", "true")
    yield


class TestFlagOffLegacyParity:
    def test_anchor_output_byte_identical(self, renorm_off):
        horses = _field()
        out = rtp.calibrate_and_score(copy.deepcopy(horses), overround(
            [h["marketOdds"] for h in horses]))
        for orig, h in zip(_field(), out):
            win, tmarket, edge = _legacy_expected(orig)
            assert h["winPercentage"] == win
            assert h["trueMarketProb"] == tmarket
            assert h["modelEdge"] == edge
            assert h["fairOdds"] == round(100.0 / tmarket, 2)
            assert "_winPctBeforeRenorm" not in h

    def test_field_not_renormalised_when_off(self, renorm_off):
        horses = _field()
        out = rtp.calibrate_and_score(copy.deepcopy(horses), overround(
            [h["marketOdds"] for h in horses]))
        field_sum = sum(h["winPercentage"] for h in out)
        assert abs(field_sum - 100.0) > 1.0  # legacy: no normalisation


class TestRenormalisation:
    def test_field_sums_to_100(self, renorm_on):
        horses = _field()
        out = rtp.calibrate_and_score(copy.deepcopy(horses), overround(
            [h["marketOdds"] for h in horses]))
        field_sum = sum(h["winPercentage"] for h in out)
        assert abs(field_sum - 100.0) <= 1e-6

    def test_edge_consumes_renormalised_value(self, renorm_on):
        horses = _field()
        out = rtp.calibrate_and_score(copy.deepcopy(horses), overround(
            [h["marketOdds"] for h in horses]))
        for h in out:
            assert h["modelEdge"] == pytest.approx(
                round(h["winPercentage"] - h["trueMarketProb"], 2), abs=1e-9)
            assert "_winPctBeforeRenorm" in h

    def test_ev_consumes_renormalised_value(self, renorm_on):
        horses = _field()
        out = rtp.calibrate_and_score(copy.deepcopy(horses), overround(
            [h["marketOdds"] for h in horses]))
        for h in out:
            calib = h["winPercentage"] / 100.0
            tmarket = h["trueMarketProb"] / 100.0
            ev = calib / tmarket - 1.0
            # compute_confidence tiers from the same (renormalised) numbers.
            tier = rtp.compute_confidence(h)
            if h["marketOdds"] > 30:
                assert tier == "low"
            elif ev > 0.0 and h["modelEdge"] > 1.0:
                assert tier == "high"
            elif h["modelEdge"] > 0.0:
                assert tier == "medium"
            else:
                assert tier == "low"

    def test_renormalised_values_match_shared_helper(self, monkeypatch):
        horses = _field()
        ov = overround([h["marketOdds"] for h in horses])
        monkeypatch.delenv("STRIDE_RENORMALISE_FIELD", raising=False)
        off = rtp.calibrate_and_score(copy.deepcopy(horses), ov)
        pre = {h["horse"]: h["winPercentage"] for h in off}

        monkeypatch.setenv("STRIDE_RENORMALISE_FIELD", "true")
        on = rtp.calibrate_and_score(copy.deepcopy(horses), ov)
        expected = renormalise_probs([pre[h["horse"]] for h in horses])
        for h, exp in zip(on, expected):
            assert h["winPercentage"] == pytest.approx(exp, abs=1e-9)
            assert h["_winPctBeforeRenorm"] == pre[h["horse"]]

    def test_tier_transitions_logged_not_dropped(self, renorm_on, capsys):
        horses = _field()
        out = rtp.calibrate_and_score(copy.deepcopy(horses), overround(
            [h["marketOdds"] for h in horses]))
        assert len(out) == len(horses)  # no runner silently dropped
        err = capsys.readouterr().err
        assert "[RENORM_TIER_TRANSITION]" in err
        assert "tier_before" in err and "tier_after" in err

    def test_zero_field_is_noop(self, renorm_on):
        horses = [{"horse": "A", "winPercentage": 0.0, "marketOdds": 0,
                   "selectionScore": 1.0}]
        out = rtp.calibrate_and_score(copy.deepcopy(horses), 1.0)
        assert out[0]["winPercentage"] == 0.0


def _source_frame(days=6, pct_scale=True):
    import datetime as _dt
    rng = np.random.default_rng(11)
    rows = []
    for day in range(days):
        date = (_dt.date(2026, 6, 1) + _dt.timedelta(days=day)).isoformat()
        for race_no in range(2):
            for i in range(5):
                p = float(rng.uniform(0.02, 0.6))
                rows.append({
                    "race_date": date,
                    "track": "TEST", "race_number": race_no + 1,
                    "prob": p * 100.0 if pct_scale else p,
                    "won": 1 if i == 0 and race_no == 0 else 0,
                })
    return pd.DataFrame(rows)


class TestCoverageGate:
    def test_refusal_below_threshold(self, monkeypatch):
        monkeypatch.setenv("STRIDE_CAL_MIN_COVERAGE", "500")
        with pytest.raises(RuntimeError, match="refusing to fit"):
            fit_calibrator.check_coverage(_source_frame()[:120],
                                          "mc_win_percentage_oof")

    def test_env_threshold_override(self, monkeypatch):
        monkeypatch.setenv("STRIDE_CAL_MIN_COVERAGE", "10")
        report = fit_calibrator.check_coverage(_source_frame(),
                                               "mc_win_percentage_oof")
        assert report["sufficient"] is True
        assert report["usable_rows"] == 60
        assert report["min_coverage"] == 10
        assert report["date_min"] == "2026-06-01"
        assert report["date_max"] == "2026-06-06"
        assert report["source"] == "mc_win_percentage_oof"

    def test_default_threshold_is_500(self, monkeypatch):
        monkeypatch.delenv("STRIDE_CAL_MIN_COVERAGE", raising=False)
        assert fit_calibrator.min_coverage() == 500

    def test_explicit_min_rows_argument(self):
        with pytest.raises(RuntimeError):
            fit_calibrator.check_coverage(_source_frame(), "final_prob",
                                          min_rows=1000)
        report = fit_calibrator.check_coverage(_source_frame(), "final_prob",
                                               min_rows=60)
        assert report["sufficient"] is True

    def test_percent_scale_normalised_to_unit_interval(self, monkeypatch):
        monkeypatch.setenv("STRIDE_CAL_MIN_COVERAGE", "10")
        frame = fit_calibrator.normalise_source_frame(_source_frame())
        assert frame["prob"].max() <= 1.0
        assert frame["prob"].min() >= 0.0


class TestRecordedSourceOof:
    def test_provenance_records_stage_quantity(self):
        from walk_forward_backtest import WalkForwardSplitter
        probs, y, prov = fit_calibrator.recorded_source_oof(
            _source_frame(days=60, pct_scale=False), "mc_win_percentage_oof",
            WalkForwardSplitter(min_train_size=50, test_size=30, gap_days=1,
                                race_safe=True))
        assert prov["source"] == "mc_win_percentage_oof"
        assert prov["stage_quantity"] == "mc_win_percentage_oof"
        assert prov["out_of_fold"] is True
        assert prov["n_rows"] == 600
        assert prov["folds"] and all(
            f["test_start"] and f["test_end"] for f in prov["folds"])
        assert probs.size == y.size == 600

    def test_v2_artifact_path_and_v1_guard(self, tmp_path, monkeypatch):
        assert fit_calibrator.default_artifact_path(
            "mc_win_percentage_oof").endswith("isotonic_calibrator_v2.pkl")
        assert fit_calibrator.default_artifact_path(
            "final_prob").endswith("isotonic_calibrator_v2.pkl")
        assert fit_calibrator.default_artifact_path(
            "ml_ensemble_oof").endswith("isotonic_calibrator.pkl")
        with pytest.raises(RuntimeError, match="v1"):
            fit_calibrator.fit_from_oof(
                np.array([0.1, 0.5, 0.9]), np.array([0, 0, 1]),
                model_path=fit_calibrator.V1_CALIBRATOR_PATH)

    def test_sidecar_written_with_source(self, tmp_path):
        path = str(tmp_path / "staging" / "isotonic_calibrator_v2.pkl")
        probs = np.linspace(0.05, 0.9, 200)
        y = (probs > 0.5).astype(int)
        cal = fit_calibrator.fit_from_oof(
            probs, y, model_path=path,
            provenance={"out_of_fold": True, "source": "mc_win_percentage_oof",
                        "stage_quantity": "mc_win_percentage_oof",
                        "folds": [{"fold_number": 1, "test_start": "2026-06-01",
                                   "test_end": "2026-06-06"}]})
        from calibration_model import ProbabilityCalibrator
        meta = ProbabilityCalibrator(model_path=path).describe()["meta"]
        assert meta["source"] == "mc_win_percentage_oof"
        assert meta["stage_quantity"] == "mc_win_percentage_oof"
        assert meta["n_rows"] == 200
        assert meta["folds"][0]["test_start"] == "2026-06-01"


class TestShadowCompare:
    def test_no_data_graceful(self):
        from shadow_calibrator_compare import compare
        report = compare([], None, None)
        assert report["status"] == "no_data"
        assert report["variants"] == {}

    def test_renormalised_variant_sums_to_one(self):
        from shadow_calibrator_compare import compare
        rng = np.random.default_rng(5)
        rows = []
        for race_no in range(6):
            n = 7
            true_p = rng.dirichlet(np.ones(n))
            winner = int(rng.choice(n, p=true_p))
            for i in range(n):
                rows.append({"race_date": "2026-07-01", "track": "T",
                             "race_number": race_no,
                             "prob": float(true_p[i] * 1.4),  # sums != 1
                             "won": 1 if i == winner else 0})
        report = compare(rows, None, None)
        v = report["variants"]["current_renormalised"]
        assert v["race_sums"]["within_tolerance"] is True
        assert abs(v["race_sums"]["mean_sum"] - 1.0) <= 1e-6
        assert report["variants"]["current"]["race_sums"]["within_tolerance"] is False
        for name, variant in report["variants"].items():
            assert 0.0 <= variant["brier"] <= 1.0
            assert variant["log_loss"] > 0.0
            assert variant["bins"]
