"""Book coherence: a race whose prices cannot be a market must be visible
before money is assigned to it.

The measured case: 2026-08-08 carried three races with implied books of
232-312% (bot orders parked at $1.12-$1.16 on unformed runners) and one at
83% (4 of 7 runners priced). The de-vig divided the impossible sums into
plausible-looking trueMarketProbs and the card staked 31u into those races
(settled -7.50u, against +12.90u across the coherent ones).

Pure-function tests — zero network, zero database.
"""

import pytest

from run_tips_pipeline import (
    BOOK_COVERAGE_FLOOR,
    BOOK_MIN_PRICED,
    BOOK_SUM_CEILING,
    assess_book_coherence,
)


def _runner(odds=None, name="H"):
    r = {"horse": name}
    if odds is not None:
        # The shape betfair_enrich_racecard writes into the racecard.
        r["odds"] = [{"bookmaker": "betfair", "win_odds": odds}]
    return r


def _field(prices, n_active=None):
    rs = [_runner(p, name=f"H{i}") for i, p in enumerate(prices)]
    for i in range((n_active or len(prices)) - len(prices)):
        rs.append(_runner(None, name=f"U{i}"))  # active but unpriced
    return rs


def test_normal_book_is_coherent():
    # A real 12-runner AU win book, ~125%.
    v = assess_book_coherence(_field(
        [3.5, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0]))
    assert not v["incoherent"]
    assert 1.02 < v["book"] < 1.40


def test_the_morphettville_r3_shape_is_refused():
    # Today's live case: two phantom $1.16s inside an otherwise sane field.
    v = assess_book_coherence(_field(
        [1.16, 1.16, 3.8, 3.95, 5.9, 6.2, 9.6, 9.6, 11.0, 13.5, 15.5, 17.0, 18.5]))
    assert v["incoherent"]
    assert "book_over_ceiling" in v["reasons"]
    # Two runners cannot both be near-certain regardless of the total.
    assert "top2_impossible" in v["reasons"]


def test_two_shorts_hidden_by_a_long_tail_still_refused():
    # Book sum stays under the ceiling; only the top-2 arm can see it.
    v = assess_book_coherence(_field([1.7, 1.7] + [100.0] * 10))
    assert v["book"] < BOOK_SUM_CEILING
    assert v["incoherent"]
    assert v["reasons"] == ["top2_impossible"]


def test_partial_book_is_refused_on_floor_and_coverage():
    # 4 of 7 priced, summing 0.83 — today's Morphettville R1.
    v = assess_book_coherence(_field([3.3, 3.8, 5.9, 11.0], n_active=7))
    assert v["incoherent"]
    assert "book_under_floor" in v["reasons"]
    assert "coverage_thin" in v["reasons"]


def test_under_four_prices_is_unpriceable():
    v = assess_book_coherence(_field([2.0, 3.0, 4.0], n_active=10))
    assert v["incoherent"]
    assert v["reasons"] == ["unpriceable"]
    assert v["n_priced"] < BOOK_MIN_PRICED


def test_empty_field_does_not_divide_by_zero():
    v = assess_book_coherence([])
    assert v["incoherent"]
    assert v["coverage"] == 0.0


def test_coverage_boundary_is_inclusive_at_the_floor():
    # Exactly at the floor passes: the arm is strictly-below.
    v = assess_book_coherence(_field(
        [2.5, 4.0, 6.0, 8.0, 10.0, 15.0, 25.0], n_active=10))
    assert v["coverage"] == pytest.approx(0.70)
    assert "coverage_thin" not in v["reasons"]


def test_named_blind_spot_lone_phantom_can_pass():
    # Documented limit, not a defect: one parked $1.16 whose field is long
    # enough keeps book <= ceiling and top2 <= 1.10 and PASSES. The
    # capture-side one-sided-ladder guard owns this case; this test pins the
    # boundary so a future threshold change is a conscious decision.
    v = assess_book_coherence(_field([1.16, 6.0] + [25.0] * 12))
    assert not v["incoherent"]


def test_scratched_free_field_counts_declared_runners():
    # Coverage is priced-vs-declared-active, never priced-vs-priced.
    v = assess_book_coherence(_field([2.0, 3.0, 4.0, 5.0], n_active=12))
    assert v["n_active"] == 12
    # The helper rounds coverage to 4dp for the log line.
    assert v["coverage"] == pytest.approx(4 / 12, abs=1e-4)
    assert "coverage_thin" in v["reasons"]


def test_corruption_and_coverage_classes_are_split():
    # The split is load-bearing: replayed on 2026-08-05, coverage arms flag
    # 20 of 31 races (0u withheld — unpriced races stake nothing anyway)
    # while exactly one race is genuine corruption. A run-level rule that
    # counts both classes together aborts on coverage days.
    corrupt = assess_book_coherence(_field(
        [1.16, 1.16, 3.8, 3.95, 5.9, 6.2, 9.6, 9.6, 11.0, 13.5, 15.5]))
    assert corrupt["corrupt"] is True

    coverage_only = assess_book_coherence(
        _field([3.3, 3.8, 5.9, 11.0], n_active=7))
    assert coverage_only["incoherent"] is True
    assert coverage_only["corrupt"] is False

    unpriced = assess_book_coherence(_field([2.0, 3.0], n_active=10))
    assert unpriced["incoherent"] is True
    assert unpriced["corrupt"] is False
