"""A preview run must leave no mark the real run cannot overwrite.

tips-proof is the variant that scores a card "writing nothing". Walking its
path for the 2026-09-05 Friday preview found two writes --skip-db-store never
reached, both first-write-wins: mc_api logs prediction_audit (which
training_view_v2 is built from), feature_snapshots and race_schedule as a side
effect of scoring, and odds_snapshots captures tip_time price rows. An evening
proof re-scored a date whose real rows already existed and lost every conflict;
a preview runs before the real run and would have won them. These tests pin the
four switches, the insight check that the 2026-09-02 run needed, the llm-proof
job that reads the two-pick script, and the workflow inputs that pin a provider
and wait for a long task.
"""

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HANDLER_PATH = ROOT / "infra" / "jobs" / "handler.py"
VERIFY_JOBS = ROOT / ".github" / "workflows" / "verify-jobs.yml"
MC_API = ROOT / "server" / "python" / "mc_api.py"

SWITCHES = ("STRIDE_LEDGER_WRITE", "STRIDE_SERVE_LIVE_FEATURES_SHADOW",
            "STRIDE_ODDS_SNAPSHOT_WRITE", "STRIDE_MC_AUDIT_WRITE")


class _StubBoto3:
    def __getattr__(self, name):
        raise AssertionError(f"boto3.{name} must not be called in this test")


