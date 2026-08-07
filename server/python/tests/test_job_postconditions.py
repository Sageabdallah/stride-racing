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


# -------------------------------------------------------- baseline-night

def test_baseline_night_fails_when_no_snapshot_rows_committed(handler,
                                                              monkeypatch):
    """Mirrors the MORNING_CHECK postcondition, for the snapshot that had
    none. capture_snapshot swallows its own DB write errors (odds_movement
    .py: `except Exception: print(...)`) and returns {} when the API gives
    nothing, so exit 0 has never meant rows were committed."""
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_db_now", lambda: 0, raising=False)
    monkeypatch.setattr(handler, "_db_query", lambda sql, p: (0,))
    with pytest.raises(RuntimeError) as e:
        handler.job_baseline_night()
    assert "no BASELINE_NIGHT rows" in str(e.value)


def test_baseline_night_passes_with_snapshot_rows(handler, monkeypatch):
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_db_now", lambda: 0, raising=False)
    monkeypatch.setattr(handler, "_db_query", lambda sql, p: (40,))
    out = handler.job_baseline_night()
    assert out["baseline_rows"] == 40
    assert "quiet_day" not in out


def test_baseline_night_quiet_day_does_not_run_or_query(handler, monkeypatch):
    """A quiet day is handled the way every other card-dependent job handles
    it: return early, no snapshot attempt, no DB query, and no false 'wrote
    zero rows' alarm on a day with nothing to capture."""
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_prepare_racecard", lambda: "quiet")

    def _fail_query(sql, p):
        raise AssertionError("baseline-night queried the DB on a quiet day")
    monkeypatch.setattr(handler, "_db_query", _fail_query)

    def _fail_run(*a, **k):
        raise AssertionError("baseline-night ran odds_movement.py on a "
                             "quiet day")
    monkeypatch.setattr(handler, "_run_ok", _fail_run)

    out = handler.job_baseline_night()
    assert out["quiet_day"] is True
    assert out["baseline_rows"] == 0


def test_baseline_night_fails_when_the_card_is_missing(handler, monkeypatch):
    """The 00:30 regression, pinned.

    A baseline job scheduled before racecard-collect finds no card and
    raises here — every day, including healthy ones. The schedule-ordering
    tests in test_schedule_state.py are what keep the clock right; this is
    what makes the consequence explicit at the job level."""
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_prepare_racecard", lambda: "missing")
    with pytest.raises(RuntimeError) as e:
        handler.job_baseline_night()
    assert "no racecard" in str(e.value)


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


def test_morning_odds_fails_when_there_is_no_baseline_at_all(handler,
                                                             monkeypatch):
    """Supersedes "day one has no baseline, so zero signals is correct".

    That assumption was written to stop a daily false alarm, and it was
    right on day one. It stopped being right the moment baseline-night was
    supposed to be running: `if baseline and not signals` can only fire once
    baseline is already nonzero, so from 2026-05-04 to 2026-08-06 — every
    day baseline-night went unscheduled — the check was structurally unable
    to fire, and market_signal_scores stayed empty with every morning green.

    baseline-night now runs at 04:15 with its own "wrote zero rows"
    postcondition, so a non-quiet morning reaching here with no baseline
    means that alarm should already have fired. Raising here too is not a
    duplicate: it is the difference between "the baseline job failed" and
    "the baseline job passed and its rows are not visible to this one".
    """
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_db_now", lambda: 0, raising=False)
    counts = iter([(272,), (0,)])           # morning rows, then no baseline
    monkeypatch.setattr(handler, "_db_query", lambda sql, p: next(counts))
    with pytest.raises(RuntimeError) as e:
        handler.job_morning_odds()
    assert "ZERO BASELINE_NIGHT rows" in str(e.value)


