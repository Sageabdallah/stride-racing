#!/usr/bin/env python3
"""Convergence tier / crowd-gate P&L attribution — the number nothing measures.

SYSTEM_MAP's "empirical facts nothing in the repo measures" list opens with
exactly this: the realised return of each convergence tier, and above all what
the FLAG tier (model-confident, externally unconfirmed, forced NO_BET) would
have returned. Every pick is stamped with a tier at blend time, results land in
race_results_history, and yet the only aggregation anywhere is a MODEL_ONLY
count. This script settles the counterfactual: flat 1u on every runner of every
tier, priced at SP, stats via roi_stats so no ROI is ever printed bare.

Honest caveats, printed with every report:
  * Prices are STARTING PRICE, not tip-time — SP is the only settled price the
    history holds (runner_odds_snapshots only accrues after roi/04 deploys).
    This measures selection skill at SP, not realised betting P&L; CLV and
    execution slippage are invisible here.
  * NO_BET tiers were never actually bet: their block is a counterfactual, and
    a NOT_REPORTABLE verdict on it means exactly that — no evidence either way.
  * One window, many tiers: the multi-comparison block applies. A tier that
    "wins" this sweep still has to clear the Bonferroni bar.

Usage:
  python tier_pnl_attribution.py                       # full history
  python tier_pnl_attribution.py --from 2026-03-01 --to 2026-04-19
  python tier_pnl_attribution.py --json                # machine-readable
  python tier_pnl_attribution.py --out report.json     # persist the block

Read-only: SELECTs only, no writes, no schema changes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import roi_stats

COMMISSION_DEFAULT = 0.08

# Blend-time tier -> what the live system actually did with it. The BET/NO_BET
# split is the decision boundary the whole gate exists to draw, so the report
# rolls tiers up to it as well: "everything we bet" vs "everything we refused".
TIER_ACTION = {
    "LOCK": "BET",
    "CONFIRM": "BET",
    "CONFIRM_WEAK": "NO_BET",
    "FLAG": "NO_BET",
    "SKIP": "NO_BET",
    "CROWD_OVERRIDE": "TRACKED",
    "CROWD_ONLY_WEAK": "TRACKED",
}

JOIN_SQL = """
SELECT co.race_date::date       AS race_date,
       co.track                 AS track,
       co.race_number           AS race_number,
       co.horse_name            AS horse_name,
       co.convergence_tier      AS tier,
       rrh.position             AS position,
       rrh.sp_odds              AS sp_odds
FROM convergence_output co
LEFT JOIN race_results_history rrh
  ON co.race_date::date = rrh.race_date::date
  AND LOWER(co.track) = LOWER(rrh.track)
  AND co.race_number = rrh.race_number
  AND LOWER(co.horse_name) = LOWER(rrh.horse_name)
WHERE (%(date_from)s IS NULL OR co.race_date::date >= %(date_from)s)
  AND (%(date_to)s   IS NULL OR co.race_date::date <= %(date_to)s)