@pytest.fixture(scope="module")
def handler():
    sys.modules.setdefault("boto3", _StubBoto3())
    spec = importlib.util.spec_from_file_location("stride_handler_preview_isolation", HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_tips(root: Path, insights):
    path = root / "racecards" / "tips_2026-09-05_cloudproof.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    picks = [{"horse": f"Horse {i}", "ai_insight": text} for i, text in enumerate(insights)]
    path.write_text(json.dumps({"races": [{"track": "Randwick", "top_picks": picks}]}))


def _proof_setup(handler, monkeypatch, tmp_path, insights, card="card", raw=None):
    """insights=None means the pipeline wrote no file at all.

    raw writes that exact string as the artifact instead, for the shapes a
    list of insight texts cannot express: an empty races list, error stubs
    with no picks, and a truncated file.
    """
    calls = []
    monkeypatch.setenv("STRIDE_DATE", "2026-09-05")
    for key in SWITCHES:
        monkeypatch.setenv(key, "left-by-the-test")   # registers restoration
    monkeypatch.setattr(handler, "_root", lambda: str(tmp_path))
    monkeypatch.setattr(handler, "_tips_prepare", lambda: calls.append(("prepare",)) or card)

    def run_ok(*args, **kwargs):
        calls.append(("run",) + args)
        if raw is not None:
            path = tmp_path / "racecards" / "tips_2026-09-05_cloudproof.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
        elif insights is not None:
            _write_tips(tmp_path, insights)
        return "ok"

    monkeypatch.setattr(handler, "_run_ok", run_ok)
    monkeypatch.setattr(handler, "_sync_up", lambda *a, **k: calls.append(("sync",) + a) or 1)
    return calls


def test_the_proof_switches_off_every_incidental_write(handler, monkeypatch, tmp_path):
    calls = _proof_setup(handler, monkeypatch, tmp_path, ["THE FORM: ...", "", "THE RUN: ..."])
    result = handler.job_tips_proof()
    assert {k: os.environ[k] for k in SWITCHES} == {k: "false" for k in SWITCHES}
    assert result["insights"] == "2/3"
    assert result["last_success_date"] == "2026-09-05"
    assert ("run", "run_tips_pipeline.py", "2026-09-05", "--skip-db-store",
            "--output-suffix", "cloudproof") in calls
    assert ("sync", "racecards", "tips_2026-09-05_cloudproof.json") in calls


def test_no_insights_with_the_llm_enabled_relays_first_then_fails(handler, monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_ENABLED", raising=False)
    calls = _proof_setup(handler, monkeypatch, tmp_path, ["", "", ""])
    with pytest.raises(RuntimeError, match="0 of 3 top picks"):
        handler.job_tips_proof()
    assert any(c[0] == "sync" for c in calls), \
        "the evidence must be relayed before the check fails, or a red run leaves nothing to read"


def test_no_insights_is_the_expected_outcome_when_the_llm_is_off(handler, monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_ENABLED", "false")
    _proof_setup(handler, monkeypatch, tmp_path, ["", ""])
    assert handler.job_tips_proof()["insights"] == "0/2"


def test_a_run_that_wrote_no_file_fails_instead_of_passing_empty(handler, monkeypatch, tmp_path):
    _proof_setup(handler, monkeypatch, tmp_path, None)
    with pytest.raises(RuntimeError, match="absent"):
        handler.job_tips_proof()


def test_a_file_with_no_picks_at_all_fails(handler, monkeypatch, tmp_path):
    """The 2026-08-05 shape: every race errored, top_picks [], exit 0.

    Previously the guard read `if picks and ...`, so zero picks short-circuited
    the whole assertion and the job returned insights "0/0" — green.
    """
    _proof_setup(handler, monkeypatch, tmp_path, None, raw=json.dumps(
        {"races": [{"track": "Randwick", "race_number": 1, "error": "boom",
                    "top_picks": [], "bet_status": "ERROR"}]}))
    with pytest.raises(RuntimeError, match="no top picks at all"):
        handler.job_tips_proof()


def test_an_empty_races_list_fails(handler, monkeypatch, tmp_path):
    _proof_setup(handler, monkeypatch, tmp_path, None,
                 raw='{"date": "2026-09-05", "races": []}')
    with pytest.raises(RuntimeError, match="no top picks at all"):
        handler.job_tips_proof()


def test_an_unparseable_file_fails_rather_than_reading_as_zero_insights(
        handler, monkeypatch, tmp_path):
    _proof_setup(handler, monkeypatch, tmp_path, None,
                 raw='{"races": [{"top_picks": [{"ai_insight": "THE FORM')
    with pytest.raises(RuntimeError, match="could not be parsed"):
        handler.job_tips_proof()


def test_a_partial_llm_collapse_fails_instead_of_passing_green(handler, monkeypatch, tmp_path):
    """1 of 330 used to pass: the gate fired only at exactly zero.

    Rate limiting part-way through a card is the likely shape, not the exotic
    one — generate_rich_insight swallows every provider error into a blank
    string per pick and the run still exits 0.
    """
    monkeypatch.delenv("LLM_ENABLED", raising=False)
    _proof_setup(handler, monkeypatch, tmp_path, ["THE FORM: ..."] + [""] * 329)
    with pytest.raises(RuntimeError, match="only 1 of 330"):
        handler.job_tips_proof()


def test_coverage_at_the_floor_passes_and_just_below_it_fails(handler, monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_ENABLED", raising=False)
    assert handler.INSIGHT_COVERAGE_FLOOR == 0.80

    _proof_setup(handler, monkeypatch, tmp_path, ["x"] * 80 + [""] * 20)
    assert handler.job_tips_proof()["insights"] == "80/100"

    _proof_setup(handler, monkeypatch, tmp_path, ["x"] * 79 + [""] * 21)
    with pytest.raises(RuntimeError, match="only 79 of 100"):
        handler.job_tips_proof()


def test_llm_expected_matches_the_pipelines_own_parsing_exactly(handler, monkeypatch):
    """The handler must not be more permissive than the pipeline it asserts on.

    An added .strip() made LLM_ENABLED=' true ' mean "insights expected" here
    and "LLM off" there, failing a run that behaved exactly as configured.
    """
    pipeline_reads = lambda v: v.lower() in ("true", "1", "yes")
    for value in (" true ", "true\n", "True", "false", " ", "1", "yes", "no"):
        monkeypatch.setenv("LLM_ENABLED", value)
        assert handler._llm_expected() == pipeline_reads(value), value
    monkeypatch.delenv("LLM_ENABLED", raising=False)
    assert handler._llm_expected() is True, "unset means on, as in the pipeline"


def test_a_quiet_day_scores_nothing_and_says_so(handler, monkeypatch, tmp_path):
    calls = _proof_setup(handler, monkeypatch, tmp_path, ["x"], card="quiet")
    assert handler.job_tips_proof() == {"last_success_date": "2026-09-05", "quiet_day": True}
    assert not any(c[0] == "run" for c in calls)


PASS_OUT = "\n".join([
    "+ python llm_insight_proof.py",
    "LLM_PROOF provider=AnthropicProvider model=claude-sonnet-5",
    "LLM_PROOF ping='READY'",
    "LLM_PROOF pick=Ledger_Line chars=812",
    "----- insight for Ledger Line (first 1500 chars) -----",
    "THE FORM: ...",
    "-----",
    "LLM_PROOF pick=Blank_Docket chars=640",
    "LLM_PROOF result=PASS",
])


def test_llm_proof_reports_provider_model_and_sizes(handler, monkeypatch):
    monkeypatch.delenv("STRIDE_DATE", raising=False)
    seen = []
    monkeypatch.setattr(handler, "_run_ok", lambda *a, **k: seen.append((a, k)) or PASS_OUT)
    result = handler.job_llm_proof()
    assert seen == [(("llm_insight_proof.py",), {"ok_codes": (0, 1)})], \
        "exit 1 is a finding the markers explain, so it must be read, not raised past"
    assert result["provider"] == "AnthropicProvider"
    assert result["model"] == "claude-sonnet-5"
    assert result["insight_chars"] == "812,640"


def test_llm_proof_fails_on_a_fail_verdict_naming_the_provider(handler, monkeypatch):
    out = "\n".join([
        "LLM_PROOF provider=OllamaProvider model=llama3.2:3b",
        "LLM_PROOF ping=ERROR Cannot connect to Ollama at http://localhost:11434",
        "LLM_PROOF result=FAIL the provider cannot complete a one-word call",
    ])
    monkeypatch.setattr(handler, "_run_ok", lambda *a, **k: out)
    with pytest.raises(RuntimeError, match=r"OllamaProvider \(llama3.2:3b\)"):
        handler.job_llm_proof()


def test_llm_proof_fails_when_the_script_never_reached_a_provider(handler, monkeypatch):
    monkeypatch.setattr(handler, "_run_ok",
                        lambda *a, **k: "Traceback ...\nModuleNotFoundError: No module named 'anthropic'")
    with pytest.raises(RuntimeError, match="no LLM_PROOF provider marker"):
        handler.job_llm_proof()


def test_llm_proof_is_a_registered_hand_dispatched_job(handler):
    assert handler.JOBS["llm-proof"] is handler.job_llm_proof


def test_dispatch_says_when_the_task_pins_the_llm_provider(handler, monkeypatch, capsys):
    monkeypatch.setenv("STRIDE_JOB", "llm-proof")
    monkeypatch.delenv("STRIDE_DATE", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    for name in ("_load_secrets", "_stage_models", "_stage_panel"):
        monkeypatch.setattr(handler, name, lambda: None)
    monkeypatch.setattr(handler, "_put_state", lambda job, **kw: None)
    monkeypatch.setitem(handler.JOBS, "llm-proof", lambda: {"last_success_date": "2026-09-04"})
    handler.dispatch()
    assert "LLM_PROVIDER=anthropic set on the task" in capsys.readouterr().out


def test_verify_jobs_can_pin_the_provider_and_wait_for_a_long_task():
    text = VERIFY_JOBS.read_text()
    assert re.search(r"^\s+llm_provider:\n\s+description:", text, re.M), "no llm_provider input"
    assert re.search(r"^\s+wait_minutes:\n\s+description:", text, re.M), "no wait_minutes input"
    assert "inputs.llm_provider" in text and "LLM_PROVIDER" in text
    assert "LLM_ENABLED" in text, "pinning a provider must also switch the LLM on"
    assert re.search(r"llm-proof\)\s+TD=stride-tips-pipeline", text), \
        "llm-proof must run on the tips task definition, where the LLM secrets and image live"
    assert "seq 1 $((WAIT * 3))" in text, "the poll must honour wait_minutes"
    assert "timeout-minutes: 360" in text
    assert "STRIDE_DATE" in text and "inputs.date" in text, "the date override must survive"


def _verify_jobs_run_block():
    """The shell body of the 'Run each job and report' step, and its env names.

    Sliced out of the raw text rather than parsed: PyYAML is not in the CI
    dependency set, and a test that needs a new dependency to assert on a
    workflow is a test that will be deleted the first time CI goes red.
    """
    text = VERIFY_JOBS.read_text()
    start = text.index("- name: Run each job and report")
    tail = text.find("\n      - name:", start + 10)
    block = text[start:] if tail == -1 else text[start:tail]
    env_at = block.index("\n        env:")
    run_at = block.index("\n        run: |")
    env_names = re.findall(r"^\s+([A-Z_][A-Z0-9_]*):", block[env_at:run_at], re.M)
    return block[run_at:], env_names


def test_dispatch_inputs_reach_the_shell_as_env_not_as_interpolated_text():
    """Interpolated inline, an input is substituted into the script source
    before bash parses it, so wait_minutes' own `case` validation ran after
    the value had already executed."""
    body, env = _verify_jobs_run_block()
    assert not re.search(r"\$\{\{", body), \
        "no GitHub expression may appear inside the shell body"
    for name in ("IN_JOBS", "IN_DATE", "IN_LLM_PROVIDER", "IN_WAIT", "SUBNET_ID", "SG_ID"):
        assert name in env, f"{name} must be passed through env"


def test_the_ledger_writing_jobs_are_refused_with_a_date():
    """A rule, not a sentence in an input description: tips-proof and
    tips-pipeline differ by six characters and only one is safe to date."""
    body, _ = _verify_jobs_run_block()
    assert "tips-pipeline|tip-time-snapshot" in body
    assert re.search(r"refusing to run it with date", body)


def test_wait_minutes_is_normalised_to_base_ten():
    body, _ = _verify_jobs_run_block()
    assert "10#" in body, "a padded 030 is octal inside $(( )) and waits 24m, not 30m"


def test_the_wait_cannot_outlive_the_credentials_that_do_the_watching():
    """The OIDC role session lasts one hour.

    On 2026-09-04 a consensus run was given wait_minutes=150. At exactly 60
    minutes describe-tasks returned ExpiredTokenException, the step died with
    exit 254, and a red run was reported for a Fargate task that was still
    running and completely unaffected. Waiting longer than the credentials live
    is not a longer wait, it is a blind one.
    """
    body, _ = _verify_jobs_run_block()
    assert "WAIT=55" in body, "the wait must be clamped under the 1h session"
    assert "-gt 300" not in body, "the old 300m ceiling outlived the credentials by four hours"


def test_losing_the_credentials_is_reported_as_unobservable_not_failed():
    """The distinction that matters operationally: a task we can no longer
    watch is still running, and re-dispatching it would start a second writer
    on the same date."""
    body, _ = _verify_jobs_run_block()
    assert "UNOBSERVABLE" in body
    assert "ExpiredToken" in body, "the expiry must be detected, not inferred from silence"
    assert "still running on ECS" in body
    assert "SECOND writer" in body, "the operator must be told not to re-dispatch"
    # The reason a call failed is the whole diagnosis; discarding it to
    # /dev/null is what made an expired token look like a task problem.
    assert "2>&1 || echo \"CALL_FAILED\"" in body


def test_a_still_running_task_is_reported_apart_from_a_failure():
    """Reporting both as FAILED invited a re-dispatch that would put a second
    writer on the same date while the first was still going."""
    body, _ = _verify_jobs_run_block()
    assert 'if [ "$ST" != "STOPPED" ]' in body
    assert "OK=2" in body and "RUNNING=" in body
    assert "do NOT re-dispatch" in body
    assert "STILL RUNNING" in body, "the summary must name them separately"


def test_a_failed_dispatch_does_not_kill_the_rest_of_the_sweep():
    """run-task failing left TASK empty; describe-tasks --tasks '' exits 252 and
    bash -e killed the step, silently skipping every remaining job."""
    body, _ = _verify_jobs_run_block()
    assert '[ -z "$TASK" ]' in body
    assert "not dispatched" in body


def test_the_tips_subprocess_bound_covers_a_saturday_card_with_the_llm_on(handler):
    """Six blocking LLM calls per race, sequential, on top of MC. The old
    21600s was already 500s under its own no-LLM worst case, and a subprocess
    killed at the cap writes NO tips file at all."""
    assert handler.JOB_TIMEOUTS["run_tips_pipeline.py"] >= 28800
    # 08:05 start + the bound must still land before the 22:30 results slot.
    assert handler.JOB_TIMEOUTS["run_tips_pipeline.py"] <= 14 * 3600


def test_mc_audit_gate_defaults_on_and_reads_the_off_values(monkeypatch):
    import mc_api

    monkeypatch.delenv("STRIDE_MC_AUDIT_WRITE", raising=False)
    assert mc_api._mc_audit_write_enabled() is True
    for off in ("false", "0", "no", "OFF", " off "):
        monkeypatch.setenv("STRIDE_MC_AUDIT_WRITE", off)
        assert mc_api._mc_audit_write_enabled() is False, off
    for on in ("true", "1", "yes", " TRUE ", "on"):
        monkeypatch.setenv("STRIDE_MC_AUDIT_WRITE", on)
        assert mc_api._mc_audit_write_enabled() is True, on


def test_the_gate_fails_closed_on_a_value_it_does_not_recognise(monkeypatch):
    """An allow-list, because the fail-open direction here writes to
    prediction_audit during a preview. "" is how a variable is blanked in an
    ECS task definition, and the deny-list shape read it as ON."""
    import mc_api

    for unrecognised in ("", "   ", "n", "none", "disable", "disabled", "maybe"):
        monkeypatch.setenv("STRIDE_MC_AUDIT_WRITE", unrecognised)
        assert mc_api._mc_audit_write_enabled() is False, repr(unrecognised)


def test_the_three_mc_writers_sit_behind_the_gate():
    src = MC_API.read_text()
    start = src.index("if not _mc_audit_write_enabled():")
    block = src[start:src.index("except Exception as log_err", start)]
    for writer in ("log_feature_snapshots(_snapshot_rows)",
                   "log_prediction_audit(_audit_rows)",
                   "log_race_schedule(_race_track"):
        assert writer in block, f"{writer} is outside the STRIDE_MC_AUDIT_WRITE gate"
