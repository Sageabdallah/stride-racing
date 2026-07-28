#!/usr/bin/env python3
"""Unit tests for roi_stats — DB-free, numpy only.

Covers the acceptance cases from roi-roadmap task 02:
  - known-case CI on a synthetic 142-bet / 14-win / avg-odds-11.4 series
    reproduces CI ≈ [−44%, +68%] (the documented normal approx, tight
    tolerance; the seeded percentile bootstrap, seed-deterministic band);
  - streak/drawdown on fixed sequences;
  - winner-concentration math;
  - multi-comparison verdicts;
  - the reportability floor.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import roi_stats  # noqa: E402


def _known_series():
    """142 bets, 14 winners at avg odds 11.4 -> +12.3% gross ROI."""
    won = [1] * 14 + [0] * 128
    odds = [11.4] * 14 + [3.0] * 128
    return won, odds


# ---------------------------------------------------------------- roi_ci ---

class TestKnownCaseCI:
    def test_gross_normal_ci_reproduces_documented_interval(self):
        # docs/roi-roadmap/00-evidence-base.md: 95% CI [-43.6%, +68.2%],
        # SE +-28.5pp, z = 0.43 on the gross +12.3% headline.
        won, odds = _known_series()
        ci = roi_stats.roi_ci(
            roi_stats.per_bet_returns(won, odds, commission=0.0),
            n_boot=10000, seed=42)
        assert ci['roi'] == pytest.approx(12.32, abs=0.5)
        assert ci['se'] == pytest.approx(28.5, abs=1.5)
        assert ci['z'] == pytest.approx(0.43, abs=0.05)
        nlo, nhi = ci['ci95_normal']
        assert nlo == pytest.approx(-43.6, abs=1.5)
        assert nhi == pytest.approx(68.2, abs=1.5)

    def test_bootstrap_ci_within_tolerance_of_documented_interval(self):
        # Percentile bootstrap on the lattice series (all winners at the mean
        # odds) is narrower than the normal interval; seeded, so the band is
        # deterministic. Both endpoints of [-44, +68] must sit inside the
        # tolerance band, and the CI must span zero.
        won, odds = _known_series()
        ci = roi_stats.roi_ci(
            roi_stats.per_bet_returns(won, odds, commission=0.0),
            n_boot=10000, seed=42)
        lo, hi = ci['ci95']
        assert -50 < lo < -35, lo
        assert 50 < hi < 76, hi
        assert ci['spans_zero'] is True
        assert 0.2 < ci['p_roi_le_zero'] < 0.5  # documented: ~35%
        assert ci['n_boot'] == 10000

    def test_bootstrap_is_seeded_and_resamples_bets(self):
        won, odds = _known_series()
        returns = roi_stats.per_bet_returns(won, odds)
        a = roi_stats.roi_ci(returns, n_boot=2000, seed=42)
        b = roi_stats.roi_ci(returns, n_boot=2000, seed=42)
        c = roi_stats.roi_ci(returns, n_boot=2000, seed=7)
        assert a['ci95'] == b['ci95']
        # On a lattice series different seeds can round to the same CI; use a
        # heterogeneous-odds series to prove the seed actually drives draws.
        rng = np.random.default_rng(0)
        varied = np.where(rng.random(142) < 0.1, rng.uniform(2, 20, 142), -1.0)
        va = roi_stats.roi_ci(varied, n_boot=2000, seed=42)
        vc = roi_stats.roi_ci(varied, n_boot=2000, seed=7)
        assert va['ci95'] != vc['ci95']
        assert a['n'] == 142  # the bet count is the resample unit

    def test_empty_series(self):
        ci = roi_stats.roi_ci([], n_boot=100)
        assert ci['n'] == 0 and ci['ci95'] == [None, None]
        assert ci['spans_zero'] is None and ci['z'] is None


# ------------------------------------------------- per-bet returns ---------

class TestPerBetReturns:
    def test_flat_staking(self):
        r = roi_stats.per_bet_returns([1, 0], [5.0, 3.0])
        assert r[0] == pytest.approx(4.0)
        assert r[1] == pytest.approx(-1.0)

    def test_net_variant_charges_commission_on_winnings_only(self):
        r = roi_stats.per_bet_returns([1, 0], [5.0, 3.0], commission=0.08)
        assert r[0] == pytest.approx(4.0 * 0.92)
        assert r[1] == pytest.approx(-1.0)  # losers never pay commission

    def test_known_case_net_roi(self):
        # docs A3: +12.3% gross -> ~+4.1% net at 8% commission.
        won, odds = _known_series()
        net = roi_stats.per_bet_returns(won, odds, commission=0.08)
        assert float(net.mean()) * 100 == pytest.approx(4.19, abs=0.3)

    def test_commission_env_parsing(self):
        prev = os.environ.pop('STRIDE_COMMISSION_RATE', None)
        try:
            assert roi_stats.commission_rate_from_env() == 0.08
            os.environ['STRIDE_COMMISSION_RATE'] = '0.10'
            assert roi_stats.commission_rate_from_env() == pytest.approx(0.10)
            os.environ['STRIDE_COMMISSION_RATE'] = 'junk'
            assert roi_stats.commission_rate_from_env() == 0.08
            os.environ['STRIDE_COMMISSION_RATE'] = '1.5'
            assert roi_stats.commission_rate_from_env() == 0.08
        finally:
            if prev is None:
                os.environ.pop('STRIDE_COMMISSION_RATE', None)
            else:
                os.environ['STRIDE_COMMISSION_RATE'] = prev


# ------------------------------------------------- drawdown & streaks ------

class TestDrawdownAndStreaks:
    def test_fixed_sequence(self):
        # +5 then four -1s then +2: equity 5,4,3,2,1,3 -> dd 4; streak 4.
        r = roi_stats.max_drawdown_and_streaks([5, -1, -1, -1, -1, 2])
        assert r['max_drawdown'] == pytest.approx(4.0)
        assert r['max_losing_streak'] == 4

    def test_streak_resets(self):
        r = roi_stats.max_drawdown_and_streaks([-1, -1, 3, -1, -1, -1, 2])
        assert r['max_losing_streak'] == 3

    def test_monotone_loss_drawdown(self):
        r = roi_stats.max_drawdown_and_streaks([-1] * 7)
        assert r['max_drawdown'] == pytest.approx(6.0)  # peak -1, trough -7
        assert r['max_losing_streak'] == 7
        assert r['expected_max_losing_streak'] == 7.0  # q = 1 edge case

    def test_no_losses(self):
        r = roi_stats.max_drawdown_and_streaks([2, 3, 1])
        assert r['max_drawdown'] == 0.0
        assert r['max_losing_streak'] == 0
        assert r['expected_max_losing_streak'] == 0.0

    def test_expected_streak_known_case(self):
        # Evidence base: at 9.9% strike over 142 bets, expected max losing
        # streak ~= 30.
        won, odds = _known_series()
        r = roi_stats.max_drawdown_and_streaks(
            roi_stats.per_bet_returns(won, odds))
        assert r['strike_rate'] == pytest.approx(14 / 142, abs=1e-3)
        assert r['expected_max_losing_streak'] == pytest.approx(30, abs=2.0)

    def test_empty(self):
        r = roi_stats.max_drawdown_and_streaks([])
        assert r['n'] == 0 and r['max_losing_streak'] == 0


# ------------------------------------------------------ concentration ------

class TestWinnerConcentration:
    def test_fixed_sequence(self):
        # returns [5, -1, -1, 3]: ROI 150%; minus top1 -> (3-1-1)/3 = 33.3%.
        conc = roi_stats.winner_concentration([5, -1, -1, 3])
        assert conc['roi_minus_top1'] == pytest.approx(100.0 / 3, abs=0.01)
        # minus top2 -> (-1-1)/2 = -100%; minus top3 -> only 2 winners -> None.
        assert conc['roi_minus_top2'] == pytest.approx(-100.0)
        assert conc['roi_minus_top3'] is None

    def test_known_case_sign_flip(self):
        # The documented fragility: the +12.3% edge is 1-2 horses.
        won, odds = _known_series()
        conc = roi_stats.winner_concentration(
            roi_stats.per_bet_returns(won, odds))
        assert conc['roi_minus_top1'] == pytest.approx(7.2 / 141 * 100, abs=0.01)
        assert conc['roi_minus_top2'] == pytest.approx(-3.2 / 140 * 100, abs=0.01)
        assert conc['roi_minus_top3'] == pytest.approx(-13.6 / 139 * 100, abs=0.01)

    def test_no_winners(self):
        conc = roi_stats.winner_concentration([-1, -1, -1])
        assert conc['roi_minus_top1'] is None
        assert conc['roi_minus_top2'] is None


# -------------------------------------------------- multi-comparison -------

class TestMultiComparison:
    def test_known_sweep_fails_bonferroni(self):
        # Best of 6 bands: +12.3% at SE 28.5 -> z 0.43 << required 2.64.
        note = roi_stats.multi_comparison_note(6, 12.32, 28.5)
        assert note['n_strategies'] == 6
        assert note['bonferroni_z_required'] == pytest.approx(2.638, abs=0.01)
        assert note['best_observed_z'] == pytest.approx(0.432, abs=0.01)
        assert note['verdict'] == 'NOT_SIGNIFICANT_AFTER_BONFERRONI'

    def test_single_strategy_uses_plain_95_bar(self):
        note = roi_stats.multi_comparison_note(1, 5.0, 2.0)
        assert note['bonferroni_z_required'] == pytest.approx(1.96, abs=0.01)
        assert note['verdict'] == 'SURVIVES_BONFERRONI'

    def test_strong_result_survives(self):
        note = roi_stats.multi_comparison_note(13, 30.0, 8.0)
        assert note['best_observed_z'] == pytest.approx(3.75)
        assert note['verdict'] == 'SURVIVES_BONFERRONI'

    def test_indeterminate_without_se(self):
        assert roi_stats.multi_comparison_note(4, 10.0, None)['verdict'] == 'INDETERMINATE'
        assert roi_stats.multi_comparison_note(4, 10.0, 0.0)['verdict'] == 'INDETERMINATE'

    def test_invalid_n(self):
        with pytest.raises(ValueError):
            roi_stats.multi_comparison_note(0, 1.0, 1.0)

    def test_block_helper(self):
        won, odds = _known_series()
        blocks = [roi_stats.summarise_bets(won, odds, n_boot=2000, seed=42),
                  roi_stats.summarise_bets([1, 0, 0], [2.0, 3.0, 4.0], n_boot=100)]
        mc = roi_stats.multi_comparison_block(blocks)
        assert mc['n_strategies'] == 2
        assert mc['selection_caveat'] is True
        assert mc['verdict'] == 'NOT_SIGNIFICANT_AFTER_BONFERRONI'
        assert roi_stats.multi_comparison_block(blocks[:1]) is None


# ------------------------------------------------------ reportability ------

class TestReportable:
    def test_floor_and_ci(self):
        assert roi_stats.is_reportable([1.0, 5.0], 200) is True
        assert roi_stats.is_reportable([1.0, 5.0], 199) is False
        assert roi_stats.is_reportable([-1.0, 5.0], 500) is False
        assert roi_stats.is_reportable([0.0, 5.0], 500) is False
        assert roi_stats.is_reportable([None, None], 500) is False
        assert roi_stats.is_reportable({'ci95': [1.0, 5.0]}, 300) is True

    def test_ci_spans_zero_shapes(self):
        assert roi_stats.ci_spans_zero([-1.0, 2.0]) is True
        assert roi_stats.ci_spans_zero([1.0, 2.0]) is False
        assert roi_stats.ci_spans_zero({'spans_zero': True}) is True
        assert roi_stats.ci_spans_zero({'ci_95': [-1.0, 2.0]}) is True
        assert roi_stats.ci_spans_zero({'ci95': [1.0, 2.0]}) is False
        assert roi_stats.ci_spans_zero(None) is None
        assert roi_stats.ci_spans_zero({}) is None

    def test_reportability_floor_is_single_sourced(self):
        """selection_ledger + shadow_pl_tracker consume the roi_stats floor, never a
        private copy (roi-roadmap task 02). Also proves no import cycle."""
        import shadow_pl_tracker
        import selection_ledger  # noqa: F401  (import-smoke: must not raise / cycle)
        assert shadow_pl_tracker.MIN_BETS_REPORTABLE == roi_stats.MIN_BETS_REPORTABLE == 200


# --------------------------------------------------------- full block ------

class TestSummariseBets:
    def test_known_case_block(self):
        won, odds = _known_series()
        block = roi_stats.summarise_bets(won, odds, commission=0.08,
                                         n_boot=10000, seed=42)
        for key in ('bets', 'wins', 'strike_rate', 'roi_gross', 'roi_net',
                    'se', 'ci95', 'max_drawdown', 'max_losing_streak',
                    'roi_minus_top1', 'roi_minus_top2', 'reportable'):
            assert key in block, key
        assert block['bets'] == 142 and block['wins'] == 14
        assert block['strike_rate'] == pytest.approx(9.86, abs=0.05)
        assert block['roi_gross'] == pytest.approx(12.32, abs=0.5)
        assert block['roi_net'] == pytest.approx(4.19, abs=0.3)
        assert block['reportable'] is False
        assert block['reportable_status'] == 'NOT_REPORTABLE'
        # n = 142 < 200 AND the CI spans zero: both reasons must be given.
        assert '142' in block['reportable_reason']
        assert 'CI' in block['reportable_reason']

    def test_reportable_block(self):
        rng = np.random.default_rng(3)
        won = (rng.random(400) < 0.30).astype(int)
        odds = np.full(400, 4.5)
        block = roi_stats.summarise_bets(won, odds, commission=0.0,
                                         n_boot=5000, seed=42)
        assert block['reportable'] is True
        assert block['reportable_status'] == 'REPORTABLE'

    def test_empty_block(self):
        block = roi_stats.summarise_bets([], [], n_boot=100)
        assert block['bets'] == 0
        assert block['reportable'] is False
        assert block['roi_gross'] is None
