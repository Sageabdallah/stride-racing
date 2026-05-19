#!/usr/bin/env python3
"""
Market movement analysis: detects steam (odds shortening) and drift (lengthening)
to identify smart money signals.
"""

import sys
import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime

def calculate_steam_drift(opening_odds: float, current_odds: float) -> Tuple[float, str]:
    """
    Calculate percentage change in odds and categorise the movement.

    Positive = steamed (shortened) — smart money backing.
    Negative = drifted (lengthened) — money avoiding.
    """
    if opening_odds is None or current_odds is None:
        return 0.0, 'unknown'
    
    if opening_odds <= 1.0 or current_odds <= 1.0:
        return 0.0, 'invalid'
    
    pct_change = ((opening_odds - current_odds) / opening_odds) * 100
    
    if pct_change >= 25:
        category = 'strong_steam'
    elif pct_change >= 10:
        category = 'steam'
    elif pct_change <= -25:
        category = 'strong_drift'
    elif pct_change <= -10:
        category = 'drift'
    else:
        category = 'steady'
    
    return round(pct_change, 2), category


def detect_sharp_money(
    opening_odds: float, 
    current_odds: float, 
    field_avg_move: float = 0.0
) -> bool:
    """
    Detect sharp money signals.

    Sharp money: horse steams significantly (>15% shorter), against the overall market trend,
    or steams despite other runners drifting.
    """
    if opening_odds is None or current_odds is None:
        return False
    
    if opening_odds <= 1.0 or current_odds <= 1.0:
        return False
    
    pct_change = ((opening_odds - current_odds) / opening_odds) * 100
    
    if pct_change >= 15:
        return True
    
    if pct_change >= 10 and field_avg_move <= 0:
        return True
    
    return False


def analyze_field_market_movements(
    runners: List[Dict], 
    opening_key: str = 'opening_odds',
    current_key: str = 'win_odds'
) -> Dict:
    """
    Analyse market movements across the entire field.

    Returns summary with field_avg_move, steamers, drifters, and sharp money horses.
    """
    movements = []
    steamers = []
    drifters = []
    sharp_money = []
    
    for runner in runners:
        name = runner.get('horse') or runner.get('name') or runner.get('horse_name', 'Unknown')
        opening = runner.get(opening_key)
        current = runner.get(current_key) or runner.get('odds') or runner.get('win_odds')
        
        if opening is None:
            opening = current
        
        if opening is not None and current is not None:
            try:
                opening = float(opening)
                current = float(current)
                
                pct_change, category = calculate_steam_drift(opening, current)
                movements.append(pct_change)
                
                runner_data = {
                    'name': name,
                    'opening_odds': opening,
                    'current_odds': current,
                    'pct_change': pct_change,
                    'category': category
                }
                
                if category in ['steam', 'strong_steam']:
                    steamers.append(runner_data)
                elif category in ['drift', 'strong_drift']:
                    drifters.append(runner_data)
                    
            except (ValueError, TypeError):
                continue
    
    field_avg_move = sum(movements) / len(movements) if movements else 0
    
    for runner in runners:
        name = runner.get('horse') or runner.get('name') or runner.get('horse_name', 'Unknown')
        opening = runner.get(opening_key)
        current = runner.get(current_key) or runner.get('odds') or runner.get('win_odds')
        
        if opening is None:
            opening = current
            
        if opening is not None and current is not None:
            try:
                opening = float(opening)
                current = float(current)
                
                if detect_sharp_money(opening, current, field_avg_move):
                    pct_change, category = calculate_steam_drift(opening, current)
                    sharp_money.append({
                        'name': name,
                        'opening_odds': opening,
                        'current_odds': current,
                        'pct_change': pct_change,
                        'against_field': pct_change - field_avg_move
                    })
            except (ValueError, TypeError):
                continue
    
    steamers.sort(key=lambda x: x['pct_change'], reverse=True)
    drifters.sort(key=lambda x: x['pct_change'])
    sharp_money.sort(key=lambda x: x['against_field'], reverse=True)
    
    return {
        'field_avg_move': round(field_avg_move, 2),
        'steamers': steamers[:5],
        'drifters': drifters[:5],
        'sharp_money': sharp_money[:3],
        'total_runners_analyzed': len(movements)
    }


def get_market_adjustment(
    opening_odds: Optional[float],
    current_odds: Optional[float],
    field_avg_move: float = 0.0
) -> float:
    """
    Get a probability adjustment based on market movement.

    Steam = positive adjustment (market confidence).
    Drift = negative adjustment (market concern).
    Sharp money = strong positive adjustment.
    """
    if opening_odds is None or current_odds is None:
        return 1.0
    
    try:
        opening = float(opening_odds)
        current = float(current_odds)
    except (ValueError, TypeError):
        return 1.0
    
    pct_change, category = calculate_steam_drift(opening, current)
    is_sharp = detect_sharp_money(opening, current, field_avg_move)
    
    adjustment = 1.0
    
    if category == 'strong_steam':
        adjustment = 1.15
    elif category == 'steam':
        adjustment = 1.08
    elif category == 'strong_drift':
        adjustment = 0.85
    elif category == 'drift':
        adjustment = 0.92
    
    if is_sharp:
        adjustment = min(adjustment * 1.1, 1.25)
    
    return round(adjustment, 3)


def extract_market_features(runner: Dict, all_runners: List[Dict]) -> Dict:
    """Extract all market-related features for ML training."""
    name = runner.get('horse') or runner.get('name') or runner.get('horse_name', 'Unknown')
    opening = runner.get('opening_odds')
    current = runner.get('win_odds') or runner.get('odds') or runner.get('market_odds')
    
    if opening is None:
        opening = current
    
    field_analysis = analyze_field_market_movements(all_runners)
    field_avg_move = field_analysis.get('field_avg_move', 0)
    
    pct_change = 0.0
    category = 'unknown'
    is_sharp = False
    adjustment = 1.0
    
    if opening is not None and current is not None:
        try:
            opening = float(opening)
            current = float(current)
            pct_change, category = calculate_steam_drift(opening, current)
            is_sharp = detect_sharp_money(opening, current, field_avg_move)
            adjustment = get_market_adjustment(opening, current, field_avg_move)
        except (ValueError, TypeError):
            pass
    
    return {
        'opening_odds': opening,
        'current_odds': current,
        'steam_drift_pct': pct_change,
        'market_move_category': category,
        'is_sharp_money': is_sharp,
        'field_avg_move': field_avg_move,
        'market_adjustment': adjustment,
        'is_favorite_steamer': pct_change > 10 and (current or 99) < 5,
        'is_longshot_steamer': pct_change > 15 and (current or 1) > 10
    }


if __name__ == '__main__':
    test_runners = [
        {'horse': 'Horse A', 'opening_odds': 5.0, 'win_odds': 3.5},
        {'horse': 'Horse B', 'opening_odds': 8.0, 'win_odds': 12.0},
        {'horse': 'Horse C', 'opening_odds': 3.0, 'win_odds': 2.5},
        {'horse': 'Horse D', 'opening_odds': 15.0, 'win_odds': 8.0},
    ]
    
    result = analyze_field_market_movements(test_runners)
    print(json.dumps(result, indent=2))
