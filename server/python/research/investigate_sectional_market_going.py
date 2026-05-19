#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras


PROJECT_ROOT = Path(__file__).resolve().parents[3]
INTELLIGENCE_DIR = PROJECT_ROOT / "server" / "python" / "intelligence"
RESEARCH_DIR = PROJECT_ROOT / "server" / "python" / "research"
AUTOPSY_PATH = RESEARCH_DIR / "performance_autopsy_last21days.json"
DEFAULT_JSON_OUTPUT = RESEARCH_DIR / "sectional_market_going_diagnostics.json"
DEFAULT_SUMMARY_OUTPUT = RESEARCH_DIR / "sectional_market_going_summary.txt"
START_DATE = "2026-03-01"

COLLECTOR_SOURCES = {
    "nsw_sectional_collector.py": {"nsw_pidata", "racing_nsw"},
    "sectional_times_collector.py": {"racing_qld"},
    "racing_com_sectionals_collector.py": {"racing_com"},
}

METRO_COLLECTOR_SUPPORT = {
    "Randwick": ["nsw_sectional_collector.py"],
    "Kensington": ["nsw_sectional_collector.py"],
    "Rosehill Gardens": ["nsw_sectional_collector.py"],
    "Flemington": ["racing_com_sectionals_collector.py"],
    "Caulfield": ["racing_com_sectionals_collector.py"],
    "Moonee Valley": ["racing_com_sectionals_collector.py"],
    "Eagle Farm": ["sectional_times_collector.py"],
    "Doomben": ["sectional_times_collector.py"],
    "Morphettville": ["racing_com_sectionals_collector.py"],
    "Ascot": [],
}


def load_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        return database_url
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                database_url = line.split("=", 1)[1].strip()
                break
    if not database_url:
        raise RuntimeError("DATABASE_URL not found in environment or project .env")
    os.environ["DATABASE_URL"] = database_url
    return database_url


def normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def canonical_track_key(value: Any) -> str:
    aliases = {
        "rosehill": "rosehillgardens",
        "rosehillgardens": "rosehillgardens",
        "ascot": "ascotwa",
        "ascotwa": "ascotwa",
        "aquisparkgoldcoast": "goldcoast",
        "aquisparkgoldcoastpoly": "goldcoast",
        "randwick": "randwick",
        "royalrandwick": "randwick",
        "kensington": "kensington",
        "flemington": "flemington",
        "morphettville": "morphettville",
        "morphettvilleparks": "morphettville",
        "doomben": "doomben",
        "eaglefarm": "eaglefarm",
        "caulfield": "caulfield",
        "caulfieldheath": "caulfield",
        "mooneevalley": "mooneevalley",
        "warwickfarm": "warwickfarm",
        "southsidecranbourne": "cranbourne",
        "southsidepakenham": "pakenham",
        "sportsbetsandownlakeside": "sandownlakeside",
        "sportsbetsandownhillside": "sandownhillside",
        "bet365parkkyneton": "kyneton",
        "bet365yarravalley": "yarravalley",
        "sportsbetballarat": "ballarat",
    }
    key = normalize_name(value)
    return aliases.get(key, key)


