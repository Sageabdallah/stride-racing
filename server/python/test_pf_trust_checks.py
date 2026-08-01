"""Tests for pf_trust_checks — pure-function fixtures only, zero DB/network."""
import pytest

from pf_trust_checks import (
    bridge_disagreements,
    build_old_day,
    build_pf_day,
    compare_day,
    compare_race,
    literal_track_collisions,
    margin_bucket,
    match_meetings,
    norm_track,
    sectionals_alignment,
    state_for,
    state_rollup,
)


def _pf_payload(track, races):
    """races: {rnum: [(position, margin, name), ...]}"""
    return {
        "track": track,
        "raceResults": [
            {"raceNumber": rnum,
             "runners": [{"position": pos, "margin": margin, "runner": name,
                          "runnerId": 1000 + i}
                         for i, (pos, margin, name) in enumerate(runners)]}
            for rnum, runners in races.items()
        ],
    }


def _old_rows(track, rnum, runners, field_size=None):
    """runners: [(position, margin, name)] -> build_old_day row tuples."""
    return [(track, rnum, pos, name, f"hrs_aus_{i}", margin,
             field_size if field_size is not None else len(runners))
            for i, (pos, margin, name) in enumerate(runners)]


# ---------------------------------------------------------------- build

def test_build_pf_day_list_and_dict_payload():
    payload = _pf_payload("Randwick", {1: [(1, 1.25, "Alpha"), (2, 1.25, "Beta")]})
    from_list = build_pf_day([ [payload] ])  # archive payload is a list
    from_dict = build_pf_day([payload])      # tolerate a bare meeting dict
    assert from_list == from_dict
    race = from_list["Randwick"]["races"][1]
    assert race["runners"]["alpha"]["position"] == 1
    assert race["runners"]["beta"]["margin"] == 1.25


def test_build_pf_day_position_zero_is_scratched():
    payload = _pf_payload("Nyngan", {1: [(1, 0, "Winner"), (0, 0, "Scratched")]})
    race = build_pf_day([payload])["Nyngan"]["races"][1]
    assert race["runners"]["scratched"]["position"] is None


def test_build_old_day_normalises_names():
    rows = _old_rows("Moe", 5, [(1, None, "Shalassa (NZ)"), (2, 1.5, "Orchid Sky")])
    day = build_old_day(rows)
    assert set(day["Moe"]["races"][5]["runners"]) == {"shalassa", "orchidsky"}


# ---------------------------------------------------------------- matching

def test_match_meetings_alias_via_runner_overlap():
    pf = {"Ballarat Synthetic": {"names": {"alpha", "beta", "gamma", "delta"}}}
    old = {"Sportsbet-Ballarat Synthetic": {"names": {"alpha", "beta", "gamma", "delta"}}}
    matches, unmatched_pf, _ = match_meetings(pf, old)
    assert matches == [("Ballarat Synthetic", "Sportsbet-Ballarat Synthetic", 4, 1.0)]
    assert unmatched_pf == []


def test_match_meetings_disjoint_names_no_match():
    pf = {"Flemington": {"names": {"a", "b", "c", "d"}}}
    old = {"Randwick": {"names": {"w", "x", "y", "z"}}}
    matches, unmatched_pf, unmatched_old = match_meetings(pf, old)
    assert matches == []
    assert unmatched_pf == ["Flemington"]
    assert unmatched_old == ["Randwick"]


# ---------------------------------------------------------------- parity

def test_compare_race_agree_exact_margins():
    pf_race = {"runners": {
        "alpha": {"name": "Alpha", "position": 1, "margin": 1.25},
        "beta": {"name": "Beta", "position": 2, "margin": 1.25}}}
    old_race = {"runners": {
        "alpha": {"name": "Alpha", "position": 1, "margin": None},
        "beta": {"name": "Beta", "position": 2, "margin": 1.25}},
        "field_size": 2}
    cmp = compare_race(pf_race, old_race)
    assert cmp["winner"] is True
    assert cmp["margins"]["exact"] == 1            # beta: 1.25 vs 1.25
    assert cmp["winner_null_semantic"] == 1        # alpha: old NULL winner
    assert cmp["null_mismatch"] == 0
    assert cmp["field_delta"] == 0


def test_margin_bucket_boundaries():
    assert margin_bucket(0.0) == "exact"
    assert margin_bucket(0.5) == "<=0.5L"
    assert margin_bucket(0.51) == "<=1L"
    assert margin_bucket(1.0) == "<=1L"
    assert margin_bucket(1.01) == ">1L"


def test_compare_race_margin_off_and_winner_disagree():
    pf_race = {"runners": {
        "alpha": {"name": "Alpha", "position": 1, "margin": 2.0},
        "beta": {"name": "Beta", "position": 2, "margin": 3.0}}}
    old_race = {"runners": {
        "beta": {"name": "Beta", "position": 1, "margin": None},
        "alpha": {"name": "Alpha", "position": 2, "margin": 2.0}},
        "field_size": 2}
    cmp = compare_race(pf_race, old_race)
    assert cmp["winner"] is False
    assert cmp["margins"]["exact"] == 1            # alpha: 2.0 vs 2.0
    assert cmp["null_mismatch"] == 1               # beta: pf 3.0 vs old NULL
    assert cmp["winner_null_semantic"] == 0        # beta's NULL is not the pf winner


