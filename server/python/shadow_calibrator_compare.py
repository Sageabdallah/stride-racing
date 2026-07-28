#!/usr/bin/env python3
"""Shadow comparison: current vs refit calibrator, renormalisation on/off
(ROI roadmap task 05, step 4).

Runs both calibrators (live v1 and the staged task-05 refit) and per-race
renormalisation (off/on) over logged audit rows, and emits a comparison JSON:

- calibration-bin alignment (mean predicted vs observed per equal-width bin),
- Brier score and log-loss per variant,
- per-race probability sums (renormalised variants must sum to 1.0 ± 1e-6),
- confidence-tier transitions per runner: tier under the legacy variant vs
  each renormalised/calibrated variant, using the PRODUCTION tier rules
  (run_tips_pipeline.compute_confidence — imported, never restated), with
  per-race transition detail and a summary count matrix (task-05 guardrail:
  "log tier transitions during the shadow week").

The core functions are pure and DB-free; the CLI loads audit rows from an
export CSV or DATABASE_URL in the live environment. No data is not an error —
the report says so and the CLI exits 0, so the shadow week can be scheduled
before coverage exists.

Usage:
    python shadow_calibrator_compare.py --self-test          # no DB required
    python shadow_calibrator_compare.py --input-csv audit_export.csv
    python shadow_calibrator_compare.py --v2 models/staging/isotonic_calibrator_v2.pkl \
        --output shadow_compare.json
"""

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from market_prob import overround, renormalise_probs, true_market_prob_pct

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
V1_PATH = os.path.join(SCRIPT_DIR, "models", "isotonic_calibrator.pkl")
V2_PATH = os.path.join(SCRIPT_DIR, "models", "staging", "isotonic_calibrator_v2.pkl")

SUM_TOLERANCE = 1e-6


# --------------------------------------------------------------------------
# Pure core
# --------------------------------------------------------------------------

