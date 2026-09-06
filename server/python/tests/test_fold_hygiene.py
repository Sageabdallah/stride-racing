"""The outer test fold is read only to be scored.

Until 2026-09-05 retrain_v2.train_single_fold early-stopped LightGBM on the
test fold, let CatBoost select its best iteration on the test fold
(use_best_model defaults to True with an eval_set), and fitted all three
per-model isotonics on the test fold's labels — so the published fold
AUC/Brier were mildly self-fitted. These tests pin the carve-out: early
stopping and isotonic use a tail of the TRAINING window; nothing touches the
test fold's labels before scoring; degenerate tails degrade loudly, never by
borrowing test rows.
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
    fake = types.ModuleType("psycopg2")
    extras = types.ModuleType("psycopg2.extras")
    fake.extras = extras
    sys.modules.setdefault("psycopg2", fake)
    sys.modules.setdefault("psycopg2.extras", extras)
    os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost/unused")
    import retrain_v2
    return retrain_v2


FEATS = ["f1", "f2", "f3", "f4"]


def _frame(n_days=90, races_per_day=2, seed=5, dead_tail_days=0):
    """Date-ordered synthetic runners; dead_tail_days > 0 makes the last N
    days winner-free so the date tail is degenerate."""
    rng = np.random.default_rng(seed)
    rows = []
    base = pd.Timestamp("2026-02-01")
    for d in range(n_days):
        for r in range(races_per_day):
            n = int(rng.integers(6, 10))
            strength = rng.normal(0, 1, n)
            prob = np.exp(strength) / np.exp(strength).sum()
            w = int(rng.choice(n, p=prob))
            for i in range(n):
                won = int(i == w) if d < n_days - dead_tail_days else 0
                rows.append({"race_date": base + pd.Timedelta(days=d),
                             "f1": strength[i] + rng.normal(0, 0.5), "f2": strength[i] + rng.normal(0, 1),
                             "f3": rng.normal(), "f4": rng.normal(), "is_winner": won})
    return pd.DataFrame(rows)


def _split(df, test_from_day=76, purge_days=14):
    dates = pd.to_datetime(df["race_date"])
    d0 = dates.min()
    train = dates < d0 + pd.Timedelta(days=test_from_day - purge_days)
    test = dates >= d0 + pd.Timedelta(days=test_from_day)
    return df[train], df[test]


class _RecordingIsotonic:
    """Stands in for sklearn's IsotonicRegression and records every y it sees."""
    seen = []

    def __init__(self, out_of_bounds="clip"):
        self.out_of_bounds = out_of_bounds

    def fit(self, x, y):
        _RecordingIsotonic.seen.append(np.asarray(y).copy())
        self._x = np.asarray(x)
        return self

    def transform(self, x):
        return np.asarray(x)


def test_carve_tail_takes_the_last_days_of_the_train_window(retrain):
    df = _frame()
    tr, te = _split(df)
    y = tr["is_winner"].to_numpy()
    mask, rec = retrain._carve_tail(len(tr), y, tr["race_date"])
    assert rec["fallback"] is None and rec["early_stopping"] and rec["isotonic_on"] == "tail"
    tail_dates = pd.to_datetime(tr["race_date"]).to_numpy()[mask]
    assert (tail_dates.max() - tail_dates.min()) <= np.timedelta64(retrain.TAIL_DAYS - 1, "D")
    assert tail_dates.max() == pd.to_datetime(tr["race_date"]).max().to_datetime64()
    assert rec["tail_rows"] == int(mask.sum()) and rec["fit_rows"] == len(tr) - int(mask.sum())
    # the tail ends before the purge gap, so strictly before the test fold
    assert pd.Timestamp(rec["tail_end"]) < pd.to_datetime(te["race_date"]).min()


