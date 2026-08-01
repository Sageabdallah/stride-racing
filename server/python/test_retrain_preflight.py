#!/usr/bin/env python3
"""Tests for retrain_preflight.py — every RED/AMBER path driven by fixture
pkls (tiny dicts written by the test), no production artifact, no DB."""

import json
import os
import sys
from pathlib import Path

import joblib
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import retrain_preflight as rp


CODE_COLS = ["a", "b", "c"]


def _write_pkl(path: Path, **overrides):
    payload = {
        "xgb_model": object(),
        "lgb_model": object(),
        "cb_model": object(),
        "feature_columns": list(CODE_COLS),
        "feature_importance": {"a": 0.5, "b": 0.3, "c": 0.2},
        "trained_at": "2026-08-01T10:00:00",
        "version": "v3",
    }
    payload.update(overrides)
    joblib.dump(payload, path)
    return path


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """models/ + live pkl + staging candidate + backup, all in tmp."""
    models = tmp_path / "models"
    backups = models / "backups"
    backups.mkdir(parents=True)
    live = _write_pkl(models / "racing_ensemble_v2.pkl",
                      trained_at="2026-04-15T09:00:00", version="v2")
    (backups / "racing_ensemble_v2_20260701_120000.pkl").write_bytes(b"backup")
    staging = _write_pkl(tmp_path / "candidate.pkl")
    return {"models": models, "backups": backups, "live": live, "staging": staging}


def _status(rows, name):
    return next(r["status"] for r in rows if r["name"] == name)


# ------------------------------------------------------------- gate 1: paths

def test_live_path_refusal(layout):
    rows = rp.gate_paths(layout["live"], layout["live"], layout["backups"])
    assert _status(rows, "staging!=live") == rp.RED
    assert "refusing" in next(r["detail"] for r in rows if r["name"] == "staging!=live")


def test_missing_backup(layout):
    for f in layout["backups"].glob("*.pkl"):
        f.unlink()
    rows = rp.gate_paths(layout["staging"], layout["live"], layout["backups"])
    assert _status(rows, "staging!=live") == rp.GREEN
    assert _status(rows, "live-backup") == rp.RED


def test_backup_present_named(layout):
    rows = rp.gate_paths(layout["staging"], layout["live"], layout["backups"])
    row = next(r for r in rows if r["name"] == "live-backup")
    assert row["status"] == rp.GREEN
    assert "racing_ensemble_v2_20260701_120000.pkl" in row["detail"]


# ------------------------------------------------------------ gate 2: loads

def test_wrong_keys(tmp_path):
    bad = _write_pkl(tmp_path / "bad.pkl")
    joblib.dump({"xgb_model": object()}, bad)
    obj, rows = rp.gate_loads(bad)
    assert _status(rows, "artifact-loads") == rp.RED
    assert "missing keys" in rows[0]["detail"]


def test_loads_green(layout):
    obj, rows = rp.gate_loads(layout["staging"])
    assert _status(rows, "artifact-loads") == rp.GREEN


# ---------------------------------------------------------- gate 3: liveness

def test_zero_at_serve_red(layout, monkeypatch):
    import feature_liveness_audit as fla
    monkeypatch.setattr(fla, "audit_static", lambda cols=None: {
        "n_features": 3,
        "verdict_counts": {"ZERO_AT_SERVE": 2, "LIVE_BOTH": 1},
        "features": [
            {"feature": "ghost1", "verdict": "ZERO_AT_SERVE"},
            {"feature": "ghost2", "verdict": "ZERO_AT_SERVE"},
        ],
    })
    rows = rp.gate_liveness(layout["staging"], CODE_COLS)
    assert _status(rows, "liveness:serve") == rp.RED
    assert _status(rows, "liveness:trained") == rp.GREEN


def test_in_code_not_in_pkl_red(layout):
    rows = rp.gate_liveness(layout["staging"], CODE_COLS + ["untrained_new_feature"])
    assert _status(rows, "liveness:trained") == rp.RED


