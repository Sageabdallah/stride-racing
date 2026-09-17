#!/usr/bin/env python3
"""Offline flat-vs-logged-shadow replay. No plan generation and no DB writes.

Flat = 1% of the explicitly supplied starting bank on the recorded selections;
shadow = the logged stake_pct of current replay equity. Bets are consumed in
race_date/track/race_number/horse order (within-day clock times are unavailable).
An unfundable flat bet stops that rule; there is no borrowing or stake resizing.
Mean realised log return per selection estimates expected log growth; it is not
a population guarantee. Paired race-day bootstrap intervals describe this path.
"""
import argparse
import json
import math
import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from roi_stats import (DEFAULT_N_BOOT, max_drawdown_and_streaks,
                       race_day_bootstrap_ci, roi_ci)
from selection_ledger import STAKE_UNITS
from shadow_pl_tracker import PHANTOM_PRICE_FENCE
from walk_forward_backtest import compute_max_drawdown

INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
METRICS = ("mean_log_growth_per_selection", "max_drawdown", "max_drawdown_pct",
           "longest_losing_streak", "net_roi_pct")
RULES = ("live_flat_1u", "logged_shadow_fraction")


def _number(value, name):
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"invalid {name}") from None
    if not math.isfinite(value):
        raise ValueError(f"non-finite {name}")
    return value


def prepare_rows(rows):
    """Validate stored outcomes/plans, reporting each input's implied units."""
    paired, conventions, seen = [], [], set()
    counts = dict(input_rows=len(rows), eligible_settled_bets=0, rows_with_plan=0,
                  excluded_unsettled_or_nonbet=0, excluded_phantom_price_fence=0)
    for index, row in enumerate(rows):
        if (not row.get("settled") or row.get("refused") or row.get("should_bet") is False
                or _number(row.get("stake") or 0, "stake") <= 0):
            counts["excluded_unsettled_or_nonbet"] += 1
            conventions.append({"input_index": index, "excluded": "unsettled/refused/nonbet",
                                "ledger_implied_bankroll": None,
                                "note": "No positive settled stake to infer a replay convention"})
            continue
        day = str(row.get("race_date", ""))
        date.fromisoformat(day)  # missing days cannot become independent-bet blocks
        if PHANTOM_PRICE_FENCE[0] <= day <= PHANTOM_PRICE_FENCE[1]:
            counts["excluded_phantom_price_fence"] += 1
            conventions.append({"input_index": index, "excluded": "phantom_price_fence",
                                "ledger_implied_bankroll": None})
            continue
        key = (day, str(row.get("track", "")), int(row.get("race_number") or 0),
               str(row.get("horse_name", row.get("horse", ""))))
        if key in seen:
            raise ValueError(f"duplicate selection: {key}")
        seen.add(key)
        if row.get("won") is not True and row.get("won") is not False:
            raise ValueError(f"missing settled outcome: {key}")
        stake = _number(row["stake"], "stake")
        pnl = _number(row.get("pnl"), "pnl")
        # Use the recorded NET settlement, not today's price/commission code.
        net_return = pnl / stake
        if net_return < -1 or (not row["won"] and not math.isclose(net_return, -1)):
            raise ValueError(f"inconsistent net settlement: {key}")
        units = _number(row.get("stake_units", STAKE_UNITS.get(row.get("stake_rule"))), "stake_units")
        if units <= 0:
            raise ValueError(f"positive stake without unit convention: {key}")
        convention = {"input_index": index, "selection": list(key),
                      "stake_rule": row.get("stake_rule"), "recorded_stake_units": units,
                      "ledger_implied_bankroll": stake * 100.0 / units,
                      "ledger_amount_per_unit": stake / units,
                      "shadow_implied_bankroll": None}
        conventions.append(convention)
        counts["eligible_settled_bets"] += 1
        plan = row.get("shadow_kelly_json")
        if plan is None:
            continue
        if isinstance(plan, str):
            plan = json.loads(plan)
        if not isinstance(plan, dict) or plan.get("applied") is not False:
            raise ValueError(f"invalid or applied shadow plan: {key}")
        pct = _number(plan.get("stake_pct"), "shadow stake_pct")
        cap = _number(plan.get("max_stake_pct"), "shadow max_stake_pct")
        if not 0 <= pct <= cap < 100:
            raise ValueError(f"invalid logged fraction/cap: {key}")
        amount = _number(plan.get("stake"), "shadow stake")
        if amount < 0 or (pct == 0 and amount != 0):
            raise ValueError(f"inconsistent shadow amount: {key}")
        if pct > 0:
            convention["shadow_implied_bankroll"] = amount * 100.0 / pct
        counts["rows_with_plan"] += 1
        clv = _number(row["clv_pct"], "clv_pct") if row.get("clv_pct") is not None else None
        paired.append({"key": key, "day": day, "net_return": net_return,
                       "fraction": pct / 100.0, "clv_pct": clv})
    return sorted(paired, key=lambda r: r["key"]), counts, conventions


