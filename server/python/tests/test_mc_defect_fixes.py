"""Regression pins for the #124 MC-engine defect fixes (audit 2026-08-07).

Each defect is fixed behind its own default-OFF STRIDE_MC_FIX_* flag, per the
sequencing recorded in #124: flag-off behaviour stays identical to the shipped
engine so common-random-numbers A/B against settled results can flip one flag
at a time. These tests therefore pin BOTH sides where the defect is cheap to
reproduce: the defect stays reproducible with the flag off, and the fix
behaves with it on.
"""

import numpy as np
import pytest

import mc_api

racing_system = mc_api.racing_system


class _StubRace:
    is_valid = True
    going = 'Good'
    course = 'randwick'


class _StubModel:
    horse_history = {}

    def _get_horse_key(self, runner):
        return str(runner)


def _analysis(n, styles=None, probs=None):
    styles = styles or ['midfield'] * n
    if probs is None:
        probs = [100.0 / n] * n
    return [{'horse': f'H{i}', 'model_prob': probs[i], 'style': styles[i],
             'runner_obj': None} for i in range(n)]


def _simulate(n=6, sims=2000, seed=7, styles=None, probs=None, **mc_kw):
    return racing_system.simulate_race_monte_carlo(
        _StubRace(), _analysis(n, styles=styles, probs=probs), _StubModel(),
        mc_sims=sims, seed=seed, **mc_kw)


class TestDefect5DeadFields:
    """#124 defect 5: consumer keys that no producer ever wrote."""

    def test_engine_results_never_carried_the_dead_keys(self):
        res = _simulate()
        for key in ('stability', 'pace_regime', 'expected_position',
                    'kelly_stake', 'pace_position'):
            assert key not in res[0], (
                f"'{key}' now exists on MC results — the flag-off legacy "
                f"reads in mc_api stop being dead and would double-publish; "
                f"revisit STRIDE_MC_FIX_DEAD_FIELDS before this ships")

    def test_engine_results_carry_the_keys_the_fix_reads(self):
        res = _simulate()
        for key in ('stability_score', 'style', 'expected_pos'):
            assert key in res[0], f"remapped read source '{key}' missing"
        assert 0 <= res[0]['stability_score'] <= 100
        assert res[0]['expected_pos'] >= 1.0

    def test_style_ordinal_matches_banker_detector_contract(self):
        # banker_detector reads running_style_score as: >=2 leader-ish,
        # ==0 backmarker, anything else mid-pack.
        assert mc_api._style_ordinal('leader') >= 2
        assert mc_api._style_ordinal('on_pace') >= 2
        assert mc_api._style_ordinal('backmarker') == 0
        assert mc_api._style_ordinal('midfield') == 1
        assert mc_api._style_ordinal('handy') == 1
        assert mc_api._style_ordinal(None) == 1
        assert mc_api._style_ordinal('never-seen-style') == 1

    def test_display_kelly_delegates_to_engine_formula(self):
        assert mc_api._display_kelly(0.3, 5.0) == pytest.approx(
            racing_system.kelly_stake(0.3, 5.0))

    def test_display_kelly_zero_on_unusable_inputs(self):
        assert mc_api._display_kelly(0.0, 5.0) == 0.0
        assert mc_api._display_kelly(0.3, 1.0) == 0.0
        assert mc_api._display_kelly(0.3, None) == 0.0
        assert mc_api._display_kelly(None, 5.0) == 0.0

    def test_staking_dict_carries_kelly_pct_for_the_tip_formatter(self):
        # The batch tip formatter's remapped kellyStake read.
        cand = {'win_prob_sim': 0.2, 'market_odds': 8.0,
                'stability_score': 60.0, 'ev': 0.2}
        staking = racing_system.mc_compute_staking(
            cand, 'fractional_kelly', 10, 1000, 10, 10)
        assert 'kelly_pct' in staking
        assert staking['kelly_pct'] > 0

    def test_fix_flags_default_off(self, monkeypatch):
        for flag in ('STRIDE_MC_FIX_DEAD_FIELDS',
                     'STRIDE_MC_FIX_SCENARIO_RANGES',
                     'STRIDE_MC_FIX_STABILITY_FIELDSIZE',
                     'STRIDE_MC_FIX_ENERGY_SIGN',
                     'STRIDE_MC_FIX_DIRICHLET_CONC'):
            monkeypatch.delenv(flag, raising=False)
            assert not mc_api._flag_enabled(flag)
            monkeypatch.setenv(flag, 'true')
            assert mc_api._flag_enabled(flag)
            monkeypatch.setenv(flag, 'false')
            assert not mc_api._flag_enabled(flag)


class TestDefect3ScenarioRanges:
    """#124 defect 3: unsampled pace regimes read as genuine 0% win rates.

    pace_mode='off' samples exactly one regime ('even'), the cleanest
    deterministic way to leave three phantom zero rows in scenario_win.
    """

    def test_flag_off_range_is_the_favourites_own_win_rate(self, monkeypatch):
        monkeypatch.delenv('STRIDE_MC_FIX_SCENARIO_RANGES', raising=False)
        res = _simulate(probs=[60, 8, 8, 8, 8, 8], pace_mode='off')
        fav = res[0]
        # max over regimes is the sampled row, min is a phantom zero row, so
        # the "range" is the favourite's own win rate — and at >28.6% that
        # zeroes scenario stability. This is the shipped defect; if this pin
        # breaks, flag-off behaviour changed without the flag.
        assert fav['scenario_sensitivity'] == pytest.approx(fav['win_prob_sim'])
        assert fav['win_prob_sim'] > 0.35

    def test_flag_on_single_regime_means_no_observed_variation(self, monkeypatch):
        monkeypatch.setenv('STRIDE_MC_FIX_SCENARIO_RANGES', 'true')
        res = _simulate(probs=[60, 8, 8, 8, 8, 8], pace_mode='off')
        assert all(r['scenario_sensitivity'] == 0.0 for r in res)

    def test_flag_on_raises_stability_never_lowers_it(self, monkeypatch):
        monkeypatch.delenv('STRIDE_MC_FIX_SCENARIO_RANGES', raising=False)
        off = _simulate(probs=[60, 8, 8, 8, 8, 8], pace_mode='off')
        monkeypatch.setenv('STRIDE_MC_FIX_SCENARIO_RANGES', 'true')
        on = _simulate(probs=[60, 8, 8, 8, 8, 8], pace_mode='off')
        # Same seed, same draws: dropping phantom zero rows can only shrink
        # the range, so stability_score is monotonically >= the defect's.
        for r_off, r_on in zip(off, on):
            assert r_on['scenario_sensitivity'] <= r_off['scenario_sensitivity']
            assert r_on['stability_score'] >= r_off['stability_score']

    def test_flag_on_multi_regime_range_still_reports_real_spread(self, monkeypatch):
        monkeypatch.setenv('STRIDE_MC_FIX_SCENARIO_RANGES', 'true')
        # All-leader field: pressure 1.0 forces fast/melt sampling, two real
        # regimes, so a genuine spread must survive the fix.
        res = _simulate(styles=['leader'] * 6, pace_mode='basic')
        assert any(r['scenario_sensitivity'] > 0.0 for r in res)
