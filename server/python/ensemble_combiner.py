#!/usr/bin/env python3
"""Learned, persisted combination of the three base learners — evaluated
cross-fitted, and compared against what production actually runs.

Why (12-retrain-rebaseline.md step 4, evidence base C3): production blends
XGBoost/LightGBM/CatBoost with the hardcoded seed accuracies in
ml_model.RacingMLModel._model_performance (never updated — update_model_
performance has no callers), while retrain_v2's CV scores an equal-weight
mean of isotonic-calibrated probabilities. The CV number has never described
the function that runs live, and the one learned combiner in the tree
(stacking_meta_learner) was fitted on shuffled StratifiedKFold folds and was
never pickled by ml_model.save() — dead after every reload.

What this is: a convex combination of the RAW base probabilities (what
production has at inference), fitted on purge-gapped walk-forward OOF
predictions by log-loss. Three methods:

  equal      1/k each — the CV arm's weights
  simplex    non-negative weights summing to 1 (SLSQP; grid fallback). A
             weighted AVERAGE, not a re-mapping: it keeps the base
             probability scale and adds no calibration layer (standing
             prohibition 3 — do not stack another calibrator). The persisted
             default.
  logistic   L2 logistic regression on logit(p) — a comparison arm only;
             its output is a re-mapping, so it is never the persisted default.

How it is judged (pre-registration amendment 2026-09-05 §3): CROSS-FITTED.
In run_walk_forward_cv, the combiner scored on fold k is fitted on OOF rows
from folds strictly before k; the persisted production combiner is fitted on
all OOF rows and its in-sample numbers are labelled as such, never quoted as
performance. production_blend() reproduces the live hardcoded blend on the
same rows so that, for the first time, the CV reports what production runs.

Expected effect: small. Three GBMs on identical features are highly
correlated; any convex blend lands within thousandths of AUC of any other.
The value is that production, CV and the artifact then compute ONE function.

Serving: ml_model.RacingMLModel uses a persisted combiner only under
STRIDE_LEARNED_BLEND (default OFF — byte-identical legacy blend).

`python ensemble_combiner.py` runs the self-test (numpy + scikit-learn).
"""

from __future__ import annotations

import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

EPS = 1e-6
METHODS = ("equal", "simplex", "logistic")
DEFAULT_MODEL_NAMES = ("xgb", "lgb", "cb")
# Artifacts carry the combiner as the plain dict to_state() returns — never
# as a pickled instance of this class. A class path inside the artifact
# would make every v3 pickle's load depend on this module existing under
# this name on the loading checkout, and RacingMLModel.load treats any
# unpickling error as "no model" (the run would then score with no ML at
# all, looking normal). Bump the format when the state shape changes.
STATE_FORMAT = "ensemble_combiner/1"
# Evaluation-only guards for the cross-fitted arm: how much prior OOF must
# exist before a combiner is fitted and scored on the next fold. Not model
# thresholds — a fold that falls short simply has no combiner arm.
MIN_PRIOR_FOLDS = 2
MIN_PRIOR_ROWS = 200


def _clip(p: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(p, dtype=float), EPS, 1.0 - EPS)


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = _clip(p)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def _simplex_grid(k: int, step: float = 0.05) -> Iterable[np.ndarray]:
    """All weight vectors on the k-simplex with coordinates on a `step` grid."""
    n = int(round(1.0 / step))

    def rec(prefix: List[int], remaining: int, slots: int):
        if slots == 1:
            yield prefix + [remaining]
            return
        for i in range(remaining + 1):
            yield from rec(prefix + [i], remaining - i, slots - 1)

    for combo in rec([], n, k):
        yield np.asarray(combo, dtype=float) / n


