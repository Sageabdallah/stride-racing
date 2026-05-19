from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg2
import psycopg2.extras


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RACECARDS_DIR = PROJECT_ROOT / "racecards"
INTELLIGENCE_DIR = Path(__file__).resolve().parent
BUILD_LOG_PATH = INTELLIGENCE_DIR / "build_log.txt"

TRACK_ALIASES = {
    "royal randwick": "randwick",
    "randwick": "randwick",
    "randwick-kensington": "kensington",
    "kensington": "kensington",
    "rosehill gardens": "rosehill",
    "rosehill": "rosehill",
    "flemington": "flemington",
    "morphettville": "morphettville",
    "doomben": "doomben",
    "eagle farm": "eaglefarm",
    "ascot wa": "ascotwa",
    "ascot": "ascotwa",
    "moonee valley": "mooneevalley",
    "caulfield": "caulfield",
}


def load_database_url() -> str:
    database_url = __import__("os").environ.get("DATABASE_URL", "").strip()
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
    __import__("os").environ["DATABASE_URL"] = database_url
    return database_url


def get_connection(statement_timeout_ms: int = 120000):
    conn = psycopg2.connect(load_database_url())
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"SET statement_timeout TO {int(statement_timeout_ms)}")
        cur.execute("SET idle_in_transaction_session_timeout TO 0")
    return conn


def fetch_df(query: str, params: dict[str, Any] | None = None, statement_timeout_ms: int = 120000) -> pd.DataFrame:
    with get_connection(statement_timeout_ms=statement_timeout_ms) as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(query, params or {})
        rows = cur.fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def fetch_all_dicts(conn, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params or {})
        return [dict(row) for row in cur.fetchall()]


def append_build_log(text: str) -> None:
    INTELLIGENCE_DIR.mkdir(parents=True, exist_ok=True)
    with BUILD_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(text.rstrip() + "\n")


def iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def now_iso() -> str:
    return iso_now()


def normalize_track(track: Any) -> str:
    raw = str(track or "").strip().lower()
    if not raw:
        return ""
    if raw in TRACK_ALIASES:
        return TRACK_ALIASES[raw]
    return re.sub(r"[^a-z0-9]+", "", raw)


