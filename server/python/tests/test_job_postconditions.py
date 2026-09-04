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
import hashlib
import json
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
    # morning rows, venues scheduled, missing venues, baseline, signals
    counts = iter([(272,), (2,), ([],), (230,), (0,)])
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
    # morning rows, venues scheduled, missing venues, then no baseline
    counts = iter([(272,), (2,), ([],), (0,)])
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
        if "array_agg" in sql:
            return ([],)
        return (272,) if "MORNING_CHECK" in sql else (0,)
    monkeypatch.setattr(handler, "_db_query", _q)

    with pytest.raises(RuntimeError):
        handler.job_morning_odds()
    assert "sync" in order, "the signals file was never relayed"
    assert order.index("sync") < len(order) - 1, (
        "sync_up ran last — the failing assertion above it would have "
        "stranded the file in a container that is about to exit")


# ------------------------------------- per-venue morning odds coverage

def _venue_coverage_db(handler, monkeypatch, scheduled, covered_rows,
                       rows=40, baseline=230, signals=12):
    """Route _db_query by query shape, so the venue post-condition runs
    against a fake schedule/snapshot state rather than a positional count
    script. covered_rows are (track, race_number) pairs, so a venue covered
    on only some races is still a covered venue — the distinction the
    per-venue granularity exists to protect."""
    queries = []

    def query(sql, params):
        queries.append(sql)
        text = " ".join(sql.split()).lower()
        if "array_agg" in text:
            covered_venues = {track for track, _ in covered_rows}
            return (sorted(set(scheduled) - covered_venues),)
        if "from race_schedule" in text:
            return (len(set(scheduled)),)
        if "morning_check" in text:
            return (rows,)
        if "baseline_night" in text:
            return (baseline,)
        if "market_signal_scores" in text:
            return (signals,)
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(handler, "_db_query", query)
    return queries


def test_morning_odds_passes_when_every_scheduled_venue_is_covered(handler,
                                                                   monkeypatch):
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_db_now", lambda: 0, raising=False)
    _venue_coverage_db(
        handler, monkeypatch,
        scheduled=["Canterbury", "Doomben"],
        covered_rows=[("Canterbury", n) for n in range(1, 8)]
                     + [("Doomben", n) for n in range(1, 9)])
    out = handler.job_morning_odds()
    assert out["morning_rows"] == 40


def test_morning_odds_fails_naming_venues_with_no_odds(handler, monkeypatch):
    """The 2026-08-05 shape, finally loud.

    Canterbury and Doomben wrote MORNING_CHECK odds; Cranbourne and Belmont
    Park mapped zero markets. The day-level row check passed (15 rows > 0)
    and the job went green with 16 races dark. The per-venue check must
    raise and NAME the dark venues — a bare count tells nobody which
    meetings went missing.
    """
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_db_now", lambda: 0, raising=False)
    _venue_coverage_db(
        handler, monkeypatch,
        scheduled=["Canterbury", "Doomben", "Cranbourne", "Belmont Park"],
        covered_rows=[("Canterbury", n) for n in range(1, 8)]
                     + [("Doomben", n) for n in range(1, 9)])
    with pytest.raises(RuntimeError) as e:
        handler.job_morning_odds()
    message = str(e.value)
    assert "Cranbourne" in message
    assert "Belmont Park" in message
    assert "2 of 4" in message


def test_morning_odds_venue_check_is_per_venue_not_per_race(handler,
                                                            monkeypatch):
    """A venue with only 3 of 8 races covered still passes.

    Betfair publishes provincial markets only a few hours before the jump,
    so at 08:00 most of a provincial card has no market yet. Tightening
    this to per-race would false-alarm on every provincial card — the
    scheduled-venue count must stay COUNT(DISTINCT venue), never a
    race-level comparison.
    """
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_db_now", lambda: 0, raising=False)
    queries = _venue_coverage_db(
        handler, monkeypatch,
        scheduled=["Ballarat Synthetic"],
        covered_rows=[("Ballarat Synthetic", 1), ("Ballarat Synthetic", 2),
                      ("Ballarat Synthetic", 3)])
    handler.job_morning_odds()   # must not raise
    count_sql = next(
        " ".join(sql.split()).lower() for sql in queries
        if "count(" in " ".join(sql.split()).lower()
        and "from race_schedule" in " ".join(sql.split()).lower())
    assert "count(distinct stride_norm_track(track))" in count_sql


def test_morning_odds_venue_check_passes_with_no_schedule(handler,
                                                          monkeypatch):
    """Zero scheduled venues is not a dark venue: a quiet day is handled
    upstream by _require_racecard, so this check must simply not fire."""
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_db_now", lambda: 0, raising=False)
    _venue_coverage_db(handler, monkeypatch, scheduled=[], covered_rows=[])
    handler.job_morning_odds()   # must not raise


