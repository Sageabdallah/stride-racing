#!/usr/bin/env python3
"""Task 11 (buildable slice): place and each-way shadow settlement.

Settles shadow place bets from finishing positions, which the DB has, at a
MODELLED place price. Settlement at actual place dividends is blocked: no
dividend data exists anywhere in the stack (race_results_history carries
position only, PF payloads carry no dividend keys, no collector parses
them). The 0.25 rule below is the explicitly-marked placeholder the task
forbids ever going live on; it exists so the shadow ledger can accrue while
a real dividend source is decided.

Everything here is shadow-only. STRIDE_MARKET_MIX stays off; going live
requires the pre-registered go/no-go in the task 09 registry (shadow net
ROI CI above zero over at least 200 shadow bets with positive CLV).
"""

from __future__ import annotations

from typing import Optional


def place_terms(field_size: int) -> int:
    """AU convention: 1-4 runners win-only, 5-7 two places, 8+ three."""
    if field_size <= 4:
        return 0
    if field_size <= 7:
        return 2
    return 3


def modelled_place_price(win_price: float, field_size: int) -> Optional[float]:
    """PLACEHOLDER quarter-odds rule: place = 1 + (win - 1) * 0.25.

    Never to be used live (task 11 guardrail). Replaced by a fitted model
    the day a dividend source exists to fit it on.
    """
    terms = place_terms(field_size)
    if terms == 0 or not win_price or win_price <= 1:
        return None
    return round(1.0 + (win_price - 1.0) * 0.25, 2)


def settle_place(position: Optional[int], field_size: int,
                 place_price: float, stake: float = 1.0,
                 commission_rate: float = 0.08,
                 dead_heat_fraction: float = 1.0) -> Optional[float]:
    """Net P&L of a shadow place bet.

    dead_heat_fraction: the share of the win portion paid on a dead heat
    for the last paid place (0.5 for a two-way dead heat, and so on).
    Returns None for no-place-market fields (1-4 runners).
    """
    terms = place_terms(field_size)
    if terms == 0:
        return None
    if position is None or position > terms:
        return -stake
    gross_profit = stake * (place_price - 1.0) * dead_heat_fraction
    if gross_profit <= 0:
        return -stake * (1.0 - dead_heat_fraction)
    return gross_profit * (1.0 - commission_rate) \
        - stake * (1.0 - dead_heat_fraction)


def settle_each_way(position: Optional[int], field_size: int,
                    win_price: float, place_price: float,
                    stake: float = 1.0, commission_rate: float = 0.08,
                    win_split: float = 0.5,
                    dead_heat_fraction: float = 1.0) -> Optional[float]:
    """Net P&L of a shadow each-way bet, stake split win/place.

    The split stays at 50/50 outside a pre-registered experiment (task 11
    guardrail).
    """
    terms = place_terms(field_size)
    if terms == 0:
        return None
    win_stake = stake * win_split
    place_stake = stake - win_stake
    win_pnl = (win_stake * (win_price - 1.0) * dead_heat_fraction
               * (1.0 - commission_rate)
               if position == 1 else -win_stake)
    place_pnl = settle_place(position, field_size, place_price, place_stake,
                             commission_rate, dead_heat_fraction)
    if place_pnl is None:
        return None
    return win_pnl + place_pnl
