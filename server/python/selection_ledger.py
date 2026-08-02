#!/usr/bin/env python3
"""Selection ledger and weekly metrics (Workstream E), plus as-of guards (Workstream D).

The harness in `walk_forward_backtest.py` measures the ML ensemble. Nothing
measures the *wrapper* — `calibrate_and_score` -> `apply_safety_filters` ->
`evaluate_bet_candidate` -> `crowd_bet_decision`, the chain that actually
decides bets. This module builds the row that makes that measurable: one record
per published selection carrying both prices, the probability at each stage, the
staking rule applied, and the settled P&L.

Two rules shape it:

  * Both prices, always. `price_taken` is the racecard price the tip was
    published at; `price_close` is SP. Storing only one makes CLV
    uncomputable, and CLV is the earliest signal that an edge is real.
  * Metric code is imported, never re-implemented. `weekly_metrics` calls into
    `walk_forward_backtest`, so a change to how ROI or a bootstrap CI is
    computed lands in the backtest and the weekly report at the same time.

Nothing here writes to the database. Rows are built and returned; persistence is
the caller's decision, which keeps this module testable without a DB and keeps
schema changes additive and deliberate (guardrail 4).

Run `python selection_ledger.py` for the self-test.
"""

import os
import roi_stats
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(__file__))

from portfolio_risk import closing_line_value, ev_at_price, shadow_stake_plan

# Staking ladder as coded in run_tips_pipeline.compute_staking (:1007-1015).
STAKE_UNITS = {"2u": 2.0, "1u": 1.0, "0u": 0.0}


def _stride_flag(name: str) -> bool:
    """Default-off env flag, Variant B idiom (see run_tips_pipeline.py:591)."""
    return os.environ.get(name, "false").strip().lower() in ("true", "1", "yes")


def default_commission_rate() -> float:
    """Commission on net winnings, from env. Default 8% MBR.

    NSW/ACT tote deduction is 10% — set STRIDE_COMMISSION_RATE=0.10 when
    settling at tote prices in those states. EV and settlement must use the
    same rate so a gross-tuned edge is never booked (roi-roadmap task 01:
    +12.3% gross is ~+4.1% at 8% and ~+2.1% at 10%).
    """
    return float(os.environ.get("STRIDE_COMMISSION_RATE", "0.08"))


# ---------------------------------------------------------------------------
# Workstream D — as-of guards
# ---------------------------------------------------------------------------

class AsOfViolation(ValueError):
    """A feature was computed from data that did not exist at prediction time."""


def assert_as_of(feature_name: str, source_timestamp: Any,
                 prediction_timestamp: Any, strict: bool = True) -> bool:
    """Assert a feature's source predates the moment the prediction was made.

    Leakage rulebook rules 1, 2 and 7 all reduce to this check. `strict` makes
    the boundary exclusive, which is what rolling form windows need: a race
    result published at the same instant as the prediction is not usable.
    """
    src = _coerce_dt(source_timestamp)
    pred = _coerce_dt(prediction_timestamp)
    if src is None or pred is None:
        raise AsOfViolation(
            f"{feature_name}: cannot verify as-of — source={source_timestamp!r} "
            f"prediction={prediction_timestamp!r}")

    ok = src < pred if strict else src <= pred
    if not ok:
        raise AsOfViolation(
            f"{feature_name}: source {src.isoformat()} is not before prediction "
            f"{pred.isoformat()} — this feature would leak")
    return True


def _coerce_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def rolling_window_before(rows: Sequence[Dict[str, Any]], race_date: Any,
                          days: int, date_key: str = "race_date") -> List[Dict[str, Any]]:
    """Rows inside the `days` window ending STRICTLY before `race_date`.

    The strictness is the point: anchoring on "today" or including the race's
    own date is leakage rulebook rule 7, and it is the single easiest way to
    build a form feature that knows the result.
    """
    anchor = _coerce_dt(race_date)
    if anchor is None:
        raise AsOfViolation(f"unusable race_date: {race_date!r}")
    start = anchor - timedelta(days=days)

    out = []
    for r in rows:
        d = _coerce_dt(r.get(date_key))
        if d is not None and start <= d < anchor:
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Workstream E — the ledger row
# ---------------------------------------------------------------------------