class EnsembleCombiner:
    def __init__(self, method: str = "simplex",
                 model_names: Sequence[str] = DEFAULT_MODEL_NAMES):
        if method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}, got {method!r}")
        self.method = method
        self.model_names = tuple(model_names)
        self.weights_: Optional[np.ndarray] = None
        self.coef_: Optional[np.ndarray] = None
        self.intercept_: float = 0.0
        self.n_rows_: int = 0
        self.fit_log_loss_: Optional[float] = None
        self.is_fitted = False

    # ----------------------------------------------------------------- fit
    def fit(self, P, y) -> "EnsembleCombiner":
        P = _clip(np.atleast_2d(np.asarray(P, dtype=float)))
        y = np.asarray(y, dtype=float).ravel()
        if P.shape[0] != y.shape[0]:
            raise ValueError("P and y must have the same number of rows")
        if P.shape[1] != len(self.model_names):
            raise ValueError(f"P has {P.shape[1]} columns, expected {len(self.model_names)} "
                             f"({self.model_names})")
        k = P.shape[1]
        if self.method == "equal":
            self.weights_ = np.full(k, 1.0 / k)
        elif self.method == "simplex":
            self.weights_ = self._fit_simplex(P, y)
        else:
            self._fit_logistic(P, y)
        self.n_rows_ = int(P.shape[0])
        self.is_fitted = True
        self.fit_log_loss_ = log_loss(self.predict(P), y)
        return self

    def _fit_simplex(self, P: np.ndarray, y: np.ndarray) -> np.ndarray:
        k = P.shape[1]
        if k == 1:
            return np.ones(1)

        def objective(w):
            return log_loss(P @ w, y)

        best = None
        try:
            from scipy.optimize import minimize
            w0 = np.full(k, 1.0 / k)
            res = minimize(objective, w0, method="SLSQP",
                           bounds=[(0.0, 1.0)] * k,
                           constraints=[{"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}],
                           options={"maxiter": 500, "ftol": 1e-12})
            if res.success or np.isfinite(res.fun):
                w = np.clip(res.x, 0.0, None)
                best = w / w.sum() if w.sum() > 0 else w0
        except Exception:
            best = None
        # Grid check: cheap for k <= 3, and it guards against a local SLSQP
        # stall — whichever is better by the objective wins.
        if k <= 3:
            grid_best, grid_val = None, np.inf
            for w in _simplex_grid(k, 0.05):
                v = objective(w)
                if v < grid_val:
                    grid_best, grid_val = w, v
            if best is None or grid_val < objective(best) - 1e-12:
                best = grid_best
        if best is None:
            best = np.full(k, 1.0 / k)
        return np.asarray(best, dtype=float)

    def _fit_logistic(self, P: np.ndarray, y: np.ndarray) -> None:
        from sklearn.linear_model import LogisticRegression
        Z = np.log(P / (1.0 - P))
        lr = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000)
        lr.fit(Z, y.astype(int))
        self.coef_ = np.asarray(lr.coef_[0], dtype=float)
        self.intercept_ = float(lr.intercept_[0])

    # ------------------------------------------------------------- predict
    def predict(self, P) -> np.ndarray:
        P = _clip(np.atleast_2d(np.asarray(P, dtype=float)))
        if P.shape[1] != len(self.model_names):
            raise ValueError(f"P has {P.shape[1]} columns, expected {len(self.model_names)}")
        if not self.is_fitted:
            return P.mean(axis=1)
        if self.method in ("equal", "simplex"):
            return _clip(P @ self.weights_)
        Z = np.log(P / (1.0 - P))
        return _clip(1.0 / (1.0 + np.exp(-(Z @ self.coef_ + self.intercept_))))

    # --------------------------------------------------------------- state
    def to_state(self) -> Dict[str, Any]:
        """Plain-data form for the artifact (JSON-serialisable, no class
        path in the pickle); from_state restores it exactly."""
        return {
            "format": STATE_FORMAT,
            "method": self.method,
            "model_names": list(self.model_names),
            "is_fitted": bool(self.is_fitted),
            "n_rows": int(self.n_rows_),
            "fit_log_loss_in_sample": self.fit_log_loss_,
            "weights": None if self.weights_ is None else [float(w) for w in self.weights_],
            "coef": None if self.coef_ is None else [float(c) for c in self.coef_],
            "intercept": float(self.intercept_),
        }

    @classmethod
    def from_state(cls, state: Dict[str, Any]) -> "EnsembleCombiner":
        if not isinstance(state, dict) or state.get("format") != STATE_FORMAT:
            raise ValueError(f"not an {STATE_FORMAT} state: {str(state)[:80]!r}")
        c = cls(state["method"], model_names=state["model_names"])
        k = len(c.model_names)
        if state.get("weights") is not None:
            w = np.asarray(state["weights"], dtype=float)
            if w.shape != (k,) or not np.all(np.isfinite(w)):
                raise ValueError("weights do not match model_names")
            c.weights_ = w
        if state.get("coef") is not None:
            coef = np.asarray(state["coef"], dtype=float)
            if coef.shape != (k,) or not np.all(np.isfinite(coef)):
                raise ValueError("coef does not match model_names")
            c.coef_ = coef
        c.intercept_ = float(state.get("intercept", 0.0))
        c.n_rows_ = int(state.get("n_rows", 0))
        c.fit_log_loss_ = state.get("fit_log_loss_in_sample")
        fitted = bool(state.get("is_fitted", False))
        if fitted and c.method in ("equal", "simplex") and c.weights_ is None:
            raise ValueError("fitted equal/simplex state without weights")
        if fitted and c.method == "logistic" and c.coef_ is None:
            raise ValueError("fitted logistic state without coef")
        c.is_fitted = fitted
        return c

    def describe(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"method": self.method, "model_names": list(self.model_names),
                               "is_fitted": self.is_fitted, "n_rows": self.n_rows_,
                               "fit_log_loss_in_sample": self.fit_log_loss_}
        if self.weights_ is not None:
            out["weights"] = {n: float(w) for n, w in zip(self.model_names, self.weights_)}
        if self.coef_ is not None:
            out["logit_coef"] = {n: float(c) for n, c in zip(self.model_names, self.coef_)}
            out["logit_intercept"] = self.intercept_
        return out


# --------------------------------------------------------------- production

def production_weights(distance_m: Optional[float]) -> Dict[str, float]:
    """The live blend's weights for one race, from ml_model.RacingMLModel's
    hardcoded seed accuracies — called on the real method, never restated."""
    from ml_model import RacingMLModel
    shell = RacingMLModel.__new__(RacingMLModel)
    if distance_m is None or not np.isfinite(distance_m) or distance_m <= 0:
        cat = "mile"      # what predict_adjustment_with_stages (the mc_api path) uses
    else:
        cat = shell._get_race_category(float(distance_m))
    w = shell._get_ensemble_weights(cat)
    return {"xgb": w["xgb"], "lgb": w["lgb"], "cb": w["cat"]}


def production_blend(P, model_names: Sequence[str], distances=None) -> np.ndarray:
    """What production computes today on these raw predictions — the
    hardcoded weighted average in RacingMLModel.predict_components, per row
    by race distance category (the run_tips_pipeline path passes distance_m;
    the mc_api path does not, so None -> 'mile'). Faithful to the live
    function, not a tidied version of it: the category comes from the live
    method, an absent model contributes zero with the weights NOT
    renormalised (predict_components substitutes np.zeros for a missing
    model's predictions), the terms are summed in production's order, and
    nothing is clipped — so with model_names in production order the result
    is bit-identical to the live blend. Measured in the CV for the first time
    next to the other arms."""
    from ml_model import RacingMLModel
    shell = RacingMLModel.__new__(RacingMLModel)
    P = np.atleast_2d(np.asarray(P, dtype=float))
    n = P.shape[0]
    if P.shape[1] != len(model_names):
        raise ValueError(f"P has {P.shape[1]} columns, expected {len(model_names)}")
    d = (np.asarray(distances, dtype=float).reshape(-1) if distances is not None
         else np.full(n, np.nan))
    if d.shape != (n,):
        raise ValueError("distances must have one entry per row")
    cats = np.array(["mile" if not (np.isfinite(x) and x > 0) else shell._get_race_category(float(x))
                     for x in d])
    out = np.zeros(n)
    for cat in np.unique(cats):
        mask = cats == cat
        w = shell._get_ensemble_weights(cat)
        live = {"xgb": w["xgb"], "lgb": w["lgb"], "cb": w["cat"]}
        acc = np.zeros(int(mask.sum()))
        for j, name in enumerate(model_names):
            acc = acc + P[mask, j] * live[name]
        out[mask] = acc
    return out


def arm_metrics(p: np.ndarray, y: np.ndarray) -> Dict[str, float]:
    from sklearn.metrics import brier_score_loss, roc_auc_score
    y = np.asarray(y).astype(int)
    try:
        auc = float(roc_auc_score(y, p))
    except Exception:
        auc = float("nan")
    return {"auc": auc, "brier": float(brier_score_loss(y, _clip(p))), "log_loss": log_loss(p, y)}


# ---------------------------------------------------------------- self-test

def _self_test() -> None:
    print("ensemble_combiner self-test")
    rng = np.random.default_rng(12)
    n = 6000
    latent = rng.normal(0, 1.2, n)
    y = (rng.random(n) < 1 / (1 + np.exp(-(latent - 2.0)))).astype(int)

    def base(noise):
        z = latent - 2.0 + rng.normal(0, noise, n)
        return 1 / (1 + np.exp(-z))

    P = np.column_stack([base(0.3), base(1.5), base(3.0)])   # good, noisy, very noisy
    tr, te = slice(0, 4000), slice(4000, n)

    simplex = EnsembleCombiner("simplex").fit(P[tr], y[tr])
    w = simplex.weights_
    assert abs(w.sum() - 1) < 1e-9 and np.all(w >= -1e-12), w
    assert w[0] > 0.6 and w[0] > w[1] > w[2] - 1e-9, f"weight should follow signal quality: {w}"
    equal = EnsembleCombiner("equal").fit(P[tr], y[tr])
    logistic = EnsembleCombiner("logistic").fit(P[tr], y[tr])
    m_s, m_e, m_l = (arm_metrics(c.predict(P[te]), y[te]) for c in (simplex, equal, logistic))
    assert m_s["log_loss"] < m_e["log_loss"], (m_s, m_e)
    assert m_l["log_loss"] < m_e["log_loss"] + 1e-9
    print(f"  simplex weights {np.round(w, 3)}: log-loss simplex {m_s['log_loss']:.4f} < equal "
          f"{m_e['log_loss']:.4f}; logistic {m_l['log_loss']:.4f}")

    # one model only: weight 1, prediction = that model
    one = EnsembleCombiner("simplex", model_names=("lgb",)).fit(P[tr, :1], y[tr])
    assert one.weights_.tolist() == [1.0]
    assert np.allclose(one.predict(P[te, :1]), _clip(P[te, 0]))

    # pickle roundtrip: identical predictions
    import pickle
    blob = pickle.dumps(simplex)
    back = pickle.loads(blob)
    assert np.array_equal(back.predict(P[te]), simplex.predict(P[te]))
    assert back.describe()["weights"] == simplex.describe()["weights"]

    # production blend reproduces the live weights per distance category
    from ml_model import RacingMLModel
    shell = RacingMLModel.__new__(RacingMLModel)
    for dist, cat in ((1000, "sprint"), (1400, "mile"), (2400, "staying"), (None, "mile")):
        w_live = shell._get_ensemble_weights(cat)
        pw = production_weights(dist)
        assert abs(pw["xgb"] - w_live["xgb"]) < 1e-12 and abs(pw["cb"] - w_live["cat"]) < 1e-12
    row = P[:1]
    expect = (row[0, 0] * shell._get_ensemble_weights("sprint")["xgb"]
              + row[0, 1] * shell._get_ensemble_weights("sprint")["lgb"]
              + row[0, 2] * shell._get_ensemble_weights("sprint")["cat"])
    got = production_blend(row, ("xgb", "lgb", "cb"), distances=[1000])[0]
    assert abs(got - expect) < 1e-12, (got, expect)
    # a missing model contributes zero and the weights are NOT renormalised —
    # exactly what predict_components does with np.zeros for a missing model
    got2 = production_blend(row[:, :2], ("xgb", "lgb"), distances=[1400])[0]
    wl = shell._get_ensemble_weights("mile")
    assert abs(got2 - (row[0, 0] * wl["xgb"] + row[0, 1] * wl["lgb"])) < 1e-12

    # plain-state roundtrip (the artifact form): exact, JSON-serialisable
    import json
    for c in (simplex, equal, logistic):
        st = json.loads(json.dumps(c.to_state()))
        assert st["format"] == STATE_FORMAT
        back3 = EnsembleCombiner.from_state(st)
        assert np.array_equal(back3.predict(P[te]), c.predict(P[te]))
        assert back3.describe() == c.describe()
    for bad in ({"format": "other"}, {"format": STATE_FORMAT, "method": "simplex",
                                       "model_names": ["a", "b"], "is_fitted": True, "weights": [1.0]}):
        try:
            EnsembleCombiner.from_state(bad)
            raise AssertionError("bad state must raise")
        except ValueError:
            pass

    # unfitted -> plain mean; wrong width -> error
    assert np.allclose(EnsembleCombiner("simplex").predict(P[:3]), _clip(P[:3]).mean(axis=1))
    try:
        EnsembleCombiner("simplex").fit(P[:, :2], y)
        raise AssertionError("width mismatch must raise")
    except ValueError:
        pass
    print("All ensemble_combiner self-tests passed.")


if __name__ == "__main__":
    _self_test()
    sys.exit(0)
