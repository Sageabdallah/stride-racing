#!/usr/bin/env python3
"""The promotion gate: decide whether a flagged change may be turned on.

`orchestrator_instuctions.md` step 6 states the ship criteria in prose, and
without something that evaluates them they stay aspirational — a ticket gets
promoted because its headline number moved, which is how a system talks itself
into a change that costs money. This module turns them into a decision.

The criteria, and where each comes from:

  reportability     >= 200 settled bets. MIN_BETS_REPORTABLE in
                    shadow_pl_tracker; the same floor applies here so a good
                    fortnight is never read as evidence.
  roi_significance  the ROI bootstrap CI must not span zero. A CI that
                    straddles zero is not a weak result, it is NO result, and
                    it is reported as NOT REPORTABLE rather than as a loss.
  lever_direction   the change must move the lever it claimed. A ROI ticket
                    that only moved strike rate did not do what it said.
  other_lever       the other lever may not regress by more than the tolerance.
  clv               CLV positive or neutral (brief step 6).
  calibration       Brier may not degrade (SYSTEM_MAP constraint 18: a
                    default-on change must raise hit rate WITHOUT degrading
                    calibration Brier or the value band's ROI).

A verdict of SHIP means every criterion passed. Anything else keeps the flag
off. NOT_REPORTABLE is deliberately distinct from HOLD: it means the
measurement cannot answer the question yet, not that the change is bad.

The asymmetry is intentional. Turning a flag on is a real-money decision made
once; leaving it off costs nothing but time. Missing evidence therefore counts
against promotion, never for it.

Run `python ship_criteria.py` for the self-test.
"""

import os
import sys
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(__file__))

# Same floor shadow_pl_tracker uses for tier reporting.
MIN_BETS_REPORTABLE = 200

# Strike rate is strictly positive, so a relative tolerance is meaningful.
# ROI is not — it is already a percentage and can be negative or near zero, so
# a relative test there is unstable and it is compared in percentage POINTS.
DEFAULT_STRIKE_RATE_TOLERANCE_REL = 0.05      # 5% relative
DEFAULT_ROI_TOLERANCE_PP = 1.0                # 1 percentage point

SHIP = "SHIP"
HOLD = "HOLD"
NOT_REPORTABLE = "NOT_REPORTABLE"

LEVERS = ("ROI", "STRIKE-RATE", "BOTH")


def _get(metrics: Dict[str, Any], key: str) -> Optional[float]:
    value = metrics.get(key)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _ci_spans_zero(metrics: Dict[str, Any]) -> Optional[bool]:
    """Read the bootstrap CI, accepting either shape the harness emits."""
    ci = metrics.get("roi_ci95_bootstrap")
    if isinstance(ci, dict):
        if ci.get("spans_zero") is not None:
            return bool(ci["spans_zero"])
        ci = ci.get("ci_95")
    if isinstance(ci, (list, tuple)) and len(ci) == 2 and None not in ci:
        return bool(float(ci[0]) <= 0.0 <= float(ci[1]))
    return None