def build_ledger_row(pick: Dict[str, Any], race: Dict[str, Any],
                     result: Optional[Dict[str, Any]] = None,
                     commission_rate: Optional[float] = None,
                     bankroll: float = 10000.0,
                     refused: bool = False) -> Dict[str, Any]:
    """One settled-or-pending record per published selection.

    `pick` follows the live contract (examples/sample_race.json): `win_pct`,
    `raw_model_pct` and `edge_pct` are 0-100, `odds` is decimal.
    `result` is {'won': bool, 'placed': bool, 'starting_price': float} once the
    race is settled, or None while pending. A truthy 'scratched' marks a
    non-runner; a truthy 'dh'/'dead_heat' marks a dead heat.

    `refused=True` records a NO_BET set the EV gate rejected (with the
    would-be price), so gate quality becomes measurable. `commission_rate`
    defaults to STRIDE_COMMISSION_RATE (0.08) — never silently gross.
    """
    if commission_rate is None:
        commission_rate = default_commission_rate()

    win_pct = pick.get("win_pct")
    raw_pct = pick.get("raw_model_pct")
    price_taken = pick.get("odds")
    staking = pick.get("staking")

    calibrated_prob = (win_pct / 100.0) if win_pct is not None else None
    raw_prob = (raw_pct / 100.0) if raw_pct is not None else None

    res = result or {}
    price_close = res.get("starting_price")
    won = res.get("won")
    scratched = bool(res.get("scratched"))
    dead_heat = bool(res.get("dh") or res.get("dead_heat"))

    units = STAKE_UNITS.get(str(staking), 0.0)
    stake = bankroll * units / 100.0

    row: Dict[str, Any] = {
        "race_date": race.get("race_date"),
        "track": race.get("track"),
        "race_number": race.get("race_number"),
        "horse": pick.get("horse"),
        "selection_origin": pick.get("selection_origin"),
        "should_bet": pick.get("should_bet"),
        "confidence": pick.get("confidence"),

        # probabilities at each stage of the chain
        "raw_model_prob": raw_prob,
        "calibrated_prob": calibrated_prob,
        "model_edge_pp": pick.get("edge_pct"),
        "fair_odds": pick.get("fair_odds"),

        # both prices — never one
        "price_taken": price_taken,
        "price_source": pick.get("price_source") or ("betfair" if pick.get("has_real_market_odds") else "none"),
        "refusal_reason": pick.get("refusal_reason"),
        "price_close": price_close,
        "has_real_market_odds": pick.get("has_real_market_odds"),

        # staking as actually applied by the live ladder
        "stake_rule": staking,
        "stake_units": units,
        "stake": round(stake, 2),

        "commission_rate": commission_rate,
        "settled": result is not None,
        "refused": bool(refused),
        "settled_at_sp_fallback": False,
        "sp": price_close,
    }

    row["ev_at_taken"] = (
        round(ev_at_price(calibrated_prob, price_taken, commission_rate), 6)
        if calibrated_prob is not None and price_taken else None)
    row["clv_pct"] = (round(closing_line_value(price_taken, price_close), 4)
                      if price_taken and price_close else None)

    # Single settlement contract (roi-roadmap 01): the taken price settles.
    # SP is used only when no taken price exists, and that is always marked
    # explicitly — the shadow tracker's silent `sp or tipped_odds` fallback is
    # the defect this replaces. Net P&L: win = (price-1)*(1-commission), loss = -1.
    settle_price = price_taken if price_taken else price_close

    if result is None:
        row["won"] = None
        row["pnl"] = None
        row["gross_pnl"] = None
    elif scratched:
        # Stake is returned; a scratching is never booked as a result.
        row["settled"] = False
        row["won"] = None
        row["pnl"] = 0.0
        row["gross_pnl"] = 0.0
        row["result_note"] = "scratched"
    elif won and dead_heat:
        # A dead heat does NOT pay (price-1) in full — the payout depends on
        # the number of tied runners, which this row does not carry. Refuse to
        # settle at full SP rather than book a flattered win.
        row["settled"] = False
        row["won"] = bool(won)
        row["pnl"] = None
        row["gross_pnl"] = None
        row["result_note"] = "dead_heat_unsettled"
    else:
        row["won"] = bool(won)
        row["settled_at_sp_fallback"] = bool(
            stake > 0 and not price_taken and price_close)
        if stake <= 0 or not settle_price:
            row["pnl"] = 0.0
            row["gross_pnl"] = 0.0
        elif won:
            row["gross_pnl"] = round(stake * (float(settle_price) - 1.0), 2)
            row["pnl"] = round(
                stake * (float(settle_price) - 1.0) * (1.0 - commission_rate), 2)
        else:
            row["pnl"] = round(-stake, 2)
            row["gross_pnl"] = round(-stake, 2)

    row["settled_pnl"] = row["pnl"]

    if _stride_flag("STRIDE_SHADOW_KELLY") and calibrated_prob is not None and price_taken:
        row["shadow_kelly"] = shadow_stake_plan(
            calibrated_prob, price_taken, bankroll=bankroll,
            commission_rate=commission_rate)

    return row


