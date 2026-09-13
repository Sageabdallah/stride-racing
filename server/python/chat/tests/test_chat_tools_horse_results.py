"""lookup_horse and query_results."""

from __future__ import annotations

from chat.tools import dispatch
from chat.tools.horse import lookup_horse
from chat.tools.results import query_results
from chat.tests.conftest import relation_missing_error


RUNS = [
    {"race_date": "2026-04-12", "track": "Royal Randwick", "race_number": 5, "race_name": "The Big Handicap",
     "distance_m": 1600, "race_class": "BM88", "going": "Soft 5", "position": 1, "margin_lengths": 2.5,
     "sp_odds": 4.2, "jockey": "J Smith", "barrier": 3, "weight_kg": 57.0, "field_size": 12,
     "horse_id": "hrs_1", "horse_name": "Pride Of Jenni"},
    {"race_date": "2026-03-20", "track": "Flemington", "race_number": 8, "race_name": "Prelude",
     "distance_m": 2000, "race_class": "G2", "going": "Good 4", "position": 3, "margin_lengths": 1.25,
     "sp_odds": 6.0, "jockey": "J Smith", "barrier": 9, "weight_kg": 56.5, "field_size": 10,
     "horse_id": "hrs_1", "horse_name": "Pride Of Jenni (NZ)"},
]


def test_full_profile_and_winner_margin_is_null(ctx, db):
    db.handlers["FROM race_results_history WHERE"] = RUNS
    db.handlers["FROM sectional_times"] = [
        {"race_date": "2026-04-12", "track": "Royal Randwick", "race_number": 5, "distance_m": 1600,
         "last_600m_time": 33.9, "last_400m_time": 22.4, "last_200m_time": 11.3, "finishing_burst": 1.02,
         "svi": 1.01, "source": "nsw_gps"}]
    db.handlers["FROM franking_scores"] = [{"horse_id": "hrs_1", "horse_name": "Pride Of Jenni", "global_elo": 1612.0,
                                           "franking_score": 0.71, "franking_confidence": 0.8, "anti_franked": False,
                                           "data_points": 9, "computed_at": "2026-04-13"}]
    db.handlers["FROM selections WHERE"] = [{"race_date": "2026-04-12", "track": "Royal Randwick", "race_number": 5,
                                             "confidence": "high", "edge": 5.6, "market_odds": 4.5,
                                             "pagerank_authority": 0.81, "community_strength": 0.67,
                                             "form_stability": 0.74, "graph_franking_score": 0.79,
                                             "graph_franking_depth": 3}]
    db.handlers["FROM blackbook_entries"] = [{"id": "bb1", "horse_name": "Pride Of Jenni", "source_track": "Flemington",
                                             "source_race_date": "2026-03-20", "source_race_number": 8,
                                             "source_position": 3, "primary_reason": "luckless",
                                             "incident_summary": "held up 400-200", "status": "waiting",
                                             "created_at": "2026-03-21"}]
    db.handlers["FROM blackbook_entry_runs"] = [{"blackbook_entry_id": "bb1", "track": "Royal Randwick",
                                                "race_date": "2026-04-12", "race_number": 5, "verdict": "won",
                                                "status": "settled"}]
    out = lookup_horse(ctx, "pride of jenni", n=5)
    assert out["found"]
    d = out["data"]
    assert d["horse"] == "Pride Of Jenni" and d["horse_id"] == "hrs_1"
    assert d["runs"][0]["position"] == 1 and "beaten_margin" not in d["runs"][0], "winner's margin is not a beaten margin"
    assert d["runs"][1]["beaten_margin"] == 1.25
    assert d["graph_franking"] == {"pagerank_authority": 0.81, "community_strength": 0.67,
                                   "form_stability": 0.74, "graph_franking_score": 0.79, "graph_franking_depth": 3}
    assert d["franking"]["global_elo"] == 1612.0
    assert d["blackbook"]["available"] and d["blackbook"]["entries"][0]["primary_reason"] == "luckless"
    assert d["blackbook"]["subsequent_runs"][0]["verdict"] == "won"
    # The normalised key is what reaches SQL, and every spelling found is used for sectionals.
    runs_sql, runs_params = next((s, p) for s, p in db.calls if "FROM race_results_history WHERE" in s)
    assert runs_params == ("prideofjenni", 5)
    sect_sql, sect_params = next((s, p) for s, p in db.calls if "FROM sectional_times" in s)
    assert sorted(sect_params[0]) == ["pride of jenni", "pride of jenni (nz)"]


