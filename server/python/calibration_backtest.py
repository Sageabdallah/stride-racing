#!/usr/bin/env python3
"""Sectional-profile Monte Carlo calibration backtest engine that pulls historical races with both sectional data and known results."""

import sys, os, time, json, math
import numpy as np
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

import psycopg2
import psycopg2.extras

def get_db_connection():
    return psycopg2.connect(os.environ['DATABASE_URL'])


def fetch_backtest_races(min_runners_with_sectionals=5, max_races=200):
    """Pull races where we have both results AND enough sectional coverage."""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        WITH race_sectional_coverage AS (
            SELECT r.race_id, 
                   COUNT(DISTINCT CASE WHEN s.splits_json IS NOT NULL THEN r.horse_name END) as sectional_runners,
                   COUNT(*) as total_runners
            FROM race_results_history r
            LEFT JOIN sectional_times s ON LOWER(s.horse_name) = LOWER(r.horse_name)
            WHERE r.position IS NOT NULL 
              AND r.sp_odds IS NOT NULL 
              AND r.sp_odds > 1.0
              AND r.field_size >= 6
            GROUP BY r.race_id
            HAVING COUNT(DISTINCT CASE WHEN s.splits_json IS NOT NULL THEN r.horse_name END) >= %s
        )
        SELECT r.race_id, r.horse_name, r.position, r.sp_odds, r.margin_lengths,
               r.barrier, r.weight_kg, r.jockey, r.distance_m, r.track, r.field_size,
               r.race_date, r.form_string, r.race_class
        FROM race_results_history r
        INNER JOIN race_sectional_coverage c ON r.race_id = c.race_id
        WHERE r.position IS NOT NULL 
          AND r.sp_odds IS NOT NULL 
          AND r.sp_odds > 1.0
        ORDER BY r.race_date DESC, r.race_id, r.position
    """, (min_runners_with_sectionals,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    races = defaultdict(list)
    for row in rows:
        races[row['race_id']].append(dict(row))

    race_list = list(races.items())
    if max_races and len(race_list) > max_races:
        race_list = race_list[:max_races]

    return dict(race_list)


def build_market_probs(runners):
    """Convert SP odds to implied probabilities (overround-adjusted)."""
    odds = [r['sp_odds'] for r in runners]
    implied = [1.0 / o for o in odds]
    total = sum(implied)
    return np.array([p / total for p in implied])


def run_backtest_config(races, config, mc_module, sim_module):
    """Run a single configuration through all races and collect predictions."""
    blend_ratio = config['blend_ratio']
    speed_threshold = config['speed_threshold']
    use_strict_speed_class = config.get('use_strict_speed_class', False)
    use_recalibration = config.get('recalibrate', False)
    n_sims = config.get('n_sims', 10000)

    recalibrator = None
    if use_recalibration:
        from mc_recalibration import MCRecalibrator
        recalibrator = MCRecalibrator()
        if not recalibrator.load():
            recalibrator = None

    all_predictions = []
    all_actuals = []
    all_odds = []
    all_market_probs = []
    races_processed = 0
    races_skipped = 0
    total_sectional_enhanced = 0

    for race_id, runners in races.items():
        if len(runners) < 4:
            races_skipped += 1
            continue

        market_probs = build_market_probs(runners)

        pace_profiles = []
        running_profiles = []
        for r in runners:
            pp = mc_module.build_horse_pace_profile(r['horse_name'])
            rp = mc_module.build_horse_running_profile(r['horse_name'])
            pace_profiles.append(pp)
            running_profiles.append(rp)

        sim_runners = []
        for r in runners:
            sim_runners.append({
                'horse': r['horse_name'],
                'form': r.get('form_string', ''),
                'market_odds': r['sp_odds'],
                'jockey': r.get('jockey', ''),
            })

        base_result = sim_module.simulate_race_realistic(
            sim_runners, market_probs, n_sims=n_sims, seed=42
        )
        base_win_probs = base_result['sim_win_probs']

        if recalibrator:
            base_win_probs = recalibrator.transform_race(base_win_probs)

        has_profiles = sum(1 for pp in pace_profiles if pp.get('has_profile', False))
        sectional_enhanced = False

        if has_profiles >= 2 and blend_ratio > 0:
            try:
                enhanced_result = sim_module.simulate_race_with_sectional_profiles(
                    sim_runners, market_probs, pace_profiles, running_profiles,
                    race_distance_m=runners[0].get('distance_m', 1400),
                    n_sims=n_sims, seed=42,
                    speed_threshold_pct=speed_threshold,
                    use_strict_speed_class=use_strict_speed_class
                )
                if enhanced_result.get('sectional_enhanced', False):
                    sect_probs = enhanced_result['sim_win_probs']
                    if recalibrator:
                        sect_probs = recalibrator.transform_race(sect_probs)
                    blended = blend_ratio * sect_probs + (1 - blend_ratio) * base_win_probs
                    blended = np.clip(blended, 0.001, 0.999)
                    blended = blended / blended.sum()
                    final_probs = blended
                    sectional_enhanced = True
                    total_sectional_enhanced += 1
                else:
                    final_probs = base_win_probs
            except Exception as e:
                final_probs = base_win_probs
        else:
            final_probs = base_win_probs

        for i, r in enumerate(runners):
            won = 1 if r['position'] == 1 else 0
            all_predictions.append(final_probs[i])
            all_actuals.append(won)
            all_odds.append(r['sp_odds'])
            all_market_probs.append(market_probs[i])

        races_processed += 1

    return {
        'predictions': np.array(all_predictions),
        'actuals': np.array(all_actuals),
        'odds': np.array(all_odds),
        'market_probs': np.array(all_market_probs),
        'races_processed': races_processed,
        'races_skipped': races_skipped,
        'sectional_enhanced': total_sectional_enhanced,
    }


def calculate_metrics(result, config_label=""):
    """Calculate all accuracy and profitability metrics."""
    preds = result['predictions']
    actuals = result['actuals']
    odds = result['odds']
    market_probs = result['market_probs']

    n = len(preds)
    n_winners = int(actuals.sum())
    n_races = result['races_processed']

    eps = 1e-10
    log_loss = -np.mean(actuals * np.log(preds + eps) + (1 - actuals) * np.log(1 - preds + eps))

    brier = np.mean((preds - actuals) ** 2)

    market_log_loss = -np.mean(actuals * np.log(market_probs + eps) + (1 - actuals) * np.log(1 - market_probs + eps))
    market_brier = np.mean((market_probs - actuals) ** 2)

    bands = [(0, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.30), (0.30, 0.50), (0.50, 1.0)]
    calibration = []
    for low, high in bands:
        mask = (preds >= low) & (preds < high)
        if mask.sum() > 0:
            predicted_avg = preds[mask].mean()
            actual_rate = actuals[mask].mean()
            count = int(mask.sum())
            calibration.append({
                'band': f'{low:.0%}-{high:.0%}',
                'predicted': round(predicted_avg * 100, 2),
                'actual': round(actual_rate * 100, 2),
                'gap': round((predicted_avg - actual_rate) * 100, 2),
                'count': count,
            })

    edge_thresholds = [0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20]
    roi_results = []
    for edge_min in edge_thresholds:
        edge = preds - (1.0 / odds)
        mask = edge >= edge_min
        if mask.sum() > 5:
            bets = mask.sum()
            wins = (actuals[mask] == 1).sum()
            returns = (actuals[mask] * odds[mask]).sum()
            staked = float(bets)
            roi = (returns - staked) / staked * 100
            strike = wins / bets * 100
            roi_results.append({
                'min_edge': f'{edge_min:.0%}',
                'bets': int(bets),
                'wins': int(wins),
                'strike_rate': round(strike, 1),
                'roi': round(roi, 1),
                'profit': round(returns - staked, 2),
            })

    top_n_results = []
    race_preds = {}
    idx = 0
    for race_id in sorted(set()):
        pass

    return {
        'label': config_label,
        'n_runners': n,
        'n_winners': n_winners,
        'n_races': n_races,
        'sectional_enhanced': result['sectional_enhanced'],
        'log_loss': round(log_loss, 5),
        'brier_score': round(brier, 5),
        'market_log_loss': round(market_log_loss, 5),
        'market_brier': round(market_brier, 5),
        'log_loss_improvement': round((market_log_loss - log_loss) / market_log_loss * 100, 2),
        'brier_improvement': round((market_brier - brier) / market_brier * 100, 2),
        'calibration': calibration,
        'roi_by_edge': roi_results,
    }


def run_full_calibration(max_races=150):
    """Run calibration across multiple configurations and find the optimal one."""
    import mc_api as mc_module
    import realistic_simulate as sim_module

    print("=" * 70)
    print("SECTIONAL MC CALIBRATION BACKTEST ENGINE")
    print("=" * 70)

    mc_module._pace_profile_cache = {}
    mc_module._speed_map_cache = {}

    print("\n[1/4] Fetching historical races with sectional coverage...")
    t0 = time.time()
    races = fetch_backtest_races(min_runners_with_sectionals=4, max_races=max_races)
    print(f"  Loaded {len(races)} races in {time.time()-t0:.1f}s")

    total_runners = sum(len(r) for r in races.values())
    print(f"  Total runners: {total_runners}")

    configs = [
        {'label': 'baseline_market', 'blend_ratio': 0.0, 'speed_threshold': 1.03, 'n_sims': 5000},
        {'label': 'blend_30_70', 'blend_ratio': 0.30, 'speed_threshold': 1.03, 'n_sims': 5000},
        {'label': 'blend_40_60', 'blend_ratio': 0.40, 'speed_threshold': 1.03, 'n_sims': 5000},
        {'label': 'blend_50_50', 'blend_ratio': 0.50, 'speed_threshold': 1.03, 'n_sims': 5000},
        {'label': 'blend_60_40', 'blend_ratio': 0.60, 'speed_threshold': 1.03, 'n_sims': 5000},
        {'label': 'blend_70_30', 'blend_ratio': 0.70, 'speed_threshold': 1.03, 'n_sims': 5000},
        {'label': 'blend_80_20', 'blend_ratio': 0.80, 'speed_threshold': 1.03, 'n_sims': 5000},
        {'label': 'thresh_101', 'blend_ratio': 0.60, 'speed_threshold': 1.01, 'n_sims': 5000},
        {'label': 'thresh_105', 'blend_ratio': 0.60, 'speed_threshold': 1.05, 'n_sims': 5000},
        {'label': 'strict_speed_103', 'blend_ratio': 0.60, 'speed_threshold': 1.03, 'use_strict_speed_class': True, 'n_sims': 5000},
        {'label': 'strict_speed_101', 'blend_ratio': 0.60, 'speed_threshold': 1.01, 'use_strict_speed_class': True, 'n_sims': 5000},
        {'label': 'optimal_combo', 'blend_ratio': 0.30, 'speed_threshold': 1.01, 'use_strict_speed_class': True, 'n_sims': 5000},
        {'label': 'optimal_combo_40', 'blend_ratio': 0.40, 'speed_threshold': 1.01, 'use_strict_speed_class': True, 'n_sims': 5000},
        {'label': 'RECAL_baseline', 'blend_ratio': 0.0, 'speed_threshold': 1.03, 'recalibrate': True, 'n_sims': 5000},
        {'label': 'RECAL_blend30', 'blend_ratio': 0.30, 'speed_threshold': 1.01, 'use_strict_speed_class': True, 'recalibrate': True, 'n_sims': 5000},
        {'label': 'RECAL_blend40', 'blend_ratio': 0.40, 'speed_threshold': 1.01, 'use_strict_speed_class': True, 'recalibrate': True, 'n_sims': 5000},
        {'label': 'RECAL_blend50', 'blend_ratio': 0.50, 'speed_threshold': 1.01, 'use_strict_speed_class': True, 'recalibrate': True, 'n_sims': 5000},
        {'label': 'RECAL_blend60', 'blend_ratio': 0.60, 'speed_threshold': 1.01, 'use_strict_speed_class': True, 'recalibrate': True, 'n_sims': 5000},
    ]

    print(f"\n[2/4] Running {len(configs)} configurations...")
    all_metrics = []

    for i, config in enumerate(configs):
        t1 = time.time()
        label = config['label']
        print(f"\n  [{i+1}/{len(configs)}] {label}...", end='', flush=True)

        result = run_backtest_config(races, config, mc_module, sim_module)
        metrics = calculate_metrics(result, config_label=label)
        all_metrics.append(metrics)

        dt = time.time() - t1
        print(f" done ({dt:.1f}s) | LL={metrics['log_loss']:.5f} Brier={metrics['brier_score']:.5f} Enhanced={metrics['sectional_enhanced']}/{metrics['n_races']}")

    print(f"\n\n[3/4] RESULTS COMPARISON")
    print("=" * 70)

    print(f"\n{'Config':<25s} {'Log-Loss':>10s} {'Brier':>10s} {'LL Imp%':>10s} {'Brier Imp%':>10s} {'Enhanced':>10s}")
    print("-" * 75)
    for m in all_metrics:
        print(f"{m['label']:<25s} {m['log_loss']:>10.5f} {m['brier_score']:>10.5f} {m['log_loss_improvement']:>9.2f}% {m['brier_improvement']:>9.2f}% {m['sectional_enhanced']:>10d}")

    best_ll = min(all_metrics, key=lambda x: x['log_loss'])
    best_brier = min(all_metrics, key=lambda x: x['brier_score'])
    print(f"\n  Best Log-Loss:  {best_ll['label']} ({best_ll['log_loss']:.5f})")
    print(f"  Best Brier:     {best_brier['label']} ({best_brier['brier_score']:.5f})")

    print(f"\n\n  CALIBRATION CURVES (best config: {best_ll['label']}):")
    print(f"  {'Band':<12s} {'Predicted':>10s} {'Actual':>10s} {'Gap':>8s} {'Count':>8s}")
    print("  " + "-" * 50)
    for band in best_ll['calibration']:
        print(f"  {band['band']:<12s} {band['predicted']:>9.2f}% {band['actual']:>9.2f}% {band['gap']:>+7.2f}% {band['count']:>8d}")

    print(f"\n\n  ROI BY EDGE THRESHOLD (best config: {best_ll['label']}):")
    print(f"  {'Min Edge':>10s} {'Bets':>8s} {'Wins':>8s} {'Strike%':>10s} {'ROI%':>10s} {'Profit':>10s}")
    print("  " + "-" * 58)
    for roi in best_ll['roi_by_edge']:
        print(f"  {roi['min_edge']:>10s} {roi['bets']:>8d} {roi['wins']:>8d} {roi['strike_rate']:>9.1f}% {roi['roi']:>9.1f}% {roi['profit']:>10.2f}")

    best_roi_config = None
    best_roi_val = -999
    for m in all_metrics:
        for roi in m['roi_by_edge']:
            if roi['min_edge'] == '5%' and roi['bets'] >= 20:
                if roi['roi'] > best_roi_val:
                    best_roi_val = roi['roi']
                    best_roi_config = m['label']

    if best_roi_config:
        print(f"\n  Best ROI at 5% edge: {best_roi_config} ({best_roi_val:.1f}%)")

    print(f"\n\n[4/4] RECOMMENDATIONS")
    print("=" * 70)

    recommendations = []
    if best_ll['label'] != 'blend_60_40':
        recommendations.append(f"Change blend ratio: current=60/40, optimal={best_ll['label']}")
    else:
        recommendations.append("Current 60/40 blend ratio is already optimal for log-loss")

    strict_configs = [m for m in all_metrics if 'strict' in m['label']]
    loose_configs = [m for m in all_metrics if m['label'] == 'blend_60_40']
    if strict_configs and loose_configs:
        strict_best = min(strict_configs, key=lambda x: x['log_loss'])
        loose_best = loose_configs[0]
        if strict_best['log_loss'] < loose_best['log_loss']:
            recommendations.append(f"Use strict speed classification: improves LL from {loose_best['log_loss']:.5f} to {strict_best['log_loss']:.5f}")
        else:
            recommendations.append("Current broad speed classification is better than strict")

    thresh_configs = {m['label']: m for m in all_metrics if 'thresh' in m['label']}
    if thresh_configs:
        best_thresh = min(thresh_configs.values(), key=lambda x: x['log_loss'])
        base_thresh = next((m for m in all_metrics if m['label'] == 'blend_60_40'), None)
        if base_thresh and best_thresh['log_loss'] < base_thresh['log_loss']:
            recommendations.append(f"Change speed threshold: {best_thresh['label']} is better (LL {best_thresh['log_loss']:.5f} vs {base_thresh['log_loss']:.5f})")
        else:
            recommendations.append("Current 103% speed threshold is optimal")

    for rec in recommendations:
        print(f"  -> {rec}")

    report = {
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'races_tested': len(races),
        'total_runners': total_runners,
        'configs_tested': len(configs),
        'results': all_metrics,
        'best_log_loss': {'config': best_ll['label'], 'value': best_ll['log_loss']},
        'best_brier': {'config': best_brier['label'], 'value': best_brier['brier_score']},
        'recommendations': recommendations,
    }

    report_path = os.path.join(os.path.dirname(__file__), 'calibration_report.json')
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Report saved to: {report_path}")

    return report


if __name__ == '__main__':
    max_races = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    run_full_calibration(max_races=max_races)