"""


def _connect():
    import psycopg2

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        env = Path(__file__).resolve().parents[2] / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
                    break
    if not url:
        raise RuntimeError("DATABASE_URL not set (env or project .env)")
    conn = psycopg2.connect(url)
    conn.autocommit = True  # read-only; never hold a transaction open
    return conn


def _require_columns(conn) -> None:
    """Fail with a message that names the missing column, not a KeyError later."""
    need = {
        "convergence_output": {"race_date", "track", "race_number", "horse_name",
                               "convergence_tier"},
        "race_results_history": {"race_date", "track", "race_number", "horse_name",
                                 "position", "sp_odds"},
    }
    cur = conn.cursor()
    try:
        for table, cols in need.items():
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s", (table,))
            have = {r[0] for r in cur.fetchall()}
            missing = cols - have
            if not have:
                raise RuntimeError(f"table {table} not found")
            if missing:
                raise RuntimeError(f"{table} is missing column(s): {sorted(missing)}")
    finally:
        cur.close()


def fetch_rows(conn, date_from: Optional[str], date_to: Optional[str]) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    try:
        cur.execute(JOIN_SQL, {"date_from": date_from, "date_to": date_to})
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        cur.close()


def attribute(rows: Sequence[Dict[str, Any]],
              commission: float = COMMISSION_DEFAULT) -> Dict[str, Any]:
    """Pure aggregation: tier -> full roi_stats block, plus BET/NO_BET rollups.

    A row enters a P&L block only when it has BOTH a matched result (position)
    and a usable SP (> 1.0). Everything excluded is counted, never silently
    dropped — a coverage number nobody can see is how a gate stays unmeasured
    for a year.
    """
    total = len(rows)
    unmatched = [r for r in rows if r.get("position") is None]
    matched = [r for r in rows if r.get("position") is not None]

    def _usable_sp(r):
        try:
            return r.get("sp_odds") is not None and float(r["sp_odds"]) > 1.0
        except (TypeError, ValueError):
            return False

    no_price = [r for r in matched if not _usable_sp(r)]
    usable = [r for r in matched if _usable_sp(r)]

    def _block(sub: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not sub:
            return None
        won = [1.0 if int(r["position"]) == 1 else 0.0 for r in sub]
        odds = [float(r["sp_odds"]) for r in sub]
        return roi_stats.summarise_bets(won, odds, commission=commission)

    tiers: Dict[str, List[Dict[str, Any]]] = {}
    for r in usable:
        tier = (r.get("tier") or "UNTIERED").strip().upper()
        tiers.setdefault(tier, []).append(r)

    per_tier = {t: _block(sub) for t, sub in sorted(tiers.items())}

    rollups: Dict[str, List[Dict[str, Any]]] = {}
    for r in usable:
        tier = (r.get("tier") or "UNTIERED").strip().upper()
        rollups.setdefault(TIER_ACTION.get(tier, "TRACKED"), []).append(r)
    per_action = {a: _block(sub) for a, sub in sorted(rollups.items())}

    measured = [b for b in per_tier.values() if b is not None]
    multi = roi_stats.multi_comparison_block(measured) if len(measured) > 1 else None

    return {
        "commission": commission,
        "stake_model": "flat 1u per runner, priced at SP",
        "coverage": {
            "convergence_rows": total,
            "matched_results": len(matched),
            "unmatched_results": len(unmatched),
            "matched_no_usable_sp": len(no_price),
            "used_in_pnl": len(usable),
        },
        "tiers": per_tier,
        "actions": per_action,
        "multi_comparison": multi,
        "caveats": [
            "prices are SP, not tip-time — selection skill at SP, not realised P&L",
            "NO_BET tiers are counterfactual: they were never actually staked",
            "one window, many tiers: apply the multi_comparison block before "
            "believing any single tier's ROI",
        ],
    }


def _fmt_block(name: str, b: Optional[Dict[str, Any]]) -> str:
    if b is None:
        return f"  {name:<16} (no usable rows)"
    ci = b.get("ci95") or [None, None]
    ci_s = (f"[{ci[0]:+.1f}, {ci[1]:+.1f}]" if None not in ci else "-")
    return (f"  {name:<16} bets {b['bets']:>5}  wins {b['wins']:>4}  "
            f"strike {b['strike_rate']:>5.1f}%  roi_gross {b['roi_gross']:>+7.2f}%  "
            f"roi_net {b['roi_net']:>+7.2f}%  ci95 {ci_s:<18} {b['reportable_status']}")


def render(report: Dict[str, Any]) -> str:
    cov = report["coverage"]
    lines = ["", "=== TIER / CROWD-GATE P&L ATTRIBUTION (counterfactual, SP-priced) ==="]
    lines.append(f"rows {cov['convergence_rows']} | matched {cov['matched_results']} "
                 f"| unmatched {cov['unmatched_results']} "
                 f"| no-usable-SP {cov['matched_no_usable_sp']} "
                 f"| used {cov['used_in_pnl']}")
    lines.append(f"stake model: {report['stake_model']} | commission {report['commission']:.0%}")
    lines.append("\n-- per tier --")
    for t, b in report["tiers"].items():
        lines.append(_fmt_block(t, b))
    lines.append("\n-- per live action (LOCK+CONFIRM were BET; FLAG/CONFIRM_WEAK/SKIP were NO_BET) --")
    for a, b in report["actions"].items():
        lines.append(_fmt_block(a, b))
    if report.get("multi_comparison"):
        m = report["multi_comparison"]
        lines.append(f"\nmulti-comparison: {m['n_strategies']} tiers on one window; "
                     f"needs z >= {m['bonferroni_z_required']}, best observed "
                     f"{m['best_observed_z']} -> {m['verdict']}")
    lines.append("\ncaveats:")
    for c in report["caveats"]:
        lines.append(f"  * {c}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Counterfactual P&L per convergence tier (SP-priced, read-only).")
    ap.add_argument("--from", dest="date_from", default=None, help="YYYY-MM-DD inclusive")
    ap.add_argument("--to", dest="date_to", default=None, help="YYYY-MM-DD inclusive")
    ap.add_argument("--commission", type=float, default=COMMISSION_DEFAULT)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--out", default=None, help="also write the JSON block to this path")
    args = ap.parse_args()

    conn = _connect()
    try:
        _require_columns(conn)
        rows = fetch_rows(conn, args.date_from, args.date_to)
    finally:
        conn.close()

    report = attribute(rows, commission=args.commission)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, default=str),
                                  encoding="utf-8")
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    return 0


def _self_test() -> None:
    print("tier_pnl_attribution self-test")

    def row(tier, pos, sp, date="2026-03-07"):
        return {"race_date": date, "track": "Flemington", "race_number": 1,
                "horse_name": f"H{pos}{sp}", "tier": tier, "position": pos,
                "sp_odds": sp}

    rows = [
        # FLAG: 1 win at $6, 2 losses -> gross (5 - 2)/3
        row("FLAG", 1, 6.0), row("FLAG", 4, 3.0), row("FLAG", 7, 11.0),
        # LOCK: 1 win at $2.5, 1 loss
        row("LOCK", 1, 2.5), row("LOCK", 2, 4.0),
        # unmatched result and unusable SP must be excluded but counted
        {"race_date": "2026-03-07", "track": "Flemington", "race_number": 2,
         "horse_name": "NoResult", "tier": "SKIP", "position": None, "sp_odds": 5.0},
        row("SKIP", 3, None),
        row(None, 5, 8.0),  # untiered bucket
    ]
    rep = attribute(rows, commission=0.08)

    cov = rep["coverage"]
    assert cov["convergence_rows"] == 8 and cov["used_in_pnl"] == 6, cov
    assert cov["unmatched_results"] == 1 and cov["matched_no_usable_sp"] == 1, cov

    flag = rep["tiers"]["FLAG"]
    assert flag["bets"] == 3 and flag["wins"] == 1
    expected = roi_stats.summarise_bets([1, 0, 0], [6.0, 3.0, 11.0], commission=0.08)
    assert flag["roi_gross"] == expected["roi_gross"] == 100.0, flag["roi_gross"]
    assert flag["roi_net"] == expected["roi_net"], "net must come from roi_stats, one source"
    assert flag["reportable"] is False, "3 bets can never clear the 200 floor"

    assert "UNTIERED" in rep["tiers"], "a null tier must still be counted, not dropped"
    acts = rep["actions"]
    assert acts["BET"]["bets"] == 2 and acts["NO_BET"]["bets"] == 3, \
        {a: b["bets"] for a, b in acts.items() if b}
    assert acts["TRACKED"]["bets"] == 1  # the untiered row falls to TRACKED

    assert rep["multi_comparison"] is not None
    out = render(rep)
    assert "NOT_REPORTABLE" in out and "SP" in out
    print("  coverage counting, per-tier blocks, rollups, sweep label: all OK")
    print("All tests completed successfully.")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        raise SystemExit(main())
