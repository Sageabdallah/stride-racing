#!/usr/bin/env python3
"""Feature comparison and drift detection between training and prediction datasets."""

import os
import sys
import json
import argparse
import pickle
import math
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2 is required. Install with: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)

MODEL_PATH = os.path.join(os.path.dirname(__file__), 'models', 'enhanced_racing_ensemble.pkl')

EXPECTED_FEATURE_RANGES = {
    'barrier': (1, 24),
    'weight': (48, 65),
    'market_odds': (1.01, 200),
    'field_size': (3, 24),
    'class_level': (0, 100),
    'distance_numeric': (800, 3600),
    'weighted_form_score': (0, 50),
    'form_consistency': (0, 10),
    'ground_suitability': (0, 100),
    'distance_strike_rate': (0, 100),
    'course_strike_rate': (0, 100),
    'days_since_run': (0, 365),
    'career_win_rate': (0, 100),
    'career_place_rate': (0, 100),
    'jockey_win_rate': (0, 100),
    'trainer_win_rate': (0, 100),
    'implied_prob': (0, 100),
    'odds_rank': (1, 24),
    'barrier_ratio': (0, 1.5),
    'weight_diff': (-10, 10),
}


def load_feature_columns() -> List[str]:
    if not os.path.exists(MODEL_PATH):
        print(f"Warning: Model file not found at {MODEL_PATH}", file=sys.stderr)
        return []
    try:
        with open(MODEL_PATH, 'rb') as f:
            model_data = pickle.load(f)
        if isinstance(model_data, dict) and 'feature_columns' in model_data:
            return list(model_data['feature_columns'])
        return []
    except Exception as e:
        print(f"Warning: Could not load model file: {e}", file=sys.stderr)
        return []


def get_db_connection():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("Error: DATABASE_URL environment variable not set", file=sys.stderr)
        sys.exit(1)
    try:
        return psycopg2.connect(db_url)
    except Exception as e:
        print(f"Error connecting to database: {e}", file=sys.stderr)
        sys.exit(1)


