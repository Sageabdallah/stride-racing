"""Catalogue mapping tests: Betfair markets/runners -> STRIDE races/horse_ids.

Transport and DB cursors are faked; unmapped things must be reported, never
guessed, and must not sink the rest of the capture.
"""

import json

import pytest

import betfair_markets
from betfair_markets import BetfairAPIError
from providers.betfair_auth import BetfairEdgeBlocked
from tests.betfair_fixtures import (
    APP_KEYS_DELAYED, APP_KEYS_LIVE, CATALOGUE, DATE, FakeConn, make_post,
)


def _map_with(catalogue):
    conn = FakeConn()
    cur = conn.cursor()
    return betfair_markets.map_markets(
        catalogue,
        betfair_markets.load_schedule(cur, DATE),
        betfair_markets.load_horse_bridge(cur))


def _mapping():
    return _map_with(CATALOGUE)


def test_markets_map_to_schedule_races():
    mapped, unmapped_markets, _ = _mapping()
    assert [m["market_id"] for m in mapped] == ["1.111", "1.222"]
    r1 = mapped[0]
    # Alias table bridges Betfair 'Randwick' to schedule 'Royal Randwick';
    # the row/race identity keeps the schedule's own track name.
    assert r1["race_id"] == "2026-08-01|royalrandwick|R1"
    assert r1["track"] == "Royal Randwick"
    assert r1["race_number"] == 1
    assert r1["start_time"].isoformat() == "2026-08-01T03:15:00+00:00"
    assert [m["market_id"] for m in unmapped_markets] == ["1.999"]
    assert "Nowhere Park" in unmapped_markets[0]["venue"]


def test_runners_bridge_to_horse_ids():
    mapped, _, unmapped_runners = _mapping()
    r1 = mapped[0]
    assert r1["runners"] == [
        {"selection_id": 111, "horse_name": "Fast Horse", "horse_id": "h_fast"}]
    # Mystery Guest (NZ) is not in the bridge: reported, not invented, and
    # the rest of the market still maps.
    assert len(unmapped_runners) == 1
    assert unmapped_runners[0]["selection_id"] == 222
    assert unmapped_runners[0]["runner_name"] == "Mystery Guest (NZ)"
    r2 = mapped[1]
    assert {r["horse_id"] for r in r2["runners"]} == {"h_second", "h_late", "h_nomarket"}


def test_start_time_fallback_when_market_name_has_no_race_number():
    catalogue = [{
        "marketId": "1.555", "marketName": "1200m Hcap",
        "marketStartTime": "2026-08-01T03:16:00.000Z",
        "event": {"venue": "Randwick"},
        "runners": [{"selectionId": 111, "runnerName": "1. Fast Horse"}],
    }]
    schedule = [{"track": "Royal Randwick", "race_number": 1,
                 "race_date": DATE, "off_time": "2026-08-01T13:15:00"}]
    mapped, unmapped, _ = betfair_markets.map_markets(
        catalogue, schedule, {"fasthorse": "h_fast"})
    assert unmapped == []
    assert mapped[0]["race_id"] == "2026-08-01|royalrandwick|R1"


def test_unmatched_race_number_is_never_reguessed_by_time():
    # R7 doesn't exist in the schedule; the 03:15Z start sits right on R1's
    # jump but must not pull the market onto the wrong race.
    catalogue = [dict(CATALOGUE[0], marketId="1.777", marketName="R7 1600m Hcap")]
    mapped, unmapped, _ = _map_with(catalogue)
    assert mapped == []
    assert unmapped[0]["market_id"] == "1.777"


def test_duplicate_market_for_same_race_is_reported_not_written_twice():
    twin = dict(CATALOGUE[0], marketId="1.112")
    mapped, unmapped, _ = _map_with([CATALOGUE[0], twin])
    assert [m["market_id"] for m in mapped] == ["1.111"]
    assert unmapped[0]["market_id"] == "1.112"
    assert "already mapped" in unmapped[0]["reason"]


def test_load_helpers_issue_read_only_selects():
    conn = FakeConn()
    cur = conn.cursor()
    betfair_markets.load_schedule(cur, DATE)
    bridge = betfair_markets.load_horse_bridge(cur)
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _ in conn.executed)
    # norm_name is the pf_backfill_results bridge rule (suffix + punctuation safe).
    assert bridge["fasthorse"] == "h_fast"


