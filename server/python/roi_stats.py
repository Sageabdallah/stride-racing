#!/usr/bin/env python3
"""Shared backtest statistics: CIs, drawdown, streaks, concentration, multi-comparison.

Single source of truth for the uncertainty and risk-path numbers every STRIDE
backtest report must carry (roi-roadmap task 02). Pure functions over per-bet
return sequences — no database, no model artifacts, numpy only.

Conventions:
  - Per-bet return, flat staking, in UNITS: a winner pays ``(odds - 1)`` and a
    loser pays ``-1``. The net variant charges commission on net winnings:
    ``(odds - 1) * (1 - commission)`` (AU exchange convention; commission never
    touches a losing bet). This is the settlement contract from task 01.
  - ROI figures are PERCENT (profit / staked * 100), matching the repo's
    existing backtest output.
  - The bootstrap resamples BETS, never runners, and is seeded so a rerun
    reproduces byte-identically.
  - A result is reportable only when the lower CI bound is above zero AND the
    sample clears MIN_BETS_REPORTABLE (the floor shadow_pl_tracker already
    uses). Anything else is NOT_REPORTABLE — never rounded into a win.

Run ``python roi_stats.py`` for the built-in self-test; pytest coverage lives
in ``test_roi_stats.py``.
"""

import math
import os
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# Same floor shadow_pl_tracker.py and ship_criteria.py use.
MIN_BETS_REPORTABLE = 200

DEFAULT_CONFIDENCE = 0.95
DEFAULT_N_BOOT = 10000          # spec floor: >= 10k resamples
DEFAULT_SEED = 42
DEFAULT_COMMISSION = 0.08       # until task 01's plumbing lands

_EULER_MASCHERONI = 0.5772156649015329


def commission_rate_from_env(default: float = DEFAULT_COMMISSION) -> float:
    """STRIDE_COMMISSION_RATE, default 0.08 until task 01's plumbing merges."""
    raw = os.environ.get('STRIDE_COMMISSION_RATE')
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if not (0.0 <= value < 1.0):
        return default
    return value


def per_bet_returns(won: Sequence[float], odds: Sequence[float],
                    commission: float = 0.0) -> np.ndarray:
    """Flat-stake per-bet returns in units. commission=0.0 is gross."""
    won = np.asarray(won, dtype=float)
    odds = np.asarray(odds, dtype=float)
    net_win = (odds - 1.0) * (1.0 - commission)
    return np.where(won == 1, net_win, -1.0)


def _bootstrap_roi_distribution(returns: np.ndarray, n_boot: int,
                                seed: int) -> np.ndarray:
    """Vectorised resample-with-replacement ROI distribution, over BETS."""
    n = returns.size
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    return returns[idx].mean(axis=1) * 100.0


def roi_ci(per_bet_returns_seq: Sequence[float],
           confidence: float = DEFAULT_CONFIDENCE,
           n_boot: int = DEFAULT_N_BOOT,
           seed: int = DEFAULT_SEED) -> Dict[str, Any]:
    """Bootstrap CI + normal-approx SE for ROI from per-bet unit returns.

    The CI is the percentile bootstrap over resampled bets; the SE is the
    normal approximation (sample SD / sqrt(n)). Both are reported: the SE is
    what the z-score and Bonferroni correction consume, the bootstrap CI is
    what ``spans_zero`` and reportability consume.
    """
    returns = np.asarray(per_bet_returns_seq, dtype=float)
    n = returns.size
    out: Dict[str, Any] = {
        'n': int(n),
        'roi': None,
        'se': None,
        'ci95': [None, None],
        'ci95_normal': [None, None],
        'z': None,
        'p_roi_le_zero': None,
        'spans_zero': None,
        'confidence': confidence,
        'n_boot': 0,
        'seed': seed,
    }
    if n == 0:
        return out

    roi = float(returns.mean()) * 100.0
    se = float(returns.std(ddof=1) / math.sqrt(n)) * 100.0 if n > 1 else 0.0
    out['roi'] = round(roi, 2)
    out['se'] = round(se, 2)
    out['z'] = round(roi / se, 3) if se > 0 else None

    # Normal-approx interval alongside the bootstrap: the two diverge when the
    # return distribution is a lattice (few winner prices), which is exactly
    # the small-sample racing case, so both are always carried.
    z_crit = float(NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0))
    out['ci95_normal'] = [round(roi - z_crit * se, 2),
                          round(roi + z_crit * se, 2)]

    if n_boot > 0:
        dist = _bootstrap_roi_distribution(returns, n_boot, seed)
        alpha = 1.0 - confidence
        lo = float(np.percentile(dist, 100 * alpha / 2))
        hi = float(np.percentile(dist, 100 * (1 - alpha / 2)))
        out['ci95'] = [round(lo, 2), round(hi, 2)]
        out['p_roi_le_zero'] = round(float((dist <= 0.0).mean()), 4)
        out['spans_zero'] = bool(lo <= 0.0 <= hi)
        out['n_boot'] = int(n_boot)
    return out


