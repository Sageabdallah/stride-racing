#!/usr/bin/env python3
"""Build Betfair market/runner mapping and joinable labeled training view."""

from __future__ import annotations

import argparse
import bz2
import json
import logging
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from dateutil import parser as dt_parser
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from identity_normalization import DDL_FUNCTIONS

LOGGER = logging.getLogger("betfair_mapping")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Betfair market runner mapping and training view.")
    parser.add_argument(
        "--data-dir",
        type=str,
        default=os.environ.get("BETFAIR_HISTORICAL_DIR"),
        help="Root folder containing Betfair BASIC .bz2 files (default: "
             "$BETFAIR_HISTORICAL_DIR). Not needed with --refresh-view-only.",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Optional DATABASE_URL override.",
    )
    parser.add_argument(
        "--max-markets",
        type=int,
        default=None,
        help="Optional cap on number of market IDs processed.",
    )
    parser.add_argument(
        "--timestamp-from",
        type=str,
        default=None,
        help="Optional lower bound for market_data.timestamp (inclusive), e.g. 2024-12-23.",
    )
    parser.add_argument(
        "--timestamp-to",
        type=str,
        default=None,
        help="Optional upper bound for market_data.timestamp (exclusive), e.g. 2026-02-26.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=max((os.cpu_count() or 4) // 2, 1),
        help="Process workers for bz2 parsing.",
    )
    parser.add_argument(
        "--refresh-view-only",
        action="store_true",
        help="Only (re)create mapping table/view DDL; skip file parsing/loading.",
    )
    parser.add_argument(
        "--include-already-mapped",
        action="store_true",
        help="Reprocess all market IDs even if already present in mapping table.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity.",
    )
    return parser.parse_args()


def resolve_db_url(cli_db_url: Optional[str]) -> Optional[str]:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    if cli_db_url:
        return cli_db_url
    return os.environ.get("DATABASE_URL")


def parse_timestamp_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text == "":
            return None
        try:
            dt = dt_parser.isoparse(text)
        except Exception:
            try:
                dt = dt_parser.parse(text)
            except Exception:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text != "":
            return text
    return None


