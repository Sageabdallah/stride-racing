#!/usr/bin/env python3
"""Expanding-window cross-validation that respects temporal ordering, trains the ensemble ML model on each fold, and computes comprehensive metrics including AUC-ROC, Brier, log-loss, ECE, and ROI at multiple probability thresholds."""

import os
import sys
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple, Generator, Optional

sys.path.insert(0, os.path.dirname(__file__))

from ml_model import RacingMLModel

try:
    from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss as sklearn_log_loss
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    from scipy.stats import t as t_dist
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

import psycopg2
import psycopg2.extras


RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'backtest_results')
os.makedirs(RESULTS_DIR, exist_ok=True)


class WalkForwardSplitter:
    """Expanding window cross-validation respecting temporal ordering."""

    def __init__(self, min_train_size: int = 3000, test_size: int = 500,
                 step_size: int = None, gap_days: int = 7):
        self.min_train_size = min_train_size
        self.test_size = test_size
        self.step_size = step_size if step_size is not None else test_size
        self.gap_days = gap_days

    def split(self, df: pd.DataFrame, date_column: str = 'race_date') -> Generator:
        df = df.copy()
        df[date_column] = pd.to_datetime(df[date_column])
        df = df.sort_values(date_column).reset_index(drop=True)

        n = len(df)
        fold_number = 0
        train_end_idx = self.min_train_size

        while train_end_idx < n:
            train_end_date = df[date_column].iloc[train_end_idx - 1]
            gap_cutoff = train_end_date + timedelta(days=self.gap_days)

            test_candidates = df[date_column] > gap_cutoff
            if not test_candidates.any():
                break

            test_start_pos = int(test_candidates.idxmax())
            test_end_pos = min(test_start_pos + self.test_size, n)

            if test_end_pos - test_start_pos < 10:
                break

            train_cutoff_date = df[date_column].iloc[test_start_pos] - timedelta(days=self.gap_days)
            safe_train_end = int((df[date_column] <= train_cutoff_date).sum())
            if safe_train_end < self.min_train_size:
                safe_train_end = train_end_idx
            safe_train_end = min(safe_train_end, test_start_pos)

            train_idx = df.index[:safe_train_end].tolist()
            test_idx = df.index[test_start_pos:test_end_pos].tolist()

            assert df[date_column].iloc[train_idx[-1]] < df[date_column].iloc[test_idx[0]], \
                f"Leakage: train_end {df[date_column].iloc[train_idx[-1]]} >= test_start {df[date_column].iloc[test_idx[0]]}"

            fold_number += 1
            metadata = {
                'fold_number': fold_number,
                'train_start': str(df[date_column].iloc[train_idx[0]].date()),
                'train_end': str(df[date_column].iloc[train_idx[-1]].date()),
                'test_start': str(df[date_column].iloc[test_idx[0]].date()),
                'test_end': str(df[date_column].iloc[test_idx[-1]].date()),
                'train_size': len(train_idx),
                'test_size': len(test_idx),
                'gap_days': self.gap_days,
                'actual_gap_days': (df[date_column].iloc[test_idx[0]] - df[date_column].iloc[train_idx[-1]]).days,
            }

            yield train_idx, test_idx, metadata

            train_end_idx = test_end_pos


def compute_ece(y_true: np.ndarray, y_pred: np.ndarray, n_bins: int = 10) -> float:
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    total = len(y_true)
    if total == 0:
        return 0.0

    for i in range(n_bins):
        mask = (y_pred >= bin_edges[i]) & (y_pred < bin_edges[i + 1])
        if i == n_bins - 1:
            mask = (y_pred >= bin_edges[i]) & (y_pred <= bin_edges[i + 1])
        bin_count = mask.sum()
        if bin_count > 0:
            avg_pred = y_pred[mask].mean()
            avg_actual = y_true[mask].mean()
            ece += (bin_count / total) * abs(avg_pred - avg_actual)

    return float(ece)


