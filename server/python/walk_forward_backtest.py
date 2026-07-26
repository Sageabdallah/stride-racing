#!/usr/bin/env python3
"""Expanding-window cross-validation that respects temporal ordering, trains the ensemble ML model on each fold, and computes comprehensive metrics including AUC-ROC, Brier, log-loss, ECE, and ROI at multiple probability thresholds.

Leakage-proof evaluation harness (Workstream A). Two additions sit behind
default-off flags so an unflagged run reproduces the historical numbers:

  STRIDE_WFB_RACE_SAFE_SPLIT  keep every runner of a race inside one fold, and
                              order rows stably so a race's runners stay
                              contiguous (pandas' default quicksort does not)
  STRIDE_WFB_PRICE_AT_TIP     settle at the racecard price available when the
                              tip was published rather than at SP, which is the
                              closing line and not obtainable at bet time

Always-on additions are read-only metrics that change no decision: per-bet P&L
net of an optional commission, max drawdown, profit factor, average odds over
all bets, and a seeded percentile bootstrap CI for ROI resampled over BETS. The
pre-existing `ci_95` in `aggregate_metrics` is a t-interval across FOLDS and
answers a different question; both are reported.

Scope caveat, carried into every report under `config.model_under_test`: this
harness refits `RacingMLModel` per fold. It does not load the production
`retrain_v2` artifact, and it does not exercise `calibrate_and_score`,
`apply_safety_filters`, `evaluate_bet_candidate` or `crowd_bet_decision`. Its
numbers describe the ML ensemble, not the shipped selection wrapper.

Run `python walk_forward_backtest.py --self-test` for the built-in regression
suite; it needs neither a database nor the heavy training stack.
"""

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

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False


RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'backtest_results')
os.makedirs(RESULTS_DIR, exist_ok=True)


def _stride_flag(name: str) -> bool:
    """Default-off env flag, Variant B idiom (see run_tips_pipeline.py:591).

    Kept byte-compatible with the STRIDE_CL_BLEND read site so every flag in the
    repo parses identically. Default is always off: an unset or unparseable
    value leaves the harness on its pre-existing behaviour.
    """
    return os.environ.get(name, "false").strip().lower() in ("true", "1", "yes")


def _race_key_series(df: pd.DataFrame, race_column: Optional[str]) -> Optional[pd.Series]:
    """Row-aligned race identity used to keep a race inside a single fold.

    Prefers an explicit race id column; falls back to the (race_date, track,
    race_number) triple that `_load_data`'s ORDER BY already groups on. Returns
    None when no usable identity exists, which leaves the caller on the legacy
    row-count cut.
    """
    if race_column and race_column in df.columns:
        return df[race_column].astype(str)

    triple = ['race_date', 'track', 'race_number']
    if all(c in df.columns for c in triple):
        return (df['race_date'].astype(str) + '|' +
                df['track'].astype(str) + '|' +
                df['race_number'].astype(str))

    return None


def _advance_to_race_start(race_keys: pd.Series, pos: int, n: int) -> int:
    """Move `pos` forward until it sits on the first row of a race.

    A boundary that lands mid-field would put some runners of a race in one fold
    and the rest in another; advancing (never retreating) keeps the fold's date
    ordering and its purge gap intact.
    """
    if pos <= 0 or pos >= n:
        return pos
    keys = race_keys.values
    while pos < n and keys[pos] == keys[pos - 1]:
        pos += 1
    return pos


