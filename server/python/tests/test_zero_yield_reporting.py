"""A failed job must arrive carrying its reason (issue #176).

On 2026-09-08 consensus-agent exited 4 and the operator's entire notification
was:

    RuntimeError: consensus_agent.py exited 4

Six distinct faults reach exit 4 — no Perplexity key, Perplexity 4xx, a spent
Claude cap, unparseable extraction JSON, a panel below the usable floor, a
horse matcher that matches nothing — and the alert distinguished none of them.
Three defects stacked to make it opaque, and these tests pin all three shut:

  * _run_ok printed the child's stderr to the container log and dropped it when
    it built the exception, so the reason never reached the one channel that
    leaves the container. The SNS budget is 1000 characters and 40 were used.
  * job_consensus_agent synced the intelligence directory only after a clean
    run, so consensus_<date>.health.json — the run's own account of what its
    two mention sources did — was uploaded exactly when it was not needed.
  * the zero-yield FATAL line reported races and mentions, both already implied
    by the exit code, while the health dict in the same scope held the panel
    and API counters that name the cause.

Zero network, zero AWS: boto3 is stubbed and every S3 call is a fake.
"""

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

HANDLER_PATH = (Path(__file__).resolve().parents[3]
                / "infra" / "jobs" / "handler.py")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("requests", types.ModuleType("requests"))

import consensus_agent as ca  # noqa: E402


class _StubBoto3:
    def __getattr__(self, name):
        raise AssertionError(f"boto3.{name} must not be called in this test")