# ---------------------------------------------------------- gate 4: lockstep

def _write_cols_file(path: Path, cols, nan_preserve=None, class_attr=False):
    if class_attr:
        text = "class M:\n    FEATURE_COLUMNS = %r\n" % (cols,)
        if nan_preserve is not None:
            text += "    NAN_PRESERVE_FEATURES = %r\n" % (nan_preserve,)
    else:
        text = "FEATURE_COLUMNS = %r\n" % (cols,)
        if nan_preserve is not None:
            text += "NAN_PRESERVE_FEATURES = %r\n" % (nan_preserve,)
    path.write_text(text)
    return path


def test_lockstep_mismatch(tmp_path, layout):
    retrain_f = _write_cols_file(tmp_path / "retrain_v2.py", CODE_COLS)
    model_f = _write_cols_file(tmp_path / "ml_model.py", CODE_COLS + ["extra"],
                               class_attr=True)
    obj = joblib.load(layout["staging"])
    rows, _ = rp.gate_lockstep(obj, retrain_f, model_f)
    assert _status(rows, "lockstep:columns") == rp.RED


def test_lockstep_green(tmp_path, layout):
    retrain_f = _write_cols_file(tmp_path / "retrain_v2.py", CODE_COLS, ["np1"])
    model_f = _write_cols_file(tmp_path / "ml_model.py", CODE_COLS, class_attr=True)
    obj = joblib.load(layout["staging"])
    rows, cols = rp.gate_lockstep(obj, retrain_f, model_f)
    assert _status(rows, "lockstep:columns") == rp.GREEN
    assert cols == CODE_COLS
    # ml_model defines no NAN_PRESERVE here -> noted, not a failure
    assert _status(rows, "lockstep:nan-preserve") == rp.GREEN


def test_nan_preserve_mismatch(tmp_path, layout):
    retrain_f = _write_cols_file(tmp_path / "retrain_v2.py", CODE_COLS, ["np1", "np2"])
    model_f = _write_cols_file(tmp_path / "ml_model.py", CODE_COLS, ["np1"],
                               class_attr=True)
    obj = joblib.load(layout["staging"])
    rows, _ = rp.gate_lockstep(obj, retrain_f, model_f)
    assert _status(rows, "lockstep:nan-preserve") == rp.RED


# ------------------------------------------------------- gate 5: parity suites

def test_parity_absent_amber(tmp_path):
    # Point every suite at a path that does not exist — the repo copies now
    # land with roi/03, so "absent" must be simulated, not assumed.
    missing = {name: str(tmp_path / name) for name in rp.PARITY_TESTS}
    rows = rp.gate_parity_suites(missing)
    assert all(r["status"] == rp.AMBER for r in rows)
    assert all("not present" in r["detail"] for r in rows)


def test_parity_runs_passing_file(tmp_path):
    suite = tmp_path / "test_feature_parity.py"
    suite.write_text("def test_ok():\n    assert True\n")
    rows = rp.gate_parity_suites(
        {"test_feature_parity.py": str(suite)})
    row = next(r for r in rows if r["name"] == "parity:test_feature_parity.py")
    assert row["status"] == rp.GREEN
    other = next(r for r in rows if r["name"] == "parity:test_serve_feature_liveness.py")
    assert other["status"] == rp.AMBER  # absent stays visible


# ---------------------------------------------------------- gate 6: freshness

def test_stale_trained_at_red(tmp_path, layout):
    stale = _write_pkl(tmp_path / "stale.pkl", trained_at="2026-01-01T00:00:00")
    obj = joblib.load(stale)
    rows = rp.gate_freshness(obj, layout["live"])
    assert _status(rows, "freshness:trained_at") == rp.RED
    assert _status(rows, "freshness:version") == rp.GREEN


def test_same_version_red(tmp_path, layout):
    same = _write_pkl(tmp_path / "same.pkl", version="v2")
    rows = rp.gate_freshness(joblib.load(same), layout["live"])
    assert _status(rows, "freshness:version") == rp.RED


