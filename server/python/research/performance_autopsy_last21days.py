#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import psycopg2
import psycopg2.extras


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RACECARDS_DIR = PROJECT_ROOT / "racecards"
INTELLIGENCE_DIR = PROJECT_ROOT / "server" / "python" / "intelligence"
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "server" / "python" / "research" / "performance_autopsy_last21days.json"
DEFAULT_SUMMARY_OUTPUT = PROJECT_ROOT / "server" / "python" / "research" / "performance_autopsy_summary.txt"
DEFAULT_QUERY_LOG = PROJECT_ROOT / "server" / "python" / "research" / "performance_autopsy_query_log.jsonl"


def load_database_url() -> str:
    env_path = PROJECT_ROOT / ".env"
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        return database_url
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                database_url = line.split("=", 1)[1].strip()
                break
    if not database_url:
        raise RuntimeError("DATABASE_URL not found in environment or project .env")
    os.environ["DATABASE_URL"] = database_url
    return database_url


class QueryLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        *,
        name: str,
        query: str,
        params: Optional[dict[str, Any]] = None,
        row_count: Optional[int] = None,
        duration_ms: Optional[float] = None,
    ) -> None:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "name": name,
            "query": query.strip(),
            "params": params or {},
            "row_count": row_count,
            "duration_ms": round(duration_ms or 0.0, 2),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=True) + "\n")


class ResearchDB:
    def __init__(self, query_log_path: Path, statement_timeout_ms: int = 120000) -> None:
        self.database_url = load_database_url()
        self.query_logger = QueryLogger(query_log_path)
        self.statement_timeout_ms = statement_timeout_ms
        self._conn: Optional[psycopg2.extensions.connection] = None

    @property
    def conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.database_url)
            self._conn.autocommit = True
            with self._conn.cursor() as cur:
                cur.execute(f"SET statement_timeout TO {int(self.statement_timeout_ms)}")
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()

    def fetch_rows(self, name: str, query: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        start = time.time()
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params or {})
            rows = [dict(row) for row in cur.fetchall()]
        self.query_logger.log(
            name=name,
            query=query,
            params=params,
            row_count=len(rows),
            duration_ms=(time.time() - start) * 1000,
        )
        return rows