def load_feature_snapshots(conn) -> List[Dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT track, race_number, race_date, horse_name, horse_number,
               features_json, feature_count, model_prediction, prediction_source,
               created_at
        FROM feature_snapshots
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    cur.close()
    return [dict(r) for r in rows]


def load_training_data(conn) -> List[Dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT track, race_number, race_date, horse_name, horse_number,
               barrier, weight, market_odds, distance, going, race_class,
               jockey, trainer, predicted_win_prob, actual_position, won, placed
        FROM training_data
        ORDER BY race_date DESC
    """)
    rows = cur.fetchall()
    cur.close()
    return [dict(r) for r in rows]


def safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def compute_stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {'count': 0, 'mean': None, 'std': None, 'min': None, 'max': None, 'median': None}
    n = len(values)
    mean = sum(values) / n
    if n > 1:
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        std = math.sqrt(variance)
    else:
        std = 0.0
    sorted_vals = sorted(values)
    median = sorted_vals[n // 2] if n % 2 == 1 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0
    return {
        'count': n,
        'mean': round(mean, 6),
        'std': round(std, 6),
        'min': round(min(values), 6),
        'max': round(max(values), 6),
        'median': round(median, 6),
    }


def compute_correlation(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) < 5 or len(ys) < 5 or len(xs) != len(ys):
        return None
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    if denom < 1e-12:
        return None
    return round(cov / denom, 6)


def analyze_feature_distribution(feature_name: str, values: List[float]) -> Dict[str, Any]:
    stats = compute_stats(values)
    total = len(values)
    nan_count = total - len(values)
    zero_count = sum(1 for v in values if abs(v) < 1e-10)

    result = {
        'feature': feature_name,
        'stats': stats,
        'nan_ratio': 0.0,
        'zero_ratio': round(zero_count / max(total, 1), 4),
        'anomalies': [],
    }

    if result['zero_ratio'] > 0.9:
        result['anomalies'].append(f"Mostly zeros ({result['zero_ratio']*100:.1f}%) - possible extraction bug")

    expected = EXPECTED_FEATURE_RANGES.get(feature_name)
    if expected and stats['mean'] is not None:
        lo, hi = expected
        if stats['mean'] < lo or stats['mean'] > hi:
            result['anomalies'].append(
                f"Mean {stats['mean']:.4f} outside expected range [{lo}, {hi}]"
            )
        if stats['min'] is not None and stats['min'] < lo - abs(lo) * 2:
            result['anomalies'].append(f"Min {stats['min']:.4f} far below expected lower bound {lo}")
        if stats['max'] is not None and stats['max'] > hi + abs(hi) * 2:
            result['anomalies'].append(f"Max {stats['max']:.4f} far above expected upper bound {hi}")

    return result


def analyze_self_consistency(snapshots: List[Dict], feature_columns: List[str]) -> Dict[str, Any]:
    horse_features = defaultdict(list)
    for snap in snapshots:
        fj = snap.get('features_json')
        if not fj or not isinstance(fj, dict):
            continue
        key = (
            str(snap.get('horse_name', '')).strip().lower(),
            str(snap.get('track', '')).strip().lower(),
        )
        horse_features[key].append(fj)

    consistency_results = {}
    horses_with_multiple = {k: v for k, v in horse_features.items() if len(v) >= 2}

    if not horses_with_multiple:
        return {'horses_checked': 0, 'features_checked': 0, 'inconsistent_features': []}

    feature_diffs = defaultdict(list)
    for key, feature_sets in horses_with_multiple.items():
        for i in range(len(feature_sets) - 1):
            f1 = feature_sets[i]
            f2 = feature_sets[i + 1]
            for feat in feature_columns:
                v1 = safe_float(f1.get(feat))
                v2 = safe_float(f2.get(feat))
                if v1 is not None and v2 is not None:
                    feature_diffs[feat].append(abs(v1 - v2))

    inconsistent = []
    for feat, diffs in feature_diffs.items():
        if not diffs:
            continue
        mean_diff = sum(diffs) / len(diffs)
        max_diff = max(diffs)
        if mean_diff > 5.0 or max_diff > 20.0:
            inconsistent.append({
                'feature': feat,
                'mean_abs_diff': round(mean_diff, 4),
                'max_diff': round(max_diff, 4),
                'comparisons': len(diffs),
            })

    inconsistent.sort(key=lambda x: x['mean_abs_diff'], reverse=True)

    return {
        'horses_checked': len(horses_with_multiple),
        'features_checked': len(feature_diffs),
        'inconsistent_features': inconsistent[:20],
    }


def compare_matched_records(
    snapshots: List[Dict],
    training: List[Dict],
    feature_columns: List[str],
) -> Dict[str, Any]:
    training_lookup = {}
    for t in training:
        key = (
            str(t.get('track', '')).strip().lower(),
            int(t.get('race_number', 0)),
            str(t.get('race_date', '')).strip(),
            str(t.get('horse_name', '')).strip().lower(),
        )
        training_lookup[key] = t

    matched = 0
    feature_pairs = defaultdict(lambda: {'inference': [], 'training': []})

    for snap in snapshots:
        fj = snap.get('features_json')
        if not fj or not isinstance(fj, dict):
            continue
        key = (
            str(snap.get('track', '')).strip().lower(),
            int(snap.get('race_number', 0)),
            str(snap.get('race_date', '')).strip(),
            str(snap.get('horse_name', '')).strip().lower(),
        )
        t_row = training_lookup.get(key)
        if not t_row:
            continue
        matched += 1

        raw_feature_map = {
            'barrier': safe_float(t_row.get('barrier')),
            'weight': safe_float(t_row.get('weight')),
            'market_odds': safe_float(t_row.get('market_odds')),
        }

        for feat in feature_columns:
            inf_val = safe_float(fj.get(feat))
            tr_val = raw_feature_map.get(feat)
            if inf_val is not None and tr_val is not None:
                feature_pairs[feat]['inference'].append(inf_val)
                feature_pairs[feat]['training'].append(tr_val)

    drift_results = []
    for feat, pair in feature_pairs.items():
        inf_vals = pair['inference']
        tr_vals = pair['training']
        if not inf_vals or not tr_vals:
            continue

        inf_stats = compute_stats(inf_vals)
        tr_stats = compute_stats(tr_vals)

        diffs = [abs(inf_vals[i] - tr_vals[i]) for i in range(min(len(inf_vals), len(tr_vals)))]
        mean_abs_diff = sum(diffs) / len(diffs) if diffs else 0

        ref = abs(tr_stats['mean']) if tr_stats['mean'] and abs(tr_stats['mean']) > 1e-6 else 1.0
        relative_diff = mean_abs_diff / ref

        corr = compute_correlation(inf_vals, tr_vals)

        drift_results.append({
            'feature': feat,
            'mean_abs_diff': round(mean_abs_diff, 6),
            'relative_diff': round(relative_diff, 6),
            'correlation': corr,
            'inference_stats': inf_stats,
            'training_stats': tr_stats,
            'is_drifted': relative_diff > 0.20,
            'matched_count': len(diffs),
        })

    drift_results.sort(key=lambda x: x['relative_diff'], reverse=True)
    return {'matched_records': matched, 'feature_drift': drift_results}


def run_analysis(feature_filter: Optional[List[str]] = None) -> Dict[str, Any]:
    canonical_features = load_feature_columns()
    if feature_filter:
        canonical_features = [f for f in canonical_features if f in feature_filter]

    conn = get_db_connection()

    try:
        snapshots = load_feature_snapshots(conn)
        training = load_training_data(conn)
    finally:
        conn.close()

    all_feature_values = defaultdict(list)
    nan_counts = defaultdict(int)
    total_snapshots = 0

    for snap in snapshots:
        fj = snap.get('features_json')
        if not fj or not isinstance(fj, dict):
            continue
        total_snapshots += 1
        target_features = canonical_features if canonical_features else list(fj.keys())
        for feat in target_features:
            raw = fj.get(feat)
            val = safe_float(raw)
            if val is not None:
                all_feature_values[feat].append(val)
            else:
                nan_counts[feat] += 1

    per_feature_drift = []
    for feat in sorted(all_feature_values.keys()):
        vals = all_feature_values[feat]
        dist_analysis = analyze_feature_distribution(feat, vals)
        nan_total = nan_counts.get(feat, 0)
        total_seen = len(vals) + nan_total
        dist_analysis['nan_ratio'] = round(nan_total / max(total_seen, 1), 4)
        if dist_analysis['nan_ratio'] > 0.5:
            dist_analysis['anomalies'].append(
                f"High NaN ratio ({dist_analysis['nan_ratio']*100:.1f}%) - feature may not be extracted properly"
            )
        per_feature_drift.append(dist_analysis)

    consistency = analyze_self_consistency(snapshots, canonical_features or list(all_feature_values.keys()))

    matched_comparison = compare_matched_records(
        snapshots, training, canonical_features or list(all_feature_values.keys())
    )

    all_anomalies = []
    for pf in per_feature_drift:
        if pf['anomalies']:
            all_anomalies.append({
                'feature': pf['feature'],
                'anomalies': pf['anomalies'],
                'zero_ratio': pf['zero_ratio'],
                'nan_ratio': pf['nan_ratio'],
            })

    drifted_from_matched = [d for d in matched_comparison.get('feature_drift', []) if d.get('is_drifted')]

    worst_by_zero = sorted(per_feature_drift, key=lambda x: x['zero_ratio'], reverse=True)[:10]
    worst_by_nan = sorted(per_feature_drift, key=lambda x: x['nan_ratio'], reverse=True)[:10]
    worst_by_range = sorted(per_feature_drift, key=lambda x: len(x.get('anomalies', [])), reverse=True)[:10]

    total_features = len(per_feature_drift)
    features_with_issues = len(all_anomalies)
    if total_features > 0:
        parity_score = round(1.0 - (features_with_issues / total_features), 4)
    else:
        parity_score = 0.0

    report = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'summary': {
            'total_features_analyzed': total_features,
            'total_inference_snapshots': total_snapshots,
            'total_training_records': len(training),
            'matched_records': matched_comparison.get('matched_records', 0),
            'features_with_anomalies': features_with_issues,
            'overall_parity_score': parity_score,
            'canonical_feature_count': len(canonical_features) if canonical_features else 0,
        },
        'per_feature_drift': per_feature_drift,
        'worst_drifters': {
            'by_zero_ratio': [
                {'feature': w['feature'], 'zero_ratio': w['zero_ratio']}
                for w in worst_by_zero if w['zero_ratio'] > 0.1
            ],
            'by_nan_ratio': [
                {'feature': w['feature'], 'nan_ratio': w['nan_ratio']}
                for w in worst_by_nan if w['nan_ratio'] > 0.1
            ],
            'by_range_violations': [
                {'feature': w['feature'], 'anomalies': w['anomalies']}
                for w in worst_by_range if w.get('anomalies')
            ],
            'matched_drifted_features': drifted_from_matched[:10],
        },
        'self_consistency': consistency,
        'matched_comparison': matched_comparison,
        'anomaly_details': all_anomalies,
    }

    return report


def main():
    parser = argparse.ArgumentParser(
        description='Compare feature values between training and inference to detect drift'
    )
    parser.add_argument(
        '--output', '-o',
        type=str,
        default=None,
        help='Path to save JSON report (default: print to stdout)',
    )
    parser.add_argument(
        '--feature-list',
        type=str,
        default=None,
        help='Comma-separated list of feature names to focus analysis on',
    )
    args = parser.parse_args()

    feature_filter = None
    if args.feature_list:
        feature_filter = [f.strip() for f in args.feature_list.split(',') if f.strip()]

    report = run_analysis(feature_filter=feature_filter)

    output_json = json.dumps(report, indent=2, default=str)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, 'w') as f:
            f.write(output_json)
        print(f"Report saved to {args.output}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == '__main__':
    main()
