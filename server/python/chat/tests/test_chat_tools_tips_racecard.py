"""get_stride_tips and get_race_card against the fake data plane."""

from __future__ import annotations

from chat.tools import dispatch
from chat.tools.tips import get_stride_tips
from chat.tools.racecard import get_race_card
from chat.tests.conftest import FakeDB, relation_missing_error


# -- tips -----------------------------------------------------------------------

def test_day_view_is_one_line_per_race(ctx):
    out = get_stride_tips(ctx, "2026-04-12")
    assert out["ok"] and out["found"]
    assert out["source"] == "fake:racecards/tips_2026-04-12.json"
    races = out["data"]["races"]
    assert [r["race_number"] for r in races] == [5, 6, 7]
    r5 = races[0]
    assert r5["bet_status"] == "BET" and r5["bet_pick"]["horse"] == "Pride Of Jenni"
    assert "top_picks" not in r5 and "full_field" not in r5
    r6 = races[1]
    assert r6["bet_status"] == "NO_BET" and "bet_pick" not in r6 and r6["coverage_pick"]["horse"] == "Imperatriz"
    assert out["data"]["convergence_summary"]["confirmed"] == 1
    assert any("Day view" in n for n in out["notes"])


def test_track_filter_uses_track_aliases_and_returns_picks(ctx):
    out = get_stride_tips(ctx, "2026-04-12", track="Randwick")
    races = out["data"]["races"]
    assert [r["race_number"] for r in races] == [5, 6]
    r5 = races[0]
    assert [p["horse"] for p in r5["top_picks"]] == ["Pride Of Jenni", "Mr Brightside", "Third Horse"]
    assert r5["top_picks"][0]["key_factors"] == ["Best closing 600m", "Drops in class", "Inside draw"]
    assert "ai_insight" not in r5["bet_pick"]
    assert "coverage_pick" not in r5, "coverage pick equal to the bet pick is not repeated"
    assert "full_field" not in r5


def test_race_view_includes_capped_field_and_truncated_insight(ctx):
    out = get_stride_tips(ctx, "2026-04-12", track="Royal Randwick", race=5)
    r5 = out["data"]["races"][0]
    assert len(r5["full_field"]) == 24 and r5["full_field_truncated"] is True
    assert "ai_insight" not in r5["full_field"][0], "field rows never carry the long insight"
    assert r5["bet_pick"]["ai_insight"].endswith("chars omitted]")
    assert len(r5["bet_pick"]["ai_insight"]) < 700


def test_miss_names_the_tracks_that_do_have_tips(ctx):
    out = get_stride_tips(ctx, "2026-04-12", track="Timbuktu")
    assert out["ok"] and not out["found"]
    assert "No tips for Timbuktu on 2026-04-12" in out["notes"][0]
    assert "Flemington" in out["notes"][1] and "Royal Randwick" in out["notes"][1]


def test_missing_artifact_falls_back_to_selections_table(ctx, db):
    db.handlers["FROM selections WHERE race_date >= %s"] = [
        {"race_date": "2026-04-06", "track": "Randwick", "race_number": 3, "horse_name": "Alpha",
         "edge": 4.2, "confidence": "high", "market_odds": 5.0, "model_probability": 0.26},
    ]
    out = get_stride_tips(ctx, "2026-04-06")
    assert out["found"] and out["source"] == "neon:selections"
    assert out["data"]["dates_with_tips"] == ["2026-04-06"]
    assert out["data"]["selections_by_date"]["2026-04-06"][0]["horse_name"] == "Alpha"
    sql, params = db.calls[0]
    assert params == ("2026-04-06", "2026-04-06")


def test_range_mode_groups_by_date_and_caps_span(ctx, db):
    db.handlers["FROM selections WHERE race_date >= %s"] = [
        {"race_date": "2026-04-01", "track": "Caulfield", "race_number": 1, "horse_name": "A", "edge": 1.0},
        {"race_date": "2026-04-03", "track": "Caulfield", "race_number": 2, "horse_name": "B", "edge": 2.0},
        {"race_date": "2026-04-03", "track": "Randwick", "race_number": 2, "horse_name": "C", "edge": 2.0},
    ]
    out = get_stride_tips(ctx, "2026-04-01", track="Caulfield", date_to="2026-06-30")
    assert out["data"]["dates_with_tips"] == ["2026-04-01", "2026-04-03"]
    assert out["data"]["date_to"] == "2026-05-02", "a range longer than 31 days is clipped, not refused"
    assert all(s["track"] == "Caulfield" for d in out["data"]["selections_by_date"].values() for s in d)