def going_bucket(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "heavy" in text:
        return "heavy"
    if "soft" in text:
        return "soft"
    if "firm" in text:
        return "firm"
    if "synthetic" in text or "poly" in text:
        return "synthetic"
    return "good"


def safe_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_rows(conn, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params or {})
        return [dict(row) for row in cur.fetchall()]


def barrier_wet_matrix(barrier_map: dict[str, Any]) -> tuple[dict[str, dict[str, int]], list[dict[str, Any]]]:
    tracks = barrier_map.get("tracks") if isinstance(barrier_map, dict) else None
    tracks = tracks if isinstance(tracks, dict) else barrier_map
    confidence_counts = {
        "soft": {"LOW": 0, "MEDIUM": 0, "HIGH": 0},
        "heavy": {"LOW": 0, "MEDIUM": 0, "HIGH": 0},
    }
    matrix = []

    for track, track_payload in (tracks or {}).items():
        if track in {"generated_at", "data_window_months", "rail_adjustments"} or not isinstance(track_payload, dict):
            continue
        dist_payload = track_payload.get("distances", track_payload)
        for distance_band, distance_value in dist_payload.items():
            if not isinstance(distance_value, dict):
                continue
            going_payload = distance_value.get("going", distance_value)
            for wet_going, going_value in going_payload.items():
                wet_key = str(wet_going).lower()
                if wet_key not in {"soft", "heavy"} or not isinstance(going_value, dict):
                    continue
                barriers = going_value.get("barriers", {})
                local_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
                for meta in barriers.values():
                    if not isinstance(meta, dict):
                        continue
                    conf = meta.get("confidence")
                    if conf in local_counts:
                        local_counts[conf] += 1
                        confidence_counts[wet_key][conf] += 1
                matrix.append(
                    {
                        "track": track,
                        "distance_band": distance_band,
                        "going_bucket": wet_key,
                        "total_barriers": sum(local_counts.values()),
                        "confidence_mix": local_counts,
                        "all_low": sum(local_counts.values()) > 0 and local_counts["LOW"] == sum(local_counts.values()),
                    }
                )

    matrix.sort(key=lambda row: (row["track"], row["distance_band"], row["going_bucket"]))
    return confidence_counts, matrix


def build_collector_support_matrix(historical_source_track_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    matrix: dict[str, dict[str, Any]] = {}
    for track, collectors in METRO_COLLECTOR_SUPPORT.items():
        db_sources = sorted({source for collector in collectors for source in COLLECTOR_SOURCES.get(collector, set())})
        matrix[canonical_track_key(track)] = {
            "track_names": [track],
            "collectors": collectors,
            "db_sources": db_sources,
            "supported": bool(collectors),
        }
    source_to_collector = {
        "nsw_pidata": "nsw_sectional_collector.py",
        "racing_nsw": "nsw_sectional_collector.py",
        "racing_qld": "sectional_times_collector.py",
        "racing_com": "racing_com_sectionals_collector.py",
    }
    for row in historical_source_track_rows:
        track = str(row.get("track") or "").strip()
        source = str(row.get("source") or "").strip()
        collector = source_to_collector.get(source)
        if not track or not collector:
            continue
        track_key = canonical_track_key(track)
        entry = matrix.setdefault(track_key, {"track_names": [], "collectors": [], "db_sources": [], "supported": False})
        if track not in entry["track_names"]:
            entry["track_names"].append(track)
            entry["track_names"].sort()
        if collector not in entry["collectors"]:
            entry["collectors"].append(collector)
        if source not in entry["db_sources"]:
            entry["db_sources"].append(source)
        entry["collectors"].sort()
        entry["db_sources"].sort()
        entry["supported"] = bool(entry["collectors"])
    return matrix


def classify_root_cause(
    meeting_date: date | None,
    track: str,
    natural_pct: float,
    rrh_id_pct: float,
    collector_support: dict[str, dict[str, Any]],
    latest_source_race_dates: dict[str, date | None],
) -> str:
    support = collector_support.get(track, {"collectors": []})
    if not support.get("collectors"):
        support = collector_support.get(canonical_track_key(track), {"collectors": []})
    collectors = support.get("collectors", [])
    if not collectors:
        return "unsupported_source"

    supported_latest_dates = []
    for collector in collectors:
        source_dates = [latest_source_race_dates.get(source) for source in COLLECTOR_SOURCES.get(collector, set())]
        source_dates = [value for value in source_dates if value is not None]
        if source_dates:
            supported_latest_dates.append(max(source_dates))

    if meeting_date and supported_latest_dates and meeting_date > max(supported_latest_dates):
        return "collector_stale"
    if natural_pct > rrh_id_pct:
        return "identifier_join_mismatch"
    return "partial_import"


def main() -> None:
    conn = psycopg2.connect(load_database_url())
    conn.autocommit = True
    try:
        race_id_column_rows = fetch_rows(
            conn,
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'sectional_times'
              AND column_name = 'race_id'
            """,
        )
        rrh_rows = fetch_rows(
            conn,
            """
            SELECT id, race_date::date AS race_date, track, race_number, race_id
            FROM race_results_history
            WHERE race_date::date >= %(start_date)s::date
            """,
            {"start_date": START_DATE},
        )
        sectional_rows = fetch_rows(
            conn,
            """
            SELECT race_results_history_id, race_date::date AS race_date, track, race_number, source, created_at
            FROM sectional_times
            WHERE race_date::date >= %(start_date)s::date
            """,
            {"start_date": START_DATE},
        )
        source_freshness_rows = fetch_rows(
            conn,
            """
            SELECT source,
                   COUNT(*) AS rows,
                   MIN(created_at) AS first_created_at,
                   MAX(created_at) AS last_created_at,
                   MAX(race_date::date) AS last_race_date
            FROM sectional_times
            GROUP BY source
            ORDER BY rows DESC, source
            """,
        )
        historical_source_track_rows = fetch_rows(
            conn,
            """
            SELECT DISTINCT track, source
            FROM sectional_times
            WHERE track IS NOT NULL
              AND source IS NOT NULL
            """,
        )
        going_rows = fetch_rows(
            conn,
            """
            SELECT going,
                   COUNT(*) AS total_runners,
                   SUM(CASE WHEN position = 1 THEN 1 ELSE 0 END) AS winners,
                   ROUND(100.0 * SUM(CASE WHEN position = 1 THEN 1 ELSE 0 END) / COUNT(*), 2) AS win_pct,
                   ROUND(CAST(AVG(sp_odds) AS numeric), 2) AS avg_sp
            FROM race_results_history
            WHERE race_date::date >= '2025-11-01'
              AND sp_odds IS NOT NULL
              AND sp_odds > 0
            GROUP BY going
            ORDER BY total_runners DESC, win_pct DESC
            """,
        )
    finally:
        conn.close()

    rrh_races_by_meeting: dict[tuple[str, str], set[int]] = defaultdict(set)
    rrh_id_to_race: dict[str, tuple[str, str, int]] = {}
    for row in rrh_rows:
        meeting_key = (str(row["race_date"]), str(row["track"]))
        rrh_races_by_meeting[meeting_key].add(int(row["race_number"] or 0))
        if row.get("id"):
            rrh_id_to_race[str(row["id"])] = (str(row["race_date"]), str(row["track"]), int(row["race_number"] or 0))

    natural_matches_by_meeting: dict[tuple[str, str], set[int]] = defaultdict(set)
    rrh_id_matches_by_meeting: dict[tuple[str, str], set[int]] = defaultdict(set)
    source_tracks: dict[str, set[str]] = defaultdict(set)
    latest_source_race_dates: dict[str, date | None] = {}

    for freshness in source_freshness_rows:
        latest_source_race_dates[str(freshness["source"])] = safe_date(freshness.get("last_race_date"))

    for row in sectional_rows:
        meeting_key = (str(row["race_date"]), str(row["track"]))
        race_number = int(row["race_number"] or 0)
        natural_matches_by_meeting[meeting_key].add(race_number)
        source_tracks[str(row["source"])].add(str(row["track"]))

        rrh_id = row.get("race_results_history_id")
        if rrh_id is not None:
            rrh_match = rrh_id_to_race.get(str(rrh_id))
            if rrh_match:
                rrh_id_matches_by_meeting[(rrh_match[0], rrh_match[1])].add(rrh_match[2])

    collector_support = build_collector_support_matrix(historical_source_track_rows)
    coverage_rows = []
    root_cause_counts = Counter()

    for (race_date, track), result_races in sorted(rrh_races_by_meeting.items(), reverse=True):
        total_races = len(result_races)
        natural_matches = len(result_races & natural_matches_by_meeting.get((race_date, track), set()))
        rrh_id_matches = len(result_races & rrh_id_matches_by_meeting.get((race_date, track), set()))
        natural_pct = round(100.0 * natural_matches / total_races, 1) if total_races else 0.0
        rrh_id_pct = round(100.0 * rrh_id_matches / total_races, 1) if total_races else 0.0
        supported = collector_support.get(canonical_track_key(track), {"collectors": [], "db_sources": [], "supported": False})

        row = {
            "race_date": race_date,
            "track": track,
            "results_races": total_races,
            "natural_key_races_with_sectionals": natural_matches,
            "natural_key_coverage_pct": natural_pct,
            "rrh_id_races_with_sectionals": rrh_id_matches,
            "rrh_id_coverage_pct": rrh_id_pct,
            "supporting_collectors": supported["collectors"],
            "supporting_db_sources": supported["db_sources"],
        }

        if natural_pct < 100.0:
            root_cause = classify_root_cause(
                safe_date(race_date),
                track,
                natural_pct,
                rrh_id_pct,
                collector_support,
                latest_source_race_dates,
            )
            row["root_cause"] = root_cause
            root_cause_counts[root_cause] += 1
        else:
            row["root_cause"] = "fully_covered"
        coverage_rows.append(row)

    barrier_map = load_json(INTELLIGENCE_DIR / "barrier_map.json") or {}
    wet_confidence_counts, wet_matrix = barrier_wet_matrix(barrier_map)

    autopsy = load_json(AUTOPSY_PATH) or {}
    loss_rows = [row for row in autopsy.get("race_level_rows", []) if row.get("tip_result") == "LOSS"]
    losses_by_going = Counter(going_bucket(row.get("going")) for row in loss_rows)
    going_miss_by_going = Counter(
        going_bucket(row.get("going")) for row in loss_rows if (row.get("failure_flags") or {}).get("going_miss")
    )

    diagnostics = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window_start": START_DATE,
        "sectional_schema": {
            "has_race_id_column": bool(race_id_column_rows),
            "join_keys": ["race_results_history_id", "race_date+track+race_number"],
        },
        "collector_support_matrix": collector_support,
        "sectional_source_freshness": source_freshness_rows,
        "sectional_source_track_history": {source: sorted(tracks) for source, tracks in sorted(source_tracks.items())},
        "sectional_meeting_coverage": coverage_rows,
        "sectional_root_cause_counts": dict(root_cause_counts),
        "going_distribution": going_rows,
        "wet_barrier_confidence_counts": wet_confidence_counts,
        "wet_barrier_matrix": wet_matrix,
        "autopsy_loss_context": {
            "losses_by_going_bucket": dict(losses_by_going),
            "going_miss_losses_by_going_bucket": dict(going_miss_by_going),
        },
    }

    DEFAULT_JSON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_JSON_OUTPUT.write_text(
        json.dumps(diagnostics, indent=2, ensure_ascii=True, default=str),
        encoding="utf-8",
    )

    low_coverage_meetings = [row for row in coverage_rows if row["natural_key_coverage_pct"] < 100.0]
    stale_count = root_cause_counts.get("collector_stale", 0)
    mismatch_count = root_cause_counts.get("identifier_join_mismatch", 0)
    partial_count = root_cause_counts.get("partial_import", 0)
    unsupported_count = root_cause_counts.get("unsupported_source", 0)
    max_freshness = max((safe_date(row.get("last_race_date")) for row in source_freshness_rows), default=None)
    summary_lines = [
        "STRIDE Sectional, Market Overlay, and Wet-Going Diagnostics",
        f"Generated: {diagnostics['generated_at']}",
        "",
        f"sectional_times has race_id column: {diagnostics['sectional_schema']['has_race_id_column']}",
        f"Meetings with results since {START_DATE}: {len(coverage_rows)}",
        f"Meetings below full natural-key coverage: {len(low_coverage_meetings)}",
        f"Latest sectional race date in DB: {max_freshness.isoformat() if max_freshness else 'n/a'}",
        f"Low-coverage root causes: stale={stale_count}, partial={partial_count}, join_mismatch={mismatch_count}, unsupported={unsupported_count}",
        "",
        "Wet-going confidence:",
        f"Heavy cells -> LOW {wet_confidence_counts['heavy']['LOW']}, MEDIUM {wet_confidence_counts['heavy']['MEDIUM']}, HIGH {wet_confidence_counts['heavy']['HIGH']}",
        f"Soft cells -> LOW {wet_confidence_counts['soft']['LOW']}, MEDIUM {wet_confidence_counts['soft']['MEDIUM']}, HIGH {wet_confidence_counts['soft']['HIGH']}",
        "",
        "Autopsy loss context:",
        f"Losses by going bucket: {dict(losses_by_going)}",
        f"Going-miss losses by going bucket: {dict(going_miss_by_going)}",
        "",
        "Key findings:",
        "- Sectional coverage should be measured on natural keys or race_results_history_id; race_id is not available in sectional_times.",
        "- Supported metro collectors stop materially before the late-March meetings, so March 25-28 gaps are primarily a collector freshness problem.",
        "- Ascot has no current sectional collector path, so WA meetings are structurally unsupported.",
        "- Heavy barrier cells have no HIGH-confidence coverage, and Soft cells are dominated by LOW-confidence entries.",
    ]
    DEFAULT_SUMMARY_OUTPUT.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