def test_freshness_green(layout):
    rows = rp.gate_freshness(joblib.load(layout["staging"]), layout["live"])
    assert all(r["status"] == rp.GREEN for r in rows)


# ------------------------------------------------------ board 2: decision inputs

def test_shadow_metrics_absent_amber():
    assert gate_shadow_metrics_status(None) == rp.AMBER


def gate_shadow_metrics_status(path):
    return rp.gate_shadow_metrics(path)[0]["status"]


def test_shadow_metrics_ship_and_hold(tmp_path):
    good = tmp_path / "ship.json"
    good.write_text(json.dumps({
        "lever": "ROI",
        "baseline": {"roi_pct": 5.0, "strike_rate": 0.20, "brier": 0.20},
        "candidate": {"n_bets": 500, "roi_pct": 8.0, "strike_rate": 0.21,
                      "brier": 0.19, "mean_clv_pct": 0.5,
                      "roi_ci95_bootstrap": {"spans_zero": False}},
    }))
    assert gate_shadow_metrics_status(str(good)) == rp.GREEN

    bad = tmp_path / "hold.json"
    bad.write_text(json.dumps({
        "lever": "ROI",
        "baseline": {"roi_pct": 9.0, "strike_rate": 0.20, "brier": 0.20},
        "candidate": {"n_bets": 500, "roi_pct": 1.0, "strike_rate": 0.10,
                      "brier": 0.25, "mean_clv_pct": -1.0,
                      "roi_ci95_bootstrap": {"spans_zero": True}},
    }))
    assert gate_shadow_metrics_status(str(bad)) == rp.RED


def test_preregistration_markers(tmp_path):
    doc = tmp_path / "12-preregistration.md"
    doc.write_text("# Plan\n\n[SAGE-APPROVAL] confirm band\n[SAGE-APPROVAL] confirm window\n")
    row = rp.gate_preregistration(doc)[0]
    assert row["status"] == rp.AMBER
    assert "2 unresolved" in row["detail"]

    doc.write_text("# Plan\n\nAll approved.\n")
    assert rp.gate_preregistration(doc)[0]["status"] == rp.GREEN

    assert rp.gate_preregistration(tmp_path / "missing.md")[0]["status"] == rp.AMBER


# ------------------------------------------------------------- full assembly

def test_full_run_blocked_and_exit_code(layout, monkeypatch, tmp_path):
    import feature_liveness_audit as fla
    # Force the liveness board green so the only non-GREEN marks are PENDs.
    monkeypatch.setattr(fla, "audit_static", lambda cols=None: {
        "n_features": 3, "verdict_counts": {"LIVE_BOTH": 3}, "features": []})
    retrain_f = _write_cols_file(tmp_path / "retrain_v2.py", CODE_COLS)
    model_f = _write_cols_file(tmp_path / "ml_model.py", CODE_COLS, class_attr=True)
    boards = rp.run_preflight(str(layout["staging"]),
                              live_pkl=layout["live"],
                              backups_dir=layout["backups"],
                              prereg_path=layout["models"] / "nope.md",
                              parity_test_paths={},
                              retrain_path=retrain_f, ml_model_path=model_f)
    out = rp.render(boards)
    # No RED (parity absent = PEND, prereg missing = PEND, metrics absent = PEND)
    assert "PROMOTION: cleared pending" in out
    assert all(r["status"] != rp.RED for r in boards["board1"])

    # Point --staging at the live pkl: full refusal, nonzero exit path.
    boards2 = rp.run_preflight(str(layout["live"]),
                               live_pkl=layout["live"],
                               backups_dir=layout["backups"],
                               prereg_path=layout["models"] / "nope.md",
                               parity_test_paths={},
                               retrain_path=retrain_f, ml_model_path=model_f)
    out2 = rp.render(boards2)
    assert "PROMOTION: BLOCKED" in out2
    assert any(r["status"] == rp.RED and r["name"] == "staging!=live"
               for r in boards2["board1"])