def extract_race_number(*candidates: Any) -> Optional[int]:
    for candidate in candidates:
        if candidate is None:
            continue
        text = str(candidate).strip()
        if text == "":
            continue
        direct = re.search(r"^\d{1,2}$", text)
        if direct:
            return int(direct.group(0))
        match = re.search(r"\bR(?:ace)?\s*0?(\d{1,2})\b", text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
        match = re.search(r"\bRace\s*0?(\d{1,2})\b", text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def clean_track_name(name: str) -> str:
    text = name.strip()
    text = re.sub(r"\([^)]*\)", "", text).strip()
    text = re.sub(r"\b\d{1,2}(st|nd|rd|th)\b.*$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\b\d{1,2}\s+[A-Za-z]{3,9}\b.*$", "", text).strip()
    text = text.split(" - ")[0].strip()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_horse_name(name: str) -> str:
    text = name.strip()
    text = re.sub(r"^\d+\.\s*", "", text)
    return text.strip()


def normalize_key(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    key = re.sub(r"[^a-z0-9]+", "", text.lower())
    return key or None


def derive_race_local_date(event_start_utc: datetime, tz_name: Optional[str]) -> date:
    if tz_name:
        try:
            return event_start_utc.astimezone(ZoneInfo(tz_name)).date()
        except Exception:
            pass
    return event_start_utc.astimezone(ZoneInfo("Australia/Sydney")).date()


@dataclass
class MarketMeta:
    market_id: str
    event_id: Optional[str]
    event_name: Optional[str]
    venue: Optional[str]
    track: Optional[str]
    race_number: Optional[int]
    market_name: Optional[str]
    market_type: Optional[str]
    country_code: Optional[str]
    event_type_id: Optional[str]
    timezone: Optional[str]
    market_time: Optional[datetime]
    meeting_date: Optional[date]
    source_file: str


def parse_market_file(task: Tuple[str, str]) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    market_id, file_path = task
    path = Path(file_path)

    metadata: Optional[MarketMeta] = None
    runners: Dict[str, str] = {}

    try:
        with bz2.open(path, mode="rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if line == "":
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue

                market_changes = payload.get("mc")
                if not isinstance(market_changes, list):
                    continue

                for market_change in market_changes:
                    if not isinstance(market_change, dict):
                        continue

                    stream_market_id = first_non_empty(market_change.get("id"), market_change.get("marketId"))
                    if stream_market_id != market_id:
                        continue

                    md = market_change.get("marketDefinition")
                    if not isinstance(md, dict):
                        continue

                    market_time = parse_timestamp_utc(first_non_empty(md.get("marketTime"), md.get("market_time")))
                    timezone_name = first_non_empty(md.get("timezone"))
                    meeting_date = derive_race_local_date(market_time, timezone_name) if market_time else None
                    event_name = first_non_empty(md.get("eventName"), md.get("event_name"))
                    venue = first_non_empty(md.get("venue"), md.get("venueName"))
                    track = clean_track_name(first_non_empty(venue, event_name) or "UNKNOWN")
                    market_name = first_non_empty(md.get("name"), md.get("marketName"))

                    metadata = MarketMeta(
                        market_id=market_id,
                        event_id=first_non_empty(md.get("eventId"), md.get("event_id")),
                        event_name=event_name,
                        venue=venue,
                        track=track,
                        race_number=extract_race_number(market_name, event_name),
                        market_name=market_name,
                        market_type=first_non_empty(md.get("marketType"), md.get("market_type")),
                        country_code=first_non_empty(md.get("countryCode"), md.get("country_code")),
                        event_type_id=first_non_empty(md.get("eventTypeId"), md.get("event_type_id")),
                        timezone=timezone_name,
                        market_time=market_time,
                        meeting_date=meeting_date,
                        source_file=str(path),
                    )

                    stream_runners = md.get("runners")
                    if isinstance(stream_runners, list):
                        for runner in stream_runners:
                            if not isinstance(runner, dict):
                                continue
                            selection_id = first_non_empty(runner.get("id"), runner.get("selectionId"))
                            horse_name = first_non_empty(
                                runner.get("name"),
                                runner.get("runnerName"),
                                runner.get("selectionName"),
                            )
                            if selection_id and horse_name:
                                runners[str(selection_id)] = clean_horse_name(horse_name)

                    # Market definition with runners is enough for mapping.
                    if runners:
                        break
                if runners:
                    break

        if metadata is None or not runners:
            return market_id, [], None

        rows: List[Dict[str, Any]] = []
        for selection_id, horse_name in runners.items():
            rows.append(
                {
                    "market_id": metadata.market_id,
                    "selection_id": selection_id,
                    "event_id": metadata.event_id,
                    "event_name": metadata.event_name,
                    "venue": metadata.venue,
                    "track": metadata.track,
                    "track_norm": normalize_key(metadata.track),
                    "race_number": metadata.race_number,
                    "market_name": metadata.market_name,
                    "market_type": metadata.market_type,
                    "country_code": metadata.country_code,
                    "event_type_id": metadata.event_type_id,
                    "timezone": metadata.timezone,
                    "market_time": metadata.market_time,
                    "meeting_date": metadata.meeting_date,
                    "horse_name": horse_name,
                    "horse_name_norm": normalize_key(horse_name),
                    "source_file": metadata.source_file,
                }
            )
        return market_id, rows, None
    except Exception as exc:
        return market_id, [], str(exc)


DDL_TABLE = """
CREATE TABLE IF NOT EXISTS betfair_market_runner_map (
    market_id TEXT NOT NULL,
    selection_id TEXT NOT NULL,
    event_id TEXT NULL,
    event_name TEXT NULL,
    venue TEXT NULL,
    track TEXT NULL,
    track_norm TEXT NULL,
    race_number INTEGER NULL,
    market_name TEXT NULL,
    market_type TEXT NULL,
    country_code TEXT NULL,
    event_type_id TEXT NULL,
    timezone TEXT NULL,
    market_time TIMESTAMPTZ NULL,
    meeting_date DATE NULL,
    horse_name TEXT NOT NULL,
    horse_name_norm TEXT NULL,
    source_file TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (market_id, selection_id)
);

CREATE INDEX IF NOT EXISTS idx_betfair_map_meeting_race_horse
    ON betfair_market_runner_map (meeting_date, race_number, horse_name_norm);

CREATE INDEX IF NOT EXISTS idx_betfair_map_track_norm
    ON betfair_market_runner_map (track_norm);

CREATE INDEX IF NOT EXISTS idx_betfair_map_market_type
    ON betfair_market_runner_map (market_type, country_code, event_type_id);
"""


DDL_DROP_VIEW = "DROP VIEW IF EXISTS betfair_labeled_training_view;"


DDL_VIEW = """
CREATE OR REPLACE VIEW betfair_labeled_training_view AS
WITH mapped_ticks AS (
    SELECT
        m.market_id,
        m.selection_id,
        NULLIF(m.timestamp, '')::timestamp AS capture_timestamp_utc,
        NULLIF(m.price, '')::numeric AS market_odds,
        b.market_time,
        b.meeting_date,
        b.track,
        b.track_norm,
        b.race_number,
        b.horse_name,
        b.horse_name_norm,
        b.market_type,
        b.country_code,
        b.event_type_id,
        EXTRACT(EPOCH FROM (b.market_time - (NULLIF(m.timestamp, '')::timestamp AT TIME ZONE 'UTC'))) / 60.0 AS minutes_to_jump_utc,
        EXTRACT(EPOCH FROM (b.market_time - (NULLIF(m.timestamp, '')::timestamp AT TIME ZONE 'Australia/Sydney'))) / 60.0 AS minutes_to_jump_sydney
    FROM market_data m
    JOIN betfair_market_runner_map b
      ON b.market_id = m.market_id
     AND b.selection_id = m.selection_id
    WHERE m.type = 'ltp'
      AND NULLIF(m.price, '') IS NOT NULL
      AND b.market_time IS NOT NULL
      AND b.meeting_date IS NOT NULL
      AND b.race_number IS NOT NULL
      AND (b.country_code IS NULL OR b.country_code = 'AU')
      AND (b.event_type_id IS NULL OR b.event_type_id = '7')
      AND (b.market_type IS NULL OR b.market_type = 'WIN')
),
ticks_scored AS (
    SELECT
        mt.*,
        CASE
            WHEN abs(mt.minutes_to_jump_utc - 10.0) <= abs(mt.minutes_to_jump_sydney - 10.0)
                THEN mt.minutes_to_jump_utc
            ELSE mt.minutes_to_jump_sydney
        END AS minutes_to_jump
    FROM mapped_ticks mt
),
t10 AS (
    SELECT
        mt2.*,
        ROW_NUMBER() OVER (
            PARTITION BY mt2.market_id, mt2.selection_id
            ORDER BY
                CASE WHEN mt2.minutes_to_jump >= 0 THEN 0 ELSE 1 END,
                ABS(mt2.minutes_to_jump - 10.0),
                mt2.capture_timestamp_utc DESC
        ) AS rn
    FROM ticks_scored mt2
),
results_norm AS (
    SELECT
        race_id,
        track,
        stride_norm_track(track) AS track_norm,
        race_date::date AS meeting_date,
        race_number,
        horse_name,
        stride_norm_name(horse_name) AS horse_name_norm,
        position AS actual_position,
        CASE WHEN position = 1 THEN 1 ELSE 0 END AS won,
        CASE WHEN position BETWEEN 1 AND 3 THEN 1 ELSE 0 END AS placed
    FROM race_results_history
)
SELECT
    t.market_id,
    t.selection_id,
    t.capture_timestamp_utc AS capture_timestamp,
    t.market_time AS race_time_scheduled,
    t.meeting_date,
    t.track,
    t.race_number,
    t.horse_name,
    t.market_odds,
    t.minutes_to_jump,
    CASE WHEN t.minutes_to_jump BETWEEN 8.0 AND 12.0 THEN 1 ELSE 0 END AS is_t10_window,
    r.race_id,
    r.actual_position,
    r.won,
    r.placed,
    CASE WHEN r.race_id IS NOT NULL THEN 1 ELSE 0 END AS has_label
FROM t10 t
LEFT JOIN results_norm r
  ON r.meeting_date = t.meeting_date
 AND r.race_number = t.race_number
 AND r.horse_name_norm = t.horse_name_norm
 AND (
      r.track_norm = t.track_norm
      OR t.track_norm IS NULL
      OR r.track_norm IS NULL
 )
WHERE t.rn = 1;
"""


UPSERT_SQL = """
INSERT INTO betfair_market_runner_map (
    market_id, selection_id, event_id, event_name, venue, track, track_norm, race_number,
    market_name, market_type, country_code, event_type_id, timezone, market_time, meeting_date,
    horse_name, horse_name_norm, source_file, updated_at
)
VALUES (
    :market_id, :selection_id, :event_id, :event_name, :venue, :track, :track_norm, :race_number,
    :market_name, :market_type, :country_code, :event_type_id, :timezone, :market_time, :meeting_date,
    :horse_name, :horse_name_norm, :source_file, now()
)
ON CONFLICT (market_id, selection_id) DO UPDATE
SET
    event_id = EXCLUDED.event_id,
    event_name = EXCLUDED.event_name,
    venue = EXCLUDED.venue,
    track = EXCLUDED.track,
    track_norm = EXCLUDED.track_norm,
    race_number = EXCLUDED.race_number,
    market_name = EXCLUDED.market_name,
    market_type = EXCLUDED.market_type,
    country_code = EXCLUDED.country_code,
    event_type_id = EXCLUDED.event_type_id,
    timezone = EXCLUDED.timezone,
    market_time = EXCLUDED.market_time,
    meeting_date = EXCLUDED.meeting_date,
    horse_name = EXCLUDED.horse_name,
    horse_name_norm = EXCLUDED.horse_name_norm,
    source_file = EXCLUDED.source_file,
    updated_at = now();
"""


def ensure_schema(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(DDL_TABLE))
        conn.execute(text(DDL_FUNCTIONS))
        conn.execute(text(DDL_DROP_VIEW))
        conn.execute(text(DDL_VIEW))


def fetch_target_market_ids(
    engine,
    include_already_mapped: bool,
    max_markets: Optional[int],
    timestamp_from: Optional[str],
    timestamp_to: Optional[str],
) -> List[str]:
    filters: List[str] = ["md.market_id IS NOT NULL"]
    params: Dict[str, Any] = {}

    # market_data.timestamp is text in YYYY-MM-DD HH:MM:SS format, so lexical range works.
    if timestamp_from:
        filters.append("md.timestamp >= :timestamp_from")
        params["timestamp_from"] = timestamp_from
    if timestamp_to:
        filters.append("md.timestamp < :timestamp_to")
        params["timestamp_to"] = timestamp_to
    where_clause = " AND ".join(filters)

    with engine.connect() as conn:
        if include_already_mapped:
            sql = f"""
            SELECT DISTINCT market_id
            FROM market_data md
            WHERE {where_clause}
            ORDER BY market_id
            """
        else:
            sql = f"""
            SELECT DISTINCT md.market_id
            FROM market_data md
            WHERE {where_clause}
              AND NOT EXISTS (
                SELECT 1
                FROM betfair_market_runner_map mm
                WHERE mm.market_id = md.market_id
              )
            ORDER BY md.market_id
            """
        ids = [str(row[0]) for row in conn.execute(text(sql), params).fetchall()]
    if max_markets is not None:
        ids = ids[: max(max_markets, 0)]
    return ids


def build_bz2_index(data_dir: Path) -> Dict[str, str]:
    index: Dict[str, str] = {}
    for path in data_dir.rglob("*.bz2"):
        stem = path.stem
        # First hit wins; market IDs are expected to be unique.
        if stem not in index:
            index[stem] = str(path)
    return index


def upsert_rows(engine, rows: Sequence[Dict[str, Any]]) -> None:
    if not rows:
        return
    with engine.begin() as conn:
        conn.execute(text(UPSERT_SQL), list(rows))


def process_markets(
    engine,
    market_tasks: Sequence[Tuple[str, str]],
    workers: int,
    flush_batch_rows: int = 5000,
) -> Tuple[int, int, int]:
    processed = 0
    inserted_rows = 0
    errors = 0
    buffer: List[Dict[str, Any]] = []

    with ProcessPoolExecutor(max_workers=max(workers, 1)) as executor:
        futures = [executor.submit(parse_market_file, task) for task in market_tasks]
        for idx, future in enumerate(as_completed(futures), start=1):
            market_id, rows, error = future.result()
            processed += 1
            if error:
                errors += 1
                LOGGER.warning("Parse failed for market %s: %s", market_id, error)
                continue
            if rows:
                buffer.extend(rows)
            if len(buffer) >= flush_batch_rows:
                upsert_rows(engine, buffer)
                inserted_rows += len(buffer)
                buffer = []
            if idx % 500 == 0:
                LOGGER.info("Progress: %d/%d markets parsed", idx, len(futures))

    if buffer:
        upsert_rows(engine, buffer)
        inserted_rows += len(buffer)

    return processed, inserted_rows, errors


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)

    db_url = resolve_db_url(args.db_url)
    if not db_url:
        LOGGER.error("DATABASE_URL not provided. Use --db-url or set DATABASE_URL.")
        return 1

    engine = create_engine(db_url, pool_pre_ping=True, future=True)
    ensure_schema(engine)
    LOGGER.info("Mapping table and labeled training view ensured.")

    if args.refresh_view_only:
        LOGGER.info("refresh-view-only mode complete.")
        return 0

    if not args.data_dir:
        LOGGER.error(
            "No archive to read: pass --data-dir or set BETFAIR_HISTORICAL_DIR. "
            "(--refresh-view-only rebuilds the view without the archive.)")
        return 1

    data_dir = Path(args.data_dir).expanduser().resolve()
    if not data_dir.exists():
        LOGGER.error("Data directory not found: %s", data_dir)
        return 1

    market_ids = fetch_target_market_ids(
        engine=engine,
        include_already_mapped=args.include_already_mapped,
        max_markets=args.max_markets,
        timestamp_from=args.timestamp_from,
        timestamp_to=args.timestamp_to,
    )
    if not market_ids:
        LOGGER.info("No market IDs to process.")
        return 0
    LOGGER.info("Target market IDs: %d", len(market_ids))

    file_index = build_bz2_index(data_dir)
    LOGGER.info("Indexed bz2 files: %d", len(file_index))

    tasks: List[Tuple[str, str]] = []
    missing = 0
    for market_id in market_ids:
        path = file_index.get(market_id)
        if not path:
            missing += 1
            continue
        tasks.append((market_id, path))

    LOGGER.info("Markets with source files: %d | missing files: %d", len(tasks), missing)
    if not tasks:
        return 0

    processed, inserted_rows, errors = process_markets(
        engine=engine,
        market_tasks=tasks,
        workers=args.workers,
    )

    with engine.connect() as conn:
        mapped_markets = conn.execute(text("SELECT COUNT(DISTINCT market_id) FROM betfair_market_runner_map")).scalar() or 0
        mapped_rows = conn.execute(text("SELECT COUNT(*) FROM betfair_market_runner_map")).scalar() or 0
        labeled_rows = conn.execute(text("SELECT COUNT(*) FROM betfair_labeled_training_view WHERE has_label = 1")).scalar() or 0
        total_rows = conn.execute(text("SELECT COUNT(*) FROM betfair_labeled_training_view")).scalar() or 0

    LOGGER.info("========== Mapping Summary ==========")
    LOGGER.info("Markets processed: %d", processed)
    LOGGER.info("Rows upserted this run: %d", inserted_rows)
    LOGGER.info("Parse errors: %d", errors)
    LOGGER.info("Mapped market IDs (table total): %d", mapped_markets)
    LOGGER.info("Mapped runner rows (table total): %d", mapped_rows)
    LOGGER.info("Labeled rows in training view: %d / %d", labeled_rows, total_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
