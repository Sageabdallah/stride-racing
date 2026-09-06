"""The retrain gates measure what they claim to measure.

gate_status gate 5 used to run retrain_preflight.py with no --staging (which
argparse rejects) and grep for a VERDICT line the script never prints, so the
gate could never pass. Gate 3's `ok` was `all(flipped)`: the evidence-day
counts were printed and never enforced. gate_preregistration counted
[SAGE-APPROVAL] markers and never noticed the window start date was still
"_to be filled in on restart day_". These tests pin the repaired behaviour:
inputs-only preflight for gate 5, evidence + PASS review + flag for gate 3,
and the "most recent declaration governs" placeholder rule.
"""

import json
import os
import re
import subprocess
import sys
import types
from datetime import date
from pathlib import Path

import pytest

SERVER_PYTHON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_PYTHON))

import retrain_preflight as rp  # noqa: E402
import gate_status as gs  # noqa: E402


# ------------------------------------------------------ placeholder rule

PLACEHOLDER_DOC = """# Pre-registration
## WINDOW
- **Start:** the tips-restart date ... **start date: _to be filled in on restart day_**.
- **End:** the retrain cutoff.
"""


def test_unfilled_placeholder_is_reported():
    assert rp.unfilled_placeholders(PLACEHOLDER_DOC) == ["start date"]


def test_later_dated_line_supersedes_the_placeholder():
    text = PLACEHOLDER_DOC + "\n## Amendment 2026-09-05\nWindow start date: 2026-08-02 (registered in project_retrain_gate.md).\n"
    assert rp.unfilled_placeholders(text) == []


def test_supersession_needs_both_the_label_and_a_date():
    text = PLACEHOLDER_DOC + "\nThe start date will be decided later.\n"
    assert rp.unfilled_placeholders(text) == ["start date"]
    text = PLACEHOLDER_DOC + "\nSome other date: 2026-08-02.\n"
    assert rp.unfilled_placeholders(text) == ["start date"]


def test_an_earlier_dated_line_does_not_resolve_a_later_placeholder():
    text = "Window start date: 2026-08-02\n" + PLACEHOLDER_DOC
    assert rp.unfilled_placeholders(text) == ["start date"]


def test_gate_preregistration_placeholder_is_amber_and_named(tmp_path):
    doc = tmp_path / "12-preregistration.md"
    doc.write_text(PLACEHOLDER_DOC)
    row = rp.gate_preregistration(doc)[0]
    assert row["status"] == rp.AMBER
    assert "start date" in row["detail"] and "amendment" in row["detail"]

    doc.write_text(PLACEHOLDER_DOC + "\n## Amendment\nWindow start date: 2026-08-02\n")
    row = rp.gate_preregistration(doc)[0]
    assert row["status"] == rp.GREEN and "no unfilled placeholders" in row["detail"]

    doc.write_text(PLACEHOLDER_DOC + "\n[SAGE-APPROVAL] confirm\n")
    assert rp.gate_preregistration(doc)[0]["status"] == rp.AMBER


def test_the_real_preregistration_doc_reports_its_state():
    """Whatever the live document says today, the gate must read it as a
    decision (GREEN or a named AMBER), never crash on it."""
    row = rp.gate_preregistration()[0]
    assert row["status"] in (rp.GREEN, rp.AMBER), row


# --------------------------------------------------------- inputs-only

def _write_cols_file(path: Path, cols, nan_preserve=None, class_attr=False):
    if class_attr:
        text = "class M:\n    FEATURE_COLUMNS = %r\n" % (cols,)
    else:
        text = "FEATURE_COLUMNS = %r\n" % (cols,)
    if nan_preserve is not None:
        text += ("    " if class_attr else "") + "NAN_PRESERVE_FEATURES = %r\n" % (nan_preserve,)
    path.write_text(text)
    return path