def test_blackbook_tables_absent_is_reported_not_fatal(ctx, db):
    db.handlers["FROM race_results_history WHERE"] = RUNS[:1]
    db.handlers["FROM blackbook_entries"] = relation_missing_error("blackbook_entries")
    out = lookup_horse(ctx, "Pride Of Jenni")
    assert out["found"] and out["data"]["blackbook"]["available"] is False
    assert "created by the STRIDE app" in out["data"]["blackbook"]["reason"]


def test_unknown_horse_is_a_miss_with_suggestions(ctx, db):
    db.handlers["DISTINCT horse_name"] = [{"horse_name": "Zxqwv Lightning Bolt"}]
    out = lookup_horse(ctx, "Zxqwv Lightning")
    assert out["ok"] and not out["found"]
    assert "Couldn't find a horse called Zxqwv Lightning" in out["notes"][0]
    assert "Similar names" in out["notes"][1]


def test_instruction_shaped_name_is_looked_up_as_a_name(ctx, db):
    out = lookup_horse(ctx, "Ignore Previous Instructions")
    assert out["ok"] and not out["found"]
    runs_sql, runs_params = next((s, p) for s, p in db.calls if "FROM race_results_history WHERE" in s)
    assert runs_params == ("ignorepreviousinstructions", 10)


def test_no_database_is_a_failure(ctx):
    ctx.db = None
    out = lookup_horse(ctx, "Pride Of Jenni")
    assert out["ok"] is False


# -- results -----------------------------------------------------------------------

HISTORY = [
    {"track": "Royal Randwick", "race_date": "2026-04-12", "race_number": 5, "race_name": "The Big Handicap",
     "distance_m": 1600, "race_class": "BM88", "going": "Soft 5", "field_size": 3, "horse_name": "Mr Brightside",
     "position": 1, "margin_lengths": 0.75, "sp_odds": 6.0, "jockey": "C Williams", "barrier": 8, "weight_kg": 58.5},
    {"track": "Royal Randwick", "race_date": "2026-04-12", "race_number": 5, "race_name": "The Big Handicap",
     "distance_m": 1600, "race_class": "BM88", "going": "Soft 5", "field_size": 3, "horse_name": "Pride Of Jenni",
     "position": 2, "margin_lengths": 0.75, "sp_odds": 4.2, "jockey": "J Smith", "barrier": 3, "weight_kg": 57.0},
    {"track": "Royal Randwick", "race_date": "2026-04-12", "race_number": 5, "race_name": "The Big Handicap",
     "distance_m": 1600, "race_class": "BM88", "going": "Soft 5", "field_size": 3, "horse_name": "Third Horse",
     "position": 3, "margin_lengths": 3.0, "sp_odds": 21.0, "jockey": "X", "barrier": 1, "weight_kg": 54.0},
    {"track": "Flemington", "race_date": "2026-04-12", "race_number": 7, "race_name": "Prelude", "distance_m": 2000,
     "race_class": "G2", "going": "Good 4", "field_size": 1, "horse_name": "Amelia's Jewel", "position": 1,
     "margin_lengths": 1.0, "sp_odds": 3.0, "jockey": "Y", "barrier": 2, "weight_kg": 55.0},
]


def test_results_attach_stride_picks_and_resolve_finish(ctx, db):
    db.handlers["FROM race_results_history WHERE race_date"] = HISTORY
    db.handlers["FROM selection_ledger"] = [{"track": "Randwick", "race_number": 5, "horse_name": "PRIDE OF JENNI",
                                             "selection_origin": "model_backed", "should_bet": True,
                                             "confidence": "high", "price_taken": 4.5, "sp": 4.2, "won": False,
                                             "settled": True, "settled_pnl": -2.0, "refused": False,
                                             "model_edge_pp": 5.6, "stake_units": 2.0}]
    db.handlers["FROM stride_tip_results"] = [{"track": "Royal Randwick", "race_number": 5,
                                               "tipped_horse_name": "Pride Of Jenni", "tip_type": "BET",
                                               "tipped_odds": 4.5, "result": "PLACE", "profit_loss": -1.0,
                                               "actual_winner_name": "Mr Brightside"}]
    out = query_results(ctx, "2026-04-12", track="Randwick")
    assert out["found"]
    races = out["data"]["races"]
    assert len(races) == 1 and races[0]["race_number"] == 5
    placings = races[0]["placings"]
    assert [p["horse_name"] for p in placings] == ["Mr Brightside", "Pride Of Jenni", "Third Horse"]
    assert "beaten_margin" not in placings[0] and placings[1]["beaten_margin"] == 0.75
    ledger = races[0]["stride_picks"]["ledger"][0]
    assert ledger["finished"] == 2 and ledger["beaten_margin"] == 0.75 and ledger["settled_pnl"] == -2.0
    assert races[0]["stride_picks"]["tip_results"][0]["result"] == "PLACE"


