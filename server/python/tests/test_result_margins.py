"""A win must score the same whichever importer wrote the row.

race_results_history carries two conventions for a winner's margin_lengths:
NULL from the old importers, the winning margin from pf_results_mapper.
The readers were written for NULL. These tests pin the read-boundary
normaliser and, through the real franking Elo and form-feature code, pin
that a race recorded under either convention produces identical numbers.
"""

import math

import pandas as pd
import pytest

import form_feature_builder
import form_franking
from result_margins import beaten_margin, beaten_margins_frame, opponent_beaten_margin


# --- the rule itself -------------------------------------------------------

@pytest.mark.parametrize("position", [1, "1", 1.0])
def test_a_winner_has_no_beaten_margin_whatever_was_stored(position):
    assert beaten_margin(position, 3.25) is None
    assert beaten_margin(position, 0.0) is None
    assert beaten_margin(position, None) is None


def test_a_placed_runner_keeps_its_margin():
    assert beaten_margin(2, 3.25) == 3.25
    assert beaten_margin("5", "1.5") == 1.5
    assert beaten_margin(None, 2.0) == 2.0, "unknown position must not erase a real margin"


def test_unknown_or_junk_margins_are_none():
    assert beaten_margin(2, None) is None
    assert beaten_margin(2, "nk") is None
    assert beaten_margin(2, float("nan")) is None


def test_opponent_entries_use_the_same_rule():
    assert opponent_beaten_margin({"position": 1, "margin": 4.0}) is None
    assert opponent_beaten_margin({"position": 3, "margin": 4.0}) == 4.0
    assert opponent_beaten_margin(None) is None


def test_frame_normaliser_blanks_winners_only_and_copies_only_when_needed():
    df = pd.DataFrame({"position": [1, 2, 3], "margin_lengths": [2.5, 2.5, 6.0]})
    out = beaten_margins_frame(df)
    assert out is not df
    assert math.isnan(out.loc[0, "margin_lengths"])
    assert list(out.loc[1:, "margin_lengths"]) == [2.5, 6.0]
    assert df.loc[0, "margin_lengths"] == 2.5, "the caller's frame is untouched"

    already = pd.DataFrame({"position": [1, 2], "margin_lengths": [None, 2.5]})
    assert beaten_margins_frame(already) is already
    empty = pd.DataFrame()
    assert beaten_margins_frame(empty) is empty
    assert beaten_margins_frame(None) is None


# --- the readers -----------------------------------------------------------

def _runs(winner_margin):
    """Five prior runs for one horse; the first is a 3L win."""
    return pd.DataFrame({
        "race_date": pd.to_datetime(["2026-08-20", "2026-08-01", "2026-07-10",
                                     "2026-06-20", "2026-06-01"]),
        "track": ["Randwick"] * 5,
        "distance_m": [1400] * 5,
        "class_level": [5] * 5,
        "position": [1, 2, 4, 3, 6],
        "margin_lengths": [winner_margin, 1.0, 4.5, 2.0, 8.0],
        "jockey": ["J Smith"] * 5,
        "sp_odds": [4.0, 5.0, 6.0, 7.0, 9.0],
        "field_size": [10] * 5,
        "weight_kg": [56.0] * 5,
        "going": ["Good"] * 5,
    })


def test_form_features_do_not_depend_on_which_importer_wrote_the_win():
    old = form_feature_builder.compute_single_horse_features(
        _runs(None), "Randwick", 1400, current_class_level=5, race_date_str="2026-09-03")
    pf = form_feature_builder.compute_single_horse_features(
        _runs(3.0), "Randwick", 1400, current_class_level=5, race_date_str="2026-09-03")
    for key in ("weighted_form_score", "has_dominant_win"):
        assert old[key] == pf[key], f"{key}: {old[key]} (NULL winner) vs {pf[key]} (PF winner)"


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_):
        pass

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _Cursor(self._rows)

    def close(self):
        pass


def _elo_rows(winner_margin):
    # (horse_id, race_id, race_date, position, margin_lengths, distance_m,
    #  class_level, going, field_size, weight_kg)
    common = ("2026-08-15", 1400, 5, "Good", 3, 56.0)
    return [
        ("A", "r1", common[0], 1, winner_margin, *common[1:]),
        ("B", "r1", common[0], 2, 3.0, *common[1:]),
        ("C", "r1", common[0], 3, 6.0, *common[1:]),
    ]


def _elo_for(monkeypatch, winner_margin):
    monkeypatch.setattr(form_franking, "_get_connection", lambda: _Conn(_elo_rows(winner_margin)))
    monkeypatch.setattr(form_franking, "_save_elo_cache", lambda ratings: None)
    return form_franking.compute_global_elo(iterations=3, force_recompute=True)


def test_franking_elo_scores_a_win_the_same_under_both_conventions(monkeypatch):
    old = _elo_for(monkeypatch, None)
    pf = _elo_for(monkeypatch, 3.0)
    assert old["A"] > old["B"] > old["C"]
    assert old == pf, (
        "the same race rated differently by importer: a winning margin read as "
        "a beaten margin makes winner and runner-up a dead heat")
