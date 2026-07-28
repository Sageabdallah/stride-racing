#!/usr/bin/env python3
"""STRIDE retrain promotion preflight — the "never promote directly" rule as a gate.

One command, two boards, in the style of deploy_preflight.py. It gates a STAGED
artifact before it may replace models/racing_ensemble_v2.pkl. It *reports* — it
never copies, installs, or promotes anything itself.

  BOARD 1 — ARTIFACT HYGIENE: everything automatable. Any RED blocks.
  BOARD 2 — DECISION INPUTS: the human approvals (shadow metrics SHIP verdict,
            pre-registration sign-off). Listed as PEND; they cannot be automated,
            and absence is always visible, never silent.

Gate 3 is the one that would have caught the 25.45% dead-at-serve importance
mass before it shipped: every declared feature must be trained AND served.

Usage:
  python retrain_preflight.py --staging models/candidates/racing_ensemble_v2_20260801.pkl
  python retrain_preflight.py --staging <pkl> --metrics shadow_metrics.json
  python retrain_preflight.py --staging <pkl> --json

Exit 1 on any RED. Exit 0 with PEND items means "cleared pending the human
approvals listed" — not approval.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIVE_PKL = SCRIPT_DIR / "models" / "racing_ensemble_v2.pkl"
BACKUPS_DIR = SCRIPT_DIR / "models" / "backups"
PREREG_DOC = SCRIPT_DIR.parent.parent / "docs" / "roi-roadmap" / "12-preregistration.md"
PARITY_TESTS = ("test_feature_parity.py", "test_serve_feature_liveness.py")

EXPECTED_KEYS = ("xgb_model", "lgb_model", "cb_model", "feature_columns",
                 "feature_importance", "trained_at", "version")

GREEN, RED, AMBER = "GREEN", "RED", "AMBER"
SYM = {GREEN: "OK  ", RED: "FAIL", AMBER: "PEND"}


def _row(name, status, detail=""):
    return {"name": name, "status": status, "detail": detail}


def _parse_list_literal(path: Path, name: str):
    """FEATURE_COLUMNS / NAN_PRESERVE_FEATURES from source without importing
    (retrain_v2's module level demands DATABASE_URL + psycopg2; a preflight
    must run anywhere). Handles module-level and class-attribute assignments."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    candidates = list(ast.walk(tree))
    for node in candidates:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        else:
            continue
        for t in targets:
            if getattr(t, "id", None) == name and isinstance(value, (ast.List, ast.Tuple)):
                try:
                    return [ast.literal_eval(e) for e in value.elts]
                except (ValueError, SyntaxError):
                    return None
    return None


# ---------------------------------------------------------------------------
# BOARD 1 — artifact hygiene
# ---------------------------------------------------------------------------

def gate_paths(staging: Path, live_pkl: Path = LIVE_PKL,
               backups_dir: Path = BACKUPS_DIR):
    rows = []
    if staging.resolve() == live_pkl.resolve():
        rows.append(_row(
            "staging!=live", RED,
            f"staging path IS the live path ({live_pkl.name}) — refusing to "
            f"evaluate the production artifact as a candidate"))
    else:
        rows.append(_row("staging!=live", GREEN, f"staging {staging.name} != live {live_pkl.name}"))

    if not live_pkl.exists():
        rows.append(_row("live-backup", AMBER,
                         f"live pkl not found at {live_pkl} (first deploy?) — "
                         f"nothing to back up"))
    else:
        backups = sorted(backups_dir.glob(f"{live_pkl.stem}*.pkl")) if backups_dir.is_dir() else []
        if backups:
            rows.append(_row("live-backup", GREEN, f"newest backup: {backups[-1].name}"))
        else:
            rows.append(_row("live-backup", RED,
                             f"no timestamped backup of the live pkl in {backups_dir} — "
                             f"create one before any promotion"))
    return rows