def path_metrics(rows, bankroll, rule):
    equity, pnl, stakes, stopped = bankroll, [], [], False
    for row in rows:
        stake = bankroll / 100.0 if rule == RULES[0] else equity * row["fraction"]
        if stopped or stake > equity + 1e-9:
            stopped = True
            stake = 0.0
        gain = stake * row["net_return"]
        equity += gain
        pnl.append(gain)
        stakes.append(stake)
    pnl, stakes = np.asarray(pnl), np.asarray(stakes)
    # Prepending starting equity makes the shared P&L drawdown function count
    # an initial losing run. Passing pnl alone would miss the exact failure.
    drawdown, drawdown_pct = compute_max_drawdown(np.r_[bankroll, pnl])
    active = stakes > 0
    total_stake = float(stakes.sum())
    # roi_ci's mean is the shared ROI calculation. Rescale to equal mean stake
    # so variable-size bets yield profit / total staked, not mean individual ROI.
    roi = roi_ci(pnl * len(rows) / total_stake, n_boot=0)["roi"] if total_stake else None
    growth = math.log(equity / bankroll) / len(rows) if equity > 0 else -math.inf
    return dict(zip(METRICS, [growth, drawdown, drawdown_pct,
        max_drawdown_and_streaks(pnl[active])["max_losing_streak"], roi])), {
        "ending_bankroll": equity, "bets_staked": int(active.sum()),
        "stopped_unfundable": stopped, "total_staked": total_stake}


def replay(rows, *, bankroll, min_bets=500, n_boot=DEFAULT_N_BOOT, seed=42):
    bankroll = _number(bankroll, "bankroll")
    if bankroll <= 0 or min_bets < 1 or n_boot < 1:
        raise ValueError("bankroll, min_bets and bootstrap count must be positive")
    paired, counts, conventions = prepare_rows(rows)
    days = sorted({row["day"] for row in paired})
    report = {
        "status": "OK", "applied": False, **counts, "paired_race_days": len(days),
        "bankroll": bankroll, "min_bets": min_bets,
        "threshold_note": "Replay ticket requires 500; kelly_readiness uses 400. Not reconciled.",
        "convention": "Flat 1u = 1% of explicit starting bank, fixed; shadow logged stake_pct compounds. "
                      "Both use the same selections and recorded pnl/stake net returns. "
                      "Unfundable flat bets stop that rule. No selection/risk gates are rerun.",
        "input_convention_note": "Ledger convention is stake * 100 / stake_units (default bank 10000); "
                                 "staking_controls starts at 100 units. Shadow implied bank is approximate "
                                 "because stored currency/percent are rounded; zero fraction is indeterminate.",
        "ordering": "race_date, track, race_number, horse_name; actual within-day time ordering unverified",
        "provenance": "Only stored shadow_kelly_json is replayed, never recomputed. Historical tip-time "
                      "provenance is unverified: the old settlement path could overwrite plans.",
        "input_row_conventions": conventions,
        "rules": {rule: {metric: INSUFFICIENT_SAMPLE for metric in METRICS} for rule in RULES},
        "ci95_race_day_bootstrap": INSUFFICIENT_SAMPLE,
        "comparison": INSUFFICIENT_SAMPLE,
        "coverage_note": "Only the paired subset is compared; missing plans may cause selection bias.",
    }
    # Counting settled rows alone passes even if all plans are NULL. The matched
    # plan count is the denominator for BOTH rules, and is printed on refusal.
    if len(paired) < min_bets or len(days) < 2:
        report["status"] = INSUFFICIENT_SAMPLE
        report["reason"] = (f"{len(paired)}/{counts['eligible_settled_bets']} eligible rows had a stored plan; "
                            f"need {min_bets} plans and at least 2 race-days")
        return report
    for rule in RULES:
        metrics, detail = path_metrics(paired, bankroll, rule)
        report["rules"][rule] = {**metrics, **detail}
    flat, shadow = (report["rules"][r] for r in RULES)
    clvs = [row["clv_pct"] for row in paired if row["clv_pct"] is not None]
    mean_clv = sum(clvs) / len(clvs) if clvs else None
    report["comparison"] = {
        "shadow_higher_log_growth": shadow[METRICS[0]] > flat[METRICS[0]],
        "shadow_lower_max_drawdown": shadow["max_drawdown"] < flat["max_drawdown"],
        "paired_mean_clv_pct": mean_clv, "paired_clv_rows": len(clvs),
        "positive_clv_over_full_paired_window": (mean_clv > 0 if len(clvs) == len(paired) else None),
        "activation": "PROHIBITED; this is a replay, not a readiness decision",
    }
    def statistic(indices):
        sample = [paired[int(i)] for i in indices]
        values = []
        for rule in RULES:
            metrics, _ = path_metrics(sample, bankroll, rule)
            values.extend(metrics[metric] for metric in METRICS)
        return values
    report["ci95_race_day_bootstrap"] = race_day_bootstrap_ci(
        [row["day"] for row in paired], statistic, n_boot=n_boot, seed=seed)
    report["ci95_race_day_bootstrap"]["metric_order"] = [f"{r}.{m}" for r in RULES for m in METRICS]
    return report


