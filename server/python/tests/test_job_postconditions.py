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
    monkeypatch.setattr(handler, "_prepare_racecard", lambda: "missing")
    with pytest.raises(RuntimeError) as e:
        handler._require_racecard("tips-pipeline")
    assert "no racecard" in str(e.value)
    assert "tips-pipeline" in str(e.value)


def test_require_racecard_passes_when_card_present(handler, monkeypatch):
    monkeypatch.setattr(handler, "_prepare_racecard", lambda: "card")
    assert handler._require_racecard("tips-pipeline") == "card"   # must not raise


def test_require_racecard_passes_on_a_quiet_day(handler, monkeypatch, capsys):
    """A quiet day is the one case where no card is correct: the provider was
    healthy and listed meetings, none on the target-track list. Measured over
    the 75 days to 2026-08-03 that was 28 of them, so raising here took four
    jobs red about three mornings a week for a calendar fact."""
    monkeypatch.setattr(handler, "_prepare_racecard", lambda: "quiet")
    assert handler._require_racecard("tips-pipeline") == "quiet"
    assert "QUIET DAY" in capsys.readouterr().out


def test_prepare_racecard_tri_state_is_driven_by_the_filesystem(handler, monkeypatch, tmp_path):
    """The three answers come from real files, not from a stub. "missing" and
    "quiet" both mean "no card" and need opposite handling, so the thing that
    tells them apart has to be exercised for real."""
    monkeypatch.setenv("LAMBDA_TASK_ROOT", str(tmp_path))
    monkeypatch.setattr(handler, "_sync_down", lambda *a, **k: 0)
    src = tmp_path / "server" / "python" / "racecards"
    src.mkdir(parents=True)
    today = handler._today()

    assert handler._prepare_racecard() == "missing"

    (src / f"quiet_{today}.json").write_text('{"status": "quiet"}')
    assert handler._prepare_racecard() == "quiet"
    # Relayed to the repo-root location so anything resolving the card path
    # sees the same evidence the handler did.
    assert (tmp_path / "racecards" / f"quiet_{today}.json").exists()

    # A card present alongside a stale sentinel is still a card: a leftover
    # sentinel must never suppress a real day's work.
    (src / f"racecard_{today}.json").write_text("[]")
    assert handler._prepare_racecard() == "card"


def test_a_sentinel_for_another_date_is_not_todays_quiet_day(handler, monkeypatch, tmp_path):
    monkeypatch.setenv("LAMBDA_TASK_ROOT", str(tmp_path))
    monkeypatch.setattr(handler, "_sync_down", lambda *a, **k: 0)
    src = tmp_path / "server" / "python" / "racecards"
    src.mkdir(parents=True)
    (src / "quiet_1999-01-01.json").write_text('{"status": "quiet"}')
    assert handler._prepare_racecard() == "missing"


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


def test_morning_odds_fails_when_no_snapshot_rows_committed(handler,
                                                            monkeypatch):
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_db_now", lambda: 0, raising=False)
    monkeypatch.setattr(handler, "_db_query", lambda sql, p: (0,))
    with pytest.raises(RuntimeError) as e:
        handler.job_morning_odds()
    assert "no MORNING_CHECK rows" in str(e.value)


def test_morning_odds_fails_on_baseline_present_but_zero_signals(handler,
                                                                 monkeypatch):
    """The 2026-04-06 shape: prices stored, signals silently absent.

    272 morning rows landed against a 230-row baseline and produced zero
    market_signal_scores, because the baseline join matched nothing and
    every runner fell back to neutral. It passed green at the time.
    """
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_db_now", lambda: 0, raising=False)
    counts = iter([(272,), (230,), (0,)])   # morning, baseline, signals
    monkeypatch.setattr(handler, "_db_query", lambda sql, p: next(counts))
    with pytest.raises(RuntimeError) as e:
        handler.job_morning_odds()
    assert "ZERO market_signal_scores" in str(e.value)


