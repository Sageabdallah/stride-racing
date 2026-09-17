"""Shadow display parity, actual emit paths and unchanged live stake bytes."""
import copy
import json
from types import SimpleNamespace

import pytest
import mc_api
from portfolio_risk import shadow_kelly_display, shadow_stake_plan
from run_tips_pipeline import build_export_pick, compute_staking
from selection_ledger import build_ledger_row


def test_flag_on_display_uses_shadow_fraction_cap_and_commission(monkeypatch):
    monkeypatch.setenv('STRIDE_MC_FIX_DEAD_FIELDS', 'true')
    monkeypatch.setenv('STRIDE_COMMISSION_RATE', '0.08')
    for p, odds, fair in ((0.3, 5, 6.25), (0.9, 5, None), (1.0, 5, None), (0.1, 5, None)):
        plan = shadow_stake_plan(p, odds, market_probability=1/fair if fair else None,
                                 commission_rate=0.08)
        display = shadow_kelly_display(p, odds, fair)
        assert mc_api._display_kelly(p, odds, fair) * 100 == pytest.approx(plan['stake_pct'])
        assert display['kellyStake'] == plan['stake_pct']
        assert display['kellyStakeApplied'] is False and plan['applied'] is False
        assert display['kellyStakeType'] == 'shadow_estimate'
        assert display['kellyStakeUnits'] == 'percent_bankroll'
        assert 'not a placed stake' in display['kellyStakeLabel']
    assert shadow_kelly_display(0.205, 5)['kellyStake'] == 0.0  # gross edge, negative net


@pytest.mark.parametrize('fair', [None, 0, 1, 'bad', float('nan'), float('inf')])
def test_display_records_market_fallback(fair):
    display = shadow_kelly_display(0.3, 5, fair)
    assert display['kellyMarketProbabilitySource'] == 'raw_implied_odds'
    assert display['kellyStake'] == shadow_stake_plan(0.3, 5, commission_rate=0.08)['stake_pct']


def test_export_refreshes_stale_api_number_using_final_pick_inputs(monkeypatch):
    monkeypatch.setenv('STRIDE_MC_FIX_DEAD_FIELDS', 'true')
    horse = {'horse': 'A', 'winPercentage': 30.04, 'marketOdds': 5, 'fairOdds': 6.25,
             'staking': '1u', 'kellyStake': 5.0, 'ciLower': 24}
    before = copy.deepcopy(horse)
    pick = build_export_pick(horse)
    plan = shadow_stake_plan(pick['win_pct']/100, pick['odds'],
                             market_probability=1/pick['fair_odds'], commission_rate=0.08)
    assert pick['kellyStake'] == pick['_mc_data']['kellyStake'] == plan['stake_pct'] == 0.9375
    assert pick['kellyMarketProbabilitySource'] == 'fair_odds'
    assert pick['_mc_data']['ciLower'] == 24 and horse == before
    assert pick['staking'] == '1u'


def test_display_matches_ledger_on_this_independent_branch(monkeypatch):
    monkeypatch.setenv('STRIDE_MC_FIX_DEAD_FIELDS', 'true')
    monkeypatch.setenv('STRIDE_SHADOW_KELLY', 'true')
    # The de-vigged ledger input change lives in the independent step-2 PR.
    # At a fair book both the old and new ledger inputs are 1/odds.
    pick = build_export_pick({'horse': 'A', 'winPercentage': 30, 'marketOdds': 5,
                              'fairOdds': 5, 'staking': '1u'})
    row = build_ledger_row(pick, {'race_date': '2026-09-01'})
    assert pick['kellyStake'] == row['shadow_kelly']['stake_pct']
    assert row['shadow_kelly']['applied'] is False and row['stake'] == 100


def test_live_staking_bytes_unchanged_by_both_flags(monkeypatch):
    for flat in ('true', 'false'):
        monkeypatch.setenv('STRIDE_FLAT_STAKING', flat)
        snapshots = []
        for display in ('false', 'true'):
            for shadow in ('false', 'true'):
                monkeypatch.setenv('STRIDE_MC_FIX_DEAD_FIELDS', display)
                monkeypatch.setenv('STRIDE_SHADOW_KELLY', shadow)
                payload = []
                for confidence in ('high', 'medium', 'low'):
                    horse = {'horse': 'A', 'confidence': confidence, 'winPercentage': 30,
                             'marketOdds': 5, 'fairOdds': 6.25}
                    horse['staking'] = compute_staking(horse)
                    pick = build_export_pick(horse)
                    row = build_ledger_row(pick, {})
                    payload.append([horse['staking'], pick['staking'], row['stake']])
                snapshots.append(json.dumps(payload).encode())
        assert all(value == snapshots[0] for value in snapshots)
        expected = [['1u','1u',100.0], ['1u','1u',100.0], ['0u','0u',0.0]]
        if flat == 'false':
            expected[0] = ['2u','2u',200.0]
        assert snapshots[0] == json.dumps(expected).encode()