def gate_loads(staging: Path):
    import joblib
    try:
        obj = joblib.load(staging)
    except Exception as e:
        return None, [_row("artifact-loads", RED, f"joblib.load failed: {e}")]
    if not isinstance(obj, dict):
        return obj, [_row("artifact-loads", RED,
                          f"artifact is {type(obj).__name__}, expected dict")]
    missing = [k for k in EXPECTED_KEYS if k not in obj]
    if missing:
        return obj, [_row("artifact-loads", RED, f"missing keys: {missing}")]
    return obj, [_row("artifact-loads", GREEN, f"all {len(EXPECTED_KEYS)} expected keys present")]


def gate_liveness(staging: Path, code_columns):
    """Every declared feature trained AND served; no code/pkl drift."""
    import feature_liveness_audit as fla
    static = fla.audit_static(code_columns)
    zero_at_serve = static["verdict_counts"].get("ZERO_AT_SERVE", 0)
    pkl = fla.audit_pkl(str(staging), code_columns)
    in_code_not_in_pkl = pkl.get("in_code_not_in_pkl", [])

    rows = []
    if zero_at_serve:
        names = [r["feature"] for r in static["features"] if r["verdict"] == "ZERO_AT_SERVE"]
        rows.append(_row("liveness:serve", RED,
                         f"{zero_at_serve} features trained but never served: {names[:5]}"))
    else:
        rows.append(_row("liveness:serve", GREEN, "ZERO_AT_SERVE = 0"))
    if in_code_not_in_pkl:
        rows.append(_row("liveness:trained", RED,
                         f"{len(in_code_not_in_pkl)} declared features not in artifact: "
                         f"{in_code_not_in_pkl[:5]}"))
    else:
        rows.append(_row("liveness:trained", GREEN, "in_code_not_in_pkl = []"))
    return rows


def gate_lockstep(staging_obj, retrain_path=None, ml_model_path=None):
    retrain_path = retrain_path or SCRIPT_DIR / "retrain_v2.py"
    ml_model_path = ml_model_path or SCRIPT_DIR / "ml_model.py"
    retrain_cols = _parse_list_literal(Path(retrain_path), "FEATURE_COLUMNS")
    model_cols = _parse_list_literal(Path(ml_model_path), "FEATURE_COLUMNS")
    pkl_cols = list(staging_obj.get("feature_columns") or [])

    rows = []
    if retrain_cols is None or model_cols is None:
        rows.append(_row("lockstep:columns", RED, "could not parse FEATURE_COLUMNS from source"))
    elif retrain_cols == model_cols == pkl_cols:
        rows.append(_row("lockstep:columns", GREEN,
                         f"retrain_v2 == ml_model == pkl ({len(pkl_cols)} columns, ordered)"))
    else:
        drift = []
        if retrain_cols != model_cols:
            drift.append("retrain_v2 != ml_model")
        if model_cols != pkl_cols:
            drift.append(f"ml_model ({len(model_cols or [])}) != pkl ({len(pkl_cols)})")
        rows.append(_row("lockstep:columns", RED, "; ".join(drift)))

    retrain_np = set(_parse_list_literal(Path(retrain_path), "NAN_PRESERVE_FEATURES") or [])
    model_np = _parse_list_literal(Path(ml_model_path), "NAN_PRESERVE_FEATURES")
    if model_np is None:
        rows.append(_row("lockstep:nan-preserve", GREEN,
                         "ml_model defines no NAN_PRESERVE list (nothing to compare); "
                         f"retrain_v2 has {sorted(retrain_np)}"))
    elif retrain_np == set(model_np):
        rows.append(_row("lockstep:nan-preserve", GREEN, f"NAN_PRESERVE sets equal: {sorted(retrain_np)}"))
    else:
        rows.append(_row("lockstep:nan-preserve", RED,
                         f"retrain_v2 {sorted(retrain_np)} != ml_model {sorted(model_np)}"))
    return rows, retrain_cols


