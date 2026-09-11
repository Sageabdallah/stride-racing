"""lookup_horse: everything STRIDE holds on one horse.

Runs (race_results_history, with the winner's margin read the only correct
way, through result_margins.beaten_margin), sectionals (sectional_times),
the Elo franking row (franking_scores), STRIDE's own selections on the horse
with their graph-franking metrics (selections), and the blackbook.

The blackbook tables belong to stride-app: declared in its shared/schema.ts
and created by its runtime DDL. The columns read here are pinned in
BLACKBOOK_KEYS and BLACKBOOK_RUN_KEYS and covered by a test; if the tables
are absent the tool says so instead of failing the whole lookup.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from result_margins import beaten_margin  # flat module

from ..db import DatabaseUnavailable, relation_missing
from ._common import (Context, ToolError, ToolSpec, compact, failure, miss,
                      norm_name, norm_name_sql, optional_text, positive_int, result)

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


def lookup_horse(ctx: Context, name: Any, n: Any = 10) -> Dict[str, Any]:
    name = optional_text(name, "name", max_len=80)
    if not name:
        raise ToolError("name is required")
    key = norm_name(name)
    if not key:
        raise ToolError(f"name {name!r} has no letters or digits to match on")
    n = positive_int(n, "n", default=10, maximum=25)
    if ctx.db is None:
        return failure("No database is configured, so horse records are unavailable.",
                       source="neon")
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
        "Names are matched ignoring case, punctuation and a country suffix like (NZ)."),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The horse's name."},
            "n": {"type": "integer", "description": "How many recent runs and sectionals (default 10, max 25)."},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
    fn=lookup_horse,
)