# Same floor as shadow_pl_tracker.MIN_BETS_REPORTABLE: a 30-bet week is
# never read as a result. Headline ROI/CLV metrics print INSUFFICIENT_SAMPLE
# below the floor, not numbers (roi-roadmap 01, step 5).
MIN_BETS_REPORTABLE = roi_stats.MIN_BETS_REPORTABLE
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


def kelly_readiness(rows: Sequence[Dict[str, Any]],
                    min_bets: int = 400) -> Dict[str, Any]:
    """Task 06 step 5: report-only gate for fractional Kelly activation.

    True only when (a) at least min_bets settled real bets exist, (b) the
    lower 95% CI bound of net ROI is above zero, and (c) mean CLV over the
    same window is positive. Activation itself remains a separate,
    human-approved change at 0.25x fractional Kelly; this function only
    reports.
    """
    settled = [r for r in rows
               if r.get("settled") and not r.get("refused")
               and r.get("price_taken")]
    n = len(settled)
    ci_lower = None
    mean_clv = None
    if settled:
        try:
            from roi_stats import (per_bet_returns, roi_ci, ci_lower_bound,
                                    commission_rate_from_env)
            won = [1.0 if r.get("won") else 0.0 for r in settled]
            odds = [float(r.get("price_taken") or 0.0) for r in settled]
            returns = per_bet_returns(won, odds, commission_rate_from_env())
            ci_lower = ci_lower_bound(roi_ci(returns).get("ci95"))
        except Exception:
            ci_lower = None
        clvs = [float(r["clv_pct"]) for r in settled
                if r.get("clv_pct") is not None]
        mean_clv = (sum(clvs) / len(clvs)) if clvs else None
    checks = {
        "settled_bets": n,
        "min_bets": min_bets,
        "enough_bets": n >= min_bets,
        "roi_ci_lower": ci_lower,
        "roi_ci_positive": bool(ci_lower is not None and ci_lower > 0),
        "mean_clv": mean_clv,
        "clv_positive": bool(mean_clv is not None and mean_clv > 0),
    }
    checks["ready"] = (checks["enough_bets"] and checks["roi_ci_positive"]
                       and checks["clv_positive"])
    return checks


def weekly_metrics(rows: Sequence[Dict[str, Any]], bootstrap_n: int = 2000,
                   bootstrap_seed: int = 42) -> Dict[str, Any]:
    """Summarise settled ledger rows using the harness's own metric functions.

    Imports from `walk_forward_backtest` rather than recomputing, so the weekly
    report and the backtest can never drift apart (guardrail 3).

    Emits n bets, mean CLV, % CLV>0, net ROI and gross ROI. Headline metrics
    are gated on the >=200-bet reportability floor: below it they are the
    string INSUFFICIENT_SAMPLE, not a flattering small-sample number.
    """
    import numpy as np
    from walk_forward_backtest import (bootstrap_roi_ci, compute_max_drawdown,
                                       compute_profit_factor)

    settled = [r for r in rows if r.get("settled") and (r.get("stake") or 0) > 0]
    if not settled:
        return {"n_bets": 0, "reportable": False,
                "reason": "no settled rows with a stake"}

    pnl = np.array([float(r["pnl"]) for r in settled], dtype=float)
    gross = np.array([
        float(r["gross_pnl"]) if r.get("gross_pnl") is not None else float(r["pnl"])
        for r in settled], dtype=float)
    staked = float(sum(float(r["stake"]) for r in settled))
    wins = sum(1 for r in settled if r.get("won"))

    dd, dd_pct = compute_max_drawdown(pnl)
    # The bootstrap resamples a flat-stake ROI, so give it a flat equivalent.
    flat = pnl / (np.array([float(r["stake"]) for r in settled]) / 100.0)
    ci = bootstrap_roi_ci(flat, stake=100.0, n_boot=bootstrap_n, seed=bootstrap_seed)

    clvs = [r["clv_pct"] for r in settled if r.get("clv_pct") is not None]
    n = len(settled)

    def _gated(value: Any) -> Any:
        """Headline numbers only exist at or above the reportability floor."""
        return value if n >= MIN_BETS_REPORTABLE else INSUFFICIENT_SAMPLE

    roi_net = round(float(pnl.sum()) / staked * 100.0, 2) if staked else 0.0
    roi_gross = round(float(gross.sum()) / staked * 100.0, 2) if staked else 0.0
    mean_clv = round(float(np.mean(clvs)), 4) if clvs else None
    pct_clv_pos = (round(sum(1 for c in clvs if c > 0) / len(clvs) * 100.0, 2)
                   if clvs else None)

    settled_priced = [r for r in rows if r.get("settled") and not r.get("refused")
                      and r.get("price_taken") and r.get("sp")]
    if settled_priced:
        gains = [float(r["price_taken"]) / float(r["sp"]) - 1.0
                 for r in settled_priced if float(r["sp"] or 0) > 1.0]
        if gains:
            print(f"  [EXECUTION] avg execution_gain_pct vs SP: "
                  f"{sum(gains) / len(gains) * 100:.2f}% over {len(gains)} bets")
    metrics_kelly = kelly_readiness(rows)
    print(f"  [KELLY] readiness: {metrics_kelly['ready']} "
          f"(bets {metrics_kelly['settled_bets']}/{metrics_kelly['min_bets']}, "
          f"roi_ci_lower {metrics_kelly['roi_ci_lower']}, "
          f"mean_clv {metrics_kelly['mean_clv']})")
    return {
        "n_bets": n,
        "reportable": n >= roi_stats.MIN_BETS_REPORTABLE,
        "strike_rate": round(wins / n, 4),
        "total_staked": round(staked, 2),
        "profit": round(float(pnl.sum()), 2),
        "profit_gross": round(float(gross.sum()), 2),
        # Net of commission — the only ROI staking decisions may ever use.
        "roi_pct": _gated(roi_net),
        "roi_net_pct": _gated(roi_net),
        "roi_gross_pct": _gated(roi_gross),
        "roi_ci95_bootstrap": ci,
        "max_drawdown": round(dd, 2),
        "max_drawdown_pct": round(dd_pct, 2),
        "profit_factor": (round(compute_profit_factor(pnl), 4)
                          if compute_profit_factor(pnl) is not None else None),
        "avg_price_taken": round(
            float(np.mean([float(r["price_taken"]) for r in settled
                           if r.get("price_taken")])), 2),
        "mean_clv_pct": _gated(mean_clv),
        "pct_clv_positive": _gated(pct_clv_pos),
        "n_with_clv": len(clvs),
    }