def test_lockstep_without_a_candidate_compares_the_two_sources(tmp_path):
    cols = ["a", "b", "c"]
    retrain_f = _write_cols_file(tmp_path / "retrain_v2.py", cols)
    model_f = _write_cols_file(tmp_path / "ml_model.py", cols, class_attr=True)
    rows, parsed = rp.gate_lockstep(None, retrain_f, model_f)
    row = next(r for r in rows if r["name"] == "lockstep:columns")
    assert row["status"] == rp.GREEN and "no candidate artifact" in row["detail"]
    assert parsed == cols

    model_f = _write_cols_file(tmp_path / "ml_model.py", cols + ["d"], class_attr=True)
    rows, _ = rp.gate_lockstep(None, retrain_f, model_f)
    assert next(r for r in rows if r["name"] == "lockstep:columns")["status"] == rp.RED


def test_inputs_only_runs_exactly_the_staging_independent_gates(tmp_path, monkeypatch):
    import feature_liveness_audit as fla
    monkeypatch.setattr(fla, "audit_static", lambda cols=None: {
        "n_features": 3, "verdict_counts": {"LIVE_BOTH": 3}, "features": []})
    cols = ["a", "b", "c"]
    retrain_f = _write_cols_file(tmp_path / "retrain_v2.py", cols)
    model_f = _write_cols_file(tmp_path / "ml_model.py", cols, class_attr=True)
    prereg = tmp_path / "prereg.md"
    prereg.write_text("# Plan\nAll approved. Window start date: 2026-08-02\n")
    boards = rp.run_inputs_preflight(
        prereg_path=prereg,
        parity_test_paths={name: str(tmp_path / name) for name in rp.PARITY_TESTS},
        retrain_path=retrain_f, ml_model_path=model_f)
    assert boards["mode"] == "inputs-only"
    names = {r["name"] for r in boards["board1"] + boards["board2"]}
    assert names == {"liveness:serve", "lockstep:columns", "lockstep:nan-preserve",
                     "parity:test_feature_parity.py", "parity:test_serve_feature_liveness.py",
                     "asof-td-profiles", "preregistration"}
    for artifact_gate in ("artifact-loads", "liveness:trained", "freshness:trained_at",
                          "freshness:version", "shadow-metrics", "staging!=live", "live-backup"):
        assert artifact_gate not in names
    # absent parity suites are PEND (visible), never silently GREEN
    assert all(r["status"] == rp.AMBER for r in boards["board1"] if r["name"].startswith("parity:"))


