#!/usr/bin/env python3
"""
Extends market_analysis.py with velocity features, smart money detection,
MC simulation sigma modifiers, and field market confidence.

Used by mc_api.py to feed velocity features into Monte Carlo simulations.
"""

import math
from typing import Dict, List, Optional, Tuple
from datetime import datetime


def _get_opening_odds(runner: Dict) -> Optional[float]:
    """Extract opening odds from runner dict, trying multiple key names."""
    for key in ('opening_odds', 'openingOdds'):
        val = runner.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return None


def _get_current_odds(runner: Dict) -> Optional[float]:
    """Extract current odds from runner dict, trying multiple key names."""
    for key in ('win_odds', 'odds', 'market_odds', 'marketOdds'):
        val = runner.get(key)
        if val is not None:
            try:
                v = val
                if isinstance(v, list) and len(v) > 0:
                    first = v[0]
                    if isinstance(first, dict):
                        v = first.get('win_odds', first.get('odds'))
                    else:
                        v = first
                return float(v)
            except (ValueError, TypeError):
                continue
    return None


def _get_horse_name(runner: Dict) -> str:
    """Extract horse name from runner dict."""
    return runner.get('horseName') or runner.get('horse') or runner.get('name') or runner.get('horse_name', 'Unknown')


def _price_change_pct(opening: float, current: float) -> float:
    """Percentage change from opening to current. Positive = shortening (steam)."""
    if opening <= 1.0:
        return 0.0
    return ((opening - current) / opening) * 100.0


def _classify_momentum(pct: float) -> str:
    """Classify price momentum from percentage change."""
    if pct >= 25:
        return 'strong_steam'
    elif pct >= 10:
        return 'steam'
    elif pct <= -25:
        return 'strong_drift'
    elif pct <= -10:
        return 'drift'
    return 'steady'


def compute_field_market_confidence(all_runners: List[Dict]) -> float:
    """
    Measure how settled the market is across the whole field.

    Low variance means the market is confident and settled (returns 0.8-1.0).
    High variance means the market is reshuffling (returns 0.3-0.6).

    Returns float 0-1 representing field market confidence.
    """
    try:
        changes: List[float] = []
        for r in all_runners:
            opening = _get_opening_odds(r)
            current = _get_current_odds(r)
            if opening is not None and current is not None and opening > 1.0 and current > 1.0:
                changes.append(_price_change_pct(opening, current))

        if len(changes) < 2:
            return 0.5

        mean_change = sum(changes) / len(changes)
        variance = sum((c - mean_change) ** 2 for c in changes) / len(changes)
        std_dev = math.sqrt(variance)

        if std_dev <= 3.0:
            return min(1.0, 0.8 + (3.0 - std_dev) / 15.0)
        elif std_dev <= 8.0:
            return max(0.5, 0.8 - (std_dev - 3.0) * 0.06)
        elif std_dev <= 20.0:
            return max(0.3, 0.5 - (std_dev - 8.0) * 0.017)
        else:
            return 0.3
    except Exception:
        return 0.5


def compute_smart_money_score(
    price_change_pct: float,
    relative_move: float,
    is_longshot: bool,
    opening_odds: float,
) -> float:
    """
    Composite smart money score from 0-100.

    Scoring tiers:
    - Big shortening from long price = 80-100 (insider signal)
    - Moderate shortening from short price = 40-60 (expected money)
    - Stable price = 30-50 (neutral)
    - Drift = 10-30 (low)
    - Strong drift = 0-15 (very low)
    """
    try:
        score = 40.0

        if price_change_pct >= 25:
            score = 75.0
        elif price_change_pct >= 15:
            score = 60.0
        elif price_change_pct >= 10:
            score = 50.0
        elif price_change_pct >= 5:
            score = 45.0
        elif price_change_pct >= -5:
            score = 40.0
        elif price_change_pct >= -10:
            score = 30.0
        elif price_change_pct >= -25:
            score = 20.0
        else:
            score = 8.0

        if is_longshot and price_change_pct >= 15:
            score += 20.0
        elif is_longshot and price_change_pct >= 10:
            score += 12.0

        if opening_odds > 20 and price_change_pct >= 20:
            score += 10.0

        if relative_move > 10:
            score += min(relative_move * 0.5, 10.0)
        elif relative_move < -10:
            score -= min(abs(relative_move) * 0.3, 8.0)

        return max(0.0, min(100.0, round(score, 1)))
    except Exception:
        return 40.0


