"""get_race_card: the field for a meeting or a race.

The day's racecard artifact first (it is the card the pipeline scored), and
Punting Form's meeting payload when the artifact has no such meeting, which
replaces the Racing API client the TypeScript chat still points at.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..artifacts import ArtifactMissing, ArtifactUnavailable, racecard_path
from ..pf import PuntingFormOutsideWindow, PuntingFormUnavailable
from ._common import (Context, ToolSpec, cap_list, compact, failure, miss,
                      optional_race_number, optional_text, parse_iso_date,
                      result, track_matches)

RACE_HEADER_KEYS = ("race_number", "race_name", "distance", "going", "class", "race_class",
                    "off_time", "race_status", "prize_total", "state", "is_trial")
RUNNER_KEYS = ("number", "horse", "draw", "weight", "jockey", "trainer", "form",
               "age", "sex", "rating", "scratched", "scratch_deduction", "sp")
MAX_RUNNERS = 24


def _runner(r: Dict[str, Any]) -> Dict[str, Any]:
    out = compact(r, RUNNER_KEYS)
    odds = r.get("odds")
    if isinstance(odds, list) and odds:
        head = odds[:3]
        out["odds"] = [compact(o, ("bookmaker", "decimal", "fractional", "odds")) if isinstance(o, dict) else o
                       for o in head]
    stats = r.get("stats")
    if isinstance(stats, dict):
        out["stats"] = compact(stats, ("career_win_percent", "career_place_percent",
                                       "career_starts", "career_wins"))
    return out


def _races_from_artifact(meeting: Dict[str, Any], race: Optional[int]):
    races = [r for r in (meeting.get("races") or []) if isinstance(r, dict)]
    if race is None:
        heads = []
        for r in races:
            h = compact(r, RACE_HEADER_KEYS)
            h["runners"] = len([x for x in (r.get("runners") or []) if not x.get("scratched")])
            heads.append(h)
        return heads, None
    for r in races:
        try:
            if int(r.get("race_number")) == race:
                head = compact(r, RACE_HEADER_KEYS)
                runners, more = cap_list([x for x in (r.get("runners") or []) if isinstance(x, dict)],
                                         MAX_RUNNERS)
                head["runners"] = [_runner(x) for x in runners]
                if more:
                    head["runners_truncated"] = True
                return [head], None
        except (TypeError, ValueError):
            continue
    return [], f"No race {race} on that card"


def _from_artifact(ctx: Context, date: str, track: str, race: Optional[int]):
    payload, source = ctx.artifacts.get_json(racecard_path(date))
    meetings = payload if isinstance(payload, list) else (payload.get("meetings") or []) \
        if isinstance(payload, dict) else []
    courses = [str(m.get("course") or m.get("track") or "") for m in meetings if isinstance(m, dict)]
    for m in meetings:
        if not isinstance(m, dict):
            continue
        course = m.get("course") or m.get("track")
        if not track_matches(course, track):
            continue
        races, problem = _races_from_artifact(m, race)
        if problem:
            return miss(f"{problem} at {course} on {date}.", source=source,
                        notes=[f"Races on the card: {len(m.get('races') or [])}"])
        data = {"date": date, "track": course, "races": races}
        notes = [] if race is not None else [
            "Meeting view: race headers with field sizes. Ask with a race number for runners."]
        return result(data, source=source, notes=notes)
    return None, source, courses


def _from_puntingform(ctx: Context, date: str, track: str, race: Optional[int]):
    meeting = ctx.pf.find_meeting(date, track)
    if not meeting:
        return None
    detail = ctx.pf.meeting_detail(meeting.get("meetingId")) or {}
    races_out: List[Dict[str, Any]] = []
    for r in detail.get("races") or []:
        if not isinstance(r, dict):
            continue
        rn = r.get("raceNumber") or r.get("raceNo") or r.get("race_number")
        head = compact(r, ("raceNumber", "raceName", "distance", "trackCondition", "raceClass",
                           "startTime", "prizeMoney"))
        head["race_number"] = rn
        if race is None:
            head["runners"] = len(r.get("runners") or [])
            races_out.append(head)
            continue
        try:
            if int(rn) != race:
                continue
        except (TypeError, ValueError):
            continue
        runners, more = cap_list(r.get("runners") or [], MAX_RUNNERS)
        head["runners"] = [{
            "number": x.get("tabNo"), "horse": x.get("name") or x.get("runner"),
            "barrier": x.get("barrier"), "weight": x.get("weight"),
            "jockey": (x.get("jockey") or {}).get("fullName") if isinstance(x.get("jockey"), dict) else x.get("jockey"),
            "trainer": (x.get("trainer") or {}).get("fullName") if isinstance(x.get("trainer"), dict) else x.get("trainer"),
            "last10": x.get("last10"), "career_win_pct": x.get("winPct"),
            "career_place_pct": x.get("placePct"),
        } for x in runners if isinstance(x, dict)]
        if more:
            head["runners_truncated"] = True
        races_out.append(head)
    if race is not None and not races_out:
        return miss(f"Punting Form has the {track} meeting on {date} but no race {race}.",
                    source="puntingform:/form/meeting")
    track_name = (meeting.get("track") or {}).get("name") if isinstance(meeting.get("track"), dict) \
        else meeting.get("track")
    return result({"date": date, "track": track_name, "meeting_id": meeting.get("meetingId"),
                   "races": races_out}, source="puntingform:/form/meeting",
                  notes=["From Punting Form's meeting payload (no STRIDE racecard artifact for this meeting)."])


def get_race_card(ctx: Context, date: Any, track: Any, race: Any = None) -> Dict[str, Any]:
    date = parse_iso_date(date)
    track = optional_text(track, "track")
    if not track:
        from ._common import ToolError
        raise ToolError("track is required")
    race = optional_race_number(race)

    courses: List[str] = []
    artifact_source = ""
    try:
        hit = _from_artifact(ctx, date, track, race)
        if isinstance(hit, dict):
            return hit
        _, artifact_source, courses = hit
    except ArtifactMissing:
        pass
    except ArtifactUnavailable as e:
        return failure(str(e), source="s3")

    try:
        pf_hit = _from_puntingform(ctx, date, track, race)
        if pf_hit is not None:
            return pf_hit
    except PuntingFormOutsideWindow as e:
        return miss(f"No racecard for {track} on {date} in STRIDE's artifacts, and Punting Form "
                    f"does not serve that date: {e}", source=artifact_source or "none")
    except PuntingFormUnavailable as e:
        if courses:
            return miss(f"No meeting matching {track} on the {date} racecard.",
                        source=artifact_source, notes=[f"Meetings on that card: {', '.join(courses)}",
                                                       f"Punting Form was not reachable: {e}"])
        return failure(f"No racecard artifact for {date} and Punting Form did not answer: {e}",
                       source="puntingform")
    notes = [f"Meetings on that card: {', '.join(courses)}"] if courses else []
    return miss(f"Couldn't find a racecard for {track} on {date}.",
                source=artifact_source or "none", notes=notes)


SPEC = ToolSpec(
    name="get_race_card",
    description=(
        "The racecard for a meeting: races, distances, going, class and, for a given race "
        "number, the runners with barrier, weight, jockey, trainer and form. Reads STRIDE's "
        "scored card for the day first and falls back to Punting Form's meeting data."),
    input_schema={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Race date, YYYY-MM-DD."},
            "track": {"type": "string", "description": "Track name."},
            "race": {"type": "integer", "description": "Race number, for the runners."},
        },
        "required": ["date", "track"],
        "additionalProperties": False,
    },
    fn=get_race_card,
)
