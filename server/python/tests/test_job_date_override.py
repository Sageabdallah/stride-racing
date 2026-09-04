"""STRIDE_DATE lets the Fargate chain be run for a chosen day.

Every job keys its racecard, intelligence, consensus and tips artifacts off
handler._today(), which was the Sydney calendar date and nothing else, so the
chain could only ever be run for the day it was running on. An operator who
wants Saturday's card built on Friday afternoon (a preview for people asking
for tips early) or a day rebuilt after the schedule missed it had no lever.
The override is one env var on a hand-dispatched task, honoured in the one
function every job reads the date from. tips-proof, the out-of-slot tips
variant that writes nothing to the database, now relays its suffixed output
to S3 so a preview run has an artifact that outlives the task.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HANDLER_PATH = ROOT / "infra" / "jobs" / "handler.py"
VERIFY_JOBS = ROOT / ".github" / "workflows" / "verify-jobs.yml"


class _StubBoto3:
    def __getattr__(self, name):
        raise AssertionError(f"boto3.{name} must not be called in this test")


@pytest.fixture(scope="module")
def handler():
    sys.modules.setdefault("boto3", _StubBoto3())
    spec = importlib.util.spec_from_file_location("stride_handler_date_override", HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_today_is_the_sydney_date_unless_overridden(handler, monkeypatch):
    monkeypatch.delenv("STRIDE_DATE", raising=False)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", handler._today())
    monkeypatch.setenv("STRIDE_DATE", "2026-09-05")
    assert handler._today() == "2026-09-05"
    monkeypatch.setenv("STRIDE_DATE", "  2026-09-05  ")
    assert handler._today() == "2026-09-05", "whitespace from a workflow input must not leak into file names"
    monkeypatch.setenv("STRIDE_DATE", "")
    assert handler._today() != "", "an empty override means no override"


def test_a_non_date_override_is_refused_not_used_as_a_filename(handler, monkeypatch):
    monkeypatch.setenv("STRIDE_DATE", "saturday")
    with pytest.raises(ValueError):
        handler._today()


def test_tips_proof_runs_for_the_overridden_date_and_relays_its_output(handler, monkeypatch):
    monkeypatch.setenv("STRIDE_DATE", "2026-09-05")
    calls = {"run": [], "sync": []}
    monkeypatch.setattr(handler, "_tips_prepare", lambda: None)
    monkeypatch.setattr(handler, "_run_ok", lambda *a, **k: calls["run"].append(a) or "ok")
    monkeypatch.setattr(handler, "_sync_up", lambda *a, **k: calls["sync"].append(a) or 1)

    result = handler.job_tips_proof()

    assert calls["run"] == [("run_tips_pipeline.py", "2026-09-05",
                             "--skip-db-store", "--output-suffix", "cloudproof")], \
        "the proof run must stay database-free and write the suffixed file, for the chosen date"
    assert calls["sync"] == [("racecards", "tips_2026-09-05_cloudproof.json")], \
        "the suffixed file must leave the task, and only that file"
    assert result["last_success_date"] == "2026-09-05"


def test_verify_jobs_workflow_can_pass_the_date_through():
    text = VERIFY_JOBS.read_text()
    assert re.search(r"^\s+date:\n\s+description:", text, re.M), "verify-jobs has no date input"
    assert 'STRIDE_DATE' in text and 'inputs.date' in text, \
        "the date input must reach the container as STRIDE_DATE"


def _quiet_dispatch(handler, monkeypatch, puts):
    monkeypatch.setenv("STRIDE_JOB", "tips-proof")
    monkeypatch.setattr(handler, "_load_secrets", lambda: None)
    monkeypatch.setattr(handler, "_stage_models", lambda: None)
    monkeypatch.setattr(handler, "_stage_panel", lambda: None)
    monkeypatch.setattr(handler, "_put_state", lambda job, **kw: puts.append((job, kw)))
    monkeypatch.setitem(handler.JOBS, "tips-proof", lambda: {"last_success_date": handler._today()})


def test_a_preview_run_keeps_its_own_run_state_row(handler, monkeypatch):
    puts = []
    _quiet_dispatch(handler, monkeypatch, puts)
    monkeypatch.setenv("STRIDE_DATE", "2026-09-05")
    handler.dispatch()
    assert puts == [("tips-proof~preview", {"last_success_date": "2026-09-05"})], \
        "a preview must never stamp the real job's row: missing-run-watch reads it as 'ran today'"


def test_a_scheduled_run_still_writes_the_real_row(handler, monkeypatch):
    puts = []
    _quiet_dispatch(handler, monkeypatch, puts)
    monkeypatch.delenv("STRIDE_DATE", raising=False)
    handler.dispatch()
    assert [j for j, _ in puts] == ["tips-proof"]