def compute_fold_metrics(y_true: np.ndarray, y_pred_proba: np.ndarray,
                         odds: np.ndarray,
                         thresholds: List[float] = None) -> Dict[str, Any]:
    if thresholds is None:
        thresholds = [0.05, 0.10, 0.15, 0.20, 0.30]

    y_true = np.asarray(y_true, dtype=int)
    y_pred_proba = np.asarray(y_pred_proba, dtype=float)
    odds = np.asarray(odds, dtype=float)

    metrics: Dict[str, Any] = {}

    try:
        if len(np.unique(y_true)) >= 2:
            metrics['auc_roc'] = float(roc_auc_score(y_true, y_pred_proba))
        else:
            metrics['auc_roc'] = None
    except Exception:
        metrics['auc_roc'] = None

    try:
        metrics['brier'] = float(brier_score_loss(y_true, y_pred_proba))
    except Exception:
        metrics['brier'] = None

    try:
        eps = 1e-15
        clipped = np.clip(y_pred_proba, eps, 1 - eps)
        metrics['log_loss'] = float(sklearn_log_loss(y_true, clipped))
    except Exception:
        metrics['log_loss'] = None

    try:
        metrics['ece'] = compute_ece(y_true, y_pred_proba, n_bins=10)
    except Exception:
        metrics['ece'] = None

    stake = 100.0
    roi_at_thresholds = {}

    for thresh in thresholds:
        key = f"{thresh:.2f}"
        mask = y_pred_proba >= thresh
        n_bets = int(mask.sum())

        if n_bets == 0:
            roi_at_thresholds[key] = {
                'threshold': thresh,
                'n_bets': 0,
                'hit_rate': 0.0,
                'avg_odds_winners': 0.0,
                'roi_pct': 0.0,
                'profit': 0.0,
            }
            continue

        bet_won = y_true[mask]
        bet_odds = odds[mask]
        n_winners = int(bet_won.sum())
        hit_rate = n_winners / n_bets if n_bets > 0 else 0.0

        total_staked = n_bets * stake
        total_returned = float((bet_won * bet_odds * stake).sum())
        profit = total_returned - total_staked
        roi_pct = (profit / total_staked) * 100.0 if total_staked > 0 else 0.0

        winner_odds = bet_odds[bet_won == 1]
        avg_odds_winners = float(winner_odds.mean()) if len(winner_odds) > 0 else 0.0

        roi_at_thresholds[key] = {
            'threshold': thresh,
            'n_bets': n_bets,
            'hit_rate': round(hit_rate, 4),
            'avg_odds_winners': round(avg_odds_winners, 2),
            'roi_pct': round(roi_pct, 2),
            'profit': round(profit, 2),
        }

    metrics['roi_at_thresholds'] = roi_at_thresholds
    return metrics


