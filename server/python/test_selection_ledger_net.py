#!/usr/bin/env python3
"""DB-free unit tests for roi-roadmap task 01: net-of-commission settlement,
explicit SP fallback, dead-heat/scratched handling, refused rows, and the
weekly_metrics reportability floor.

Run: /tmp/stride-venv/bin/python -m pytest server/python/test_selection_ledger_net.py -x -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from selection_ledger import (LEDGER_COLUMNS, INSUFFICIENT_SAMPLE,
                              MIN_BETS_REPORTABLE, build_ledger_row,
                              default_commission_rate, persist_rows,
                              weekly_metrics, _row_to_tuple)
from shadow_pl_tracker import settle_shadow_row

RACE = {"race_date": "2026-03-08", "track": "Flemington", "race_number": 5}
PICK = {"horse": "Alpha", "win_pct": 25.0, "raw_model_pct": 30.0,
        "edge_pct": 5.0, "odds": 5.0, "fair_odds": 4.0, "staking": "2u",
        "confidence": "high", "should_bet": True, "has_real_market_odds": True}


@pytest.fixture(autouse=True)
def _clean_env():
    saved = {k: os.environ.get(k)
             for k in ("STRIDE_COMMISSION_RATE", "STRIDE_LEDGER_WRITE")}
    for k in saved:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ---------------------------------------------------------------------------
# Net settlement math
# ---------------------------------------------------------------------------

def test_commission_default_is_8pct_from_env():
    assert default_commission_rate() == 0.08
    os.environ["STRIDE_COMMISSION_RATE"] = "0.10"  # NSW/ACT tote MBR
    assert default_commission_rate() == 0.10


def test_win_settles_net_of_commission():
    row = build_ledger_row(PICK, RACE, result={"won": True, "starting_price": 4.0})
    # stake 200 on 5.0: gross 800, net 800 * 0.92 = 736
    assert row["gross_pnl"] == 800.0
    assert row["pnl"] == pytest.approx(736.0)
    assert row["settled_pnl"] == row["pnl"]
    assert row["commission_rate"] == 0.08


def test_win_settles_net_at_10pct_nsw_act():
    row = build_ledger_row(PICK, RACE, result={"won": True, "starting_price": 4.0},
                           commission_rate=0.10)
    assert row["pnl"] == pytest.approx(720.0)  # 800 * 0.90


def test_loss_is_minus_one_unit_regardless_of_commission():
    row = build_ledger_row(PICK, RACE, result={"won": False, "starting_price": 6.0})
    assert row["pnl"] == -200.0
    assert row["gross_pnl"] == -200.0


def test_ev_at_taken_is_net():
    row = build_ledger_row(PICK, RACE)
    # 0.25 * (5-1) * 0.92 - 0.75 = 0.17
    assert row["ev_at_taken"] == pytest.approx(0.17)


# ---------------------------------------------------------------------------
# Single settlement contract: taken price settles, SP fallback is explicit
# ---------------------------------------------------------------------------

def test_taken_price_settles_not_sp():
    row = build_ledger_row(PICK, RACE, result={"won": True, "starting_price": 3.0})
    assert row["pnl"] == pytest.approx(736.0)  # settled at 5.0, not SP 3.0
    assert row["settled_at_sp_fallback"] is False
    assert row["sp"] == 3.0


def test_sp_fallback_is_explicit():
    pick = {**PICK, "odds": None}
    row = build_ledger_row(pick, RACE, result={"won": True, "starting_price": 4.0})
    assert row["settled_at_sp_fallback"] is True
    assert row["pnl"] == pytest.approx(200 * 3.0 * 0.92)  # settled at SP 4.0


def test_clv_null_when_price_taken_null():
    pick = {**PICK, "odds": None}
    row = build_ledger_row(pick, RACE, result={"won": True, "starting_price": 4.0})
    assert row["clv_pct"] is None, "CLV on SP==price-taken is definitionally zero"


# ---------------------------------------------------------------------------
# Dead heats and scratchings never settle at full SP
# ---------------------------------------------------------------------------

def test_dead_heat_winner_never_settles_at_full_price():
    row = build_ledger_row(PICK, RACE,
                           result={"won": True, "starting_price": 4.0, "dh": True})
    assert row["settled"] is False
    assert row["pnl"] is None
    assert row["won"] is True
    assert row["result_note"] == "dead_heat_unsettled"


def test_clean_winner_still_settles():
    row = build_ledger_row(PICK, RACE, result={"won": True, "starting_price": 4.0})
    assert row["settled"] is True and row["pnl"] == pytest.approx(736.0)


def test_dead_heat_marker_irrelevant_for_loser():
    row = build_ledger_row(PICK, RACE,
                           result={"won": False, "starting_price": 4.0, "dh": True})
    assert row["settled"] is True and row["pnl"] == -200.0


def test_scratched_books_nothing_and_never_falls_back():
    pick = {**PICK, "odds": None}
    row = build_ledger_row(pick, RACE,
                           result={"won": None, "starting_price": 4.0, "scratched": True})
    assert row["settled"] is False
    assert row["pnl"] == 0.0
    assert row["won"] is None
    assert row["settled_at_sp_fallback"] is False


def test_shadow_tracker_dead_heat_winner_does_not_settle():
    assert settle_shadow_row(1, 4.0, 5.0, won=True, placed=True, dh=True) is None
    res = settle_shadow_row(1, 4.0, 5.0, won=True, placed=True, dh=False)
    assert res is not None and res[0] == "WIN"


def test_shadow_tracker_scratched_settles_zero_without_fallback():
    assert settle_shadow_row(None, None, 5.0, won=False, placed=False) == \
        ("SCRATCHED", 0, False)


# ---------------------------------------------------------------------------
# Shadow tracker: the silent fallback is gone
# ---------------------------------------------------------------------------

def test_shadow_settles_at_taken_price_net():
    result, pl, fb = settle_shadow_row(1, 10.0, 5.0, won=True, placed=True)
    assert (result, fb) == ("WIN", False)
    assert pl == pytest.approx(4.0 * 0.92)  # taken price 5.0, not SP 10.0


def test_shadow_sp_fallback_flagged_when_no_taken_price():
    result, pl, fb = settle_shadow_row(1, 4.0, 0, won=True, placed=True)
    assert (result, fb) == ("WIN", True)
    assert pl == pytest.approx(3.0 * 0.92)


def test_shadow_loss_is_flat_minus_one():
    assert settle_shadow_row(4, 4.0, 5.0, won=False, placed=False) == ("LOSS", -1, False)
    assert settle_shadow_row(2, 4.0, 5.0, won=False, placed=True) == ("PLACE", -1, False)


def test_shadow_commission_env_override():
    os.environ["STRIDE_COMMISSION_RATE"] = "0.10"
    result, pl, _ = settle_shadow_row(1, 4.0, 5.0, won=True, placed=True)
    assert pl == pytest.approx(4.0 * 0.90)


# ---------------------------------------------------------------------------
# Refused rows
# ---------------------------------------------------------------------------

def test_refused_row_carries_would_be_price_and_books_nothing():
    refused_pick = {**PICK, "should_bet": False, "staking": "0u"}
    row = build_ledger_row(refused_pick, RACE, refused=True)
    assert row["refused"] is True
    assert row["price_taken"] == 5.0
    assert row["stake"] == 0.0
    settled = build_ledger_row(refused_pick, RACE, refused=True,
                               result={"won": True, "starting_price": 4.0})
    assert settled["pnl"] == 0.0, "a refused row must never book P&L"
    assert settled["clv_pct"] is not None, "gate quality stays measurable via CLV"


def test_refused_flag_round_trips_through_ledger_columns():
    row = build_ledger_row({**PICK, "staking": "0u"}, RACE, refused=True)
    tup = _row_to_tuple(row)
    assert tup[LEDGER_COLUMNS.index("refused")] is True
    assert len(tup) == len(LEDGER_COLUMNS)


# ---------------------------------------------------------------------------
# weekly_metrics: the 200-bet floor
# ---------------------------------------------------------------------------

def _settled_rows(n, commission=0.0):
    rows = []
    for i in range(n):
        rows.append(build_ledger_row(
            {**PICK, "horse": f"H{i}"}, RACE,
            result={"won": (i % 5 == 0), "starting_price": 4.5},
            commission_rate=commission))
    return rows


def test_weekly_metrics_below_floor_prints_insufficient_sample():
    wm = weekly_metrics(_settled_rows(120), bootstrap_n=100)
    assert wm["n_bets"] == 120
    assert wm["reportable"] is False
    for key in ("roi_pct", "roi_net_pct", "roi_gross_pct",
                "mean_clv_pct", "pct_clv_positive"):
        assert wm[key] == INSUFFICIENT_SAMPLE, key
    assert wm["n_with_clv"] == 120  # counts stay numeric


def test_weekly_metrics_at_floor_reports_net_gross_and_clv():
    wm = weekly_metrics(_settled_rows(MIN_BETS_REPORTABLE), bootstrap_n=100)
    assert wm["reportable"] is True
    assert isinstance(wm["mean_clv_pct"], float)
    assert wm["pct_clv_positive"] == 100.0  # 5.0 taken vs 4.5 close
    # commission=0.0 rows: net == gross
    assert wm["roi_net_pct"] == wm["roi_gross_pct"]


def test_weekly_metrics_net_and_gross_diverge_with_commission():
    wm = weekly_metrics(_settled_rows(220, commission=0.08), bootstrap_n=100)
    assert wm["roi_net_pct"] < wm["roi_gross_pct"]
    assert wm["profit"] < wm["profit_gross"]


# ---------------------------------------------------------------------------
# persist_rows stays opt-in (no DB required)
# ---------------------------------------------------------------------------

def test_persist_rows_noop_when_flag_off():
    res = persist_rows(None, [build_ledger_row(PICK, RACE)])
    assert res["written"] == 0 and res["skipped"] == 1
    assert "STRIDE_LEDGER_WRITE" in res["reason"]


# ---------------------------------------------------------------------------
# Settle pass: pending rows meet results (acceptance #1)
# ---------------------------------------------------------------------------

from selection_ledger import settle_pending_rows

_SETTLE_COLS = ["race_date", "track", "race_number", "horse_name",
                "selection_origin", "should_bet", "confidence",
                "raw_model_prob", "calibrated_prob", "model_edge_pp",
                "fair_odds", "price_taken", "has_real_market_odds",
                "stake_rule", "stake", "commission_rate", "refused"]


def _pending_row(**over):
    row = ["2026-03-08", "Flemington", 5, "Alpha", "bet_pick", True, "high",
           0.30, 0.25, 5.0, 4.0, 5.0, True, "2u", 200.0, 0.08, False]
    d = dict(zip(_SETTLE_COLS, row))
    d.update(over)
    return tuple(d[c] for c in _SETTLE_COLS)


class _SettleCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0

    def execute(self, sql, params):
        pass

    @property
    def description(self):
        return [(c,) for c in _SETTLE_COLS]

    def fetchall(self):
        return self.conn.pending

    def executemany(self, sql, payload):
        self.conn.upserted.extend(payload)
        self.rowcount = len(payload)

    def close(self):
        pass


class _SettleConn:
    def __init__(self, pending):
        self.pending = pending
        self.upserted = []
        self.commits = 0

    def cursor(self):
        return _SettleCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def _col(tup, name):
    return tup[LEDGER_COLUMNS.index(name)]


def test_settle_fills_both_prices_net_pnl_and_clv():
    os.environ["STRIDE_LEDGER_WRITE"] = "true"
    conn = _SettleConn([_pending_row()])
    results = {("flemington", 5, "alpha"):
               {"position": 1, "sp": 4.0, "won": True, "placed": True}}
    out = settle_pending_rows(conn, "2026-03-08", results_map=results)
    assert out == {"settled": 1, "unmatched": 0, "pending": 1, "reason": None}
    assert len(conn.upserted) == 1 and conn.commits == 1
    tup = conn.upserted[0]
    assert _col(tup, "sp") == 4.0 and _col(tup, "price_taken") == 5.0
    assert _col(tup, "clv_pct") == pytest.approx(25.0)
    assert _col(tup, "pnl") == pytest.approx(736.0)  # settled at taken price, net 8%
    assert _col(tup, "settled_pnl") == pytest.approx(736.0)
    assert _col(tup, "settled") is True and _col(tup, "won") is True
    assert _col(tup, "settled_at_sp_fallback") is False


def test_settle_uses_the_commission_rate_recorded_at_tip_time():
    os.environ["STRIDE_LEDGER_WRITE"] = "true"
    os.environ["STRIDE_COMMISSION_RATE"] = "0.08"  # today's env must NOT rewrite history
    conn = _SettleConn([_pending_row(commission_rate=0.10)])
    results = {("flemington", 5, "alpha"):
               {"position": 1, "sp": 4.0, "won": True, "placed": True}}
    settle_pending_rows(conn, "2026-03-08", results_map=results)
    tup = conn.upserted[0]
    assert _col(tup, "commission_rate") == pytest.approx(0.10)
    assert _col(tup, "pnl") == pytest.approx(720.0)  # 800 * 0.90, the booked rate


def test_settle_leaves_unmatched_rows_pending():
    os.environ["STRIDE_LEDGER_WRITE"] = "true"
    conn = _SettleConn([_pending_row()])
    out = settle_pending_rows(conn, "2026-03-08", results_map={})
    assert out["settled"] == 0 and out["unmatched"] == 1
    assert conn.upserted == [], "no result yet — the row stays pending for the next run"


def test_settle_dead_heat_captures_sp_but_never_settles():
    os.environ["STRIDE_LEDGER_WRITE"] = "true"
    conn = _SettleConn([_pending_row()])
    results = {("flemington", 5, "alpha"):
               {"position": "1dh", "sp": 4.0, "won": True, "placed": True}}
    settle_pending_rows(conn, "2026-03-08", results_map=results)
    tup = conn.upserted[0]
    assert _col(tup, "sp") == 4.0, "SP is captured so the row is not re-picked"
    assert _col(tup, "settled") is False and _col(tup, "pnl") is None


def test_settle_scratched_books_zero():
    os.environ["STRIDE_LEDGER_WRITE"] = "true"
    conn = _SettleConn([_pending_row()])
    results = {("flemington", 5, "alpha"):
               {"position": None, "sp": None, "won": False, "placed": False}}
    settle_pending_rows(conn, "2026-03-08", results_map=results)
    tup = conn.upserted[0]
    assert _col(tup, "pnl") == 0.0 and _col(tup, "settled") is False
    assert _col(tup, "settled_at_sp_fallback") is False


def test_settle_refused_row_never_books_pnl_but_keeps_clv():
    os.environ["STRIDE_LEDGER_WRITE"] = "true"
    conn = _SettleConn([_pending_row(stake_rule="0u", stake=0.0,
                                     should_bet=False, refused=True)])
    results = {("flemington", 5, "alpha"):
               {"position": 1, "sp": 4.0, "won": True, "placed": True}}
    settle_pending_rows(conn, "2026-03-08", results_map=results)
    tup = conn.upserted[0]
    assert _col(tup, "refused") is True
    assert _col(tup, "pnl") == 0.0, "a refused row never books P&L"
    assert _col(tup, "clv_pct") == pytest.approx(25.0), "gate quality stays measurable"


def test_settle_noop_when_flag_off():
    conn = _SettleConn([_pending_row()])
    out = settle_pending_rows(conn, "2026-03-08", results_map={})
    assert out["reason"] and "STRIDE_LEDGER_WRITE" in out["reason"]
    assert conn.upserted == []