def normalize_name(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def canonical_track_key(value: Optional[str]) -> str:
    if not value:
        return ""
    text = normalize_name(value)
    aliases = {
        "rosehill": "rosehillgardens",
        "rosehillgardens": "rosehillgardens",
        "ascot": "ascotwa",
        "ascotwa": "ascotwa",
        "randwick": "randwick",
        "royalrandwick": "randwick",
        "kensington": "kensington",
        "flemington": "flemington",
        "morphettville": "morphettville",
        "doomben": "doomben",
        "eaglefarm": "eaglefarm",
        "caulfield": "caulfield",
        "mooneevalley": "mooneevalley",
    }
    return aliases.get(text, text)


def classify_distance(distance_value: Any) -> str:
    if distance_value is None:
        return "mile"
    if isinstance(distance_value, str):
        match = re.search(r"(\d+)", distance_value)
        distance_m = int(match.group(1)) if match else 0
    else:
        try:
            distance_m = int(distance_value)
        except (TypeError, ValueError):
            distance_m = 0
    if distance_m < 1200:
        return "sprint"
    if distance_m <= 1600:
        return "mile"
    return "staying"


def normalize_going_key(going: Optional[str]) -> str:
    text = str(going or "").strip().lower()
    if "heavy" in text:
        return "heavy"
    if "soft" in text:
        return "soft"
    if "firm" in text:
        return "firm"
    if "synthetic" in text or "poly" in text:
        return "synthetic"
    return "good"


def parse_going_flags(going: Optional[str]) -> tuple[bool, bool]:
    text = str(going or "").strip().lower()
    if "heavy" in text:
        return True, False
    if "soft" not in text:
        return False, False
    match = re.search(r"soft\s*([0-9]+)", text)
    if match:
        return int(match.group(1)) >= 6, False
    return False, True


def overlay_bracket(price: Optional[float]) -> str:
    if price is None or price <= 1.0:
        return "unknown"
    if price <= 3.0:
        return "short"
    if price <= 8.0:
        return "mid"
    if price <= 20.0:
        return "long"
    return "outsider"


def safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def make_race_key(track: Optional[str], race_date: Optional[str], race_number: Any) -> tuple[str, str, int]:
    return (
        canonical_track_key(track),
        str(race_date or "")[:10],
        int(race_number or 0),
    )


def make_runner_key(track: Optional[str], race_date: Optional[str], race_number: Any, horse_name: Optional[str]) -> tuple[str, str, int, str]:
    return (
        canonical_track_key(track),
        str(race_date or "")[:10],
        int(race_number or 0),
        normalize_name(horse_name),
    )


def match_track_dict_key(track: Optional[str], payload: dict[str, Any]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    target = canonical_track_key(track)
    normalized_map = {canonical_track_key(key): key for key in payload.keys() if isinstance(key, str)}
    if target in normalized_map:
        return normalized_map[target]
    raw_target = normalize_name(track)
    for key in payload.keys():
        if not isinstance(key, str):
            continue
        key_norm = normalize_name(key)
        if raw_target and (raw_target in key_norm or key_norm in raw_target):
            return key
    return None


def classify_franking_record(data: dict[str, Any]) -> tuple[str, float]:
    score = safe_float(data.get("franking_score")) or 50.0
    confidence = safe_float(data.get("franking_confidence")) or 0.1
    anti = bool(data.get("anti_franked"))
    if anti:
        return "UNFRANKED", 0.6
    if confidence < 0.15:
        return "PENDING", 0.8
    if score >= 70 and confidence >= 0.45:
        return "DEEP_FRANKED", 1.2
    if score >= 55 and confidence >= 0.3:
        return "FRANKED", 1.1
    if score >= 35:
        return "PARTIAL", 1.0
    return "UNFRANKED", 0.7


def select_final_pick(race: dict[str, Any]) -> Optional[dict[str, Any]]:
    for key in ("bet_pick", "coverage_pick", "primary_pick"):
        pick = race.get(key)
        if isinstance(pick, dict) and pick:
            return pick
    top_picks = race.get("top_picks") or []
    if isinstance(top_picks, list) and top_picks:
        first = top_picks[0]
        if isinstance(first, dict):
            return first
    return None


def extract_tip_horse_id(pick: dict[str, Any]) -> Optional[str]:
    for key in ("horse_id", "horseId"):
        if pick.get(key):
            return str(pick[key])
    mc_data = pick.get("_mc_data")
    if isinstance(mc_data, dict):
        for key in ("horse_id", "horseId", "id"):
            if mc_data.get(key):
                return str(mc_data[key])
    luckless = pick.get("luckless_json")
    if isinstance(luckless, dict) and luckless.get("horse_id"):
        return str(luckless["horse_id"])
    return None


def load_canonical_tip_files(date_from: str, date_to: str) -> list[Path]:
    tip_files = []
    pattern = re.compile(r"tips_(\d{4}-\d{2}-\d{2})\.json$")
    for path in sorted(RACECARDS_DIR.glob("tips_*.json")):
        match = pattern.fullmatch(path.name)
        if not match:
            continue
        file_date = match.group(1)
        if date_from <= file_date <= date_to:
            tip_files.append(path)
    return tip_files


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_intelligence() -> dict[str, Any]:
    barrier_map = load_json(INTELLIGENCE_DIR / "barrier_map.json")
    market_overlays = load_json(INTELLIGENCE_DIR / "market_overlays.json")
    form_franking = load_json(INTELLIGENCE_DIR / "form_franking.json")
    prep_cycles = load_json(INTELLIGENCE_DIR / "prep_cycles.json")

    franking_by_name = {}
    for key, value in form_franking.items():
        if not isinstance(value, dict):
            continue
        if isinstance(key, str):
            franking_by_name[normalize_name(value.get("horse_name") or key)] = value

    prep_by_name = {}
    for key, value in prep_cycles.items():
        if not isinstance(value, dict):
            continue
        if isinstance(key, str):
            prep_by_name[normalize_name(value.get("horse_name") or key)] = value

    return {
        "barrier_map": barrier_map,
        "market_overlays": market_overlays,
        "form_franking": form_franking,
        "prep_cycles": prep_cycles,
        "_franking_by_name": franking_by_name,
        "_prep_by_name": prep_by_name,
    }


def fetch_base_data(db: ResearchDB, date_from: str, date_to: str) -> dict[str, Any]:
    rrh = db.fetch_rows(
        "race_results_history_window",
        """
        SELECT horse_id, horse_name, race_id, track, race_date, race_number, race_name,
               distance_m, race_class, going, position, barrier, sp_odds, field_size
        FROM race_results_history
        WHERE race_date BETWEEN %(date_from)s AND %(date_to)s
        ORDER BY race_date, track, race_number, position NULLS LAST, horse_name
        """,
        {"date_from": date_from, "date_to": date_to},
    )
    sectionals = db.fetch_rows(
        "sectional_times_window",
        """
        SELECT race_results_history_id, track, race_date, race_number, horse_name
        FROM sectional_times
        WHERE race_date BETWEEN %(date_from)s AND %(date_to)s
        """,
        {"date_from": date_from, "date_to": date_to},
    )
    betfair_map = db.fetch_rows(
        "betfair_market_runner_map_window",
        """
        SELECT market_id, selection_id, meeting_date, track, race_number, horse_name
        FROM betfair_market_runner_map
        WHERE meeting_date BETWEEN %(date_from)s::date AND %(date_to)s::date
        """,
        {"date_from": date_from, "date_to": date_to},
    )
    labeled_market = db.fetch_rows(
        "betfair_labeled_training_view_window",
        """
        SELECT market_id, selection_id, meeting_date, track, race_number, horse_name,
               market_odds, minutes_to_jump, is_t10_window, capture_timestamp
        FROM betfair_labeled_training_view
        WHERE meeting_date BETWEEN %(date_from)s::date AND %(date_to)s::date
        """,
        {"date_from": date_from, "date_to": date_to},
    )
    return {
        "rrh": rrh,
        "sectionals": sectionals,
        "betfair_map": betfair_map,
        "labeled_market": labeled_market,
    }


def build_market_lookup(betfair_map_rows: list[dict[str, Any]], labeled_rows: list[dict[str, Any]]) -> dict[tuple[str, str, int, str], dict[str, Any]]:
    labeled_by_runner_id: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    labeled_by_join_key: dict[tuple[str, str, int, str], list[dict[str, Any]]] = defaultdict(list)

    for row in labeled_rows:
        market_id = str(row.get("market_id") or "")
        selection_id = str(row.get("selection_id") or "")
        if market_id and selection_id:
            labeled_by_runner_id[(market_id, selection_id)].append(row)
        join_key = make_runner_key(row.get("track"), row.get("meeting_date"), row.get("race_number"), row.get("horse_name"))
        labeled_by_join_key[join_key].append(row)

    def choose_best(rows: Iterable[dict[str, Any]]) -> Optional[dict[str, Any]]:
        candidates = [row for row in rows if safe_float(row.get("market_odds")) and safe_float(row.get("market_odds")) > 1.0]
        if not candidates:
            return None
        candidates.sort(
            key=lambda row: (
                0 if row.get("is_t10_window") else 1,
                abs(safe_float(row.get("minutes_to_jump")) or 999.0),
                str(row.get("capture_timestamp") or ""),
            )
        )
        return candidates[0]

    lookup: dict[tuple[str, str, int, str], dict[str, Any]] = {}

    for row in betfair_map_rows:
        runner_id = (str(row.get("market_id") or ""), str(row.get("selection_id") or ""))
        best = choose_best(labeled_by_runner_id.get(runner_id, []))
        if not best:
            continue
        join_key = make_runner_key(row.get("track"), row.get("meeting_date"), row.get("race_number"), row.get("horse_name"))
        lookup[join_key] = {
            "market_odds": safe_float(best.get("market_odds")),
            "minutes_to_jump": safe_float(best.get("minutes_to_jump")),
            "is_t10_window": bool(best.get("is_t10_window")),
            "source": "betfair_labeled_training_view",
        }

    for join_key, rows in labeled_by_join_key.items():
        if join_key in lookup:
            continue
        best = choose_best(rows)
        if not best:
            continue
        lookup[join_key] = {
            "market_odds": safe_float(best.get("market_odds")),
            "minutes_to_jump": safe_float(best.get("minutes_to_jump")),
            "is_t10_window": bool(best.get("is_t10_window")),
            "source": "betfair_labeled_training_view_unmapped",
        }

    return lookup


def fetch_franking_fallback(
    db: ResearchDB,
    horse_ids: set[str],
    horse_names: set[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = db.fetch_rows(
        "franking_scores_lookup",
        """
        SELECT horse_id, horse_name, global_elo, franking_score, franking_confidence,
               anti_franked, field_strength_avg, form_quality_trend,
               best_adjusted_margin, collateral_advantage
        FROM franking_scores
        WHERE (
            horse_id = ANY(%(horse_ids)s)
            OR lower(regexp_replace(coalesce(horse_name, ''), '[^a-z0-9]+', '', 'g')) = ANY(%(horse_names)s)
        )
        """,
        {
            "horse_ids": sorted(horse_ids) or [""],
            "horse_names": sorted(horse_names) or [""],
        },
    )
    by_id = {}
    by_name = {}
    for row in rows:
        horse_id = row.get("horse_id")
        horse_name = row.get("horse_name")
        if horse_id:
            by_id[str(horse_id)] = row
        if horse_name:
            by_name[normalize_name(horse_name)] = row
    return by_id, by_name


def derive_prep_fallback(winner_name: str, race_date: str, conn) -> dict[str, Any]:
    from fitness_peak import detect_current_prep, analyze_form_trajectory

    prep_data = detect_current_prep(winner_name, race_date, conn)
    prior_positions = prep_data.get("positions_this_prep", []) or []
    prior_margins = prep_data.get("margins_this_prep", []) or []
    form = analyze_form_trajectory(prior_positions, prior_margins)
    if not prior_positions and (prep_data.get("days_since_last_run", 999) >= 60):
        prep_position = "first_up"
    else:
        run_number = len(prior_positions) + 1
        labels = {1: "first_up", 2: "second_up", 3: "third_up", 4: "fourth_up", 5: "fifth_up"}
        prep_position = labels.get(run_number, f"{run_number}th_up")
    trajectory_map = {
        "improving": "IMPROVING",
        "declining": "DECLINING",
        "peaked": "DECLINING",
        "steady": "FLAT",
    }
    return {
        "horse_name": winner_name,
        "prep_position": prep_position,
        "trajectory": trajectory_map.get(form.get("trajectory", "steady"), "FLAT"),
        "source": "fitness_peak_fallback",
    }


def summarize_findings(
    rows: list[dict[str, Any]],
    failure_rank: list[dict[str, Any]],
    sectional_coverage: dict[str, Any],
    betfair_coverage: dict[str, Any],
    notes: dict[str, list[dict[str, Any]]],
) -> str:
    total = len(rows)
    settled_rows = [row for row in rows if row.get("tip_result") in {"WIN", "LOSS"}]
    settled = len(settled_rows)
    wins = sum(1 for row in settled_rows if row.get("tip_result") == "WIN")
    losses = sum(1 for row in settled_rows if row.get("tip_result") == "LOSS")
    unsettled = total - settled
    strike_rate = (wins / settled * 100.0) if settled else 0.0

    lines = [
        "STRIDE Last-21-Days Performance Autopsy",
        f"Window: {rows[0]['date']} to {rows[-1]['date']}" if rows else "Window: n/a",
        "",
        f"Canonical tipped races loaded: {total}",
        f"Settled races matched to results: {settled}",
        f"Unsettled or unmatched races: {unsettled}",
        f"Wins: {wins}",
        f"Losses: {losses}",
        f"Settled strike rate: {strike_rate:.1f}%",
        "",
        "Failure categories (ranked):",
    ]
    if failure_rank:
        for item in failure_rank:
            lines.append(f"- {item['category']}: {item['count']} ({item['loss_pct']:.1f}% of losing tips)")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            f"Sectional race coverage: {sectional_coverage['race_level_pct']:.1f}% "
            f"({'FAIL' if sectional_coverage['sectional_pipeline_failure'] else 'OK'})",
            f"Betfair runner coverage: {betfair_coverage['overall_pct']:.1f}% "
            f"({'FAIL' if betfair_coverage['market_mapping_failure'] else 'OK'})",
            "",
            "Key findings:",
        ]
    )

    insights = []
    if failure_rank:
        insights.append(
            f"The most common losing-tip pattern was {failure_rank[0]['category']} "
            f"at {failure_rank[0]['count']} races."
        )
    if sectional_coverage["sectional_pipeline_failure"]:
        insights.append(
            f"Sectional coverage was only {sectional_coverage['race_level_pct']:.1f}% of tipped races, "
            "which points to a live data quality problem in the sectional signal."
        )
    if betfair_coverage["market_mapping_failure"]:
        insights.append(
            f"Betfair mapping coverage was only {betfair_coverage['overall_pct']:.1f}% of runners, "
            "so the market-calibration layer was effectively running blind."
        )
    missing_barrier = len(notes["missing_barrier_map_cells"])
    if missing_barrier:
        insights.append(
            f"{missing_barrier} losing races could not be scored against the current barrier map because the track/distance/going/barrier cell was missing."
        )
    missing_franking = len(notes["missing_franking_entries"])
    if missing_franking:
        insights.append(
            f"{missing_franking} winners were not present in the current daily form-franking snapshot and had to rely on persisted DB franking data."
        )

    for insight in insights[:5]:
        lines.append(f"- {insight}")

    unmatched_races = len(notes["unmatched_races"])
    unmatched_runners = len(notes["unmatched_tipped_runners"])
    if unmatched_races or unmatched_runners:
        lines.extend(
            [
                "",
                "Data quality notes:",
                f"- Unmatched races: {unmatched_races}",
                f"- Unmatched tipped runners: {unmatched_runners}",
            ]
        )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="STRIDE last-21-days performance autopsy")
    parser.add_argument("--date-from", default="2026-03-08")
    parser.add_argument("--date-to", default="2026-03-29")
    parser.add_argument("--output-json", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--output-summary", default=str(DEFAULT_SUMMARY_OUTPUT))
    parser.add_argument("--query-log", default=str(DEFAULT_QUERY_LOG))
    args = parser.parse_args()

    output_json = Path(args.output_json)
    output_summary = Path(args.output_summary)
    query_log = Path(args.query_log)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_summary.parent.mkdir(parents=True, exist_ok=True)
    if query_log.exists():
        query_log.unlink()

    intelligence = load_intelligence()
    tip_files = load_canonical_tip_files(args.date_from, args.date_to)
    if not tip_files:
        raise RuntimeError("No canonical tip files found in the requested window")

    tip_rows: list[dict[str, Any]] = []
    for tip_file in tip_files:
        payload = load_json(tip_file)
        race_date = payload.get("date") or tip_file.stem.replace("tips_", "")
        for race in payload.get("races", []):
            final_pick = select_final_pick(race)
            if not final_pick:
                continue
            tip_rows.append(
                {
                    "source_tip_file": tip_file.name,
                    "date": str(race_date)[:10],
                    "track": race.get("track"),
                    "track_key": canonical_track_key(race.get("track")),
                    "race_number": int(race.get("race_number") or 0),
                    "race_name": race.get("race_name"),
                    "distance": race.get("distance"),
                    "distance_band": classify_distance(race.get("distance")),
                    "going": race.get("going"),
                    "going_key": normalize_going_key(race.get("going")),
                    "race_class": race.get("race_class"),
                    "field_size": race.get("field_size"),
                    "tipped_horse_name": final_pick.get("horse"),
                    "tipped_horse_name_norm": normalize_name(final_pick.get("horse")),
                    "tipped_horse_id": extract_tip_horse_id(final_pick),
                    "tipped_horse_barrier": final_pick.get("barrier"),
                    "edge_pct": safe_float(final_pick.get("edge_pct")),
                    "selection_origin": final_pick.get("selection_origin"),
                    "should_bet": final_pick.get("should_bet"),
                }
            )

    tip_rows.sort(key=lambda row: (row["date"], row["track_key"], row["race_number"]))

    db = ResearchDB(query_log)
    try:
        base = fetch_base_data(db, args.date_from, args.date_to)
    finally:
        db.close()

    rrh_by_race: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    rrh_by_race_runner: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for row in base["rrh"]:
        race_key = make_race_key(row.get("track"), row.get("race_date"), row.get("race_number"))
        rrh_by_race[race_key].append(row)
        rrh_by_race_runner[make_runner_key(row.get("track"), row.get("race_date"), row.get("race_number"), row.get("horse_name"))] = row

    sectionals_race_keys = set()
    sectionals_runner_keys = set()
    for row in base["sectionals"]:
        sectionals_race_keys.add(make_race_key(row.get("track"), row.get("race_date"), row.get("race_number")))
        sectionals_runner_keys.add(make_runner_key(row.get("track"), row.get("race_date"), row.get("race_number"), row.get("horse_name")))

    market_lookup = build_market_lookup(base["betfair_map"], base["labeled_market"])

    winner_horse_ids: set[str] = set()
    winner_name_norms: set[str] = set()
    notes: dict[str, list[dict[str, Any]]] = {
        "unmatched_races": [],
        "unmatched_tipped_runners": [],
        "missing_barrier_map_cells": [],
        "missing_franking_entries": [],
        "missing_prep_entries": [],
    }

    race_level_rows: list[dict[str, Any]] = []

    for tip in tip_rows:
        race_key = make_race_key(tip["track"], tip["date"], tip["race_number"])
        race_results = rrh_by_race.get(race_key, [])
        if not race_results:
            row = dict(tip)
            row.update(
                {
                    "matched_race": False,
                    "matched_tipped_runner": False,
                    "tip_correct": None,
                    "tip_result": "UNMATCHED_RACE",
                }
            )
            notes["unmatched_races"].append(
                {"date": tip["date"], "track": tip["track"], "race_number": tip["race_number"], "source_tip_file": tip["source_tip_file"]}
            )
            race_level_rows.append(row)
            continue

        winner_row = next((row for row in race_results if int(row.get("position") or 0) == 1), None)
        tipped_row = None
        if tip["tipped_horse_id"]:
            for candidate in race_results:
                if str(candidate.get("horse_id") or "") == str(tip["tipped_horse_id"]):
                    tipped_row = candidate
                    break
        if tipped_row is None:
            tipped_row = rrh_by_race_runner.get(
                make_runner_key(tip["track"], tip["date"], tip["race_number"], tip["tipped_horse_name"])
            )

        if winner_row:
            if winner_row.get("horse_id"):
                winner_horse_ids.add(str(winner_row["horse_id"]))
            winner_name_norms.add(normalize_name(winner_row.get("horse_name")))

        if tipped_row is None:
            notes["unmatched_tipped_runners"].append(
                {
                    "date": tip["date"],
                    "track": tip["track"],
                    "race_number": tip["race_number"],
                    "tipped_horse_name": tip["tipped_horse_name"],
                }
            )

        tipped_market = market_lookup.get(make_runner_key(tip["track"], tip["date"], tip["race_number"], tip["tipped_horse_name"]))
        winner_market = None
        if winner_row:
            winner_market = market_lookup.get(
                make_runner_key(tip["track"], tip["date"], tip["race_number"], winner_row.get("horse_name"))
            )

        row = dict(tip)
        row.update(
            {
                "matched_race": True,
                "matched_tipped_runner": tipped_row is not None,
                "race_id": winner_row.get("race_id") if winner_row else (tipped_row.get("race_id") if tipped_row else None),
                "actual_winner_name": winner_row.get("horse_name") if winner_row else None,
                "actual_winner_id": winner_row.get("horse_id") if winner_row else None,
                "actual_winner_barrier": winner_row.get("barrier") if winner_row else None,
                "tipped_horse_fallback_sp": safe_float(tipped_row.get("sp_odds")) if tipped_row else None,
                "actual_winner_fallback_sp": safe_float(winner_row.get("sp_odds")) if winner_row else None,
                "tipped_horse_betfair_price": tipped_market.get("market_odds") if tipped_market else None,
                "actual_winner_betfair_price": winner_market.get("market_odds") if winner_market else None,
                "tip_correct": bool(winner_row and normalize_name(winner_row.get("horse_name")) == tip["tipped_horse_name_norm"]),
                "tip_result": "WIN" if winner_row and normalize_name(winner_row.get("horse_name")) == tip["tipped_horse_name_norm"] else "LOSS",
            }
        )
        race_level_rows.append(row)

    db = ResearchDB(query_log)
    try:
        franking_by_id, franking_by_name = fetch_franking_fallback(db, winner_horse_ids, winner_name_norms)
    finally:
        db.close()

    prep_json = intelligence["prep_cycles"]
    prep_by_name = intelligence["_prep_by_name"]
    franking_json = intelligence["form_franking"]
    franking_by_name_json = intelligence["_franking_by_name"]

    matched_race_rows = [row for row in race_level_rows if row.get("matched_race")]
    sectional_race_hits = 0
    sectional_runner_total = 0
    sectional_runner_hits = 0
    betfair_runner_total = 0
    betfair_runner_hits = 0
    betfair_race_rollup = []

    for row in matched_race_rows:
        race_key = make_race_key(row["track"], row["date"], row["race_number"])
        race_results = rrh_by_race.get(race_key, [])
        if race_key in sectionals_race_keys:
            sectional_race_hits += 1
        race_runner_betfair_hits = 0
        for runner in race_results:
            sectional_runner_total += 1
            betfair_runner_total += 1
            runner_key = make_runner_key(row["track"], row["date"], row["race_number"], runner.get("horse_name"))
            if runner_key in sectionals_runner_keys:
                sectional_runner_hits += 1
            if market_lookup.get(runner_key) and safe_float(market_lookup[runner_key].get("market_odds")):
                race_runner_betfair_hits += 1
                betfair_runner_hits += 1
        betfair_race_rollup.append(
            {
                "date": row["date"],
                "track": row["track"],
                "race_number": row["race_number"],
                "runners": len(race_results),
                "mapped_betfair_runners": race_runner_betfair_hits,
                "coverage_pct": round((race_runner_betfair_hits / len(race_results) * 100.0), 2) if race_results else 0.0,
            }
        )

    sectional_coverage = {
        "races_total": len(matched_race_rows),
        "races_with_sectionals": sectional_race_hits,
        "race_level_pct": round((sectional_race_hits / len(matched_race_rows) * 100.0), 2) if matched_race_rows else 0.0,
        "runner_total": sectional_runner_total,
        "runner_with_sectionals": sectional_runner_hits,
        "runner_level_pct": round((sectional_runner_hits / sectional_runner_total * 100.0), 2) if sectional_runner_total else 0.0,
    }
    sectional_coverage["sectional_pipeline_failure"] = sectional_coverage["race_level_pct"] < 70.0

    betfair_mapping_coverage = {
        "race_rollup": betfair_race_rollup,
        "runner_total": betfair_runner_total,
        "runner_with_betfair_price": betfair_runner_hits,
        "overall_pct": round((betfair_runner_hits / betfair_runner_total * 100.0), 2) if betfair_runner_total else 0.0,
    }
    betfair_mapping_coverage["market_mapping_failure"] = betfair_mapping_coverage["overall_pct"] < 80.0

    barrier_map = intelligence["barrier_map"]
    market_overlays = intelligence["market_overlays"]

    # Prep fallback connection reused for speed.
    load_database_url()
    prep_conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        failure_counts = Counter()
        for row in race_level_rows:
            row["failure_flags"] = {}
            row["soft_unspecified"] = False
            row["franking_lookup_source"] = None
            row["prep_lookup_source"] = None

            if row.get("tip_result") != "LOSS" or not row.get("matched_race"):
                row["primary_failure_category_ranked"] = []
                continue

            race_results = rrh_by_race.get(make_race_key(row["track"], row["date"], row["race_number"]), [])
            runner_prices = []
            for runner in race_results:
                runner_key = make_runner_key(row["track"], row["date"], row["race_number"], runner.get("horse_name"))
                mapped_price = safe_float((market_lookup.get(runner_key) or {}).get("market_odds"))
                fallback_price = safe_float(runner.get("sp_odds"))
                price = mapped_price if mapped_price and mapped_price > 1.0 else fallback_price
                if price and price > 1.0:
                    runner_prices.append((normalize_name(runner.get("horse_name")), price))
            market_favourite_norm = min(runner_prices, key=lambda item: item[1])[0] if runner_prices else None

            row["failure_flags"]["wrong_favourite"] = bool(
                market_favourite_norm and market_favourite_norm == row["tipped_horse_name_norm"]
            )

            barrier_track_key = match_track_dict_key(row["track"], barrier_map)
            barrier_cell = None
            if barrier_track_key:
                distance_band = row["distance_band"]
                going_key = row["going_key"]
                barrier_value = row.get("actual_winner_barrier")
                if barrier_value is not None:
                    barrier_cell = (
                        barrier_map.get(barrier_track_key, {})
                        .get(distance_band, {})
                        .get(going_key, {})
                        .get("barriers", {})
                        .get(str(barrier_value))
                    )
            if barrier_cell is None:
                row["failure_flags"]["unknown_barrier_map"] = True
                row["failure_flags"]["barrier_blindspot"] = False
                row["failure_flags"]["going_miss"] = False
                notes["missing_barrier_map_cells"].append(
                    {
                        "date": row["date"],
                        "track": row["track"],
                        "race_number": row["race_number"],
                        "winner_barrier": row.get("actual_winner_barrier"),
                        "distance_band": row["distance_band"],
                        "going_key": row["going_key"],
                    }
                )
            else:
                row["failure_flags"]["unknown_barrier_map"] = False
                confidence = str(barrier_cell.get("confidence") or "").upper()
                edge_vs_random = safe_float(barrier_cell.get("edge_vs_random")) or 0.0
                row["failure_flags"]["barrier_blindspot"] = edge_vs_random < 0 and confidence != "LOW"
                row["failure_flags"]["going_miss"] = confidence == "LOW"

            franking_record = None
            winner_id = row.get("actual_winner_id")
            winner_name_norm = normalize_name(row.get("actual_winner_name"))
            if winner_id and winner_id in franking_json:
                franking_record = franking_json[winner_id]
                row["franking_lookup_source"] = "form_franking.json:id"
            elif winner_name_norm and winner_name_norm in franking_by_name_json:
                franking_record = franking_by_name_json[winner_name_norm]
                row["franking_lookup_source"] = "form_franking.json:name"
            elif winner_id and str(winner_id) in franking_by_id:
                franking_record = franking_by_id[str(winner_id)]
                row["franking_lookup_source"] = "franking_scores:horse_id"
            elif winner_name_norm and winner_name_norm in franking_by_name:
                franking_record = franking_by_name[winner_name_norm]
                row["franking_lookup_source"] = "franking_scores:horse_name"

            if franking_record is None:
                row["failure_flags"]["franking_miss"] = False
                notes["missing_franking_entries"].append(
                    {
                        "date": row["date"],
                        "track": row["track"],
                        "race_number": row["race_number"],
                        "winner": row.get("actual_winner_name"),
                    }
                )
            else:
                classification = franking_record.get("classification")
                if not classification:
                    classification, _ = classify_franking_record(franking_record)
                row["winner_franking_classification"] = classification
                row["failure_flags"]["franking_miss"] = classification in {"UNFRANKED", "PENDING"}

            prep_record = None
            if winner_id and winner_id in prep_json:
                prep_record = prep_json[winner_id]
                row["prep_lookup_source"] = "prep_cycles.json:id"
            elif winner_name_norm and winner_name_norm in prep_by_name:
                prep_record = prep_by_name[winner_name_norm]
                row["prep_lookup_source"] = "prep_cycles.json:name"
            else:
                try:
                    prep_record = derive_prep_fallback(row.get("actual_winner_name") or "", row["date"], prep_conn)
                    row["prep_lookup_source"] = prep_record.get("source")
                except Exception:
                    prep_record = None

            if prep_record is None:
                row["failure_flags"]["prep_cycle_miss"] = False
                notes["missing_prep_entries"].append(
                    {
                        "date": row["date"],
                        "track": row["track"],
                        "race_number": row["race_number"],
                        "winner": row.get("actual_winner_name"),
                    }
                )
            else:
                row["winner_prep_position"] = prep_record.get("prep_position")
                row["winner_prep_trajectory"] = prep_record.get("trajectory")
                row["failure_flags"]["prep_cycle_miss"] = (
                    prep_record.get("prep_position") == "first_up"
                    or prep_record.get("trajectory") == "DECLINING"
                )

            track_key = match_track_dict_key(row["track"], market_overlays)
            winner_price_for_bracket = row.get("actual_winner_betfair_price")
            if not winner_price_for_bracket:
                winner_price_for_bracket = row.get("actual_winner_fallback_sp")
            row["winner_price_bracket"] = overlay_bracket(winner_price_for_bracket)
            if track_key and row["winner_price_bracket"] != "unknown":
                going_bucket = row["going_key"]
                row["failure_flags"]["price_range_miss"] = (
                    row["winner_price_bracket"] not in market_overlays.get(track_key, {}).get(going_bucket, {})
                )
            else:
                row["failure_flags"]["price_range_miss"] = True

            heavy_soft_flag, soft_unspecified = parse_going_flags(row["going"])
            row["failure_flags"]["heavy_or_soft_track"] = heavy_soft_flag
            row["soft_unspecified"] = soft_unspecified

            for name, value in row["failure_flags"].items():
                if value and name != "unknown_barrier_map":
                    failure_counts[name] += 1
    finally:
        prep_conn.close()

    total_tipped_races = len(race_level_rows)
    settled_rows = [row for row in race_level_rows if row.get("tip_result") in {"WIN", "LOSS"}]
    settled_races = len(settled_rows)
    total_wins = sum(1 for row in settled_rows if row.get("tip_result") == "WIN")
    total_losses = sum(1 for row in settled_rows if row.get("tip_result") == "LOSS")
    unresolved_races = total_tipped_races - settled_races
    strike_rate = round((total_wins / settled_races * 100.0), 2) if settled_races else 0.0

    for row in race_level_rows:
        if row.get("tip_result") != "LOSS" or not row.get("matched_race"):
            row["primary_failure_category_ranked"] = []
            continue
        ranked_failures = [
            name for name, value in row.get("failure_flags", {}).items() if value and name != "unknown_barrier_map"
        ]
        ranked_failures.sort(key=lambda item: (-failure_counts[item], item))
        row["primary_failure_category_ranked"] = ranked_failures

    failure_rank = [
        {
            "category": category,
            "count": count,
            "loss_pct": round((count / total_losses * 100.0), 2) if total_losses else 0.0,
        }
        for category, count in sorted(failure_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    report = {
        "metadata": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "analysis_window": {"date_from": args.date_from, "date_to": args.date_to},
            "source_tip_files": [path.name for path in tip_files],
        },
        "race_level_rows": race_level_rows,
        "losing_tip_failure_counts": dict(failure_counts),
        "losing_tip_failure_rank": failure_rank,
        "summary_metrics": {
            "total_tipped_races": total_tipped_races,
            "settled_races": settled_races,
            "unsettled_or_unmatched_races": unresolved_races,
            "total_wins": total_wins,
            "total_losses": total_losses,
            "settled_strike_rate": strike_rate,
        },
        "sectional_coverage": sectional_coverage,
        "betfair_mapping_coverage": betfair_mapping_coverage,
        "data_quality_notes": notes,
    }

    output_json.write_text(json.dumps(report, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
    output_summary.write_text(
        summarize_findings(race_level_rows, failure_rank, sectional_coverage, betfair_mapping_coverage, notes),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
