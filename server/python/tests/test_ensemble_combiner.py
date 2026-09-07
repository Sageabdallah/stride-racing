"""The learned blend is cross-fitted, persisted, measured against production,
and inert until STRIDE_LEARNED_BLEND is on.

Pins: simplex weights are a convex combination; the combiner scored on a
fold was fitted only on prior folds; the production blend arm reproduces
RacingMLModel's hardcoded weights; ml_model.save()/load() carry the combiner
and ONLY the combiner (the shuffled-fold stacker and the double calibrator
stay un-persisted, so the flag-off path after a reload is unchanged);
predict_components is byte-identical with the flag off and uses the combiner
with it on; the price-ablation arm drops exactly the four odds-derived inputs.
"""

import os
import pickle
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

SERVER_PYTHON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_PYTHON))

import ensemble_combiner as ec  # noqa: E402


def _synthetic(n=3000, seed=1):
    rng = np.random.default_rng(seed)
    latent = rng.normal(0, 1.2, n)
    y = (rng.random(n) < 1 / (1 + np.exp(-(latent - 2.0)))).astype(int)
    P = np.column_stack([1 / (1 + np.exp(-(latent - 2.0 + rng.normal(0, s, n)))) for s in (0.3, 1.5, 3.0)])
    return P, y


def test_simplex_is_a_convex_combination_that_follows_signal_quality():
    P, y = _synthetic()
    c = ec.EnsembleCombiner("simplex").fit(P, y)
    w = c.weights_
    assert abs(w.sum() - 1) < 1e-9 and (w >= -1e-12).all()
    assert w[0] > w[1] >= w[2] - 1e-9
    assert ec.log_loss(c.predict(P), y) <= ec.log_loss(ec.EnsembleCombiner("equal").fit(P, y).predict(P), y)


def test_methods_and_shapes():
    P, y = _synthetic(800)
    for m in ec.METHODS:
        c = ec.EnsembleCombiner(m).fit(P, y)
        p = c.predict(P)
        assert p.shape == (800,) and (p > 0).all() and (p < 1).all()
        assert c.describe()["method"] == m and c.describe()["is_fitted"]
    with pytest.raises(ValueError):
        ec.EnsembleCombiner("nope")
    with pytest.raises(ValueError):
        ec.EnsembleCombiner("simplex").fit(P[:, :2], y)
    assert np.allclose(ec.EnsembleCombiner("simplex").predict(P[:5]), ec._clip(P[:5]).mean(axis=1))


def test_pickle_and_plain_state_roundtrips_are_exact():
    import json
    P, y = _synthetic(500)
    for method in ec.METHODS:
        c = ec.EnsembleCombiner(method).fit(P, y)
        back = pickle.loads(pickle.dumps(c))
        assert np.array_equal(back.predict(P), c.predict(P))
        # the artifact form: a JSON-serialisable dict, no class path
        state = json.loads(json.dumps(c.to_state()))
        assert state["format"] == ec.STATE_FORMAT
        restored = ec.EnsembleCombiner.from_state(state)
        assert restored.is_fitted and restored.describe() == c.describe()
        assert np.array_equal(restored.predict(P), c.predict(P))
    for bad in (None, {"format": "something-else"},
                {"format": ec.STATE_FORMAT, "method": "simplex", "model_names": ["a", "b", "c"],
                 "is_fitted": True, "weights": [0.5, 0.5]},
                {"format": ec.STATE_FORMAT, "method": "logistic", "model_names": ["a"], "is_fitted": True}):
        with pytest.raises(ValueError):
            ec.EnsembleCombiner.from_state(bad)


