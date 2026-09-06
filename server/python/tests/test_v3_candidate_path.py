"""The v3 candidate path exists end to end, and production never sees it.

Before 2026-09-05 the promotion path in project_retrain_gate.md
("racing_ensemble_v3.pkl beside v2, one week parallel scoring") had no
mechanism: retrain_v2 hardcoded version "v2" (which the preflight freshness
gate REDs against a live v2), the retrain workflow had no way to select
snapshot odds or a v3 filename, RacingMLModel had no way to load a candidate,
and nothing scored one in parallel. These tests pin each piece and the
guardrails around it: production's slot is untouched, snapshot odds are fixed
in the workflow rather than chosen, a proof refuses to run without its
candidate staged, and the artifact carries its version.
"""

import importlib.util
import json
import os
import pickle
import re
import sys
import types
from pathlib import Path

import pytest

SERVER_PYTHON = Path(__file__).resolve().parents[1]
ROOT = SERVER_PYTHON.parents[1]
sys.path.insert(0, str(SERVER_PYTHON))

HANDLER_PATH = ROOT / "infra" / "jobs" / "handler.py"
VERIFY_JOBS = ROOT / ".github" / "workflows" / "verify-jobs.yml"
RETRAIN_WF = ROOT / ".github" / "workflows" / "retrain-model.yml"


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


def test_save_model_default_version_unchanged_and_v3_selectable(retrain, tmp_path):
    p = tmp_path / "a.pkl"
    retrain.save_model(None, None, None, ["a"], {"a": 1.0}, {"n_folds": 0}, {}, str(p))
    assert pickle.load(open(p, "rb"))["version"] == "v2"
    retrain.save_model(None, None, None, ["a"], {"a": 1.0}, {"n_folds": 0}, {}, str(p),
                       version="v3", extra={"odds_source_mode": "snapshot"})
    obj = pickle.load(open(p, "rb"))
    assert obj["version"] == "v3" and obj["odds_source_mode"] == "snapshot"
    for k in retrain_expected_keys():
        assert k in obj, k


def retrain_expected_keys():
    import retrain_preflight as rp
    return rp.EXPECTED_KEYS


def test_v3_artifact_passes_the_freshness_gate_against_a_live_v2(retrain, tmp_path):
    import joblib
    import retrain_preflight as rp
    live = tmp_path / "racing_ensemble_v2.pkl"
    retrain.save_model(None, None, None, ["a"], {"a": 1.0}, {}, {}, str(live))
    cand = tmp_path / "racing_ensemble_v3.pkl"
    retrain.save_model(None, None, None, ["a"], {"a": 1.0}, {}, {}, str(cand), version="v3")
    rows = rp.gate_freshness(joblib.load(cand), live)
    assert {r["name"]: r["status"] for r in rows}["freshness:version"] == rp.GREEN
    # and the old behaviour — same version — is exactly what the gate rejects
    same = tmp_path / "same.pkl"
    retrain.save_model(None, None, None, ["a"], {"a": 1.0}, {}, {}, str(same))
    rows = rp.gate_freshness(joblib.load(same), live)
    assert {r["name"]: r["status"] for r in rows}["freshness:version"] == rp.RED


def test_snapshot_floor_refuses_small_frames_and_is_off_by_default(retrain, capsys):
    import pandas as pd
    df = pd.DataFrame({"x": range(10)})
    assert retrain.enforce_snapshot_floor(df, None) == 10
    assert retrain.enforce_snapshot_floor(df, 10) == 10
    with pytest.raises(SystemExit) as e:
        retrain.enforce_snapshot_floor(df, 11)
    assert e.value.code == 2 and "REFUSAL" in capsys.readouterr().err


def test_report_strips_fitted_calibrators_and_keeps_race_metrics(retrain, tmp_path):
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression().fit([0.1, 0.5, 0.9], [0, 1, 1])
    cv = {"n_folds": 1, "mean_auc": 0.7, "oof_calibrators": {"xgb": iso},
          "race_metrics": {"model_top1_hit": 0.4}, "folds": [{"auc": 0.7, "hygiene": {"tail_rows": 30}}]}
    path = retrain.write_report(str(tmp_path / "r.json"), cv, {"delta_auc": 0.0}, {"a": 0.5},
                                {"version": "v3", "odds_source_mode": "snapshot"})
    rep = json.load(open(path))
    assert "oof_calibrators" not in rep["cv"]
    assert rep["cv"]["race_metrics"]["model_top1_hit"] == 0.4
    assert rep["cv"]["folds"][0]["hygiene"]["tail_rows"] == 30
    assert rep["meta"]["version"] == "v3" and rep["feature_importance"] == {"a": 0.5}


