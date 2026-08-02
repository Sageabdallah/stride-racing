"""Task 11 acceptance: WIN/PLACE/DEAD-HEAT settlement, including halving."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from place_shadow import (modelled_place_price, place_terms, settle_each_way,
                          settle_place)


class TestPlaceTerms:
    def test_au_terms(self):
        assert place_terms(4) == 0
        assert place_terms(5) == 2
        assert place_terms(7) == 2
        assert place_terms(8) == 3
        assert place_terms(14) == 3


class TestSettlePlace:
    def test_placed_pays_net_of_commission(self):
        # price 2.0, stake 1: gross profit 1.0, net 0.92 at 8 percent
        pnl = settle_place(2, 10, place_price=2.0)
        assert math.isclose(pnl, 0.92)

    def test_unplaced_loses_stake_not_minus_one_forever(self):
        assert settle_place(5, 10, place_price=2.0) == -1.0

    def test_no_place_market_returns_none(self):
        assert settle_place(1, 4, place_price=2.0) is None

    def test_dead_heat_halving(self):
        full = settle_place(3, 10, place_price=3.0)
        dh = settle_place(3, 10, place_price=3.0, dead_heat_fraction=0.5)
        # dead heat pays half the win portion and returns half the stake
        assert dh < full
        assert math.isclose(dh, (1.0 * 2.0 * 0.5) * 0.92 - 0.5)


class TestSettleEachWay:
    def test_winner_collects_both_legs(self):
        pnl = settle_each_way(1, 10, win_price=5.0, place_price=2.0)
        win_leg = 0.5 * 4.0 * 0.92
        place_leg = 0.5 * 1.0 * 0.92
        assert math.isclose(pnl, win_leg + place_leg)

    def test_placed_only_wins_place_loses_win(self):
        pnl = settle_each_way(3, 10, win_price=5.0, place_price=2.0)
        assert math.isclose(pnl, -0.5 + 0.5 * 1.0 * 0.92)

    def test_unplaced_loses_whole_stake(self):
        assert settle_each_way(9, 10, 5.0, 2.0) == -1.0


class TestModelledPrice:
    def test_quarter_odds_placeholder(self):
        assert modelled_place_price(5.0, 10) == 2.0
        assert modelled_place_price(5.0, 4) is None