def test_morning_odds_relays_the_signals_file_before_it_asserts(handler,
                                                                monkeypatch):
    """The evidence must survive the postcondition that judges it.

    market_signals_<date>.json is what consensus_blender actually reads at
    tips time (consensus_blender.py:315) — not the DB table — and Fargate
    tasks share no filesystem, so a file that is not synced up does not
    exist as far as the tips job is concerned. On 2026-08-06 the row
    assertion fired and the file never left the container.

    Ordering, not merely presence: _sync_up has to happen BEFORE the
    assertions, or every failing check silently also deletes the day's
    degraded-but-usable market file.
    """
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_db_now", lambda: 0, raising=False)
    order = []
    monkeypatch.setattr(handler, "_sync_up",
                        lambda *a, **k: order.append("sync") or 0)

    def _q(sql, p):
        order.append("query")
        return (272,) if "MORNING_CHECK" in sql else (0,)
    monkeypatch.setattr(handler, "_db_query", _q)

    with pytest.raises(RuntimeError):
        handler.job_morning_odds()
    assert "sync" in order, "the signals file was never relayed"
    assert order.index("sync") < len(order) - 1, (
        "sync_up ran last — the failing assertion above it would have "
        "stranded the file in a container that is about to exit")


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
        "stride_build.py": 3400,
        "consensus_agent.py": 9000,
        "run_tips_pipeline.py": 21600,
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

# ------------------------------------------------------------- _run_ok stderr

def _completed(stdout="", stderr="", returncode=0):
    import subprocess
    return subprocess.CompletedProcess(args=["fake"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def test_run_ok_surfaces_stderr_on_success(handler, monkeypatch, capsys):
    """A green exit must not throw the diagnostics away.

    Every per-race diagnostic in run_tips_pipeline.py goes to stderr, so when
    the 2026-08-05 run dropped all 31 races and exited 0, the log kept the
    stdout summary and discarded the entire explanation. stderr on success is
    the only record of why a run did what it did.
    """
    monkeypatch.setattr(handler, "_run", lambda *a, **k: _completed(
        stdout="31 races scored", stderr="race 7: MC failed, skipping"))
    out = handler._run_ok("run_tips_pipeline.py", "2026-08-05")
    assert out == "31 races scored"          # stdout contract unchanged
    captured = capsys.readouterr()
    assert "MC failed, skipping" in captured.err
    assert "31 races scored" in captured.out


def test_run_ok_keeps_the_tail_of_a_long_stderr(handler, monkeypatch, capsys):
    """The bound keeps the LAST 4000 chars, not the first.

    With 31 races of diagnostics the useful lines are the most recent ones;
    keeping the head would truncate exactly where the log gets informative.
    """
    noise = "x" * 5000
    monkeypatch.setattr(handler, "_run", lambda *a, **k: _completed(
        stderr=noise + "\nthe real reason"))
    handler._run_ok("script.py")
    err = capsys.readouterr().err
    assert "the real reason" in err
    assert len(err) <= 4001                  # tail + newline, bounded


def test_run_ok_still_raises_with_stderr_on_failure(handler, monkeypatch,
                                                    capsys):
    monkeypatch.setattr(handler, "_run", lambda *a, **k: _completed(
        stderr="boom", returncode=1))
    with pytest.raises(RuntimeError) as e:
        handler._run_ok("script.py")
    assert "script.py exited 1" in str(e.value)
    assert "boom" in capsys.readouterr().err


def test_bounds_cover_a_metro_saturday(handler):
    """The bounds were first sized on a 31-race midweek card; Saturday is the
    day the system exists for.

    Measured per-race cost from the only two real cards the cloud chain has
    run: stride_build 269.1s/8 races and 1132s/31 races (~36s), consensus_agent
    ~2280s/31 races (~74s). A metro Saturday of 8-10 meetings is plausibly ~90
    races. At 2700s both jobs died on exactly that day.
    """
    SATURDAY_RACES = 90
    assert handler.JOB_TIMEOUTS["stride_build.py"] > 36 * SATURDAY_RACES
    assert handler.JOB_TIMEOUTS["consensus_agent.py"] > 74 * SATURDAY_RACES


def test_stride_build_still_dies_inside_its_gap(handler):
    """consensus syncs down stride_build's output. A bound past the
    04:20->05:30 gap would let consensus start on yesterday's intelligence
    instead of failing loudly — the silent stale-data trade this whole scheme
    exists to avoid. The chain moved on 2026-08-06 and the gap WIDENED from
    3600s to 4200s; this stays at the tighter number, because the bound only
    ever needed to cover ~90 races (3,285s) and a bound is not improved by
    being closer to its gap."""
    assert handler.JOB_TIMEOUTS["stride_build.py"] < 3600


def test_consensus_finishes_before_tips_reads_it(handler):
    """consensus_<date>.json has exactly one reader — run_tips_pipeline at
    10:00, three hours later. Not morning-odds at 08:00, which runs
    odds_movement.py and never references consensus."""
    assert handler.JOB_TIMEOUTS["consensus_agent.py"] < 3 * 3600