def test_morning_odds_venue_check_runs_after_the_signals_file_is_relayed(
        handler, monkeypatch):
    """A dark venue is a health finding, not a reason to strand the day's
    degraded-but-usable market file in a container about to exit."""
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_db_now", lambda: 0, raising=False)
    order = []
    monkeypatch.setattr(handler, "_sync_up",
                        lambda *a, **k: order.append("sync") or 0)
    queries = _venue_coverage_db(
        handler, monkeypatch,
        scheduled=["Canterbury", "Cranbourne"],
        covered_rows=[("Canterbury", 1)])
    routed = handler._db_query

    def _q(sql, p):
        if "array_agg" in sql:
            order.append("venue")
        return routed(sql, p)
    monkeypatch.setattr(handler, "_db_query", _q)
    with pytest.raises(RuntimeError, match="Cranbourne"):
        handler.job_morning_odds()
    assert order.index("sync") < order.index("venue")


# ------------------------------------- normaliser lockstep

# The verification script's case table: capitalised schedule values, an
# already-lowercase snapshot value, and one name per explicit alias branch.
NORM_CASE_TABLE = {
    "Canterbury": "canterbury",
    "Belmont Park": "belmontpark",
    "Ballarat Synthetic": "ballaratsynthetic",
    "canterbury": "canterbury",
    "Royal Randwick": "randwick",
    "Caulfield": "caulfield",
}


def test_python_mirror_agrees_with_sql_normaliser_on_the_case_table():
    """betfair_markets.norm_track is documented as the Python lockstep
    mirror of stride_norm_track (identity_normalization.py, whose ordering
    test_phase_minus_1_contracts pins). If they are supposed to agree, a
    test should say so — on the same case table the verification script
    uses, so the two normalisers cannot drift apart silently."""
    import betfair_markets
    for name, expected in NORM_CASE_TABLE.items():
        assert betfair_markets.norm_track(name) == expected, name