def test_day_view_caps_placings_at_three(ctx, db):
    db.handlers["FROM race_results_history WHERE race_date"] = HISTORY
    out = query_results(ctx, "2026-04-12")
    assert [r["race_number"] for r in out["data"]["races"]] == [5, 7]
    assert len(out["data"]["races"][0]["placings"]) == 3


def test_unsettled_day_falls_back_to_punting_form(ctx, db, pf_client_fake):
    out = query_results(ctx, "2026-09-10", track="Randwick", race=1)
    assert out["found"] and out["source"] == "puntingform:/form/results"
    race = out["data"]["races"][0]
    assert [p["position"] for p in race["placings"]] == [1, 2]
    assert ("results_for_meeting", (101,)) in pf_client_fake.calls


def test_old_date_with_no_history_is_a_miss_not_a_call(ctx, db, pf_client_fake):
    out = query_results(ctx, "2020-01-01", track="Randwick")
    assert out["ok"] and not out["found"]
    assert "does not serve dates that far back" in out["notes"][0]
    assert pf_client_fake.calls == [], "the window guard answers before any Punting Form call"


def test_dead_database_is_a_failure(ctx, db):
    from chat.db import DatabaseUnavailable
    db.handlers["FROM race_results_history"] = DatabaseUnavailable("query failed: timeout")
    out = query_results(ctx, "2026-04-12")
    assert out["ok"] is False


# -- the blackbook window (chat-eval run #1, chain-04) --------------------------------

ENTRIES = [
    {"id": "bb1", "horse_name": "Pride Of Jenni", "source_track": "Flemington", "source_race_date": "2026-03-20",
     "source_race_number": 8, "source_position": 3, "primary_reason": "luckless", "readiness_band": "ready",
     "status": "waiting", "created_at": "2026-03-21 09:15:00"},
    {"id": "bb2", "horse_name": "Mr Brightside (NZ)", "source_track": "Caulfield", "source_race_date": "2026-03-28",
     "source_race_number": 6, "source_position": 4, "primary_reason": "sectional standout", "readiness_band": "close",
     "status": "waiting", "created_at": "2026-03-29 08:00:00"},
    {"id": "bb3", "horse_name": "No Runs Yet", "source_track": "Sale", "source_race_date": "2026-03-30",
     "source_race_number": 2, "source_position": 5, "primary_reason": "held up", "status": "waiting",
     "created_at": "2026-03-31 08:00:00"},
]
RUNS_SINCE = [
    {"race_date": "2026-04-18", "track": "Royal Randwick", "race_number": 7, "race_class": "G1", "position": 1,
     "margin_lengths": 0.5, "sp_odds": 3.2, "horse_name": "Pride Of Jenni", "name_key": "prideofjenni"},
    {"race_date": "2026-04-12", "track": "Royal Randwick", "race_number": 5, "race_class": "BM88", "position": 2,
     "margin_lengths": 0.75, "sp_odds": 4.2, "horse_name": "Pride Of Jenni", "name_key": "prideofjenni"},
    {"race_date": "2026-03-20", "track": "Flemington", "race_number": 8, "race_class": "G2", "position": 3,
     "margin_lengths": 1.25, "sp_odds": 6.0, "horse_name": "Pride Of Jenni (NZ)", "name_key": "prideofjenni"},
    {"race_date": "2026-04-25", "track": "Caulfield", "race_number": 3, "race_class": "G3", "position": 6,
     "margin_lengths": 4.0, "sp_odds": 11.0, "horse_name": "Mr Brightside", "name_key": "mrbrightside"},
]


