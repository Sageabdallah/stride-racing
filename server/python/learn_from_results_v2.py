#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import traceback
import uuid
from collections import defaultdict
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RUNS_DIR = SCRIPT_DIR / "research" / "learning_runs"
RUN_HISTORY_DIR = RUNS_DIR / "history"
LATEST_POINTER_PATH = RUNS_DIR / "latest.json"
ACTIVE_LOCK_PATH = RUNS_DIR / "active_run.lock.json"
STAGING_DIR = SCRIPT_DIR / "models" / "staging"

sys.path.insert(0, str(SCRIPT_DIR))

from fetch_and_import_date import fetch_date, import_meetings
from learning_track_map import normalize_track_key, resolve_track
from nsw_sectional_collector import run_backfill as collect_nsw_sectionals
from racing_com_sectionals_collector import collect_for_date as collect_racing_com_sectionals
from refresh_training_view_v2 import refresh_training_view_v2
from results_collector import collect_results_for_date
from sectional_times_collector import collect_for_date_track as collect_qld_sectionals
from stride_results_collector import ensure_table, find_tipped_races, score_tip_accuracy


load_dotenv(PROJECT_ROOT / ".env")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Canonical daily v2 results-to-learning workflow."
    )
    parser.add_argument("--start-date", help="Inclusive start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="Inclusive end date (YYYY-MM-DD)")
    parser.add_argument("--tracks", help="Optional comma-separated track list")
    parser.add_argument("--dry-run", action="store_true", help="Compute gaps only; do not write or retrain")
    parser.add_argument(
        "--stage-retrain",
        dest="stage_retrain",
        action="store_true",
        default=True,
        help="Stage a v2 retrain when gates allow (default: true).",
    )
    parser.add_argument(
        "--no-stage-retrain",
        dest="stage_retrain",
        action="store_false",
        help="Skip staged retraining even if gates allow.",
    )
    parser.add_argument("--source", default="manual", help="Invoker label for logging")
    return parser.parse_args()


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def format_date(value: date) -> str:
    return value.isoformat()


def default_window() -> tuple[date, date]:
    yesterday = datetime.now().date() - timedelta(days=1)
    return yesterday - timedelta(days=7), yesterday


def iter_dates(start_date: date, end_date: date) -> list[str]:
    current = start_date
    dates: list[str] = []
    while current <= end_date:
        dates.append(format_date(current))
        current += timedelta(days=1)
    return dates


def get_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL not configured")
    return database_url


def get_connection():
    return psycopg2.connect(get_database_url())


def ensure_runs_dirs() -> None:
    RUN_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    temp_path.replace(path)


def is_process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock(run_id: str, source: str) -> dict[str, Any] | None:
    ensure_runs_dirs()

    if ACTIVE_LOCK_PATH.exists():
        try:
            existing = json.loads(ACTIVE_LOCK_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = None

        if existing and is_process_alive(existing.get("pid")):
            return existing

        try:
            ACTIVE_LOCK_PATH.unlink()
        except FileNotFoundError:
            pass

    payload = {
        "runId": run_id,
        "source": source,
        "pid": os.getpid(),
        "startedAt": datetime.now().isoformat(),
    }

    fd = os.open(str(ACTIVE_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
        json.dump(payload, lock_file, indent=2)
    return None


def release_lock(run_id: str) -> None:
    if not ACTIVE_LOCK_PATH.exists():
        return
    try:
        payload = json.loads(ACTIVE_LOCK_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        ACTIVE_LOCK_PATH.unlink(missing_ok=True)
        return
    if payload.get("runId") == run_id:
        ACTIVE_LOCK_PATH.unlink(missing_ok=True)


def build_track_filter(raw_tracks: list[str] | None) -> tuple[list[str], set[str] | None, list[str]]:
    if not raw_tracks:
        return [], None, []

    warnings: list[str] = []
    canonical_tracks: list[str] = []
    normalized_keys: set[str] = set()

    for raw in raw_tracks:
        resolution = resolve_track(raw)
        canonical = resolution["canonical_track"] or str(raw)
        if resolution["normalized_key"]:
            normalized_keys.add(resolution["normalized_key"])
        if canonical and canonical not in canonical_tracks:
            canonical_tracks.append(canonical)
        if not resolution["known"]:
            warnings.append(
                f'Track "{raw}" is not mapped to a sectional collector; treating it as results_only.'
            )

    return canonical_tracks, normalized_keys or None, warnings


def normalize_track_scope(track: str, selected_track_keys: set[str] | None) -> tuple[dict[str, Any], bool]:
    resolution = resolve_track(track)
    in_scope = True
    if selected_track_keys is not None and resolution["normalized_key"] not in selected_track_keys:
        in_scope = False
    return resolution, in_scope


def fetch_prediction_audit_rows(
    conn,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                race_date::date AS race_date,
                track,
                COUNT(*) AS predicted_rows,
                COUNT(*) FILTER (
                    WHERE COALESCE(result_status, 'pending') <> 'resulted'
                       OR actual_position IS NULL
                ) AS pending_rows,
                COUNT(*) FILTER (
                    WHERE COALESCE(result_status, 'pending') = 'resulted'
                       OR actual_position IS NOT NULL
                ) AS resulted_rows,
                COUNT(DISTINCT race_number) AS predicted_races,
                COUNT(DISTINCT race_number) FILTER (
                    WHERE COALESCE(result_status, 'pending') = 'resulted'
                       OR actual_position IS NOT NULL
                ) AS resulted_races
            FROM prediction_audit
            WHERE race_date::date BETWEEN %s::date AND %s::date
            GROUP BY 1, 2
            ORDER BY 1, 2
            """,
            (start_date, end_date),
        )
        return [dict(row) for row in cur.fetchall()]


def fetch_rrh_rows(
    conn,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                race_date::date AS race_date,
                track,
                COUNT(*) AS rrh_rows,
                COUNT(DISTINCT race_number) AS rrh_races
            FROM race_results_history
            WHERE race_date::date BETWEEN %s::date AND %s::date
            GROUP BY 1, 2
            ORDER BY 1, 2
            """,
            (start_date, end_date),
        )
        return [dict(row) for row in cur.fetchall()]


def fetch_sectional_rows(
    conn,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                race_date::date AS race_date,
                track,
                source,
                COUNT(*) AS rows_count,
                COUNT(*) FILTER (WHERE race_results_history_id IS NOT NULL) AS matched_rows
            FROM sectional_times
            WHERE race_date::date BETWEEN %s::date AND %s::date
            GROUP BY 1, 2, 3
            ORDER BY 1, 2, 3
            """,
            (start_date, end_date),
        )
        return [dict(row) for row in cur.fetchall()]


def build_gap_plan(
    conn,
    start_date: str,
    end_date: str,
    selected_track_keys: set[str] | None,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    pa_rows = fetch_prediction_audit_rows(conn, start_date, end_date)
    rrh_rows = fetch_rrh_rows(conn, start_date, end_date)
    sectional_rows = fetch_sectional_rows(conn, start_date, end_date)

    pa_tracks: dict[tuple[str, str], dict[str, Any]] = {}
    rrh_tracks: dict[tuple[str, str], dict[str, Any]] = {}
    sectionals_lookup: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"rows": 0, "matched_rows": 0}
    )

    result_gap_dates: set[str] = set()

    for row in pa_rows:
        race_date = str(row["race_date"])
        resolution, in_scope = normalize_track_scope(str(row["track"]), selected_track_keys)
        if not in_scope:
            continue

        key = (race_date, resolution["normalized_key"])
        pa_tracks[key] = {
            "date": race_date,
            "track": resolution["canonical_track"],
            "source": resolution["source"],
            "predictedRows": int(row["predicted_rows"] or 0),
            "pendingRows": int(row["pending_rows"] or 0),
            "resultedRows": int(row["resulted_rows"] or 0),
            "predictedRaces": int(row["predicted_races"] or 0),
            "resultedRaces": int(row["resulted_races"] or 0),
        }

        if row["predicted_races"] and (
            int(row["pending_rows"] or 0) > 0
            or int(row["resulted_races"] or 0) == 0
            or int(row["resulted_races"] or 0) < int(row["predicted_races"] or 0)
        ):
            result_gap_dates.add(race_date)

        if not resolution["known"] and resolution["canonical_track"]:
            warnings.append(
                f'No sectional collector mapping found for "{resolution["canonical_track"]}"; treating it as results_only.'
            )

    for row in rrh_rows:
        race_date = str(row["race_date"])
        resolution, in_scope = normalize_track_scope(str(row["track"]), selected_track_keys)
        if not in_scope:
            continue
        key = (race_date, resolution["normalized_key"])
        rrh_tracks[key] = {
            "date": race_date,
            "track": resolution["canonical_track"],
            "source": resolution["source"],
            "rrhRows": int(row["rrh_rows"] or 0),
            "rrhRaces": int(row["rrh_races"] or 0),
        }
        if not resolution["known"] and resolution["canonical_track"]:
            warnings.append(
                f'No sectional collector mapping found for "{resolution["canonical_track"]}"; treating it as results_only.'
            )

    for row in sectional_rows:
        race_date = str(row["race_date"])
        resolution, in_scope = normalize_track_scope(str(row["track"]), selected_track_keys)
        if not in_scope:
            continue
        lookup_key = (race_date, resolution["normalized_key"], str(row["source"]))
        sectionals_lookup[lookup_key]["rows"] += int(row["rows_count"] or 0)
        sectionals_lookup[lookup_key]["matched_rows"] += int(row["matched_rows"] or 0)

    sectional_gap_targets: list[dict[str, Any]] = []
    sectional_seen: set[tuple[str, str, str]] = set()

    candidate_keys = set(pa_tracks.keys()) | set(rrh_tracks.keys())
    for date_key, track_key in sorted(candidate_keys):
        pa_stats = pa_tracks.get((date_key, track_key))
        rrh_stats = rrh_tracks.get((date_key, track_key))
        seed = rrh_stats or pa_stats
        if not seed:
            continue
        source = seed["source"]
        if source == "results_only":
            continue

        existing = sectionals_lookup.get((date_key, track_key, source), {"rows": 0, "matched_rows": 0})
        if existing["rows"] > 0 and existing["matched_rows"] > 0:
            continue

        reason = "missing_sectionals" if existing["rows"] == 0 else "unmatched_sectionals"
        dedupe_key = (date_key, track_key, source)
        if dedupe_key in sectional_seen:
            continue
        sectional_seen.add(dedupe_key)
        sectional_gap_targets.append(
            {
                "date": date_key,
                "track": seed["track"],
                "source": source,
                "reason": reason,
                "rrhRows": int((rrh_stats or {}).get("rrhRows") or 0),
                "rrhRaces": int((rrh_stats or {}).get("rrhRaces") or 0),
                "predictedRaces": int((pa_stats or {}).get("predictedRaces") or 0),
                "existingRows": int(existing["rows"] or 0),
                "matchedRows": int(existing["matched_rows"] or 0),
            }
        )

    warnings = list(dict.fromkeys(warnings))
    plan = {
        "dateRange": {"startDate": start_date, "endDate": end_date},
        "resultGapDates": sorted(result_gap_dates),
        "sectionalGapTargets": sectional_gap_targets,
    }
    return plan, warnings


def count_rrh_rows(conn, date_str: str, selected_track_keys: set[str] | None = None) -> int:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT track, COUNT(*) AS rows_count
            FROM race_results_history
            WHERE race_date::date = %s::date
            GROUP BY track
            """,
            (date_str,),
        )
        total = 0
        for row in cur.fetchall():
            resolution, in_scope = normalize_track_scope(str(row["track"]), selected_track_keys)
            if not in_scope:
                continue
            if selected_track_keys is None or resolution["normalized_key"] in selected_track_keys:
                total += int(row["rows_count"] or 0)
        return total


def count_sectional_rows_for_track(conn, date_str: str, track: str, source: str) -> tuple[int, int]:
    target_key = normalize_track_key(track)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT track, COUNT(*) AS rows_count,
                   COUNT(*) FILTER (WHERE race_results_history_id IS NOT NULL) AS matched_rows
            FROM sectional_times
            WHERE race_date::date = %s::date
              AND source = %s
            GROUP BY track
            """,
            (date_str, source),
        )
        rows_count = 0
        matched_rows = 0
        for row in cur.fetchall():
            if normalize_track_key(str(row["track"])) != target_key:
                continue
            rows_count += int(row["rows_count"] or 0)
            matched_rows += int(row["matched_rows"] or 0)
        return rows_count, matched_rows


def capture_noisy_call(func, *args, **kwargs):
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
        result = func(*args, **kwargs)
    return result, stdout_buffer.getvalue(), stderr_buffer.getvalue()


def filter_meetings_for_tracks(meetings: Iterable[dict[str, Any]], track_keys: set[str] | None) -> list[dict[str, Any]]:
    if track_keys is None:
        return list(meetings)

    selected: list[dict[str, Any]] = []
    for meeting in meetings:
        track = meeting.get("course") or meeting.get("track") or ""
        if normalize_track_key(track) in track_keys:
            selected.append(meeting)
    return selected


def import_results_history_for_date(date_str: str, selected_track_keys: set[str] | None) -> dict[str, Any]:
    meetings = fetch_date(date_str, metro_only=False)
    selected_meetings = filter_meetings_for_tracks(meetings, selected_track_keys)

    if not selected_meetings:
        return {
            "status": "skipped",
            "rowsImported": 0,
            "meetingsFetched": len(meetings),
            "meetingsSelected": 0,
            "warning": f"No race_results_history meetings matched the requested track scope for {date_str}.",
        }

    conn = get_connection()
    try:
        before_rows = count_rrh_rows(conn, date_str, selected_track_keys)
        rows_imported, skipped_races, skipped_runners = import_meetings(selected_meetings, conn)
        conn.commit()
        after_rows = count_rrh_rows(conn, date_str, selected_track_keys)
    finally:
        conn.close()

    return {
        "status": "success",
        "rowsImported": max(after_rows - before_rows, 0),
        "rawRowsImported": int(rows_imported or 0),
        "skippedRaces": int(skipped_races or 0),
        "skippedRunners": int(skipped_runners or 0),
        "meetingsFetched": len(meetings),
        "meetingsSelected": len(selected_meetings),
    }


def reconcile_tip_results(dates: list[str]) -> dict[str, Any]:
    if not dates:
        return {"status": "skipped", "rowsUpserted": 0}

    conn = get_connection()
    try:
        ensure_table(conn)
        tipped_races = find_tipped_races(dates)
        if not tipped_races:
            conn.commit()
            return {"status": "skipped", "rowsUpserted": 0}
        results = score_tip_accuracy(conn, tipped_races)
        conn.commit()
        return {"status": "success", "rowsUpserted": len(results)}
    finally:
        conn.close()


def run_retrain(staged_model_path: Path) -> tuple[bool, str]:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "retrain_v2.py"),
        "--output-path",
        str(staged_model_path),
    ]
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    combined_output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    return result.returncode == 0 and staged_model_path.exists(), combined_output


def initial_summary(
    run_id: str,
    args: argparse.Namespace,
    start_date: str,
    end_date: str,
    requested_tracks: list[str],
) -> dict[str, Any]:
    return {
        "runId": run_id,
        "status": "running",
        "source": args.source,
        "dryRun": bool(args.dry_run),
        "stageRetrainRequested": bool(args.stage_retrain),
        "requested": {
            "startDate": start_date,
            "endDate": end_date,
            "tracks": requested_tracks,
        },
        "workingDirectory": str(PROJECT_ROOT),
        "startedAt": datetime.now().isoformat(),
        "warnings": [],
        "plan": {
            "dateRange": {"startDate": start_date, "endDate": end_date},
            "resultGapDates": [],
            "sectionalGapTargets": [],
            "executedSectionalTargets": [],
        },
        "gates": {
            "resultsDelta": 0,
            "sectionalsDelta": 0,
            "refreshedTrainingView": False,
            "retrainEligible": False,
        },
        "results": {
            "status": "skipped",
            "updatedRows": 0,
            "projectedSelectionResults": 0,
            "projectedTrainingRows": 0,
            "raceResultsHistoryRows": 0,
            "perDate": [],
        },
        "sectionals": {
            "status": "skipped",
            "bySource": {},
            "byTrack": {},
            "warnings": [],
        },
        "tipReconciliation": {
            "status": "skipped",
            "rowsUpserted": 0,
            "error": None,
        },
        "trainingView": {
            "status": "skipped",
            "rows": None,
            "error": None,
        },
        "retrain": {
            "status": "skipped",
            "stagedModelPath": None,
            "error": None,
        },
    }


def finalize_status(summary: dict[str, Any]) -> str:
    if summary["status"] == "active":
        return "active"
    if summary.get("dryRun"):
        return "dry_run"
    if summary["trainingView"]["status"] == "failed" or summary["retrain"]["status"] == "failed":
        return "failed"
    if summary["results"]["status"] in {"failed", "partial"}:
        return "warning"
    if summary["sectionals"]["status"] in {"failed", "partial"}:
        return "warning"
    if summary["warnings"]:
        return "warning"
    return "success"


def persist_summary(summary: dict[str, Any]) -> None:
    ensure_runs_dirs()
    summary_path = RUN_HISTORY_DIR / f"{summary['runId']}.json"
    atomic_write_json(summary_path, summary)
    atomic_write_json(
        LATEST_POINTER_PATH,
        {
            "runId": summary["runId"],
            "path": str(summary_path),
            "updatedAt": datetime.now().isoformat(),
        },
    )


def main() -> int:
    args = parse_args()
    if args.start_date:
        start_date = parse_iso_date(args.start_date)
    else:
        start_date, _ = default_window()
    if args.end_date:
        end_date = parse_iso_date(args.end_date)
    else:
        _, end_date = default_window()
    if end_date < start_date:
        raise SystemExit("end-date must be on or after start-date")

    raw_tracks = [track.strip() for track in (args.tracks or "").split(",") if track.strip()]
    requested_tracks, selected_track_keys, track_warnings = build_track_filter(raw_tracks)
    run_id = uuid.uuid4().hex
    summary = initial_summary(
        run_id=run_id,
        args=args,
        start_date=format_date(start_date),
        end_date=format_date(end_date),
        requested_tracks=requested_tracks,
    )
    summary["warnings"].extend(track_warnings)

    if not args.dry_run:
        active_run = acquire_lock(run_id, args.source)
        if active_run:
            print(
                json.dumps(
                    {
                        "runId": active_run.get("runId"),
                        "status": "active",
                        "warnings": [
                            f"Another learn-from-results run is already active ({active_run.get('runId')})."
                        ],
                        "activeRun": active_run,
                    }
                )
            )
            return 0

    try:
        conn = get_connection()
        try:
            plan, plan_warnings = build_gap_plan(
                conn,
                format_date(start_date),
                format_date(end_date),
                selected_track_keys,
            )
        finally:
            conn.close()

        summary["plan"]["dateRange"] = plan["dateRange"]
        summary["plan"]["resultGapDates"] = plan["resultGapDates"]
        summary["plan"]["sectionalGapTargets"] = plan["sectionalGapTargets"]
        summary["warnings"].extend(plan_warnings)

        if args.dry_run:
            summary["results"]["status"] = "planned" if plan["resultGapDates"] else "skipped"
            summary["sectionals"]["status"] = "planned" if plan["sectionalGapTargets"] else "skipped"
            summary["status"] = finalize_status(summary)
            summary["finishedAt"] = datetime.now().isoformat()
            print(json.dumps(summary))
            return 0

        result_failures = 0
        results_executed = False

        for date_str in plan["resultGapDates"]:
            results_executed = True
            per_date: dict[str, Any] = {"date": date_str}
            try:
                canonical_result, _, canonical_stderr = capture_noisy_call(collect_results_for_date, date_str)
                rrh_import, _, rrh_stderr = capture_noisy_call(
                    import_results_history_for_date,
                    date_str,
                    selected_track_keys,
                )
                per_date.update(
                    {
                        "canonical": canonical_result,
                        "raceResultsHistory": rrh_import,
                    }
                )
                if canonical_stderr.strip():
                    per_date["canonicalLog"] = canonical_stderr.strip()[-1500:]
                if rrh_stderr.strip():
                    per_date["rrhLog"] = rrh_stderr.strip()[-1500:]

                summary["gates"]["resultsDelta"] += int(canonical_result.get("audit_rows_updated", 0))
                summary["results"]["updatedRows"] += int(canonical_result.get("audit_rows_updated", 0))
                summary["results"]["projectedSelectionResults"] += int(
                    canonical_result.get("selection_results_projected", 0)
                )
                summary["results"]["projectedTrainingRows"] += int(
                    canonical_result.get("training_rows_projected", 0)
                )
                summary["results"]["raceResultsHistoryRows"] += int(rrh_import.get("rowsImported", 0))

                if rrh_import.get("warning"):
                    summary["warnings"].append(rrh_import["warning"])
            except Exception as exc:
                result_failures += 1
                per_date["error"] = str(exc)
                per_date["traceback"] = traceback.format_exc()
                summary["warnings"].append(f"Results ingestion failed for {date_str}: {exc}")
            summary["results"]["perDate"].append(per_date)

        if results_executed:
            if result_failures == 0:
                summary["results"]["status"] = "success"
            elif result_failures < len(plan["resultGapDates"]):
                summary["results"]["status"] = "partial"
            else:
                summary["results"]["status"] = "failed"
        elif plan["resultGapDates"]:
            summary["results"]["status"] = "failed"
        else:
            summary["results"]["status"] = "skipped"

        if summary["gates"]["resultsDelta"] == 0 and results_executed:
            summary["warnings"].append(
                "Canonical results ingestion produced zero new official-result rows, so retraining will be skipped."
            )

        conn = get_connection()
        try:
            post_results_plan, post_results_warnings = build_gap_plan(
                conn,
                format_date(start_date),
                format_date(end_date),
                selected_track_keys,
            )
        finally:
            conn.close()
        summary["plan"]["sectionalGapTargets"] = post_results_plan["sectionalGapTargets"]
        summary["warnings"].extend(post_results_warnings)

        sectionals_by_source: dict[str, int] = defaultdict(int)
        sectionals_by_track: dict[str, dict[str, Any]] = {}
        sectional_failures = 0

        for target in post_results_plan["sectionalGapTargets"]:
            date_str = target["date"]
            track = target["track"]
            source = target["source"]
            track_key = f"{date_str}:{track}"
            executed_target = {
                "date": date_str,
                "track": track,
                "source": source,
                "reason": target["reason"],
            }
            try:
                conn = get_connection()
                try:
                    before_rows, before_matched = count_sectional_rows_for_track(
                        conn, date_str, track, source
                    )
                finally:
                    conn.close()
            except Exception:
                before_rows, before_matched = 0, 0

            try:
                if source == "nsw_pidata":
                    imported_result, _, collector_stderr = capture_noisy_call(
                        collect_nsw_sectionals,
                        date_filter=date_str,
                        venue_filter=track,
                    )
                    imported = int(imported_result or 0)
                elif source == "racing_qld":
                    qld_result, _, collector_stderr = capture_noisy_call(
                        collect_qld_sectionals,
                        date_str,
                        track,
                        get_database_url(),
                        False,
                    )
                    _, _, imported, _ = qld_result
                elif source == "racing_com":
                    imported_result, _, collector_stderr = capture_noisy_call(
                        collect_racing_com_sectionals,
                        date_str,
                        "VIC|SA",
                        get_database_url(),
                        False,
                        [track],
                    )
                    imported = int(imported_result or 0)
                else:
                    collector_stderr = ""
                    imported = 0

                conn = get_connection()
                try:
                    after_rows, after_matched = count_sectional_rows_for_track(conn, date_str, track, source)
                finally:
                    conn.close()

                delta = max(after_rows - before_rows, 0)
                matched_delta = max(after_matched - before_matched, 0)
                sectionals_by_source[source] += delta
                summary["gates"]["sectionalsDelta"] += delta
                sectionals_by_track[track_key] = {
                    "date": date_str,
                    "track": track,
                    "source": source,
                    "rowsImported": delta,
                    "matchedRowsImported": matched_delta,
                    "collectorReportedImported": imported,
                }
                if collector_stderr.strip():
                    sectionals_by_track[track_key]["collectorLog"] = collector_stderr.strip()[-1500:]
                executed_target["rowsImported"] = delta
                executed_target["matchedRowsImported"] = matched_delta
            except Exception as exc:
                sectional_failures += 1
                executed_target["error"] = str(exc)
                summary["sectionals"]["warnings"].append(
                    f"Sectional collection failed for {track} on {date_str} ({source}): {exc}"
                )
            summary["plan"]["executedSectionalTargets"].append(executed_target)

        if post_results_plan["sectionalGapTargets"]:
            if sectional_failures == 0:
                summary["sectionals"]["status"] = "success"
            elif sectional_failures < len(post_results_plan["sectionalGapTargets"]):
                summary["sectionals"]["status"] = "partial"
            else:
                summary["sectionals"]["status"] = "failed"
        else:
            summary["sectionals"]["status"] = "skipped"

        summary["sectionals"]["bySource"] = dict(sectionals_by_source)
        summary["sectionals"]["byTrack"] = sectionals_by_track

        tip_dates = sorted(
            set(plan["resultGapDates"])
            | {target["date"] for target in post_results_plan["sectionalGapTargets"]}
        )
        try:
            tip_summary, _, tip_stderr = capture_noisy_call(reconcile_tip_results, tip_dates)
            summary["tipReconciliation"].update(tip_summary)
            if tip_stderr.strip():
                summary["tipReconciliation"]["log"] = tip_stderr.strip()[-1500:]
        except Exception as exc:
            summary["tipReconciliation"]["status"] = "failed"
            summary["tipReconciliation"]["error"] = str(exc)
            summary["warnings"].append(f"Tip reconciliation failed: {exc}")

        should_refresh = (
            summary["gates"]["resultsDelta"] > 0 or summary["gates"]["sectionalsDelta"] > 0
        )
        if should_refresh:
            try:
                refresh_result = refresh_training_view_v2(get_database_url())
                summary["trainingView"]["status"] = "success"
                summary["trainingView"]["rows"] = int(refresh_result["rows"] or 0)
                summary["gates"]["refreshedTrainingView"] = True
            except Exception as exc:
                summary["trainingView"]["status"] = "failed"
                summary["trainingView"]["error"] = str(exc)
                summary["warnings"].append(f"training_view_v2 refresh failed: {exc}")
        else:
            summary["trainingView"]["status"] = "skipped"
            summary["warnings"].append(
                "No new results or sectionals were ingested, so training_view_v2 refresh was skipped."
            )

        retrain_eligible = (
            bool(args.stage_retrain)
            and summary["gates"]["resultsDelta"] > 0
            and summary["results"]["status"] == "success"
            and summary["trainingView"]["status"] == "success"
        )
        summary["gates"]["retrainEligible"] = retrain_eligible

        if retrain_eligible:
            staged_model_path = STAGING_DIR / (
                f"racing_ensemble_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{run_id[:8]}.pkl"
            )
            success, retrain_output = run_retrain(staged_model_path)
            if success:
                summary["retrain"]["status"] = "success"
                summary["retrain"]["stagedModelPath"] = str(staged_model_path)
            else:
                summary["retrain"]["status"] = "failed"
                summary["retrain"]["stagedModelPath"] = None
                summary["retrain"]["error"] = retrain_output or "retrain_v2.py failed"
        else:
            summary["retrain"]["status"] = "skipped"
            if not args.stage_retrain:
                summary["warnings"].append("Staged retraining was disabled for this run.")
            elif summary["gates"]["resultsDelta"] == 0:
                summary["warnings"].append(
                    "No new official-result rows were ingested, so staged retraining was skipped."
                )
            elif summary["trainingView"]["status"] != "success":
                summary["warnings"].append(
                    "training_view_v2 was not refreshed successfully, so staged retraining was skipped."
                )
            elif summary["results"]["status"] != "success":
                summary["warnings"].append(
                    "Results ingestion did not complete cleanly, so staged retraining was skipped."
                )

        summary["status"] = finalize_status(summary)
    except Exception as exc:
        summary["status"] = "failed"
        summary["warnings"].append(str(exc))
        summary["error"] = traceback.format_exc()
    finally:
        summary["warnings"] = list(dict.fromkeys(summary["warnings"]))
        summary["sectionals"]["warnings"] = list(dict.fromkeys(summary["sectionals"]["warnings"]))
        summary["finishedAt"] = datetime.now().isoformat()
        if not args.dry_run:
            try:
                persist_summary(summary)
            finally:
                release_lock(run_id)

    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