def test_honest_miss_says_before_records_begin(ctx, db):
    db.handlers["MIN(race_date)"] = [{"first_date": "2026-02-01"}]
    out = get_stride_tips(ctx, "2020-01-01")
    assert out["ok"] and not out["found"]
    assert "Couldn't find any STRIDE tips for 2020-01-01" in out["notes"][0]
    assert "before STRIDE's earliest recorded tips (2026-02-01)" in out["notes"][1]


def test_dead_database_is_a_failure_not_a_miss(ctx, db):
    from chat.db import DatabaseUnavailable
    db.handlers["FROM selections"] = DatabaseUnavailable("connect failed: timeout")
    out = get_stride_tips(ctx, "2026-04-06")
    assert out["ok"] is False and "did not answer" in out["error"]


def test_bad_date_is_reported_through_dispatch_not_raised(ctx):
    out = dispatch(ctx, "get_stride_tips", {"date": "12 April 2026"})
    assert out["ok"] is False and "YYYY-MM-DD" in out["error"]
    out = dispatch(ctx, "get_stride_tips", {"date": "2026-04-12", "date_to": "2026-04-01"})
    assert out["ok"] is False and "before" in out["error"]


# -- racecard ----------------------------------------------------------------------

def test_meeting_view_lists_races_with_unscratched_counts(ctx):
    out = get_race_card(ctx, "2026-04-12", "Randwick")
    assert out["found"] and out["data"]["track"] == "Royal Randwick"
    races = out["data"]["races"]
    assert [(r["race_number"], r["runners"]) for r in races] == [(5, 2), (6, 1)]


def test_race_view_shapes_runners(ctx):
    out = get_race_card(ctx, "2026-04-12", "Royal Randwick", race=5)
    race = out["data"]["races"][0]
    runner = race["runners"][0]
    assert runner["horse"] == "Pride Of Jenni" and runner["draw"] == 3 and runner["number"] == 1
    assert runner["odds"] == [{"bookmaker": "tab", "decimal": 4.5}]
    assert runner["stats"]["career_win_percent"] == 35.7
    assert "silk_url" not in runner


def test_unknown_race_number_is_a_miss_with_count(ctx):
    out = get_race_card(ctx, "2026-04-12", "Randwick", race=9)
    assert not out["found"] and "No race 9" in out["notes"][0] and "Races on the card: 2" in out["notes"][1]


def test_track_missing_from_card_falls_back_to_punting_form(ctx, pf_client_fake):
    out = get_race_card(ctx, "2026-09-10", "Randwick", race=1)
    assert out["found"] and out["source"] == "puntingform:/form/meeting"
    race = out["data"]["races"][0]
    assert race["runners"][0]["jockey"] == "J Smith" and race["runners"][0]["horse"] == "Fast Horse"
    assert ("meetings_for_date", ("2026-09-10",)) in pf_client_fake.calls
    assert ("meeting_detail", (101,)) in pf_client_fake.calls


def test_no_card_and_date_outside_window_is_an_honest_miss(ctx):
    out = get_race_card(ctx, "2026-01-05", "Randwick")
    assert out["ok"] and not out["found"]
    assert "does not serve that date" in out["notes"][0]


def test_track_is_required(ctx):
    out = dispatch(ctx, "get_race_card", {"date": "2026-04-12"})
    assert out["ok"] is False and "track" in out["error"]


# -- the miss that says what did exist (chat-eval run #1, follow-02 / miss-02) --------------

SUNDAY_ROWS = [
    {"race_date": "2026-04-12", "track": "Gundagai", "race_number": 3, "horse_name": "Bush Flyer",
     "edge": 3.1, "market_odds": 6.0, "confidence": "medium", "win_percentage": 22.0},
    {"race_date": "2026-04-12", "track": "Hobart", "race_number": 5, "horse_name": "Apple Isle",
     "edge": 6.4, "market_odds": 4.0, "confidence": "high", "win_percentage": 31.0},
    {"race_date": "2026-04-12", "track": "Hobart", "race_number": 7, "horse_name": "Derwent Star",
     "edge": None, "market_odds": 9.0, "confidence": "low"},
]


