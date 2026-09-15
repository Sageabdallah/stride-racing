"""get_stride_tips: what STRIDE published for a day.

Reads the day's tips artifact first, because it carries the whole decision
contract (bet_pick, coverage_pick, bet_status, convergence tier, the field),
and the `selections` table second, which holds only the published rows. A
date range is answered from the table in two queries, so "the first week of
April" is one call, not seven: a grouped calendar of every date and track
with selections, which is complete whatever the volume, and the rows
themselves, active only and capped per track per date, so a busy month does
not come back as its first two days (chat-eval run #1, chain-04: a month
came back as "6 and 7 March"). The pipeline keeps superseded runs in the
table with is_active = false; only the active rows are STRIDE's published
set for the day, and only they are counted.
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
RANGE_KEYS = ("track", "race_number", "horse_name", "edge", "market_odds", "win_percentage",
              "confidence", "value_rating")
ACTIVE = "COALESCE(is_active, true)"  # rows from before the flag existed count as live

MAX_RACES = 60
MAX_TOP_PICKS = 5
MAX_FIELD = 24
MAX_RANGE_DAYS = 31
NEAREST_TOP = 3
MAX_NEAREST_TRACKS = 12
NEARBY_DAYS = 10
NEARBY_TOP = 4
ROW_FETCH_CAP = 1500
MAX_RANGE_DATES = 14
# detail -> (rows per track per date, rows in total, keys). The totals keep a
# worst-case payload (14 dates, six tracks, long names) near 18k characters,
# clear of the 24k backstop in frame_for_model, which cuts JSON mid-way.
ROW_CAPS = {"range": (2, 70, RANGE_KEYS), "day": (3, 40, RANGE_KEYS),
            "track": (12, 30, SELECTION_KEYS), "race": (24, 48, SELECTION_KEYS)}


def _stages(p: Dict[str, Any]) -> Dict[str, Any]:
    """The pick's own calibration ladder, in the order it was computed.

    decision_contract.py writes `prediction_stages` onto every pick through
    prediction_stages.put_stage: base_xgb, base_lightgbm and base_catboost,
    their ensemble, the Monte Carlo raw and recalibrated figures, the
    sectional blend, the ML and combined adjustments, the wrapper's
    pre-calibration and blend, the market anchor, the selection score and the
    final decision. That is what "why did the model favour this one" is asking
    for, and until now `compact(p, PICK_KEYS)` dropped it on the floor because
    the key was not in the allowlist -- so the chat could offer the decision's
    label and a prose insight, but never the numbers behind either.

    Shaped, not forwarded: each stage becomes one `name: value` entry, with
    the owner kept only where it is not obvious, and anything the writer
    recorded as None omitted. STAGE_DEFINITIONS is imported rather than
    restated so a stage added upstream appears here without an edit.
    """
    raw = p.get("prediction_stages")
    if not isinstance(raw, dict) or not raw:
        return {}
    from prediction_stages import STAGE_DEFINITIONS  # flat module, stdlib-only
    order = list(STAGE_DEFINITIONS)
    names = sorted(raw, key=lambda n: (order.index(n) if n in order else len(order), n))
    out: Dict[str, Any] = {}
    for name in names:
        rec = raw.get(name)
        if not isinstance(rec, dict):
            continue
        value = rec.get("value")
        if value is None:
            continue
        entry: Dict[str, Any] = {"value": value}
        if rec.get("quantity_type") and rec["quantity_type"] != "probability":
            entry["quantity_type"] = rec["quantity_type"]
        if rec.get("note"):
            entry["note"] = truncate_text(rec["note"], 160)
        out[name] = entry
    return out


def _pick(p: Any, with_insight: bool = False) -> Dict[str, Any]:
    out = compact(p, PICK_KEYS)
    if isinstance(p, dict):
        kf = p.get("key_factors")
        if isinstance(kf, list) and kf:
            out["key_factors"] = kf[:3]
        if with_insight and p.get("ai_insight"):
            out["ai_insight"] = truncate_text(p["ai_insight"], 600)
        # Only at detail='race'. The ladder is a dozen entries per runner, so
        # a whole day of it would spend the payload cap on numbers nobody
        # asked for; a question about one runner is where it belongs.
        if with_insight:
            stages = _stages(p)
            if stages:
                out["prediction_stages"] = stages
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
        # When the card was built. run_tips_pipeline.py:3687 stamps every tips
        # file with it and the chat used to drop it, which left "how old is
        # this?" unanswerable and left a re-run card indistinguishable from
        # the one the page is showing. It is the only freshness fact the chat
        # has: there is no pipeline-run status anywhere in this repository, so
        # an absent file still cannot tell "not published yet" from "nothing
        # tipped". This says how old the answer is, not that it is current.
        if payload.get("generated_at"):
            data["generated_at"] = payload["generated_at"]
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
    calendar = [c for c in _calendar(ctx, date_from, date_to)
                if date_from <= c["race_date"] <= date_to]
    span_rows = ctx.db.query(
        "SELECT race_date, track, race_number, race_name, horse_name, horse_number, barrier, "
        "jockey, trainer, win_percentage, model_probability, market_odds, expected_value, "
        "edge, confidence, value_rating, kelly_stake, convergence_gate, consensus_vote_pct, "
        "is_active FROM selections WHERE race_date >= %s AND race_date <= %s AND " + ACTIVE + " "
        "ORDER BY race_date, track, edge DESC NULLS LAST, race_number LIMIT %s",
        (date_from, date_to, ROW_FETCH_CAP))
    rows = [r for r in span_rows if track_matches(r.get("track"), track)
            and (race is None or _int(r.get("race_number")) == race)]
    source = "neon:selections"
    if not rows:
        return _miss_with_context(ctx, date_from, date_to, track, race, source, span_rows, calendar)

    detail = ("race" if race is not None else "track" if track else
              "day" if date_from == date_to else "range")
    per_group, total_cap, keys = ROW_CAPS[detail]
    grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for r in rows:
        d = str(r.get("race_date"))[:10]
        grouped.setdefault(d, {}).setdefault(str(r.get("track") or ""), []).append(r)
    # The calendar is the complete list of dates; the rows are a capped sample
    # of each. A date the calendar has and the rows do not is reported, never
    # silently dropped, which is the defect this replaces.
    cal_dates = sorted({c["race_date"] for c in calendar
                        if track_matches(c["track"], track)}) if race is None else sorted(grouped)
    dates = cal_dates or sorted(grouped)
    shown_dates, more_dates = cap_list(dates, MAX_RANGE_DATES)
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    dropped = 0
    budget = total_cap
    for d in shown_dates:
        out: List[Dict[str, Any]] = []
        for t, trows in sorted(grouped.get(d, {}).items()):
            ranked = sorted(trows, key=lambda r: (number(r.get("edge")) is None,
                                                  -(number(r.get("edge")) or 0),
                                                  _int(r.get("race_number")) or 0))
            take = ranked[:min(per_group, max(budget, 0))]
            dropped += len(trows) - len(take)
            budget -= len(take)
            out.extend(compact(r, keys) for r in take)
        by_date[d] = out
    calendar_out = []
    for d in shown_dates:
        tracks = [{"track": c["track"], "selections": c["n"]} for c in calendar
                  if c["race_date"] == d and track_matches(c["track"], track)]
        if not tracks:  # no calendar entry (fake backends): count the rows
            tracks = [{"track": t, "selections": len(trows)} for t, trows in sorted(grouped.get(d, {}).items())]
        calendar_out.append({"race_date": d, "selections": sum(t["selections"] for t in tracks),
                             "tracks": tracks})
    missing_rows = [d for d in shown_dates if not by_date.get(d)]
    truncated = more_dates or dropped > 0 or len(span_rows) >= ROW_FETCH_CAP
    data = {"date_from": date_from, "date_to": date_to, "dates_with_tips": shown_dates,
            "calendar": calendar_out, "selections_by_date": by_date}
    notes = ["Published selection rows only, the active set for each day; the per-race "
             "decision contract (bet/coverage/NO_BET) lives in the day's tips artifact.",
             "calendar lists every date and track with selections in the span and how many; "
             "dates_with_tips is complete."]
    if detail in ("range", "day"):
        notes.append(f"selections_by_date shows the leading {per_group} by edge per track per date. "
                     "Ask with a track for all of a meeting's selections, or a track and race "
                     "for one race.")
    elif dropped:
        notes.append(f"selections_by_date is capped at {per_group} rows per date; the calendar "
                     "has the full counts.")
    if more_dates:
        notes.append(f"Only the first {MAX_RANGE_DATES} dates are shown; {len(dates) - MAX_RANGE_DATES} "
                     "more have selections. Narrow the range for those.")
    if missing_rows:
        notes.append("Rows for " + ", ".join(missing_rows) + " are not shown; the calendar "
                     "still counts them. Ask for those dates directly.")
    return result(data, source=source, truncated=truncated, notes=notes)


def _calendar(ctx, date_from: str, date_to: str) -> List[Dict[str, Any]]:
    """Every date and track with active selections in the span, with counts.

    Grouped in SQL, so it is complete whatever the row volume, and cheap: a
    month is a few dozen rows. Returns [] when the database does not answer;
    the callers treat that as "no calendar", never as "no tips".
    """
    try:
        grouped = ctx.db.query(
            "SELECT race_date, track, COUNT(*) AS n FROM selections WHERE race_date >= %s "
            "AND race_date <= %s AND " + ACTIVE + " GROUP BY race_date, track "
            "ORDER BY race_date, track LIMIT 600", (date_from, date_to))
    except DatabaseUnavailable:
        return []
    out = []
    for g in grouped:
        d = str(g.get("race_date") or "")[:10]
        if not d:
            continue
        out.append({"race_date": d, "track": str(g.get("track") or ""),
                    "n": _int(g.get("n")) or 1})
    return out


def _miss_with_context(ctx, date_from, date_to, track, race, source, span_rows=(), calendar=()):
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
        tracks = _tracks_with_tips(list(calendar or []) or rows)
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
    """Per-track counts from calendar entries (which carry n) or from plain rows."""
    by_track: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        t = str(r.get("track") or "").strip()
        if not t:
            continue
        slot = by_track.setdefault(t, {"track": t, "selections": 0, "dates": set()})
        slot["selections"] += _int(r.get("n")) or 1
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
        rows = ctx.db.query("SELECT MIN(race_date) AS first_date, MAX(race_date) AS last_date "
                            "FROM selections WHERE " + ACTIVE, ())
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
    by_date: Dict[str, Dict[str, Any]] = {}
    for g in _calendar(ctx, lo, hi):
        d = g["race_date"]
        if track and not track_matches(g["track"], track):
            continue
        slot = by_date.setdefault(d, {"race_date": d, "tracks": [], "selections": 0})
        if g["track"]:
            slot["tracks"].append(g["track"])
        slot["selections"] += g["n"]

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
        "race's full field. Pass date_to for a range of days (max 31): the answer carries a "
        "complete calendar of dates and tracks with selections and the leading selections "
        "per track per date."),
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