def load_rows(path=None):
    if path:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("ledger JSON must be an array of database row objects")
        return rows
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise ValueError("DATABASE_URL unset and no --ledger-json supplied; no real ledger rows available")
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(url)
    try:
        conn.set_session(readonly=True)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT race_date, track, race_number, horse_name, settled, refused, "
                        "should_bet, stake_rule, stake_units, stake, pnl, won, clv_pct, shadow_kelly_json "
                        "FROM selection_ledger WHERE settled = TRUE "
                        "ORDER BY race_date, track, race_number, horse_name")
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return "BANKRUPT" if value == -math.inf else "UNDEFINED"
    return value


def _synthetic_rows(n=500):
    return [{"race_date": str(date(2026, 9, 1) + timedelta(days=i // 5)),
             "track": "Synthetic", "race_number": i % 5 + 1, "horse_name": f"H{i}",
             "settled": True, "refused": False, "should_bet": True,
             "stake_rule": "1u", "stake_units": 1, "stake": 100.0,
             "pnl": 184.0 if i % 3 == 0 else -100.0, "won": i % 3 == 0, "clv_pct": 1.0,
             "shadow_kelly_json": {"applied": False, "stake_pct": 0.5,
                                   "stake": 50.0, "max_stake_pct": 2.0}} for i in range(n)]


def _self_test():
    rows = _synthetic_rows()
    small = replay(rows[:499], bankroll=10000, n_boot=20)
    assert small["status"] == INSUFFICIENT_SAMPLE
    assert all(v == INSUFFICIENT_SAMPLE for r in small["rules"].values() for v in r.values())
    missing = replay([{**r, "shadow_kelly_json": None} for r in rows], bankroll=10000, n_boot=20)
    assert missing["rows_with_plan"] == 0 and missing["status"] == INSUFFICIENT_SAMPLE
    report = replay(rows, bankroll=10000, n_boot=30)
    assert report["status"] == "OK" and report["rows_with_plan"] == 500 and report["applied"] is False
    assert report["input_row_conventions"][0]["ledger_implied_bankroll"] == 10000
    assert report["input_row_conventions"][0]["shadow_implied_bankroll"] == 10000
    assert replay(rows, bankroll=10000, n_boot=30) == report
    losing = [{"net_return": -1.0, "fraction": 0.02}] * 3
    metrics, _ = path_metrics(losing, 100, RULES[0])
    assert metrics["max_drawdown"] == 3 and metrics["max_drawdown_pct"] == 3
    assert metrics["longest_losing_streak"] == 3 and metrics["net_roi_pct"] == -100
    assert abs(metrics["mean_log_growth_per_selection"] - math.log(0.97)/3) < 1e-12
    metrics, detail = path_metrics(losing * 40, 100, RULES[0])
    assert detail["stopped_unfundable"] and detail["bets_staked"] == 100
    assert metrics["mean_log_growth_per_selection"] == -math.inf
    # Bootstrap must sample whole days with replacement, retaining within-day
    # row order; merely counting blocks would pass independent-bet resampling.
    seen = []
    def check_blocks(indices):
        values = list(map(int, indices))
        assert all(values[i:i+2] in ([0, 1], [2, 3]) for i in range(0, 4, 2))
        seen.append(values)
        return [sum(values)]
    race_day_bootstrap_ci(["A", "A", "B", "B"], check_blocks, n_boot=100)
    assert any(v == [0, 1, 0, 1] for v in seen)
    print("staking_replay self-test: 500-plan floor, absent-plan refusal, shared net ROI/drawdown/streaks, "
          "bankroll conventions, ruin and reproducible paired race-day blocks PASS")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--ledger-json", help="Read-only export of selection_ledger; otherwise DATABASE_URL")
    parser.add_argument("--bankroll", type=float, help="REQUIRED initial bankroll in the units you choose")
    parser.add_argument("--min-bets", type=int, default=500)
    parser.add_argument("--bootstrap-n", type=int, default=DEFAULT_N_BOOT)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    if args.self_test:
        _self_test()
        return 0
    if args.bankroll is None:
        parser.error("--bankroll is required; ledger and risk controls use unresolved conventions")
    try:
        report = replay(load_rows(args.ledger_json), bankroll=args.bankroll,
                        min_bets=args.min_bets, n_boot=args.bootstrap_n, seed=args.seed)
    except Exception as exc:
        # Driver messages may contain connection details; only safe validation
        # text is emitted. A DB failure must never become an empty green report.
        reason = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: ledger read/replay failed"
        print(json.dumps({"status": "REFUSED", "reason": reason}))
        return 2
    print(json.dumps(_json_safe(report), indent=2, allow_nan=False))
    return 0 if report["status"] == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
