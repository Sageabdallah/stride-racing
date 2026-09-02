"""betfair_odds_snapshots.lay_price / matched_volume are written, not left NULL.

#123: both columns were NULL in every row ever written, so judging whether a
stored back_price came from a formed book (the 2026-08-05 phantom-price
cluster) meant reconstructing ladders from runner_odds_snapshots by hand.
The direct Exchange path already has the lay side and the market's matched
total in hand; these pin that they travel through the price map into the
snapshot write, and that every fallback path degrades to NULL rather than a
misleading zero.
"""

from datetime import datetime, timezone

import betfair_prices
import odds_movement

CAPTURED = datetime(2026, 9, 2, 21, 45, tzinfo=timezone.utc)


def test_races_from_rows_keeps_the_five_tuple_contract():
    races = betfair_prices._races_from_rows(
        [("canterbury", 1, "Fast Horse", 5.0, CAPTURED)])
    runner = races[("canterbury", 1)]["runners"]["fasthorse"]
    assert runner["price"] == 5.0
    assert runner["lay_price"] is None
    assert runner["matched_volume"] is None


def test_races_from_rows_carries_depth_when_given():
    races = betfair_prices._races_from_rows(
        [("canterbury", 1, "Fast Horse", 5.0, CAPTURED, 5.4, 12345.6)])
    runner = races[("canterbury", 1)]["runners"]["fasthorse"]
    assert runner["lay_price"] == 5.4
    assert runner["matched_volume"] == 12345.6


def test_races_from_rows_zero_matched_is_kept_but_missing_lay_is_null():
    # 0.0 matched is a real observation (nothing traded yet); an absent lay
    # offer is the unformed-ladder shape and must stay NULL, not 0.
    races = betfair_prices._races_from_rows(
        [("canterbury", 1, "Fast Horse", 5.0, CAPTURED, None, 0.0)])
    runner = races[("canterbury", 1)]["runners"]["fasthorse"]
    assert runner["lay_price"] is None
    assert runner["matched_volume"] == 0.0


def test_direct_path_carries_lay_and_market_total(monkeypatch):
    """fetch_direct must pass the runner's lay price and the MARKET's
    totalMatched through: the runner-level total is 0.00 on every stored
    depth row (#123), so it must not be the one forwarded."""
    monkeypatch.setattr(betfair_prices, "_direct_context",
                        lambda d: ("tok", "key", "betfair_delayed",
                                   [{"market_id": "1.111"}]))
    books = [{"marketId": "1.111", "totalMatched": 98765.0, "runners": []}]
    monkeypatch.setattr(betfair_prices.betfair_markets, "list_market_books",
                        lambda ids, key, tok: books)
    import betfair_odds_snapshot
    rows = [{"track": "canterbury", "race_number": 1, "horse_name": "Fast Horse",
             "decimal_odds": 5.0, "captured_at": CAPTURED, "market_id": "1.111",
             "lay_price": 5.4, "total_matched": 0.0}]
    monkeypatch.setattr(betfair_odds_snapshot, "build_snapshot_rows",
                        lambda *a, **k: (rows, []))
    out = betfair_prices.fetch_direct("2026-09-02")
    runner = out["races"][("canterbury", 1)]["runners"]["fasthorse"]
    assert runner["lay_price"] == 5.4
    assert runner["matched_volume"] == 98765.0


def _price_map(**runner_extra):
    runner = {"horse": "Fast Horse", "price": 5.0, "captured_at": CAPTURED}
    runner.update(runner_extra)
    return {"source": "test", "fetched_at": CAPTURED,
            "races": {("canterbury", 1): {
                "track": "canterbury", "race_number": 1,
                "runners": {"fasthorse": runner,
                            "noprice": {"horse": "No Price", "price": None}}}}}


def test_fetch_fresh_odds_contract_unchanged_without_depth(monkeypatch):
    monkeypatch.setattr(betfair_prices, "fetch_price_map",
                        lambda d, **k: _price_map(lay_price=5.4, matched_volume=1.0))
    odds = odds_movement.fetch_fresh_odds("2026-09-02")
    assert odds == {"canterbury_R1": {"Fast Horse": 5.0}}


def test_fetch_fresh_odds_depth_is_keyed_like_the_odds(monkeypatch):
    monkeypatch.setattr(betfair_prices, "fetch_price_map",
                        lambda d, **k: _price_map(lay_price=5.4, matched_volume=100.0))
    odds, depth = odds_movement.fetch_fresh_odds("2026-09-02", with_depth=True)
    assert set(depth) == set(odds)
    assert set(depth["canterbury_R1"]) == set(odds["canterbury_R1"])
    assert depth["canterbury_R1"]["Fast Horse"] == (5.4, 100.0)


def test_fetch_fresh_odds_depth_is_none_when_the_source_had_none(monkeypatch):
    monkeypatch.setattr(betfair_prices, "fetch_price_map",
                        lambda d, **k: _price_map())
    _, depth = odds_movement.fetch_fresh_odds("2026-09-02", with_depth=True)
    assert depth["canterbury_R1"]["Fast Horse"] == (None, None)


class _Cursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params):
        self.log.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return _Cursor(self.log)

    def close(self):
        pass


def test_capture_snapshot_writes_lay_and_matched(monkeypatch):
    log = []
    monkeypatch.setattr(odds_movement, "get_connection", lambda: _Conn(log))
    monkeypatch.setattr(
        odds_movement, "fetch_fresh_odds",
        lambda d, with_depth=False: ({"canterbury_R1": {"Fast Horse": 5.0}},
                                     {"canterbury_R1": {"Fast Horse": (5.4, 100.0)}}))
    odds_movement.capture_snapshot("2026-09-02", "MORNING_CHECK")
    assert len(log) == 1
    sql, params = log[0]
    assert "lay_price" in sql and "matched_volume" in sql
    assert "lay_price = EXCLUDED.lay_price" in sql
    assert "matched_volume = EXCLUDED.matched_volume" in sql
    assert params[-3:] == (5.0, 5.4, 100.0)


def test_capture_snapshot_racecard_fallback_writes_null_depth(monkeypatch):
    log = []
    monkeypatch.setattr(odds_movement, "get_connection", lambda: _Conn(log))
    monkeypatch.setattr(odds_movement, "fetch_fresh_odds",
                        lambda d, with_depth=False: ({}, {}))
    monkeypatch.setattr(odds_movement, "read_racecard_odds",
                        lambda d: {"canterbury_R1": {"Fast Horse": 5.0}})
    odds_movement.capture_snapshot("2026-09-02", "BASELINE_NIGHT")
    _, params = log[0]
    assert params[-3:] == (5.0, None, None)