def max_drawdown_and_streaks(results_sequence: Sequence[float]) -> Dict[str, Any]:
    """Risk path of a per-bet return sequence, consumed in the order given.

    Returns max drawdown (units, peak-to-trough of cumulative P&L), the
    observed max losing streak, and the expected max losing streak under the
    observed strike rate — the standard longest-run approximation

        E[L] ≈ ln(n·(1−q)) / ln(1/q) + γ / ln(1/q) − 1/2

    (q = losing-bet fraction, γ = Euler–Mascheroni). At a 9.9% strike over
    142 bets this yields ≈ 30, matching the evidence base.
    """
    returns = np.asarray(results_sequence, dtype=float)
    n = returns.size
    out: Dict[str, Any] = {
        'n': int(n),
        'max_drawdown': 0.0,
        'max_losing_streak': 0,
        'expected_max_losing_streak': None,
        'strike_rate': None,
    }
    if n == 0:
        return out

    equity = np.cumsum(returns)
    running_peak = np.maximum.accumulate(equity)
    out['max_drawdown'] = round(float((running_peak - equity).max()), 2)

    losses = returns < 0
    streak = max_streak = 0
    for lost in losses:
        streak = streak + 1 if lost else 0
        max_streak = max(max_streak, streak)
    out['max_losing_streak'] = int(max_streak)

    q = float(losses.mean())
    out['strike_rate'] = round(1.0 - q, 4)
    if q <= 0.0:
        out['expected_max_losing_streak'] = 0.0
    elif q >= 1.0 or n < 2:
        out['expected_max_losing_streak'] = float(max_streak)
    else:
        inv = 1.0 / math.log(1.0 / q)
        expected = math.log(n * (1.0 - q)) * inv + _EULER_MASCHERONI * inv - 0.5
        out['expected_max_losing_streak'] = round(max(expected, 0.0), 1)
    return out


def winner_concentration(per_bet_returns_seq: Sequence[float],
                         ks: Sequence[int] = (1, 2, 3)) -> Dict[str, Any]:
    """ROI with the top-k winning bets removed, k = 1..3.

    A strategy whose ROI flips sign when one or two horses are removed has no
    measurable edge — it has one lucky result. Returns None for a k that
    exceeds the number of winners.
    """
    returns = np.asarray(per_bet_returns_seq, dtype=float)
    n = returns.size
    out: Dict[str, Any] = {f'roi_minus_top{k}': None for k in ks}
    if n == 0:
        return out
    winners = np.sort(returns[returns > 0])[::-1]
    for k in ks:
        if winners.size < k:
            continue
        # Removing a bet removes its stake too: ROI over the remaining n-k bets.
        mask = np.ones(n, dtype=bool)
        for value in winners[:k]:
            idx = int(np.argmax((returns == value) & mask))
            mask[idx] = False
        kept_returns = returns[mask]
        out[f'roi_minus_top{k}'] = round(
            float(kept_returns.sum()) / kept_returns.size * 100.0, 2)
    return out