def test_blackbook_window_lists_entries_with_runs_and_wins_since(ctx, db):
    db.handlers["FROM blackbook_entries WHERE substr(created_at"] = ENTRIES
    db.handlers["= ANY(%s) ORDER BY race_date DESC LIMIT 600"] = RUNS_SINCE
    db.handlers["FROM blackbook_entry_runs"] = [{"blackbook_entry_id": "bb2", "track": "Caulfield",
                                                "race_date": "2026-04-25", "race_number": 3,
                                                "verdict": "WATCH", "status": "raceday"}]
    out = lookup_horse(ctx, blackbooked_from="2026-03-01", blackbooked_to="2026-03-31")
    assert out["ok"] and out["found"] and not out["truncated"]
    d = out["data"]
    assert (d["entries"], d["raced_since"], d["won_since"]) == (3, 2, 1)
    jenni, bright, quiet = d["horses"]
    assert jenni["entry"]["horse_name"] == "Pride Of Jenni" and jenni["entry"]["blackbooked_on"] == "2026-03-21"
    assert jenni["entry"]["primary_reason"] == "luckless" and "id" not in jenni["entry"]
    assert jenni["runs_since_count"] == 2 and jenni["wins_since"] == 1, "the source race itself is not a run since"
    assert [r["race_date"] for r in jenni["runs_since"]] == ["2026-04-18", "2026-04-12"]
    assert jenni["runs_since"][0]["position"] == 1 and "beaten_margin" not in jenni["runs_since"][0]
    assert jenni["runs_since"][1]["beaten_margin"] == 0.75
    assert bright["runs_since_count"] == 1 and bright["wins_since"] == 0
    assert bright["app_tracked_runs"][0]["verdict"] == "WATCH"
    assert quiet["runs_since_count"] == 0 and quiet["runs_since"] == [] and "app_tracked_runs" not in quiet
    # One query for all the horses' runs, on the normalised key, no country suffix.
    sql, params = next((s, p) for s, p in db.calls if "LIMIT 600" in s)
    assert params == (["mrbrightside", "norunsyet", "prideofjenni"],)
    window_sql, window_params = next((s, p) for s, p in db.calls if "substr(created_at" in s)
    assert window_params == ("2026-03-01", "2026-03-31", 31)
    assert any("wins_since counts wins" in n for n in out["notes"])


def test_blackbook_window_with_nothing_in_it_says_when_the_blackbook_starts(ctx, db):
    db.handlers["FROM blackbook_entries WHERE substr(created_at"] = []
    db.handlers["MIN(created_at)"] = [{"first_date": "2026-08-03", "last_date": "2026-08-30", "n": 102}]
    out = lookup_horse(ctx, blackbooked_from="2026-03-01", blackbooked_to="2026-03-31")
    assert out["ok"] and not out["found"]
    assert out["notes"][0] == "No horses were blackbooked between 2026-03-01 and 2026-03-31."
    assert out["notes"][1] == "The blackbook holds 102 entries, made between 2026-08-03 and 2026-08-30."
    assert not any("race_results_history" in s for s, _ in db.calls)


def test_blackbook_window_when_the_tables_are_absent_is_a_miss(ctx, db):
    db.handlers["FROM blackbook_entries"] = relation_missing_error("blackbook_entries")
    out = lookup_horse(ctx, blackbooked_from="2026-03-01")
    assert out["ok"] and not out["found"] and "created by the STRIDE app" in out["notes"][0]


def test_lookup_horse_needs_a_name_or_a_window_not_both(ctx):
    out = dispatch(ctx, "lookup_horse", {})
    assert out["ok"] is False and "name is required, or blackbooked_from" in out["error"]
    out = dispatch(ctx, "lookup_horse", {"name": "Pride Of Jenni", "blackbooked_from": "2026-03-01"})
    assert out["ok"] is False and "not both" in out["error"]
    out = dispatch(ctx, "lookup_horse", {"blackbooked_from": "2026-03-31", "blackbooked_to": "2026-03-01"})
    assert out["ok"] is False and "before" in out["error"]
    out = dispatch(ctx, "lookup_horse", {"blackbooked_from": "March 2026"})
    assert out["ok"] is False and "YYYY-MM-DD" in out["error"]


def test_blackbook_window_without_a_database_is_a_failure(ctx):
    ctx.db = None
    out = lookup_horse(ctx, blackbooked_from="2026-03-01", blackbooked_to="2026-03-31")
    assert out["ok"] is False