def test_cli_requires_exactly_one_mode():
    for argv in ([], ["--staging", "x.pkl", "--inputs-only"]):
        proc = subprocess.run([sys.executable, str(SERVER_PYTHON / "retrain_preflight.py"), *argv],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 2, (argv, proc.stderr)
        assert "exactly one of" in proc.stderr


def test_cli_inputs_only_json_is_a_readable_board_on_this_repo():
    """The real thing gate 5 runs. Colours are findings about the repo and
    may legitimately be RED or PEND today; the shape must always parse."""
    proc = subprocess.run(
        [sys.executable, str(SERVER_PYTHON / "retrain_preflight.py"), "--inputs-only", "--json"],
        capture_output=True, text=True, timeout=300, cwd=str(SERVER_PYTHON))
    boards = json.loads(proc.stdout)
    assert boards["mode"] == "inputs-only"
    rows = boards["board1"] + boards["board2"]
    assert {r["name"] for r in rows} >= {"liveness:serve", "lockstep:columns",
                                         "asof-td-profiles", "preregistration"}
    assert all(r["status"] in (rp.GREEN, rp.RED, rp.AMBER) for r in rows)
    n_red = sum(1 for r in rows if r["status"] == rp.RED)
    assert proc.returncode == (1 if n_red else 0)


# ------------------------------------------------------------- gate 3

@pytest.fixture
def local_store(tmp_path, monkeypatch):
    import evidence_store
    monkeypatch.delenv("STRIDE_EVIDENCE_BUCKET", raising=False)
    monkeypatch.setattr(evidence_store, "local_dir", lambda: tmp_path)
    return evidence_store


def _days(store, stem, n):
    """n day files, 2026-08-10 onward. Content is irrelevant to gate 3 now:
    it reads the reviewer's clean-day count, not the store's filename count."""
    for i in range(n):
        store.put_evidence(f"{stem}_2026-08-{10 + i:02d}.json", "[]")


def _record(which, verdict="PASS", n_clean=5, flag=None):
    """What shadow_flip_review.run_review emits: verdict, the flag it is
    about, and the clean-day count it was computed on."""
    import shadow_flip_review as sfr
    return {"auto_verdict": verdict, "flag": flag or sfr.FLAG_NAMES[which],
            "n_days": n_clean, "n_clean_days": n_clean}


def _reviews(verdict_serve="PASS", verdict_renorm="PASS", n_clean=5,
             on=date(2026, 9, 5), flag_serve=None, flag_renorm=None):
    import shadow_flip_review as sfr
    sfr.emit_review("serve", _record("serve", verdict_serve, n_clean, flag_serve), on=on)
    sfr.emit_review("renorm", _record("renorm", verdict_renorm, n_clean, flag_renorm), on=on)


def test_gate3_flags_alone_no_longer_pass(local_store, monkeypatch):
    monkeypatch.setenv("STRIDE_SERVE_LIVE_FEATURES", "true")
    monkeypatch.setenv("STRIDE_RENORMALISE_FIELD", "true")
    g = gs.gate3_shadow_flips()
    assert g["ok"] is False
    assert "serve=NONE" in g["detail"] and "renorm=NONE" in g["detail"]


def test_gate3_needs_days_reviews_and_flags(local_store, monkeypatch):
    _days(local_store, "serve_liveness_shadow", 5)
    _days(local_store, "calibrator_shadow", 5)
    monkeypatch.setenv("STRIDE_SERVE_LIVE_FEATURES", "1")
    monkeypatch.setenv("STRIDE_RENORMALISE_FIELD", "yes")

    g = gs.gate3_shadow_flips()
    assert g["ok"] is False and "shadow_flip_review.py --emit-evidence" in g["detail"]

    _reviews()
    g = gs.gate3_shadow_flips()
    assert g["ok"] is True, g["detail"]
    assert "serve=PASS@2026-09-05 on 5 clean days" in g["detail"]
    assert "renorm=PASS@2026-09-05 on 5 clean days" in g["detail"]


def test_gate3_a_pass_record_is_bound_to_the_flag_it_is_about(local_store, monkeypatch):
    """Until 2026-09-06 a record's verdict was read without its flag: the
    renorm record could be a copy of the serve one."""
    _days(local_store, "serve_liveness_shadow", 5)
    _days(local_store, "calibrator_shadow", 5)
    monkeypatch.setenv("STRIDE_SERVE_LIVE_FEATURES", "true")
    monkeypatch.setenv("STRIDE_RENORMALISE_FIELD", "true")
    _reviews(flag_renorm="STRIDE_SERVE_LIVE_FEATURES")
    g = gs.gate3_shadow_flips()
    assert g["ok"] is False and "record is about 'STRIDE_SERVE_LIVE_FEATURES', not STRIDE_RENORMALISE_FIELD" in g["detail"]


def test_gate3_reads_the_reviewers_clean_day_count_not_the_file_count(local_store, monkeypatch):
    """Six files in the store, but the reviewer found only four clean days
    (empty or runner-less files do not count): not enough."""
    _days(local_store, "serve_liveness_shadow", 6)
    _days(local_store, "calibrator_shadow", 6)
    monkeypatch.setenv("STRIDE_SERVE_LIVE_FEATURES", "true")
    monkeypatch.setenv("STRIDE_RENORMALISE_FIELD", "true")
    _reviews(n_clean=4)
    g = gs.gate3_shadow_flips()
    assert g["ok"] is False and "computed on 4 clean day(s) < 5" in g["detail"]


def test_gate3_later_evidence_invalidates_an_earlier_pass(local_store, monkeypatch):
    """A PASS emitted on the 14th; a day file for the 15th arrives (it may be
    the dirty day that restarts the count). The record is stale until the
    review is re-run on the whole store."""
    _days(local_store, "serve_liveness_shadow", 5)          # 08-10 .. 08-14
    _days(local_store, "calibrator_shadow", 5)
    monkeypatch.setenv("STRIDE_SERVE_LIVE_FEATURES", "true")
    monkeypatch.setenv("STRIDE_RENORMALISE_FIELD", "true")
    _reviews(on=date(2026, 8, 14))
    assert gs.gate3_shadow_flips()["ok"] is True
    local_store.put_evidence("serve_liveness_shadow_2026-08-15.json", "[]")
    g = gs.gate3_shadow_flips()
    assert g["ok"] is False
    assert "STALE: record 2026-08-14 predates evidence 2026-08-15" in g["detail"]
    _reviews(on=date(2026, 8, 15))
    assert gs.gate3_shadow_flips()["ok"] is True

    monkeypatch.setenv("STRIDE_RENORMALISE_FIELD", "false")
    assert gs.gate3_shadow_flips()["ok"] is False


def test_gate3_a_failing_review_blocks_even_with_days_and_flags(local_store, monkeypatch):
    _days(local_store, "serve_liveness_shadow", 6)
    _days(local_store, "calibrator_shadow", 6)
    monkeypatch.setenv("STRIDE_SERVE_LIVE_FEATURES", "true")
    monkeypatch.setenv("STRIDE_RENORMALISE_FIELD", "true")
    _reviews(verdict_renorm="FAIL")
    g = gs.gate3_shadow_flips()
    assert g["ok"] is False and "renorm=FAIL" in g["detail"]


def test_gate3_too_few_days_blocks_even_with_pass_reviews(local_store, monkeypatch):
    _days(local_store, "serve_liveness_shadow", 4)
    _days(local_store, "calibrator_shadow", 5)
    monkeypatch.setenv("STRIDE_SERVE_LIVE_FEATURES", "true")
    monkeypatch.setenv("STRIDE_RENORMALISE_FIELD", "true")
    import shadow_flip_review as sfr
    sfr.emit_review("serve", _record("serve", n_clean=4), on=date(2026, 9, 5))
    sfr.emit_review("renorm", _record("renorm", n_clean=5), on=date(2026, 9, 5))
    g = gs.gate3_shadow_flips()
    assert g["ok"] is False and "computed on 4 clean day(s) < 5" in g["detail"]


# ------------------------------------------------------------- gate 5

def _fake_run(boards, rc=0):
    def run(*a, **k):
        return types.SimpleNamespace(stdout=json.dumps(boards), stderr="", returncode=rc)
    return run


def test_gate5_calls_inputs_only_and_passes_on_all_green(monkeypatch):
    seen = {}

    def run(cmd, **k):
        seen["cmd"] = cmd
        return types.SimpleNamespace(stdout=json.dumps({
            "board1": [{"name": "lockstep:columns", "status": "GREEN", "detail": ""}],
            "board2": [{"name": "preregistration", "status": "GREEN", "detail": ""}],
            "mode": "inputs-only"}), stderr="", returncode=0)

    monkeypatch.setattr(gs.subprocess, "run", run)
    g = gs.gate5_preflight()
    assert "--inputs-only" in seen["cmd"] and "--json" in seen["cmd"]
    assert "--staging" not in seen["cmd"]
    assert g["ok"] is True and "all GREEN" in g["detail"]


def test_gate5_pend_is_a_blocker_here(monkeypatch):
    boards = {"board1": [{"name": "asof-td-profiles", "status": "GREEN", "detail": ""}],
              "board2": [{"name": "preregistration", "status": "AMBER", "detail": "x"}]}
    monkeypatch.setattr(gs.subprocess, "run", _fake_run(boards, rc=0))
    g = gs.gate5_preflight()
    assert g["ok"] is False and "preregistration=AMBER" in g["detail"]


def test_gate5_red_blocks(monkeypatch):
    boards = {"board1": [{"name": "liveness:serve", "status": "RED", "detail": "2 dead"}], "board2": []}
    monkeypatch.setattr(gs.subprocess, "run", _fake_run(boards, rc=1))
    g = gs.gate5_preflight()
    assert g["ok"] is False and "liveness:serve=RED" in g["detail"]


def test_gate5_unreadable_output_is_a_named_wait(monkeypatch):
    monkeypatch.setattr(gs.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="usage: retrain_preflight.py [-h] --staging STAGING", stderr="", returncode=2))
    g = gs.gate5_preflight()
    assert g["ok"] is False and "unreadable preflight output" in g["detail"]


