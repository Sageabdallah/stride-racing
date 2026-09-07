"""Tests for flag_state.py — the resolved-flag diagnostic.

The value of this diagnostic is entirely in its scanner not under-counting, so
the tests assert on NAMED flags whose discovery depends on one parser feature
each. A test that asserted "at least N flags found" would pass on exactly the
regression it exists to catch, which is the failure mode `feature_liveness_audit`
already has (it credits features as served on flag-gated evidence and contains
no reference to flags at all).

No DB, no network, no model artifacts: the whole thing is a source scan plus
os.environ.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flag_state as fs  # noqa: E402

HANDLER_PATH = (Path(__file__).resolve().parents[3] / "infra" / "jobs" / "handler.py")


class _StubBoto3:
    """Offline like the rest of the suite; every boto3 call in the handler is
    inside a function body, so importing it only needs the name to exist."""

    def __getattr__(self, name):
        raise AssertionError(f"boto3.{name} must not be called in this test")


class _Completedish:
    """Enough of subprocess.CompletedProcess for the job under test."""

    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def _proc(stdout: str, stderr: str = "", returncode: int = 0):
    return lambda *a, **k: _Completedish(stdout, stderr, returncode)


@pytest.fixture(scope="module")
def handler():
    sys.modules.setdefault("boto3", _StubBoto3())
    spec = importlib.util.spec_from_file_location("stride_handler_flagstate", HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["stride_handler_flagstate"] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# Read-shape coverage — one named flag per shape
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def readers():
    return fs.scan_readers()


@pytest.fixture(scope="module")
def report():
    return fs.build_report()


def test_finds_direct_os_environ_get(readers):
    """Shape 1: os.environ.get("STRIDE_X", "legacy")."""
    rec = readers["STRIDE_TRAIN_ODDS_SOURCE"]
    assert rec["sites"], "no read site for a plain os.environ.get flag"
    assert "'legacy'" in rec["defaults"]


@pytest.mark.parametrize("flag", [
    "STRIDE_EV_GATE_AT_PRICE",
    "STRIDE_RENORMALISE_FIELD",
    "STRIDE_LEDGER_WRITE",
    "STRIDE_SERVE_LIVE_FEATURES",
])
def test_finds_helper_mediated_reads(readers, flag):
    """Shape 2: read only through _flag_enabled/_stride_flag.

    This is the dominant idiom in the repo. A scanner that knows only shape 1
    misses most flags -- measured at 53 of 88 on the first draft -- and would
    report the fixes this diagnostic exists to find as absent entirely.
    """
    assert flag in readers, f"{flag} is read only via a helper and was missed"
    assert readers[flag]["sites"], f"{flag} recorded no read site"
    assert any(s["shape"].startswith("helper:") for s in readers[flag]["sites"])


def test_finds_const_mediated_setter():
    """Shape 3: F = "STRIDE_X" ... os.environ.setdefault(F, "true").

    handler.py turns the context-multiplier diagnostic on for cloud tips runs
    through a module-level constant. Miss it and the flag reads as having no
    delivery path when the image sets it on every run.
    """
    setters = fs.handler_setters()
    assert "STRIDE_CTX_MULT_DIAG" in setters
    assert setters["STRIDE_CTX_MULT_DIAG"], "no setter site recorded"


# --------------------------------------------------------------------------
# Precision — the scanner must not invent flags
# --------------------------------------------------------------------------

def test_rejects_sentences_that_start_with_the_prefix():
    """`print("STRIDE_LEARNED_BLEND ignored ...")` is not a flag named after
    the sentence. The first draft of this scanner produced exactly that."""
    assert not fs._is_flag_name("STRIDE_LEARNED_BLEND ignored")
    assert not fs._is_flag_name("STRIDE_")
    assert not fs._is_flag_name("STRIDE_lowercase")
    assert fs._is_flag_name("STRIDE_EV_GATE_AT_PRICE")


def test_helper_discovery_requires_an_os_environ_receiver():
    """`.get` is the commonest method in Python. Matching it on any receiver
    made `_style_ordinal(name, 1)` look like a flag helper; the discovered set
    must contain only functions that actually read the environment."""
    helpers = fs.discover_helpers()
    assert "_flag_enabled" in helpers and "_stride_flag" in helpers
    for bogus in ("_style_ordinal", "_map_prep_position", "_cli_horse",
                  "_feature_to_explanation", "_run"):
        assert bogus not in helpers, f"{bogus} is not an env helper"


def test_test_sites_do_not_define_the_code_default(readers):
    """feature_interactions.py's --self-test pops the flag and asserts the
    default-off branch through a default-off helper, while the production
    reader in serve_features.py is default-on. Counting the self-test as a
    reader manufactures a defect that does not exist."""
    rec = readers["STRIDE_INTERACTION_PARITY"]
    assert rec["defaults"] == ["'true'"], (
        "self-test read sites leaked into the production default set")
    assert any(s["test"] for s in rec["sites"]), "expected the self-test sites to be recorded"
    assert any(not s["test"] for s in rec["sites"]), "expected a production site too"


# --------------------------------------------------------------------------
# Delivery — the half a grep cannot answer
# --------------------------------------------------------------------------

def test_secret_keys_parsed_by_identity_not_count():
    """A count passes while the wrong nine keys ship. `put-secret-value`
    replaces the whole blob, so this list is exactly the set of flags a
    hand edit survives."""
    assert fs.secret_keys() == {
        "STRIDE_BOOK_COHERENCE", "STRIDE_COMMISSION_RATE", "STRIDE_LEDGER_WRITE",
        "STRIDE_MODEL_WEIGHT", "STRIDE_RENORMALISE_FIELD", "STRIDE_SERVE_LIVE_FEATURES",
        "STRIDE_SERVE_LIVE_FEATURES_SHADOW", "STRIDE_SERVE_NAN_CONTRACT",
        "STRIDE_SHADOW_KELLY",
    }


@pytest.mark.parametrize("flag", ["STRIDE_EV_GATE_AT_PRICE", "STRIDE_ML_APPLY_ISOTONIC"])
def test_undeliverable_fixes_are_reported(report, flag):
    """Both are written, tested and default-off, and neither is in the secret
    blob, set by the image, or passable by a workflow -- so neither can be
    switched on without a code change. That is the finding."""
    assert flag in report["no_delivery_path"]
    assert report["flags"][flag]["delivery"] == []


def test_deliverable_flag_not_reported_as_gated(report):
    """The inverse tripwire: if the delivery scan silently broke, every flag
    would look undeliverable and the report would be uniformly alarming."""
    assert "STRIDE_SERVE_LIVE_FEATURES" not in report["no_delivery_path"]
    assert "secret" in report["flags"]["STRIDE_SERVE_LIVE_FEATURES"]["delivery"]


def test_workflow_override_path_detected():
    """verify-jobs.yml passes STRIDE_DATE through containerOverrides -- the
    third delivery path, and the cheapest one for a shadow run."""
    assert "STRIDE_DATE" in fs.workflow_overrides()


# --------------------------------------------------------------------------
# Runtime half
# --------------------------------------------------------------------------

def test_resolved_value_and_provenance_track_the_environment(monkeypatch):
    monkeypatch.setenv("STRIDE_EV_GATE_AT_PRICE", "true")
    rep = fs.build_report()
    assert rep["flags"]["STRIDE_EV_GATE_AT_PRICE"]["resolved"] == "true"
    assert rep["flags"]["STRIDE_EV_GATE_AT_PRICE"]["effective"] == "true"

    monkeypatch.delenv("STRIDE_EV_GATE_AT_PRICE", raising=False)
    rep = fs.build_report()
    entry = rep["flags"]["STRIDE_EV_GATE_AT_PRICE"]
    assert entry["resolved"] is None
    assert entry["provenance"] == "unset"
    assert entry["effective"] == "'false'"


def test_setdefault_ambiguity_is_reported_not_guessed(monkeypatch):
    """_load_secrets merges with setdefault, so after the merge an identical
    value could have come from either layer. Reporting a guess here would be
    the same proxy-for-a-fact mistake the diagnostic exists to end."""
    monkeypatch.setenv("STRIDE_LEDGER_WRITE", "true")
    rep = fs.build_report({"STRIDE_LEDGER_WRITE": "true"})
    assert "ambiguous" in rep["flags"]["STRIDE_LEDGER_WRITE"]["provenance"]

    rep = fs.build_report({"STRIDE_LEDGER_WRITE": "false"})
    assert rep["flags"]["STRIDE_LEDGER_WRITE"]["provenance"] == "task-env overrode secret"


def test_scope_never_claims_production_from_a_checkout(monkeypatch):
    monkeypatch.delenv("AWS_EXECUTION_ENV", raising=False)
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert "NOT production" in fs.build_report()["scope"]

    monkeypatch.setenv("AWS_EXECUTION_ENV", "AWS_ECS_FARGATE")
    monkeypatch.setenv("STRIDE_JOB", "tips-pipeline")
    assert "tips-pipeline" in fs.build_report()["scope"]


@pytest.mark.parametrize("env,expect", [
    ({}, "NOT production"),
    ({"CI": "true"}, "ci"),
    ({"AWS_EXECUTION_ENV": "AWS_ECS_FARGATE", "STRIDE_JOB": "flag-state"}, "container"),
])
def test_self_test_passes_in_every_environment_scope_can_report(monkeypatch, env, expect):
    """The first version of the scope tripwire allowed for two environments
    while _scope() returns three, so it failed every CI run on a scope that
    was correct. Running the self-test under each answer _scope() can give is
    the check that would have caught it -- locally, where it was written."""
    for key in ("CI", "AWS_EXECUTION_ENV", "ECS_CONTAINER_METADATA_URI_V4", "STRIDE_JOB"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert expect in fs.build_report()["scope"]
    assert fs.self_test() == 0, f"self-test must pass under scope={expect!r}"


def test_secret_values_of_non_stride_keys_are_never_returned():
    """The same blob holds DATABASE_URL and four API keys. A diagnostic that
    dumps its environment is a credential leak, not a diagnostic."""
    import types

    fake = types.SimpleNamespace()

    class _Client:
        def get_secret_value(self, SecretId):  # noqa: N803 - boto3 signature
            import json as _json
            return {"SecretString": _json.dumps({
                "DATABASE_URL": "postgres://user:pw@host/db",
                "ANTHROPIC_API_KEY": "sk-secret",
                "STRIDE_LEDGER_WRITE": "true",
            })}

    fake.client = lambda *a, **k: _Client()
    sys.modules["boto3"] = fake
    try:
        got = fs.fetch_secret_stride_keys("stride/prod")
    finally:
        del sys.modules["boto3"]
    assert got == {"STRIDE_LEDGER_WRITE": "true"}


def test_json_and_table_render_without_error(capsys):
    assert fs.main(["--json"]) == 0
    assert "\"flags\"" in capsys.readouterr().out
    assert fs.main([]) == 0
    out = capsys.readouterr().out
    assert "STRIDE FLAG STATE" in out and "DELIVERY" in out


def test_self_test_passes():
    assert fs.self_test() == 0


# --------------------------------------------------------------------------
# Exact provenance, and the cloud job that supplies it
# --------------------------------------------------------------------------

def test_recorded_origin_beats_value_comparison(monkeypatch):
    """The merge record turns "ambiguous" into a fact. Comparing values can
    only ever say the two layers agree; only the merger knows who won."""
    monkeypatch.setenv("STRIDE_LEDGER_WRITE", "true")
    monkeypatch.setenv("STRIDE_SERVE_NAN_CONTRACT", "true")
    monkeypatch.setenv(fs.ORIGIN_ENV, json.dumps({
        "STRIDE_LEDGER_WRITE": "secret",
        "STRIDE_SERVE_NAN_CONTRACT": "task-env",
    }))
    rep = fs.build_report(secret_origin=fs.secret_origin_from_env())
    assert rep["origin_recorded"] is True
    assert rep["flags"]["STRIDE_LEDGER_WRITE"]["provenance"] == "secret"
    assert rep["flags"]["STRIDE_SERVE_NAN_CONTRACT"]["provenance"] \
        == "task-env overrode secret"


def test_origin_env_is_not_itself_a_stride_flag():
    """Naming the channel STRIDE_* would make the diagnostic report itself as
    an undeliverable flag on every run."""
    assert not fs.ORIGIN_ENV.startswith("STRIDE_")
    assert fs.secret_origin_from_env.__doc__


@pytest.mark.parametrize("raw", ["", "not json", '["a"]', "null"])
def test_malformed_origin_degrades_to_ambiguous(monkeypatch, raw):
    monkeypatch.setenv(fs.ORIGIN_ENV, raw)
    assert fs.secret_origin_from_env() is None


def test_load_secrets_records_which_layer_won(handler, monkeypatch):
    """_load_secrets is the only moment the two layers are distinguishable."""
    blob = {"STRIDE_LEDGER_WRITE": "true", "STRIDE_SERVE_NAN_CONTRACT": "true",
            "DATABASE_URL": "postgres://u:p@h/db"}

    class _SM:
        def get_secret_value(self, SecretId):  # noqa: N803 - boto3 signature
            return {"SecretString": json.dumps(blob)}

    monkeypatch.setattr(handler, "boto3",
                        type("B", (), {"client": staticmethod(lambda *a, **k: _SM())}))
    monkeypatch.setenv("STRIDE_LEDGER_WRITE", "false")   # already on the task
    monkeypatch.delenv("STRIDE_SERVE_NAN_CONTRACT", raising=False)
    handler._SECRET_ORIGIN.clear()
    handler._load_secrets()

    assert handler._SECRET_ORIGIN["STRIDE_LEDGER_WRITE"] == "task-env"
    assert handler._SECRET_ORIGIN["STRIDE_SERVE_NAN_CONTRACT"] == "secret"
    # setdefault semantics preserved: the task value must still win.
    assert os.environ["STRIDE_LEDGER_WRITE"] == "false"
    assert os.environ["STRIDE_SERVE_NAN_CONTRACT"] == "true"


def _dispatch_wiring():
    """(jobs registered, families registered, lambda jobs, explicit TD map)."""
    repo = Path(__file__).resolve().parents[3]
    wf = (repo / ".github" / "workflows" / "verify-jobs.yml").read_text(encoding="utf-8")

    td_map = dict(re.findall(r"^\s*([a-z0-9-]+)\)\s*TD=(\S+)\s*;;", wf, re.M))
    td_map.pop("*", None)
    lam = set(re.search(r'LAMBDA_JOBS:\s*"([^"]*)"', wf).group(1).split())

    heavy = (repo / "infra" / "07_fargate_heavy.sh").read_text(encoding="utf-8")
    block = heavy.split("for spec in", 1)[1].split("; do", 1)[0]
    families = {f"stride-{m}" for m in re.findall(r'"([a-z0-9-]+)\s+\d+\s+\d+"', block)}
    return families, lam, td_map


def test_every_registered_job_can_actually_be_dispatched(handler):
    """A job in JOBS that no task definition can run is a job that exists only
    on paper.

    verify-jobs falls through to `TD="stride-$JOB"`, so registering a handler
    job LOOKS sufficient — routing to Fargate succeeds and the family is
    resolved. It is not: run-task then fails on a family nobody registered,
    and the failure reads as a broken job rather than a missing task
    definition. I asserted "registration alone is enough" on the strength of
    the routing and did not check the family existed; this is the check that
    would have caught it.

    infra/*.sh is off limits to this change, so the fix is a TD case in the
    workflow, exactly as the four proof jobs already do.
    """
    families, lam, td_map = _dispatch_wiring()
    undispatchable = []
    for job in handler.JOBS:
        if job in lam:
            continue                      # runs as a Lambda, no task definition
        family = td_map.get(job, f"stride-{job}")
        if family not in families:
            undispatchable.append(f"{job} -> {family} (no such family)")
    assert not undispatchable, (
        "these jobs resolve to a task-definition family that is never "
        f"registered: {undispatchable}")


def test_borrowed_task_definitions_get_their_job_name_by_override():
    """Borrowing a family is only safe because containerOverrides replaces
    STRIDE_JOB; the family bakes in its own name (07_fargate_heavy.sh:77), so
    without the override flag-state would silently run preflight."""
    repo = Path(__file__).resolve().parents[3]
    wf = (repo / ".github" / "workflows" / "verify-jobs.yml").read_text(encoding="utf-8")
    assert 'containerOverrides' in wf
    assert re.search(r'containerOverrides.*STRIDE_JOB.*\$JOB', wf), \
        "the dispatch must override STRIDE_JOB, or a borrowed family runs its own job"


def test_flag_state_job_is_registered_and_writes_nothing(handler):
    assert handler.JOBS["flag-state"] is handler.job_flag_state
    src = HANDLER_PATH.read_text(encoding="utf-8")
    body = src.split("def job_flag_state(")[1].split("\ndef ")[0]
    for forbidden in ("_sync_up(", "_put_state(", "psycopg2", "store_selections"):
        assert forbidden not in body, f"flag-state must not {forbidden}"


def test_flag_state_job_refuses_an_empty_scan(handler, monkeypatch):
    """The silent no-op class the repo keeps rediscovering: a scan that finds
    nothing exits 0 and reads as a clean bill of health."""
    monkeypatch.setattr(handler, "_run", _proc(json.dumps(
        {"counts": {"with_read_sites": 0}, "flags": {}})))
    monkeypatch.setattr(handler, "_today", lambda: "2026-09-07")
    with pytest.raises(RuntimeError, match="broken scan"):
        handler.job_flag_state()


def test_flag_state_job_refuses_unparseable_output(handler, monkeypatch):
    monkeypatch.setattr(handler, "_run", _proc("not json at all"))
    monkeypatch.setattr(handler, "_today", lambda: "2026-09-07")
    with pytest.raises(RuntimeError, match="did not emit JSON"):
        handler.job_flag_state()


def test_flag_state_job_fails_on_a_nonzero_exit(handler, monkeypatch):
    monkeypatch.setattr(handler, "_run", _proc("{}", returncode=2))
    monkeypatch.setattr(handler, "_today", lambda: "2026-09-07")
    with pytest.raises(RuntimeError, match="exited 2"):
        handler.job_flag_state()


def test_flag_state_job_passes_only_stride_origins_downstream(handler, monkeypatch):
    """The origin map is handed to a subprocess; it must carry flag names and
    the layer, never a secret value, and never a non-STRIDE key."""
    seen = {}

    def _fake_run(script, *args, **kw):
        seen["script"] = script
        seen["args"] = args
        seen["origin"] = os.environ.get("FLAG_STATE_SECRET_ORIGIN")
        return _Completedish(json.dumps(
            {"counts": {"with_read_sites": 3, "names_seen": 3},
             "flags": {"STRIDE_LEDGER_WRITE": {"resolved": "true"}},
             "no_delivery_path": [], "scope": "container"}))

    monkeypatch.setattr(handler, "_run", _fake_run)
    monkeypatch.setattr(handler, "_today", lambda: "2026-09-07")
    handler._SECRET_ORIGIN.clear()
    handler._SECRET_ORIGIN.update({"STRIDE_LEDGER_WRITE": "secret",
                                   "DATABASE_URL": "secret"})
    out = handler.job_flag_state()

    assert seen["script"] == "flag_state.py"
    assert "--json" in seen["args"] and "--evidence" in seen["args"]
    assert "flag_state_2026-09-07.json" in seen["args"]
    passed = json.loads(seen["origin"])
    assert passed == {"STRIDE_LEDGER_WRITE": "secret"}, "non-STRIDE key leaked downstream"
    assert out["evidence"] == "flag_state_2026-09-07.json"
    assert out["flags_on"] == 1
