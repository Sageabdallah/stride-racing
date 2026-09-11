"""query_results: what actually happened, and how STRIDE's picks fared.

race_results_history is the settled record. STRIDE's side of each race comes
from selection_ledger (net-settled, both prices), prediction_audit and
stride_tip_results, attached per race so "how did our highest-edge tip go"
is one call. For a day the history has not settled yet and Punting Form
still serves, the results payload from Punting Form is the fallback.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from result_margins import beaten_margin  # flat module

from ..db import DatabaseUnavailable
from ..pf import PuntingFormOutsideWindow, PuntingFormUnavailable
from ._common import (Context, ToolSpec, cap_list, compact, failure, miss, norm_name,
                      norm_track, optional_race_number, optional_text, parse_iso_date,
                      result, track_matches)

RUNNER_KEYS = ("position", "horse_name", "beaten_margin", "sp_odds", "jockey", "barrier", "weight_kg")
LEDGER_KEYS = ("horse_name", "selection_origin", "should_bet", "confidence", "price_taken", "sp",
               "won", "settled", "settled_pnl", "refused", "model_edge_pp", "stake_units", "clv_pct")
AUDIT_KEYS = ("horse_name", "predicted_win_prob", "market_odds", "confidence", "edge",
              "actual_position", "won", "starting_price", "profit_loss", "result_status")
TIPRES_KEYS = ("tipped_horse_name", "tip_type", "tipped_odds", "tipped_edge_pct", "confidence",
               "actual_position", "result", "profit_loss", "tipped_horse_sp", "winner_sp",
               "actual_winner_name")
MAX_RACES = 60


def _group(rows: List[Dict[str, Any]]) -> "OrderedDict[Tuple[str, int], List[Dict[str, Any]]]":
    grouped: "OrderedDict[Tuple[str, int], List[Dict[str, Any]]]" = OrderedDict()
    for r in rows:
        try:
            rn = int(r.get("race_number"))
        except (TypeError, ValueError):
            continue
        grouped.setdefault((norm_track(r.get("track")), rn), []).append(r)
    return grouped


def _stride_side(db, date: str) -> Dict[Tuple[str, int], Dict[str, List[Dict[str, Any]]]]:
    side: Dict[Tuple[str, int], Dict[str, List[Dict[str, Any]]]] = {}

    def add(kind: str, rows: List[Dict[str, Any]], keys):
        for r in rows:
            try:
                k = (norm_track(r.get("track")), int(r.get("race_number")))
            except (TypeError, ValueError):
                continue
            side.setdefault(k, {}).setdefault(kind, []).append(compact(r, keys))

    add("ledger", db.query(
        "SELECT track, race_number, horse_name, selection_origin, should_bet, confidence, "
        "price_taken, sp, won, settled, settled_pnl, refused, model_edge_pp, stake_units, clv_pct "
        "FROM selection_ledger WHERE race_date = %s::date ORDER BY track, race_number LIMIT 300",
        (date,)), LEDGER_KEYS)
    add("prediction_audit", db.query(
        "SELECT track, race_number, horse_name, predicted_win_prob, market_odds, confidence, edge, "
        "actual_position, won, starting_price, profit_loss, result_status FROM prediction_audit "
        "WHERE race_date = %s ORDER BY track, race_number LIMIT 300", (date,)), AUDIT_KEYS)
    add("tip_results", db.query(
        "SELECT track, race_number, tipped_horse_name, tip_type, tipped_odds, tipped_edge_pct, "
        "confidence, actual_position, result, profit_loss, tipped_horse_sp, winner_sp, "
        "actual_winner_name FROM stride_tip_results WHERE race_date = %s "
        "ORDER BY track, race_number LIMIT 300", (date,)), TIPRES_KEYS)
    return side


def _from_history(ctx: Context, date: str, track: Optional[str], race: Optional[int]):
    rows = ctx.db.query(
        "SELECT track, race_date, race_number, race_name, distance_m, race_class, going, "
        "field_size, horse_name, position, margin_lengths, sp_odds, jockey, barrier, weight_kg "
        "FROM race_results_history WHERE race_date = %s "
        + ("AND race_number = %s " if race is not None else "")
        + "ORDER BY track, race_number, position NULLS LAST LIMIT 900",
        (date, race) if race is not None else (date,))
    rows = [r for r in rows if track_matches(r.get("track"), track)]
    source = "neon:race_results_history+selection_ledger+prediction_audit+stride_tip_results"
    if not rows:
        return None, source
    side = _stride_side(ctx.db, date)
    detail = "race" if race is not None else ("track" if track else "day")
    runner_cap = {"race": 24, "track": 4, "day": 3}[detail]
    races = []
    for (tkey, rn), runners in _group(rows).items():
        head = compact(runners[0], ("track", "race_date", "race_number", "race_name",
                                    "distance_m", "race_class", "going", "field_size"))
        shaped = []
        for r in runners:
            r = dict(r)
            r["beaten_margin"] = beaten_margin(r.get("position"), r.get("margin_lengths"))
            shaped.append(compact(r, RUNNER_KEYS))
        placed, more = cap_list(shaped, runner_cap)
        head["placings" if detail != "race" else "field"] = placed
        if more:
            head["runners_truncated"] = True
        ours = side.get((tkey, rn))
        if ours:
            head["stride_picks"] = ours
            # Where our pick finished, resolved here so the model need not
            # match names across two lists itself.
            picks = [p for p in ours.get("ledger", []) if p.get("horse_name")]
            for p in picks:
                pk = norm_name(p["horse_name"])
                hit = next((s for s in shaped if norm_name(s.get("horse_name")) == pk), None)
                if hit:
                    p["finished"] = hit.get("position")
                    p["beaten_margin"] = hit.get("beaten_margin")
        races.append(head)
    shown, more = cap_list(races, MAX_RACES)
    notes = ["beaten_margin is lengths behind the winner; null for the winner.",
             "stride_picks.ledger is net of commission (settled_pnl); tip_results.profit_loss is gross."]
    if detail == "day":
        notes.append("Day view shows the first three home per race. Ask with a track or race for more.")
    return result({"date": date, "races": shown}, source=source, truncated=more, notes=notes), source


def _from_puntingform(ctx: Context, date: str, track: Optional[str], race: Optional[int]):
    meetings = []
    if track:
        m = ctx.pf.find_meeting(date, track)
        meetings = [m] if m else []
    else:
        meetings, _ = cap_list(ctx.pf.meetings_for_date(date), 8)
    races_out = []
    for m in meetings:
        if not isinstance(m, dict):
            continue
        payload = ctx.pf.results_for_meeting(m.get("meetingId")) or []
        for meet in payload if isinstance(payload, list) else [payload]:
            tname = meet.get("track") if isinstance(meet, dict) else None
            tname = tname.get("name") if isinstance(tname, dict) else tname
            for r in (meet.get("raceResults") or []) if isinstance(meet, dict) else []:
                rn = r.get("raceNumber")
                if race is not None:
                    try:
                        if int(rn) != race:
                            continue
                    except (TypeError, ValueError):
                        continue
                runners = sorted((r.get("runners") or []),
                                 key=lambda x: (x.get("position") is None, x.get("position") or 99))
                placed, more = cap_list(runners, 24 if race is not None else 4)
                races_out.append({
                    "track": tname, "race_number": rn,
                    "track_condition": r.get("trackConditionLabel"),
                    "placings": [compact(x, ("position", "runner", "margin", "price", "tabNo",
                                             "jockey", "barrier")) for x in placed
                                 if isinstance(x, dict)],
                    "runners_truncated": more or None,
                })
    if not races_out:
        return None
    return result({"date": date, "races": races_out}, source="puntingform:/form/results",
                  notes=["From Punting Form's results payload: STRIDE's history has not "
                         "recorded this day yet, so no STRIDE pick outcomes are attached."])


def query_results(ctx: Context, date: Any, track: Any = None, race: Any = None) -> Dict[str, Any]:
    date = parse_iso_date(date)
    track = optional_text(track, "track")
    race = optional_race_number(race)
    source = "neon:race_results_history"
    if ctx.db is not None:
        try:
            hit, source = _from_history(ctx, date, track, race)
            if hit is not None:
                return hit
        except DatabaseUnavailable as e:
            return failure(f"The database did not answer: {e}", source=source)
    try:
        pf_hit = _from_puntingform(ctx, date, track, race)
        if pf_hit is not None:
            return pf_hit
        where = f" at {track}" if track else ""
        return miss(f"Couldn't find results{where} for {date} in STRIDE's history or at Punting Form.",
                    source=source)
    except PuntingFormOutsideWindow:
        where = f" at {track}" if track else ""
        return miss(f"No results{where} recorded for {date}, and Punting Form does not serve "
                    f"dates that far back.", source=source)
    except PuntingFormUnavailable as e:
        where = f" at {track}" if track else ""
        return miss(f"No results{where} recorded for {date} in STRIDE's history.", source=source,
                    notes=[f"Punting Form was not reachable for a fallback: {e}"])


SPEC = ToolSpec(
    name="query_results",
    description=(
        "Race results for a date: placings with beaten margins and starting prices, and for "
        "each race what STRIDE picked and how it fared (settled net P&L, prediction audit, "
        "tip results). Use for 'who won', 'how did our tip go', 'which horse beat our pick'. "
        "Narrow with a track, and a race number for the full field."),
    input_schema={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Race date, YYYY-MM-DD."},
            "track": {"type": "string", "description": "Track name."},
            "race": {"type": "integer", "description": "Race number."},
        },
        "required": ["date"],
        "additionalProperties": False,
    },
    fn=query_results,
)
