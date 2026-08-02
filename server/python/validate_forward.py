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

REGISTRY: Dict[str, Dict[str, Any]] = {
    # Mirrors docs/validation/registry.md VR-001. The doc is the ledger of
    # record; this dict is the machine-readable copy the runner executes.
    "VR-001": {
        "window_b": ("2026-08-02", "2026-09-13"),
        "price_min": 2.0,
        "price_max": 15.0,
        "price_sources": ("betfair", "betfair_delayed", "betfair_snapshot_db",
                          "betfair_delayed_backfill"),
        "min_bets": 200,
    },
}


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("entry_id", choices=sorted(REGISTRY))
    args = parser.parse_args()
    entry = REGISTRY[args.entry_id]
    rows = _load_ledger_rows(*entry["window_b"])
    result = verdict_for(entry, rows)
    print(json.dumps({"entry": args.entry_id, **result}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