# ---------------------------------------------------------------------------
# Persistence — additive table, see migrations/selection_ledger.sql
# ---------------------------------------------------------------------------

LEDGER_COLUMNS = (
    "race_date", "track", "race_number", "horse_name",
    "selection_origin", "should_bet", "confidence",
    "raw_model_prob", "calibrated_prob", "model_edge_pp", "ev_at_taken",
    "fair_odds", "price_taken", "price_close", "clv_pct",
    "has_real_market_odds", "stake_rule", "stake_units", "stake",
    "commission_rate", "settled", "won", "pnl", "shadow_kelly_json",
    "refused", "settled_at_sp_fallback", "sp", "settled_pnl",
    "price_source", "refusal_reason",
)

# The upsert names uq_selection_ledger_race_horse, which the migration creates
# in the same file as the table. prediction_audit sat near-empty for months
# because its ON CONFLICT named an arbiter that did not exist and every insert
# failed silently; _ensure_table below makes that impossible to repeat quietly.
LEDGER_UPSERT_SQL = (
    "INSERT INTO selection_ledger ({cols}) VALUES ({placeholders}) "
    "ON CONFLICT (track, race_number, race_date, horse_name) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in LEDGER_COLUMNS
                if c not in ("track", "race_number", "race_date", "horse_name"))
    + ", updated_at = NOW()"
).format(cols=", ".join(LEDGER_COLUMNS),
         placeholders=", ".join(["%s"] * len(LEDGER_COLUMNS)))


def _row_to_tuple(row: Dict[str, Any]) -> tuple:
    """Project a ledger dict onto LEDGER_COLUMNS, JSON-encoding the shadow plan."""
    import json
    out = []
    for col in LEDGER_COLUMNS:
        if col == "horse_name":
            out.append(row.get("horse_name", row.get("horse")))
        elif col == "shadow_kelly_json":
            plan = row.get("shadow_kelly")
            out.append(json.dumps(plan) if plan is not None else None)
        elif col == "sp":
            out.append(row.get("sp", row.get("price_close")))
        elif col == "settled_pnl":
            out.append(row.get("settled_pnl", row.get("pnl")))
        else:
            out.append(row.get(col))
    return tuple(out)


