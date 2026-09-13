"""lookup_horse: everything STRIDE holds on one horse.

Runs (race_results_history, with the winner's margin read the only correct
way, through result_margins.beaten_margin), sectionals (sectional_times),
the Elo franking row (franking_scores), STRIDE's own selections on the horse
with their graph-franking metrics (selections), and the blackbook.

The blackbook tables belong to stride-app: declared in its shared/schema.ts
and created by its runtime DDL. The columns read here are pinned in
BLACKBOOK_KEYS and BLACKBOOK_RUN_KEYS and covered by a test; if the tables
are absent the tool says so instead of failing the whole lookup.

Without a name, a blackbooked_from/blackbooked_to window lists the horses
blackbooked in that period, each with its runs in the results history since
its source race. That is the only way to answer "who did we blackbook in
March, and which of them have won since": the blackbook is keyed by horse,
so a period question needs the listing before any name exists to look up.
chat-eval run #1 (2026-09-13) showed the agent, lacking it, reaching for a
month of tips instead and then asking the user for names.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from result_margins import beaten_margin  # flat module

from ..db import DatabaseUnavailable, relation_missing
from ._common import (Context, ToolError, ToolSpec, cap_list, compact, failure, miss,
                      norm_name, norm_name_sql, optional_text, parse_iso_date, positive_int,
                      result)

RUN_KEYS = ("race_date", "track", "race_number", "race_name", "distance_m", "race_class",
            "going", "position", "beaten_margin", "sp_odds", "jockey", "barrier",
            "weight_kg", "field_size")
SECTIONAL_KEYS = ("race_date", "track", "race_number", "distance_m", "last_600m_time",
                  "last_400m_time", "last_200m_time", "last_600m_speed", "finishing_burst",
                  "avg_speed", "svi", "rsi", "z_600m", "track_config", "source")
FRANKING_KEYS = ("horse_name", "global_elo", "franking_score", "franking_confidence",
                 "anti_franked", "field_strength_avg", "form_quality_trend",
                 "best_adjusted_margin", "collateral_advantage", "data_points",
                 "last_race_date", "computed_at")
SELECTION_KEYS = ("race_date", "track", "race_number", "confidence", "edge", "market_odds",
                  "model_probability", "win_percentage", "expected_value", "value_rating",
                  "franking_elo", "franking_score", "is_anti_franked")
GRAPH_KEYS = ("pagerank_authority", "community_strength", "form_stability",
              "graph_franking_score", "graph_franking_depth")
BLACKBOOK_KEYS = ("id", "horse_name", "source_track", "source_race_date", "source_race_number",
                  "source_position", "source_margin_lengths", "primary_reason",
                  "secondary_evidence_tags", "incident_summary", "sectional_delta_600m",
                  "franking_score", "readiness_band", "status", "expiry_reason", "created_at")
BLACKBOOK_RUN_KEYS = ("track", "race_date", "race_number", "market_price", "true_price",
                      "model_win_prob", "value_edge_pct", "readiness_band", "verdict", "status")
WINDOW_ENTRY_KEYS = ("horse_name", "source_track", "source_race_date", "source_race_number",
                     "source_position", "primary_reason", "readiness_band", "status")
WINDOW_RUN_KEYS = ("race_date", "track", "race_number", "race_class", "position", "beaten_margin",
                   "sp_odds")
WINDOW_TRACKED_KEYS = ("race_date", "track", "race_number", "verdict", "status")
MAX_WINDOW_ENTRIES = 30
MAX_RUNS_SINCE = 3


def _runs(db, key: str, n: int) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT race_date, track, race_number, race_name, distance_m, race_class, going, "
        "position, margin_lengths, sp_odds, jockey, barrier, weight_kg, field_size, horse_id, "
        "horse_name FROM race_results_history WHERE " + norm_name_sql("horse_name") + " = %s "
        "ORDER BY race_date DESC LIMIT %s", (key, n))
    out = []
    for r in rows:
        r = dict(r)
        r["beaten_margin"] = beaten_margin(r.get("position"), r.get("margin_lengths"))
        out.append(r)
    return out


def _suggestions(db, key: str) -> List[str]:
    if len(key) < 4:
        return []
    rows = db.query(
        "SELECT DISTINCT horse_name FROM race_results_history WHERE "
        + norm_name_sql("horse_name") + " LIKE %s LIMIT 5", (f"%{key[:6]}%",))
    return [r["horse_name"] for r in rows if r.get("horse_name")]


def _sectionals(db, spellings: List[str], n: int) -> List[Dict[str, Any]]:
    if not spellings:
        return []
    return db.query(
        "SELECT race_date, track, race_number, distance_m, last_600m_time, last_400m_time, "
        "last_200m_time, last_600m_speed, finishing_burst, avg_speed, svi, rsi, z_600m, "
        "track_config, source FROM sectional_times WHERE LOWER(horse_name) = ANY(%s) "
        "ORDER BY race_date DESC LIMIT %s", ([s.lower() for s in spellings], n))


def _franking(db, key: str) -> Optional[Dict[str, Any]]:
    rows = db.query(
        "SELECT horse_id, horse_name, global_elo, franking_score, franking_confidence, "
        "anti_franked, field_strength_avg, form_quality_trend, best_adjusted_margin, "
        "collateral_advantage, data_points, last_race_date, computed_at FROM franking_scores "
        "WHERE " + norm_name_sql("horse_name") + " = %s ORDER BY computed_at DESC NULLS LAST "
        "LIMIT 1", (key,))
    return rows[0] if rows else None


def _selections(db, key: str) -> List[Dict[str, Any]]:
    return db.query(
        "SELECT race_date, track, race_number, confidence, edge, market_odds, model_probability, "
        "win_percentage, expected_value, value_rating, franking_elo, franking_score, "
        "is_anti_franked, pagerank_authority, community_strength, form_stability, "
        "graph_franking_score, graph_franking_depth FROM selections WHERE "
        + norm_name_sql("horse_name") + " = %s ORDER BY race_date DESC LIMIT 5", (key,))


def _blackbook(db, key: str) -> Dict[str, Any]:
    try:
        entries = db.query(
            "SELECT id, horse_name, source_track, source_race_date, source_race_number, "
            "source_position, source_margin_lengths, primary_reason, secondary_evidence_tags, "
            "incident_summary, sectional_delta_600m, franking_score, readiness_band, status, "
            "expiry_reason, created_at FROM blackbook_entries WHERE "
            + norm_name_sql("horse_name") + " = %s ORDER BY created_at DESC LIMIT 5", (key,))
    except DatabaseUnavailable as e:
        if relation_missing(e):
            return {"available": False,
                    "reason": "The blackbook tables are not present in this database; they are "
                              "created by the STRIDE app, not the pipeline."}
        raise
    if not entries:
        return {"available": True, "entries": []}
    ids = [e["id"] for e in entries if e.get("id")]
    runs: List[Dict[str, Any]] = []
    if ids:
        try:
            runs = db.query(
                "SELECT blackbook_entry_id, track, race_date, race_number, market_price, "
                "true_price, model_win_prob, value_edge_pct, readiness_band, verdict, status "
                "FROM blackbook_entry_runs WHERE blackbook_entry_id = ANY(%s) "
                "ORDER BY race_date DESC LIMIT 10", (ids,))
        except DatabaseUnavailable as e:
            if not relation_missing(e):
                raise
    return {"available": True,
            "entries": [compact(e, BLACKBOOK_KEYS) for e in entries],
            "subsequent_runs": [compact(r, BLACKBOOK_RUN_KEYS) for r in runs]}


def _is_win(position: Any) -> bool:
    try:
        return int(position) == 1
    except (TypeError, ValueError):
        return False


def _blackbook_window(ctx: Context, date_from: str, date_to: str) -> Dict[str, Any]:
    """Horses blackbooked in the window, each with its runs since the source race."""
    db = ctx.db
    source = "neon:blackbook_entries,blackbook_entry_runs,race_results_history"
    # created_at is a timestamp and source_race_date is text; both compare as
    # ISO dates once cut to ten characters, so neither type is assumed.
    try:
        entries = db.query(
            "SELECT id, horse_name, source_track, source_race_date, source_race_number, "
            "source_position, primary_reason, readiness_band, status, created_at "
            "FROM blackbook_entries WHERE substr(created_at::text, 1, 10) >= %s "
            "AND substr(created_at::text, 1, 10) <= %s ORDER BY created_at, horse_name LIMIT %s",
            (date_from, date_to, MAX_WINDOW_ENTRIES + 1))
    except DatabaseUnavailable as e:
        if relation_missing(e):
            return miss("The blackbook tables are not present in this database; they are created "
                        "by the STRIDE app, not the pipeline.", source=source)
        raise
    entries, more = cap_list(entries, MAX_WINDOW_ENTRIES)
    if not entries:
        notes = []
        span = db.query(
            "SELECT substr(MIN(created_at)::text, 1, 10) AS first_date, "
            "substr(MAX(created_at)::text, 1, 10) AS last_date, COUNT(*) AS n FROM blackbook_entries", ())
        head = span[0] if span else {}
        if head.get("n"):
            notes.append(f"The blackbook holds {head['n']} entries, made between "
                         f"{head.get('first_date')} and {head.get('last_date')}.")
        else:
            notes.append("The blackbook has no entries at all yet.")
        return miss(f"No horses were blackbooked between {date_from} and {date_to}.",
                    source=source, notes=notes)

    keys = sorted({norm_name(e.get("horse_name")) for e in entries if e.get("horse_name")})
    runs = db.query(
        "SELECT race_date, track, race_number, race_class, position, margin_lengths, sp_odds, "
        "horse_name, " + norm_name_sql("horse_name") + " AS name_key FROM race_results_history "
        "WHERE " + norm_name_sql("horse_name") + " = ANY(%s) ORDER BY race_date DESC LIMIT 600",
        (keys,))
    by_key: Dict[str, List[Dict[str, Any]]] = {}
    for r in runs:
        by_key.setdefault(str(r.get("name_key") or ""), []).append(r)
    tracked: Dict[str, List[Dict[str, Any]]] = {}
    ids = [e["id"] for e in entries if e.get("id")]
    if ids:
        try:
            for r in db.query(
                    "SELECT blackbook_entry_id, track, race_date, race_number, market_price, "
                    "true_price, model_win_prob, value_edge_pct, readiness_band, verdict, status "
                    "FROM blackbook_entry_runs WHERE blackbook_entry_id = ANY(%s) "
                    "ORDER BY race_date DESC LIMIT 200", (ids,)):
                tracked.setdefault(str(r.get("blackbook_entry_id")), []).append(r)
        except DatabaseUnavailable as e:
            if not relation_missing(e):
                raise

    horses = []
    raced = won = 0
    for e in entries:
        since_date = str(e.get("source_race_date") or e.get("created_at") or "")[:10]
        since = [r for r in by_key.get(norm_name(e.get("horse_name")), [])
                 if str(r.get("race_date") or "")[:10] > since_date]
        shaped = []
        for r in since[:MAX_RUNS_SINCE]:
            r = dict(r)
            r["beaten_margin"] = beaten_margin(r.get("position"), r.get("margin_lengths"))
            shaped.append(compact(r, WINDOW_RUN_KEYS))
        wins = sum(1 for r in since if _is_win(r.get("position")))
        raced += 1 if since else 0
        won += 1 if wins else 0
        entry = compact(e, WINDOW_ENTRY_KEYS)
        entry["blackbooked_on"] = str(e.get("created_at") or "")[:10]
        horse = {"entry": entry, "runs_since_count": len(since), "wins_since": wins,
                 "runs_since": shaped}
        seen = tracked.get(str(e.get("id")))
        if seen:
            horse["app_tracked_runs"] = [compact(r, WINDOW_TRACKED_KEYS) for r in seen[:2]]
        horses.append(horse)
    data = {"blackbooked_from": date_from, "blackbooked_to": date_to, "entries": len(entries),
            "raced_since": raced, "won_since": won, "horses": horses}
    notes = [
        "Entries are those made in the window, by the date the horse was blackbooked. runs_since "
        "are the horse's runs in STRIDE's results history after its source race, most recent first "
        f"(up to {MAX_RUNS_SINCE} shown; runs_since_count is the full count); wins_since counts "
        "wins among all of them, and beaten_margin is null for a winner.",
        "For a horse's full record, sectionals and franking, ask by name.",
    ]
    if more:
        notes.append(f"More than {MAX_WINDOW_ENTRIES} horses were blackbooked in the window; "
                     "the earliest are shown. Narrow the dates for the rest.")
    return result(data, source=source, truncated=more, notes=notes)


def lookup_horse(ctx: Context, name: Any = None, n: Any = 10, blackbooked_from: Any = None,
                 blackbooked_to: Any = None) -> Dict[str, Any]:
    name = optional_text(name, "name", max_len=80)
    window = optional_text(blackbooked_from, "blackbooked_from"), optional_text(blackbooked_to, "blackbooked_to")
    if name and any(window):
        raise ToolError("give either a name or a blackbooked_from/blackbooked_to window, not both")
    if not name and not any(window):
        raise ToolError("name is required, or blackbooked_from and blackbooked_to to list the "
                        "horses blackbooked in a period")
    if ctx.db is None:
        return failure("No database is configured, so horse records are unavailable.",
                       source="neon")
    if not name:
        date_from = parse_iso_date(window[0] or window[1], "blackbooked_from")
        date_to = parse_iso_date(window[1] or window[0], "blackbooked_to")
        if date_to < date_from:
            raise ToolError(f"blackbooked_to ({date_to}) is before blackbooked_from ({date_from})")
        try:
            return _blackbook_window(ctx, date_from, date_to)
        except DatabaseUnavailable as e:
            return failure(f"The database did not answer: {e}", source="neon:blackbook_entries")
    key = norm_name(name)
    if not key:
        raise ToolError(f"name {name!r} has no letters or digits to match on")
    n = positive_int(n, "n", default=10, maximum=25)
    source = "neon:race_results_history,sectional_times,franking_scores,selections,blackbook_entries"
    try:
        runs = _runs(ctx.db, key, n)
        spellings = sorted({str(r["horse_name"]).lower() for r in runs if r.get("horse_name")} | {name.lower()})
        sectionals = _sectionals(ctx.db, spellings, n)
        franking = _franking(ctx.db, key)
        selections = _selections(ctx.db, key)
        blackbook = _blackbook(ctx.db, key)
    except DatabaseUnavailable as e:
        return failure(f"The database did not answer: {e}", source=source)

    graph = next((compact(s, GRAPH_KEYS) for s in selections
                  if s.get("pagerank_authority") is not None), {})
    found = bool(runs or sectionals or franking or selections or blackbook.get("entries"))
    if not found:
        notes = []
        try:
            sugg = _suggestions(ctx.db, key)
            if sugg:
                notes.append("Similar names in the records: " + ", ".join(sugg))
        except DatabaseUnavailable:
            pass
        return miss(f"Couldn't find a horse called {name} in STRIDE's records "
                    f"(no runs, sectionals, franking, selections or blackbook entry).",
                    source=source, notes=notes)

    data = {
        "horse": (runs[0]["horse_name"] if runs else name),
        "horse_id": (runs[0].get("horse_id") if runs else None),
        "runs": [compact(r, RUN_KEYS) for r in runs],
        "sectionals": [compact(s, SECTIONAL_KEYS) for s in sectionals],
        "franking": compact(franking, FRANKING_KEYS) if franking else None,
        "graph_franking": graph or None,
        "stride_selections": [compact(s, SELECTION_KEYS) for s in selections],
        "blackbook": blackbook,
    }
    notes = [
        "beaten_margin is lengths behind the winner and is null for the winner.",
        f"runs and sectionals are the most recent {n}; ask for more with n.",
    ]
    if not graph:
        notes.append("No graph-franking metrics (pagerank, community, form stability) are "
                     "recorded for this horse; they exist only for horses STRIDE has selected.")
    return result(data, source=source, notes=notes)


SPEC = ToolSpec(
    name="lookup_horse",
    description=(
        "Everything STRIDE holds on one horse by name: recent runs with beaten margins and "
        "starting prices, closing sectionals, the Elo franking row, STRIDE's own selections "
        "on the horse with graph-franking metrics (pagerank authority, community strength, "
        "form stability), and any blackbook entry with the reason and subsequent runs. "
        "Names are matched ignoring case, punctuation and a country suffix like (NZ). "
        "Without a name, blackbooked_from and blackbooked_to list every horse blackbooked in "
        "that period with the reason and its runs and wins since: the only way to answer "
        "'who did we blackbook in March, and which have won since'. The blackbook is not the "
        "tips; do not answer a blackbook question from get_stride_tips."),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The horse's name. Omit to list a blackbook period instead."},
            "n": {"type": "integer", "description": "How many recent runs and sectionals (default 10, max 25)."},
            "blackbooked_from": {"type": "string",
                                 "description": "Start of the period, YYYY-MM-DD: list the horses blackbooked from this date."},
            "blackbooked_to": {"type": "string",
                               "description": "End of the period, YYYY-MM-DD (defaults to blackbooked_from)."},
        },
        "required": [],
        "additionalProperties": False,
    },
    fn=lookup_horse,
)