def normalize_name(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def norm_name(name: Any) -> str:
    return normalize_name(name)


def slugify(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-") or "all-tracks"


def safe_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        value = value.strip().lower().replace("kg", "").replace("m", "")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        value = value.strip().lower().replace("kg", "").replace("$", "")
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_distance_m(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value or "")
    match = re.search(r"(\d{3,4})", text)
    return int(match.group(1)) if match else None


def normalize_going_group(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "Unknown"
    if "heavy" in text:
        return "Heavy"
    if "soft" in text or "slow" in text:
        return "Soft"
    if "poly" in text or "synthetic" in text:
        return "Synthetic"
    if "good" in text or "firm" in text:
        return "Good"
    return text.title()


def going_category(value: Any) -> str:
    return normalize_going_group(value)


def parse_class_score(race_class: Any, class_level: Any = None) -> int:
    numeric_class = safe_int(class_level)
    if numeric_class and numeric_class > 0:
        return numeric_class
    raw = str(race_class or "").strip().upper()
    if not raw:
        return 30
    if raw in {"GROUP 1", "GR1", "G1"}:
        return 100
    if raw in {"GROUP 2", "GR2", "G2"}:
        return 95
    if raw in {"GROUP 3", "GR3", "G3"}:
        return 90
    if "LISTED" in raw:
        return 85
    bm_match = re.search(r"BM\s*(\d+)", raw)
    if bm_match:
        return min(80, 40 + int(bm_match.group(1)) // 2)
    class_match = re.search(r"CL(?:ASS)?\s*(\d+)", raw)
    if class_match:
        return max(35, 75 - int(class_match.group(1)) * 5)
    if "MIDWAY" in raw or "HCP" in raw or "HANDICAP" in raw:
        return 65
    if "MAIDEN" in raw or "MDN" in raw:
        return 30
    if "OPEN" in raw:
        return 80
    return 55


def class_bucket(score: int) -> str:
    if score >= 95:
        return "Group 1-2"
    if score >= 90:
        return "Group 3"
    if score >= 85:
        return "Listed"
    if score >= 70:
        return "Open/BM84+"
    if score >= 55:
        return "Metro/BM70-78"
    if score >= 40:
        return "Provincial/BM58-68"
    return "Maiden/Low Grade"


def distance_bucket(distance_m: Any) -> str:
    distance = safe_int(distance_m, 0) or 0
    if distance <= 1200:
        return "Sprint"
    if distance <= 1600:
        return "Mile"
    if distance <= 2000:
        return "Middle"
    return "Staying"


def field_size_band(field_size: Any) -> str:
    runners = safe_int(field_size, 0) or 0
    if runners <= 8:
        return "Small"
    if runners <= 12:
        return "Medium"
    return "Large"


def band_field_size(field_size: Any) -> str:
    runners = safe_int(field_size, 0) or 0
    if runners <= 8:
        return "small"
    if runners <= 12:
        return "medium"
    if runners <= 16:
        return "large"
    return "maximum"


def barrier_segment(barrier: Any) -> str:
    value = safe_int(barrier, 0) or 0
    if value <= 0:
        return "Unknown"
    if value <= 4:
        return "1-4"
    if value <= 8:
        return "5-8"
    return "9+"


def racecard_path_for_date(target_date: str, racecard_path: str | None = None) -> Path:
    return Path(racecard_path) if racecard_path else RACECARDS_DIR / f"racecard_{target_date}.json"


def load_racecard_meetings(target_date: str, racecard_path: str | None = None) -> list[dict[str, Any]]:
    path = racecard_path_for_date(target_date, racecard_path=racecard_path)
    if not path.exists():
        raise FileNotFoundError(f"Racecard not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def active_runners(race: dict[str, Any]) -> list[dict[str, Any]]:
    return [runner for runner in race.get("runners", []) if not runner.get("scratched")]


def flatten_races(meetings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for meeting in meetings:
        track = meeting.get("course")
        for race in meeting.get("races", []):
            flattened.append(
                {
                    "track": track,
                    "track_key": normalize_track(track),
                    "race_date": race.get("date") or meeting.get("date"),
                    "race_number": safe_int(race.get("race_number")) or 0,
                    "race_name": race.get("race_name"),
                    "distance_m": parse_distance_m(race.get("distance")),
                    "race_class": race.get("class"),
                    "class_score": parse_class_score(race.get("class")),
                    "going_group": normalize_going_group(race.get("going")),
                    "raw_race": race,
                    "runners": active_runners(race),
                }
            )
    return flattened


def build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--track", default=None)
    parser.add_argument("--statement-timeout-ms", type=int, default=120000)
    parser.add_argument("--output", default=None)
    return parser


def build_output_path(base_name: str, target_date: str, track_filter: str | None = None, output_path: str | None = None) -> Path:
    if output_path:
        path = Path(output_path)
    else:
        file_name = base_name
        if track_filter:
            file_name = f"{file_name}_{slugify(track_filter)}"
        path = INTELLIGENCE_DIR / "output" / target_date / f"{file_name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def ensure_output_path(file_name: str, output_path: str | None = None) -> Path:
    path = Path(output_path) if output_path else INTELLIGENCE_DIR / file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def horse_key(horse_id: Any, horse_name: Any) -> str:
    horse_id_text = str(horse_id or "").strip()
    return horse_id_text if horse_id_text else normalize_name(horse_name)


def load_target_runners(target_date: str, track_filter: str | None = None) -> list[dict[str, Any]]:
    meetings = load_racecard_meetings(target_date)
    races = flatten_races(meetings)
    runners: list[dict[str, Any]] = []
    track_filter_norm = normalize_track(track_filter) if track_filter else None
    for race in races:
        if track_filter_norm and race["track_key"] != track_filter_norm:
            continue
        for runner in race["runners"]:
            horse_name = runner.get("horse")
            runners.append(
                {
                    "track": race["track"],
                    "track_norm": race["track_key"],
                    "race_number": race["race_number"],
                    "race_name": race["race_name"],
                    "distance_m": race["distance_m"],
                    "race_class": race["race_class"],
                    "horse_name": horse_name,
                    "horse_name_norm": normalize_name(horse_name),
                    "horse_id": runner.get("horse_id"),
                    "market_odds": median(
                        [
                            safe_float(item.get("win_odds"))
                            for item in runner.get("odds", [])
                            if isinstance(item, dict)
                        ]
                    )
                    if isinstance(runner.get("odds"), list)
                    else safe_float(runner.get("sp")),
                    "barrier": safe_int(runner.get("draw")),
                    "trainer": runner.get("trainer"),
                    "jockey": runner.get("jockey"),
                    "horse_key": horse_key(runner.get("horse_id"), horse_name),
                }
            )
    return runners


def race_filter_sql(column: str = "track") -> str:
    return f"""
    CASE
      WHEN lower({column}) IN ('royal randwick', 'randwick') THEN 'Randwick'
      WHEN lower({column}) IN ('randwick-kensington', 'kensington') THEN 'Kensington'
      WHEN lower({column}) IN ('rosehill gardens', 'rosehill') THEN 'Rosehill'
      WHEN lower({column}) IN ('ascot', 'ascot wa') THEN 'Ascot WA'
      WHEN lower({column}) LIKE '%%moonee valley%%' THEN 'Moonee Valley'
      WHEN lower({column}) LIKE '%%eagle farm%%' THEN 'Eagle Farm'
      WHEN lower({column}) LIKE '%%morphettville%%' THEN 'Morphettville'
      WHEN lower({column}) LIKE '%%caulfield%%' THEN 'Caulfield'
      WHEN lower({column}) LIKE '%%flemington%%' THEN 'Flemington'
      WHEN lower({column}) LIKE '%%doomben%%' THEN 'Doomben'
      ELSE coalesce({column}, '')
    END
    """


def parse_splits_to_sections(splits_json_data: Any) -> list[dict[str, Any]]:
    try:
        if not splits_json_data:
            return []
        data = splits_json_data
        if isinstance(data, str):
            data = json.loads(data)
        sections: list[dict[str, Any]] = []
        if isinstance(data, dict):
            for key, val in data.items():
                if not isinstance(val, dict):
                    continue
                try:
                    mark = int(str(key).replace("m", "").strip())
                except (TypeError, ValueError):
                    continue
                sections.append(
                    {
                        "mark_m": mark,
                        "time_s": safe_float(val.get("time")),
                        "speed_ms": safe_float(val.get("speed_ms")),
                        "position": safe_int(val.get("position")),
                    }
                )
        sections.sort(key=lambda s: s["mark_m"])
        return sections
    except Exception:
        return []


def derive_split_position(splits_json: Any, mark: str) -> int | None:
    if not isinstance(splits_json, dict):
        return None
    segment = splits_json.get(mark)
    if not isinstance(segment, dict):
        return None
    return safe_int(segment.get("position"))


def merge_results_with_sectionals(results_df: pd.DataFrame, sectionals_df: pd.DataFrame) -> pd.DataFrame:
    results = results_df.copy()
    sectionals = sectionals_df.copy()
    if results.empty:
        return results
    results["join_key"] = results.apply(
        lambda row: f"{normalize_track(row.get('track'))}|{str(row.get('race_date') or '')}|{safe_int(row.get('race_number'), 0)}|{normalize_name(row.get('horse_name'))}",
        axis=1,
    )
    sectionals["join_key"] = sectionals.apply(
        lambda row: f"{normalize_track(row.get('track'))}|{str(row.get('race_date') or '')}|{safe_int(row.get('race_number'), 0)}|{normalize_name(row.get('horse_name'))}",
        axis=1,
    )

    merged = results.merge(
        sectionals,
        how="left",
        left_on="id",
        right_on="race_results_history_id",
        suffixes=("", "_st"),
    )
    unmatched = merged["race_results_history_id"].isna()
    if unmatched.any():
        fallback_lookup = (
            sectionals.sort_values(["race_date", "track", "race_number"], na_position="last")
            .drop_duplicates(subset=["join_key"], keep="last")
            .set_index("join_key")
        )
        fallback_rows = fallback_lookup.reindex(merged.loc[unmatched, "join_key"])
        for column in sectionals.columns:
            if column in {"join_key"} or column not in fallback_rows.columns:
                continue
            merged.loc[unmatched, column] = merged.loc[unmatched, column].where(
                merged.loc[unmatched, column].notna(),
                fallback_rows[column].values,
            )
    return merged


def median(values: Any) -> float | None:
    cleaned = [float(v) for v in values if v is not None and not pd.isna(v)]
    if not cleaned:
        return None
    cleaned.sort()
    mid = len(cleaned) // 2
    if len(cleaned) % 2:
        return cleaned[mid]
    return round((cleaned[mid - 1] + cleaned[mid]) / 2, 4)


def data_window_summary() -> dict[str, Any]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT MIN(race_date::date), MAX(race_date::date), COUNT(*) FROM race_results_history")
        rrh_min, rrh_max, rrh_rows = cur.fetchone()
        cur.execute("SELECT MIN(race_date::date), MAX(race_date::date), COUNT(*) FROM sectional_times")
        st_min, st_max, st_rows = cur.fetchone()
        cur.execute("SELECT MIN(meeting_date), MAX(meeting_date), COUNT(*) FROM betfair_market_runner_map")
        bf_min, bf_max, bf_rows = cur.fetchone()
    months = 0
    if rrh_min and rrh_max:
        months = max(1, round((rrh_max - rrh_min).days / 30.4375))
    return {
        "race_results_history": {"min_date": rrh_min, "max_date": rrh_max, "rows": rrh_rows},
        "sectional_times": {"min_date": st_min, "max_date": st_max, "rows": st_rows},
        "betfair_market_runner_map": {"min_date": bf_min, "max_date": bf_max, "rows": bf_rows},
        "data_window_months": months,
    }


def write_json(arg1, arg2) -> None:
    if isinstance(arg1, (str, Path)):
        output_path = Path(arg1)
        payload = arg2
    else:
        payload = arg1
        output_path = Path(arg2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