def multi_comparison_note(n_strategies: int, best_roi: float,
                          se: Optional[float],
                          alpha: float = 0.05) -> Dict[str, Any]:
    """Bonferroni honesty check for a best-of-N strategy sweep on one window.

    Picking the best of N bands inflates the apparent edge: the z a winner
    must clear is no longer 1.96 but the two-sided Bonferroni quantile
    Φ⁻¹(1 − α / 2N). Compares that bar against the observed z of the best
    strategy and returns a verdict string.
    """
    if n_strategies < 1:
        raise ValueError('n_strategies must be >= 1')
    z_required = float(NormalDist().inv_cdf(1.0 - alpha / (2.0 * n_strategies)))
    z_observed = (best_roi / se) if (se is not None and se > 0) else None

    if z_observed is None:
        verdict = 'INDETERMINATE'
    elif z_observed >= z_required:
        verdict = 'SURVIVES_BONFERRONI'
    else:
        verdict = 'NOT_SIGNIFICANT_AFTER_BONFERRONI'

    return {
        'n_strategies': int(n_strategies),
        'bonferroni_z_required': round(z_required, 3),
        'best_observed_z': round(z_observed, 3) if z_observed is not None else None,
        'verdict': verdict,
        'note': (
            f'Best of {n_strategies} strategies on one window needs z >= '
            f'{z_required:.2f} under Bonferroni; observed '
            + (f'{z_observed:.2f}.' if z_observed is not None else 'z unknown.')
        ),
    }


def ci_spans_zero(ci: Any) -> Optional[bool]:
    """Does a CI span zero? Accepts a roi_ci dict, a harness bootstrap dict,
    or a bare [lo, hi] pair. None when no usable CI is present."""
    if isinstance(ci, dict):
        if ci.get('spans_zero') is not None:
            return bool(ci['spans_zero'])
        ci = ci.get('ci95', ci.get('ci_95'))
    if isinstance(ci, (list, tuple)) and len(ci) == 2 and None not in ci:
        return bool(float(ci[0]) <= 0.0 <= float(ci[1]))
    return None


def ci_lower_bound(ci: Any) -> Optional[float]:
    """Lower bound of a 95% CI. Accepts a roi_ci/harness dict or a bare
    [lo, hi] pair. None when no usable CI is present."""
    if isinstance(ci, dict):
        ci = ci.get('ci95', ci.get('ci_95'))
    if isinstance(ci, (list, tuple)) and len(ci) == 2 and None not in ci:
        return float(ci[0])
    return None


def ci_is_positive(ci: Any) -> Optional[bool]:
    """Is the CI entirely above zero (lower bound > 0 — a significant win)?
    None when no usable CI. Reads the bounds, NOT any 'spans_zero' flag, so it
    distinguishes a below-zero CI from an above-zero one."""
    lo = ci_lower_bound(ci)
    return None if lo is None else lo > 0.0


def is_reportable(ci95: Any, n_bets: Optional[int],
                  min_bets: int = MIN_BETS_REPORTABLE) -> bool:
    """The reportability floor: lower CI bound > 0 AND bets >= min_bets."""
    if n_bets is None or n_bets < min_bets:
        return False
    lo = ci_lower_bound(ci95)
    return lo is not None and lo > 0.0


def summarise_bets(won: Sequence[float], odds: Sequence[float],
                   commission: float = 0.0,
                   n_boot: int = DEFAULT_N_BOOT,
                   seed: int = DEFAULT_SEED,
                   min_bets: int = MIN_BETS_REPORTABLE) -> Dict[str, Any]:
    """Full statistics block for one strategy: counts, gross/net ROI, SE, CI,
    risk path, concentration, and the reportability verdict.

    Per the settlement contract the CI, SE, z and reportability are computed
    on NET returns; the gross ROI is reported alongside for comparison with
    historical numbers.
    """
    won = np.asarray(won, dtype=float)
    odds = np.asarray(odds, dtype=float)
    n = int(won.size)
    wins = int(won.sum())

    gross = per_bet_returns(won, odds, commission=0.0)
    net = per_bet_returns(won, odds, commission=commission)

    ci = roi_ci(net, n_boot=n_boot, seed=seed)
    risk = max_drawdown_and_streaks(net)
    conc = winner_concentration(net)

    block: Dict[str, Any] = {
        'bets': n,
        'wins': wins,
        'strike_rate': round(wins / n * 100.0, 2) if n else 0.0,
        'roi_gross': round(float(gross.mean()) * 100.0, 2) if n else None,
        'roi_net': round(float(net.mean()) * 100.0, 2) if n else None,
        'commission': commission,
        'se': ci['se'],
        'ci95': ci['ci95'],
        'ci95_normal': ci['ci95_normal'],
        'z': ci['z'],
        'p_roi_le_zero': ci['p_roi_le_zero'],
        'max_drawdown': risk['max_drawdown'],
        'max_losing_streak': risk['max_losing_streak'],
        'expected_max_losing_streak': risk['expected_max_losing_streak'],
        'roi_minus_top1': conc['roi_minus_top1'],
        'roi_minus_top2': conc['roi_minus_top2'],
        'roi_minus_top3': conc['roi_minus_top3'],
        'bootstrap': {'n_boot': ci['n_boot'], 'seed': seed,
                      'resamples': 'bets'},
    }
    reportable = is_reportable(ci['ci95'], n, min_bets=min_bets)
    block['reportable'] = reportable
    if not reportable:
        reasons = []
        if n < min_bets:
            reasons.append(f'bets {n} < {min_bets}')
        spans = ci_spans_zero(ci)
        if spans is not False:
            reasons.append('CI lower bound <= 0' if spans
                           else 'no CI computed')
        block['reportable_status'] = 'NOT_REPORTABLE'
        block['reportable_reason'] = '; '.join(reasons)
    else:
        block['reportable_status'] = 'REPORTABLE'
        block['reportable_reason'] = None
    return block