def compute_market_sigma_modifier(
    price_change_pct: float,
    smart_money_score: float,
    market_confidence: float,
) -> float:
    """
    Compute a modifier (0.85-1.15) for Monte Carlo simulation sigma/variance.

    - High market confidence (money agrees) -> lower sigma (0.85-0.95)
    - High uncertainty -> higher sigma (1.05-1.15)
    - Strong steam -> slightly lower sigma (market thinks horse is better)
    - Strong drift -> slightly higher sigma (market uncertain about horse)
    """
    try:
        modifier = 1.0

        if market_confidence >= 0.8:
            modifier -= (market_confidence - 0.8) * 0.5
        elif market_confidence <= 0.5:
            modifier += (0.5 - market_confidence) * 0.3

        if price_change_pct >= 20:
            modifier -= 0.06
        elif price_change_pct >= 10:
            modifier -= 0.03
        elif price_change_pct <= -20:
            modifier += 0.06
        elif price_change_pct <= -10:
            modifier += 0.03

        if smart_money_score >= 80:
            modifier -= 0.04
        elif smart_money_score >= 60:
            modifier -= 0.02
        elif smart_money_score <= 15:
            modifier += 0.03

        return max(0.85, min(1.15, round(modifier, 4)))
    except Exception:
        return 1.0


