"""puntingform: Punting Form's live data behind one tool with an endpoint enum.

Every endpoint goes through chat.pf, so envelope errors, the auth failure
and the pacing are pf_client's. Payloads are cut to the fields a racing
question needs; meeting_detail in particular is a whole card with per-runner
form and would fill the context on its own.

Punting Form's data is licensed for personal use (PUNTINGFORM_MIGRATION.md).
This tool is for the operator's own questions; the plan's §2 records the
licence as the gate on any wider audience.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..pf import PuntingFormOutsideWindow, PuntingFormUnavailable
from ._common import (Context, ToolError, ToolSpec, cap_list, compact, failure, miss,
                      optional_race_number, optional_text, parse_iso_date, result,
                      track_matches)

ENDPOINTS = ("meetings", "meeting_detail", "results", "scratchings", "conditions",
             "speedmaps", "ratings", "strike_rates")


def _person(v: Any) -> Any:
    if isinstance(v, dict):
        return v.get("fullName") or v.get("name") or v.get("surname")
    return v


def _track_name(m: Any) -> Any:
    t = m.get("track") if isinstance(m, dict) else None
    return t.get("name") if isinstance(t, dict) else t


def _meeting(ctx: Context, date: Optional[str], track: Optional[str], meeting_id: Any):
    if meeting_id:
        return {"meetingId": meeting_id}
    if not date or not track:
        raise ToolError("this endpoint needs meeting_id, or date and track")
    m = ctx.pf.find_meeting(date, track)
    if not m:
        raise LookupError(f"No Punting Form meeting for {track} on {date}")
    return m


def _meetings(ctx, date):
    rows = []
    for m in ctx.pf.meetings_for_date(date):
        if not isinstance(m, dict):
            continue
        t = m.get("track") if isinstance(m.get("track"), dict) else {}
        rows.append({"meetingId": m.get("meetingId"), "track": _track_name(m),
                     "state": t.get("state"), "country": t.get("country"),
                     "tabMeeting": m.get("tabMeeting"), "isBarrierTrial": m.get("isBarrierTrial"),
                     "railPosition": m.get("railPosition"),
                     "expectedCondition": m.get("expectedCondition")})
    rows, more = cap_list(rows, 40)
    return rows, more


def _detail(ctx, meeting):
    d = ctx.pf.meeting_detail(meeting.get("meetingId")) or {}
    races = []
    for r in d.get("races") or []:
        if not isinstance(r, dict):
            continue
        runners, more = cap_list(r.get("runners") or [], 24)
        races.append({
            "raceNumber": r.get("raceNumber") or r.get("raceNo"), "raceName": r.get("raceName"),
            "distance": r.get("distance"), "trackCondition": r.get("trackCondition"),
            "runners": [{"tabNo": x.get("tabNo"), "name": x.get("name"), "barrier": x.get("barrier"),
                         "weight": x.get("weight"), "jockey": _person(x.get("jockey")),
                         "trainer": _person(x.get("trainer")), "last10": x.get("last10"),
                         "winPct": x.get("winPct"), "placePct": x.get("placePct")}
                        for x in runners if isinstance(x, dict)],
            "runners_truncated": more or None,
        })
    return {"track": _track_name(d) or _track_name(meeting), "races": cap_list(races, 12)[0]}


def _results(ctx, meeting, race):
    payload = ctx.pf.results_for_meeting(meeting.get("meetingId")) or []
    out = []
    for meet in payload if isinstance(payload, list) else [payload]:
        if not isinstance(meet, dict):
            continue
        for r in meet.get("raceResults") or []:
            rn = r.get("raceNumber")
            if race is not None and str(rn) != str(race):
                continue
            runners = sorted(r.get("runners") or [],
                             key=lambda x: (x.get("position") is None, x.get("position") or 99))
            placed, more = cap_list(runners, 24 if race is not None else 4)
            out.append({"track": _track_name(meet), "raceNumber": rn,
                        "trackCondition": r.get("trackConditionLabel"),
                        "officialRaceTime": r.get("officialRaceTime"),
                        "runners": [compact(x, ("position", "tabNo", "runner", "margin", "price",
                                                "jockey", "trainer", "barrier", "inRun"))
                                    for x in placed if isinstance(x, dict)],
                        "runners_truncated": more or None})
    return cap_list(out, 12)[0]


def puntingform(ctx: Context, endpoint: Any, date: Any = None, track: Any = None,
                meeting_id: Any = None, race: Any = None, jurisdiction: Any = None,
                entity_type: Any = None) -> Dict[str, Any]:
    endpoint = str(endpoint or "").strip().lower()
    if endpoint not in ENDPOINTS:
        raise ToolError(f"endpoint must be one of {', '.join(ENDPOINTS)}")
    date = parse_iso_date(date) if date else None
    track = optional_text(track, "track")
    race = optional_race_number(race)
    source = f"puntingform:{endpoint}"
    try:
        if endpoint == "meetings":
            if not date:
                raise ToolError("meetings needs a date")
            rows, more = _meetings(ctx, date)
            if not rows:
                return miss(f"Punting Form lists no meetings on {date}.", source=source)
            return result({"date": date, "meetings": rows}, source=source, truncated=more)
        if endpoint == "meeting_detail":
            m = _meeting(ctx, date, track, meeting_id)
            return result(_detail(ctx, m), source=source)
        if endpoint == "results":
            m = _meeting(ctx, date, track, meeting_id)
            rows = _results(ctx, m, race)
            if not rows:
                return miss("Punting Form has no results for that meeting yet.", source=source)
            return result({"races": rows}, source=source)
        if endpoint == "scratchings":
            rows = [s for s in ctx.pf.scratchings(jurisdiction) if isinstance(s, dict)
                    and (not date or str(s.get("meetingDate", ""))[:10] == date)
                    and track_matches(s.get("track"), track)]
            rows, more = cap_list([compact(s, ("meetingDate", "track", "raceNo", "tabNo", "runnerId",
                                               "timeStamp", "deduction")) for s in rows], 60)
            if not rows:
                return miss("No scratchings listed" + (f" for {track}" if track else "")
                            + (f" on {date}" if date else "") + ".", source=source)
            return result({"scratchings": rows}, source=source, truncated=more)
        if endpoint == "conditions":
            rows = [c for c in ctx.pf.conditions(jurisdiction) if isinstance(c, dict)
                    and (not date or str(c.get("meetingDate", ""))[:10] == date)
                    and track_matches(c.get("track"), track)]
            rows, more = cap_list([compact(c, ("meetingDate", "track", "trackCondition",
                                               "trackConditionNumber", "weather", "rail",
                                               "penetrometer", "rainfall", "irrigation", "comment",
                                               "lastUpdate")) for c in rows], 60)
            if not rows:
                return miss("No track conditions listed for that filter.", source=source)
            return result({"conditions": rows}, source=source, truncated=more)
        if endpoint == "speedmaps":
            m = _meeting(ctx, date, track, meeting_id)
            maps = ctx.pf.speedmaps_for_meeting(m.get("meetingId"), race or 0)
            shaped = []
            for sm in maps if isinstance(maps, list) else [maps]:
                if not isinstance(sm, dict):
                    continue
                items, more = cap_list(sm.get("items") or [], 24)
                shaped.append({"raceNo": sm.get("raceNo"), "track": sm.get("track"),
                               "items": [compact(i, ("tabNo", "runnerName", "barrier", "speed",
                                                     "settle", "ratedRunStyle", "pfScore",
                                                     "neuralPrice", "assessedPrice", "mapA2E"))
                                         for i in items if isinstance(i, dict)],
                               "items_truncated": more or None})
            if not shaped:
                return miss("No speed maps for that meeting.", source=source)
            return result({"speedmaps": cap_list(shaped, 12)[0]}, source=source)
        if endpoint == "ratings":
            m = _meeting(ctx, date, track, meeting_id)
            rows, more = cap_list(ctx.pf.ratings_for_meeting(m.get("meetingId")), 40)
            if not rows:
                return miss("No ratings for that meeting.", source=source)
            keep = ("raceNo", "tabNo", "runnerName", "name", "pfScore", "neuralPrice", "runStyle",
                    "rating", "lastStartRating", "bestRating", "timeRank", "assessedPrice")
            return result({"ratings": [compact(r, keep) if isinstance(r, dict) else r for r in rows]},
                          source=source, truncated=more)
        if endpoint == "strike_rates":
            et = None
            if entity_type not in (None, ""):
                try:
                    et = int(entity_type)
                except (TypeError, ValueError):
                    raise ToolError("entity_type is Punting Form's integer enum; omit it for all")
            rows, more = cap_list(ctx.pf.strike_rates(et, jurisdiction), 50)
            if not rows:
                return miss("No strike rates returned.", source=source)
            keep = ("entityName", "careerWins", "careerStarts", "last100Wins", "last100Starts",
                    "careerExpectedWins", "last100ExpectedWins", "careerPL", "last100PL")
            return result({"strike_rates": [compact(r, keep) if isinstance(r, dict) else r for r in rows]},
                          source=source, truncated=more)
    except PuntingFormOutsideWindow as e:
        return miss(f"Punting Form does not serve that date on the Starter plan: {e}. "
                    f"STRIDE's own records (query_results, lookup_horse) cover older dates.",
                    source=source)
    except PuntingFormUnavailable as e:
        return failure(str(e), source=source)
    except LookupError as e:
        return miss(str(e), source=source)
    raise ToolError(f"unhandled endpoint {endpoint}")


SPEC = ToolSpec(
    name="puntingform",
    description=(
        "Live Punting Form data (about the last 31 days): meetings for a date, a meeting's "
        "card with runner form (meeting_detail), results, upcoming scratchings, track "
        "conditions and weather, speed maps, ratings, and jockey/trainer strike rates. "
        "Give date and track, or a meeting_id from the meetings endpoint."),
    input_schema={
        "type": "object",
        "properties": {
            "endpoint": {"type": "string", "enum": list(ENDPOINTS)},
            "date": {"type": "string", "description": "YYYY-MM-DD."},
            "track": {"type": "string", "description": "Track name, to resolve the meeting."},
            "meeting_id": {"type": "integer", "description": "Punting Form meetingId."},
            "race": {"type": "integer", "description": "Race number, for results and speedmaps."},
            "jurisdiction": {"type": "integer", "description": "Punting Form jurisdiction enum (optional)."},
            "entity_type": {"type": "integer", "description": "Punting Form entity enum for strike_rates (optional)."},
        },
        "required": ["endpoint"],
        "additionalProperties": False,
    },
    fn=puntingform,
)
