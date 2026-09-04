"""#124 defect 6: the deployed isotonic calibrator descends.

The shipped _pool_adjacent_violators merged violating pairs in place with
broken weight bookkeeping and pair-only backtracking, so its "isotonic"
output could still descend — the deployed artifact carried 21 descending
knots out of 50, violating the ordering guarantee the class documents.
These pin: the old algorithm really produces descending output (so the
replacement is warranted), the replacement is a correct PAVA, and load()
refuses a non-monotone artifact instead of applying it.
"""

import json

import numpy as np
import pytest

from mc_recalibration import MCRecalibrator


def _shipped_pava(y):
    """Verbatim copy of the replaced algorithm, kept to prove the defect."""
    n = len(y)
    result = y.copy().astype(float)
    weights = np.ones(n)
    i = 0
    while i < n - 1:
        if result[i] > result[i + 1]:
            merged = (result[i] * weights[i] + result[i + 1] * weights[i + 1]) / (weights[i] + weights[i + 1])
            result[i] = merged
            result[i + 1] = merged
            weights[i] += weights[i + 1]
            weights[i + 1] = weights[i]
            j = i - 1
            while j >= 0 and result[j] > result[j + 1]:
                merged = (result[j] * weights[j] + result[j + 1] * weights[j + 1]) / (weights[j] + weights[j + 1])
                result[j] = merged
                result[j + 1] = merged
                weights[j] += weights[j + 1]
                weights[j + 1] = weights[j]
                j -= 1
        i += 1
    return result


ADVERSARIAL = np.array([0.6, 0.2, 0.55, 0.15])


class TestPavaReplacement:
    def test_the_shipped_algorithm_really_descends(self):
        out = _shipped_pava(ADVERSARIAL)
        assert np.any(np.diff(out) < 0), (
            "the copied legacy PAVA no longer reproduces the defect this "
            "module was rewritten for — update or retire this pin")

    def test_replacement_is_monotone_on_the_same_input(self):
        out = MCRecalibrator()._pool_adjacent_violators(ADVERSARIAL)
        assert np.all(np.diff(out) >= 0)
        # Full pool: mean-preserving single block.
        assert out == pytest.approx(np.full(4, ADVERSARIAL.mean()))

    def test_known_block_structure(self):
        y = np.array([0.1, 0.3, 0.2, 0.5, 0.4, 0.35, 0.6])
        out = MCRecalibrator()._pool_adjacent_violators(y)
        expected = np.array([0.1, 0.25, 0.25, 1.25 / 3, 1.25 / 3, 1.25 / 3, 0.6])
        assert out == pytest.approx(expected)

    def test_monotone_input_is_untouched(self):
        y = np.array([0.05, 0.05, 0.2, 0.4, 0.9])
        out = MCRecalibrator()._pool_adjacent_violators(y)
        assert out == pytest.approx(y)

    def test_fuzz_always_monotone_and_mean_preserving(self):
        rng = np.random.default_rng(29)
        for _ in range(200):
            y = rng.uniform(0, 1, size=rng.integers(1, 40))
            out = MCRecalibrator()._pool_adjacent_violators(y)
            assert np.all(np.diff(out) >= -1e-12)
            assert out.mean() == pytest.approx(y.mean())

    def test_fit_refuses_to_persist_a_descending_curve(self, monkeypatch):
        cal = MCRecalibrator()
        monkeypatch.setattr(MCRecalibrator, "_pool_adjacent_violators",
                            lambda self, y: np.asarray(y, dtype=float))
        # Anti-calibrated data: low predictions all win, high all lose, so
        # bin means descend and the stubbed identity "fit" stays descending.
        preds = np.linspace(0.01, 0.6, 600)
        actuals = (preds < 0.3).astype(float)
        with pytest.raises(ValueError, match="descending"):
            cal._fit_isotonic(preds, actuals)


def _artifact(tmp_path, iso_y):
    path = tmp_path / "calibration_model.json"
    path.write_text(json.dumps({
        "fitted": True,
        "iso_x": list(np.linspace(0.05, 0.5, len(iso_y))),
        "iso_y": iso_y,
        "n_samples": 1000,
        "n_races": 100,
        "fit_date": "2026-01-01",
    }))
    return str(path)


class TestLoadGuard:
    def test_monotone_artifact_loads_and_applies(self, tmp_path):
        cal = MCRecalibrator()
        assert cal.load(_artifact(tmp_path, [0.05, 0.1, 0.2, 0.3])) is True
        assert cal.fitted
        out = cal.transform(np.array([0.04, 0.2, 0.6]))
        assert np.all(np.diff(out) >= 0)

    def test_nonmonotone_artifact_is_refused(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("STRIDE_MC_ALLOW_NONMONOTONE_CALIBRATOR",
                           raising=False)
        cal = MCRecalibrator()
        assert cal.load(_artifact(tmp_path, [0.05, 0.2, 0.1, 0.3])) is False
        assert not cal.fitted
        assert "REFUSED" in capsys.readouterr().out
        # Unfitted transform is the identity — clean downgrade to serving
        # uncalibrated, the path the cloud already runs.
        probes = np.array([0.1, 0.2, 0.4])
        assert cal.transform(probes) is probes

    def test_escape_hatch_reproduces_old_behaviour_on_purpose(
            self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("STRIDE_MC_ALLOW_NONMONOTONE_CALIBRATOR", "true")
        cal = MCRecalibrator()
        assert cal.load(_artifact(tmp_path, [0.05, 0.2, 0.1, 0.3])) is True
        assert cal.fitted
        assert "WARNING" in capsys.readouterr().out

    def test_missing_artifact_still_returns_false(self, tmp_path):
        cal = MCRecalibrator()
        assert cal.load(str(tmp_path / "absent.json")) is False
