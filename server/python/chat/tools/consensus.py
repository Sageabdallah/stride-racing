"""get_consensus: what the tipster panel and web research said about a race.

Read only. The day's consensus artifact is keyed '{track_key}_R{n}' with a
dict of horses per race (consensus_agent.py); the consensus_scores table is
the mirror the agent writes at the same time. The agent itself is untouched
by this package, as CLAUDE.md requires.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..artifacts import ArtifactMissing, ArtifactUnavailable, consensus_path
from ..db import DatabaseUnavailable
from ._common import (Context, ToolError, ToolSpec, cap_list, compact, failure, miss,
                      optional_race_number, optional_text, parse_iso_date, result,
                      split_race_key, track_matches)

HORSE_KEYS = ("consensus_score", "crowd_score", "vote_pct", "total_mentions",
              "independent_mentions", "commercial_mentions", "bucket_spread",
              "high_confidence_mentions", "market_alignment", "tipsters_polled",
              "independent_source_rate", "reasoning_alignment")
MAX_HORSES = 14


def _shape(horses: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for name, d in horses.items():
        if not isinstance(d, dict):
            continue
        row = {"horse": name}
        row.update(compact(d, HORSE_KEYS))
        srcs = d.get("sources")
        if isinstance(srcs, list) and srcs:
            row["sources"] = srcs[:6]
        rows.append(row)
    rows.sort(key=lambda r: (-(float(r.get("consensus_score") or 0)),
                             -(int(r.get("total_mentions") or 0))))
    return rows


def _from_artifact(ctx: Context, date: str, track: str, race: int):
    payload, source = ctx.artifacts.get_json(consensus_path(date))
    if not isinstance(payload, dict) or not payload:
        return miss(f"The consensus file for {date} is empty (no racecard that day, or a dry run).",
                    source=source)
    keys_seen = []
    for key, horses in payload.items():
        tkey, rn = split_race_key(key)
        keys_seen.append(key)
        if rn == race and track_matches(tkey, track) and isinstance(horses, dict):
            rows, more = cap_list(_shape(horses), MAX_HORSES)
            if not rows:
                return miss(f"Consensus ran for {track} R{race} on {date} but scored no horses.",
                            source=source)
            mentioned = sum(1 for r in rows if int(r.get("total_mentions") or 0) > 0)
            return result({"date": date, "track": track, "race": race, "horses": rows,
                           "horses_with_mentions": mentioned}, source=source, truncated=more,
                          notes=["consensus_score is the panel-weighted score; vote_pct is the "
                                 "share of polled tipsters naming the horse."])
    tracks = sorted({split_race_key(k)[0] for k in keys_seen})
    return miss(f"No consensus entry for {track} R{race} on {date}.", source=source,
                notes=[f"Tracks with consensus that day: {', '.join(tracks)}"] if tracks else None)


def _from_table(ctx: Context, date: str, track: str, race: int):
    rows = ctx.db.query(
        "SELECT track, horse_name, consensus_score, total_mentions, bucket_spread, "
        "high_confidence_mentions, sources, vote_pct, tipsters_polled, independent_source_rate "
        "FROM consensus_scores WHERE race_date = %s::date AND race_number = %s "
        "ORDER BY consensus_score DESC LIMIT 60", (date, race))
    rows = [r for r in rows if track_matches(r.get("track"), track)]
    if not rows:
        return miss(f"No consensus recorded for {track} R{race} on {date}.", source="neon:consensus_scores")
    shaped = []
    for r in rows:
        row = {"horse": r.get("horse_name")}
        row.update(compact(r, HORSE_KEYS))
        srcs = r.get("sources")
        if isinstance(srcs, list) and srcs:
            row["sources"] = srcs[:6]
        shaped.append(row)
    shaped, more = cap_list(shaped, MAX_HORSES)
    return result({"date": date, "track": track, "race": race, "horses": shaped},
                  source="neon:consensus_scores", truncated=more)


def get_consensus(ctx: Context, date: Any, track: Any, race: Any) -> Dict[str, Any]:
    date = parse_iso_date(date)
    track = optional_text(track, "track")
    race = optional_race_number(race)
    if not track or race is None:
        raise ToolError("track and race are required")
    try:
        return _from_artifact(ctx, date, track, race)
    except ArtifactMissing:
        pass
    except ArtifactUnavailable as e:
        return failure(str(e), source="s3")
    if ctx.db is None:
        return miss(f"No consensus file for {date} and no database is configured.", source="none")
    try:
        return _from_table(ctx, date, track, race)
    except DatabaseUnavailable as e:
        return failure(f"No consensus file for {date}, and the database did not answer: {e}",
                       source="neon:consensus_scores")


SPEC = ToolSpec(
    name="get_consensus",
    description=(
        "Consensus intelligence for one race: which horses the tipster panel and web research "
        "named, with consensus score, vote share, mention counts, bucket spread and market "
        "alignment. Use for 'what was the consensus saying' and 'who did the tipsters like'."),
    input_schema={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Race date, YYYY-MM-DD."},
            "track": {"type": "string", "description": "Track name."},
            "race": {"type": "integer", "description": "Race number."},
        },
        "required": ["date", "track", "race"],
        "additionalProperties": False,
    },
    fn=get_consensus,
)
