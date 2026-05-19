#!/usr/bin/env python3
"""Portfolio Risk Management Module — manages portfolio-level risk for horse racing betting, controlling overall exposure, position sizing, and stake optimization across multiple concurrent bets."""

import math
import random
import uuid
from enum import Enum
from datetime import datetime, timedelta
from typing import List, Dict, Optional


class StakingMethod(Enum):
    FLAT = 'flat'
    KELLY = 'kelly'
    FRACTIONAL_KELLY = 'fractional_kelly'
    PROPORTIONAL = 'proportional'
    PERCENTAGE = 'percentage'


def calculate_stake(method: StakingMethod, bankroll: float, odds: float,
                    win_probability: float, edge: float, **kwargs) -> float:
    """Calculate stake based on staking method."""
    if bankroll <= 0 or odds <= 1.0 or win_probability <= 0 or win_probability >= 1.0:
        return 0.0

    if method == StakingMethod.FLAT:
        flat_amount = kwargs.get('flat_amount', 100.0)
        return min(flat_amount, bankroll)

    elif method == StakingMethod.KELLY:
        b = odds - 1.0
        q = 1.0 - win_probability
        kelly_pct = (b * win_probability - q) / b
        if kelly_pct <= 0:
            return 0.0
        return kelly_pct * bankroll

    elif method == StakingMethod.FRACTIONAL_KELLY:
        fraction = kwargs.get('kelly_fraction', 0.25)
        b = odds - 1.0
        q = 1.0 - win_probability
        kelly_pct = (b * win_probability - q) / b
        if kelly_pct <= 0:
            return 0.0
        return kelly_pct * fraction * bankroll

    elif method == StakingMethod.PROPORTIONAL:
        if edge <= 0:
            return 0.0
        base_pct = edge / 100.0
        return base_pct * bankroll

    elif method == StakingMethod.PERCENTAGE:
        pct = kwargs.get('percentage', 2.0)
        return (pct / 100.0) * bankroll

    return 0.0