def gate_parity_suites(test_paths=None):
    """Run the parity suites in-process, NOW. Absent files are AMBER with an
    explicit note — absence is visible, never silent."""
    rows = []
    for name in PARITY_TESTS:
        path = Path(test_paths.get(name)) if test_paths and name in test_paths \
            else SCRIPT_DIR / name
        if not path.exists():
            rows.append(_row(f"parity:{name}", AMBER,
                             "not present on this branch — run after the parity "
                             "suite lands"))
            continue
        import io
        import contextlib
        import pytest
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = pytest.main([str(path), "-q", "--no-header"])
        tail = buf.getvalue().strip().splitlines()[-1] if buf.getvalue().strip() else ""
        if code == 0:
            rows.append(_row(f"parity:{name}", GREEN, tail))
        else:
            rows.append(_row(f"parity:{name}", RED, f"pytest exit {code}: {tail}"))
    return rows


def gate_freshness(staging_obj, live_pkl: Path = LIVE_PKL):
    if not live_pkl.exists():
        return [_row("freshness", AMBER, "no live pkl to compare trained_at/version against")]
    import joblib
    try:
        live_obj = joblib.load(live_pkl)
    except Exception as e:
        return [_row("freshness", AMBER, f"live pkl unreadable ({e}) — cannot compare")]

    rows = []
    s_at = str(staging_obj.get("trained_at", ""))
    l_at = str(live_obj.get("trained_at", "")) if isinstance(live_obj, dict) else ""
    try:
        s_dt, l_dt = datetime.fromisoformat(s_at), datetime.fromisoformat(l_at)
        if s_dt > l_dt:
            rows.append(_row("freshness:trained_at", GREEN, f"{s_at} > live {l_at}"))
        else:
            rows.append(_row("freshness:trained_at", RED,
                             f"staging trained_at {s_at} is not newer than live {l_at}"))
    except ValueError:
        rows.append(_row("freshness:trained_at", RED,
                         f"unparseable trained_at (staging {s_at!r}, live {l_at!r})"))

    s_ver, l_ver = staging_obj.get("version"), (live_obj.get("version") if isinstance(live_obj, dict) else None)
    if s_ver != l_ver:
        rows.append(_row("freshness:version", GREEN, f"version {s_ver!r} != live {l_ver!r}"))
    else:
        rows.append(_row("freshness:version", RED,
                         f"staging version {s_ver!r} is identical to live — bump it"))
    return rows


# ---------------------------------------------------------------------------
# BOARD 2 — decision inputs (cannot be automated)
# ---------------------------------------------------------------------------

def gate_shadow_metrics(metrics_path: str | None):
    if not metrics_path:
        return [_row("shadow-metrics", AMBER,
                     "shadow metrics not supplied (--metrics) — SHIP verdict required "
                     "for promotion")]
    try:
        import ship_criteria
    except ImportError:
        return [_row("shadow-metrics", AMBER,
                     "ship_criteria not on this branch — run after merge")]
    try:
        payload = json.loads(Path(metrics_path).read_text(encoding="utf-8"))
    except Exception as e:
        return [_row("shadow-metrics", RED, f"metrics JSON unreadable: {e}")]
    try:
        result = ship_criteria.evaluate_ship_criteria(
            payload.get("baseline", {}), payload.get("candidate", {}),
            payload.get("lever", "BOTH"))
    except Exception as e:
        return [_row("shadow-metrics", RED, f"evaluate_ship_criteria failed: {e}")]
    verdict = result.get("verdict")
    detail = f"{verdict} — {result.get('summary', '')}"
    if verdict == "SHIP":
        return [_row("shadow-metrics", GREEN, detail)]
    return [_row("shadow-metrics", RED, f"{detail} (SHIP required for promotion)")]