def test_cli_flags_have_safe_defaults(retrain, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["retrain_v2.py"])
    args = retrain.parse_args()
    assert args.model_version == "v2" and args.min_snapshot_rows is None and args.report_path is None
    monkeypatch.setattr(sys, "argv", ["retrain_v2.py", "--model-version", "v3",
                                      "--min-snapshot-rows", "2000", "--report-path", "r.json"])
    args = retrain.parse_args()
    assert (args.model_version, args.min_snapshot_rows, args.report_path) == ("v3", 2000, "r.json")


# ---------------------------------------------------------------- ml_model

def test_artifact_override_resolves_only_when_set(monkeypatch, tmp_path, capsys):
    from ml_model import RacingMLModel
    monkeypatch.delenv("STRIDE_ENSEMBLE_ARTIFACT", raising=False)
    default = RacingMLModel.resolve_artifact_path(None)
    assert default.endswith(os.path.join("models", "racing_ensemble_v2.pkl"))
    assert capsys.readouterr().err == "", "no override, no announcement"

    monkeypatch.setenv("STRIDE_ENSEMBLE_ARTIFACT", "racing_ensemble_v3.pkl")
    p = RacingMLModel.resolve_artifact_path(None)
    assert p.endswith(os.path.join("models", "racing_ensemble_v3.pkl"))
    err = capsys.readouterr().err
    assert "artifact override" in err and "WARNING: missing" in err

    absolute = tmp_path / "cand.pkl"
    absolute.write_bytes(b"x")
    monkeypatch.setenv("STRIDE_ENSEMBLE_ARTIFACT", str(absolute))
    assert RacingMLModel.resolve_artifact_path(None) == str(absolute)
    assert "WARNING" not in capsys.readouterr().err

    # an explicit model_path always wins over the environment
    assert RacingMLModel.resolve_artifact_path("/explicit/p.pkl") == "/explicit/p.pkl"


def test_constructor_uses_the_resolver(monkeypatch, tmp_path):
    from ml_model import RacingMLModel
    monkeypatch.setenv("STRIDE_ENSEMBLE_ARTIFACT", str(tmp_path / "nope" / "c.pkl"))
    m = RacingMLModel()
    assert m.model_path == str(tmp_path / "nope" / "c.pkl")
    assert m.is_trained is False


# ---------------------------------------------------------------- handler

class _StubBoto3:
    def __getattr__(self, name):
        raise AssertionError(f"boto3.{name} must not be called in this test")


