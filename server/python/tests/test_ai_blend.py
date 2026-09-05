"""The LLM blend must re-rank inside the group it scored, not tax it.

The formula these tests replace was `0.70*s + 0.30*(ai/100)*s`. That factors
to `s * (0.70 + 0.003*ai)`: a multiplier bounded above by 1.0, reaching it
only at ai_score 100. Because only the top 6 of a field are sent to the LLM,
it shrank exactly the horses that had been looked at and left runners 7+
untouched — so being scored was a penalty, and the ranking inverted. It never
showed up in production because the truncation bug means no horse has ever
carried an ai_score, so the whole path has been dead.

These tests pin the properties that make the blend safe to switch on: it can
raise as well as lower, indifference moves nothing, partial coverage applies
nothing, and the flag actually gates it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_tips_pipeline as rtp


@pytest.fixture(autouse=True)
def blend_on(monkeypatch):
    monkeypatch.setenv("STRIDE_AI_BLEND", "true")


def _h(name, score, ai=None):
    h = {"horse": name, "selectionScore": score}
    if ai is not None:
        h["ai_score"] = ai
    return h


def _old_formula(score, ai):
    """The formula that shipped, kept here so the regression is legible."""
    return round(0.70 * score + 0.30 * (ai / 100.0) * score, 2)


def test_a_horse_the_llm_rates_above_its_peers_gains():
    """The defining defect of the old formula: no ai_score below 100 could
    ever raise a selectionScore, so the LLM could only ever demote."""
    horses = [_h("Alpha", 80.0, 90), _h("Beta", 78.0, 60), _h("Gamma", 76.0, 60)]
    rec = rtp.blend_ai_scores(horses, expected=3)
    assert rec["applied"]
    assert horses[0]["selectionScore"] > 80.0, "the best-rated horse must go up"
    assert horses[0]["ai_blend_multiplier"] > 1.0
    assert horses[1]["selectionScore"] < 78.0, "a below-mean horse comes down"

    assert _old_formula(80.0, 90) < 80.0, \
        "the old formula demoted even a 90-rated horse; that is the bug"


def test_being_scored_does_not_cost_a_horse_its_place_to_an_unscored_runner():
    """The production failure: the top 6 are scored, runners 7+ are not, and
    the old multiplier was a pure shrink — so the 7th horse climbed over the
    6th without the LLM saying anything about either of them."""
    def card():
        return ([_h(f"Top{i}", 70.0 - i, 75) for i in range(6)]
                + [_h("Unscored", 64.5)])      # sits last, just below Top5

    # The same card under the old formula, scored from pristine values.
    old = sorted(card(), key=lambda h: -(_old_formula(h["selectionScore"], h["ai_score"])
                                         if "ai_score" in h else h["selectionScore"]))
    assert [h["horse"] for h in old].index("Unscored") == 1, \
        "under the old formula the unscored horse climbed from last to second"

    horses = card()
    rtp.blend_ai_scores(horses, expected=6)
    order = [h["horse"] for h in sorted(horses, key=lambda x: -x["selectionScore"])]
    assert order[-1] == "Unscored", "the unscored runner must not climb the field"
    assert order == ["Top0", "Top1", "Top2", "Top3", "Top4", "Top5", "Unscored"], \
        "an LLM that rated the six equally must leave the card exactly as it found it"


def test_a_uniformly_generous_llm_changes_no_ranking():
    """Centring on the cohort mean is what makes a subset safe to score. If
    the LLM likes everyone equally it has said nothing, and nothing may move."""
    for uniform in (20, 50, 99):
        horses = [_h("A", 80.0, uniform), _h("B", 70.0, uniform), _h("C", 60.0, uniform)]
        rtp.blend_ai_scores(horses, expected=3)
        assert [h["selectionScore"] for h in horses] == [80.0, 70.0, 60.0], \
            f"uniform ai_score {uniform} must be a no-op"


def test_partial_coverage_applies_nothing_at_all():
    """Blending the horses the LLM scored re-ranks them against the ones it
    dropped. That is a worse ranking than leaving the race alone."""
    horses = [_h("A", 80.0, 90), _h("B", 78.0, 40), _h("C", 76.0)]
    rec = rtp.blend_ai_scores(horses, expected=3)
    assert rec["applied"] is False
    assert rec["reason"] == "partial_coverage"
    assert rec["scored"] == 2 and rec["expected"] == 3
    assert [h["selectionScore"] for h in horses] == [80.0, 78.0, 76.0]


def test_no_scores_at_all_is_reported_as_its_own_reason():
    """Today's live state. It must be distinguishable from a blend that ran,
    or the log cannot tell a dead LLM from a quiet one."""
    horses = [_h("A", 80.0), _h("B", 70.0)]
    rec = rtp.blend_ai_scores(horses, expected=6)
    assert rec["reason"] == "no_ai_scores" and rec["scored"] == 0


def test_the_flag_gates_the_ranking_but_not_the_scores(monkeypatch):
    """The truncation fix and this blend are separate changes on purpose: the
    day ai_score first lands must not also be the day ranking starts moving."""
    monkeypatch.setenv("STRIDE_AI_BLEND", "false")
    horses = [_h("A", 80.0, 95), _h("B", 70.0, 20)]
    rec = rtp.blend_ai_scores(horses, expected=2)
    assert rec["applied"] is False and rec["reason"] == "disabled"
    assert rec["scored"] == 2, "the scores are still counted and still exported"
    assert [h["selectionScore"] for h in horses] == [80.0, 70.0]


def test_an_extreme_spread_is_clamped_to_the_house_band():
    """Matches the +/-25% band consensus_agent.py uses, so one runaway LLM
    score cannot outweigh the model outright."""
    horses = [_h("A", 100.0, 100), _h("B", 100.0, 0)]
    rtp.blend_ai_scores(horses, expected=2, weight=5.0)
    assert horses[0]["selectionScore"] == 125.0
    assert horses[1]["selectionScore"] == 75.0


def test_a_boolean_is_not_a_score():
    """bool is a subclass of int, so `isinstance(x, (int, float))` alone lets
    ai_score=True through as 1.0 and drags a real horse to the clamp floor."""
    horses = [_h("A", 80.0, 90), _h("B", 70.0)]
    horses[1]["ai_score"] = True
    rec = rtp.blend_ai_scores(horses, expected=2)
    assert rec["scored"] == 1, "True is not a score"
    assert rec["reason"] == "partial_coverage"


def test_the_record_reports_what_moved_not_just_what_was_scored():
    """A count of scored horses would pass identically on a race the blend
    skipped — the substitution this repo keeps finding."""
    horses = [_h("A", 80.0, 90), _h("B", 78.0, 50)]
    rec = rtp.blend_ai_scores(horses, expected=2)
    assert rec["applied"] and rec["moved"] == 2 and rec["mean_ai"] == 70.0