def aggregate_metrics(fold_results: List[Dict]) -> Dict[str, Any]:
    if not fold_results:
        return {}

    def _get_metrics(f):
        return f.get('metrics', f) if isinstance(f, dict) else f

    scalar_keys = ['auc_roc', 'brier', 'log_loss', 'ece']
    agg: Dict[str, Any] = {}

    for key in scalar_keys:
        values = [_get_metrics(f).get(key) for f in fold_results if _get_metrics(f).get(key) is not None]
        if not values:
            agg[key] = {'mean': None, 'std': None, 'ci_95': [None, None]}
            continue
        arr = np.array(values, dtype=float)
        mean_val = float(arr.mean())
        std_val = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
        ci_lo, ci_hi = mean_val, mean_val
        if SCIPY_AVAILABLE and len(arr) > 1:
            se = std_val / np.sqrt(len(arr))
            t_crit = t_dist.ppf(0.975, df=len(arr) - 1)
            ci_lo = mean_val - t_crit * se
            ci_hi = mean_val + t_crit * se
        agg[key] = {
            'mean': round(mean_val, 6),
            'std': round(std_val, 6),
            'ci_95': [round(ci_lo, 6), round(ci_hi, 6)],
        }

    all_threshold_keys = set()
    for f in fold_results:
        all_threshold_keys.update(_get_metrics(f).get('roi_at_thresholds', {}).keys())

    roi_by_threshold = {}
    for tkey in sorted(all_threshold_keys):
        rois = []
        hit_rates = []
        n_bets_list = []
        profits = []
        for f in fold_results:
            t_data = _get_metrics(f).get('roi_at_thresholds', {}).get(tkey)
            if t_data and t_data['n_bets'] > 0:
                rois.append(t_data['roi_pct'])
                hit_rates.append(t_data['hit_rate'])
                n_bets_list.append(t_data['n_bets'])
                profits.append(t_data['profit'])

        if rois:
            roi_arr = np.array(rois)
            mean_roi = float(roi_arr.mean())
            std_roi = float(roi_arr.std(ddof=1)) if len(roi_arr) > 1 else 0.0
            ci_lo_roi, ci_hi_roi = mean_roi, mean_roi
            if SCIPY_AVAILABLE and len(roi_arr) > 1:
                se = std_roi / np.sqrt(len(roi_arr))
                t_crit = t_dist.ppf(0.975, df=len(roi_arr) - 1)
                ci_lo_roi = mean_roi - t_crit * se
                ci_hi_roi = mean_roi + t_crit * se

            roi_by_threshold[tkey] = {
                'mean_roi': round(mean_roi, 2),
                'std_roi': round(std_roi, 2),
                'ci_95_roi': [round(ci_lo_roi, 2), round(ci_hi_roi, 2)],
                'mean_hit_rate': round(float(np.mean(hit_rates)), 4),
                'total_bets': int(sum(n_bets_list)),
                'total_profit': round(float(sum(profits)), 2),
                'n_folds_with_bets': len(rois),
            }
        else:
            roi_by_threshold[tkey] = {
                'mean_roi': 0.0, 'std_roi': 0.0,
                'ci_95_roi': [0.0, 0.0],
                'mean_hit_rate': 0.0,
                'total_bets': 0, 'total_profit': 0.0,
                'n_folds_with_bets': 0,
            }

    agg['roi_by_threshold'] = roi_by_threshold
    return agg


