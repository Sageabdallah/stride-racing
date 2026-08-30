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
        # Half-Kelly, deliberately matching mc_compute_staking's default
        # fractional_kelly mode so the two endpoints publish comparable
        # kellyStake numbers (audit follow-up on the #124 defect-5 fix).
        assert mc_api._display_kelly(0.3, 5.0) == pytest.approx(
            racing_system.kelly_stake(0.3, 5.0, fraction=0.5))
        assert mc_api._display_kelly(0.2, 8.0) == pytest.approx(
            racing_system.mc_compute_staking(
                {'win_prob_sim': 0.2, 'market_odds': 8.0,
                 'stability_score': 60.0, 'ev': 0.2},
                'fractional_kelly', 10, 1000, 10, 10)['kelly_pct'] / 100.0)

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


class TestDefect4StabilityFieldSize:
    """#124 defect 4: stability_from_positions punishes field size itself."""

    UNIFORM_STD_8 = np.sqrt((8 ** 2 - 1) / 12.0)     # 2.2913
    UNIFORM_STD_14 = np.sqrt((14 ** 2 - 1) / 12.0)   # 4.0311

    def test_flag_off_big_fields_zero_out(self, monkeypatch):
        monkeypatch.delenv('STRIDE_MC_FIX_STABILITY_FIELDSIZE', raising=False)
        # A maximally unpredictable runner in each field size, same ci.
        s8 = racing_system.stability_from_positions(self.UNIFORM_STD_8, 0.05)
        s14 = racing_system.stability_from_positions(self.UNIFORM_STD_14, 0.05)
        # The shipped defect: identical (relative) chaos scores ~16 in an
        # 8-field but 0 in a 14-field purely because the field is bigger.
        assert s8 > 0
        assert s14 == 0.0

    def test_flag_off_ignores_field_size_entirely(self, monkeypatch):
        monkeypatch.delenv('STRIDE_MC_FIX_STABILITY_FIELDSIZE', raising=False)
        legacy = racing_system.stability_from_positions(3.0, 0.1)
        assert racing_system.stability_from_positions(3.0, 0.1, field_size=14) == legacy
        assert racing_system.stability_from_positions(3.0, 0.1, field_size=6) == legacy

    def test_flag_on_same_relative_volatility_scores_the_same(self, monkeypatch):
        monkeypatch.setenv('STRIDE_MC_FIX_STABILITY_FIELDSIZE', 'true')
        s8 = racing_system.stability_from_positions(
            self.UNIFORM_STD_8, 0.05, field_size=8)
        s14 = racing_system.stability_from_positions(
            self.UNIFORM_STD_14, 0.05, field_size=14)
        assert s14 == pytest.approx(s8, abs=1e-9)

    def test_flag_on_is_identity_at_the_8_horse_anchor(self, monkeypatch):
        monkeypatch.delenv('STRIDE_MC_FIX_STABILITY_FIELDSIZE', raising=False)
        legacy = racing_system.stability_from_positions(1.8, 0.07)
        monkeypatch.setenv('STRIDE_MC_FIX_STABILITY_FIELDSIZE', 'true')
        anchored = racing_system.stability_from_positions(1.8, 0.07, field_size=8)
        assert anchored == pytest.approx(legacy)

    def test_flag_on_a_14_field_can_pass_the_playability_gate(self, monkeypatch):
        monkeypatch.delenv('STRIDE_MC_FIX_STABILITY_FIELDSIZE', raising=False)
        off = _simulate(n=14, probs=[35] + [5] * 13)
        monkeypatch.setenv('STRIDE_MC_FIX_STABILITY_FIELDSIZE', 'true')
        on = _simulate(n=14, probs=[35] + [5] * 13)
        for r_off, r_on in zip(off, on):
            assert r_on['stability_score'] >= r_off['stability_score']
        # mc_is_playable / staking gate at stability >= 45: the defect kept
        # whole 10+ fields under it; the fix must let a solid favourite back
        # over the line.
        assert max(r['stability_score'] for r in off) < 45
        assert on[0]['stability_score'] >= 45


class TestDefect1EnergySign:
    """#124 defect 1: (mu + noise + style) * energy with mu = log(p) < 0
    shrinks the deficit as energy depletes — burning energy RAISED scores.

    Style advantages are zeroed via monkeypatch so leader vs backmarker
    differ only through energy depletion; under a hot pace leaders burn
    hardest, so the sign of mean(leader) - mean(backmarker) isolates the
    direction of the energy term.
    """

    @staticmethod
    def _phase_scores(monkeypatch, flag):
        import realistic_simulate as rsim
        if flag:
            monkeypatch.setenv('STRIDE_MC_FIX_ENERGY_SIGN', 'true')
        else:
            monkeypatch.delenv('STRIDE_MC_FIX_ENERGY_SIGN', raising=False)
        monkeypatch.setattr(rsim, 'PHASE_STYLE_MATRIX', {
            k: {s: 0.0 for s in v} for k, v in rsim.PHASE_STYLE_MATRIX.items()})
        n = 2
        mu = np.log(np.array([0.25, 0.25]))
        sigmas = np.array([0.01, 0.01])
        scores = rsim.simulate_multi_phase(
            mu, sigmas, ['leader', 'backmarker'], 1200, 4000, n,
            np.random.default_rng(11), pace_scenario='hot')
        return scores.mean(axis=0)

    def test_flag_off_burning_energy_still_raises_the_score(self, monkeypatch):
        leader, backmarker = self._phase_scores(monkeypatch, flag=False)
        # The shipped defect, pinned: under a hot pace the leader burns more
        # energy yet scores HIGHER than the identical-ability backmarker.
        assert leader > backmarker

    def test_flag_on_burning_energy_costs_performance(self, monkeypatch):
        leader, backmarker = self._phase_scores(monkeypatch, flag=True)
        assert leader < backmarker

    def test_flag_on_full_energy_is_a_no_op(self, monkeypatch):
        import realistic_simulate as rsim
        monkeypatch.setenv('STRIDE_MC_FIX_ENERGY_SIGN', 'true')
        monkeypatch.setattr(rsim, 'ENERGY_DEPLETION', {
            k: [0.0, 0.0, 0.0, 0.0] for k in rsim.ENERGY_DEPLETION})
        mu = np.log(np.array([0.3, 0.3]))
        sigmas = np.array([0.0, 0.0])
        on = rsim.simulate_multi_phase(
            mu, sigmas, ['leader', 'backmarker'], 1200, 50, 2,
            np.random.default_rng(3))
        monkeypatch.delenv('STRIDE_MC_FIX_ENERGY_SIGN')
        off = rsim.simulate_multi_phase(
            mu, sigmas, ['leader', 'backmarker'], 1200, 50, 2,
            np.random.default_rng(3))
        # log(1.0) == 0 and * 1.0 are both identity: with no depletion the
        # two formulations must agree exactly.
        assert np.allclose(on, off)


