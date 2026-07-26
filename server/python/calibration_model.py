#!/usr/bin/env python3
"""
Isotonic calibration for post-hoc probability correction.

Corrects systematic miscalibration:
  - Model predicts 10-15% but actual win rate is 7.1% (overconfident)
  - Model predicts 20-30% but actual win rate is 60% (underconfident)

Fitted on out-of-fold predictions from retrain_v2.py walk-forward CV.
Applied in run_tips_pipeline.py before market blending.
"""

import json
import os
import pickle
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(SCRIPT_DIR, "models", "isotonic_calibrator.pkl")


def _sidecar_path(model_path):
    """Provenance file that sits beside the pickle."""
    return os.path.splitext(model_path)[0] + ".meta.json"


class ProbabilityCalibrator:
    def __init__(self, model_path=None):
        self.model_path = model_path or DEFAULT_PATH
        self.iso_reg = None
        self.meta = None

    def fit(self, predicted_probs, actual_outcomes, meta=None):
        """Fit isotonic regression on predicted probabilities vs actual outcomes.

        `meta` records what the fit was made from. It is written to a sidecar
        .meta.json so a loaded artifact can state its own provenance — without
        it there is no way to tell an out-of-fold fit from an in-sample one,
        and an in-sample fit silently flatters every downstream edge.
        """
        from sklearn.isotonic import IsotonicRegression

        predicted_probs = np.asarray(predicted_probs, dtype=float)
        actual_outcomes = np.asarray(actual_outcomes, dtype=int)
        if predicted_probs.size != actual_outcomes.size:
            raise ValueError(
                f"length mismatch: {predicted_probs.size} probs vs "
                f"{actual_outcomes.size} outcomes")
        if predicted_probs.size == 0:
            raise ValueError("cannot fit a calibrator on zero rows")

        self.iso_reg = IsotonicRegression(
            y_min=0.01, y_max=0.95, out_of_bounds="clip"
        )
        self.iso_reg.fit(predicted_probs, actual_outcomes)

        self.meta = dict(meta or {})
        self.meta.setdefault("fit_method", "isotonic")
        self.meta.setdefault("n_rows", int(predicted_probs.size))
        self.meta.setdefault("base_rate", round(float(actual_outcomes.mean()), 6))
        self.meta.setdefault("out_of_fold", None)

        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump(self.iso_reg, f)
        with open(_sidecar_path(self.model_path), "w") as f:
            json.dump(self.meta, f, indent=2, default=str)
        print(f"  Isotonic calibrator saved to {self.model_path}")
        if self.meta.get("out_of_fold") is not True:
            print("  WARNING: provenance does not assert out_of_fold=True — "
                  "an in-sample fit will overstate calibration quality")

    def describe(self):
        """Provenance of the loaded artifact, or a clear statement of its absence.

        Callers use this to assert that the calibrator in production is the one
        they think it is. Returns a dict; never raises.
        """
        if self.iso_reg is None:
            self.load()
        return {
            "path": self.model_path,
            "exists": os.path.exists(self.model_path),
            "loaded": self.iso_reg is not None,
            "meta": self.meta,
            "has_provenance": self.meta is not None,
        }

    def calibrate(self, predicted_probs):
        """Apply calibration to new predictions. Input/output on 0-1 scale."""
        if self.iso_reg is None:
            self.load()
        if self.iso_reg is None:
            return predicted_probs
        return self.iso_reg.transform(np.asarray(predicted_probs))

    def load(self):
        if os.path.exists(self.model_path):
            with open(self.model_path, "rb") as f:
                self.iso_reg = pickle.load(f)
            side = _sidecar_path(self.model_path)
            if os.path.exists(side):
                try:
                    with open(side) as f:
                        self.meta = json.load(f)
                except Exception as err:
                    print(f"  [CALIB] provenance unreadable ({err}) — treating as absent")
                    self.meta = None
            return True
        return False


def _self_test():
    import tempfile
    print("calibration_model self-test")

    rng = np.random.default_rng(42)
    # Deliberately overconfident scores: true rate is half the stated one.
    raw = rng.uniform(0.02, 0.60, size=4000)
    y = (rng.random(4000) < raw * 0.5).astype(int)

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "models", "iso.pkl")

        cal = ProbabilityCalibrator(model_path=path)
        cal.fit(raw, y, meta={"out_of_fold": True, "source": "self-test",
                              "folds": 5})
        assert os.path.exists(path)
        assert os.path.exists(_sidecar_path(path)), "sidecar provenance must be written"

        out = cal.calibrate(raw)
        assert out.min() >= 0.01 - 1e-9 and out.max() <= 0.95 + 1e-9, (out.min(), out.max())
        # Calibration must pull an overconfident model toward the base rate.
        assert abs(out.mean() - y.mean()) < abs(raw.mean() - y.mean()), \
            (raw.mean(), out.mean(), y.mean())
        print(f"  isotonic: mean {raw.mean():.3f} -> {out.mean():.3f} vs base rate {y.mean():.3f}")

        fresh = ProbabilityCalibrator(model_path=path)
        d = fresh.describe()
        assert d["exists"] and d["loaded"] and d["has_provenance"], d
        assert d["meta"]["out_of_fold"] is True and d["meta"]["n_rows"] == 4000, d["meta"]
        print(f"  provenance survives reload: {d['meta']['fit_method']}, "
              f"n={d['meta']['n_rows']}, out_of_fold={d['meta']['out_of_fold']}")

        missing = ProbabilityCalibrator(model_path=os.path.join(tmp, "absent.pkl"))
        dm = missing.describe()
        assert dm["exists"] is False and dm["loaded"] is False, dm
        passthrough = missing.calibrate(np.array([0.1, 0.2, 0.3]))
        assert np.allclose(passthrough, [0.1, 0.2, 0.3]), \
            "a missing artifact must pass probabilities through untouched"
        print("  missing artifact: describe() reports absence, calibrate() passes through")

        for bad in ((np.array([0.1, 0.2]), np.array([1])), (np.array([]), np.array([]))):
            try:
                ProbabilityCalibrator(model_path=path).fit(*bad)
                raise AssertionError(f"expected ValueError for {bad}")
            except ValueError:
                pass
        print("  fit() rejects length mismatch and empty input")

    print("All tests completed successfully.")


if __name__ == "__main__":
    _self_test()