def test_preflight_board_verdict_is_every_row_green():
    """One rule for gate 5 and for the retrain-model workflow's refusal:
    ok only when every row is GREEN. AMBER blocks, RED blocks, and an empty
    board — a preflight that evaluated nothing — is not green either."""
    green = {"board1": [{"name": "a", "status": "GREEN", "detail": ""}],
             "board2": [{"name": "b", "status": "GREEN", "detail": ""}]}
    assert gs.preflight_board_verdict(green) == (True, "2 gate(s) all GREEN")
    amber = {"board1": [{"name": "a", "status": "GREEN", "detail": ""}],
             "board2": [{"name": "preregistration", "status": "AMBER", "detail": ""}]}
    ok, detail = gs.preflight_board_verdict(amber)
    assert ok is False and detail == "preregistration=AMBER"
    ok, detail = gs.preflight_board_verdict({"board1": [], "board2": []})
    assert ok is False and "empty" in detail
    with pytest.raises((KeyError, TypeError)):
        gs.preflight_board_verdict({"mode": "inputs-only"})


def test_preflight_board_cli_exits_nonzero_unless_fully_green(tmp_path, capsys):
    """`gate_status.py --preflight-board FILE` is what the workflow runs on the
    JSON it wrote; it must never need a database and must fail closed on a
    file it cannot read."""
    board = tmp_path / "b.json"
    board.write_text(json.dumps({"board1": [{"name": "a", "status": "GREEN", "detail": ""}],
                                 "board2": []}), encoding="utf-8")
    assert gs._preflight_board_cli(str(board)) == 0
    board.write_text(json.dumps({"board1": [{"name": "a", "status": "RED", "detail": ""}],
                                 "board2": []}), encoding="utf-8")
    assert gs._preflight_board_cli(str(board)) == 1
    assert "a=RED" in capsys.readouterr().out
    board.write_text("not json", encoding="utf-8")
    assert gs._preflight_board_cli(str(board)) == 1
    assert gs._preflight_board_cli(str(tmp_path / "absent.json")) == 1
    proc = subprocess.run([sys.executable, str(SERVER_PYTHON / "gate_status.py"),
                           "--preflight-board", str(board)],
                          capture_output=True, text=True, env={**os.environ, "DATABASE_URL": ""})
    assert proc.returncode == 1 and "unreadable" in proc.stdout, proc.stdout + proc.stderr