def test_wrong_track_miss_names_tracks_leading_picks_and_the_nearest_date_at_that_track(ctx, db):
    ctx.artifacts.files.pop("racecards/tips_2026-04-12.json")  # the selections path, as for April in the relay
    # Handlers are matched in insertion order by substring: the grouped
    # neighbourhood query must be registered before the broader span query.
    db.handlers["GROUP BY race_date, track"] = [
        {"race_date": "2026-04-11", "track": "Royal Randwick", "n": 10},
        {"race_date": "2026-04-11", "track": "Caulfield", "n": 10},
        {"race_date": "2026-04-12", "track": "Gundagai", "n": 1},
        {"race_date": "2026-04-12", "track": "Hobart", "n": 2},
        {"race_date": "2026-04-18", "track": "Royal Randwick", "n": 9},
    ]
    db.handlers["FROM selections WHERE race_date >= %s"] = SUNDAY_ROWS
    out = get_stride_tips(ctx, "2026-04-12", track="Randwick")
    assert out["ok"] and not out["found"]
    assert out["notes"][0] == "Couldn't find any STRIDE tips at Randwick for 2026-04-12."
    assert "STRIDE did not tip at Randwick for 2026-04-12; its tips were at Hobart (2); Gundagai (1)" in out["notes"][1]
    assert "not Randwick selections" in out["notes"][1]
    assert out["notes"][2] == "Nearest Randwick tips: 2026-04-11 (10); 2026-04-18 (9)."
    d = out["data"]
    assert [t["track"] for t in d["tracks_with_tips"]] == ["Hobart", "Gundagai"]
    assert [p["horse_name"] for p in d["top_selections_elsewhere"]] == ["Apple Isle", "Bush Flyer", "Derwent Star"]
    assert d["top_selections_elsewhere"][0]["track"] == "Hobart" and d["top_selections_elsewhere"][0]["edge"] == 6.4
    assert [n["race_date"] for n in d["nearby_dates"]] == ["2026-04-11", "2026-04-18"]
    assert d["nearby_dates"][0]["tracks"] == ["Royal Randwick"], "only the track asked for counts as nearby"
    windows = [p for s, p in db.calls if "GROUP BY race_date, track" in s]
    assert ("2026-04-02", "2026-04-22") in windows, "ten days either side of the span"


def test_empty_day_miss_names_the_nearest_dates_with_tips(ctx, db):
    db.handlers["MIN(race_date)"] = [{"first_date": "2026-02-25", "last_date": "2026-09-11"}]
    db.handlers["GROUP BY race_date, track"] = [
        {"race_date": "2026-03-28", "track": "Caulfield", "n": 60},
        {"race_date": "2026-03-28", "track": "Rosehill", "n": 33},
        {"race_date": "2026-04-08", "track": "Eagle Farm", "n": 8},
        {"race_date": "2026-04-10", "track": "Tamworth", "n": 8},
        {"race_date": "2026-04-10", "track": "Darwin", "n": 4},
    ]
    out = get_stride_tips(ctx, "2026-04-06")
    assert out["ok"] and not out["found"]
    assert out["notes"][0] == "Couldn't find any STRIDE tips for 2026-04-06."
    assert out["notes"][1] == ("Nearest dates with tips: 2026-03-28 (93 at Caulfield, Rosehill); "
                               "2026-04-08 (8 at Eagle Farm); 2026-04-10 (12 at Darwin, Tamworth).")
    assert [n["selections"] for n in out["data"]["nearby_dates"]] == [93, 8, 12]


def test_wrong_race_miss_lists_the_races_that_had_tips(ctx, db):
    ctx.artifacts.files.pop("racecards/tips_2026-04-12.json")
    db.handlers["FROM selections WHERE race_date >= %s"] = SUNDAY_ROWS
    out = get_stride_tips(ctx, "2026-04-12", track="Hobart", race=9)
    assert not out["found"]
    assert out["notes"][0] == "Couldn't find any STRIDE tips at Hobart in race 9 for 2026-04-12."
    assert out["notes"][1] == "STRIDE's tips at Hobart for 2026-04-12 were in races 5, 7; nothing for race 9."
    assert out["data"]["races_with_tips"] == [5, 7]
    assert [p["horse_name"] for p in out["data"]["top_selections_elsewhere"]] == ["Apple Isle", "Derwent Star"]
    windows = [p for s, p in db.calls if "GROUP BY race_date, track" in s]
    assert windows == [("2026-04-12", "2026-04-12")], "the track had tips; no neighbourhood needed"