class WalkForwardBacktester:
    """Walk-forward backtester using the RacingMLModel pipeline."""

    def __init__(self, min_train_size: int = 3000, test_size: int = 500,
                 step_size: int = None, gap_days: int = 7,
                 use_optuna: bool = False, verbose: bool = True):
        self.min_train_size = min_train_size
        self.test_size = test_size
        self.step_size = step_size
        self.gap_days = gap_days
        self.use_optuna = use_optuna
        self.verbose = verbose

    def _load_data(self) -> pd.DataFrame:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            raise RuntimeError('DATABASE_URL environment variable not set')

        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT race_id, track, race_number, race_date, distance, going, race_class,
                   horse_name, horse_number, barrier, jockey, trainer, weight,
                   market_odds, actual_position, won, placed, starting_price
            FROM training_data
            WHERE won IS NOT NULL AND race_date IS NOT NULL
            ORDER BY race_date, track, race_number
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        df = pd.DataFrame(rows)
        if df.empty:
            raise RuntimeError('No training data found in database')

        df['won'] = df['won'].astype(int)
        if 'placed' in df.columns:
            df['placed'] = df['placed'].fillna(0).astype(int)
        if 'barrier' in df.columns:
            df['barrier_draw'] = pd.to_numeric(df['barrier'], errors='coerce').fillna(0)
        if 'weight' in df.columns:
            df['weight_kg'] = pd.to_numeric(df['weight'], errors='coerce').fillna(55)
        if 'market_odds' in df.columns:
            df['market_odds'] = pd.to_numeric(df['market_odds'], errors='coerce').fillna(10)

        return df

    def _log(self, msg: str):
        if self.verbose:
            print(msg, flush=True)

    def run(self, data: pd.DataFrame = None) -> Dict[str, Any]:
        t_start = time.time()
        self._log("=" * 70)
        self._log("WALK-FORWARD BACKTEST  —  RacingMLModel Pipeline")
        self._log("=" * 70)

        if data is None:
            self._log("\n[1] Loading training data from database...")
            data = self._load_data()
        else:
            data = data.copy()
            if 'barrier' in data.columns and 'barrier_draw' not in data.columns:
                data['barrier_draw'] = pd.to_numeric(data['barrier'], errors='coerce').fillna(0)
            if 'weight' in data.columns and 'weight_kg' not in data.columns:
                data['weight_kg'] = pd.to_numeric(data['weight'], errors='coerce').fillna(55)

        total_records = len(data)
        win_rate = float(data['won'].mean()) if 'won' in data.columns else 0.0
        date_min = str(pd.to_datetime(data['race_date']).min().date())
        date_max = str(pd.to_datetime(data['race_date']).max().date())

        self._log(f"  Records: {total_records}  |  Date range: {date_min} to {date_max}")
        self._log(f"  Win rate: {win_rate:.3f}  ({int(data['won'].sum())} winners)")

        splitter = WalkForwardSplitter(
            min_train_size=self.min_train_size,
            test_size=self.test_size,
            step_size=self.step_size,
            gap_days=self.gap_days,
        )

        folds = list(splitter.split(data, date_column='race_date'))
        n_folds = len(folds)
        self._log(f"\n[2] Generated {n_folds} walk-forward folds")
        self._log(f"    min_train={self.min_train_size}  test={self.test_size}  "
                  f"step={self.step_size or self.test_size}  gap={self.gap_days}d  "
                  f"optuna={self.use_optuna}")

        if n_folds == 0:
            self._log("  WARNING: No folds generated. Check data size / parameters.")
            return self._build_report(data, [], {}, t_start)

        fold_results = []
        self._log(f"\n[3] Training & evaluating {n_folds} folds...\n")

        for train_idx, test_idx, meta in folds:
            fold_num = meta['fold_number']
            t_fold = time.time()
            self._log(f"  Fold {fold_num}/{n_folds}  "
                      f"train={meta['train_start']}..{meta['train_end']} ({meta['train_size']})  "
                      f"test={meta['test_start']}..{meta['test_end']} ({meta['test_size']})")

            train_df = data.iloc[train_idx].copy()
            test_df = data.iloc[test_idx].copy()

            model = RacingMLModel(model_path=None)
            model.model_path = os.path.join(RESULTS_DIR, f'_tmp_fold_{fold_num}.pkl')

            try:
                train_result = model.train(
                    train_df, target_col='won',
                    use_optuna=self.use_optuna,
                    n_trials=20 if self.use_optuna else 0,
                )
            except Exception as e:
                self._log(f"    ERROR training fold {fold_num}: {e}")
                continue

            if not train_result.get('success'):
                self._log(f"    SKIP fold {fold_num}: {train_result.get('error', 'unknown')}")
                continue

            try:
                X_test = model.prepare_features(test_df)
                y_pred_proba = model.predict_proba(X_test)
            except Exception as e:
                self._log(f"    ERROR predicting fold {fold_num}: {e}")
                continue

            y_true = test_df['won'].values.astype(int)
            odds = pd.to_numeric(
                test_df['starting_price'].fillna(test_df.get('market_odds', 10)),
                errors='coerce'
            ).fillna(10).values

            fold_metrics = compute_fold_metrics(y_true, y_pred_proba, odds)

            fold_entry = {
                'fold_number': fold_num,
                'train_period': {
                    'start': meta['train_start'],
                    'end': meta['train_end'],
                    'size': meta['train_size'],
                },
                'test_period': {
                    'start': meta['test_start'],
                    'end': meta['test_end'],
                    'size': meta['test_size'],
                },
                'metrics': fold_metrics,
            }
            fold_results.append(fold_entry)

            dt = time.time() - t_fold
            auc_str = f"{fold_metrics['auc_roc']:.4f}" if fold_metrics['auc_roc'] is not None else "N/A"
            brier_str = f"{fold_metrics['brier']:.4f}" if fold_metrics['brier'] is not None else "N/A"
            roi_10 = fold_metrics.get('roi_at_thresholds', {}).get('0.10', {})
            roi_str = f"{roi_10.get('roi_pct', 0):.1f}%" if roi_10.get('n_bets', 0) > 0 else "N/A"

            self._log(f"    AUC={auc_str}  Brier={brier_str}  "
                      f"ROI@0.10={roi_str}  ({dt:.1f}s)")

            try:
                os.remove(model.model_path)
            except OSError:
                pass

        self._log(f"\n[4] Aggregating metrics across {len(fold_results)} folds...")
        aggregate = aggregate_metrics(fold_results)

        report = self._build_report(data, fold_results, aggregate, t_start)

        if aggregate:
            self._log("\n" + "=" * 70)
            self._log("AGGREGATE RESULTS")
            self._log("=" * 70)
            for key in ['auc_roc', 'brier', 'log_loss', 'ece']:
                vals = aggregate.get(key, {})
                if vals.get('mean') is not None:
                    ci = vals.get('ci_95', [None, None])
                    ci_str = f"[{ci[0]:.4f}, {ci[1]:.4f}]" if ci[0] is not None else "N/A"
                    self._log(f"  {key:<12s}  mean={vals['mean']:.4f}  "
                              f"std={vals['std']:.4f}  95%CI={ci_str}")

            self._log("\n  ROI by threshold:")
            self._log(f"  {'Thresh':<8s} {'Mean ROI':>10s} {'Hit Rate':>10s} "
                      f"{'Bets':>8s} {'Profit':>10s} {'Folds':>6s}")
            self._log("  " + "-" * 54)
            for tkey, tvals in sorted(aggregate.get('roi_by_threshold', {}).items()):
                self._log(f"  {tkey:<8s} {tvals['mean_roi']:>9.1f}% "
                          f"{tvals['mean_hit_rate']:>9.2%} "
                          f"{tvals['total_bets']:>8d} "
                          f"${tvals['total_profit']:>9.0f} "
                          f"{tvals['n_folds_with_bets']:>6d}")

        elapsed = time.time() - t_start
        self._log(f"\nCompleted in {elapsed:.1f}s")

        return report

    def _build_report(self, data: pd.DataFrame, fold_results: List[Dict],
                      aggregate: Dict, t_start: float) -> Dict[str, Any]:
        return {
            'config': {
                'min_train_size': self.min_train_size,
                'test_size': self.test_size,
                'step_size': self.step_size or self.test_size,
                'gap_days': self.gap_days,
                'use_optuna': self.use_optuna,
                'timestamp': datetime.now().isoformat(),
            },
            'data_summary': {
                'total_records': len(data),
                'date_range': {
                    'start': str(pd.to_datetime(data['race_date']).min().date()),
                    'end': str(pd.to_datetime(data['race_date']).max().date()),
                },
                'win_rate': round(float(data['won'].mean()), 4) if 'won' in data.columns else 0.0,
                'n_folds': len(fold_results),
            },
            'folds': fold_results,
            'aggregate': aggregate,
            'elapsed_seconds': round(time.time() - t_start, 1),
        }


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Walk-Forward Backtest for RacingMLModel')
    parser.add_argument('--min-train', type=int, default=3000,
                        help='Minimum training set size (default: 3000)')
    parser.add_argument('--test-size', type=int, default=500,
                        help='Test fold size (default: 500)')
    parser.add_argument('--step-size', type=int, default=None,
                        help='Step size between folds (default: test-size)')
    parser.add_argument('--gap-days', type=int, default=7,
                        help='Purge gap in days (default: 7)')
    parser.add_argument('--use-optuna', action='store_true',
                        help='Enable Optuna hyperparameter tuning (slower)')
    parser.add_argument('--output', type=str, default=None,
                        help='Output JSON path (default: backtest_results/wf_backtest_<timestamp>.json)')
    args = parser.parse_args()

    backtester = WalkForwardBacktester(
        min_train_size=args.min_train,
        test_size=args.test_size,
        step_size=args.step_size,
        gap_days=args.gap_days,
        use_optuna=args.use_optuna,
        verbose=True,
    )

    report = backtester.run()

    output_path = args.output
    if not output_path:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = os.path.join(RESULTS_DIR, f'wf_backtest_{ts}.json')

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\nReport saved to: {output_path}")
