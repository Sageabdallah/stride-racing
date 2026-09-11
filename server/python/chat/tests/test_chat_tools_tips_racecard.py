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