class WalkForwardSplitter:
    """Expanding window cross-validation respecting temporal ordering."""

    def __init__(self, min_train_size: int = 3000, test_size: int = 500,
                 step_size: int = None, gap_days: int = 7,
                 race_safe: bool = False, race_column: Optional[str] = None):
        self.min_train_size = min_train_size
        self.test_size = test_size
        self.step_size = step_size if step_size is not None else test_size
        self.gap_days = gap_days
        # race_safe=False preserves the historical row-count cut exactly.
        self.race_safe = race_safe
        self.race_column = race_column

    def prepare_frame(self, df: pd.DataFrame, date_column: str = 'race_date') -> pd.DataFrame:
        """Canonical row order that the yielded fold indices address.

        Callers that slice by the returned indices must slice THIS frame, not
        the frame they passed in. Under race_safe the sort is stable and adds
        the race key as a secondary term, which restores the
        `ORDER BY race_date, track, race_number` grouping `_load_data` selects
        with — pandas' default quicksort is not stable and scatters a race's
        runners across the date block.
        """
        df = df.copy()
        df[date_column] = pd.to_datetime(df[date_column])

        if self.race_safe:
            keys = _race_key_series(df, self.race_column)
            if keys is not None:
                df['__race_key'] = keys.values
                return df.sort_values([date_column, '__race_key'],
                                      kind='mergesort').reset_index(drop=True)

        return df.sort_values(date_column).reset_index(drop=True)

    def split(self, df: pd.DataFrame, date_column: str = 'race_date') -> Generator:
        df = self.prepare_frame(df, date_column=date_column)

        race_keys = df['__race_key'] if '__race_key' in df.columns else None

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

            if race_keys is not None:
                test_start_pos = _advance_to_race_start(race_keys, test_start_pos, n)
                test_end_pos = _advance_to_race_start(race_keys, test_end_pos, n)
                if test_start_pos >= n:
                    break

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

            n_shared_races = 0
            if race_keys is not None:
                train_races = set(race_keys.iloc[train_idx].values)
                test_races = set(race_keys.iloc[test_idx].values)
                shared = train_races & test_races
                n_shared_races = len(shared)
                assert not shared, \
                    f"Leakage: {n_shared_races} race(s) span train and test, e.g. {sorted(shared)[:3]}"

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
                'race_safe': race_keys is not None,
                'n_test_races': int(race_keys.iloc[test_idx].nunique()) if race_keys is not None else None,
                'n_races_spanning_folds': n_shared_races,
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


def compute_bet_pnl(bet_won: np.ndarray, bet_odds: np.ndarray,
                    stake: float = 100.0, commission_rate: float = 0.0) -> np.ndarray:
    """Per-bet profit, net of commission on winnings only.

    commission_rate=0.0 reproduces the harness's original gross arithmetic
    exactly: a winner returns stake*(odds-1), a loser returns -stake.
    Commission is charged on net winnings, which is how AU exchange markets
    price it; fixed-odds and tote bets carry no commission, so 0.0 stays the
    default.
    """
    bet_won = np.asarray(bet_won, dtype=float)
    bet_odds = np.asarray(bet_odds, dtype=float)
    gross_win = stake * (bet_odds - 1.0)
    net_win = gross_win * (1.0 - commission_rate)
    return np.where(bet_won == 1, net_win, -stake)


def compute_max_drawdown(pnl: np.ndarray) -> Tuple[float, float]:
    """Largest peak-to-trough fall of the cumulative P&L curve.

    Returns (drawdown_currency, drawdown_pct_of_peak). Bets are consumed in the
    order given, which for a fold is chronological, so the figure is the worst
    run an operator would actually have lived through.
    """
    pnl = np.asarray(pnl, dtype=float)
    if pnl.size == 0:
        return 0.0, 0.0
    equity = np.cumsum(pnl)
    running_peak = np.maximum.accumulate(equity)
    drawdowns = running_peak - equity
    idx = int(np.argmax(drawdowns))
    dd = float(drawdowns[idx])
    peak_at_idx = float(running_peak[idx])
    dd_pct = (dd / peak_at_idx * 100.0) if peak_at_idx > 0 else 0.0
    return dd, dd_pct


def compute_profit_factor(pnl: np.ndarray) -> Optional[float]:
    """Gross profit divided by gross loss. None when there are no losing bets."""
    pnl = np.asarray(pnl, dtype=float)
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(-pnl[pnl < 0].sum())
    if gross_loss <= 0:
        return None
    return gross_profit / gross_loss


def bootstrap_roi_ci(pnl: np.ndarray, stake: float = 100.0, n_boot: int = 2000,
                     seed: int = 42, alpha: float = 0.05) -> Dict[str, Any]:
    """Percentile bootstrap CI for ROI, resampling BETS (not folds).

    The harness's pre-existing t-distribution interval is computed across folds,
    which answers "how much does fold-mean ROI vary" — not "could this ROI have
    arisen from no edge". This resamples the bet population directly, which is
    the quantity the ship criteria need. Seeded so a rerun reproduces.
    """
    pnl = np.asarray(pnl, dtype=float)
    n = pnl.size
    if n == 0 or n_boot <= 0:
        return {'n_boot': 0, 'ci_95': [None, None], 'spans_zero': None, 'seed': seed}

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    roi_samples = pnl[idx].sum(axis=1) / (n * stake) * 100.0
    lo = float(np.percentile(roi_samples, 100 * alpha / 2))
    hi = float(np.percentile(roi_samples, 100 * (1 - alpha / 2)))
    return {
        'n_boot': int(n_boot),
        'ci_95': [round(lo, 2), round(hi, 2)],
        'spans_zero': bool(lo <= 0.0 <= hi),
        'seed': seed,
    }