class _ExperiencedModel:
    """Two identical-probability horses; H0 has 40 career runs, H1 none."""

    def __init__(self):
        self.horse_history = {'R0': {'runs': [1] * 40}, 'R1': {'runs': []}}

    def _get_horse_key(self, runner):
        return str(runner)


def _simulate_evidence_pair(sims=6000, seed=13, **mc_kw):
    analysis = [
        {'horse': 'H0', 'model_prob': 50.0, 'style': 'midfield', 'runner_obj': 'R0'},
        {'horse': 'H1', 'model_prob': 50.0, 'style': 'midfield', 'runner_obj': 'R1'},
    ]
    return racing_system.simulate_race_monte_carlo(
        _StubRace(), analysis, _ExperiencedModel(), mc_sims=sims, seed=seed,
        **mc_kw)


class TestDefect2DirichletConcentration:
    """#124 defect 2: per-horse Dirichlet concentration converges to
    p_i * (12 + 1.3 * runs_i) renormalised, not to p_i."""

    def test_flag_off_experience_reweights_equal_probabilities(self, monkeypatch):
        monkeypatch.delenv('STRIDE_MC_FIX_DIRICHLET_CONC', raising=False)
        res = _simulate_evidence_pair()
        # Two 50% horses; conc 64 vs 12 recentres the mean to 64/76 = 84%.
        # The shipped defect, pinned: the experienced horse wins far more.
        assert res[0]['win_prob_sim'] - res[1]['win_prob_sim'] > 0.15

    def test_flag_on_equal_probabilities_stay_equal(self, monkeypatch):
        monkeypatch.setenv('STRIDE_MC_FIX_DIRICHLET_CONC', 'true')
        res = _simulate_evidence_pair()
        assert abs(res[0]['win_prob_sim'] - res[1]['win_prob_sim']) < 0.05

    def test_flag_on_dirichlet_model_path_fixed_too(self, monkeypatch):
        monkeypatch.delenv('STRIDE_MC_FIX_DIRICHLET_CONC', raising=False)
        off = _simulate_evidence_pair(mc_model='dirichlet')
        assert off[0]['win_prob_sim'] - off[1]['win_prob_sim'] > 0.15
        monkeypatch.setenv('STRIDE_MC_FIX_DIRICHLET_CONC', 'true')
        on = _simulate_evidence_pair(mc_model='dirichlet')
        assert abs(on[0]['win_prob_sim'] - on[1]['win_prob_sim']) < 0.05

    def test_flag_on_evidence_still_sets_tightness_as_a_scalar(self):
        # Evidence keeps its real job — setting how tight the noise is —
        # but as one scalar per field so it cannot recentre the mean.
        # (Aggregate win rates are provably invariant to concentration for
        # a fixed p, so this is pinned at the concentration itself.)
        experienced = racing_system._dirichlet_concentration(
            np.array([40.0, 40.0]), True)
        fresh = racing_system._dirichlet_concentration(np.zeros(2), True)
        mixed = racing_system._dirichlet_concentration(
            np.array([40.0, 0.0]), True)
        assert isinstance(experienced, float)
        assert experienced == pytest.approx(64.0)
        assert fresh == pytest.approx(12.0)
        assert mixed == pytest.approx(38.0)
        assert experienced > mixed > fresh

    def test_flag_off_concentration_keeps_the_legacy_shape(self):
        evidence = np.array([40.0, 0.0])
        legacy = racing_system._dirichlet_concentration(evidence, False)
        assert np.array_equal(legacy, np.maximum(6.0, 12.0 + 1.3 * evidence))

    def test_flag_on_thin_regimes_do_not_resurrect_the_phantom(self, monkeypatch):
        # Half leaders puts pressure at ~0.5, so fast/melt are drawn only on
        # >2.4-sigma noise — a handful of sims whose per-runner "win rates"
        # are 0 or 1 by construction. Counting those as sampled would bring
        # the phantom range straight back; the min-draws floor excludes them.
        styles = ['leader', 'leader', 'leader',
                  'midfield', 'midfield', 'midfield']
        monkeypatch.setenv('STRIDE_MC_FIX_SCENARIO_RANGES', 'true')
        on = _simulate(probs=[60, 8, 8, 8, 8, 8], styles=styles)
        assert all(r['scenario_sensitivity'] == 0.0 for r in on)
        monkeypatch.delenv('STRIDE_MC_FIX_SCENARIO_RANGES')
        off = _simulate(probs=[60, 8, 8, 8, 8, 8], styles=styles)
        assert off[0]['scenario_sensitivity'] > 0.3