def group_races(rows: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Group audit rows into races, preserving input order within a race."""
    races: Dict[Any, List[Dict[str, Any]]] = {}
    order: List[Any] = []
    for i, r in enumerate(rows):
        key = r.get("race_key")
        if key is None:
            key = (str(r.get("race_date", "")), str(r.get("track", "")),
                   str(r.get("race_number", "")))
        if key not in races:
            races[key] = []
            order.append(key)
        races[key].append({**r, "_row": i})
    return [races[k] for k in order]


def variant_probs(rows: Sequence[Dict[str, Any]],
                  calibrator=None,
                  renormalise: bool = False) -> List[float]:
    """Per-runner probabilities (0-1) for one shadow variant.

    `calibrator` is a calibration_model.ProbabilityCalibrator (or None for the
    identity). Renormalisation mirrors production: percent scale, 2dp, with
    the largest-remainder residual correction from market_prob.
    """
    flat: List[Optional[float]] = [None] * len(rows)
    for race in group_races(rows):
        probs = [max(0.0, float(r["prob"])) for r in race]
        if calibrator is not None:
            probs = [float(p) for p in calibrator.calibrate(np.asarray(probs))]
        if renormalise:
            pct = renormalise_probs([p * 100.0 for p in probs], target=100.0)
            probs = [p / 100.0 for p in pct]
        for r, p in zip(race, probs):
            flat[r["_row"]] = p
    return [p if p is not None else 0.0 for p in flat]


def brier_score(probs: Sequence[float], y: Sequence[int]) -> float:
    p = np.asarray(probs, dtype=float)
    t = np.asarray(y, dtype=float)
    return float(np.mean((p - t) ** 2))


def log_loss_score(probs: Sequence[float], y: Sequence[int], eps: float = 1e-15) -> float:
    p = np.clip(np.asarray(probs, dtype=float), eps, 1.0 - eps)
    t = np.asarray(y, dtype=float)
    return float(-np.mean(t * np.log(p) + (1.0 - t) * np.log(1.0 - p)))


def calibration_bins(probs: Sequence[float], y: Sequence[int],
                     n_bins: int = 10) -> List[Dict[str, Any]]:
    """Mean predicted vs observed per equal-width bin (README band table)."""
    p = np.asarray(probs, dtype=float)
    t = np.asarray(y, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p >= lo) & (p < hi) if i < n_bins - 1 else (p >= lo) & (p <= hi)
        n = int(mask.sum())
        if n == 0:
            continue
        bins.append({
            "bin": f"[{lo:.1f},{hi:.1f})",
            "n": n,
            "mean_pred": round(float(p[mask].mean()), 4),
            "observed": round(float(t[mask].mean()), 4),
        })
    return bins


def race_sum_stats(rows: Sequence[Dict[str, Any]],
                   probs: Sequence[float]) -> Dict[str, Any]:
    """Per-race probability sums and their deviation from 1.0."""
    sums = []
    for race in group_races(rows):
        sums.append(sum(probs[r["_row"]] for r in race))
    if not sums:
        return {"n_races": 0, "mean_sum": None, "max_abs_dev": None,
                "within_tolerance": False}
    devs = [abs(s - 1.0) for s in sums]
    return {
        "n_races": len(sums),
        "mean_sum": round(float(np.mean(sums)), 9),
        "max_abs_dev": round(float(max(devs)), 9),
        "within_tolerance": bool(max(devs) <= SUM_TOLERANCE),
    }


# --------------------------------------------------------------------------
# Confidence-tier transitions (task-05 guardrail: log them, never drop silently)
# --------------------------------------------------------------------------

def _tier_fn():
    """The production confidence-tier rules (run_tips_pipeline.compute_confidence).

    Imported, never restated — the thresholds live in exactly one place. None
    when the pipeline module is not importable (report marks tiers
    unavailable; the rest of the comparison still runs).
    """
    try:
        from run_tips_pipeline import compute_confidence
        return compute_confidence
    except Exception:
        return None


def runner_tiers(rows: Sequence[Dict[str, Any]],
                 probs: Sequence[float],
                 tier_fn) -> List[Optional[str]]:
    """Per-runner confidence tier for one variant's probabilities (0-1).

    Mirrors the pipeline's own renormalisation tier check (run_tips_pipeline
    RENORM_TIER_TRANSITION block): the true market is the shared overround-
    corrected market_prob helper applied to the race's recorded market_odds,
    and the edge is recomputed per variant as prob_pct - true_market. A
    runner without usable market odds gets tier None (excluded from the
    transition counts, noted in n_compared).
    """
    tiers: List[Optional[str]] = [None] * len(rows)
    if tier_fn is None:
        return tiers
    for race in group_races(rows):
        ov = overround([r.get("market_odds") for r in race])
        for r in race:
            odds = r.get("market_odds")
            if not odds or odds <= 1:
                continue
            tm = true_market_prob_pct(odds, ov)
            if tm <= 0:
                continue
            win_pct = probs[r["_row"]] * 100.0
            tiers[r["_row"]] = tier_fn({
                "winPercentage": win_pct,
                "marketOdds": odds,
                "modelEdge": round(win_pct - tm, 2),
                "trueMarketProb": tm,
            })
    return tiers


def tier_transitions(rows: Sequence[Dict[str, Any]],
                     probs_by_variant: Dict[str, Sequence[float]],
                     tier_fn,
                     base: str = "current") -> Dict[str, Any]:
    """Confidence tier under the legacy variant vs each other variant.

    Returns {pair: {n_compared, n_transitions, matrix, races}} where matrix
    counts old_tier>new_tier transitions and races carries the per-race
    detail (runner, old_tier, new_tier) for every runner whose tier moved.
    """
    tiers_by_variant = {
        name: runner_tiers(rows, probs, tier_fn)
        for name, probs in probs_by_variant.items()
    }
    base_tiers = tiers_by_variant.get(base)
    pairs: Dict[str, Any] = {}
    if base_tiers is None:
        return pairs
    for name, new_tiers in tiers_by_variant.items():
        if name == base:
            continue
        matrix: Dict[str, int] = {}
        races_detail: List[Dict[str, Any]] = []
        n_compared = 0
        for race in group_races(rows):
            race_key = race[0].get("race_key") or (
                str(race[0].get("race_date", "")),
                str(race[0].get("track", "")),
                str(race[0].get("race_number", "")))
            runners_detail = []
            for r in race:
                old_t = base_tiers[r["_row"]]
                new_t = new_tiers[r["_row"]]
                if old_t is None or new_t is None:
                    continue
                n_compared += 1
                if old_t != new_t:
                    matrix[f"{old_t}>{new_t}"] = matrix.get(f"{old_t}>{new_t}", 0) + 1
                    runners_detail.append({
                        "runner": (r.get("horse_name") or r.get("horse")
                                   or f"row{r['_row']}"),
                        "old_tier": old_t,
                        "new_tier": new_t,
                    })
            if runners_detail:
                races_detail.append({"race_key": [str(k) for k in race_key] if isinstance(race_key, tuple) else str(race_key),
                                     "transitions": runners_detail})
        pairs[f"{base}__{name}"] = {
            "n_compared": n_compared,
            "n_transitions": sum(matrix.values()),
            "matrix": matrix,
            "races": races_detail,
        }
    return pairs


def compare(rows: Sequence[Dict[str, Any]],
            cal_old=None,
            cal_new=None,
            n_bins: int = 10) -> Dict[str, Any]:
    """Run every shadow variant over the audit rows and build the report."""
    if not rows:
        return {"status": "no_data", "n_rows": 0, "variants": {},
                "reason": "no audit rows supplied — shadow week has no data yet"}

    y = [int(r["won"]) for r in rows]
    dates = sorted({str(r.get("race_date", "")) for r in rows if r.get("race_date")})

    variants: Dict[str, Any] = {}
    probs_by_variant: Dict[str, List[float]] = {}
    specs = [("current", cal_old, False), ("current_renormalised", cal_old, True)]
    if cal_new is not None:
        specs += [("refit", cal_new, False), ("refit_renormalised", cal_new, True)]

    for name, cal, renorm in specs:
        probs = variant_probs(rows, calibrator=cal, renormalise=renorm)
        probs_by_variant[name] = probs
        variants[name] = {
            "calibrator": "identity" if cal is None else "fitted",
            "renormalised": renorm,
            "brier": round(brier_score(probs, y), 6),
            "log_loss": round(log_loss_score(probs, y), 6),
            "bins": calibration_bins(probs, y, n_bins=n_bins),
            "race_sums": race_sum_stats(rows, probs),
        }

    tier_fn = _tier_fn()
    if tier_fn is not None:
        tier_report: Dict[str, Any] = {
            "available": True,
            "base": "current",
            "pairs": tier_transitions(rows, probs_by_variant, tier_fn),
        }
    else:
        tier_report = {
            "available": False,
            "reason": "run_tips_pipeline.compute_confidence not importable "
                      "— tier transitions not computed",
        }

    return {
        "status": "ok",
        "n_rows": len(rows),
        "n_races": len(group_races(rows)),
        "date_min": dates[0] if dates else None,
        "date_max": dates[-1] if dates else None,
        "has_refit": cal_new is not None,
        "variants": variants,
        "tier_transitions": tier_report,
    }


# --------------------------------------------------------------------------
# Live-environment loading (not exercised by unit tests)
# --------------------------------------------------------------------------

# The mc_win_percentage_oof source (fit_calibrator._SOURCE_SQL) plus the
# recorded market_odds, so per-variant confidence tiers can be computed with
# the production tier rules. Logging only — no behaviour change.
_TIER_SOURCE_SQL = """
    SELECT race_date, track, race_number, horse_name,
           predicted_win_prob::float8 AS prob,
           market_odds::float8 AS market_odds,
           (actual_position = 1)::int AS won
    FROM prediction_audit
    WHERE predicted_win_prob IS NOT NULL
      AND actual_position IS NOT NULL
    ORDER BY race_date, track, race_number