def compute_fold_metrics(y_true: np.ndarray, y_pred_proba: np.ndarray,
                         odds: np.ndarray,
                         thresholds: List[float] = None,
                         commission_rate: float = 0.0,
                         bootstrap_n: int = 2000,
                         bootstrap_seed: int = 42) -> Dict[str, Any]:
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
                'avg_odds_all': 0.0,
                'max_drawdown': 0.0,
                'max_drawdown_pct': 0.0,
                'profit_factor': None,
                'roi_ci95_bootstrap': {'n_boot': 0, 'ci_95': [None, None],
                                       'spans_zero': None, 'seed': bootstrap_seed},
                'commission_rate': commission_rate,
            }
            continue

        bet_won = y_true[mask]
        bet_odds = odds[mask]
        n_winners = int(bet_won.sum())
        hit_rate = n_winners / n_bets if n_bets > 0 else 0.0

        total_staked = n_bets * stake
        pnl = compute_bet_pnl(bet_won, bet_odds, stake=stake,
                              commission_rate=commission_rate)
        profit = float(pnl.sum())
        roi_pct = (profit / total_staked) * 100.0 if total_staked > 0 else 0.0

        winner_odds = bet_odds[bet_won == 1]
        avg_odds_winners = float(winner_odds.mean()) if len(winner_odds) > 0 else 0.0
        avg_odds_all = float(bet_odds.mean())

        dd, dd_pct = compute_max_drawdown(pnl)

        roi_at_thresholds[key] = {
            'threshold': thresh,
            'n_bets': n_bets,
            'hit_rate': round(hit_rate, 4),
            'avg_odds_winners': round(avg_odds_winners, 2),
            'roi_pct': round(roi_pct, 2),
            'profit': round(profit, 2),
            'avg_odds_all': round(avg_odds_all, 2),
            'max_drawdown': round(dd, 2),
            'max_drawdown_pct': round(dd_pct, 2),
            'profit_factor': (round(compute_profit_factor(pnl), 4)
                              if compute_profit_factor(pnl) is not None else None),
            'roi_ci95_bootstrap': bootstrap_roi_ci(
                pnl, stake=stake, n_boot=bootstrap_n, seed=bootstrap_seed),
            'commission_rate': commission_rate,
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
                 use_optuna: bool = False, verbose: bool = True,
                 commission_rate: float = 0.0,
                 race_safe: bool = None, price_at_tip: bool = None,
                 bootstrap_n: int = 2000, bootstrap_seed: int = 42):
        self.min_train_size = min_train_size
        self.test_size = test_size
        self.step_size = step_size
        self.gap_days = gap_days
        self.use_optuna = use_optuna
        self.verbose = verbose
        self.commission_rate = commission_rate
        # Both default OFF, so an unflagged run reproduces the historical
        # numbers exactly. STRIDE_WFB_RACE_SAFE_SPLIT keeps a race inside one
        # fold; STRIDE_WFB_PRICE_AT_TIP settles at the racecard price that was
        # actually available when the tip was made rather than at SP.
        self.race_safe = (_stride_flag('STRIDE_WFB_RACE_SAFE_SPLIT')
                          if race_safe is None else race_safe)
        self.price_at_tip = (_stride_flag('STRIDE_WFB_PRICE_AT_TIP')
                             if price_at_tip is None else price_at_tip)
        self.bootstrap_n = bootstrap_n
        self.bootstrap_seed = bootstrap_seed

    def _load_data(self) -> pd.DataFrame:
        if not PSYCOPG2_AVAILABLE:
            raise RuntimeError(
                'psycopg2 is not installed — cannot load training data. '
                'Pass a DataFrame to run() to backtest without a database.')

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
            race_safe=self.race_safe,
        )

        # Fold indices address the splitter's canonical ordering, so rebind
        # `data` to it before slicing — otherwise iloc reads different rows
        # than the splitter reasoned about.
        data = splitter.prepare_frame(data, date_column='race_date')

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
            if self.price_at_tip and 'market_odds' in test_df.columns:
                # Settle at the price available when the tip was published, not
                # at SP. SP is the closing line: settling there credits the
                # backtest with money no bettor could have taken.
                odds = pd.to_numeric(
                    test_df['market_odds'].fillna(test_df.get('starting_price', 10)),
                    errors='coerce'
                ).fillna(10).values
            else:
                odds = pd.to_numeric(
                    test_df['starting_price'].fillna(test_df.get('market_odds', 10)),
                    errors='coerce'
                ).fillna(10).values

            fold_metrics = compute_fold_metrics(
                y_true, y_pred_proba, odds,
                commission_rate=self.commission_rate,
                bootstrap_n=self.bootstrap_n,
                bootstrap_seed=self.bootstrap_seed,
            )

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
                'commission_rate': self.commission_rate,
                'race_safe_split': self.race_safe,
                'settlement_price': 'market_odds_at_tip' if self.price_at_tip else 'starting_price',
                'prediction_timestamp_policy': (
                    'racecard price as published (~08:00 local on race morning)'
                    if self.price_at_tip else
                    'SP (closing) — NOT available at prediction time; legacy default'
                ),
                'bootstrap_n': self.bootstrap_n,
                'bootstrap_seed': self.bootstrap_seed,
                'model_under_test': 'RacingMLModel refit per fold — NOT the production retrain_v2 artifact',
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