def test_carve_tail_falls_back_positionally_then_to_none(retrain):
    df = _frame(dead_tail_days=20)           # last 20 days have no winners
    tr, _ = _split(df, test_from_day=90, purge_days=0)
    y = tr["is_winner"].to_numpy()
    mask, rec = retrain._carve_tail(len(tr), y, tr["race_date"])
    # the date tail (14 days) is winner-free -> positional 10% is also inside the dead zone
    assert rec["fallback"] == "none" and not rec["early_stopping"] and rec["isotonic_on"] == "none"
    assert not mask.any()

    df2 = _frame()
    y2 = df2["is_winner"].to_numpy()
    mask2, rec2 = retrain._carve_tail(len(df2), y2, None)   # legacy call: no dates
    assert rec2["fallback"] == "positional" and rec2["early_stopping"]
    assert int(mask2.sum()) == round(len(df2) * 0.10)


def test_isotonic_never_sees_test_fold_labels(retrain, monkeypatch):
    df = _frame()
    tr, te = _split(df)
    _RecordingIsotonic.seen = []
    monkeypatch.setattr(retrain, "IsotonicRegression", _RecordingIsotonic)
    y_tr, y_te = tr["is_winner"].reset_index(drop=True), te["is_winner"].reset_index(drop=True)
    xgb_m, lgb_m, cb_m, per_auc, oof = retrain.train_single_fold(
        tr[FEATS].reset_index(drop=True), y_tr, te[FEATS].reset_index(drop=True), y_te,
        FEATS, dates_train=tr["race_date"].reset_index(drop=True))
    n_models = sum(m is not None for m in (xgb_m, lgb_m, cb_m))
    assert n_models >= 1
    assert len(_RecordingIsotonic.seen) == n_models
    tail_rows = oof["hygiene"]["tail_rows"]
    y_test = y_te.to_numpy()
    for seen in _RecordingIsotonic.seen:
        assert len(seen) == tail_rows, "isotonic must be fitted on the train tail"
        assert len(seen) != len(y_test) or not np.array_equal(seen, y_test)
    # raw OOF predictions are for the test fold, one per test row
    for key in ("xgb", "lgb", "cb"):
        if oof[key]:
            assert len(oof[key]) == len(te)
    assert oof["labels"] == y_test.tolist()


def test_lightgbm_early_stops_on_the_tail_not_the_test_fold(retrain, monkeypatch):
    df = _frame()
    tr, te = _split(df)
    seen_eval = {}
    real_fit = retrain.lgb.LGBMClassifier.fit

    def spy(self, X, y, **kw):
        seen_eval["eval_rows"] = len(kw["eval_set"][0][0]) if kw.get("eval_set") else 0
        return real_fit(self, X, y, **kw)

    monkeypatch.setattr(retrain.lgb.LGBMClassifier, "fit", spy)
    y_tr, y_te = tr["is_winner"].reset_index(drop=True), te["is_winner"].reset_index(drop=True)
    _, lgb_m, _, _, oof = retrain.train_single_fold(
        tr[FEATS].reset_index(drop=True), y_tr, te[FEATS].reset_index(drop=True), y_te,
        FEATS, dates_train=tr["race_date"].reset_index(drop=True))
    assert seen_eval["eval_rows"] == oof["hygiene"]["tail_rows"]
    assert seen_eval["eval_rows"] != len(te)
    assert lgb_m.best_iteration_ is not None and lgb_m.best_iteration_ >= 1


def test_degenerate_tail_disables_early_stopping_and_isotonic_loudly(retrain, monkeypatch):
    df = _frame(dead_tail_days=20)
    tr, te = _split(df, test_from_day=90, purge_days=0)
    te = _frame(n_days=10, seed=9)             # a healthy test fold with winners
    _RecordingIsotonic.seen = []
    monkeypatch.setattr(retrain, "IsotonicRegression", _RecordingIsotonic)
    y_tr, y_te = tr["is_winner"].reset_index(drop=True), te["is_winner"].reset_index(drop=True)
    xgb_m, lgb_m, cb_m, per_auc, oof = retrain.train_single_fold(
        tr[FEATS].reset_index(drop=True), y_tr, te[FEATS].reset_index(drop=True), y_te,
        FEATS, dates_train=tr["race_date"].reset_index(drop=True))
    assert oof["hygiene"]["fallback"] == "none"
    assert _RecordingIsotonic.seen == [], "no tail -> no isotonic, not one fitted on the test fold"
    for m in (xgb_m, lgb_m, cb_m):
        if m is not None:
            assert not hasattr(m, "_isotonic")
    # predict_ensemble tolerates models without _isotonic
    p = retrain.predict_ensemble(xgb_m, lgb_m, cb_m, te[FEATS].reset_index(drop=True), FEATS)
    assert p.shape[0] == len(te) and np.all((p >= 0) & (p <= 1))