class PortfolioRiskManager:
    def __init__(self, bankroll: float = 10000.0, max_daily_exposure_pct: float = 15.0,
                 max_single_bet_pct: float = 5.0, max_concurrent_bets: int = 10):
        """
        Args:
            bankroll: total bankroll
            max_daily_exposure_pct: max % of bankroll at risk in one day
            max_single_bet_pct: max % of bankroll on a single bet
        """
        self.bankroll = bankroll
        self.max_daily_exposure = bankroll * max_daily_exposure_pct / 100
        self.max_single_bet = bankroll * max_single_bet_pct / 100
        self.max_concurrent_bets = max_concurrent_bets
        self.current_exposure = 0.0
        self.daily_bets: List[dict] = []
        self.history: List[dict] = []

    def assess_bet(self, bet: dict) -> dict:
        """
        Assess whether a bet fits within portfolio risk limits.

        Args:
            bet: dict with keys: race_id, horse_name, odds, edge_pct, win_probability, stake
        """
        race_id = bet.get('race_id', '')
        horse_name = bet.get('horse_name', '')
        odds = bet.get('odds', 1.0)
        edge_pct = bet.get('edge_pct', 0.0)
        win_prob = bet.get('win_probability', 0.0)
        suggested_stake = bet.get('suggested_stake', 0.0)
        confidence = bet.get('confidence', 0.5)

        kelly_frac = self.kelly_criterion(odds, win_prob)
        kelly_stake = kelly_frac * self.bankroll

        adjusted_stake = suggested_stake
        reasons = []

        active_bets = [b for b in self.daily_bets if b.get('status', 'active') == 'active']

        if len(active_bets) >= self.max_concurrent_bets:
            return {
                'approved': False,
                'adjusted_stake': 0.0,
                'reason': f'Max concurrent bets ({self.max_concurrent_bets}) reached',
                'risk_metrics': self._build_risk_metrics(0.0, kelly_frac),
            }

        if adjusted_stake > self.max_single_bet:
            adjusted_stake = self.max_single_bet
            reasons.append(f'Capped to max single bet ({self.max_single_bet:.2f})')

        remaining_budget = self.max_daily_exposure - self.current_exposure
        if remaining_budget <= 0:
            return {
                'approved': False,
                'adjusted_stake': 0.0,
                'reason': 'Daily exposure limit reached',
                'risk_metrics': self._build_risk_metrics(0.0, kelly_frac),
            }

        if adjusted_stake > remaining_budget:
            adjusted_stake = remaining_budget
            reasons.append(f'Reduced to fit daily budget ({remaining_budget:.2f} remaining)')

        confidence_factor = 0.5 + 0.5 * confidence
        scaled_stake = adjusted_stake * confidence_factor
        if scaled_stake < adjusted_stake:
            reasons.append(f'Scaled by confidence ({confidence:.2f} -> factor {confidence_factor:.2f})')
            adjusted_stake = scaled_stake

        adjusted_stake = round(adjusted_stake, 2)
        if adjusted_stake < 1.0:
            return {
                'approved': False,
                'adjusted_stake': 0.0,
                'reason': 'Stake too small after adjustments',
                'risk_metrics': self._build_risk_metrics(0.0, kelly_frac),
            }

        if edge_pct <= 0 and kelly_frac <= 0:
            return {
                'approved': False,
                'adjusted_stake': 0.0,
                'reason': 'No positive edge detected',
                'risk_metrics': self._build_risk_metrics(0.0, kelly_frac),
            }

        reason = '; '.join(reasons) if reasons else 'Approved within all limits'

        bet_record = {
            'bet_id': str(uuid.uuid4())[:8],
            'race_id': race_id,
            'horse_name': horse_name,
            'odds': odds,
            'edge_pct': edge_pct,
            'win_probability': win_prob,
            'stake': adjusted_stake,
            'confidence': confidence,
            'kelly_fraction': kelly_frac,
            'timestamp': datetime.now().isoformat(),
            'status': 'active',
            'result': None,
        }
        self.daily_bets.append(bet_record)
        self.current_exposure += adjusted_stake

        return {
            'approved': True,
            'adjusted_stake': adjusted_stake,
            'reason': reason,
            'risk_metrics': self._build_risk_metrics(adjusted_stake, kelly_frac),
        }

    def _build_risk_metrics(self, position_stake: float, kelly_frac: float) -> dict:
        portfolio = self.compute_portfolio_risk()
        return {
            'current_exposure_pct': (self.current_exposure / self.bankroll * 100) if self.bankroll > 0 else 0.0,
            'remaining_daily_budget': max(0.0, self.max_daily_exposure - self.current_exposure),
            'portfolio_ev': portfolio['expected_value'],
            'portfolio_var': portfolio['variance'],
            'kelly_fraction': kelly_frac,
            'position_size_pct': (position_stake / self.bankroll * 100) if self.bankroll > 0 else 0.0,
        }

    def kelly_criterion(self, odds: float, win_probability: float, fraction: float = 0.25) -> float:
        """
        Calculate Kelly Criterion stake as fraction of bankroll.

        Kelly % = (bp - q) / b
        where b = odds - 1, p = win_probability, q = 1 - p
        """
        if odds <= 1.0 or win_probability <= 0 or win_probability >= 1.0:
            return 0.0

        b = odds - 1.0
        p = win_probability
        q = 1.0 - p

        full_kelly = (b * p - q) / b
        if full_kelly <= 0:
            return 0.0

        return full_kelly * fraction

    def compute_portfolio_risk(self) -> dict:
        """
        Compute overall portfolio risk metrics across active bets.

        Returns dict with: total_exposure, exposure_pct, expected_value, variance, sharpe_ratio, max_drawdown.
        """
        active = [b for b in self.daily_bets if b.get('status', 'active') == 'active']

        if not active:
            return {
                'total_exposure': 0.0,
                'exposure_pct': 0.0,
                'expected_value': 0.0,
                'variance': 0.0,
                'sharpe_ratio': 0.0,
                'max_drawdown_risk': 0.0,
                'correlation_risk': 'low',
                'n_active_bets': 0,
            }

        total_exposure = sum(b['stake'] for b in active)
        exposure_pct = (total_exposure / self.bankroll * 100) if self.bankroll > 0 else 0.0

        ev = 0.0
        variance = 0.0
        for b in active:
            stake = b['stake']
            odds = b['odds']
            wp = b['win_probability']
            ev += stake * (wp * odds - 1.0)
            variance += (stake ** 2) * odds * (1.0 - 1.0 / odds)

        sharpe = (ev / math.sqrt(variance)) if variance > 0 else 0.0

        max_dd = self._estimate_max_drawdown(active)

        correlation_risk = self._assess_correlation_risk(active)

        return {
            'total_exposure': round(total_exposure, 2),
            'exposure_pct': round(exposure_pct, 2),
            'expected_value': round(ev, 2),
            'variance': round(variance, 2),
            'sharpe_ratio': round(sharpe, 4),
            'max_drawdown_risk': round(max_dd, 2),
            'correlation_risk': correlation_risk,
            'n_active_bets': len(active),
        }

    def _estimate_max_drawdown(self, active_bets: list, n_sims: int = 1000) -> float:
        """Estimate maximum drawdown risk via Monte Carlo simulation over the current active portfolio."""
        if not active_bets:
            return 0.0

        max_drawdowns = []
        for _ in range(n_sims):
            pnl = 0.0
            peak = 0.0
            max_dd = 0.0
            for b in active_bets:
                if random.random() < b['win_probability']:
                    pnl += b['stake'] * (b['odds'] - 1.0)
                else:
                    pnl -= b['stake']
                if pnl > peak:
                    peak = pnl
                dd = peak - pnl
                if dd > max_dd:
                    max_dd = dd
            max_drawdowns.append(max_dd)

        max_drawdowns.sort()
        idx_95 = int(0.95 * len(max_drawdowns))
        return max_drawdowns[min(idx_95, len(max_drawdowns) - 1)]

    def _assess_correlation_risk(self, active_bets: list) -> str:
        """Assess correlation risk based on concentration of bets on the same race or track."""
        if len(active_bets) <= 1:
            return 'low'

        race_ids = [b.get('race_id', '') for b in active_bets]
        tracks = set()
        race_counts: Dict[str, int] = {}
        for rid in race_ids:
            race_counts[rid] = race_counts.get(rid, 0) + 1
            parts = rid.split('_') if rid else []
            if parts:
                tracks.add(parts[0])

        max_same_race = max(race_counts.values()) if race_counts else 0

        if max_same_race >= 3:
            return 'high'

        if max_same_race >= 2 or (len(tracks) == 1 and len(active_bets) >= 3):
            return 'medium'

        return 'low'

    def optimize_stakes(self, bets: list) -> list:
        """
        Given a set of candidate bets, optimize stake allocation using fractional Kelly with portfolio constraints.

        Each bet dict should have: race_id, horse_name, odds, win_probability, edge_pct.
        """
        if not bets:
            return []

        kelly_fractions = []
        for b in bets:
            kf = self.kelly_criterion(b.get('odds', 1.0), b.get('win_probability', 0.0))
            kelly_fractions.append(kf)

        total_kelly = sum(kelly_fractions)
        remaining_budget = self.max_daily_exposure - self.current_exposure
        if remaining_budget <= 0:
            for b in bets:
                b['optimized_stake'] = 0.0
            return bets

        results = []
        for i, b in enumerate(bets):
            kf = kelly_fractions[i]
            if kf <= 0 or total_kelly <= 0:
                b['optimized_stake'] = 0.0
                results.append(b)
                continue

            proportion = kf / total_kelly
            raw_stake = proportion * remaining_budget

            raw_stake = min(raw_stake, self.max_single_bet)

            confidence = b.get('confidence', 0.5)
            confidence_factor = 0.5 + 0.5 * confidence
            raw_stake *= confidence_factor

            b['optimized_stake'] = round(max(0.0, raw_stake), 2)
            b['kelly_fraction'] = round(kf, 6)
            results.append(b)

        total_allocated = sum(b['optimized_stake'] for b in results)
        if total_allocated > remaining_budget and total_allocated > 0:
            scale = remaining_budget / total_allocated
            for b in results:
                b['optimized_stake'] = round(b['optimized_stake'] * scale, 2)

        n_eligible = sum(1 for b in results if b['optimized_stake'] > 0)
        if n_eligible > self.max_concurrent_bets:
            scored = [(b['optimized_stake'] * b.get('confidence', 0.5), i)
                      for i, b in enumerate(results) if b['optimized_stake'] > 0]
            scored.sort(reverse=True)
            keep_indices = {idx for _, idx in scored[:self.max_concurrent_bets]}
            for i, b in enumerate(results):
                if b['optimized_stake'] > 0 and i not in keep_indices:
                    b['optimized_stake'] = 0.0

        return results

    def record_result(self, bet_id: str, won: bool, actual_return: float):
        """Record bet result and update bankroll."""
        for b in self.daily_bets:
            if b.get('bet_id') == bet_id:
                b['status'] = 'settled'
                b['result'] = 'won' if won else 'lost'
                b['actual_return'] = actual_return
                b['settled_at'] = datetime.now().isoformat()

                if won:
                    profit = actual_return
                    self.bankroll += profit
                else:
                    self.bankroll -= b['stake']

                self.current_exposure = max(0.0, self.current_exposure - b['stake'])

                self.history.append(dict(b))
                self._update_limits()
                return

    def _update_limits(self):
        """Recalculate exposure limits after bankroll change."""
        self.max_single_bet = self.bankroll * 0.05
        self.max_daily_exposure = self.bankroll * 0.15

    def get_daily_summary(self) -> dict:
        """Return today's betting summary."""
        active = [b for b in self.daily_bets if b.get('status') == 'active']
        settled = [b for b in self.daily_bets if b.get('status') == 'settled']
        winners = [b for b in settled if b.get('result') == 'won']
        losers = [b for b in settled if b.get('result') == 'lost']

        total_staked = sum(b['stake'] for b in settled)
        total_returns = sum(b.get('actual_return', 0.0) for b in winners)
        total_lost = sum(b['stake'] for b in losers)
        net_pnl = total_returns - total_lost

        hit_rate = (len(winners) / len(settled) * 100) if settled else 0.0
        avg_odds_winners = (sum(b['odds'] for b in winners) / len(winners)) if winners else 0.0

        return {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'total_bets': len(self.daily_bets),
            'active_bets': len(active),
            'settled_bets': len(settled),
            'winners': len(winners),
            'losers': len(losers),
            'hit_rate_pct': round(hit_rate, 1),
            'total_staked': round(total_staked, 2),
            'total_returns': round(total_returns, 2),
            'net_pnl': round(net_pnl, 2),
            'roi_pct': round((net_pnl / total_staked * 100) if total_staked > 0 else 0.0, 2),
            'avg_odds_winners': round(avg_odds_winners, 2),
            'current_exposure': round(self.current_exposure, 2),
            'remaining_budget': round(max(0.0, self.max_daily_exposure - self.current_exposure), 2),
            'bankroll': round(self.bankroll, 2),
        }

    def get_performance_report(self, period_days: int = 30) -> dict:
        """Return performance metrics over the last N days. Returns dict with ROI, hit_rate, avg_odds, profit_loss."""
        cutoff = (datetime.now() - timedelta(days=period_days)).isoformat()
        period_bets = [b for b in self.history
                       if b.get('timestamp', '') >= cutoff and b.get('status') == 'settled']

        if not period_bets:
            return {
                'period_days': period_days,
                'total_bets': 0,
                'winners': 0,
                'losers': 0,
                'hit_rate_pct': 0.0,
                'total_staked': 0.0,
                'total_returns': 0.0,
                'net_pnl': 0.0,
                'roi_pct': 0.0,
                'avg_odds': 0.0,
                'avg_stake': 0.0,
                'sharpe_ratio': 0.0,
                'max_drawdown': 0.0,
                'longest_losing_streak': 0,
                'best_bet': None,
                'worst_bet': None,
            }

        winners = [b for b in period_bets if b.get('result') == 'won']
        losers = [b for b in period_bets if b.get('result') == 'lost']

        total_staked = sum(b['stake'] for b in period_bets)
        total_returns = sum(b.get('actual_return', 0.0) for b in winners)
        total_lost = sum(b['stake'] for b in losers)
        net_pnl = total_returns - total_lost

        avg_odds = sum(b['odds'] for b in period_bets) / len(period_bets)
        avg_stake = total_staked / len(period_bets)

        pnl_series = []
        for b in period_bets:
            if b.get('result') == 'won':
                pnl_series.append(b.get('actual_return', 0.0))
            else:
                pnl_series.append(-b['stake'])

        mean_pnl = sum(pnl_series) / len(pnl_series) if pnl_series else 0.0
        if len(pnl_series) > 1:
            var_pnl = sum((x - mean_pnl) ** 2 for x in pnl_series) / (len(pnl_series) - 1)
            std_pnl = math.sqrt(var_pnl)
        else:
            std_pnl = 0.0
        sharpe = (mean_pnl / std_pnl) if std_pnl > 0 else 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnl_series:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        current_streak = 0
        longest_losing = 0
        for b in period_bets:
            if b.get('result') == 'lost':
                current_streak += 1
                longest_losing = max(longest_losing, current_streak)
            else:
                current_streak = 0

        best_bet = max(period_bets, key=lambda x: x.get('actual_return', 0.0) if x.get('result') == 'won' else -x['stake'], default=None)
        worst_bet = min(period_bets, key=lambda x: x.get('actual_return', 0.0) if x.get('result') == 'won' else -x['stake'], default=None)

        return {
            'period_days': period_days,
            'total_bets': len(period_bets),
            'winners': len(winners),
            'losers': len(losers),
            'hit_rate_pct': round(len(winners) / len(period_bets) * 100, 1),
            'total_staked': round(total_staked, 2),
            'total_returns': round(total_returns, 2),
            'net_pnl': round(net_pnl, 2),
            'roi_pct': round((net_pnl / total_staked * 100) if total_staked > 0 else 0.0, 2),
            'avg_odds': round(avg_odds, 2),
            'avg_stake': round(avg_stake, 2),
            'sharpe_ratio': round(sharpe, 4),
            'max_drawdown': round(max_dd, 2),
            'longest_losing_streak': longest_losing,
            'best_bet': {
                'horse_name': best_bet.get('horse_name', ''),
                'odds': best_bet.get('odds', 0),
                'stake': best_bet.get('stake', 0),
                'return': best_bet.get('actual_return', 0),
            } if best_bet else None,
            'worst_bet': {
                'horse_name': worst_bet.get('horse_name', ''),
                'odds': worst_bet.get('odds', 0),
                'stake': worst_bet.get('stake', 0),
                'return': worst_bet.get('actual_return', 0) if worst_bet.get('result') == 'won' else -worst_bet.get('stake', 0),
            } if worst_bet else None,
        }

    def reset_daily(self):
        """Reset daily tracking. Call at the start of each new day."""
        for b in self.daily_bets:
            if b.get('status') == 'active':
                self.history.append(dict(b))
        self.daily_bets = []
        self.current_exposure = 0.0