def test_production_blend_reproduces_racingmlmodel_weights():
    from ml_model import RacingMLModel
    shell = RacingMLModel.__new__(RacingMLModel)
    P = np.array([[0.2, 0.3, 0.4], [0.1, 0.1, 0.5]])
    out = ec.production_blend(P, ("xgb", "lgb", "cb"), distances=[1000, 2400])
    for i, cat in enumerate(("sprint", "staying")):
        w = shell._get_ensemble_weights(cat)
        assert abs(out[i] - (P[i, 0] * w["xgb"] + P[i, 1] * w["lgb"] + P[i, 2] * w["cat"])) < 1e-12
    # no distance -> the mc_api path's 'mile'
    w = shell._get_ensemble_weights("mile")
    assert abs(ec.production_blend(P[:1], ("xgb", "lgb", "cb"))[0]
               - (P[0, 0] * w["xgb"] + P[0, 1] * w["lgb"] + P[0, 2] * w["cat"])) < 1e-12
    # an absent model contributes zero, weights NOT renormalised — as predict_components does
    assert abs(ec.production_blend(P[:1, :2], ("xgb", "lgb"), distances=[1400])[0]
               - (P[0, 0] * w["xgb"] + P[0, 1] * w["lgb"])) < 1e-12
    with pytest.raises(ValueError):
        ec.production_blend(P, ("xgb", "lgb", "cb"), distances=[1000])


def test_production_arm_is_bit_identical_to_predict_components(monkeypatch, tmp_path):
    """The arm named 'production' must be the function production runs, on
    the numbers production has — checked against predict_components itself
    (flag off), with all three models and with one missing."""
    monkeypatch.delenv("STRIDE_LEARNED_BLEND", raising=False)
    X = pd.DataFrame(np.random.default_rng(11).normal(size=(40, 3)), columns=["a", "b", "c"])
    for dist in (1000, 1400, 2400, None):
        m = _wrapper(tmp_path, False)
        out = m.predict_components(X, distance_m=dist)
        assert out["method"] == "weighted_average"
        P = np.column_stack([out["xgb"], out["lightgbm"], out["catboost"]])
        arm = ec.production_blend(P, ("xgb", "lgb", "cb"),
                                  distances=None if dist is None else [dist] * len(X))
        assert np.array_equal(arm, out["ensemble"]), f"distance {dist}"
    m = _wrapper(tmp_path, False)
    m.catboost_model = None          # production feeds zeros for it, unrenormalised
    out = m.predict_components(X, distance_m=1400)
    P2 = np.column_stack([out["xgb"], out["lightgbm"]])
    assert np.array_equal(ec.production_blend(P2, ("xgb", "lgb"), distances=[1400] * len(X)),
                          out["ensemble"])


def test_self_test_runs():
    ec._self_test()


# ---------------------------------------------------------------- retrain_v2

@pytest.fixture(scope="module")
def retrain():
    fake = types.ModuleType("psycopg2")
    extras = types.ModuleType("psycopg2.extras")
    fake.extras = extras
    sys.modules.setdefault("psycopg2", fake)
    sys.modules.setdefault("psycopg2.extras", extras)
    os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost/unused")
    import retrain_v2
    return retrain_v2


def _frame(n_days=170, seed=4):
    rng = np.random.default_rng(seed)
    rows = []
    base = pd.Timestamp("2026-01-01")
    for d in range(n_days):
        for r in range(2):
            n = int(rng.integers(6, 10))
            strength = rng.normal(0, 1, n)
            prob = np.exp(1.2 * strength) / np.exp(1.2 * strength).sum()
            w = int(rng.choice(n, p=prob))
            odds = 1.0 / np.clip(prob + rng.normal(0, 0.02, n), 0.02, None) * 1.12
            for i in range(n):
                rows.append({"race_date": (base + pd.Timedelta(days=d)).strftime("%Y-%m-%d"),
                             "track": "S", "race_number": r + 1,
                             "f1": strength[i] + rng.normal(0, 0.5), "f2": strength[i] + rng.normal(0, 1),
                             "f3": rng.normal(), "distance": float(rng.choice([1000, 1400, 2000])),
                             "market_odds": float(odds[i]), "is_winner": int(i == w),
                             "tip_time_odds": float(odds[i]), "sp_odds": float(odds[i])})
    return pd.DataFrame(rows)