def test_parity_gate_without_pytest_is_a_named_amber_row(monkeypatch, tmp_path):
    """The Fargate image is built from requirements.txt. Before pytest was
    listed there, gate_parity_suites raised ImportError inside the scheduled
    preflight and gate 5 read 'unreadable preflight output' every morning.
    Missing runner = visible AMBER row, and the dependency is pinned in the
    file the image is built from."""
    suite = tmp_path / "test_feature_parity.py"
    suite.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    paths = {name: str(suite) for name in rp.PARITY_TESTS}
    monkeypatch.setitem(sys.modules, "pytest", None)   # `import pytest` -> ImportError
    rows = rp.gate_parity_suites(paths)
    assert rows and all(r["status"] == rp.AMBER for r in rows)
    assert all("pytest is not installed" in r["detail"] for r in rows)
    requirements = (SERVER_PYTHON.parents[1] / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^pytest\b", requirements, re.M), \
        "the image that runs gate 5 daily must carry the runner the gate needs"


def test_gate5_against_the_real_script_parses(monkeypatch):
    """The old gate failed here every day: argparse exit 2, no VERDICT line.
    Whatever the repo's inputs say today, the gate must now read a board."""
    g = gs.gate5_preflight()
    assert "unreadable" not in g["detail"], g
    assert g["name"] == "5. retrain inputs preflight"
