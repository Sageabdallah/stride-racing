#!/usr/bin/env python3
"""
Isotonic calibration for post-hoc probability correction.

Corrects systematic miscalibration:
  - Model predicts 10-15% but actual win rate is 7.1% (overconfident)
  - Model predicts 20-30% but actual win rate is 60% (underconfident)

Fitted on out-of-fold predictions from retrain_v2.py walk-forward CV.
Applied in run_tips_pipeline.py before market blending.
"""

import os
import pickle
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(SCRIPT_DIR, "models", "isotonic_calibrator.pkl")


class ProbabilityCalibrator:
    def __init__(self, model_path=None):
        self.model_path = model_path or DEFAULT_PATH
        self.iso_reg = None

    def fit(self, predicted_probs, actual_outcomes):
        """Fit isotonic regression on predicted probabilities vs actual outcomes."""
        from sklearn.isotonic import IsotonicRegression

        self.iso_reg = IsotonicRegression(
            y_min=0.01, y_max=0.95, out_of_bounds="clip"
        )
        self.iso_reg.fit(predicted_probs, actual_outcomes)

        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump(self.iso_reg, f)
        print(f"  Isotonic calibrator saved to {self.model_path}")

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
            return True
        return False
