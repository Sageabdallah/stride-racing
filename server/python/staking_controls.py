#!/usr/bin/env python3
"""Task 06: drawdown circuit-breaker and daily exposure caps.

Pure decision functions plus thin pipeline appliers. Both appliers demote
picks in place (should_bet False, refusal_reason set) so every demotion
flows into the ledger as a refused row and stays measurable. Neither ever
raises into the pipeline; risk controls that crash the pipeline would be a
worse risk than the ones they manage. The breaker only ever reduces stakes;
there is no path that increases them (no martingale logic).
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

STARTING_BANKROLL_UNITS = 100.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def breaker_enabled() -> bool:
    return os.environ.get("STRIDE_DRAWDOWN_BREAKER", "true").strip().lower() \
        in ("true", "1", "yes")


def realised_drawdown_pct(settled_pnls: Sequence[float],
                          starting_bankroll: float = STARTING_BANKROLL_UNITS
                          ) -> float:
    """Peak-to-current drawdown, percent of the rolling bankroll peak.

    Bankroll is starting units plus cumulative settled net P&L in order.
    Returns a non-negative percentage (0.0 = at or above the peak).
    """
    bankroll = starting_bankroll
    peak = starting_bankroll
    for pnl in settled_pnls:
        bankroll += float(pnl or 0.0)
        peak = max(peak, bankroll)
    if peak <= 0:
        return 100.0
    return max(0.0, (peak - bankroll) / peak * 100.0)


def breaker_state(drawdown_pct: float,
                  halve_at: Optional[float] = None,
                  suspend_at: Optional[float] = None) -> str:
    """'normal' | 'halved' | 'suspended'. Thresholds env-configurable."""
    halve_at = _env_float("STRIDE_DRAWDOWN_HALVE_PCT", 15.0) \
        if halve_at is None else halve_at
    suspend_at = _env_float("STRIDE_DRAWDOWN_SUSPEND_PCT", 25.0) \
        if suspend_at is None else suspend_at
    if drawdown_pct >= suspend_at:
        return "suspended"
    if drawdown_pct >= halve_at:
        return "halved"
    return "normal"


def halve_stake(staking: Any) -> str:
    """'2u' -> '1u', '1u' -> '0.5u'. Anything unparsable stays as-is."""
    try:
        units = float(str(staking or "0u").lower().rstrip("u"))
    except ValueError:
        return str(staking)
    return f"{units / 2:g}u"


def _load_settled_pnls() -> Optional[List[float]]:
    """Settled net P&L per bet, oldest first, from the ledger. None when the
    DB is unreachable (caller treats that as breaker 'normal', loudly)."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(url)
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(settled_pnl, pnl, 0) FROM selection_ledger "
            "WHERE settled AND NOT refused ORDER BY race_date, updated_at")
        pnls = [float(r[0] or 0.0) for r in cur.fetchall()]
        conn.close()
        return pnls
    except Exception as e:
        print(f"  [RISK] drawdown read failed (breaker stays normal): {e}",
              file=sys.stderr)
        return None


def apply_drawdown_breaker(race_tips: List[Dict[str, Any]],
                           state: Optional[str] = None) -> str:
    """Apply the breaker to the day's bet picks in place. Returns the state."""
    if not breaker_enabled():
        return "disabled"
    if state is None:
        pnls = _load_settled_pnls()
        state = "normal" if pnls is None else breaker_state(
            realised_drawdown_pct(pnls))
    if state == "normal":
        return state
    for race in race_tips:
        pick = race.get("bet_pick")
        if not isinstance(pick, dict) or not pick.get("should_bet"):
            continue
        if state == "suspended":
            pick["should_bet"] = False
            pick["refusal_reason"] = "drawdown_breaker"
            pick["selection_origin"] = "drawdown_breaker"
            pick["selection_origin_reason"] = (
                "drawdown_breaker: realised drawdown past the suspend "
                "threshold; betting suspended")
            print(f"  [RISK] {pick.get('horse', '?')}: suspended by "
                  "drawdown breaker", file=sys.stderr)
        elif state == "halved":
            old = pick.get("staking")
            pick["staking"] = halve_stake(old)
            print(f"  [RISK] {pick.get('horse', '?')}: stake {old} -> "
                  f"{pick['staking']} (drawdown breaker)", file=sys.stderr)
    return state


def _net_ev(pick: Dict[str, Any]) -> float:
    ev = pick.get("expected_value")
    if ev is None:
        ev = pick.get("edge_pct", 0)
    try:
        return float(ev or 0.0)
    except (TypeError, ValueError):
        return 0.0


def enforce_exposure_caps(race_tips: List[Dict[str, Any]],
                          max_per_day: Optional[int] = None,
                          max_per_track: Optional[int] = None
                          ) -> Tuple[int, int]:
    """Cap the day at N bets and M per track, keeping the highest net EV.

    Returns (kept, demoted). Demoted picks become refused rows tagged
    exposure_cap so the cap's cost is measurable.
    """
    max_per_day = _env_int("STRIDE_MAX_BETS_PER_DAY", 6) \
        if max_per_day is None else max_per_day
    max_per_track = _env_int("STRIDE_MAX_BETS_PER_TRACK", 2) \
        if max_per_track is None else max_per_track

    bets = []
    for race in race_tips:
        pick = race.get("bet_pick")
        if isinstance(pick, dict) and pick.get("should_bet"):
            bets.append((race, pick))
    if not bets:
        return 0, 0

    bets.sort(key=lambda rp: _net_ev(rp[1]), reverse=True)
    kept = 0
    per_track: Dict[str, int] = {}
    demoted = 0
    for race, pick in bets:
        track = str(race.get("track") or "?")
        if kept < max_per_day and per_track.get(track, 0) < max_per_track:
            kept += 1
            per_track[track] = per_track.get(track, 0) + 1
            continue
        pick["should_bet"] = False
        pick["refusal_reason"] = "exposure_cap"
        pick["selection_origin"] = "exposure_cap"
        pick["selection_origin_reason"] = (
            f"exposure_cap: day cap {max_per_day} / track cap "
            f"{max_per_track}, outranked on net EV")
        demoted += 1
        print(f"  [RISK] {pick.get('horse', '?')} ({track}): demoted by "
              "exposure cap", file=sys.stderr)
    return kept, demoted