"""


def _load_frame(input_csv: Optional[str]):
    """Load the audit frame from CSV export or DATABASE_URL (extended SQL)."""
    if input_csv:
        import pandas as pd
        return pd.read_csv(input_csv)
    import psycopg2
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        env_path = os.path.join(SCRIPT_DIR, "..", "..", ".env")
        if os.path.exists(env_path):
            for line in open(env_path):
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip().strip('"')
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL unavailable — pass --input-csv with an export of "
            "the recorded probabilities instead")
    import pandas as pd
    conn = psycopg2.connect(db_url, connect_timeout=10)
    try:
        return pd.read_sql(_TIER_SOURCE_SQL, conn)
    finally:
        conn.close()


def load_audit_rows(input_csv: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load MC-stage audit rows (predicted_win_prob vs realised outcome).

    Optional passthrough columns (present in the DB query; CSV exports may
    carry them): horse_name and market_odds — used only for the confidence-
    tier transition log. Rows without market odds are excluded from tier
    counts, never from the calibration comparison itself.
    """
    from fit_calibrator import normalise_source_frame

    frame = normalise_source_frame(_load_frame(input_csv))
    rows = []
    for rec in frame.to_dict("records"):
        odds = rec.get("market_odds")
        try:
            odds = float(odds) if odds is not None and odds == odds else None
        except (TypeError, ValueError):
            odds = None
        rows.append({
            "race_date": str(rec["race_date"].date()),
            "track": str(rec.get("track", "")),
            "race_number": str(rec.get("race_number", "")),
            "horse_name": str(rec.get("horse_name", "")),
            "prob": float(rec["prob"]),
            "won": int(rec["won"]),
            "market_odds": odds,
        })
    return rows


