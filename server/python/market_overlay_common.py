#!/usr/bin/env python3
from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def overlay_going_bucket(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "heavy" in text:
        return "heavy"
    if "soft" in text:
        return "soft"
    if "firm" in text:
        return "firm"
    if "synthetic" in text or "poly" in text:
        return "synthetic"
    return "good"


def overlay_price_bracket(odds: Any) -> str | None:
    try:
        price = float(odds)
    except (TypeError, ValueError):
        return None
    if price <= 1.0:
        return None
    if price <= 3.0:
        return "short"
    if price <= 8.0:
        return "mid"
    if price <= 20.0:
        return "long"
    return "outsider"


def build_market_overlay_map_from_sp(
    conn,
    tracks: list[str] | tuple[str, ...],
    target_date: str,
    *,
    lookback_days: int = 450,
    min_sample: int = 10,
) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
    track_list = sorted({str(track).strip() for track in tracks if str(track or "").strip()})
    if not track_list:
        return {}

    target_dt = date.fromisoformat(str(target_date)[:10])
    history_start = (target_dt - timedelta(days=lookback_days)).isoformat()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT track,
                   CASE WHEN LOWER(going) LIKE '%%heavy%%' THEN 'heavy'
                        WHEN LOWER(going) LIKE '%%soft%%' THEN 'soft'
                        WHEN LOWER(going) LIKE '%%firm%%' THEN 'firm'
                        WHEN LOWER(going) LIKE '%%synthetic%%' OR LOWER(going) LIKE '%%poly%%' THEN 'synthetic'
                        ELSE 'good' END as going_cat,
                   CASE WHEN sp_odds BETWEEN 1.01 AND 3.0 THEN 'short'
                        WHEN sp_odds BETWEEN 3.01 AND 8.0 THEN 'mid'
                        WHEN sp_odds BETWEEN 8.01 AND 20.0 THEN 'long'
                        ELSE 'outsider' END as bracket,
                   COUNT(*) as runners,
                   COUNT(CASE WHEN position = 1 THEN 1 END) as wins,
                   AVG(1.0 / NULLIF(sp_odds, 0)) as avg_implied_prob
            FROM race_results_history
            WHERE track = ANY(%s)
              AND race_date::date < %s::date
              AND race_date::date >= %s::date
              AND sp_odds IS NOT NULL
              AND sp_odds > 1.0
              AND position IS NOT NULL
            GROUP BY 1, 2, 3
            HAVING COUNT(*) >= %s
            ORDER BY 1, 2, 3
            """,
            (track_list, target_date, history_start, int(min_sample)),
        )
        rows = cur.fetchall()

    result: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for track, going_cat, bracket, runners, wins, avg_implied in rows:
        result.setdefault(track, {})
        result[track].setdefault(going_cat, {})

        actual_win_rate = (wins / runners) if runners else 0.0
        avg_implied_f = float(avg_implied) if avg_implied else 0.0
        market_edge = actual_win_rate - avg_implied_f

        result[track][going_cat][bracket] = {
            "actual_win_rate": round(actual_win_rate * 100, 2),
            "avg_implied_prob": round(avg_implied_f * 100, 2),
            "avg_model_prob": None,
            "market_edge": round(market_edge * 100, 2),
            "sample": int(runners),
            "overlay": market_edge > 0,
        }

    return result