def evaluate_ship_criteria(
    baseline: Dict[str, Any],
    candidate: Dict[str, Any],
    lever: str,
    strike_rate_tolerance_rel: float = DEFAULT_STRIKE_RATE_TOLERANCE_REL,
    roi_tolerance_pp: float = DEFAULT_ROI_TOLERANCE_PP,
    min_bets: int = MIN_BETS_REPORTABLE,
) -> Dict[str, Any]:
    """Apply the promotion gate to one ticket's measured result.

    `baseline` and `candidate` are metric dicts carrying, where available:
    n_bets, roi_pct, strike_rate, brier, mean_clv_pct, and either
    roi_ci95_bootstrap (the harness's dict) or a [lo, hi] pair.

    Every criterion reports its own verdict, so a HOLD says which bar was
    missed rather than just refusing.
    """
    if lever not in LEVERS:
        raise ValueError(f"lever must be one of {LEVERS}, got {lever!r}")

    checks: List[Dict[str, Any]] = []

    def add(name, passed, detail, blocking=True):
        checks.append({"criterion": name, "passed": passed,
                       "detail": detail, "blocking": blocking})

    # --- reportability -----------------------------------------------------
    n_bets = _get(candidate, "n_bets")
    if n_bets is None:
        add("reportability", None, "candidate has no n_bets")
    else:
        add("reportability", n_bets >= min_bets,
            f"{int(n_bets)} settled bets vs floor {min_bets}")

    # --- ROI significance --------------------------------------------------
    spans = _ci_spans_zero(candidate)
    if spans is None:
        add("roi_significance", None, "no bootstrap CI on candidate ROI")
    else:
        add("roi_significance", not spans,
            "CI spans zero — no measurable effect" if spans
            else "CI excludes zero")

    # --- lever direction ---------------------------------------------------
    b_roi, c_roi = _get(baseline, "roi_pct"), _get(candidate, "roi_pct")
    b_sr, c_sr = _get(baseline, "strike_rate"), _get(candidate, "strike_rate")

    def direction(name, b, c, unit):
        if b is None or c is None:
            add(f"lever_direction_{name}", None, f"missing {name} on one side")
            return
        add(f"lever_direction_{name}", c > b,
            f"{name} {b:.4f} -> {c:.4f} ({c - b:+.4f}{unit})")

    if lever in ("ROI", "BOTH"):
        direction("roi", b_roi, c_roi, "pp")
    if lever in ("STRIKE-RATE", "BOTH"):
        direction("strike_rate", b_sr, c_sr, "")

    # --- the other lever may not regress beyond tolerance -------------------
    if lever == "ROI":
        if b_sr is None or c_sr is None:
            add("other_lever_strike_rate", None, "missing strike_rate on one side")
        else:
            allowed = b_sr * (1.0 - strike_rate_tolerance_rel)
            add("other_lever_strike_rate", c_sr >= allowed,
                f"strike_rate {b_sr:.4f} -> {c_sr:.4f}, floor "
                f"{allowed:.4f} ({strike_rate_tolerance_rel:.0%} relative)")
    elif lever == "STRIKE-RATE":
        if b_roi is None or c_roi is None:
            add("other_lever_roi", None, "missing roi_pct on one side")
        else:
            allowed = b_roi - roi_tolerance_pp
            add("other_lever_roi", c_roi >= allowed,
                f"roi_pct {b_roi:.2f} -> {c_roi:.2f}, floor {allowed:.2f} "
                f"({roi_tolerance_pp:.1f}pp)")
    else:
        add("other_lever", True, "lever is BOTH — both are checked above",
            blocking=False)

    # --- CLV ---------------------------------------------------------------
    clv = _get(candidate, "mean_clv_pct")
    if clv is None:
        add("clv", None, "no CLV recorded")
    else:
        add("clv", clv >= 0.0, f"mean CLV {clv:+.3f}% (needs >= 0)")

    # --- calibration must not degrade (SYSTEM_MAP constraint 18) -----------
    b_brier, c_brier = _get(baseline, "brier"), _get(candidate, "brier")
    if b_brier is None or c_brier is None:
        add("calibration_brier", None, "missing Brier on one side")
    else:
        add("calibration_brier", c_brier <= b_brier,
            f"Brier {b_brier:.6f} -> {c_brier:.6f} (lower is better)")

    blocking = [c for c in checks if c["blocking"]]
    failed = [c for c in blocking if c["passed"] is False]
    unknown = [c for c in blocking if c["passed"] is None]

    reportability_failed = any(
        c["criterion"] == "reportability" and c["passed"] is False for c in checks)
    significance_failed = any(
        c["criterion"] == "roi_significance" and c["passed"] is False for c in checks)

    if reportability_failed or significance_failed:
        verdict = NOT_REPORTABLE
    elif unknown:
        # Missing evidence never counts toward promotion.
        verdict = NOT_REPORTABLE
    elif failed:
        verdict = HOLD
    else:
        verdict = SHIP

    return {
        "verdict": verdict,
        "lever": lever,
        "checks": checks,
        "failed": [c["criterion"] for c in failed],
        "unknown": [c["criterion"] for c in unknown],
        "summary": _summarise(verdict, failed, unknown),
        "action": ("promote: set the flag on by default" if verdict == SHIP
                   else "keep the flag OFF and record the result"),
    }