def _make_synthetic_races(n_days: int = 60, races_per_day: int = 4,
                          runners_per_race: int = 8, seed: int = 42) -> pd.DataFrame:
    """Fixture generator: a well-formed card with exactly one winner per race.

    Used by the self-test so the split and metric logic is exercised without a
    database, model artifact, or the heavy training stack.
    """
    rng = np.random.default_rng(seed)
    rows = []
    base = datetime(2026, 1, 1)
    for d in range(n_days):
        race_date = base + timedelta(days=d)
        for r in range(races_per_day):
            winner = int(rng.integers(0, runners_per_race))
            for h in range(runners_per_race):
                rows.append({
                    'race_id': f'{race_date.date()}|SYN|{r}',
                    'race_date': race_date,
                    'track': 'SYN',
                    'race_number': r,
                    'horse_name': f'H{h}',
                    'won': 1 if h == winner else 0,
                    'market_odds': float(round(2.0 + h * 1.5, 2)),
                    'starting_price': float(round(2.2 + h * 1.5, 2)),
                })
    return pd.DataFrame(rows)


def _self_test():
    print("walk_forward_backtest self-test")

    df = _make_synthetic_races()
    runners = 8
    total = len(df)
    assert total == 60 * 4 * runners, total
    assert df.groupby('race_id')['won'].sum().eq(1).all(), "fixture must have one winner per race"
    print(f"  fixture: {total} runners across {df['race_id'].nunique()} races")

    # --- splitter: legacy row cut can truncate a race; race_safe must not ---
    legacy = WalkForwardSplitter(min_train_size=200, test_size=100, gap_days=7,
                                 race_safe=False)
    legacy_folds = list(legacy.split(df, date_column='race_date'))
    assert legacy_folds, "legacy splitter produced no folds"

    # Each splitter's indices address its OWN canonical frame, so measure each
    # against the frame it actually produced.
    legacy_frame = legacy.prepare_frame(df)
    legacy_keys = _race_key_series(legacy_frame, 'race_id')
    legacy_partial = 0
    for tr, te, meta in legacy_folds:
        counts = legacy_keys.iloc[te].value_counts()
        legacy_partial += int((counts != runners).sum())
    print(f"  legacy row cut: {legacy_partial} partially-included race(s) across {len(legacy_folds)} folds")

    safe = WalkForwardSplitter(min_train_size=200, test_size=100, gap_days=7,
                               race_safe=True, race_column='race_id')
    safe_frame = safe.prepare_frame(df)
    safe_keys = safe_frame['__race_key']
    safe_folds = list(safe.split(df, date_column='race_date'))
    assert safe_folds, "race-safe splitter produced no folds"
    for tr, te, meta in safe_folds:
        assert meta['n_races_spanning_folds'] == 0, meta
        assert meta['race_safe'] is True, meta
        assert meta['actual_gap_days'] >= 1, meta
        # every race in the test fold must be present in full
        counts = safe_keys.iloc[te].value_counts()
        assert (counts == runners).all(), f"partial race in test fold: {counts[counts != runners].to_dict()}"
        # and no race may appear on both sides of the cut
        assert not (set(safe_keys.iloc[tr].values) & set(safe_keys.iloc[te].values))
    assert legacy_partial > 0, \
        "fixture no longer exercises the truncation the race-safe split exists to fix"
    print(f"  race_safe=True: {len(safe_folds)} folds, 0 partial fields, 0 races spanning a split")

    # --- indices must address the frame the caller slices (shuffled input) ---
    shuffled = df.sample(frac=1.0, random_state=3).reset_index(drop=True)
    for sp in (WalkForwardSplitter(min_train_size=200, test_size=100, gap_days=7,
                                   race_safe=False),
               WalkForwardSplitter(min_train_size=200, test_size=100, gap_days=7,
                                   race_safe=True, race_column='race_id')):
        frame = sp.prepare_frame(shuffled)
        n_folds_checked = 0
        for tr, te, meta in sp.split(shuffled, date_column='race_date'):
            train_max = frame['race_date'].iloc[tr].max()
            test_min = frame['race_date'].iloc[te].min()
            assert train_max < test_min, (
                f"leak after slicing: train_max={train_max} >= test_min={test_min}")
            assert (test_min - train_max).days >= sp.gap_days, (
                f"purge gap violated: {(test_min - train_max).days}d < {sp.gap_days}d")
            n_folds_checked += 1
        assert n_folds_checked > 0
    print("  index alignment: train/test dates stay disjoint and gapped when input is unsorted")

    # --- commission: 0.0 must reproduce the original gross arithmetic ---
    won = np.array([1, 0, 0, 1, 0])
    odds = np.array([3.0, 5.0, 2.0, 4.0, 6.0])
    pnl0 = compute_bet_pnl(won, odds, stake=100.0, commission_rate=0.0)
    legacy_profit = float((won * odds * 100.0).sum()) - 5 * 100.0
    assert abs(pnl0.sum() - legacy_profit) < 1e-9, (pnl0.sum(), legacy_profit)
    pnl8 = compute_bet_pnl(won, odds, stake=100.0, commission_rate=0.08)
    assert pnl8.sum() < pnl0.sum(), "commission must reduce profit"
    assert abs(pnl8[1] - (-100.0)) < 1e-9, "commission must not touch losing bets"
    print(f"  commission: 0.0 reproduces gross ({legacy_profit:.0f}); 8% cuts to {pnl8.sum():.0f}")

    # --- drawdown / profit factor ---
    dd, dd_pct = compute_max_drawdown(np.array([100.0, -50.0, -30.0, 20.0]))
    assert abs(dd - 80.0) < 1e-9, dd
    assert abs(compute_max_drawdown(np.array([]))[0]) < 1e-9
    pf = compute_profit_factor(np.array([100.0, -50.0, -30.0, 20.0]))
    assert abs(pf - 1.5) < 1e-9, pf
    assert compute_profit_factor(np.array([10.0, 5.0])) is None, "no losses ⇒ undefined"
    print(f"  max_drawdown=80 on a 100/-50/-30/20 path; profit_factor=1.5")

    # --- bootstrap CI: seeded, reproducible, and brackets the point estimate ---
    rng = np.random.default_rng(7)
    big_pnl = np.where(rng.random(4000) < 0.25, 300.0, -100.0)
    ci_a = bootstrap_roi_ci(big_pnl, n_boot=500, seed=42)
    ci_b = bootstrap_roi_ci(big_pnl, n_boot=500, seed=42)
    assert ci_a['ci_95'] == ci_b['ci_95'], "same seed must reproduce"
    point_roi = big_pnl.sum() / (big_pnl.size * 100.0) * 100.0
    assert ci_a['ci_95'][0] <= point_roi <= ci_a['ci_95'][1], (ci_a, point_roi)
    assert bootstrap_roi_ci(np.array([]), n_boot=100)['n_boot'] == 0
    flat = bootstrap_roi_ci(np.where(rng.random(2000) < 0.25, 300.0, -100.0),
                            n_boot=500, seed=42)
    assert flat['spans_zero'] is not None
    print(f"  bootstrap: seeded reproducible, 95%CI={ci_a['ci_95']} brackets point ROI {point_roi:.1f}%")

    # --- fold metrics keep every legacy key and add the new ones ---
    y_true = df['won'].values[:800]
    y_pred = np.clip(np.random.default_rng(1).random(800), 0.01, 0.99)
    odds_arr = df['market_odds'].values[:800]
    m = compute_fold_metrics(y_true, y_pred, odds_arr, bootstrap_n=200)
    for legacy_key in ('auc_roc', 'brier', 'log_loss', 'ece', 'roi_at_thresholds'):
        assert legacy_key in m, legacy_key
    t = m['roi_at_thresholds']['0.10']
    for k in ('threshold', 'n_bets', 'hit_rate', 'avg_odds_winners', 'roi_pct', 'profit',
              'avg_odds_all', 'max_drawdown', 'max_drawdown_pct', 'profit_factor',
              'roi_ci95_bootstrap', 'commission_rate'):
        assert k in t, k
    empty = compute_fold_metrics(y_true, np.zeros(800), odds_arr, thresholds=[0.9])
    assert empty['roi_at_thresholds']['0.90']['n_bets'] == 0
    assert 'profit_factor' in empty['roi_at_thresholds']['0.90'], "zero-bet branch must match schema"
    print("  compute_fold_metrics: legacy keys intact, 6 new keys present, zero-bet branch matches schema")

    # --- flags default OFF (env save/restore, per tips_day_aggregates.py) ---
    prev_rs = os.environ.pop('STRIDE_WFB_RACE_SAFE_SPLIT', None)
    prev_pt = os.environ.pop('STRIDE_WFB_PRICE_AT_TIP', None)
    try:
        b = WalkForwardBacktester(verbose=False)
        assert b.race_safe is False, "race-safe split must default OFF"
        assert b.price_at_tip is False, "price-at-tip must default OFF"
        assert b.commission_rate == 0.0, "commission must default to 0.0"
        os.environ['STRIDE_WFB_RACE_SAFE_SPLIT'] = 'true'
        os.environ['STRIDE_WFB_PRICE_AT_TIP'] = 'yes'
        b2 = WalkForwardBacktester(verbose=False)
        assert b2.race_safe is True and b2.price_at_tip is True, "flags must parse Variant B"
        os.environ['STRIDE_WFB_RACE_SAFE_SPLIT'] = 'nonsense'
        assert WalkForwardBacktester(verbose=False).race_safe is False, "unparseable ⇒ off"
    finally:
        for name, prev in (('STRIDE_WFB_RACE_SAFE_SPLIT', prev_rs),
                           ('STRIDE_WFB_PRICE_AT_TIP', prev_pt)):
            if prev is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prev
    print("  flags: both default OFF, parse true/yes, unparseable falls back to OFF")

    print("All tests completed successfully.")