def test_handler_normalises_both_sides_of_the_venue_comparison(handler,
                                                               monkeypatch):
    """A one-sided edit — normalising the schedule but not the snapshots,
    or vice versa — compiles fine, passes faked-cursor tests, and never
    matches in production. Pin that BOTH sides go through
    stride_norm_track."""
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_db_now", lambda: 0, raising=False)
    queries = _venue_coverage_db(
        handler, monkeypatch,
        scheduled=["Canterbury"], covered_rows=[("Canterbury", 1)])
    handler.job_morning_odds()
    texts = [" ".join(sql.split()).lower() for sql in queries]
    missing_sql = next(t for t in texts if "array_agg" in t)
    assert missing_sql.count("stride_norm_track(track)") == 2, (
        "the missing-venue query must apply stride_norm_track to the "
        "schedule side AND the snapshot side")
    scheduled_sql = next(t for t in texts
                         if "count(distinct" in t and "race_schedule" in t)
    assert "count(distinct stride_norm_track(track))" in scheduled_sql


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

    The tips bound moved 21600 -> 30600 on 2026-09-04. Every measurement it was
    sized on came from runs where the LLM contributed nothing — 1.1s across 30
    races on 2026-09-02, because every call was failing. A working provider adds
    six blocking, sequential calls per race, roughly 120s, which puts a 55-race
    card at 92% of the old bound. The failure that bound buys is the worst
    available: the tips JSON is written once, after the whole meet loop, so a
    subprocess killed at the cap leaves no file at all.
    """
    calls = _captured_run(monkeypatch, handler)
    expected = {
        "stride_build.py": 3400,
        "consensus_agent.py": 9000,
        "run_tips_pipeline.py": 30600,
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


# ------------------------------------------------------- proof jobs that proof

def test_consensus_proof_fails_if_it_never_reached_the_panel(handler,
                                                             monkeypatch):
    """The defect this job actually had, pinned.

    With no card staged, run_consensus returns at its load_racecard_meetings
    check — which sits above load_tipster_panel — prints "No racecard found",
    writes an empty consensus file and exits 0. The job reported PASSED
    (ECS task 417b4554, 2026-08-06) while proving none of the container,
    secret or panel setup its docstring claims.
    """
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(
        handler, "_run_ok",
        lambda *a, **k: "[CONSENSUS] No racecard found for 2026-08-06. "
                        "Writing empty consensus file.")
    with pytest.raises(RuntimeError) as e:
        handler.job_consensus_proof()
    assert "without reaching the tipster panel" in str(e.value)


def test_consensus_proof_passes_once_the_panel_line_appears(handler,
                                                            monkeypatch):
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(
        handler, "_run_ok",
        lambda *a, **k: "[PANEL] 16 active+verified tipsters in panel")
    out = handler.job_consensus_proof()
    assert out["last_success_date"]


def test_consensus_proof_stages_the_card_before_running(handler, monkeypatch):
    """Ordering: the card has to be relayed BEFORE consensus_agent starts, or
    the assertion above can only ever fail. Fargate tasks start empty."""
    _neutralise_io(handler, monkeypatch)
    order = []
    monkeypatch.setattr(handler, "_prepare_racecard",
                        lambda: order.append("card") or "card")
    monkeypatch.setattr(handler, "_run_ok",
                        lambda *a, **k: order.append("run") or "[PANEL] 16")
    handler.job_consensus_proof()
    assert order == ["card", "run"], order


def test_panel_proof_asks_for_a_real_fetch_not_a_dry_run(handler, monkeypatch):
    """--dry-run returns before tavily_client.extract, so it can never answer
    "does the panel fetch". panel-proof must pass --panel-only instead."""
    _neutralise_io(handler, monkeypatch)
    calls = []
    monkeypatch.setattr(
        handler, "_run_ok",
        lambda *a, **k: calls.append(a) or "PANEL_STAGED 1\nPANEL_USABLE 4 16\n")
    handler.job_panel_proof()
    assert "--panel-only" in calls[0]
    assert "--dry-run" not in calls[0]


# ------------------------------------------------------------- panel staging

def test_dispatch_stages_the_panel_for_every_job(handler, monkeypatch):
    """Staging lives in dispatch, so no job can be added without it.

    Wiring it per-job is how the panel would go missing again: the next
    consensus-adjacent job someone adds is the one nobody remembers to wire.
    Covering it here means Fargate, Lambda and the proof variants are all
    covered by construction, including paths that do not exist yet.
    """
    order = []
    monkeypatch.setattr(handler, "_load_secrets", lambda: order.append("secrets"))
    monkeypatch.setattr(handler, "_stage_models", lambda: order.append("models"))
    monkeypatch.setattr(handler, "_stage_panel", lambda: order.append("panel"))
    monkeypatch.setattr(handler, "_put_state", lambda *a, **k: None)
    monkeypatch.setitem(handler.JOBS, "unit-test-job",
                        lambda: order.append("job") or {"ok": 1})
    monkeypatch.setenv("STRIDE_JOB", "unit-test-job")
    handler.dispatch()
    assert "panel" in order, "dispatch never staged the panel"
    assert order.index("panel") < order.index("job"), (
        "the panel was staged after the job ran, which is no staging at all")


def test_stage_panel_does_not_take_unrelated_jobs_red(handler, monkeypatch):
    """A missing panel must fail the CONSUMER, not results-collect.

    The loud failure belongs to consensus_agent.load_tipster_panel; this
    returning False keeps a best-effort stage safe, because something else
    is not best-effort.
    """
    monkeypatch.setenv("STRIDE_MODELS_BUCKET", "some-bucket")

    class _Boom:
        def download_file(self, *a, **k):
            raise RuntimeError("NoSuchKey")
    monkeypatch.setattr(handler, "_s3", lambda: _Boom())
    monkeypatch.setattr(handler, "_root", lambda: "/tmp/stride-panel-test")
    assert handler._stage_panel() is False        # must not raise


def test_stage_models_does_not_flatten_config_into_models(handler,
                                                          monkeypatch):
    """config/ is not a model artifact.

    Without the skip, the panel lands a second time at
    models/tipster_panel.json where nothing reads it, and a bucket holding
    only config would satisfy the "models bucket is EMPTY" assertion —
    turning a genuinely missing ensemble into a silent pass.
    """
    import pytest as _pytest
    monkeypatch.setenv("STRIDE_MODELS_BUCKET", "some-bucket")
    monkeypatch.setattr(handler, "_root", lambda: "/tmp/stride-panel-test")
    got = []

    class _S3:
        def get_paginator(self, _):
            class P:
                def paginate(self, **kw):
                    return [{"Contents": [{"Key": "config/tipster_panel.json"}]}]
            return P()

        def download_file(self, b, k, d):
            got.append(k)
    monkeypatch.setattr(handler, "_s3", lambda: _S3())
    with _pytest.raises(RuntimeError) as e:
        handler._stage_models()
    assert got == [], f"config/ was downloaded as a model artifact: {got}"
    assert "EMPTY" in str(e.value)


def test_the_panel_escape_hatch_is_documented(handler):
    """"Written down, not discovered by accident three weeks from now."

    The variable is the whole fallback story for local dev and CI, so it
    lives beside the .claude/skills/ note rather than only in a docstring.
    """
    from pathlib import Path
    md = (Path(__file__).resolve().parents[3] / "CLAUDE.md").read_text()
    assert "STRIDE_PANEL_OPTIONAL" in md
    assert "tipster_panel.json" in md


def test_stage_models_names_the_artifact_instead_of_counting(handler,
                                                             monkeypatch,
                                                             tmp_path):
    """A count is a proxy; the scorer opens one file by name.

    Staging the four sectional_combiner JSONs and no .pkl satisfied
    "the bucket is not empty" while models/racing_ensemble_v2.pkl — the file
    ml_model.py:165 loads — was absent. The tips job would then discover it
    at 08:05, with the morning already spent.
    """
    import pytest as _pytest
    monkeypatch.setenv("STRIDE_MODELS_BUCKET", "some-bucket")
    monkeypatch.setattr(handler, "_root", lambda: str(tmp_path))

    class _S3:
        def get_paginator(self, _):
            class P:
                def paginate(self, **kw):
                    return [{"Contents": [
                        {"Key": "sectional_combiner_mile.json"},
                        {"Key": "sectional_combiner_sprint.json"}]}]
            return P()

        def download_file(self, b, k, d):
            open(d, "w").close()
    monkeypatch.setattr(handler, "_s3", lambda: _S3())
    with _pytest.raises(RuntimeError) as e:
        handler._stage_models()
    assert "racing_ensemble_v2.pkl" in str(e.value)
    assert "EMPTY" not in str(e.value), "counted instead of named"


def test_stage_models_passes_when_the_named_artifact_lands(handler,
                                                           monkeypatch,
                                                           tmp_path):
    monkeypatch.setenv("STRIDE_MODELS_BUCKET", "some-bucket")
    monkeypatch.setattr(handler, "_root", lambda: str(tmp_path))

    class _S3:
        def get_paginator(self, _):
            class P:
                def paginate(self, **kw):
                    return [{"Contents": [{"Key": "racing_ensemble_v2.pkl"}]}]
            return P()

        def download_file(self, b, k, d):
            open(d, "w").close()
    monkeypatch.setattr(handler, "_s3", lambda: _S3())
    handler._stage_models()          # must not raise


def test_stage_models_fails_when_manifest_is_required_but_unset(
        handler, monkeypatch, tmp_path):
    monkeypatch.setenv("STRIDE_MODELS_BUCKET", "some-bucket")
    monkeypatch.setenv("STRIDE_RELEASE_MANIFEST_REQUIRED", "true")
    monkeypatch.delenv("STRIDE_RELEASE_MANIFEST_KEY", raising=False)
    monkeypatch.setattr(handler, "_root", lambda: str(tmp_path))
    with pytest.raises(RuntimeError, match="STRIDE_RELEASE_MANIFEST_KEY is unset"):
        handler._stage_models()


def _runtime_manifest(ensemble_path="racing_ensemble_v2.pkl"):
    ensemble_payload = b"ensemble"
    auxiliary_payload = b"weights"
    artifacts = {
        name: {"status": "NONE"}
        for name in (
            "catboost", "lightgbm", "xgboost", "ensemble", "calibrator",
            "decision_model",
        )
    }
    artifacts["ensemble"] = {
        "status": "PRESENT",
        "path": ensemble_path,
        "object_key": "releases/r1/ensemble.pkl",
        "sha256": hashlib.sha256(ensemble_payload).hexdigest(),
    }
    return {
        "manifest_version": 1,
        "release_id": "r1",
        "created_at": "2026-08-10T00:00:00Z",
        "source_commit": "abc123",
        "image_digest": "sha256:image",
        "training_data_build": {"id": "td1"},
        "feature_schema": {"version": "v2"},
        "artifacts": artifacts,
        "auxiliary_artifacts": [{
            "path": "sectional_combiner_weights.json",
            "object_key": "releases/r1/sectional_combiner_weights.json",
            "sha256": hashlib.sha256(auxiliary_payload).hexdigest(),
        }],
        "wrapper": {"version": "v1"},
        "risk_configuration": {"version": "flat"},
        "decision_time_configuration": {"version": "tip-time"},
        "settlement_configuration": {"version": "v1"},
    }, {
        "releases/r1/ensemble.pkl": ensemble_payload,
        "releases/r1/sectional_combiner_weights.json": auxiliary_payload,
    }


def test_manifest_mode_stages_auxiliary_runtime_artifacts(
        handler, monkeypatch, tmp_path):
    manifest, payloads = _runtime_manifest()
    monkeypatch.setenv("STRIDE_MODELS_BUCKET", "some-bucket")
    monkeypatch.setenv("STRIDE_RELEASE_MANIFEST_KEY", "releases/r1/manifest.json")
    monkeypatch.delenv("STRIDE_RELEASE_MANIFEST_REQUIRED", raising=False)
    monkeypatch.setattr(handler, "_root", lambda: str(tmp_path))
    downloaded = []

    class _S3:
        def download_file(self, _bucket, key, destination):
            downloaded.append(key)
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            if key == "releases/r1/manifest.json":
                path.write_text(json.dumps(manifest))
            else:
                path.write_bytes(payloads[key])

    monkeypatch.setattr(handler, "_s3", lambda: _S3())
    handler._stage_models()
    models = tmp_path / "server" / "python" / "models"
    assert (models / "racing_ensemble_v2.pkl").read_bytes() == b"ensemble"
    assert (models / "sectional_combiner_weights.json").read_bytes() == b"weights"
    assert "releases/r1/sectional_combiner_weights.json" in downloaded


def test_manifest_mode_rejects_wrong_ensemble_filename(
        handler, monkeypatch, tmp_path):
    manifest, payloads = _runtime_manifest("renamed-ensemble.pkl")
    monkeypatch.setenv("STRIDE_MODELS_BUCKET", "some-bucket")
    monkeypatch.setenv("STRIDE_RELEASE_MANIFEST_KEY", "releases/r1/manifest.json")
    monkeypatch.setattr(handler, "_root", lambda: str(tmp_path))

    class _S3:
        def download_file(self, _bucket, key, destination):
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            if key == "releases/r1/manifest.json":
                path.write_text(json.dumps(manifest))
            else:
                path.write_bytes(payloads[key])

    monkeypatch.setattr(handler, "_s3", lambda: _S3())
    with pytest.raises(RuntimeError, match="racing_ensemble_v2.pkl"):
        handler._stage_models()


def test_panel_proof_treats_rotted_sources_as_a_finding_not_a_failure(
        handler, monkeypatch):
    """Exit 1 means staged-but-degraded. Failing on it inverts the runbook.

    At the measured 4 of 16 usable sources, --panel-only returns 1 (below its
    50% floor). With _run_ok's default ok_codes=(0,) that raised, so
    panel-proof would have gone RED on the first day the panel was ever
    shipped working — and the documented response to a red panel-proof is to
    set STRIDE_PANEL_OPTIONAL and run without the panel. A red that makes its
    reader disable the healthy thing is worse than no check.
    """
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_run_ok",
                        lambda *a, **k: "PANEL_STAGED 1\nPANEL_USABLE 4 16\n")
    out = handler.job_panel_proof()
    assert out["panel_staged"] is True
    assert (out["sources_usable"], out["sources_total"]) == (4, 16)
    assert out["degraded"] is True


def test_panel_proof_accepts_the_degraded_exit_code_from_the_script(
        handler, monkeypatch):
    """The acceptance has to reach _run_ok, not just the parsing below it."""
    seen = {}

    def _capture(script, *args, **kw):
        seen["ok_codes"] = kw.get("ok_codes", (0,))
        return "PANEL_STAGED 1\nPANEL_USABLE 16 16\n"
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_run_ok", _capture)
    handler.job_panel_proof()
    assert 1 in seen["ok_codes"], (
        "exit 1 must be accepted or a degraded panel reads as a staging "
        "failure")
    assert 6 in seen["ok_codes"], (
        "exit 6 must reach the handler so it can raise the specific message, "
        "not _run_ok's generic 'exited 6'")


def test_panel_proof_fails_when_the_panel_never_reached_the_container(
        handler, monkeypatch):
    """The one thing this job exists to detect."""
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_run_ok", lambda *a, **k: "PANEL_STAGED 0\n")
    with pytest.raises(RuntimeError) as e:
        handler.job_panel_proof()
    assert "NOT in the container" in str(e.value)
    assert "09c_upload_panel.sh" in str(e.value)


def test_panel_proof_fails_when_no_marker_was_printed_at_all(handler,
                                                             monkeypatch):
    """Neither outcome reported is its own outcome. Absent a marker the run
    stopped before the panel, and reporting that as a pass is the exact shape
    consensus-proof already had."""
    _neutralise_io(handler, monkeypatch)
    monkeypatch.setattr(handler, "_run_ok", lambda *a, **k: "some other output")
    with pytest.raises(RuntimeError) as e:
        handler.job_panel_proof()
    assert "no PANEL_STAGED marker" in str(e.value)