def _summarise(verdict, failed, unknown) -> str:
    if verdict == SHIP:
        return "All criteria passed."
    if verdict == NOT_REPORTABLE:
        bits = []
        if failed:
            bits.append("failed: " + ", ".join(c["criterion"] for c in failed))
        if unknown:
            bits.append("not measured: " + ", ".join(c["criterion"] for c in unknown))
        return "Cannot answer the question yet — " + "; ".join(bits) + "."
    return "Measured and rejected — failed: " + ", ".join(c["criterion"] for c in failed) + "."


def format_results_row(ticket: str, lever: str, baseline: Dict[str, Any],
                       candidate: Dict[str, Any], outcome: Dict[str, Any]) -> str:
    """One RESULTS.md table row, in the protocol's step-5 column order."""
    def fmt(metrics, key, spec, scale=1.0):
        v = _get(metrics, key)
        return "-" if v is None else format(v * scale, spec)

    ci = candidate.get("roi_ci95_bootstrap")
    if isinstance(ci, dict):
        ci = ci.get("ci_95")
    ci_str = (f"[{ci[0]:+.1f}, {ci[1]:+.1f}]"
              if isinstance(ci, (list, tuple)) and len(ci) == 2 and None not in ci
              else "-")

    return " | ".join([
        ticket,
        lever,
        f"{fmt(baseline, 'roi_pct', '+.1f')}% / {fmt(baseline, 'strike_rate', '.1f', 100)}%",
        f"{fmt(candidate, 'roi_pct', '+.1f')}% / {fmt(candidate, 'strike_rate', '.1f', 100)}%",
        ci_str,
        fmt(candidate, "n_bets", ".0f"),
        fmt(candidate, "max_drawdown", ".0f"),
        f"{fmt(candidate, 'mean_clv_pct', '+.2f')}%",
        outcome["verdict"],
    ])


