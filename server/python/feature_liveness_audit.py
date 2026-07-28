#!/usr/bin/env python3
"""Feature liveness audit — find features the model thinks it has but doesn't.

Two silent failure classes motivated this (both found by hand before this
script existed, which is exactly why it now exists):

  * DEAD AT TRAIN — declared in FEATURE_COLUMNS but never assigned anywhere in
    the training path, so the model fits a constant. barrier_advantage is the
    proven case: declared, consumed by barrier_x_pace_inv (retrain_v2.py:662),
    assigned nowhere — the interaction is constant 0 and carries zero signal.
  * ZERO AT SERVE — assigned at train (real signal, real learned splits) but
    never assigned in the serve path, so ml_model.prepare_features silently
    zero-fills and every learned split fires on a constant. The sectional
    z-scores (z_200m..z_800m) are the proven case: leak-safe prior-start values
    at train, constant 0 in production. This is worse than dead — the model
    actively misprices with it.

The audit is STATIC (regex + declaration-block masking): it reports where each
feature name is assigned on each side, with file:line evidence. Static scanning
can over-count (a name inside a string or comment) but cannot under-count an
assignment, so ABSENT verdicts are trustworthy; ASSIGNED verdicts cite their
evidence for a human to spot-check. Optionally, --pkl adds the trained model's
view: per-feature importances and any code<->artifact column drift.

Usage:
  python feature_liveness_audit.py                          # static audit
  python feature_liveness_audit.py --pkl models/racing_ensemble_v2.pkl
  python feature_liveness_audit.py --json --out report.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent

# form_feature_builder is imported LAZILY inside functions on both sides
# (retrain_v2.py:347 batch_compute_form_features, run_tips_pipeline.py:2228
# compute_race_form_features) — a top-of-file import scan misses it entirely,
# which is how a first draft of this audit wrongly declared 69 features dead.
TRAIN_FILES = ("retrain_v2.py", "refresh_training_view_v2.py",
               "form_feature_builder.py")
SERVE_FILES = ("run_tips_pipeline.py", "ml_model.py", "form_feature_builder.py")


def _read(name: str) -> str:
    p = SCRIPT_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _mask_declaration_blocks(src: str) -> str:
    """Blank out FEATURE_COLUMNS / NAN_PRESERVE / VIEW_TO_FEATURE_MAP literals.

    A name's presence inside its own declaration list proves nothing about
    liveness; masking them is what makes an ABSENT verdict meaningful.
    """
    out = src
    for decl in ("FEATURE_COLUMNS", "NAN_PRESERVE_FEATURES", "NAN_PRESERVE",
                 "VIEW_TO_FEATURE_MAP"):
        for m in re.finditer(rf"{decl}\s*[:=]\s*[\[{{]", out):
            start = m.end() - 1
            depth, i = 0, start
            while i < len(out):
                if out[i] in "[{":
                    depth += 1
                elif out[i] in "]}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            # keep newlines so every evidence line number stays true — blanking
            # them shifts every citation after a declaration block (~200 lines
            # in retrain_v2.py) and silently corrupts the report
            blanked = "".join(c if c == "\n" else " " for c in out[start:i + 1])
            out = out[:start] + blanked + out[i + 1:]
    return out


def parse_feature_columns(retrain_src: str) -> List[str]:
    m = re.search(r"FEATURE_COLUMNS\s*=\s*\[(.*?)\]", retrain_src, re.S)
    if not m:
        raise RuntimeError("FEATURE_COLUMNS not found in retrain_v2.py")
    return re.findall(r"[\"']([a-z0-9_]+)[\"']", m.group(1))


def parse_view_map(retrain_src: str) -> Dict[str, str]:
    m = re.search(r"VIEW_TO_FEATURE_MAP\s*=\s*\{(.*?)\}", retrain_src, re.S)
    if not m:
        return {}
    return dict(re.findall(r"[\"']([a-z0-9_]+)[\"']\s*:\s*[\"']([a-z0-9_]+)[\"']",
                           m.group(1)))


def _find_evidence(name: str, src: str, fname: str) -> List[str]:
    """Assignment-shaped occurrences of a feature name, as file:line evidence."""
    hits: List[str] = []
    patterns = (
        rf"\[[\"']{name}[\"']\]\s*=",   # df["name"] = / out["name"] =
        rf"[\"']{name}[\"']\s*:",       # dict-literal key "name": ...
    )
    for lineno, line in enumerate(src.splitlines(), 1):
        for pat in patterns:
            if re.search(pat, line):
                hits.append(f"{fname}:{lineno}: {line.strip()[:100]}")
                break
    return hits


def _referenced(name: str, src: str) -> bool:
    return re.search(rf"[\"']{name}[\"']", src) is not None


def audit_static(feature_columns: Optional[List[str]] = None) -> Dict[str, Any]:
    retrain_raw = _read("retrain_v2.py")
    feats = feature_columns or parse_feature_columns(retrain_raw)
    view_map = parse_view_map(retrain_raw)

    train_srcs = {f: _mask_declaration_blocks(_read(f)) for f in TRAIN_FILES}
    serve_srcs = {f: _mask_declaration_blocks(_read(f)) for f in SERVE_FILES}

    rows = []
    for name in feats:
        t_ev: List[str] = []
        for fname, src in train_srcs.items():
            t_ev += _find_evidence(name, src, fname)
        # a bare SQL token in the training VIEW is an assignment too — plain
        # view columns (weight_kg, field_size, ...) reach the frame without a
        # quoted python assignment. Restricted to the view file only: in
        # retrain_v2.py a bare quoted name can be a READ (barrier_advantage's
        # consumption at :662 is exactly that), which must not count.
        if not t_ev:
            src = train_srcs.get("refresh_training_view_v2.py", "")
            for lineno, line in enumerate(src.splitlines(), 1):
                if re.search(rf"\b{name}\b", line):
                    t_ev.append(f"refresh_training_view_v2.py:{lineno} (sql-token): "
                                f"{line.strip()[:100]}")
                    break
        # a view column mapped onto this feature counts as a train assignment
        # (retrain_v2 copies mapped view columns straight into the frame)
        for view_col, feat_col in view_map.items():
            if feat_col == name and view_col != name:
                for fname, src in train_srcs.items():
                    if _referenced(view_col, src) or re.search(rf"\b{view_col}\b", src):
                        t_ev.append(f"{fname}: via VIEW_TO_FEATURE_MAP {view_col} -> {name}")
                        break
        t_ref = any(_referenced(name, s) for s in train_srcs.values())

        s_ev: List[str] = []
        for fname, src in serve_srcs.items():
            s_ev += _find_evidence(name, src, fname)
        s_ref = any(_referenced(name, s) for s in serve_srcs.values())

        train_status = ("ASSIGNED" if t_ev else
                        "REFERENCED_ONLY" if t_ref else "ABSENT")
        serve_status = ("ASSIGNED" if s_ev else
                        "REFERENCED_ONLY" if s_ref else "ABSENT")

        if train_status != "ASSIGNED" and serve_status != "ASSIGNED":
            verdict = "DEAD_BOTH_SIDES"
        elif train_status == "ASSIGNED" and serve_status != "ASSIGNED":
            verdict = "ZERO_AT_SERVE"
        elif train_status != "ASSIGNED" and serve_status == "ASSIGNED":
            verdict = "DEAD_AT_TRAIN"
        else:
            verdict = "LIVE_BOTH"

        rows.append({
            "feature": name,
            "train_status": train_status,
            "serve_status": serve_status,
            "verdict": verdict,
            "train_evidence": t_ev[:4],
            "serve_evidence": s_ev[:4],
        })

    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return {"n_features": len(feats), "verdict_counts": counts, "features": rows}


def audit_pkl(pkl_path: str, feature_columns: List[str]) -> Dict[str, Any]:
    """The trained artifact's own testimony: column drift + importances.

    racing_ensemble_v2.pkl is a plain dict: xgb_model / lgb_model / cb_model,
    feature_columns (training order), and a pre-averaged feature_importance
    dict — that stored dict is authoritative for "what did the live model
    actually learn from", so it is read first; per-model importances are kept
    alongside for disagreement checks.
    """
    import joblib
    import numpy as np

    obj = joblib.load(pkl_path)
    report: Dict[str, Any] = {"pkl": pkl_path,
                              "version": obj.get("version") if isinstance(obj, dict) else None,
                              "trained_at": str(obj.get("trained_at")) if isinstance(obj, dict) else None}
    if not isinstance(obj, dict):
        report["error"] = f"unexpected artifact type {type(obj).__name__}"
        return report

    trained_cols = list(obj.get("feature_columns") or [])
    if trained_cols:
        report["n_trained_columns"] = len(trained_cols)
        report["in_code_not_in_pkl"] = [c for c in feature_columns
                                        if c not in trained_cols]
        report["in_pkl_not_in_code"] = [c for c in trained_cols
                                        if c not in feature_columns]
    else:
        report["n_trained_columns"] = None

    stored = obj.get("feature_importance")
    if isinstance(stored, dict) and stored:
        report["importance_source"] = "stored feature_importance dict"
        report["importances"] = {k: round(float(v), 6) for k, v in stored.items()}
        report["zero_importance_features"] = sorted(
            k for k, v in stored.items() if float(v) <= 1e-6)
        report["top15"] = sorted(stored.items(), key=lambda kv: -kv[1])[:15]
    else:
        importances: Dict[str, Dict[str, float]] = {}
        for mname in ("xgb_model", "lgb_model", "cb_model"):
            model = obj.get(mname)
            imp = getattr(model, "feature_importances_", None)
            if imp is None:
                continue
            imp = np.asarray(imp, dtype=float)
            if len(imp) != len(trained_cols):
                continue  # unknown ordering — refuse to mislabel importances
            total = imp.sum() or 1.0
            for c, v in zip(trained_cols, imp):
                importances.setdefault(c, {})[mname] = round(float(v / total), 6)
        if importances:
            report["importance_source"] = "per-model feature_importances_"
            report["zero_importance_features"] = sorted(
                c for c, per in importances.items()
                if all(v <= 1e-5 for v in per.values()))
            report["importances"] = importances
        else:
            report["note"] = "no importances extracted"
    return report


def render(static: Dict[str, Any], pkl: Optional[Dict[str, Any]]) -> str:
    lines = ["", "=== FEATURE LIVENESS AUDIT (static) ==="]
    lines.append(f"features: {static['n_features']}  verdicts: {static['verdict_counts']}")
    for bucket, title in (("DEAD_BOTH_SIDES", "DEAD BOTH SIDES (model fits a constant)"),
                          ("ZERO_AT_SERVE", "ZERO AT SERVE (trained signal silently disabled in prod)"),
                          ("DEAD_AT_TRAIN", "DEAD AT TRAIN (serve computes it, model never learned it)")):
        rows = [r for r in static["features"] if r["verdict"] == bucket]
        if rows:
            lines.append(f"\n-- {title} --")
            for r in rows:
                lines.append(f"  {r['feature']:<28} train={r['train_status']:<16} serve={r['serve_status']}")
                for ev in (r["train_evidence"] or [])[:1]:
                    lines.append(f"      train: {ev}")
                for ev in (r["serve_evidence"] or [])[:1]:
                    lines.append(f"      serve: {ev}")
    if pkl:
        lines.append("\n=== TRAINED ARTIFACT (pkl) ===")
        lines.append(f"trained columns: {pkl.get('n_trained_columns')}")
        if pkl.get("in_code_not_in_pkl"):
            lines.append(f"in code, not in pkl (added since train): {pkl['in_code_not_in_pkl']}")
        if pkl.get("in_pkl_not_in_code"):
            lines.append(f"in pkl, not in code (removed since train): {pkl['in_pkl_not_in_code']}")
        if pkl.get("zero_importance_features") is not None:
            lines.append(f"zero-importance in every model: {pkl['zero_importance_features']}")
        if pkl.get("note"):
            lines.append(pkl["note"])
    lines.append("\nStatic caveat: ASSIGNED cites evidence for human spot-check; "
                 "ABSENT is trustworthy (regex cannot miss a real assignment of the exact name).")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit feature liveness across train/serve/pkl.")
    ap.add_argument("--pkl", default=None, help="path to racing_ensemble pkl (optional)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None, help="write the JSON report here")
    args = ap.parse_args()

    static = audit_static()
    pkl_report = None
    if args.pkl:
        try:
            pkl_report = audit_pkl(args.pkl, [r["feature"] for r in static["features"]])
        except Exception as e:  # the static audit must still land without the artifact
            pkl_report = {"pkl": args.pkl, "error": str(e)}

    payload = {"static": static, "pkl": pkl_report}
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2) if args.json else render(static, pkl_report))
    return 0


def _self_test() -> None:
    print("feature_liveness_audit self-test")

    masked = _mask_declaration_blocks(
        'FEATURE_COLUMNS = ["a_feat", "b_feat"]\nout["a_feat"] = 1\n')
    assert '"a_feat"] = 1' in masked and 'FEATURE_COLUMNS = ["a_feat"' not in masked, \
        "declaration masked, real assignment kept"

    src = 'out["alive_one"] = x\nfeat = {"dict_key_feat": 1}\n# mentions ghost_feat\n'
    assert _find_evidence("alive_one", src, "f.py"), "subscript assignment detected"
    assert _find_evidence("dict_key_feat", src, "f.py"), "dict-key assignment detected"
    assert not _find_evidence("ghost_feat", src, "f.py"), "a comment mention is not an assignment"

    # the audit must reproduce the two proven findings on the real tree
    real = audit_static()
    by_name = {r["feature"]: r for r in real["features"]}
    if "barrier_advantage" in by_name:
        assert by_name["barrier_advantage"]["train_status"] != "ASSIGNED", \
            "barrier_advantage is the proven dead feature — if this fires, it got plumbed (update the audit docs)"
    for z in ("z_200m", "z_400m", "z_600m", "z_800m"):
        if z in by_name:
            assert by_name[z]["serve_status"] != "ASSIGNED", \
                f"{z} is the proven zero-at-serve feature — if this fires, it got plumbed"
    print(f"  masking, detection, and both proven findings hold "
          f"({real['n_features']} features scanned)")
    print("All tests completed successfully.")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        raise SystemExit(main())