def test_cv_arms_are_cross_fitted_and_measure_production(retrain):
    df = _frame()
    feats = ["f1", "f2", "f3", "distance", "market_odds"]
    X = df[feats].reset_index(drop=True)
    y = df["is_winner"].astype(int).reset_index(drop=True)
    dates = pd.to_datetime(df["race_date"]).reset_index(drop=True)
    meta = retrain.build_race_meta(df)
    res = retrain.run_walk_forward_cv(X, y, dates, feats, label="arms", race_meta=meta,
                                      collect_oof_calibrators=True)
    folds = res["folds"]
    assert len(folds) >= ec.MIN_PRIOR_FOLDS + 1
    # every fold has the legacy and production arms on the same rows
    for f in folds:
        assert {"legacy_equal_isotonic", "production_hardcoded"} <= set(f["arms"])
    # the first MIN_PRIOR_FOLDS folds cannot have a learned arm; later ones do,
    # and each records how many PRIOR folds it was fitted on
    for f in folds[:ec.MIN_PRIOR_FOLDS]:
        assert "simplex" not in f["arms"] and f["combiner_fitted_on_prior_folds"] is None
    later = [f for f in folds if "simplex" in f["arms"]]
    assert later, "with 170 days there must be folds with a cross-fitted arm"
    for f in later:
        assert f["combiner_fitted_on_prior_folds"] >= ec.MIN_PRIOR_FOLDS
        assert f["combiner_fitted_on_prior_folds"] == folds.index(f), \
            "fitted on exactly the folds scored before this one"
        assert f["arms"]["simplex"]["race"]["n_races_used"] > 0
    arms = res["arms"]
    assert arms["production_hardcoded"]["n_folds"] == len(folds)
    assert arms["simplex"]["n_folds"] == len(later)
    assert arms["simplex"]["race_metrics"]["model_top1_hit"] is not None
    # every arm is also summarised on exactly the learned arms' folds — the
    # comparison the flip criterion reads ("on the same folds")
    for name in ("legacy_equal_isotonic", "production_hardcoded", "simplex", "logistic"):
        assert arms[name]["same_folds"]["n_folds"] == len(later)
    assert arms["simplex"]["same_folds"]["mean_brier"] == arms["simplex"]["mean_brier"]
    same = np.mean([f["arms"]["production_hardcoded"]["brier"] for f in later])
    assert abs(arms["production_hardcoded"]["same_folds"]["mean_brier"] - same) < 1e-12
    assert arms["production_hardcoded"]["same_folds"]["race_metrics"]["n_races_used"] == \
        arms["simplex"]["race_metrics"]["n_races_used"]
    # persisted combiner fitted on ALL OOF rows, labelled in-sample
    comb = res["ensemble_combiner"]
    assert comb is not None and comb.is_fitted and comb.method == "simplex"
    assert list(comb.model_names) == res["present_models"]
    rep = res["combiner_report"]
    assert "IN-SAMPLE" in rep["fitted_on"]
    assert set(rep["in_sample"]) == {"equal", "simplex", "logistic"}
    assert rep["cross_fitted_arms"] is arms


def test_price_ablation_drops_exactly_the_four_odds_inputs(retrain):
    assert retrain.PRICE_FEATURES == ["market_odds", "fair_implied_prob", "odds_rank", "odds_rank_pct"]
    assert "market_efficiency_flag" not in retrain.PRICE_FEATURES
    assert set(retrain.PRICE_FEATURES) <= set(retrain.FEATURE_COLUMNS)
    assert set(retrain.PRICE_FEATURES) == set(retrain._HYBRID_NAN_PRESERVE)


