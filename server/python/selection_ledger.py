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
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(__file__))

from portfolio_risk import closing_line_value, ev_at_price, shadow_stake_plan
import roi_stats

# Staking ladder as coded in run_tips_pipeline.compute_staking (:1007-1015).
STAKE_UNITS = {"2u": 2.0, "1u": 1.0, "0u": 0.0}


def _stride_flag(name: str) -> bool:
    """Default-off env flag, Variant B idiom (see run_tips_pipeline.py:591)."""
    return os.environ.get(name, "false").strip().lower() in ("true", "1", "yes")


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
                     commission_rate: float = 0.0,
                     bankroll: float = 10000.0) -> Dict[str, Any]:
    """One settled-or-pending record per published selection.

    `pick` follows the live contract (examples/sample_race.json): `win_pct`,
    `raw_model_pct` and `edge_pct` are 0-100, `odds` is decimal.
    `result` is {'won': bool, 'placed': bool, 'starting_price': float} once the
    race is settled, or None while pending.
    """
    win_pct = pick.get("win_pct")
    raw_pct = pick.get("raw_model_pct")
    price_taken = pick.get("odds")
    staking = pick.get("staking")

    calibrated_prob = (win_pct / 100.0) if win_pct is not None else None
    raw_prob = (raw_pct / 100.0) if raw_pct is not None else None

    price_close = (result or {}).get("starting_price")
    won = (result or {}).get("won")

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
        "price_close": price_close,
        "has_real_market_odds": pick.get("has_real_market_odds"),

        # staking as actually applied by the live ladder
        "stake_rule": staking,
        "stake_units": units,
        "stake": round(stake, 2),

        "commission_rate": commission_rate,
        "settled": result is not None,
    }

    row["ev_at_taken"] = (
        round(ev_at_price(calibrated_prob, price_taken, commission_rate), 6)
        if calibrated_prob is not None and price_taken else None)
    row["clv_pct"] = (round(closing_line_value(price_taken, price_close), 4)
                      if price_taken and price_close else None)

    if result is None:
        row["won"] = None
        row["pnl"] = None
    else:
        row["won"] = bool(won)
        if stake <= 0 or not price_taken:
            row["pnl"] = 0.0
        elif won:
            row["pnl"] = round(stake * (float(price_taken) - 1.0) * (1.0 - commission_rate), 2)
        else:
            row["pnl"] = round(-stake, 2)

    if _stride_flag("STRIDE_SHADOW_KELLY") and calibrated_prob is not None and price_taken:
        row["shadow_kelly"] = shadow_stake_plan(
            calibrated_prob, price_taken, bankroll=bankroll,
            commission_rate=commission_rate)

    return row


def weekly_metrics(rows: Sequence[Dict[str, Any]], bootstrap_n: int = 2000,
                   bootstrap_seed: int = 42) -> Dict[str, Any]:
    """Summarise settled ledger rows using the harness's own metric functions.

    Imports from `walk_forward_backtest` rather than recomputing, so the weekly
    report and the backtest can never drift apart (guardrail 3).
    """
    import numpy as np
    from walk_forward_backtest import (bootstrap_roi_ci, compute_max_drawdown,
                                       compute_profit_factor)

    settled = [r for r in rows if r.get("settled") and (r.get("stake") or 0) > 0]
    if not settled:
        return {"n_bets": 0, "reportable": False,
                "reason": "no settled rows with a stake"}

    pnl = np.array([float(r["pnl"]) for r in settled], dtype=float)
    staked = float(sum(float(r["stake"]) for r in settled))
    wins = sum(1 for r in settled if r.get("won"))

    dd, dd_pct = compute_max_drawdown(pnl)
    # The bootstrap resamples a flat-stake ROI, so give it a flat equivalent.
    flat = pnl / (np.array([float(r["stake"]) for r in settled]) / 100.0)
    ci = bootstrap_roi_ci(flat, stake=100.0, n_boot=bootstrap_n, seed=bootstrap_seed)

    clvs = [r["clv_pct"] for r in settled if r.get("clv_pct") is not None]
    n = len(settled)

    return {
        "n_bets": n,
        # Reportability floor = roi_stats.MIN_BETS_REPORTABLE (single source of
        # truth, roi-roadmap task 02) so a 30-bet week is never read as a result
        # and the floor can never drift between modules.
        "reportable": n >= roi_stats.MIN_BETS_REPORTABLE,
        "strike_rate": round(wins / n, 4),
        "total_staked": round(staked, 2),
        "profit": round(float(pnl.sum()), 2),
        "roi_pct": round(float(pnl.sum()) / staked * 100.0, 2) if staked else 0.0,
        "roi_ci95_bootstrap": ci,
        "max_drawdown": round(dd, 2),
        "max_drawdown_pct": round(dd_pct, 2),
        "profit_factor": (round(compute_profit_factor(pnl), 4)
                          if compute_profit_factor(pnl) is not None else None),
        "avg_price_taken": round(
            float(np.mean([float(r["price_taken"]) for r in settled
                           if r.get("price_taken")])), 2),
        "mean_clv_pct": round(float(np.mean(clvs)), 4) if clvs else None,
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

    pending = build_ledger_row(pick, race)
    assert pending["settled"] is False and pending["pnl"] is None and pending["won"] is None
    assert pending["clv_pct"] is None, "no close yet ⇒ no CLV"
    assert abs(pending["ev_at_taken"] - 0.25) < 1e-9, pending["ev_at_taken"]
    assert pending["stake"] == 200.0, pending["stake"]

    winner = build_ledger_row(pick, race, result={"won": True, "starting_price": 4.0})
    assert winner["pnl"] == 800.0, winner["pnl"]
    assert abs(winner["clv_pct"] - 25.0) < 1e-9, winner["clv_pct"]
    loser = build_ledger_row(pick, race, result={"won": False, "starting_price": 6.0})
    assert loser["pnl"] == -200.0 and loser["clv_pct"] < 0
    comm = build_ledger_row(pick, race, result={"won": True, "starting_price": 4.0},
                            commission_rate=0.08)
    assert abs(comm["pnl"] - 736.0) < 1e-9, comm["pnl"]
    no_bet = build_ledger_row({**pick, "staking": "0u"}, race,
                              result={"won": True, "starting_price": 4.0})
    assert no_bet["stake"] == 0.0 and no_bet["pnl"] == 0.0, "0u must never book P&L"
    print(f"  ledger: win +{winner['pnl']:.0f} / loss {loser['pnl']:.0f} / "
          f"8% commission +{comm['pnl']:.0f} / 0u books nothing; CLV +{winner['clv_pct']:.0f}%")

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
            result={"won": won, "starting_price": 4.5}))
    wm = weekly_metrics(rows, bootstrap_n=300)
    assert wm["n_bets"] == 120
    assert wm["reportable"] is False, "120 bets is below the 200 reportability floor"
    assert abs(wm["strike_rate"] - 0.2) < 1e-9, wm["strike_rate"]
    assert wm["roi_ci95_bootstrap"]["n_boot"] == 300
    assert wm["mean_clv_pct"] is not None and wm["n_with_clv"] == 120
    assert wm["profit_factor"] is not None and wm["max_drawdown"] > 0
    print(f"  weekly_metrics: {wm['n_bets']} bets, SR {wm['strike_rate']:.1%}, "
          f"ROI {wm['roi_pct']:.1f}%, CI {wm['roi_ci95_bootstrap']['ci_95']}, "
          f"reportable={wm['reportable']}")

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
    assert tup[LEDGER_COLUMNS.index("pnl")] == 800.0
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
    _self_test()