def gate_preregistration(prereg_path: Path = PREREG_DOC):
    if not prereg_path.exists():
        return [_row("preregistration", AMBER,
                     f"{prereg_path.name} not found — see the protocol pack; "
                     f"pre-registration sign-off required")]
    text = prereg_path.read_text(encoding="utf-8")
    n = text.count("[SAGE-APPROVAL]")
    if n:
        return [_row("preregistration", AMBER,
                     f"{n} unresolved [SAGE-APPROVAL] marker(s) in {prereg_path.name}")]
    return [_row("preregistration", GREEN, f"{prereg_path.name} present, no unresolved markers")]


# ---------------------------------------------------------------------------
# board assembly
# ---------------------------------------------------------------------------

def run_preflight(staging: str, metrics: str | None = None,
                  live_pkl: Path = LIVE_PKL, backups_dir: Path = BACKUPS_DIR,
                  prereg_path: Path = PREREG_DOC, parity_test_paths=None,
                  retrain_path=None, ml_model_path=None):
    staging_path = Path(staging)
    board1, board2 = [], []

    path_rows = gate_paths(staging_path, live_pkl, backups_dir)
    board1 += path_rows
    refused = any(r["status"] == RED and r["name"] == "staging!=live" for r in path_rows)

    staging_obj = None
    if not staging_path.exists():
        board1.append(_row("artifact-loads", RED, f"not found: {staging_path}"))
    elif not refused:
        staging_obj, load_rows = gate_loads(staging_path)
        board1 += load_rows
    else:
        board1.append(_row("artifact-loads", AMBER, "skipped — live-path refusal above"))

    code_columns = None
    if staging_obj is not None:
        code_columns = _parse_list_literal(
            Path(retrain_path) if retrain_path else SCRIPT_DIR / "retrain_v2.py",
            "FEATURE_COLUMNS")
        if code_columns is None:
            board1.append(_row("liveness:serve", RED,
                               "could not parse FEATURE_COLUMNS from retrain_v2.py"))
        else:
            board1 += gate_liveness(staging_path, code_columns)
        lock_rows, _ = gate_lockstep(staging_obj, retrain_path, ml_model_path)
        board1 += lock_rows
        board1 += gate_parity_suites(parity_test_paths)
        board1 += gate_freshness(staging_obj, live_pkl)

    board2 += gate_shadow_metrics(metrics)
    board2 += gate_preregistration(prereg_path)
    return {"board1": board1, "board2": board2}


def render(boards) -> str:
    lines = ["", "=== BOARD 1 — ARTIFACT HYGIENE (any FAIL blocks) ==="]
    for r in boards["board1"]:
        lines.append(f"  [{SYM[r['status']]}] {r['name']:<28} {r['detail']}")
    lines.append("")
    lines.append("=== BOARD 2 — DECISION INPUTS (human approvals; PEND is expected) ===")
    for r in boards["board2"]:
        lines.append(f"  [{SYM[r['status']]}] {r['name']:<28} {r['detail']}")
    lines.append("")
    n_red = sum(1 for r in boards["board1"] + boards["board2"] if r["status"] == RED)
    n_pend = sum(1 for r in boards["board1"] + boards["board2"] if r["status"] == AMBER)
    if n_red:
        lines.append(f"PROMOTION: BLOCKED ({n_red} red, {n_pend} pending)")
    else:
        lines.append(f"PROMOTION: cleared pending the human approvals listed above "
                     f"({n_pend} pending)")
    lines.append("(this script never copies or promotes anything itself)")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrain promotion preflight gate.")
    parser.add_argument("--staging", required=True, help="path to the staged pkl candidate")
    parser.add_argument("--metrics", default=None, help="shadow-metrics JSON (optional)")
    parser.add_argument("--json", action="store_true", help="machine-readable board")
    args = parser.parse_args()

    boards = run_preflight(args.staging, args.metrics)
    if args.json:
        print(json.dumps(boards, indent=2))
    else:
        print(render(boards))
    n_red = sum(1 for r in boards["board1"] + boards["board2"] if r["status"] == RED)
    return 1 if n_red else 0


if __name__ == "__main__":
    sys.exit(main())