def test_honest_miss_says_after_records_end(ctx, db):
    db.handlers["MIN(race_date)"] = [{"first_date": "2026-02-25", "last_date": "2026-09-11"}]
    out = get_stride_tips(ctx, "2027-12-25", track="Ascot")
    assert not out["found"]
    assert out["notes"][1] == "2027-12-25 is after STRIDE's latest recorded tips (2026-09-11)."
    windows = [p for s, p in db.calls if "GROUP BY race_date, track" in s]
    assert windows == [("2027-12-25", "2027-12-25")], "no neighbourhood search past the records"


def test_range_miss_at_a_fictional_track_still_names_the_week(ctx, db):
    db.handlers["GROUP BY race_date, track"] = []
    db.handlers["FROM selections WHERE race_date >= %s"] = [
        {"race_date": "2026-09-11", "track": "Geelong", "race_number": 4, "horse_name": "Bay Bolt", "edge": 2.0}]
    out = get_stride_tips(ctx, "2026-09-07", track="Timbuktu", date_to="2026-09-13")
    assert out["notes"][0] == "Couldn't find any STRIDE tips at Timbuktu for 2026-09-07 to 2026-09-13."
    assert "its tips were at Geelong (1 on 2026-09-11)" in out["notes"][1]
    assert out["notes"][2] == "STRIDE has no tips at Timbuktu within ten days either side of 2026-09-07 to 2026-09-13."


def test_artifact_miss_carries_the_leading_picks_elsewhere(ctx):
    out = get_stride_tips(ctx, "2026-04-12", track="Timbuktu")
    assert not out["found"]
    picks = out["data"]["top_picks_elsewhere"]
    assert [(p["horse"], p["track"]) for p in picks] == [("Amelia's Jewel", "Flemington"),
                                                          ("Pride Of Jenni", "Royal Randwick"),
                                                          ("Imperatriz", "Royal Randwick")]
    assert picks[0]["edge_pct"] == 7.0 and picks[2]["edge_pct"] == -1.0, "a coverage pick stands in where there is no bet"
    assert out["data"]["tracks_with_tips"] == ["Flemington", "Royal Randwick"]
    assert "not picks at the track asked for" in out["notes"][1]


# -- the range and day views: a complete calendar, capped rows (chain-04's "6 and 7 March") --

def _sel(date, track, race, horse, edge, **kw):
    row = {"race_date": date, "track": track, "race_number": race, "horse_name": horse, "edge": edge,
           "market_odds": 5.0, "win_percentage": 20.0, "confidence": "medium", "value_rating": "Good",
           "jockey": "J Smith", "trainer": "T Jones", "is_active": True}
    row.update(kw)
    return row


MARCH_CALENDAR = [
    {"race_date": "2026-03-06", "track": "Doomben", "n": 3},
    {"race_date": "2026-03-07", "track": "Caulfield", "n": 9},
    {"race_date": "2026-03-07", "track": "Randwick", "n": 8},
    {"race_date": "2026-03-07", "track": "Ascot", "n": 6},
    {"race_date": "2026-03-11", "track": "Kensington", "n": 4},
    {"race_date": "2026-03-14", "track": "Flemington", "n": 9},
    {"race_date": "2026-03-18", "track": "Sandown", "n": 5},
    {"race_date": "2026-03-21", "track": "Rosehill", "n": 8},
    {"race_date": "2026-03-25", "track": "Canterbury", "n": 4},
    {"race_date": "2026-03-28", "track": "Caulfield", "n": 9},
]
MARCH_ROWS = ([_sel("2026-03-06", "Doomben", i, f"D{i}", 1.0 * i) for i in (1, 2, 3)]
              + [_sel("2026-03-07", "Caulfield", i, f"C{i}", 0.5 * i) for i in range(1, 10)]
              + [_sel("2026-03-07", "Randwick", i, f"R{i}", 6.0 - i) for i in range(1, 9)])