@pytest.fixture(scope="module")
def handler():
    sys.modules.setdefault("boto3", _StubBoto3())
    spec = importlib.util.spec_from_file_location("stride_handler_176",
                                                  HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["stride_handler_176"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --------------------------------------------- the reason reaches the alert

def test_failure_tail_keeps_the_last_lines_not_the_first(handler):
    """A script explains itself on the way out, so the tail is the reason."""
    proc = FakeProc(stderr="\n".join(f"race {i} scored" for i in range(200))
                           + "\n[CONSENSUS] FATAL: zero mentions extracted")
    tail = handler._failure_tail(proc)
    assert "[CONSENSUS] FATAL: zero mentions extracted" in tail
    assert "race 0 scored" not in tail


def test_failure_tail_never_cuts_a_line_in_half(handler):
    long_line = "x" * 300
    proc = FakeProc(stderr="\n".join([long_line] * 10))
    tail = handler._failure_tail(proc, limit=700)
    assert len(tail) <= 700
    # Whole lines only: a bound that slices mid-token reads as corruption.
    assert all(part == long_line for part in tail.split(" | "))


def test_failure_tail_falls_back_to_stdout(handler):
    proc = FakeProc(stdout="the only thing printed", stderr="   \n  \n")
    assert handler._failure_tail(proc) == "the only thing printed"


def test_failure_tail_is_empty_when_the_child_said_nothing(handler):
    assert handler._failure_tail(FakeProc()) == ""


def test_run_ok_puts_the_reason_in_the_exception(handler, monkeypatch, capsys):
    """The defect itself: the exception carried an exit code and nothing else.

    _put_state truncates to 400 and the SNS publish to 1000, so the message
    must both name the cause and stay inside those budgets.
    """
    breakdown = ("[CONSENSUS] FATAL: 24 races scored, zero mentions extracted. "
                 "| [CONSENSUS] zero-yield breakdown: panel 0/16 sources "
                 "fetched OK | perplexity 96 queries, 0 returned content "
                 "(err_401=96) | claude extraction calls 0")
    monkeypatch.setattr(handler, "_run",
                        lambda *a, **k: FakeProc(4, "", breakdown))

    with pytest.raises(RuntimeError) as e:
        handler._run_ok("consensus_agent.py", "2026-09-08")

    msg = str(e.value)
    assert "consensus_agent.py exited 4" in msg
    assert "err_401=96" in msg, "the alert still cannot name the fault"
    assert "panel 0/16" in msg
    # The whole published message, as dispatch() builds it.
    assert len(f"RuntimeError: {msg}") <= 1000


def test_run_ok_still_succeeds_quietly_on_an_allowed_code(handler, monkeypatch):
    monkeypatch.setattr(handler, "_run",
                        lambda *a, **k: FakeProc(3, "out", "noise"))
    assert handler._run_ok("download_racecards.py", ok_codes=(0, 3)) == "out"


# ------------------------------- the artifact that explains a failure survives

def _consensus_job(handler, monkeypatch, run_raises):
    uploads = []
    monkeypatch.setattr(handler, "_sync_down", lambda *a, **k: 0)
    monkeypatch.setattr(handler, "_sync_up",
                        lambda d, pattern="*.json": uploads.append((d, pattern)))
    monkeypatch.setattr(handler, "_require_racecard", lambda job: "card")
    monkeypatch.setattr(handler, "_today", lambda: "2026-09-08")
    monkeypatch.setattr(os.path, "exists", lambda p: True)

    def run_ok(script, *args, **kw):
        if run_raises:
            raise RuntimeError("consensus_agent.py exited 4 -- zero-yield "
                               "breakdown: panel 0/16 sources fetched OK")
        return ""

    monkeypatch.setattr(handler, "_run_ok", run_ok)
    return uploads


def test_health_sidecar_is_uploaded_when_the_run_fails(handler, monkeypatch):
    uploads = _consensus_job(handler, monkeypatch, run_raises=True)
    with pytest.raises(RuntimeError):
        handler.job_consensus_agent()
    assert uploads == [("server/python/intelligence",
                        "consensus_2026-09-08.health.json")]


def test_a_failed_run_still_does_not_publish_its_consensus_artifact(
        handler, monkeypatch):
    """The sidecar, never the directory.

    On a zero-yield run consensus_<date>.json exists and is full of zeroes.
    Publishing it would let run_tips_pipeline sync down a failed day and score
    it as a real one — trading a stale-data bug for a wrong-data bug.
    """
    uploads = _consensus_job(handler, monkeypatch, run_raises=True)
    with pytest.raises(RuntimeError):
        handler.job_consensus_agent()
    # Non-empty first: `all()` over nothing is True, and "uploaded nothing" is
    # the old behaviour this pair exists to change.
    assert uploads
    assert all(pattern.endswith(".health.json") for _, pattern in uploads)


def test_a_failing_sidecar_upload_does_not_mask_the_reason(handler, monkeypatch):
    """The upload is best-effort; the exception it protects is not.

    An S3 error raised from inside the except block chains over the original
    RuntimeError and Python propagates the NEW one, so a denied bucket would
    put a boto stack trace in the alert in place of the cause — losing exactly
    what the block exists to save.
    """
    _consensus_job(handler, monkeypatch, run_raises=True)

    def denied(*a, **k):
        raise RuntimeError("An error occurred (AccessDenied)")

    monkeypatch.setattr(handler, "_sync_up", denied)
    with pytest.raises(RuntimeError) as e:
        handler.job_consensus_agent()
    assert "exited 4" in str(e.value)
    assert "AccessDenied" not in str(e.value)


def test_a_clean_run_uploads_the_directory_as_before(handler, monkeypatch):
    uploads = _consensus_job(handler, monkeypatch, run_raises=False)
    assert handler.job_consensus_agent() == {"last_success_date": "2026-09-08"}
    assert uploads == [("server/python/intelligence", "*.json")]


# ------------------------------------------- the breakdown names the fault

def test_breakdown_names_the_perplexity_status_that_killed_the_day():
    line = ca._zero_yield_breakdown({
        "races": 24,
        "extraction_model": "claude-sonnet-5",
        "panel_fetch_success": 0,
        "panel_fetch_attempted": 16,
        "api_calls": {"tavily": 16, "claude": 0,
                      "perplexity_newspaper": 24, "perplexity_portal": 24,
                      "perplexity_data": 24, "perplexity_social": 24,
                      "pplx_err_401": 96},
    })
    assert "panel 0/16" in line
    assert "perplexity 96 queries, 0 returned content" in line
    assert "err_401=96" in line
    assert "claude extraction calls 0" in line
    assert "claude-sonnet-5" in line
    # It travels inside a bounded stderr tail; a breakdown that overruns the
    # bound is a breakdown nobody woken at 05:37 gets to read.
    assert len(line) <= 400


def test_breakdown_separates_a_dead_panel_from_a_dead_search_leg():
    """The two legs fail for different reasons and need different repairs."""
    panel_dead = ca._zero_yield_breakdown({
        "panel_fetch_success": 0, "panel_fetch_attempted": 16,
        "api_calls": {"perplexity_newspaper": 24, "pplx_ok": 24},
    })
    search_dead = ca._zero_yield_breakdown({
        "panel_fetch_success": 14, "panel_fetch_attempted": 16,
        "api_calls": {"perplexity_newspaper": 24, "pplx_nokey": 24},
    })
    assert "panel 0/16" in panel_dead and "24 returned content" in panel_dead
    assert "panel 14/16" in search_dead and "nokey=24" in search_dead


def test_breakdown_survives_a_health_dict_with_nothing_in_it():
    """It runs on the failure path. It must not raise there."""
    assert "panel 0/0" in ca._zero_yield_breakdown({})


# ------------------------------- outcome tallies are not read as spend

def test_perplexity_outcome_tally_does_not_inflate_the_spend_warning():
    """search_race_tips_perplexity_multi sums every "perplexity_" key to decide
    its spend warning. An outcome tally counted as spend would be worse than no
    tally at all, so the prefix is deliberately different."""
    usage = {"perplexity_newspaper": 3}
    for outcome in ("ok", "err_401", "timeout", "nokey"):
        ca._pplx_record(usage, outcome)
    spend = sum(v for k, v in usage.items() if k.startswith("perplexity_"))
    assert spend == 3, "an outcome tally was counted as a Perplexity call"
    assert usage["pplx_err_401"] == 1


# ------------------------------------ a run with no keys is not a success

def test_missing_api_keys_raise_instead_of_reporting_success(monkeypatch):
    """`return {}` left LAST_RUN_HEALTH empty, main() found no zero_yield and
    no health at all, and fell off the end — exit 0. A consensus run with no
    API keys reported SUCCESS and every downstream pick degraded to NO_BET
    with no alarm anywhere."""
    monkeypatch.setattr(ca, "LAST_RUN_HEALTH", {})
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    with pytest.raises(RuntimeError) as e:
        ca.run_consensus_agent("2026-09-08")
    assert "TAVILY_API_KEY" in str(e.value)


def test_the_message_names_every_missing_key_not_just_the_first(monkeypatch):
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as e:
        ca.run_consensus_agent("2026-09-08")
    assert "TAVILY_API_KEY" in str(e.value)
    assert "ANTHROPIC_API_KEY" in str(e.value)