def _self_test():
    print("ship_criteria self-test")

    base = {"n_bets": 900, "roi_pct": -4.2, "strike_rate": 0.337,
            "brier": 0.128000, "mean_clv_pct": 0.0,
            "roi_ci95_bootstrap": {"ci_95": [-6.0, -2.4], "spans_zero": False}}

    good = {"n_bets": 880, "roi_pct": 2.5, "strike_rate": 0.341,
            "brier": 0.126000, "mean_clv_pct": 0.8,
            "roi_ci95_bootstrap": {"ci_95": [0.4, 4.6], "spans_zero": False},
            "max_drawdown": 1800.0}

    r = evaluate_ship_criteria(base, good, "ROI")
    assert r["verdict"] == SHIP, r["summary"]
    assert not r["failed"] and not r["unknown"]
    print(f"  clean improvement -> {r['verdict']} ({len(r['checks'])} criteria)")

    # A CI spanning zero is NOT REPORTABLE, never a HOLD and never a SHIP.
    noisy = {**good, "roi_ci95_bootstrap": {"ci_95": [-1.5, 6.2], "spans_zero": True}}
    r2 = evaluate_ship_criteria(base, noisy, "ROI")
    assert r2["verdict"] == NOT_REPORTABLE, r2
    assert "roi_significance" in r2["failed"], r2["failed"]
    print(f"  ROI CI spanning zero -> {r2['verdict']} (not a loss, an absence)")

    # Below the 200-bet floor, nothing is reportable however good it looks.
    thin = {**good, "n_bets": 60, "roi_pct": 40.0}
    r3 = evaluate_ship_criteria(base, thin, "ROI")
    assert r3["verdict"] == NOT_REPORTABLE and "reportability" in r3["failed"]
    print(f"  60 bets at +40% ROI -> {r3['verdict']} (floor is {MIN_BETS_REPORTABLE})")

    # Right direction on the lever, but the other lever collapses.
    tradeoff = {**good, "strike_rate": 0.240}
    r4 = evaluate_ship_criteria(base, tradeoff, "ROI")
    assert r4["verdict"] == HOLD and "other_lever_strike_rate" in r4["failed"], r4
    print(f"  ROI up but strike rate 33.7%->24.0% -> {r4['verdict']} "
          f"({r4['failed']})")

    # Wrong lever: a ROI ticket that only moved strike rate.
    wrong = {**good, "roi_pct": -4.5, "strike_rate": 0.360}
    r5 = evaluate_ship_criteria(base, wrong, "ROI")
    assert r5["verdict"] == HOLD and "lever_direction_roi" in r5["failed"], r5
    print(f"  ROI ticket that only moved strike rate -> {r5['verdict']}")

    # Constraint 18: degraded calibration blocks a hit-rate win.
    sr_base = {**base, "roi_pct": 1.0,
               "roi_ci95_bootstrap": {"ci_95": [0.2, 1.8], "spans_zero": False}}
    sr_bad = {**good, "roi_pct": 0.6, "strike_rate": 0.370, "brier": 0.140}
    r6 = evaluate_ship_criteria(sr_base, sr_bad, "STRIKE-RATE")
    assert r6["verdict"] == HOLD and "calibration_brier" in r6["failed"], r6
    print(f"  hit rate up but Brier 0.128->0.140 -> {r6['verdict']} "
          f"(constraint 18)")

    # Missing evidence must never promote.
    silent = {k: v for k, v in good.items() if k != "mean_clv_pct"}
    r7 = evaluate_ship_criteria(base, silent, "ROI")
    assert r7["verdict"] == NOT_REPORTABLE and "clv" in r7["unknown"], r7
    bare = {"n_bets": 900, "roi_pct": 5.0}
    r8 = evaluate_ship_criteria(base, bare, "ROI")
    assert r8["verdict"] == NOT_REPORTABLE and r8["unknown"], r8
    print(f"  absent CLV -> {r7['verdict']}; a bare metrics dict -> {r8['verdict']}")

    # BOTH requires movement on both levers.
    r9 = evaluate_ship_criteria(base, good, "BOTH")
    assert r9["verdict"] == SHIP, r9["summary"]
    both_half = {**good, "strike_rate": 0.330}
    r10 = evaluate_ship_criteria(base, both_half, "BOTH")
    assert r10["verdict"] == HOLD and "lever_direction_strike_rate" in r10["failed"]
    print(f"  BOTH: needs movement on each lever, half-win -> {r10['verdict']}")

    # A bare [lo, hi] pair must work as well as the harness's dict.
    pair = {**good, "roi_ci95_bootstrap": [0.4, 4.6]}
    assert evaluate_ship_criteria(base, pair, "ROI")["verdict"] == SHIP
    assert _ci_spans_zero({"roi_ci95_bootstrap": [-1.0, 1.0]}) is True
    assert _ci_spans_zero({}) is None
    print("  CI accepted as harness dict or as a bare [lo, hi] pair")

    try:
        evaluate_ship_criteria(base, good, "SPEED")
        raise AssertionError("expected ValueError on an unknown lever")
    except ValueError:
        pass

    row = format_results_row("T9", "ROI", base, good, r)
    assert row.startswith("T9 | ROI |") and "SHIP" in row
    assert row.count("|") == 8, row
    print(f"  results row: {row}")

    print("All tests completed successfully.")


if __name__ == "__main__":
    _self_test()
