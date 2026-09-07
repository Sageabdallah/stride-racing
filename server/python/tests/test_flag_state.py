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

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flag_state as fs  # noqa: E402


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