def test_compare_day_missing_race_and_unmatched_meeting():
    pf_day = build_pf_day([_pf_payload("Randwick", {
        1: [(1, 0, "A"), (2, 1.0, "B"), (3, 2.0, "C")],
        2: [(1, 0, "D"), (2, 1.0, "E"), (3, 2.0, "F")]})])
    old_day = build_old_day(
        _old_rows("Royal Randwick", 1, [(1, None, "A"), (2, 1.0, "B"), (3, 2.0, "C")]) +
        _old_rows("Royal Randwick", 3, [(1, None, "G"), (2, 1.0, "H"), (3, 2.0, "I")]))
    # old has no R2 (PF-only race) and an R3 with disjoint names -> old-only
    res = compare_day(pf_day, old_day)
    assert res["alias_pairs"] == {("Randwick", "Royal Randwick"): 1}
    assert res["tot"]["races_pf_only"] == 1
    assert res["tot"]["races_compared"] == 1
    assert res["tot"]["races_old_only"] == 1
    assert res["tot"]["winner_agree"] == 1


def test_compare_day_void_shell_field_delta():
    pf_day = build_pf_day([_pf_payload("Moe", {
        5: [(0, 0, "A"), (0, 0, "B"), (0, 0, "C")]})])  # abandoned: no placings
    old_day = build_old_day(_old_rows(
        "Moe", 5, [(None, None, "A"), (None, None, "B"), (None, None, "C")],
        field_size=12))
    res = compare_day(pf_day, old_day)
    assert res["field_deltas"] == {-12: 1}
    assert res["tot"]["field_delta_void_shells"] == 1
    assert res["tot"]["winner_unknown"] == 1


# ---------------------------------------------------------------- collisions

def test_literal_track_collisions_case_variants():
    assert literal_track_collisions(["Randwick", "RANDWICK", "Flemington"]) == {
        "randwick": ["RANDWICK", "Randwick"]}
    assert literal_track_collisions(["Randwick", "Royal Randwick"]) == {}


# ---------------------------------------------------------------- bridge

def test_bridge_disagreements():
    pf_rows = [(3, 1, "Shalassa", "pf999"), (3, 2, "Orchid Sky", "hrs_aus_1")]
    old_rows = [(3, 1, "Shalassa", "hrs_aus_7"), (3, 2, "Different Mare", "hrs_aus_2")]
    same_name, diff_name = bridge_disagreements(pf_rows, old_rows)
    assert same_name == [(3, 1, "Shalassa", "pf999", "hrs_aus_7")]
    assert diff_name == [(3, 2, "Orchid Sky", "hrs_aus_1", "hrs_aus_2")]
    # identical ids -> no disagreement
    assert bridge_disagreements([(3, 1, "X", "h1")], [(3, 1, "X", "h1")]) == ([], [])


# ---------------------------------------------------------------- sectionals

def test_sectionals_alignment_diffs_and_missing():
    pf_day = build_pf_day([_pf_payload("Rosehill", {
        1: [(1, 0, "A"), (2, 1, "B"), (3, 2, "C")],
        2: [(1, 0, "A"), (2, 1, "D"), (3, 2, "E")]})])
    sec_races = {("Rosehill Gardens", 1): {"a", "b", "c"},
                 ("Rosehill Gardens", 2): {"a", "b"},       # b vs d/e diffs
                 ("Rosehill Gardens", 3): {"a"}}            # no PF race 3
    report, unmatched = sectionals_alignment(sec_races, pf_day)
    assert unmatched == []
    by_rnum = {r[2]: r for r in report}
    assert by_rnum[1][3] == [] and by_rnum[1][4] == []     # fully aligned
    assert by_rnum[2][3] == ["b"]                          # sectionals-only
    assert set(by_rnum[2][4]) == {"d", "e"}                # pf-only
    assert by_rnum[3][5] is True                           # no PF race


def test_sectionals_alignment_unmatched_meeting():
    pf_day = build_pf_day([_pf_payload("Randwick", {1: [(1, 0, "A"), (2, 1, "B"), (3, 2, "C")]})])
    sec_races = {("Eagle Farm", 1): {"x", "y", "z"}}
    report, unmatched = sectionals_alignment(sec_races, pf_day)
    assert report == []
    assert unmatched == ["Eagle Farm"]


# ---------------------------------------------------------------- states

def test_state_for_sponsor_variants():
    assert state_for("Sportsbet-Ballarat Synthetic") == "VIC"
    assert state_for("Royal Randwick") == "NSW"
    assert state_for("Thomas Farms RC Murray Bridge") == "SA"
    assert state_for("Fannie Bay") == "NT"
    assert state_for("Aquis Park Gold Coast") == "QLD"
    assert state_for("Nowhereville") is None
    assert norm_track("Laura (Q)") == "lauraq"


def test_state_rollup_weekday_averages_and_unmapped():
    # Two full weeks, 2026-07-06 (Mon) .. 2026-07-19 (Sun).
    span = ("2026-07-06", "2026-07-19")
    pf_meetings = [("2026-07-06", "Randwick"), ("2026-07-13", "Randwick"),
                   ("2026-07-06", "Mystery Track")]     # unmapped
    hist_meetings = [("2026-07-06", "Royal Randwick"),
                     ("2026-07-06", "Flemington"),
                     ("2026-07-13", "Royal Randwick"),
                     ("2026-07-13", "Flemington")]
    table, unmapped = state_rollup(pf_meetings, hist_meetings, span, span)
    assert unmapped == ["Mystery Track"]
    # NSW Mondays: pf 2 meetings / 2 Mondays = 1.0; hist 2/2 = 1.0
    assert table["NSW"]["Mon"] == {"pf": 1.0, "hist": 1.0}
    # VIC Mondays: pf 0; hist 2/2 = 1.0
    assert table["VIC"]["Mon"] == {"pf": 0.0, "hist": 1.0}
    assert table["NSW"]["Sat"] == {"pf": 0.0, "hist": 0.0}