def load_calibrator(path: str):
    """Load a calibrator artifact; None when absent (identity passthrough)."""
    from calibration_model import ProbabilityCalibrator

    cal = ProbabilityCalibrator(model_path=path)
    return cal if cal.load() else None


# --------------------------------------------------------------------------

def _self_test():
    print("shadow_calibrator_compare self-test")

    assert compare([], None, None)["status"] == "no_data"
    print("  no-data behavior: status=no_data, no exception")

    rng = np.random.default_rng(3)
    rows = []
    for day in range(5):
        for race_no in range(4):
            n = 8
            true_p = rng.dirichlet(np.ones(n))
            # "Model" is systematically overconfident on the favourites.
            model_p = np.clip(true_p * 1.3 + 0.01, 0.0, 1.0)
            winner = int(rng.choice(n, p=true_p))
            for i in range(n):
                rows.append({
                    "race_date": f"2026-07-{day + 1:02d}",
                    "track": "TEST", "race_number": race_no + 1,
                    "prob": float(model_p[i]),
                    "won": 1 if i == winner else 0,
                })

    report = compare(rows, cal_old=None, cal_new=None)
    assert report["status"] == "ok" and report["n_rows"] == len(rows)
    assert report["n_races"] == 20
    cur = report["variants"]["current"]
    ren = report["variants"]["current_renormalised"]
    assert not cur["race_sums"]["within_tolerance"]  # raw model doesn't sum to 1
    assert ren["race_sums"]["within_tolerance"], ren["race_sums"]
    assert abs(ren["race_sums"]["mean_sum"] - 1.0) <= SUM_TOLERANCE
    assert ren["brier"] < cur["brier"], (ren["brier"], cur["brier"])
    print(f"  renormalised sums: mean={ren['race_sums']['mean_sum']}, "
          f"max dev={ren['race_sums']['max_abs_dev']}")
    print(f"  Brier current={cur['brier']:.4f} -> renormalised={ren['brier']:.4f}")

    # A fitted refit calibrator adds the refit variants.
    import tempfile
    from calibration_model import ProbabilityCalibrator
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "v2.pkl")
        cal = ProbabilityCalibrator(model_path=path)
        cal.fit([r["prob"] for r in rows], [r["won"] for r in rows],
                meta={"out_of_fold": True, "source": "self-test"})
        report2 = compare(rows, cal_old=None, cal_new=cal)
        assert report2["has_refit"] and "refit_renormalised" in report2["variants"]
        rr = report2["variants"]["refit_renormalised"]
        assert rr["race_sums"]["within_tolerance"]
        assert rr["brier"] <= report2["variants"]["current"]["brier"] + 1e-9
        assert report2["variants"]["refit"]["bins"]
        print(f"  refit variants present; refit Brier "
              f"{report2['variants']['refit']['brier']:.4f}, "
              f"refit+renorm {rr['brier']:.4f}")

    # Tier transitions (task-05 guardrail): a synthetic race that forces at
    # least one transition. overround = (50+20+10)/100 = 0.8, so the true
    # markets are 62.5 / 25 / 12.5. Legacy probs sum to 1.15, so
    # renormalisation scales them down:
    #   A: legacy 70.0% (edge +7.5, ev +12%) -> high;
    #      renorm 60.9% (edge -1.6) -> low.        TRANSITION high>low
    #   B: legacy 30.0% -> high; renorm 26.1% (edge +1.1, ev +4%) -> high.
    #   C: legacy 15.0% (edge +2.5, ev +20%) -> high;
    #      renorm 13.0% (edge +0.5) -> medium.    TRANSITION high>medium
    tier_rows = [
        {"race_date": "2026-07-10", "track": "TEST", "race_number": 1,
         "horse_name": "A", "prob": 0.70, "won": 1, "market_odds": 2.0},
        {"race_date": "2026-07-10", "track": "TEST", "race_number": 1,
         "horse_name": "B", "prob": 0.30, "won": 0, "market_odds": 5.0},
        {"race_date": "2026-07-10", "track": "TEST", "race_number": 1,
         "horse_name": "C", "prob": 0.15, "won": 0, "market_odds": 10.0},
    ]
    tier_report = compare(tier_rows, cal_old=None, cal_new=None)["tier_transitions"]
    assert tier_report["available"], tier_report
    pair = tier_report["pairs"]["current__current_renormalised"]
    assert pair["n_compared"] == 3, pair
    assert pair["n_transitions"] == 2, pair
    assert pair["matrix"] == {"high>low": 1, "high>medium": 1}, pair["matrix"]
    by_name = {d["runner"]: d for d in pair["races"][0]["transitions"]}
    assert by_name["A"] == {"runner": "A", "old_tier": "high", "new_tier": "low"}
    assert by_name["C"] == {"runner": "C", "old_tier": "high", "new_tier": "medium"}
    print(f"  tier transitions logged: {pair['matrix']} "
          f"({pair['n_transitions']}/{pair['n_compared']} runners moved)")

    # Runners without market odds are excluded from tier counts but never
    # from the calibration comparison itself.
    bare = [dict(r, market_odds=None) for r in tier_rows]
    bare_report = compare(bare, cal_old=None, cal_new=None)
    assert bare_report["variants"]["current"]["brier"] > 0
    assert bare_report["tier_transitions"]["pairs"][
        "current__current_renormalised"]["n_compared"] == 0
    print("  no-odds rows: comparison unaffected, tier counts skip them")

    print("All tests completed successfully.")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
        sys.exit(0)

    parser = argparse.ArgumentParser(
        description="Shadow comparison: calibrators x renormalisation over audit rows")
    parser.add_argument("--input-csv", type=str, default=None,
                        help="Audit export CSV (default: DATABASE_URL)")
    parser.add_argument("--v1", type=str, default=V1_PATH,
                        help="Live (current) calibrator artifact")
    parser.add_argument("--v2", type=str, default=V2_PATH,
                        help="Staged task-05 refit calibrator artifact")
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--output", type=str, default=None,
                        help="Write the comparison JSON here (default: stdout)")
    args = parser.parse_args()

    rows = load_audit_rows(input_csv=args.input_csv)
    cal_old = load_calibrator(args.v1)
    cal_new = load_calibrator(args.v2)
    if cal_old is None:
        print("  [shadow] no live v1 artifact — current variant is identity",
              file=sys.stderr)
    if cal_new is None:
        print("  [shadow] no staged v2 artifact — refit variants omitted",
              file=sys.stderr)

    report = compare(rows, cal_old=cal_old, cal_new=cal_new, n_bins=args.bins)
    tt = report.get("tier_transitions") or {}
    if tt.get("available"):
        for pair_name, pair in tt.get("pairs", {}).items():
            print(f"  [shadow] tier transitions {pair_name}: "
                  f"{pair['n_transitions']}/{pair['n_compared']} "
                  f"{pair['matrix']}", file=sys.stderr)
    else:
        print(f"  [shadow] tier transitions unavailable: "
              f"{tt.get('reason', '?')}", file=sys.stderr)
    text = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(text + "\n")
        print(f"  [shadow] comparison written to {args.output}", file=sys.stderr)
    else:
        print(text)