def test_busy_month_lists_every_date_from_the_calendar_and_caps_the_rows(ctx, db):
    db.handlers["GROUP BY race_date, track"] = MARCH_CALENDAR
    db.handlers["FROM selections WHERE race_date >= %s"] = MARCH_ROWS  # rows only for the first two dates
    out = get_stride_tips(ctx, "2026-03-01", date_to="2026-03-31")
    assert out["found"] and out["truncated"]
    d = out["data"]
    assert d["dates_with_tips"] == ["2026-03-06", "2026-03-07", "2026-03-11", "2026-03-14", "2026-03-18",
                                    "2026-03-21", "2026-03-25", "2026-03-28"], "every date, not the first 400 rows' worth"
    cal = {c["race_date"]: c for c in d["calendar"]}
    assert cal["2026-03-07"]["selections"] == 23 and [t["track"] for t in cal["2026-03-07"]["tracks"]] == ["Caulfield", "Randwick", "Ascot"]
    assert cal["2026-03-28"] == {"race_date": "2026-03-28", "selections": 9, "tracks": [{"track": "Caulfield", "selections": 9}]}
    # Two per track per date, best edge first, compact keys.
    march7 = d["selections_by_date"]["2026-03-07"]
    assert [(r["track"], r["horse_name"]) for r in march7] == [("Caulfield", "C9"), ("Caulfield", "C8"), ("Randwick", "R1"), ("Randwick", "R2")]
    assert "jockey" not in march7[0] and march7[0]["edge"] == 4.5
    assert d["selections_by_date"]["2026-03-14"] == []
    assert any(n.startswith("Rows for 2026-03-11, 2026-03-14, 2026-03-18, 2026-03-21, 2026-03-25, 2026-03-28 are not shown") for n in out["notes"])
    assert any("leading 2 by edge per track per date" in n for n in out["notes"])
    # Only the active set counts, in both queries, and the row fetch is bounded.
    cal_sql = next(s for s, _ in db.calls if "GROUP BY race_date, track" in s)
    row_sql, row_params = next((s, p) for s, p in db.calls if "ORDER BY race_date, track, edge DESC" in s)
    assert "COALESCE(is_active, true)" in cal_sql and "COALESCE(is_active, true)" in row_sql
    assert row_params == ("2026-03-01", "2026-03-31", 1500)


def test_day_view_shows_three_per_track_and_the_calendar_counts(ctx, db):
    db.handlers["GROUP BY race_date, track"] = [{"race_date": "2026-03-07", "track": "Caulfield", "n": 9},
                                                 {"race_date": "2026-03-07", "track": "Randwick", "n": 8}]
    db.handlers["FROM selections WHERE race_date >= %s"] = [r for r in MARCH_ROWS if r["race_date"] == "2026-03-07"]
    out = get_stride_tips(ctx, "2026-03-07")
    rows = out["data"]["selections_by_date"]["2026-03-07"]
    assert [r["horse_name"] for r in rows] == ["C9", "C8", "C7", "R1", "R2", "R3"]
    assert out["truncated"] and out["data"]["calendar"][0]["selections"] == 17
    assert any("leading 3 by edge per track per date" in n for n in out["notes"])


def test_track_view_keeps_the_detail_and_caps_per_date(ctx, db):
    db.handlers["GROUP BY race_date, track"] = [{"race_date": "2026-03-07", "track": "Caulfield", "n": 14}]
    db.handlers["FROM selections WHERE race_date >= %s"] = (
        [_sel("2026-03-07", "Caulfield", i, f"C{i}", float(i)) for i in range(1, 15)]
        + [_sel("2026-03-07", "Randwick", 1, "R1", 9.0)])
    out = get_stride_tips(ctx, "2026-03-07", track="Caulfield")
    rows = out["data"]["selections_by_date"]["2026-03-07"]
    assert len(rows) == 12 and rows[0]["horse_name"] == "C14" and rows[0]["jockey"] == "J Smith"
    assert all(r["track"] == "Caulfield" for r in rows)
    assert out["truncated"] and any("capped at 12 rows per date" in n for n in out["notes"])
    assert out["data"]["dates_with_tips"] == ["2026-03-07"] and out["data"]["calendar"][0]["tracks"] == [{"track": "Caulfield", "selections": 14}]