def multi_comparison_block(blocks: Sequence[Dict[str, Any]],
                           alpha: float = 0.05) -> Optional[Dict[str, Any]]:
    """Sweep label for >1 strategy reported on the same window.

    Takes the per-strategy blocks from summarise_bets and attaches the
    Bonferroni bar against the best observed z. Returns None when fewer than
    two strategies have a measurable z.
    """
    measured = [b for b in blocks if b.get('z') is not None]
    if len(measured) <= 1:
        return None
    best = max(measured, key=lambda b: b['z'])
    note = multi_comparison_note(len(blocks), best['roi_net'], best['se'],
                                 alpha=alpha)
    return {
        'n_strategies': note['n_strategies'],
        'bonferroni_z_required': note['bonferroni_z_required'],
        'best_observed_z': note['best_observed_z'],
        'verdict': note['verdict'],
        'selection_caveat': True,
        'note': note['note'],
    }


def _self_test():
    print('roi_stats self-test')

    # The known case: 142 bets, 14 wins at avg odds 11.4 -> +12.3% gross,
    # SE ~28.5pp, 95% CI ~[-44, +68], z ~0.43, NOT_REPORTABLE.
    won = [1] * 14 + [0] * 128
    odds = [11.4] * 14 + [3.0] * 128
    block = summarise_bets(won, odds, commission=0.08, n_boot=10000, seed=42)
    assert abs(block['roi_gross'] - 12.39) < 0.5, block['roi_gross']
    assert abs(block['roi_net'] - 4.19) < 0.5, block['roi_net']
    assert abs(block['se'] - 26.5) < 1.5, block['se']
    lo, hi = block['ci95']
    assert -54 < lo < -40 and 45 < hi < 65, (lo, hi)
    # The documented [-43.6, +68.2] figure is the GROSS normal approx.
    gross_block = summarise_bets(won, odds, commission=0.0, n_boot=10000, seed=42)
    assert abs(gross_block['se'] - 28.5) < 1.5, gross_block['se']
    nlo, nhi = gross_block['ci95_normal']
    assert abs(nlo - (-43.6)) < 1.5 and abs(nhi - 68.2) < 1.5, (nlo, nhi)
    assert abs(gross_block['z'] - 0.43) < 0.05, gross_block['z']
    assert block['reportable'] is False
    assert block['reportable_status'] == 'NOT_REPORTABLE'
    print(f"  known case: net {block['roi_net']}%, CI {block['ci95']} "
          f"(normal {block['ci95_normal']}), "
          f"z={block['z']} -> {block['reportable_status']}")

    mc = multi_comparison_note(6, block['roi_net'], block['se'])
    assert abs(mc['bonferroni_z_required'] - 2.638) < 0.01
    assert mc['verdict'] == 'NOT_SIGNIFICANT_AFTER_BONFERRONI'
    print(f"  multi-comparison: required z={mc['bonferroni_z_required']}, "
          f"observed z={mc['best_observed_z']} -> {mc['verdict']}")

    print('All tests completed successfully.')


if __name__ == '__main__':
    _self_test()