def test_artifact_carries_the_combiner_as_plain_state(retrain, tmp_path):
    from ml_model import RacingMLModel
    P, y = _synthetic(600)
    comb = ec.EnsembleCombiner("simplex").fit(P, y)
    cv = {"ensemble_combiner": comb, "combiner_report": {"persisted": comb.describe()}, "arms": {}, "folds": []}
    art = retrain.artifact_cv_results(cv)
    assert cv["ensemble_combiner"] is comb, "the in-process object is untouched"
    assert art["ensemble_combiner"]["format"] == ec.STATE_FORMAT
    assert b"EnsembleCombiner" not in pickle.dumps(art)
    path = tmp_path / "v3.pkl"
    retrain.save_model(None, None, None, ["a", "b", "c"], {}, art, {}, str(path), version="v3",
                       extra={"ensemble_combiner": art["ensemble_combiner"]})
    assert b"EnsembleCombiner" not in path.read_bytes(), "no class path in the artifact"
    m = RacingMLModel(model_path=str(path))
    assert isinstance(m.ensemble_combiner, ec.EnsembleCombiner) and m.ensemble_combiner.is_fitted
    assert m.ensemble_combiner.describe() == comb.describe()
    assert retrain.artifact_cv_results({"ensemble_combiner": None})["ensemble_combiner"] is None


def test_report_drops_the_combiner_object_but_keeps_its_report(retrain, tmp_path):
    import json
    cv = {"ensemble_combiner": ec.EnsembleCombiner("equal"), "combiner_report": {"persisted": {"weights": {"lgb": 1.0}}},
          "arms": {}, "folds": []}
    path = retrain.write_report(str(tmp_path / "r.json"), cv, {}, {}, {"version": "v3"})
    rep = json.load(open(path))
    assert "ensemble_combiner" not in rep["cv"] and rep["cv"]["combiner_report"]["persisted"]["weights"] == {"lgb": 1.0}


# ---------------------------------------------------------------- ml_model

def _stand_in_model(seed):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(200, 3))
    y = (X[:, 0] + rng.normal(0, 0.5, 200) > 0).astype(int)
    return LogisticRegression().fit(X, y)


def _wrapper(tmp_path, with_combiner=True):
    from ml_model import RacingMLModel
    m = RacingMLModel.__new__(RacingMLModel)
    m.model_path = str(tmp_path / "artifact.pkl")
    m.xgb_model, m.lgb_model, m.catboost_model = (_stand_in_model(s) for s in (1, 2, 3))
    m.scaler = StandardScaler()          # unfitted -> raw X, as with a retrain_v2 artifact
    m.is_trained = True
    m.feature_importance, m.training_stats = {}, {}
    m._trained_feature_columns = ["a", "b", "c"]
    if with_combiner:
        P, y = _synthetic(600)
        m.ensemble_combiner = ec.EnsembleCombiner("simplex").fit(P, y)
    return m


def test_flag_off_is_byte_identical_to_the_legacy_blend(monkeypatch, tmp_path):
    monkeypatch.delenv("STRIDE_LEARNED_BLEND", raising=False)
    X = pd.DataFrame(np.random.default_rng(7).normal(size=(12, 3)), columns=["a", "b", "c"])
    with_c = _wrapper(tmp_path, True).predict_components(X, distance_m=1400)
    without = _wrapper(tmp_path, False).predict_components(X, distance_m=1400)
    assert with_c["method"] == "weighted_average" == without["method"]
    assert np.array_equal(with_c["ensemble"], without["ensemble"])


def test_flag_on_uses_the_persisted_combiner(monkeypatch, tmp_path):
    monkeypatch.setenv("STRIDE_LEARNED_BLEND", "true")
    m = _wrapper(tmp_path, True)
    X = pd.DataFrame(np.random.default_rng(8).normal(size=(12, 3)), columns=["a", "b", "c"])
    out = m.predict_components(X)
    assert out["method"] == "learned_blend"
    P = np.column_stack([out["xgb"], out["lightgbm"], out["catboost"]])
    assert np.array_equal(out["ensemble"], m.ensemble_combiner.predict(P))
    assert set(out["weights"]) == {"xgb", "lgb", "cb"}
    # no combiner on the artifact -> legacy, even with the flag on
    assert _wrapper(tmp_path, False).predict_components(X)["method"] == "weighted_average"


