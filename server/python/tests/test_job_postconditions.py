"""A job that did nothing must not report success.

stride_build.py, odds_movement.py and run_tips_pipeline.py have no
non-zero exit path anywhere in their source: they fail loudly on an
uncaught exception, but a semantic no-op — no racecard, no intelligence
file, no tips — exits 0. The scheduled job reads only the exit code, so
the whole morning could produce nothing and every alarm stay silent.

These pin the post-conditions that make that impossible. They exist
because the same class of defect was found in auto_results_collector,
which reported success:true with 8 of 8 races failed.
"""

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

HANDLER_PATH = (Path(__file__).resolve().parents[3]
                / "infra" / "jobs" / "handler.py")


class _StubBoto3:
    """Every boto3 call in the handler is inside a function body, so import
    only needs the name to exist. Stubbed rather than installed: these tests
    must stay offline and credential-free like the rest of the suite."""

    def __getattr__(self, name):
        raise AssertionError(f"boto3.{name} must not be called in this test")


@pytest.fixture(scope="module")
def handler():
    sys.modules.setdefault("boto3", _StubBoto3())
    spec = importlib.util.spec_from_file_location("stride_handler",
                                                  HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["stride_handler"] = mod
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------ _fresh_files

def test_fresh_files_excludes_pre_existing(handler, tmp_path):
    stale = tmp_path / "stale.json"
    stale.write_text("{}")
    # Backdate well clear of filesystem mtime granularity.
    old = time.time() - 3600
    os.utime(stale, (old, old))

    since = time.time()
    assert handler._fresh_files(str(tmp_path), ".json", since) == []

    fresh = tmp_path / "fresh.json"
    fresh.write_text("{}")
    assert handler._fresh_files(str(tmp_path), ".json", since) == ["fresh.json"]


def test_fresh_files_missing_dir_is_empty_not_error(handler, tmp_path):
    assert handler._fresh_files(str(tmp_path / "nope"), ".json", 0) == []


def test_fresh_files_filters_by_suffix(handler, tmp_path):
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "b.txt").write_text("x")
    got = handler._fresh_files(str(tmp_path), ".json", 0)
    assert got == ["a.json"]


# -------------------------------------------------------- _require_racecard

def test_require_racecard_raises_when_card_absent(handler, monkeypatch):
    monkeypatch.setattr(handler, "_prepare_racecard", lambda: False)
    with pytest.raises(RuntimeError) as e:
        handler._require_racecard("tips-pipeline")
    assert "no racecard" in str(e.value)
    assert "tips-pipeline" in str(e.value)


def test_require_racecard_passes_when_card_present(handler, monkeypatch):
    monkeypatch.setattr(handler, "_prepare_racecard", lambda: True)
    handler._require_racecard("tips-pipeline")   # must not raise


# --------------------------------------------------- job-level no-op guards

def _neutralise_io(handler, monkeypatch):
    monkeypatch.setattr(handler, "_sync_down", lambda *a, **k: None)
    monkeypatch.setattr(handler, "_sync_up", lambda *a, **k: None)
    monkeypatch.setattr(handler, "_prepare_racecard", lambda: True)
    monkeypatch.setattr(handler, "_run_ok", lambda *a, **k: "")


def test_intelligence_build_fails_when_nothing_was_built(handler, monkeypatch,
                                                         tmp_path):
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_root", lambda: str(tmp_path))
    os.makedirs(tmp_path / "server" / "python" / "intelligence")
    with pytest.raises(RuntimeError) as e:
        handler.job_intelligence_build()
    assert "wrote no" in str(e.value)


def test_intelligence_build_succeeds_when_a_file_appears(handler, monkeypatch,
                                                         tmp_path):
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_root", lambda: str(tmp_path))
    intel = tmp_path / "server" / "python" / "intelligence"
    os.makedirs(intel)
    monkeypatch.setattr(handler, "_run_ok",
                        lambda *a, **k: (intel / "form.json").write_text("{}"))
    out = handler.job_intelligence_build()
    assert out["files_built"] == 1


def test_tips_pipeline_fails_when_no_tips_file(handler, monkeypatch, tmp_path):
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_root", lambda: str(tmp_path))
    with pytest.raises(RuntimeError) as e:
        handler.job_tips_pipeline()
    assert "absent after a clean exit" in str(e.value)


def test_tips_pipeline_passes_with_tips_file(handler, monkeypatch, tmp_path):
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_root", lambda: str(tmp_path))
    os.makedirs(tmp_path / "racecards")
    (tmp_path / "racecards" / f"tips_{handler._today()}.json").write_text("{}")
    handler.job_tips_pipeline()   # must not raise


def test_consensus_fails_when_no_consensus_file(handler, monkeypatch, tmp_path):
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_root", lambda: str(tmp_path))
    with pytest.raises(RuntimeError) as e:
        handler.job_consensus_agent()
    assert "NO_BET" in str(e.value)


def test_consensus_passes_with_consensus_file(handler, monkeypatch, tmp_path):
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_root", lambda: str(tmp_path))
    intel = tmp_path / "server" / "python" / "intelligence"
    os.makedirs(intel)
    (intel / f"consensus_{handler._today()}.json").write_text("{}")
    handler.job_consensus_agent()   # must not raise


def test_every_card_dependent_job_requires_the_card(handler, monkeypatch):
    """The guard must be wired into each card-dependent job, not just exist.

    A missing 05:30 racecard is the single upstream failure that would
    otherwise let build, consensus and tips all no-op in sequence.
    """
    monkeypatch.setattr(handler, "_sync_down", lambda *a, **k: None)
    monkeypatch.setattr(handler, "_sync_up", lambda *a, **k: None)
    monkeypatch.setattr(handler, "_run_ok", lambda *a, **k: "")
    monkeypatch.setattr(handler, "_prepare_racecard", lambda: False)
    for job in (handler.job_intelligence_build,
                handler.job_consensus_agent,
                handler.job_tips_pipeline):
        with pytest.raises(RuntimeError) as e:
            job()
        assert "no racecard" in str(e.value), job.__name__
