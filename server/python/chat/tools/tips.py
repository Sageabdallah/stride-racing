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
from ._common import (Context, ToolError, ToolSpec, cap_list, compact, failure, miss, number,
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

NEAREST_KEYS = ("race_date", "track", "race_number", "horse_name", "edge", "market_odds",
                "win_percentage", "confidence")

MAX_RACES = 60
MAX_TOP_PICKS = 5
MAX_FIELD = 24
MAX_RANGE_DAYS = 31
NEAREST_TOP = 3
MAX_NEAREST_TRACKS = 12
NEARBY_DAYS = 10
NEARBY_TOP = 4


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
        # A filter that matched nothing is not an empty day. Say where STRIDE
        # did tip and which picks led, so the conversation can carry on from
        # something real instead of stopping at the wrong track.
        picks = []
        for r in races:
            p = r.get("bet_pick") if isinstance(r.get("bet_pick"), dict) else r.get("coverage_pick")
            if isinstance(p, dict) and p.get("horse"):
                picks.append(dict(compact(p, ("horse", "odds", "edge_pct", "win_pct", "confidence")),
                                  track=r.get("track"), race_number=r.get("race_number")))
        picks.sort(key=lambda p: (number(p.get("edge_pct")) is None, -(number(p.get("edge_pct")) or 0)))
        notes = []
        if tracks:
            notes.append(f"Tracks with tips on {date}: {', '.join(tracks)}. The top picks by edge "
                         "elsewhere that day are in the data; they are not picks at the track asked for.")
        return miss(f"No tips for {what} on {date}.", source=source, notes=notes,
                    data={"tracks_with_tips": tracks, "top_picks_elsewhere": picks[:NEAREST_TOP]})
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
    span_rows = ctx.db.query(
        "SELECT race_date, track, race_number, race_name, horse_name, horse_number, barrier, "
        "jockey, trainer, win_percentage, model_probability, market_odds, expected_value, "
        "edge, confidence, value_rating, kelly_stake, convergence_gate, consensus_vote_pct, "
        "is_active FROM selections WHERE race_date >= %s AND race_date <= %s "
        "ORDER BY race_date, track, race_number, edge DESC NULLS LAST LIMIT 400",
        (date_from, date_to))
    rows = [r for r in span_rows if track_matches(r.get("track"), track)
            and (race is None or _int(r.get("race_number")) == race)]
    source = "neon:selections"
    if not rows:
        return _miss_with_context(ctx, date_from, date_to, track, race, source, span_rows)
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


def _miss_with_context(ctx, date_from, date_to, track, race, source, span_rows=()):
    """The honest miss for the selections table, with what did exist.

    Three different misses share this exit and the model must be able to tell
    them apart, because each leads somewhere different:

    * rows in the span that a track filter removed: a wrong track, not a quiet
      day. Name the tracks that did carry tips, list the leading selections
      elsewhere, and say when STRIDE last tipped at the track asked for;
    * rows at the track that a race filter removed: name the races with tips;
    * nothing in the span at all: say whether it falls before or after the
      records, and otherwise which nearby dates carry tips.

    Before this, "what did STRIDE tip at Randwick on the 12th" on a Sunday
    when Randwick had raced on the Saturday was a bare miss, and the two
    follow-ups in the eval (the top pick, and how it has gone since) had no
    horse to attach to. chat-eval run #1 (2026-09-13), follow-02.
    """
    span = date_from if date_from == date_to else f"{date_from} to {date_to}"
    what = (f" at {track}" if track else "") + (f" in race {race}" if race is not None else "")
    reason = f"Couldn't find any STRIDE tips{what} for {span}."
    notes: List[str] = []
    data: Dict[str, Any] = {}
    rows = list(span_rows or [])
    at_track = [r for r in rows if track_matches(r.get("track"), track)] if track else rows
    if rows and not at_track:
        tracks = _tracks_with_tips(rows)
        shown, more = cap_list(tracks, MAX_NEAREST_TRACKS)
        parts = [t["track"] + (f" ({t['selections']})" if date_from == date_to
                               else f" ({t['selections']} on {', '.join(t['dates'][:3])})") for t in shown]
        notes.append(f"STRIDE did not tip at {track} for {span}; its tips were at "
                     + "; ".join(parts) + (f"; and {len(tracks) - len(shown)} more" if more else "")
                     + f". The leading selections elsewhere are in the data and are not {track} "
                     "selections.")
        data = {"tracks_with_tips": shown, "top_selections_elsewhere": _leading(rows)}
    elif rows:
        races_with = sorted({n for n in (_int(r.get("race_number")) for r in at_track) if n is not None})
        notes.append(f"STRIDE's tips{' at ' + track if track else ''} for {span} were in race"
                     f"{'s' if len(races_with) != 1 else ''} "
                     f"{', '.join(str(n) for n in races_with)}; nothing for race {race}.")
        data = {"races_with_tips": races_with, "top_selections_elsewhere": _leading(at_track)}
        return miss(reason, source=source, notes=notes, data=data)
    else:
        first, last = _records_span(ctx)
        if first and date_to < first:
            notes.append(f"{span} is before STRIDE's earliest recorded tips ({first}).")
            return miss(reason, source=source, notes=notes)
        if last and date_from > last:
            notes.append(f"{span} is after STRIDE's latest recorded tips ({last}).")
            return miss(reason, source=source, notes=notes)
    nearby = _nearby_dates(ctx, date_from, date_to, track)
    if nearby:
        where = f"{track} tips" if track else "dates with tips"
        parts = [f"{d['race_date']} ({d['selections']}" + ("" if track else
                 f" at {', '.join(d['tracks'][:3])}" + (f" and {len(d['tracks']) - 3} more" if len(d['tracks']) > 3 else ""))
                 + ")" for d in nearby]
        notes.append(f"Nearest {where}: " + "; ".join(parts) + ".")
        data["nearby_dates"] = nearby
    elif track:
        notes.append(f"STRIDE has no tips at {track} within ten days either side of {span}.")
    return miss(reason, source=source, notes=notes, data=data or None)


def _tracks_with_tips(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_track: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        t = str(r.get("track") or "").strip()
        if not t:
            continue
        slot = by_track.setdefault(t, {"track": t, "selections": 0, "dates": set()})
        slot["selections"] += 1
        slot["dates"].add(str(r.get("race_date"))[:10])
    out = sorted(by_track.values(), key=lambda x: (-x["selections"], x["track"]))
    for t in out:
        t["dates"] = sorted(t["dates"])
    return out


def _leading(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The strongest selections by edge, as the nearest thing to what was asked."""
    ranked = sorted(rows, key=lambda r: (number(r.get("edge")) is None, -(number(r.get("edge")) or 0)))
    return [compact(r, NEAREST_KEYS) for r in ranked[:NEAREST_TOP]]


def _records_span(ctx) -> "tuple[str, str]":
    try:
        rows = ctx.db.query("SELECT MIN(race_date) AS first_date, MAX(race_date) AS last_date FROM selections", ())
    except DatabaseUnavailable:
        return "", ""
    head = (rows[0] or {}) if rows else {}
    return str(head.get("first_date") or "")[:10], str(head.get("last_date") or "")[:10]


def _nearby_dates(ctx, date_from: str, date_to: str, track: Optional[str]) -> List[Dict[str, Any]]:
    """Dates with tips within ten days either side of the span, at the track if one was asked for.

    Ten, not seven: an Easter or a wet week leaves a gap wider than a weekend,
    and the previous meeting is the useful pointer."""
    lo = (datetime.strptime(date_from, "%Y-%m-%d") - timedelta(days=NEARBY_DAYS)).strftime("%Y-%m-%d")
    hi = (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=NEARBY_DAYS)).strftime("%Y-%m-%d")
    try:
        grouped = ctx.db.query(
            "SELECT race_date, track, COUNT(*) AS n FROM selections WHERE race_date >= %s "
            "AND race_date <= %s GROUP BY race_date, track ORDER BY race_date, track LIMIT 300",
            (lo, hi))
    except DatabaseUnavailable:
        return []
    by_date: Dict[str, Dict[str, Any]] = {}
    for g in grouped:
        d = str(g.get("race_date") or "")[:10]
        if not d or (track and not track_matches(g.get("track"), track)):
            continue
        slot = by_date.setdefault(d, {"race_date": d, "tracks": [], "selections": 0})
        if g.get("track"):
            slot["tracks"].append(str(g["track"]))
        slot["selections"] += _int(g.get("n")) or 0

    def distance(d: str) -> int:
        if d < date_from:
            return (datetime.strptime(date_from, "%Y-%m-%d") - datetime.strptime(d, "%Y-%m-%d")).days
        if d > date_to:
            return (datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(date_to, "%Y-%m-%d")).days
        return 0

    nearest = sorted(by_date.values(), key=lambda x: (distance(x["race_date"]), x["race_date"]))[:NEARBY_TOP]
    for d in nearest:
        d["tracks"] = sorted(d["tracks"])
    return sorted(nearest, key=lambda x: x["race_date"])


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