def test_country_suffixed_horse_bridges_through_load_horse_bridge():
    # Regression guard. BRIDGE_SQL must project the ORIGINAL-case horse_name so
    # norm_name can strip the uppercase "(NZ)". If it regresses to projecting
    # LOWER(horse_name), the DB key becomes "oceandeepnz" while the card key is
    # "oceandeep", and every NZ-bred runner (a large slice of AU fields)
    # silently fails to bridge — no odds captured, no error. The SQL-aware
    # FakeCursor reproduces that exact failure, so this test fails loudly if
    # the projection is ever lowercased again.
    conn = FakeConn(bridge_rows=[("Ocean Deep (NZ)", "h_ocean")])
    cur = conn.cursor()
    bridge = betfair_markets.load_horse_bridge(cur)
    catalogue = [{
        "marketId": "1.101", "marketName": "R1 1200m Hcap",
        "marketStartTime": "2026-08-01T03:15:00.000Z",
        "event": {"venue": "Randwick"},
        "runners": [{"selectionId": 700, "runnerName": "3. Ocean Deep (NZ)"}],
    }]
    schedule = [{"track": "Royal Randwick", "race_number": 1,
                 "race_date": DATE, "off_time": "2026-08-01T13:15:00"}]
    mapped, _, unmapped_runners = betfair_markets.map_markets(catalogue, schedule, bridge)
    assert unmapped_runners == [], "NZ-suffixed runner must bridge, not fall through"
    assert mapped[0]["runners"] == [
        {"selection_id": 700, "horse_name": "Ocean Deep (NZ)", "horse_id": "h_ocean"}]


def test_rpc_edge_block_html_raises_network_error():
    def post(url, payload, headers, timeout=30):
        return 403, "<html>Access Denied</html>"
    with pytest.raises(BetfairEdgeBlocked):
        betfair_markets.rpc("SportsAPING/v1.0/listMarketCatalogue", {}, "k", "t", post=post)


def test_rpc_aping_error_is_loud():
    def post(url, payload, headers, timeout=30):
        return 200, json.dumps({"jsonrpc": "2.0", "error": {
            "code": -32099, "data": {"APINGException": {"errorCode": "INVALID_SESSION_INFORMATION"}}}})
    with pytest.raises(BetfairAPIError) as exc:
        betfair_markets.rpc("SportsAPING/v1.0/listMarketBook", {}, "k", "t", post=post)
    assert "INVALID_SESSION_INFORMATION" in str(exc.value)


def test_catalogue_request_filter_contract():
    post = make_post()
    betfair_markets.list_win_market_catalogue(DATE, "test-app-key", "TOK", post=post)
    url, method = post.calls[0]
    assert url == betfair_markets.BETTING_RPC_URL
    assert method == "SportsAPING/v1.0/listMarketCatalogue"


def test_market_time_window_covers_the_aest_day():
    time_from, time_to = betfair_markets.market_time_window(DATE)
    # Sydney is UTC+10 in August: the AEST race day starts 14:00Z the day before.
    assert time_from == "2026-07-31T14:00:00Z"
    assert time_to == "2026-08-01T14:00:00Z"


def test_book_requests_are_chunked_under_weight_cap():
    post = make_post(books=[])
    ids = [f"1.{i}" for i in range(betfair_markets.BOOK_CHUNK_SIZE + 1)]
    betfair_markets.list_market_books(ids, "k", "t", post=post)
    assert len(post.calls) == 2


def test_resolve_odds_source_follows_delay_data_flag():
    assert betfair_markets.resolve_odds_source(
        "test-app-key", "TOK", post=make_post(appkeys=APP_KEYS_DELAYED)) == "betfair_delayed"
    assert betfair_markets.resolve_odds_source(
        "test-app-key", "TOK", post=make_post(appkeys=APP_KEYS_LIVE)) == "betfair_live"

    def broken(url, payload, headers, timeout=30):
        return 500, json.dumps({"error": {"message": "boom"}})
    # Unknown -> delayed: never claim live prices without Betfair's word.
    assert betfair_markets.resolve_odds_source("test-app-key", "TOK", post=broken) == "betfair_delayed"