if __name__ == '__main__':
    import argparse

    if '--self-test' in sys.argv:
        _self_test()
        sys.exit(0)

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
    parser.add_argument('--self-test', action='store_true',
                        help='Run the built-in self-test (no database required)')
    parser.add_argument('--commission-rate', type=float, default=0.0,
                        help='Commission on net winnings, e.g. 0.08 for an 8%% exchange market (default: 0.0)')
    parser.add_argument('--race-safe-split', action='store_true',
                        help='Keep every runner of a race inside one fold (also STRIDE_WFB_RACE_SAFE_SPLIT)')
    parser.add_argument('--price-at-tip', action='store_true',
                        help='Settle at the racecard price, not SP (also STRIDE_WFB_PRICE_AT_TIP)')
    parser.add_argument('--bootstrap-n', type=int, default=2000,
                        help='Bootstrap resamples for the ROI CI, 0 disables (default: 2000)')
    parser.add_argument('--bootstrap-seed', type=int, default=42,
                        help='Seed for the ROI bootstrap (default: 42)')
    args = parser.parse_args()

    backtester = WalkForwardBacktester(
        min_train_size=args.min_train,
        test_size=args.test_size,
        step_size=args.step_size,
        gap_days=args.gap_days,
        use_optuna=args.use_optuna,
        verbose=True,
        commission_rate=args.commission_rate,
        race_safe=True if args.race_safe_split else None,
        price_at_tip=True if args.price_at_tip else None,
        bootstrap_n=args.bootstrap_n,
        bootstrap_seed=args.bootstrap_seed,
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