def _compute_history_features(odds_history: List[Dict]) -> Dict:
    """
    Compute velocity acceleration and final-window move from odds_history.

    Each entry in odds_history should have 'price' and 'timestamp' keys.
    Timestamp can be ISO string or Unix epoch.

    Returns dict with velocity_acceleration and final_30min_move.
    """
    result: Dict = {}
    try:
        if not odds_history or len(odds_history) < 2:
            return result

        points: List[Tuple[float, float]] = []
        for entry in odds_history:
            price = float(entry.get('price', 0))
            ts_raw = entry.get('timestamp')
            if ts_raw is None or price <= 0:
                continue
            if isinstance(ts_raw, (int, float)):
                ts = float(ts_raw)
            else:
                try:
                    dt = datetime.fromisoformat(str(ts_raw).replace('Z', '+00:00'))
                    ts = dt.timestamp()
                except (ValueError, TypeError):
                    continue
            points.append((ts, price))

        if len(points) < 2:
            return result

        points.sort(key=lambda x: x[0])

        velocities: List[float] = []
        for i in range(1, len(points)):
            dt = points[i][0] - points[i - 1][0]
            if dt <= 0:
                continue
            dp = points[i - 1][1] - points[i][1]
            vel = dp / (dt / 3600.0)
            velocities.append(vel)

        if len(velocities) >= 2:
            first_half = velocities[:len(velocities) // 2]
            second_half = velocities[len(velocities) // 2:]
            avg_first = sum(first_half) / len(first_half)
            avg_second = sum(second_half) / len(second_half)
            result['velocity_acceleration'] = round(avg_second - avg_first, 4)

        total_span = points[-1][0] - points[0][0]
        window = min(1800.0, total_span * 0.3)
        cutoff = points[-1][0] - window
        late_points = [(t, p) for t, p in points if t >= cutoff]
        if len(late_points) >= 2:
            first_price = late_points[0][1]
            last_price = late_points[-1][1]
            if first_price > 1.0:
                result['final_30min_move'] = round(((first_price - last_price) / first_price) * 100.0, 2)
    except Exception:
        pass
    return result


def compute_market_velocity_features(runner: Dict, all_runners: List[Dict]) -> Dict:
    """
    Main function: compute comprehensive market velocity features for a runner.

    Handles multiple field naming conventions. Falls back to defaults when data
    is missing or invalid.

    Returns dict of market velocity features. Always returns all keys with safe defaults.
    """
    defaults: Dict = {
        'steam_velocity': 0.0,
        'drift_velocity': 0.0,
        'price_change_pct': 0.0,
        'price_momentum': 'steady',
        'late_move_indicator': False,
        'market_confidence': 0.5,
        'relative_move': 0.0,
        'smart_money_score': 40.0,
        'market_sigma_modifier': 1.0,
        'is_insider_signal': False,
        'field_market_agreement': 0.5,
    }

    try:
        opening = _get_opening_odds(runner)
        current = _get_current_odds(runner)

        if opening is None and current is not None:
            opening = current
        if current is None and opening is not None:
            current = opening
        if opening is None or current is None:
            return defaults
        if opening <= 1.0 or current <= 1.0:
            return defaults

        pct_change = _price_change_pct(opening, current)
        momentum = _classify_momentum(pct_change)

        field_changes: List[float] = []
        for r in all_runners:
            o = _get_opening_odds(r)
            c = _get_current_odds(r)
            if o is not None and c is not None and o > 1.0 and c > 1.0:
                field_changes.append(_price_change_pct(o, c))

        field_avg = sum(field_changes) / len(field_changes) if field_changes else 0.0
        relative_move = pct_change - field_avg

        is_longshot = opening > 10.0

        field_confidence = compute_field_market_confidence(all_runners)
        smart_score = compute_smart_money_score(pct_change, relative_move, is_longshot, opening)
        sigma_mod = compute_market_sigma_modifier(pct_change, smart_score, field_confidence)

        steam_vel = max(0.0, pct_change * 0.5) if pct_change > 0 else 0.0
        drift_vel = max(0.0, abs(pct_change) * 0.5) if pct_change < 0 else 0.0

        is_insider = (momentum in ('strong_steam', 'steam') and is_longshot and pct_change >= 15)

        features: Dict = {
            'steam_velocity': round(steam_vel, 4),
            'drift_velocity': round(drift_vel, 4),
            'price_change_pct': round(pct_change, 2),
            'price_momentum': momentum,
            'late_move_indicator': abs(pct_change) > 15.0,
            'market_confidence': round(field_confidence, 4),
            'relative_move': round(relative_move, 2),
            'smart_money_score': smart_score,
            'market_sigma_modifier': sigma_mod,
            'is_insider_signal': is_insider,
            'field_market_agreement': round(field_confidence, 4),
        }

        odds_history = runner.get('odds_history')
        if isinstance(odds_history, list) and len(odds_history) >= 2:
            history_feats = _compute_history_features(odds_history)
            features.update(history_feats)

        return features
    except Exception:
        return defaults


if __name__ == '__main__':
    import json

    test_runners = [
        {'horse': 'Insider Pick', 'opening_odds': 21.0, 'win_odds': 12.0},
        {'horse': 'Favourite', 'opening_odds': 3.0, 'win_odds': 2.5},
        {'horse': 'Drifter', 'opening_odds': 6.0, 'win_odds': 10.0},
        {'horse': 'Steady Eddie', 'opening_odds': 8.0, 'win_odds': 7.5},
        {'horse': 'Big Drift', 'opening_odds': 4.0, 'win_odds': 8.0},
    ]

    print("=" * 60)
    print("Market Velocity Module — Test Output")
    print("=" * 60)

    for runner in test_runners:
        name = _get_horse_name(runner)
        features = compute_market_velocity_features(runner, test_runners)
        print(f"\n--- {name} ---")
        print(json.dumps(features, indent=2, default=str))

    print("\n--- Field Market Confidence ---")
    confidence = compute_field_market_confidence(test_runners)
    print(f"  Field confidence: {confidence}")

    print("\n--- Sigma Modifier Examples ---")
    for pct, sms, mc in [(30, 85, 0.9), (-20, 10, 0.4), (0, 40, 0.7)]:
        mod = compute_market_sigma_modifier(pct, sms, mc)
        print(f"  pct={pct:+d}%, smart={sms}, conf={mc} => sigma_mod={mod}")

    runner_with_history = {
        'horse': 'Time Series Horse',
        'opening_odds': 10.0,
        'win_odds': 6.0,
        'odds_history': [
            {'price': 10.0, 'timestamp': 1700000000},
            {'price': 9.0, 'timestamp': 1700001800},
            {'price': 8.0, 'timestamp': 1700003600},
            {'price': 7.0, 'timestamp': 1700005400},
            {'price': 6.0, 'timestamp': 1700007200},
        ],
    }

    print("\n--- Runner with Odds History ---")
    hist_features = compute_market_velocity_features(runner_with_history, test_runners + [runner_with_history])
    print(json.dumps(hist_features, indent=2, default=str))

    print("\n✓ All tests passed")