@pytest.fixture(scope="module")
def handler():
    sys.modules.setdefault("boto3", _StubBoto3())
    spec = importlib.util.spec_from_file_location("stride_handler_v3_candidate", HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _proof_setup(handler, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("STRIDE_DATE", "2026-09-20")
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setattr(handler, "_root", lambda: str(tmp_path))
    monkeypatch.setattr(handler, "_tips_prepare", lambda: calls.append(("prepare",)) or "card")

    def run_ok(*args, **kwargs):
        calls.append(("run",) + args)
        suffix = args[args.index("--output-suffix") + 1]
        path = tmp_path / "racecards" / f"tips_2026-09-20_{suffix}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"races": [{"track": "R", "top_picks": [{"horse": "A", "ai_insight": ""}]}]}))
        return "ok"

    monkeypatch.setattr(handler, "_run_ok", run_ok)
    monkeypatch.setattr(handler, "_sync_up", lambda *a, **k: calls.append(("sync",) + a) or 1)
    return calls


def test_proof_refuses_when_the_candidate_is_not_staged(handler, monkeypatch, tmp_path):
    calls = _proof_setup(handler, monkeypatch, tmp_path)
    monkeypatch.setenv("STRIDE_ENSEMBLE_ARTIFACT", "racing_ensemble_v3.pkl")
    with pytest.raises(RuntimeError, match="was not staged"):
        handler.job_tips_proof()
    assert not any(c[0] == "run" for c in calls), "nothing may be scored without the candidate"


def test_proof_scores_with_the_candidate_under_its_own_suffix(handler, monkeypatch, tmp_path):
    calls = _proof_setup(handler, monkeypatch, tmp_path)
    staged = tmp_path / "server" / "python" / "models" / "racing_ensemble_v3.pkl"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"candidate")
    monkeypatch.setenv("STRIDE_ENSEMBLE_ARTIFACT", "racing_ensemble_v3.pkl")
    result = handler.job_tips_proof()
    run = [c for c in calls if c[0] == "run"][0]
    assert run == ("run", "run_tips_pipeline.py", "2026-09-20", "--skip-db-store",
                   "--output-suffix", "candidate")
    assert ("sync", "racecards", "tips_2026-09-20_candidate.json") in calls
    assert result["last_success_date"] == "2026-09-20"


def test_proof_without_override_is_unchanged(handler, monkeypatch, tmp_path):
    calls = _proof_setup(handler, monkeypatch, tmp_path)
    monkeypatch.delenv("STRIDE_ENSEMBLE_ARTIFACT", raising=False)
    handler.job_tips_proof()
    run = [c for c in calls if c[0] == "run"][0]
    assert run[-1] == "cloudproof"
    assert ("sync", "racecards", "tips_2026-09-20_cloudproof.json") in calls


def test_dispatch_announces_the_artifact_override(handler, monkeypatch, capsys):
    monkeypatch.setenv("STRIDE_JOB", "llm-proof")
    monkeypatch.delenv("STRIDE_DATE", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("STRIDE_ENSEMBLE_ARTIFACT", "racing_ensemble_v3.pkl")
    for name in ("_load_secrets", "_stage_models", "_stage_panel"):
        monkeypatch.setattr(handler, name, lambda: None)
    monkeypatch.setattr(handler, "_put_state", lambda job, **kw: None)
    monkeypatch.setitem(handler.JOBS, "llm-proof", lambda: {"last_success_date": "2026-09-20"})
    handler.dispatch()
    assert "STRIDE_ENSEMBLE_ARTIFACT=racing_ensemble_v3.pkl set on the task" in capsys.readouterr().out


def test_production_slot_is_untouched(handler):
    assert handler.REQUIRED_MODEL_ARTIFACTS == ("racing_ensemble_v2.pkl",)


# ---------------------------------------------------------------- workflows

def _run_block(path: Path, step_name: str):
    text = path.read_text(encoding="utf-8")
    start = text.index(f"- name: {step_name}")
    tail = text.find("\n      - name:", start + 10)
    block = text[start:] if tail == -1 else text[start:tail]
    run_at = block.index("\n        run:")
    env_at = block.find("\n        env:")
    env_names = (re.findall(r"^\s+([A-Z_][A-Z0-9_]*):", block[env_at:run_at], re.M)
                 if 0 <= env_at < run_at else [])
    return block[run_at:], env_names


def test_verify_jobs_can_pass_a_candidate_to_tips_proof():
    text = VERIFY_JOBS.read_text(encoding="utf-8")
    assert re.search(r"^\s+ensemble_artifact:\n\s+description:", text, re.M)
    body, env = _run_block(VERIFY_JOBS, "Run each job and report")
    assert "IN_ENSEMBLE_ARTIFACT" in env
    assert "STRIDE_ENSEMBLE_ARTIFACT" in body
    assert not re.search(r"\$\{\{", body), "inputs reach the shell as env, never interpolated"


def test_retrain_workflow_fixes_snapshot_odds_for_the_candidate():
    text = RETRAIN_WF.read_text(encoding="utf-8")
    assert re.search(r"^\s+mode:\n\s+description:", text, re.M)
    assert 'options: ["legacy-evidence", "v3-candidate"]' in text
    assert 'default: "legacy-evidence"' in text, "a careless dispatch must not produce a candidate"
    body, env = _run_block(RETRAIN_WF, "Retrain with walk-forward CV + ablation report")
    assert "export STRIDE_TRAIN_ODDS_SOURCE=snapshot" in body, "fixed in the file, not an input"
    inputs_block = text[text.index("    inputs:"):text.index("permissions:")]
    assert "STRIDE_TRAIN_ODDS_SOURCE" not in inputs_block and "odds_source" not in inputs_block, \
        "the odds source is not a dispatch input"
    assert "--model-version v3" in body and "models/racing_ensemble_v3.pkl" in body
    assert "--min-snapshot-rows" in body and "--report-path" in body
    assert "IN_MODE" in env and "IN_MIN_ROWS" in env
    assert not re.search(r"\$\{\{", body)


def test_retrain_workflow_builds_asof_profiles_and_gates_on_inputs_preflight():
    text = RETRAIN_WF.read_text(encoding="utf-8")
    prof_body, _ = _run_block(RETRAIN_WF, "Build as-of track-distance profiles (N3 guard)")
    assert "track_profiler.py --buckets" in prof_body
    assert "test -s intelligence/track_distance_profiles_asof.json" in prof_body
    pre_body, pre_env = _run_block(RETRAIN_WF, "Inputs-only preflight (gate 5)")
    assert "retrain_preflight.py --inputs-only" in pre_body
    assert 'if [ "$IN_MODE" = "v3-candidate" ] && [ "$RC" -ne 0 ]' in pre_body
    assert "exit 1" in pre_body
    # the profiles build runs BEFORE the preflight, which runs BEFORE training
    assert (text.index("Build as-of track-distance profiles") < text.index("Inputs-only preflight")
            < text.index("Retrain with walk-forward CV"))
    cand_body, _ = _run_block(RETRAIN_WF, "Candidate preflight (report — the live pkl is not on this runner)")
    assert "--staging models/racing_ensemble_v3.pkl" in cand_body


def test_compare_candidate_tips_self_test():
    import compare_candidate_tips as cct
    cct._self_test()
