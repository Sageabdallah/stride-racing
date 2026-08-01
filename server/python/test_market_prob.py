#!/usr/bin/env python3
"""DB-free unit tests for market_prob.py (task 05)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from market_prob import (devig_probabilities_pct, implied_prob_pct, overround,
                         reference_price, renormalise_probs,
                         true_market_prob_pct, valid_odds)


class TestDevig:
    def test_probs_sum_to_one(self):
        probs = devig_probabilities_pct([2.0, 3.0, 4.0, 6.0, 10.0, 15.0])
        assert abs(sum(probs) - 100.0) < 1e-9

    def test_proportional_devig(self):
        # 2.0 / 4.0 book: implied 50 + 25 = 75 -> 66.67 / 33.33
        a, b = devig_probabilities_pct([2.0, 4.0])
        assert a == pytest.approx(200.0 / 3.0)
        assert b == pytest.approx(100.0 / 3.0)
        assert a / b == pytest.approx(2.0)  # proportional, not level stakes

    def test_invalid_quotes_contribute_zero(self):
        probs = devig_probabilities_pct([2.0, None, "x", 0.5, 4.0])
        assert probs[1] == 0.0 and probs[2] == 0.0 and probs[3] == 0.0
        assert sum(probs) == pytest.approx(100.0)

    def test_empty_book(self):
        assert devig_probabilities_pct([]) == []
        assert devig_probabilities_pct([None, 0.5]) == [0.0, 0.0]

    def test_overround(self):
        assert overround([2.0, 4.0]) == pytest.approx(0.75)
        assert overround([2.0, 3.0, 4.0, 6.0, 10.0]) == pytest.approx(1.35)
        # Fewer than two valid quotes cannot be de-vigged -> no-op.
        assert overround([5.0]) == 1.0
        assert overround([]) == 1.0
        assert overround([None, "x"]) == 1.0

    def test_true_market_matches_pipeline_formula(self):
        # run_tips_pipeline legacy: true_market = (100 / odds) / overround
        ov = 1.18
        for odds in (1.5, 2.2, 5.0, 13.0, 34.0):
            assert true_market_prob_pct(odds, ov) == pytest.approx(
                (100.0 / odds) / ov)

    def test_true_market_degrades_to_raw_implied(self):
        assert true_market_prob_pct(4.0, None) == pytest.approx(25.0)
        assert true_market_prob_pct(4.0, 0) == pytest.approx(25.0)
        assert true_market_prob_pct(4.0, 1.0) == pytest.approx(25.0)
        assert true_market_prob_pct(None, 1.2) == 0.0
        assert true_market_prob_pct(0.9, 1.2) == 0.0


class TestReferencePrice:
    def test_median_across_bookmakers(self):
        quotes = [{"bookmaker": "tab", "decimal": 3.0},
                  {"bookmaker": "sportsbet", "decimal": 3.4},
                  {"bookmaker": "ladbrokes", "decimal": 3.2}]
        assert reference_price(quotes) == pytest.approx(3.2)

    def test_even_count_median(self):
        assert reference_price([3.0, 3.2, 3.4, 3.3]) == pytest.approx(3.25)

    def test_dict_and_scalar_forms(self):
        assert reference_price({"tab": 3.0, "sb": 3.4, "lb": 3.2}) == pytest.approx(3.2)
        assert reference_price(3.5) == pytest.approx(3.5)

    def test_string_and_dollar_quotes(self):
        assert reference_price(["3.00", "$3.40", 3.2]) == pytest.approx(3.2)

    def test_alternate_keys(self):
        assert reference_price([{"win_odds": 2.5}, {"odds": 2.7}]) == pytest.approx(2.6)
        assert reference_price([{"price": 5.0}]) == pytest.approx(5.0)

    def test_invalid_quotes_skipped(self):
        assert reference_price([{"decimal": 0.5}, {"decimal": None},
                                {"decimal": 3.1}]) == pytest.approx(3.1)
        assert reference_price(None) is None
        assert reference_price([0.5, "x", None]) is None


class TestValidOdds:
    def test_coercion(self):
        assert valid_odds("2.50") == 2.5
        assert valid_odds("$3.20") == 3.2
        assert valid_odds(1.0) is None
        assert valid_odds(0.5) is None
        assert valid_odds("evens") is None
        assert valid_odds(None) is None


class TestRenormalise:
    def test_sums_to_target_within_1e6(self):
        vals = [59.43, 36.57, 22.14]  # post-anchor field that does not sum to 100
        out = renormalise_probs(vals)
        assert abs(sum(out) - 100.0) <= 1e-6

    def test_large_field_sums_exactly(self):
        vals = [23.7, 18.2, 14.9, 11.3, 9.9, 8.4, 7.7, 6.1, 5.5, 4.8, 4.1, 3.6]
        out = renormalise_probs(vals)
        assert abs(sum(out) - 100.0) <= 1e-6
        assert len(out) == len(vals)

    def test_proportions_preserved(self):
        vals = [59.43, 36.57, 22.14]
        out = renormalise_probs(vals)
        total = sum(vals)
        for o, v in zip(out, vals):
            assert o == pytest.approx(v * 100.0 / total, abs=0.02)

    def test_residual_goes_to_largest(self):
        out = renormalise_probs([33.33, 33.33, 33.33])
        assert abs(sum(out) - 100.0) <= 1e-6
        assert max(out) == pytest.approx(33.34)
        assert sorted(out)[:-1] == [33.33, 33.33]

    def test_all_zero_field_unchanged(self):
        assert renormalise_probs([0, 0, 0]) == [0.0, 0.0, 0.0]

    def test_unit_interval_target(self):
        out = renormalise_probs([0.5943, 0.3657, 0.2214], target=1.0, ndigits=6)
        assert abs(sum(out) - 1.0) <= 1e-6

    def test_implied_prob_pct(self):
        assert implied_prob_pct(4.0) == pytest.approx(25.0)
        assert implied_prob_pct(None) == 0.0