def test_save_and_load_persist_only_the_combiner(monkeypatch, tmp_path):
    from ml_model import RacingMLModel
    m = _wrapper(tmp_path, True)
    # Objects train() fits but save() must keep dropping: reviving the
    # shuffled-fold stacker or the double calibrator on reload would change
    # the flag-off blend with no flag and no measurement.
    m.stacking_learner = {"stand_in": "stacker"}
    m.double_calibrator = {"stand_in": "calibrator"}
    m.target_encoder = {"stand_in": "encoder"}
    m.save()
    raw = pickle.load(open(m.model_path, "rb"))
    assert set(raw) & {"stacking_learner", "double_calibrator", "target_encoder"} == set()
    assert isinstance(raw["ensemble_combiner"], dict) and raw["ensemble_combiner"]["is_fitted"]
    assert raw["ensemble_combiner"]["format"] == ec.STATE_FORMAT
    for attr in ("stacking_learner", "double_calibrator", "target_encoder"):
        delattr(m, attr)
    X = pd.DataFrame(np.random.default_rng(9).normal(size=(10, 3)), columns=["a", "b", "c"])
    back = RacingMLModel(model_path=m.model_path)
    assert back.is_trained
    assert getattr(back, "stacking_learner", None) is None
    assert getattr(back, "double_calibrator", None) is None
    assert isinstance(back.ensemble_combiner, ec.EnsembleCombiner) and back.ensemble_combiner.is_fitted
    for flag in ("false", "true"):
        monkeypatch.setenv("STRIDE_LEARNED_BLEND", flag)
        a, b = m.predict_components(X), back.predict_components(X)
        assert a["method"] == b["method"] == ("learned_blend" if flag == "true" else "weighted_average")
        assert np.array_equal(a["ensemble"], b["ensemble"]), "a fresh load predicts identically"


def test_legacy_artifact_without_the_keys_still_loads(monkeypatch, tmp_path):
    from ml_model import RacingMLModel
    legacy = {"xgb_model": _stand_in_model(1), "lgb_model": _stand_in_model(2), "cb_model": _stand_in_model(3),
              "feature_columns": ["a", "b", "c"], "feature_importance": {}, "version": "v2"}
    p = tmp_path / "legacy.pkl"
    pickle.dump(legacy, open(p, "wb"))
    monkeypatch.setenv("STRIDE_LEARNED_BLEND", "true")
    m = RacingMLModel(model_path=str(p))
    assert m.is_trained and m.ensemble_combiner is None and getattr(m, "stacking_learner", None) is None
    X = pd.DataFrame(np.random.default_rng(10).normal(size=(6, 3)), columns=["a", "b", "c"])
    assert m.predict_components(X)["method"] == "weighted_average"


def test_unrestorable_combiner_state_never_bricks_the_artifact(monkeypatch, tmp_path, capsys):
    from ml_model import RacingMLModel
    bad = {"xgb_model": _stand_in_model(1), "lgb_model": _stand_in_model(2), "cb_model": _stand_in_model(3),
           "feature_columns": ["a", "b", "c"], "feature_importance": {}, "version": "v3",
           "ensemble_combiner": {"format": ec.STATE_FORMAT, "method": "simplex", "model_names": ["xgb", "lgb", "cb"],
                                 "is_fitted": True, "weights": [1.0]}}
    p = tmp_path / "bad.pkl"
    pickle.dump(bad, open(p, "wb"))
    monkeypatch.setenv("STRIDE_LEARNED_BLEND", "true")
    m = RacingMLModel(model_path=str(p))
    assert m.is_trained and m.ensemble_combiner is None
    assert "could not be restored" in capsys.readouterr().err
    X = pd.DataFrame(np.random.default_rng(12).normal(size=(6, 3)), columns=["a", "b", "c"])
    assert m.predict_components(X)["method"] == "weighted_average"
