#!/usr/bin/env python3
"""Task 09: forward validation runner. No free parameters by construction.

Given a registry entry's rule, pulls settled ledger rows inside window B
that match the rule's tip-time criteria, computes net ROI and CLV via
roi_stats, and emits PASS / FAIL / INSUFFICIENT_SAMPLE against the
pre-registered criterion. The rule is data in REGISTRY below, keyed by
entry id; the script cannot search variants because it takes nothing but
the entry id.

Verdicts:
  PASS                 lower 95 CI of net ROI > 0 and mean CLV > 0, n >= min
  FAIL                 n >= min and either criterion missed
  INSUFFICIENT_SAMPLE  n < min settled bets in window B
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

PRICE_SOURCES = ("betfair", "betfair_delayed", "betfair_snapshot_db",
                 "betfair_delayed_backfill")

REGISTRY: Dict[str, Dict[str, Any]] = {
    # Mirrors docs/validation/registry.md. The doc is the ledger of record;
    # this dict is the machine-readable copy the runner executes.
    "VR-001": {
        "window_b": ("2026-08-02", "2026-09-13"),
        "price_min": 2.0,
        "price_max": 15.0,
        "price_sources": PRICE_SOURCES,
        "min_bets": 200,
        # VR-001-C1: the consensus pillar was already non-functional at
        # registration, so window B never validly ran. Until this key existed
        # the entry stayed runnable and would have emitted a verdict over an
        # invalid window on 2026-09-14 — the invalidation lived only in
        # markdown. Kept in REGISTRY rather than deleted so the id resolves
        # and refuses; deleting it would make the same command fail with an
        # argparse error that reads like a typo.
        "invalidated": (
            "VR-001-C1 (docs/validation/VR-001-invalidation.md): the "
            "extraction model was retired 2026-06-15, 48 days before "
            "registration; crowd_score was all-zero for every runner, so the "
            "realised bet population is not the one the rule describes. "
            "Not a FAIL and not graveyarded — never tested. Successor: VR-002."
        ),
    },
    "VR-002": {
        # Successor to VR-001. DRAFT until the open date is filled at deploy:
        # window_b[0] is None on purpose so a premature run refuses loudly
        # instead of silently scanning from an assumed date.
        "window_b": (None, "2026-12-31"),
        "price_min": 2.0,
        "price_max": 15.0,
        "price_sources": PRICE_SOURCES,
        "min_bets": 200,
        # Amended 2026-08-04 before open: window B closes on the n-th
        # qualifying settled bet, not on a fixed 42-day date, because 26
        # observation days x 6 bets/day cannot reach 200. window_b[1] is the
        # registered hard stop, after which the entry resolves
        # INSUFFICIENT_SAMPLE and is never re-selected.
        "closes_on_sample": True,
    },
}


def window_read_allowed(entry: Dict[str, Any], today=None, n_settled=None):
    """Mechanical no-peeking guard: window-B outcomes are unreadable until
    the window has closed. The registry's discipline, enforced in code —
    the operator being away must never leave early reading as an option.

    Four refusals, in order. An INVALIDATED entry never becomes readable at
    any date. A DRAFT entry (no open date filled) is not accruing yet. An
    entry that closes on sample is readable once the n-th qualifying settled
    bet exists, or once the registered hard stop has passed — whichever comes
    first; `n_settled` must come from count_qualifying_settled(), which reads
    no outcome column. Otherwise the fixed close date applies.

    Returns (allowed, reason)."""
    from datetime import datetime, timedelta

    if entry.get("invalidated"):
        return False, f"INVALIDATED — {entry['invalidated']}"

    opened = entry["window_b"][0]
    if opened is None:
        return False, ("DRAFT — window B has no open date. Fill it in "
                       "docs/validation/registry.md and REGISTRY at the "
                       "deploy that changes the bet population, then re-run.")

    close = datetime.strptime(entry["window_b"][1], "%Y-%m-%d").date()
    if today is None:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Australia/Sydney")).date()

    if entry.get("closes_on_sample"):
        need = entry["min_bets"]
        if n_settled is not None and n_settled >= need:
            return True, ""
        if today > close:
            return True, ""
        have = "unknown" if n_settled is None else str(n_settled)
        return False, (f"window B closes on the {need}th qualifying settled "
                       f"bet (have {have}) or at the hard stop {close}, "
                       f"whichever is first; neither has been reached")

    first_read = close + timedelta(days=1)
    if today < first_read:
        return False, (f"window B closes {close}; outcomes are readable from "
                       f"{first_read} ({(first_read - today).days} day(s) away)")
    return True, ""


def count_qualifying_settled(entry: Dict[str, Any], rows) -> int:
    """How many rows already qualify, counted without touching an outcome.

    The sample-close guard needs n before it may load outcomes, so this
    counts on the same predicates as rows_matching minus anything derived
    from the result. Callers must supply rows selected without `won`,
    `clv_pct` or `pnl` — see _load_qualifying_rows.
    """
    return len(rows_matching(entry, rows))


def rows_matching(entry: Dict[str, Any],
                  rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    lo, hi = entry["window_b"]
    out = []
    for r in rows:
        if not r.get("settled") or r.get("refused"):
            continue
        d = str(r.get("race_date") or "")
        if not (lo <= d <= hi):
            continue
        price = r.get("price_taken")
        if not price or not (entry["price_min"] <= float(price)
                             <= entry["price_max"]):
            continue
        if str(r.get("price_source") or "") not in entry["price_sources"]:
            continue
        out.append(r)
    return out


def verdict_for(entry: Dict[str, Any],
                rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    matched = rows_matching(entry, rows)
    n = len(matched)
    result: Dict[str, Any] = {"n_bets": n, "min_bets": entry["min_bets"]}
    if n < entry["min_bets"]:
        result["verdict"] = "INSUFFICIENT_SAMPLE"
        return result

    from roi_stats import ci_lower_bound, per_bet_returns, roi_ci
    won = [1.0 if r.get("won") else 0.0 for r in matched]
    odds = [float(r["price_taken"]) for r in matched]
    from roi_stats import commission_rate_from_env
    returns = per_bet_returns(won, odds, commission_rate_from_env())
    ci = roi_ci(returns)
    lower = ci_lower_bound(ci.get("ci95"))
    clvs = [float(r["clv_pct"]) for r in matched if r.get("clv_pct") is not None]
    mean_clv = (sum(clvs) / len(clvs)) if clvs else None

    result.update({
        "roi_ci95": ci.get("ci95"),
        "roi_ci_lower": lower,
        "mean_clv": mean_clv,
    })
    passed = (lower is not None and lower > 0
              and mean_clv is not None and mean_clv > 0)
    result["verdict"] = "PASS" if passed else "FAIL"
    return result


def _load_ledger_rows(lo: str, hi: str) -> List[Dict[str, Any]]:
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT race_date::text AS race_date, settled, refused, won, stake, "
        "price_taken, price_source, clv_pct FROM selection_ledger "
        "WHERE race_date BETWEEN %s AND %s", (lo, hi))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _load_qualifying_rows(lo: str, hi: str) -> List[Dict[str, Any]]:
    """Window-B rows with every outcome column left unselected.

    Feeds the sample-close guard, which must know n before it may decide
    whether outcomes are readable. `won`, `clv_pct` and `pnl` are absent by
    construction, so this cannot leak a result even if it is called early.
    """
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT race_date::text AS race_date, settled, refused, "
        "price_taken, price_source FROM selection_ledger "
        "WHERE race_date BETWEEN %s AND %s", (lo, hi))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("entry_id", choices=sorted(REGISTRY))
    args = parser.parse_args()
    entry = REGISTRY[args.entry_id]

    # An invalidated or unopened entry refuses without touching the DB at
    # all — there is no n to count and no outcome to protect.
    if entry.get("invalidated") or entry["window_b"][0] is None:
        allowed, why = window_read_allowed(entry)
        verdict = "INVALIDATED" if entry.get("invalidated") else "DRAFT_NOT_OPENED"
        print(json.dumps({"entry": args.entry_id, "verdict": verdict,
                          "reason": why}, indent=2))
        return 3

    # Sample-close entries need n first, counted over outcome-free columns.
    n_settled = None
    if entry.get("closes_on_sample"):
        n_settled = count_qualifying_settled(
            entry, _load_qualifying_rows(*entry["window_b"]))

    allowed, why = window_read_allowed(entry, n_settled=n_settled)
    if not allowed:
        # Refused BEFORE any outcome read: the guard means no outcome column
        # is ever loaded early, not merely that a loaded result goes unprinted.
        print(json.dumps({"entry": args.entry_id, "verdict": "WINDOW_LOCKED",
                          "reason": why, "n_qualifying": n_settled},
                         indent=2))
        return 3
    rows = _load_ledger_rows(*entry["window_b"])
    result = verdict_for(entry, rows)
    print(json.dumps({"entry": args.entry_id, **result}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
