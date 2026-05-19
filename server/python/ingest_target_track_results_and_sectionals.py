#!/usr/bin/env python3
"""
Target-track results + sectionals ingestion for a specific raceday.

It intentionally does not touch prediction_audit or forward-test snapshots.
"""

import argparse
import copy
import json
import os
import sys
from pathlib import Path

import psycopg2

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


def load_env():
    if not ENV_PATH.exists():
        return

    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env()

from fetch_and_import_date import fetch_date, import_meetings
from nsw_sectional_collector import run_backfill as collect_nsw_sectionals
from racing_com_sectionals_collector import collect_for_date as collect_racing_com_sectionals
from sectional_times_collector import collect_for_date_track as collect_qld_sectionals


RESULTS_ONLY_TRACKS = {"Ascot"}
SECTIONAL_SOURCES = {
    "Rosehill Gardens": {"source": "nsw_pidata", "tracks": ["Rosehill Gardens"]},
    "Caulfield": {"source": "racing_com", "tracks": ["Caulfield"]},
    "Morphettville Parks": {"source": "racing_com", "tracks": ["Morphettville Parks"]},
    "Eagle Farm": {"source": "racing_qld", "tracks": ["Eagle Farm"]},
    "Aquis Park Gold Coast": {"source": "racing_qld", "tracks": ["Gold Coast", "Aquis Park Gold Coast"]},
}
TRACK_KEY_ALIASES = {
    "rosehill": "rosehillgardens",
    "rosehillgardens": "rosehillgardens",
    "goldcoast": "goldcoast",
    "aquisparkgoldcoast": "goldcoast",
    "royalrandwick": "randwick",
    "randwick": "randwick",
}


def normalize_track_key(track_name):
    cleaned = "".join(ch.lower() for ch in str(track_name or "") if ch.isalnum())
    return TRACK_KEY_ALIASES.get(cleaned, cleaned)


def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable required")
    return psycopg2.connect(database_url)


def load_target_tracks(date_str):
    racecard_path = PROJECT_ROOT / "racecards" / f"racecard_{date_str}.json"
    if not racecard_path.exists():
        raise FileNotFoundError(f"Racecard file not found: {racecard_path}")

    payload = json.loads(racecard_path.read_text(encoding="utf-8"))
    meets = payload.get("meets", []) if isinstance(payload, dict) else payload
    if not isinstance(meets, list):
        raise ValueError(f"Unexpected racecard format in {racecard_path}")

    tracks = []
    for meeting in meets:
        if not isinstance(meeting, dict):
            continue
        track = meeting.get("course") or meeting.get("track")
        if track:
            tracks.append(track)

    if not tracks:
        raise ValueError(f"No tracks found in {racecard_path}")

    return tracks


def select_target_meetings(all_meetings, target_tracks):
    display_by_key = {normalize_track_key(track): track for track in target_tracks}
    selected = []
    seen_tracks = set()

    for meeting in all_meetings or []:
        course = meeting.get("course") or meeting.get("track") or ""
        track_key = normalize_track_key(course)
        if track_key not in display_by_key:
            continue

        display_track = display_by_key[track_key]
        meeting_copy = copy.deepcopy(meeting)
        meeting_copy["course"] = display_track
        for race in meeting_copy.get("races", []):
            race["course"] = display_track
        selected.append(meeting_copy)
        seen_tracks.add(display_track)

    missing = [track for track in target_tracks if track not in seen_tracks]
    if missing:
        raise RuntimeError(
            "Target meetings missing from API fetch for "
            f"{', '.join(missing)}"
        )

    return selected


def get_results_counts(conn, date_str, track):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) AS rows_count,
                COUNT(DISTINCT race_number) AS race_count
            FROM race_results_history
            WHERE race_date = %s AND track = %s
            """,
            (date_str, track),
        )
        rows_count, race_count = cur.fetchone()

    return {
        "rows": int(rows_count or 0),
        "races": int(race_count or 0),
    }


def get_sectional_counts(conn, date_str, display_track):
    config = SECTIONAL_SOURCES.get(display_track)
    if not config:
        return {
            "rows": 0,
            "matched_rows": 0,
            "source": None,
        }

    track_names = [track.lower() for track in config["tracks"]]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) AS rows_count,
                COUNT(*) FILTER (WHERE race_results_history_id IS NOT NULL) AS matched_rows
            FROM sectional_times
            WHERE race_date = %s
              AND source = %s
              AND LOWER(track) = ANY(%s)
            """,
            (date_str, config["source"], track_names),
        )
        rows_count, matched_rows = cur.fetchone()

    return {
        "rows": int(rows_count or 0),
        "matched_rows": int(matched_rows or 0),
        "source": config["source"],
    }