def test_flag_off_refresh_is_byte_identical(monkeypatch):
    monkeypatch.setenv('STRIDE_MC_FIX_DEAD_FIELDS', 'false')
    rows = [{'horse': 'A', 'marketOdds': 5, 'winPercentage': 30, 'kellyStake': 4.2}]
    before = json.dumps(rows).encode()
    mc_api._refresh_shadow_kelly(rows, [5, 2])
    assert json.dumps(rows).encode() == before
    pick = build_export_pick({'horse': 'A', 'kellyStake': 4.2, 'staking': '1u'})
    assert 'kellyStake' not in pick and 'kellyStakeType' not in pick['_mc_data']


def test_batch_emit_uses_full_book_and_never_engine_stake(monkeypatch):
    monkeypatch.setenv('STRIDE_MC_FIX_DEAD_FIELDS', 'true')
    tip = {'horse': 'A', 'race_number': 1, 'market_odds': 5,
           'mc': {'win_prob_sim': 0.30, 'ci_lower': 0.24},
           'staking': {'kelly_pct': 99, 'status': 'WATCH'}, 'kelly_stake': 0.04}
    original = copy.deepcopy(tip)
    monkeypatch.setattr(mc_api, 'get_or_build_base_model', lambda: object())
    monkeypatch.setattr(mc_api, 'create_race_from_api_data', lambda *a: SimpleNamespace(is_valid=True))
    monkeypatch.setattr(mc_api, 'TipGenerator', lambda *a, **kw: SimpleNamespace(
        generate_mc_tips_for_date=lambda *a, **kw: {'Track': [tip]}))
    monkeypatch.setattr(mc_api, 'parse_real_odds', lambda r: r.get('odds'))
    races = [{'track': 'Track', 'race_number': 1,
              'runners': [{'horse': 'A', 'odds': 5}, {'horse': 'B', 'odds': 2}]}]
    result = mc_api.run_batch_simulation(races)
    row = result['tips']['Track'][0]
    expected = shadow_kelly_display(0.30, 5, 3.5)
    assert all(row[key] == value for key, value in expected.items())
    assert row['confidence'] == 'WATCH' and tip == original
    monkeypatch.setenv('STRIDE_MC_FIX_DEAD_FIELDS', 'false')
    off = mc_api.run_batch_simulation(races)['tips']['Track'][0]
    assert off['kellyStake'] == 4 and 'kellyStakeType' not in off


def test_single_race_emit_uses_final_normalised_probability(monkeypatch):
    monkeypatch.setenv('STRIDE_MC_FIX_DEAD_FIELDS', 'true')
    for flag in ('MC_RECALIBRATION_AVAILABLE', 'SECTIONAL_MC_AVAILABLE', 'FRANKING_AVAILABLE',
                 'ML_MODEL_AVAILABLE', 'ENHANCED_FEATURES_AVAILABLE', 'TRACK_BIAS_AVAILABLE',
                 'FITNESS_PEAK_AVAILABLE', 'BANKER_DETECTOR_AVAILABLE'):
        monkeypatch.setattr(mc_api, flag, False)
    analysis = [{'horse': 'A', 'model_prob': 8, 'market_odds': 10},
                {'horse': 'B', 'model_prob': 40, 'market_odds': 2}]
    model = SimpleNamespace(analyze=lambda race: copy.deepcopy(analysis))
    monkeypatch.setattr(mc_api, 'get_or_build_base_model', lambda: model)
    monkeypatch.setattr(mc_api, 'create_race_from_api_data', lambda *a: SimpleNamespace(is_valid=True))
    monkeypatch.setattr(mc_api, 'simulate_race_monte_carlo', lambda *a, **kw: [
        {'horse': 'A', 'win_prob_sim': 0.08}, {'horse': 'B', 'win_prob_sim': 0.40}])
    monkeypatch.setattr(mc_api, 'parse_real_odds', lambda r: r.get('odds'))
    monkeypatch.setattr(mc_api, 'extract_all_sophisticated_features', lambda *a, **kw: {})
    monkeypatch.setattr(mc_api, 'calculate_ml_probability_adjustment', lambda *a, **kw: (1.0, {}))
    monkeypatch.setattr(mc_api, 'calculate_sophisticated_adjustment', lambda *a: 1.0)
    monkeypatch.setattr(mc_api, '_mc_audit_write_enabled', lambda: False)
    runners = [{'horse': 'A', 'odds': 10}, {'horse': 'B', 'odds': 2}]
    result = mc_api.run_simulation({'track': 'Track', 'race_number': 1}, runners, mc_sims=10)
    assert result.get('success'), result
    row = next(r for r in result['results'] if r['horse'] == 'A')
    # A presence/type check would pass the stale pre-normalisation number (0).
    assert row['winPercentage'] == 16.67
    expected = shadow_kelly_display(0.1667, 10, 6.0)
    assert expected['kellyStake'] > 0
    assert all(row[key] == value for key, value in expected.items())
    monkeypatch.setenv('STRIDE_MC_FIX_DEAD_FIELDS', 'false')
    off = mc_api.run_simulation({'track': 'Track', 'race_number': 1}, runners, mc_sims=10)
    assert off.get('success'), off
    assert all(r['kellyStake'] == 0 and 'kellyStakeType' not in r for r in off['results'])