def test_morning_odds_allows_zero_signals_when_no_baseline(handler,
                                                           monkeypatch):
    """Day one has no baseline, so zero signals is correct, not a failure.

    This is the assertion that keeps the check from becoming a daily false
    alarm — the failure mode that trains an operator to ignore alarms.
    """
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_db_now", lambda: 0, raising=False)
    counts = iter([(272,), (0,), (0,)])     # morning, no baseline, no signals
    monkeypatch.setattr(handler, "_db_query", lambda sql, p: next(counts))
    out = handler.job_morning_odds()
    assert out["morning_rows"] == 272
    assert out["signal_rows"] == 0


def test_results_collect_treats_a_timeout_on_today_as_non_fatal(handler,
                                                                monkeypatch):
    """_run's 840s cap raises TimeoutExpired, a SubprocessError.

    Catching only RuntimeError let it walk past both handlers and hard-fail
    the nightly job — the opposite of the documented today/yesterday
    asymmetry, and an SNS alarm every time a collection ran long.
    """
    import subprocess
    monkeypatch.setattr(handler, "_missed_days", lambda j, n: [])

    def slow(script, *a, **k):
        if a and a[-1] == handler._today():
            raise subprocess.TimeoutExpired(cmd=script, timeout=840)
        return ""
    monkeypatch.setattr(handler, "_run_ok", slow)
    out = handler.job_results_collect()      # must not raise
    assert out["last_success_date"] == handler._today()


def test_results_collect_still_raises_when_yesterday_times_out(handler,
                                                               monkeypatch):
    # The asymmetry must survive the fix: yesterday is still fatal.
    import subprocess
    monkeypatch.setattr(handler, "_missed_days", lambda j, n: [])

    def slow(script, *a, **k):
        raise subprocess.TimeoutExpired(cmd=script, timeout=840)
    monkeypatch.setattr(handler, "_run_ok", slow)
    with pytest.raises(subprocess.TimeoutExpired):
        handler.job_results_collect()


def test_every_card_dependent_job_requires_the_card(handler, monkeypatch):
    """The guard must be wired into each card-dependent job, not just exist.

    A missing 05:30 racecard is the single upstream failure that would
    otherwise let build, consensus and tips all no-op in sequence.
    """
    monkeypatch.setattr(handler, "_sync_down", lambda *a, **k: None)
    monkeypatch.setattr(handler, "_sync_up", lambda *a, **k: None)
    monkeypatch.setattr(handler, "_run_ok", lambda *a, **k: "")
    monkeypatch.setattr(handler, "_prepare_racecard", lambda: "missing")
    for job in (handler.job_intelligence_build,
                handler.job_consensus_agent,
                handler.job_tips_pipeline,
                handler.job_morning_odds):
        with pytest.raises(RuntimeError) as e:
            job()
        assert "no racecard" in str(e.value), job.__name__


def test_every_card_dependent_job_skips_cleanly_on_a_quiet_day(handler, monkeypatch):
    """Skipped, not run-and-tolerated.

    Each of these jobs carries a post-condition that would fire on an empty
    result — no fresh intelligence file, no consensus JSON, no tips file, no
    MORNING_CHECK rows. Loosening those to accommodate a quiet day would blind
    them on every day that does have racing, which is the failure they exist
    to catch. So the job returns before running anything instead.
    """
    monkeypatch.setattr(handler, "_sync_down", lambda *a, **k: None)
    monkeypatch.setattr(handler, "_sync_up", lambda *a, **k: None)
    monkeypatch.setattr(handler, "_prepare_racecard", lambda: "quiet")

    def ran(script, *a, **k):
        raise AssertionError(f"quiet day ran {script}")
    monkeypatch.setattr(handler, "_run_ok", ran)

    for job in (handler.job_intelligence_build,
                handler.job_consensus_agent,
                handler.job_tips_pipeline,
                handler.job_morning_odds):
        out = job()
        assert out["quiet_day"] is True, job.__name__
        assert out["last_success_date"] == handler._today(), job.__name__


# -------------------------------------------------------- subprocess timeouts