def persist_rows(conn, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Upsert ledger rows. Returns a positive count — never a silent success.

    Writing is opt-in: with STRIDE_LEDGER_WRITE unset this is a no-op that
    reports why, so the module can be imported and exercised on a live box
    without touching the database.
    """
    if not _stride_flag("STRIDE_LEDGER_WRITE"):
        return {"written": 0, "skipped": len(rows), "reason": "STRIDE_LEDGER_WRITE is off"}
    if not rows:
        return {"written": 0, "skipped": 0, "reason": "no rows"}

    payload = [_row_to_tuple(r) for r in rows]
    cur = conn.cursor()
    try:
        cur.executemany(LEDGER_UPSERT_SQL, payload)
        written = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    # A positive assertion, not the absence of an exception: SYSTEM_MAP §7b.2
    # records that this repo has twice been bitten by failures that presented
    # as slightly worse output rather than as an error.
    print(f"  [LEDGER] upserted {written} of {len(rows)} row(s)")
    if written <= 0:
        raise RuntimeError(
            f"selection_ledger upsert reported {written} rows for {len(rows)} "
            "inputs — the arbiter index is probably missing; apply "
            "migrations/selection_ledger.sql")
    return {"written": int(written), "skipped": 0, "reason": None}


# Pending = written at tip time and not yet touched by a settle pass. Rows
# the settle pass has seen always carry sp (even dead-heated/scratched ones),
# so this predicate never re-picks them; rows whose result simply is not
# collected yet stay pending for the next run.
_SETTLE_PENDING_SQL = """
    SELECT race_date, track, race_number, horse_name, selection_origin,
           should_bet, confidence, raw_model_prob, calibrated_prob,
           model_edge_pp, fair_odds, price_taken, has_real_market_odds,
           stake_rule, stake, commission_rate, refused
    FROM selection_ledger
    WHERE race_date = %s AND settled = FALSE AND pnl IS NULL AND sp IS NULL
"""


def settle_pending_rows(conn, race_date, results_map: Optional[Dict] = None) -> Dict[str, Any]:
    """Settle pending ledger rows for race_date against collected results.

    The missing half of the measurement spine (roi-roadmap 01, acceptance #1):
    rows are written pending at tip time; this pass fills sp / clv_pct /
    settled_pnl once results exist. It uses the SAME results source as the
    shadow tracker (shadow_pl_tracker.fetch_results_map) and the SAME
    settlement contract (build_ledger_row) — no third path.

    Settlement uses the commission_rate recorded on the row at tip time, never
    a re-read of today's env: historical truth is not rewritten.

    Idempotent: only still-pending rows are touched, and the rebuild is a pure
    function of the stored row plus the result, so re-running rewrites
    identical values. Flag-gated exactly like persist_rows.
    """
    if not _stride_flag("STRIDE_LEDGER_WRITE"):
        return {"settled": 0, "unmatched": 0, "pending": 0,
                "reason": "STRIDE_LEDGER_WRITE is off"}

    # Fail LOUD, not silent, when the net-settlement migration is missing.
    # Callers wrap this pass in non-fatal try/except so it can never break
    # results collection — but without these columns every settle run would be
    # a quiet no-op and tip-time rows would never get SP/CLV: the same
    # silent-non-settlement class as the bug this pass exists to fix.
    probe = conn.cursor()
    try:
        probe.execute(
            "SELECT sp, settled_pnl, refused, settled_at_sp_fallback "
            "FROM selection_ledger LIMIT 0")
    except Exception as _schema_err:
        try:
            conn.rollback()
        except Exception:
            pass
        msg = ("selection_ledger is missing the net-settlement columns — "
               "apply migrations/selection_ledger_net_settlement.sql")
        print(f"  [LEDGER] *** WARNING: {msg} ({_schema_err}). Settlement "
              "skipped; tip-time rows will NOT get SP/CLV until this is fixed.",
              file=sys.stderr)
        return {"settled": 0, "unmatched": 0, "pending": 0, "reason": msg}
    finally:
        try:
            probe.close()
        except Exception:
            pass

    from shadow_pl_tracker import _normalize_name
    if results_map is None:
        from shadow_pl_tracker import fetch_results_map
        results_map = fetch_results_map(conn, race_date)

    cur = conn.cursor()
    try:
        cur.execute(_SETTLE_PENDING_SQL, (race_date,))
        cols = [d[0] for d in cur.description]
        pending = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        cur.close()

    rebuilt, unmatched = [], 0
    for row in pending:
        key = (_normalize_name(row["track"]), row["race_number"],
               _normalize_name(row["horse_name"]))
        res = results_map.get(key)
        if not res:
            unmatched += 1
            continue
        pos = res.get("position")
        result = {
            "won": bool(res.get("won")) or pos == 1,
            "placed": bool(res.get("placed")) or (isinstance(pos, int) and pos <= 3),
            "starting_price": res.get("sp"),
            "scratched": pos is None,
            "dh": isinstance(pos, str) and "dh" in pos.lower(),
        }
        cal, raw = row.get("calibrated_prob"), row.get("raw_model_prob")
        pick = {
            "horse": row.get("horse_name"),
            "win_pct": cal * 100.0 if cal is not None else None,
            "raw_model_pct": raw * 100.0 if raw is not None else None,
            "edge_pct": row.get("model_edge_pp"),
            "odds": row.get("price_taken"),
            "fair_odds": row.get("fair_odds"),
            "staking": row.get("stake_rule"),
            "should_bet": row.get("should_bet"),
            "confidence": row.get("confidence"),
            "has_real_market_odds": row.get("has_real_market_odds"),
            "selection_origin": row.get("selection_origin"),
        }
        race = {"race_date": row.get("race_date"), "track": row.get("track"),
                "race_number": row.get("race_number")}
        units = STAKE_UNITS.get(str(row.get("stake_rule")), 0.0)
        bankroll = (float(row["stake"]) * 100.0 / units) if units else 10000.0
        commission = (float(row["commission_rate"])
                      if row.get("commission_rate") is not None else None)
        rebuilt.append(build_ledger_row(
            pick, race, result=result, commission_rate=commission,
            bankroll=bankroll, refused=bool(row.get("refused"))))

    out = {"settled": 0, "unmatched": unmatched, "pending": len(pending),
           "reason": None}
    if rebuilt:
        out["settled"] = persist_rows(conn, rebuilt)["written"]
    return out


def _self_test():
    print("selection_ledger self-test")

    # --- Workstream D: as-of guards ---
    assert assert_as_of("form", "2026-03-01", "2026-03-08") is True
    for bad in (("2026-03-08", "2026-03-08"), ("2026-03-09", "2026-03-08")):
        try:
            assert_as_of("form", *bad)
            raise AssertionError(f"expected AsOfViolation for {bad}")
        except AsOfViolation:
            pass
    assert assert_as_of("form", "2026-03-08", "2026-03-08", strict=False) is True
    try:
        assert_as_of("form", None, "2026-03-08")
        raise AssertionError("expected AsOfViolation on unverifiable timestamp")
    except AsOfViolation:
        pass
    print("  assert_as_of: same-instant rejected when strict, unverifiable rejected outright")

    hist = [{"race_date": "2026-02-01"}, {"race_date": "2026-02-20"},
            {"race_date": "2026-03-01"}, {"race_date": "2026-03-05"}]
    w = rolling_window_before(hist, "2026-03-01", days=30)
    dates = [r["race_date"] for r in w]
    assert dates == ["2026-02-01", "2026-02-20"], dates
    assert "2026-03-01" not in dates, "the race's own date must be excluded"
    assert "2026-03-05" not in dates, "future rows must be excluded"
    print(f"  rolling_window_before: {dates} — own date and future both excluded")

    # --- Workstream E: ledger row ---
    race = {"race_date": "2026-03-08", "track": "Flemington", "race_number": 5}
    pick = {"horse": "Alpha", "win_pct": 25.0, "raw_model_pct": 30.0,
            "edge_pct": 5.0, "odds": 5.0, "fair_odds": 4.0, "staking": "2u",
            "confidence": "high", "should_bet": True, "has_real_market_odds": True}

    prev_c = os.environ.pop("STRIDE_COMMISSION_RATE", None)
    try:
        pending = build_ledger_row(pick, race)
        assert pending["settled"] is False and pending["pnl"] is None and pending["won"] is None
        assert pending["clv_pct"] is None, "no close yet ⇒ no CLV"
        # Default commission is 8% net: 0.25*(5-1)*0.92 - 0.75 = 0.17.
        assert pending["commission_rate"] == 0.08, pending["commission_rate"]
        assert abs(pending["ev_at_taken"] - 0.17) < 1e-9, pending["ev_at_taken"]
        assert pending["stake"] == 200.0, pending["stake"]

        winner = build_ledger_row(pick, race, result={"won": True, "starting_price": 4.0})
        assert winner["pnl"] == 736.0 and winner["gross_pnl"] == 800.0, winner["pnl"]
        assert winner["settled_pnl"] == winner["pnl"]
        assert winner["settled_at_sp_fallback"] is False
        assert abs(winner["clv_pct"] - 25.0) < 1e-9, winner["clv_pct"]
        loser = build_ledger_row(pick, race, result={"won": False, "starting_price": 6.0})
        assert loser["pnl"] == -200.0 and loser["gross_pnl"] == -200.0 and loser["clv_pct"] < 0
        gross = build_ledger_row(pick, race, result={"won": True, "starting_price": 4.0},
                                 commission_rate=0.0)
        assert gross["pnl"] == 800.0, "explicit 0.0 still reproduces gross"
        nsw = build_ledger_row(pick, race, result={"won": True, "starting_price": 4.0},
                               commission_rate=0.10)
        assert abs(nsw["pnl"] - 720.0) < 1e-9, "NSW/ACT 10% MBR case"
        no_bet = build_ledger_row({**pick, "staking": "0u"}, race,
                                  result={"won": True, "starting_price": 4.0})
        assert no_bet["stake"] == 0.0 and no_bet["pnl"] == 0.0, "0u must never book P&L"
        assert no_bet["settled_at_sp_fallback"] is False

        # SP fallback is explicit; CLV is NULL without a real taken price.
        fb = build_ledger_row({**pick, "odds": None}, race,
                              result={"won": True, "starting_price": 4.0})
        assert fb["settled_at_sp_fallback"] is True and fb["pnl"] == 552.0  # 200*(4-1)*0.92
        assert fb["clv_pct"] is None, "clv_pct must be NULL when price_taken is NULL"

        # Dead heats and scratchings never settle at full SP.
        dh = build_ledger_row(pick, race, result={"won": True, "starting_price": 4.0, "dh": True})
        assert dh["settled"] is False and dh["pnl"] is None and dh["won"] is True
        scr = build_ledger_row(pick, race, result={"won": None, "starting_price": 4.0,
                                                   "scratched": True})
        assert scr["settled"] is False and scr["pnl"] == 0.0 and scr["won"] is None
        assert scr["settled_at_sp_fallback"] is False

        refused = build_ledger_row({**pick, "staking": "0u", "should_bet": False},
                                   race, refused=True)
        assert refused["refused"] is True and refused["price_taken"] == 5.0

        os.environ["STRIDE_COMMISSION_RATE"] = "0.10"
        env_c = build_ledger_row(pick, race)
        assert env_c["commission_rate"] == 0.10, "env must drive the default rate"
    finally:
        os.environ.pop("STRIDE_COMMISSION_RATE", None)
        if prev_c is not None:
            os.environ["STRIDE_COMMISSION_RATE"] = prev_c
    print(f"  ledger: win +{winner['pnl']:.0f} net / loss {loser['pnl']:.0f} / "
          f"gross +{gross['pnl']:.0f} / SP fallback explicit / dh+scratched never full SP; "
          f"CLV +{winner['clv_pct']:.0f}%")

    prev = os.environ.pop("STRIDE_SHADOW_KELLY", None)
    try:
        assert "shadow_kelly" not in build_ledger_row(pick, race)
        os.environ["STRIDE_SHADOW_KELLY"] = "true"
        with_k = build_ledger_row(pick, race)
        assert with_k["shadow_kelly"]["applied"] is False
        assert with_k["stake"] == 200.0, "shadow Kelly must not change the real stake"
    finally:
        if prev is None:
            os.environ.pop("STRIDE_SHADOW_KELLY", None)
        else:
            os.environ["STRIDE_SHADOW_KELLY"] = prev
    print("  shadow Kelly: absent by default; when on it is logged and the real stake is unchanged")

    # --- weekly metrics reuse the harness functions ---
    assert weekly_metrics([])["n_bets"] == 0
    rows = []
    for i in range(120):
        won = (i % 5 == 0)
        rows.append(build_ledger_row(
            {**pick, "horse": f"H{i}"}, race,
            result={"won": won, "starting_price": 4.5},
            commission_rate=0.0))
    wm = weekly_metrics(rows, bootstrap_n=300)
    assert wm["n_bets"] == 120
    assert wm["reportable"] is False, "120 bets is below the 200 reportability floor"
    assert abs(wm["strike_rate"] - 0.2) < 1e-9, wm["strike_rate"]
    assert wm["roi_ci95_bootstrap"]["n_boot"] == 300
    assert wm["profit_factor"] is not None and wm["max_drawdown"] > 0
    # Below the floor the headline metrics are INSUFFICIENT_SAMPLE, not numbers.
    for key in ("roi_pct", "roi_net_pct", "roi_gross_pct", "mean_clv_pct",
                "pct_clv_positive"):
        assert wm[key] == "INSUFFICIENT_SAMPLE", (key, wm[key])
    assert wm["n_with_clv"] == 120

    big_rows = list(rows)
    for i in range(120, 220):
        big_rows.append(build_ledger_row(
            {**pick, "horse": f"H{i}"}, race,
            result={"won": (i % 5 == 0), "starting_price": 4.5},
            commission_rate=0.0))
    wm2 = weekly_metrics(big_rows, bootstrap_n=300)
    assert wm2["reportable"] is True and wm2["n_bets"] == 220
    assert isinstance(wm2["mean_clv_pct"], float) and isinstance(wm2["pct_clv_positive"], float)
    assert wm2["roi_net_pct"] == wm2["roi_gross_pct"] == wm2["roi_pct"], \
        "at 0% commission net and gross coincide"
    assert wm2["pct_clv_positive"] == 100.0, "5.0 taken vs 4.5 close is always positive CLV"
    print(f"  weekly_metrics: 120 bets -> INSUFFICIENT_SAMPLE; "
          f"220 bets -> ROI {wm2['roi_net_pct']:.1f}% net / {wm2['roi_gross_pct']:.1f}% gross, "
          f"CLV {wm2['mean_clv_pct']:+.2f}% ({wm2['pct_clv_positive']:.0f}% >0)")

    # --- persistence: SQL shape and the write flag, without a database ---
    import json as _json

    assert LEDGER_UPSERT_SQL.count("%s") == len(LEDGER_COLUMNS)
    assert "ON CONFLICT (track, race_number, race_date, horse_name)" in LEDGER_UPSERT_SQL
    for key in ("track", "race_number", "race_date", "horse_name"):
        assert f"{key} = EXCLUDED.{key}" not in LEDGER_UPSERT_SQL, \
            f"{key} is part of the arbiter and must not be in the UPDATE set"
    assert "updated_at = NOW()" in LEDGER_UPSERT_SQL

    tup = _row_to_tuple(winner)
    assert len(tup) == len(LEDGER_COLUMNS)
    assert tup[LEDGER_COLUMNS.index("horse_name")] == "Alpha", "horse -> horse_name mapping"
    assert tup[LEDGER_COLUMNS.index("pnl")] == 736.0
    assert tup[LEDGER_COLUMNS.index("settled_pnl")] == 736.0
    assert tup[LEDGER_COLUMNS.index("sp")] == 4.0
    assert tup[LEDGER_COLUMNS.index("refused")] is False
    assert tup[LEDGER_COLUMNS.index("settled_at_sp_fallback")] is False
    assert tup[LEDGER_COLUMNS.index("shadow_kelly_json")] is None

    os.environ["STRIDE_SHADOW_KELLY"] = "true"
    try:
        with_k = build_ledger_row(pick, race, result={"won": True, "starting_price": 4.0})
        enc = _row_to_tuple(with_k)[LEDGER_COLUMNS.index("shadow_kelly_json")]
        assert _json.loads(enc)["applied"] is False, "shadow plan must serialise as not-applied"
    finally:
        os.environ.pop("STRIDE_SHADOW_KELLY", None)
    print(f"  upsert SQL: {len(LEDGER_COLUMNS)} columns, arbiter excluded from the UPDATE set, "
          f"shadow plan JSON-encoded")

    class _FakeCursor:
        def __init__(self, outer): self.outer, self.rowcount = outer, 0
        def executemany(self, sql, payload):
            self.outer.calls.append((sql, payload)); self.rowcount = len(payload)
        def close(self): pass

    class _FakeConn:
        def __init__(self): self.calls, self.commits, self.rollbacks = [], 0, 0
        def cursor(self): return _FakeCursor(self)
        def commit(self): self.commits += 1
        def rollback(self): self.rollbacks += 1

    prev_w = os.environ.pop("STRIDE_LEDGER_WRITE", None)
    try:
        conn = _FakeConn()
        res = persist_rows(conn, [winner, loser])
        assert res["written"] == 0 and res["skipped"] == 2, res
        assert not conn.calls, "flag off must not touch the database"

        os.environ["STRIDE_LEDGER_WRITE"] = "true"
        conn2 = _FakeConn()
        res2 = persist_rows(conn2, [winner, loser])
        assert res2["written"] == 2 and conn2.commits == 1, res2
        assert len(conn2.calls[0][1]) == 2
        assert persist_rows(conn2, [])["written"] == 0

        class _ZeroCursor(_FakeCursor):
            def executemany(self, sql, payload): self.rowcount = 0

        class _ZeroConn(_FakeConn):
            def cursor(self): return _ZeroCursor(self)

        try:
            persist_rows(_ZeroConn(), [winner])
            raise AssertionError("a zero-row upsert must raise, not pass quietly")
        except RuntimeError as err:
            assert "arbiter index" in str(err), err
    finally:
        os.environ.pop("STRIDE_LEDGER_WRITE", None)
        if prev_w is not None:
            os.environ["STRIDE_LEDGER_WRITE"] = prev_w
    print("  persist_rows: no-op when off, commits when on, RAISES on a zero-row upsert")

    print("All tests completed successfully.")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "settle":
        # Settle pending rows against collected results, e.g.:
        #   STRIDE_LEDGER_WRITE=true python selection_ledger.py settle 2026-03-28
        from shadow_pl_tracker import _get_connection
        conn = _get_connection()
        try:
            for _date in sys.argv[2:]:
                _out = settle_pending_rows(conn, _date)
                print(f"  [LEDGER] {_date}: settled={_out['settled']} "
                      f"unmatched={_out['unmatched']} pending={_out['pending']}"
                      + (f" ({_out['reason']})" if _out.get("reason") else ""))
        finally:
            conn.close()
    else:
        _self_test()