def get_all_results_tracks(conn, date_str):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT track
            FROM race_results_history
            WHERE race_date = %s
            ORDER BY track
            """,
            (date_str,),
        )
        return [row[0] for row in cur.fetchall()]


def import_target_results(date_str, target_tracks):
    api_meetings = fetch_date(date_str, metro_only=False)
    if not api_meetings:
        raise RuntimeError(f"No meetings fetched from The Racing API for {date_str}")

    selected_meetings = select_target_meetings(api_meetings, target_tracks)

    conn = get_db_connection()
    try:
        rows, skipped_races, skipped_runners = import_meetings(selected_meetings, conn)
    finally:
        conn.close()

    return {
        "selected_meetings": len(selected_meetings),
        "rows_imported": int(rows),
        "skipped_races": int(skipped_races),
        "skipped_runners": int(skipped_runners),
    }


def collect_sectionals_for_targets(date_str, target_tracks):
    outcomes = {}

    if "Rosehill Gardens" in target_tracks:
        outcomes["Rosehill Gardens"] = {
            "collector": "nsw_pidata",
            "return_value": collect_nsw_sectionals(
                date_filter=date_str,
                venue_filter="Rosehill Gardens",
            ),
        }

    for display_track, collector_track in (
        ("Eagle Farm", "Eagle Farm"),
        ("Aquis Park Gold Coast", "Gold Coast"),
    ):
        if display_track not in target_tracks:
            continue
        runners, matched, imported, skipped = collect_qld_sectionals(
            date_str,
            collector_track,
            os.environ["DATABASE_URL"],
            verbose=True,
        )
        outcomes[display_track] = {
            "collector": "racing_qld",
            "runners": runners,
            "matched": matched,
            "imported": imported,
            "skipped": skipped,
        }

    vic_sa_tracks = [track for track in ("Caulfield", "Morphettville Parks") if track in target_tracks]
    if vic_sa_tracks:
        outcomes["VIC_SA"] = {
            "collector": "racing_com",
            "return_value": collect_racing_com_sectionals(
                date_str,
                states="VIC|SA",
                db_url=os.environ["DATABASE_URL"],
                verbose=True,
                tracks=vic_sa_tracks,
            ),
        }

    return outcomes


def build_summary(date_str, target_tracks, before_results, after_results, before_sectionals, after_sectionals):
    conn = get_db_connection()
    try:
        tracks_after = set(get_all_results_tracks(conn, date_str))
    finally:
        conn.close()

    expected_tracks = set(target_tracks)
    summary_tracks = []

    for track in target_tracks:
        results_before = before_results[track]
        results_after = after_results[track]
        sectionals_before = before_sectionals[track]
        sectionals_after = after_sectionals[track]

        note = None
        if track in RESULTS_ONLY_TRACKS:
            status = "results_only"
            note = "WA sectionals are not supported by the current repo collectors."
        else:
            status = "complete"

        summary_tracks.append(
            {
                "track": track,
                "results_rows_imported": results_after["rows"] - results_before["rows"],
                "distinct_races_imported": results_after["races"] - results_before["races"],
                "sectional_rows_imported": sectionals_after["rows"] - sectionals_before["rows"],
                "sectional_source": sectionals_after["source"],
                "status": status,
                "matched_sectional_rows": sectionals_after["matched_rows"],
                "note": note,
            }
        )

    unexpected_tracks = sorted(tracks_after - expected_tracks)
    missing_tracks = sorted(expected_tracks - tracks_after)

    return {
        "date": date_str,
        "tracks": summary_tracks,
        "unexpected_results_tracks": unexpected_tracks,
        "missing_results_tracks": missing_tracks,
    }


def snapshot_counts(date_str, target_tracks):
    conn = get_db_connection()
    try:
        results_counts = {track: get_results_counts(conn, date_str, track) for track in target_tracks}
        sectional_counts = {track: get_sectional_counts(conn, date_str, track) for track in target_tracks}
    finally:
        conn.close()

    return results_counts, sectional_counts


def main():
    parser = argparse.ArgumentParser(description="Ingest target-track raw results and sectionals for a specific date")
    parser.add_argument("--date", required=True, help="Date YYYY-MM-DD")
    args = parser.parse_args()

    date_str = args.date
    target_tracks = load_target_tracks(date_str)

    print("=" * 72)
    print(f"  TARGET-TRACK RESULTS + SECTIONALS INGESTION — {date_str}")
    print("=" * 72)
    print("  Tracks: " + ", ".join(target_tracks))

    before_results, before_sectionals = snapshot_counts(date_str, target_tracks)
    print("\n  Preflight counts:")
    print("    race_results_history:", sum(item["rows"] for item in before_results.values()))
    print("    sectional_times:", sum(item["rows"] for item in before_sectionals.values()))

    import_summary = import_target_results(date_str, target_tracks)
    print("\n  Results import summary:")
    print(json.dumps(import_summary, indent=2))

    print("\n  Collecting sectionals...")
    collector_summary = collect_sectionals_for_targets(date_str, target_tracks)
    print(json.dumps(collector_summary, indent=2))

    after_results, after_sectionals = snapshot_counts(date_str, target_tracks)
    final_summary = build_summary(
        date_str,
        target_tracks,
        before_results,
        after_results,
        before_sectionals,
        after_sectionals,
    )

    print("\n  Final summary:")
    print(json.dumps(final_summary, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        sys.exit(1)