def _captured_run(monkeypatch, handler):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""
        return Proc()

    monkeypatch.setattr(handler.subprocess, "run", fake_run)
    return calls


def test_run_sizes_the_timeout_to_the_script(handler, monkeypatch):
    """One Lambda-era constant no longer applies identically to every job.

    stride_build and consensus_agent are bounded by the schedule gap to the
    job that reads their output. run_tips_pipeline is bounded by measurement
    instead — nothing downstream reads tips_<date>.json, and its first race
    alone (532.2s on 2026-08-05) exceeds the gap bound. Everything else keeps
    the historical 840s.
    """
    calls = _captured_run(monkeypatch, handler)
    expected = {
        "stride_build.py": 2700,
        "consensus_agent.py": 2700,
        "run_tips_pipeline.py": 10800,
        "download_racecards.py": 840,
    }
    for script, timeout in expected.items():
        handler._run(script)
        assert calls[-1][1]["timeout"] == timeout, script


def test_the_tips_bound_covers_a_full_metro_card(handler):
    """The number that matters is whether a real card fits inside it.

    Measured on the real image (ECS task 02c99b61, 2026-08-05): ~290s of
    once-per-process setup plus ~243s per race in steady state. A 31-race
    Saturday is ~7,800s. A bound under that reintroduces 2026-08-05 as a
    timeout instead of an empty file — still no tips on screen.
    """
    setup_seconds, per_race, races = 290, 243, 31
    assert handler.JOB_TIMEOUTS["run_tips_pipeline.py"] > setup_seconds + per_race * races


def test_a_timeout_still_fails_loudly(handler, monkeypatch):
    """Retiring the cap must not silence a timeout into a success.

    TimeoutExpired is a SubprocessError, not a RuntimeError: it has to
    propagate out of _run_ok untouched so the job hard-fails and the alarm
    fires, exactly as intelligence-build and consensus-agent did on
    2026-08-05 — that behaviour was correct.
    """
    import subprocess

    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

    monkeypatch.setattr(handler.subprocess, "run", slow)
    with pytest.raises(subprocess.TimeoutExpired):
        handler._run("stride_build.py")
    with pytest.raises(subprocess.TimeoutExpired):
        handler._run_ok("stride_build.py")


def test_a_timeout_prints_what_the_child_wrote(handler, monkeypatch, capfd):
    """The killed run must not also be the silent one.

    consensus-agent on 2026-08-05 worked for 14 minutes and left a 31-line
    CloudWatch stream that was entirely the parent's traceback: capture_output
    buffers the child in the parent, and the timeout raised before anything
    printed it. That is why how far it got is unknowable, and why the
    replacement bound could only be guessed at.
    """
    import subprocess

    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=cmd, timeout=kwargs["timeout"],
            output="scored race 7 of 31\n", stderr="panel call failed, retrying\n")

    monkeypatch.setattr(handler.subprocess, "run", slow)
    with pytest.raises(subprocess.TimeoutExpired):
        handler._run("consensus_agent.py")

    out, err = capfd.readouterr()
    assert "scored race 7 of 31" in out, "child stdout was lost on timeout"
    assert "panel call failed, retrying" in err, "child stderr was lost on timeout"
    assert "TIMED OUT" in out


def test_a_timeout_with_no_child_output_says_so(handler, monkeypatch, capfd):
    """Absent output and swallowed output must not look identical.

    "(empty)" is a finding — it means the child produced nothing before the
    kill, which points at a hang rather than slow progress.
    """
    import subprocess

    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

    monkeypatch.setattr(handler.subprocess, "run", slow)
    with pytest.raises(subprocess.TimeoutExpired):
        handler._run("stride_build.py")

    out, err = capfd.readouterr()
    assert "(empty)" in out and "(empty)" in err


def test_timeout_dump_handles_bytes(handler):
    """text=True yields str, but a caller passing bytes must not crash the
    only code path that explains a timeout."""
    handler._dump_captured(b"bytes out", b"bytes err", "X")