def test_walk_forward_cv_records_hygiene_per_fold(retrain):
    df = _frame(n_days=120)
    X = df[FEATS].reset_index(drop=True)
    y = df["is_winner"].astype(int).reset_index(drop=True)
    dates = pd.to_datetime(df["race_date"]).reset_index(drop=True)
    res = retrain.run_walk_forward_cv(X, y, dates, FEATS, label="h")
    assert res["n_folds"] >= 1
    for f in res["folds"]:
        h = f["hygiene"]
        assert h["fallback"] is None and h["early_stopping"] and h["isotonic_on"] == "tail"
        assert pd.Timestamp(h["tail_end"]) < pd.Timestamp(f["test_start"]), \
            "the tail must end before the test window (the purge gap sits between)"


def test_final_model_stops_early_on_the_last_ten_percent(retrain):
    df = _frame(n_days=120)
    X = df[FEATS].reset_index(drop=True)
    y = df["is_winner"].astype(int).reset_index(drop=True)
    xgb_m, lgb_m, cb_m, imp = retrain.train_final_model(X, y, FEATS, oof_calibrators=None)
    if lgb_m is not None:
        assert lgb_m.best_iteration_ is not None
    if xgb_m is not None:
        # xgboost exposes best_iteration once early stopping was configured
        assert getattr(xgb_m, "best_iteration", None) is not None
    if cb_m is not None:
        assert cb_m.get_best_iteration() is not None
    assert set(imp) == set(FEATS)


def test_tail_calibrators_survive_save_and_serve(retrain, tmp_path, monkeypatch):
    """Exercise the fold -> explicit payload -> serve contract with real trees.

    CI can have only LightGBM; a full ML environment verifies all three,
    including CatBoost's loss of the attached attribute during pickling.
    """
    import pickle
    from ml_model import RacingMLModel

    monkeypatch.setenv("STRIDE_ML_APPLY_ISOTONIC", "true")
    monkeypatch.setenv("STRIDE_LEARNED_BLEND", "false")
    tr, te = _split(_frame())
    xgb, lgb, cb, _, oof = retrain.train_single_fold(
        tr[FEATS], tr["is_winner"], te[FEATS], te["is_winner"], FEATS,
        dates_train=tr["race_date"])
    assert oof["hygiene"]["isotonic_on"] == "tail"
    models = {k: m for k, m in (("xgb", xgb), ("lgb", lgb), ("cb", cb)) if m is not None}
    assert models
    path = tmp_path / "tail.pkl"
    retrain.save_model(xgb, lgb, cb, FEATS, {}, {}, {}, str(path), version="v3")
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    assert set(payload["oof_calibrators"]) == set(models)
    if all((retrain.HAS_XGB, retrain.HAS_LGB, retrain.HAS_CB)):
        assert set(payload["oof_calibrators"]) == {"xgb", "lgb", "cb"}
    if cb is not None:
        assert not hasattr(payload["cb_model"], "_isotonic")
    loaded = RacingMLModel(model_path=str(path))
    assert loaded.is_trained and loaded.serve_calibration_status()["applied"]
    out = loaded.predict_components(te[FEATS])
    assert out["method"] == "weighted_average+isotonic"
    for key, slot in (("xgb", "xgb"), ("lgb", "lightgbm"), ("cb", "catboost")):
        if key in models:
            expected = models[key]._isotonic.transform(np.asarray(oof[key]))
            assert np.array_equal(out[slot], expected)
