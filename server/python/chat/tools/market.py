"""get_market_signals: overnight-to-morning price movement per runner.

The day's market_signals artifact (odds_movement.py) keyed '{track_key}_R{n}',
then the market_signal_scores mirror and the raw betfair_odds_snapshots.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..artifacts import ArtifactMissing, ArtifactUnavailable, market_signals_path
from ..db import DatabaseUnavailable
from ._common import (Context, ToolError, ToolSpec, cap_list, compact, failure, miss,
                      optional_race_number, optional_text, parse_iso_date, result,
                      split_race_key, track_matches)

SIGNAL_KEYS = ("signal_type", "baseline_price", "morning_price", "movement_pct", "market_signal_score")
MAX_HORSES = 16
MAX_RACES = 12


def _rank(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    notable = {"STEAM": 0, "STRONG_DRIFT": 0, "FIRMING": 1, "DRIFT": 1, "STABLE": 2, "UNKNOWN": 3}
    rows.sort(key=lambda r: (notable.get(str(r.get("signal_type")), 3),
                             -abs(float(r.get("movement_pct") or 0))))
    return rows


def _from_artifact(ctx: Context, date: str, track: str, race: Optional[int]):
    payload, source = ctx.artifacts.get_json(market_signals_path(date))
    if not isinstance(payload, dict) or not payload:
        return miss(f"The market signals file for {date} is empty.", source=source)
    races = []
    for key, horses in payload.items():
        tkey, rn = split_race_key(key)
        if not track_matches(tkey, track) or (race is not None and rn != race) or not isinstance(horses, dict):
            continue
        rows = []
        for name, d in horses.items():
            if isinstance(d, dict):
                row = {"horse": name}
                row.update(compact(d, SIGNAL_KEYS))
                rows.append(row)
        rows, more = cap_list(_rank(rows), MAX_HORSES)
        races.append({"race_number": rn, "runners": rows, "runners_truncated": more or None})
    if not races:
        where = f"{track} R{race}" if race is not None else track
        return miss(f"No market signals for {where} on {date}.", source=source)
    races.sort(key=lambda r: (r.get("race_number") or 0))
    shown, more = cap_list(races, MAX_RACES)
    return result({"date": date, "track": track, "races": shown}, source=source, truncated=more,
                  notes=["movement_pct is positive when the price shortened from the overnight "
                         "baseline to the morning check (money for the horse)."])


def _from_tables(ctx: Context, date: str, track: str, race: Optional[int]):
    q = ("SELECT track, race_number, horse_name, baseline_price, morning_price, "
         "price_movement_pct AS movement_pct, signal_type, market_signal_score "
         "FROM market_signal_scores WHERE race_date = %s::date "
         + ("AND race_number = %s " if race is not None else "")
         + "ORDER BY race_number, abs(price_movement_pct) DESC LIMIT 300")
    rows = ctx.db.query(q, (date, race) if race is not None else (date,))
    rows = [r for r in rows if track_matches(r.get("track"), track)]
    source = "neon:market_signal_scores"
    if not rows:
        q2 = ("SELECT track, race_number, horse_name, snapshot_type, back_price, lay_price, "
              "matched_volume, snapshot_time FROM betfair_odds_snapshots WHERE race_date = %s::date "
              + ("AND race_number = %s " if race is not None else "")
              + "ORDER BY race_number, horse_name, snapshot_time DESC LIMIT 400")
        snaps = ctx.db.query(q2, (date, race) if race is not None else (date,))
        snaps = [s for s in snaps if track_matches(s.get("track"), track)]
        if not snaps:
            where = f"{track} R{race}" if race is not None else track
            return miss(f"No market signals or odds snapshots for {where} on {date}.",
                        source=source + ",betfair_odds_snapshots")
        by_race: Dict[int, List[Dict[str, Any]]] = {}
        for s in snaps:
            by_race.setdefault(int(s.get("race_number") or 0), []).append(
                compact(s, ("horse_name", "snapshot_type", "back_price", "lay_price",
                            "matched_volume", "snapshot_time")))
        races = [{"race_number": rn, "snapshots": cap_list(v, 40)[0]} for rn, v in sorted(by_race.items())]
        return result({"date": date, "track": track, "races": cap_list(races, MAX_RACES)[0]},
                      source="neon:betfair_odds_snapshots",
                      notes=["Raw Betfair snapshots (BASELINE_NIGHT vs MORNING_CHECK); no signal "
                             "classification was recorded for this day."])
    by_race2: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        row = {"horse": r.get("horse_name")}
        row.update(compact(r, SIGNAL_KEYS))
        by_race2.setdefault(int(r.get("race_number") or 0), []).append(row)
    races = [{"race_number": rn, "runners": cap_list(_rank(v), MAX_HORSES)[0]}
             for rn, v in sorted(by_race2.items())]
    shown, more = cap_list(races, MAX_RACES)
    return result({"date": date, "track": track, "races": shown}, source=source, truncated=more)


def get_market_signals(ctx: Context, date: Any, track: Any, race: Any = None) -> Dict[str, Any]:
    date = parse_iso_date(date)
    track = optional_text(track, "track")
    race = optional_race_number(race)
    if not track:
        raise ToolError("track is required")
    try:
        return _from_artifact(ctx, date, track, race)
    except ArtifactMissing:
        pass
    except ArtifactUnavailable as e:
        return failure(str(e), source="s3")
    if ctx.db is None:
        return miss(f"No market signals file for {date} and no database is configured.", source="none")
    try:
        return _from_tables(ctx, date, track, race)
    except DatabaseUnavailable as e:
        return failure(f"No market signals file for {date}, and the database did not answer: {e}",
                       source="neon:market_signal_scores")


SPEC = ToolSpec(
    name="get_market_signals",
    description=(
        "Market movement at a meeting: each runner's overnight baseline price, morning price, "
        "percentage move and signal (STEAM, FIRMING, STABLE, DRIFT, STRONG_DRIFT), with steamers "
        "and drifters first. Use for 'what's been backed', 'any market moves', 'steam or drift'."),
    input_schema={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Race date, YYYY-MM-DD."},
            "track": {"type": "string", "description": "Track name."},
            "race": {"type": "integer", "description": "Race number (optional)."},
        },
        "required": ["date", "track"],
        "additionalProperties": False,
    },
    fn=get_market_signals,
)
