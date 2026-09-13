"""get_stride_tips: what STRIDE published for a day.

Reads the day's tips artifact first, because it carries the whole decision
contract (bet_pick, coverage_pick, bet_status, convergence tier, the field),
and the `selections` table second, which holds only the published rows. A
date range is answered from the table in one query, so "the first week of
April" is one call, not seven.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..artifacts import ArtifactMissing, ArtifactUnavailable, tips_path
from ..db import DatabaseUnavailable
from ._common import (Context, ToolError, ToolSpec, cap_list, compact, failure, miss,
                      optional_race_number, optional_text, parse_iso_date,
                      result, track_matches, truncate_text)

PICK_KEYS = (
    "rank", "horse", "barrier", "jockey", "trainer", "odds", "has_real_market_odds",
    "fair_odds", "win_pct", "raw_model_pct", "edge_pct", "confidence",
    "selection_score", "staking", "value_rating", "convergence_tier",
    "convergence_score", "crowd_classification", "consensus_score",
    "consensus_mentions", "market_signal_score", "market_alignment",
    "selection_origin", "selection_origin_reason", "should_bet",
    "luckless_flag", "franking_class",
)
FIELD_KEYS = (
    "horse", "saddle_number", "barrier", "jockey", "trainer", "odds", "fair_odds",
    "win_pct", "place_pct", "edge_pct", "selection_score", "form", "running_style",
    "days_since_run", "is_first_up", "is_tipped", "tip_rank", "confidence",
)
RACE_KEYS = ("track", "race_number", "race_name", "distance", "going", "race_class",
             "field_size", "bet_status", "bet_status_reason")
SELECTION_KEYS = ("race_date", "track", "race_number", "race_name", "horse_name",
                  "horse_number", "barrier", "jockey", "trainer", "win_percentage",
                  "model_probability", "market_odds", "expected_value", "edge",
                  "confidence", "value_rating", "kelly_stake", "convergence_gate",
                  "consensus_vote_pct", "is_active")

MAX_RACES = 60
MAX_TOP_PICKS = 5
MAX_FIELD = 24
MAX_RANGE_DAYS = 31


def _pick(p: Any, with_insight: bool = False) -> Dict[str, Any]:
    out = compact(p, PICK_KEYS)
    if isinstance(p, dict):
        kf = p.get("key_factors")
        if isinstance(kf, list) and kf:
            out["key_factors"] = kf[:3]
        if with_insight and p.get("ai_insight"):
            out["ai_insight"] = truncate_text(p["ai_insight"], 600)
    return out


def _race(race: Dict[str, Any], detail: str) -> Dict[str, Any]:
    """detail: 'day' (one line per race), 'track' (picks), 'race' (the field)."""
    out = compact(race, RACE_KEYS)
    bet = race.get("bet_pick")
    cov = race.get("coverage_pick")
    if detail == "day":
        if isinstance(bet, dict):
            out["bet_pick"] = compact(bet, ("horse", "odds", "edge_pct", "win_pct",
                                            "confidence", "convergence_tier", "staking"))
        elif isinstance(cov, dict):
            out["coverage_pick"] = compact(cov, ("horse", "odds", "edge_pct", "confidence"))
        return out
    if isinstance(bet, dict):
        out["bet_pick"] = _pick(bet, with_insight=(detail == "race"))
    if isinstance(cov, dict) and (not isinstance(bet, dict) or cov.get("horse") != bet.get("horse")):
        out["coverage_pick"] = _pick(cov, with_insight=(detail == "race"))
    tops, more = cap_list(race.get("top_picks") or [], MAX_TOP_PICKS)
    if tops:
        out["top_picks"] = [_pick(p) for p in tops]
        if more:
            out["top_picks_truncated"] = True
    if detail == "race":
        field, more = cap_list(race.get("full_field") or [], MAX_FIELD)
        if field:
            out["full_field"] = [compact(f, FIELD_KEYS) for f in field]
            if more:
                out["full_field_truncated"] = True
    return out


def _from_artifact(ctx: Context, date: str, track: Optional[str], race: Optional[int],
                   include_field: bool):
    payload, source = ctx.artifacts.get_json(tips_path(date))
    races = payload.get("races") if isinstance(payload, dict) else None
    races = [r for r in (races or []) if isinstance(r, dict)]
    matched = [r for r in races if track_matches(r.get("track"), track)
               and (race is None or _int(r.get("race_number")) == race)]
    if not matched:
        tracks = sorted({str(r.get("track")) for r in races if r.get("track")})
        if not races:
            return miss(f"The tips file for {date} exists but holds no races.", source=source)
        what = f"{track} R{race}" if track and race else (track or f"race {race}")
        return miss(f"No tips for {what} on {date}.", source=source,
                    notes=[f"Tracks with tips on {date}: {', '.join(tracks)}"] if tracks else None)
    detail = "race" if (race is not None or include_field) else ("track" if track else "day")
    shown, more = cap_list(matched, MAX_RACES)
    data: Dict[str, Any] = {"date": date, "races": [_race(r, detail) for r in shown]}
    if isinstance(payload, dict):
        summary = payload.get("summary")
        if isinstance(summary, dict):
            data["summary"] = compact(summary, ("total_races", "total_selections",
                                                "positive_edge", "high_confidence", "total_units"))
        conv = payload.get("convergence_summary")
        if isinstance(conv, dict) and detail == "day":
            data["convergence_summary"] = compact(conv, ("confirmed", "crowd_only", "model_only",
                                                         "rejected", "gated_no_bet"))
        contract = payload.get("selection_contract")
        if isinstance(contract, dict):
            data["selection_contract"] = compact(contract, ("version", "bet_races", "no_bet_races"))
    notes = []
    if detail == "day":
        notes.append("Day view: one line per race. Ask with a track for the picks, "
                     "or a track and race number for the full field.")
    return result(data, source=source, truncated=more, notes=notes)


def _from_selections(ctx: Context, date_from: str, date_to: str, track: Optional[str],
                     race: Optional[int]):
    if ctx.db is None:
        return miss(f"No tips artifact for {date_from} and no database is configured.",
                    source="none")
    rows = ctx.db.query(
        "SELECT race_date, track, race_number, race_name, horse_name, horse_number, barrier, "
        "jockey, trainer, win_percentage, model_probability, market_odds, expected_value, "
        "edge, confidence, value_rating, kelly_stake, convergence_gate, consensus_vote_pct, "
        "is_active FROM selections WHERE race_date >= %s AND race_date <= %s "
        "ORDER BY race_date, track, race_number, edge DESC NULLS LAST LIMIT 400",
        (date_from, date_to))
    rows = [r for r in rows if track_matches(r.get("track"), track)
            and (race is None or _int(r.get("race_number")) == race)]
    source = "neon:selections"
    if not rows:
        return _miss_with_floor(ctx, date_from, date_to, track, race, source)
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_date.setdefault(str(r.get("race_date")), []).append(compact(r, SELECTION_KEYS))
    shown, more = cap_list(sorted(by_date.items()), 14)
    data = {"date_from": date_from, "date_to": date_to,
            "dates_with_tips": [d for d, _ in shown],
            "selections_by_date": {d: v for d, v in shown}}
    return result(data, source=source, truncated=more,
                  notes=["Published selection rows only; the per-race decision contract "
                         "(bet/coverage/NO_BET) lives in the day's tips artifact."])


def _miss_with_floor(ctx, date_from, date_to, track, race, source):
    span = date_from if date_from == date_to else f"{date_from} to {date_to}"
    what = f" at {track}" if track else ""
    notes = []
    try:
        floor = ctx.db.query("SELECT MIN(race_date) AS first_date FROM selections", ())
        first = str((floor[0] or {}).get("first_date") or "") if floor else ""
        if first and date_to < first[:10]:
            notes.append(f"{span} is before STRIDE's earliest recorded tips ({first[:10]}).")
    except DatabaseUnavailable:
        pass
    return miss(f"Couldn't find any STRIDE tips{what} for {span}.", source=source, notes=notes)


def _int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def get_stride_tips(ctx: Context, date: Any, track: Any = None, race: Any = None,
                    date_to: Any = None, include_field: Any = False) -> Dict[str, Any]:
    date = parse_iso_date(date)
    track = optional_text(track, "track")
    race = optional_race_number(race)
    if date_to is not None and date_to != "":
        date_to = parse_iso_date(date_to, "date_to")
        if date_to < date:
            raise ToolError(f"date_to ({date_to}) is before date ({date})")
        d0 = datetime.strptime(date, "%Y-%m-%d")
        if datetime.strptime(date_to, "%Y-%m-%d") - d0 > timedelta(days=MAX_RANGE_DAYS):
            date_to = (d0 + timedelta(days=MAX_RANGE_DAYS)).strftime("%Y-%m-%d")
        try:
            return _from_selections(ctx, date, date_to, track, race)
        except DatabaseUnavailable as e:
            return failure(f"The database did not answer: {e}", source="neon:selections")
    try:
        return _from_artifact(ctx, date, track, race, bool(include_field))
    except ArtifactMissing:
        pass
    except ArtifactUnavailable as e:
        return failure(str(e), source="s3")
    try:
        return _from_selections(ctx, date, date, track, race)
    except DatabaseUnavailable as e:
        return failure(f"No tips artifact for {date}, and the database did not answer: {e}",
                       source="neon:selections")


SPEC = ToolSpec(
    name="get_stride_tips",
    description=(
        "STRIDE's own published tips and selections for a race day: the bet pick, coverage "
        "pick, NO_BET decisions with reasons, edges, model win percentages, confidence, "
        "convergence tier and staking. Use for any question about what STRIDE tipped or "
        "selected. Pass a track for the picks at one meeting, and a race number for that "
        "race's full field. Pass date_to for a range of days (max 31)."),
    input_schema={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Race date, YYYY-MM-DD."},
            "track": {"type": "string", "description": "Track name, e.g. Randwick, Flemington."},
            "race": {"type": "integer", "description": "Race number at that track."},
            "date_to": {"type": "string", "description": "End of a date range, YYYY-MM-DD."},
            "include_field": {"type": "boolean",
                              "description": "Include the full scored field per race."},
        },
        "required": ["date"],
        "additionalProperties": False,
    },
    fn=get_stride_tips,
)