if __name__ == '__main__':
    import json

    mgr = PortfolioRiskManager(bankroll=10000.0)

    test_bets = [
        {
            'race_id': 'flemington_R3',
            'horse_name': 'Thunder Strike',
            'odds': 4.5,
            'edge_pct': 8.2,
            'win_probability': 0.30,
            'suggested_stake': 300.0,
            'confidence': 0.75,
        },
        {
            'race_id': 'randwick_R5',
            'horse_name': 'Ocean Dream',
            'odds': 7.0,
            'edge_pct': 5.5,
            'win_probability': 0.18,
            'suggested_stake': 200.0,
            'confidence': 0.60,
        },
        {
            'race_id': 'flemington_R3',
            'horse_name': 'Royal Ace',
            'odds': 3.2,
            'edge_pct': 3.0,
            'win_probability': 0.35,
            'suggested_stake': 400.0,
            'confidence': 0.85,
        },
    ]

    print("=== Portfolio Risk Manager Test ===\n")

    print("Kelly Criterion tests:")
    print(f"  Odds 4.5, WP 0.30, quarter Kelly: {mgr.kelly_criterion(4.5, 0.30):.4f}")
    print(f"  Odds 7.0, WP 0.18, quarter Kelly: {mgr.kelly_criterion(7.0, 0.18):.4f}")
    print(f"  Odds 2.0, WP 0.40, full Kelly:    {mgr.kelly_criterion(2.0, 0.40, fraction=1.0):.4f}")
    print()

    print("Assessing individual bets:")
    for bet in test_bets:
        result = mgr.assess_bet(bet)
        print(f"  {bet['horse_name']}: approved={result['approved']}, "
              f"stake={result['adjusted_stake']:.2f}, reason='{result['reason']}'")
    print()

    print("Portfolio risk:")
    print(json.dumps(mgr.compute_portfolio_risk(), indent=2))
    print()

    print("Staking methods test:")
    for method in StakingMethod:
        stake = calculate_stake(method, 10000.0, 4.5, 0.30, 8.0)
        print(f"  {method.value}: ${stake:.2f}")
    print()

    print("Daily summary:")
    print(json.dumps(mgr.get_daily_summary(), indent=2))
