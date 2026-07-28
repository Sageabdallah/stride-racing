#!/usr/bin/env python3
"""Late (T-5min) odds snapshot capture — ROI roadmap task 04.

For each race today, when the jump is ~5 minutes away, pull the current
market via the existing Racing API client and insert ``late_t5`` rows into
``runner_odds_snapshots`` with the ACTUAL ``captured_at`` and
``seconds_to_jump`` — if the T-5 moment is missed (scheduler lag, API
error), the capture still lands as T-3 or T-10 and records the truth.
Honesty over coverage.

Designed to be called by the scheduler every 1-2 minutes during racing
hours; each run captures the races that are currently inside the capture
window and have no ``late_t5`` rows yet, then exits.

    python capture_late_odds.py --date 2026-03-28            # capture due races
    python capture_late_odds.py --date 2026-03-28 --dry-run  # show what would land

No database configured => every write is a graceful no-op (rows are still
built and reported). Writing is controlled by STRIDE_ODDS_SNAPSHOT_WRITE
(default ON; set 0/false/off to disable).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from odds_snapshots import (
    build_snapshot_rows,
    compute_seconds_to_jump,
    make_race_id,
    parse_timestamp,
    persist_rows,
    snapshot_write_enabled,
    _connect,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RACECARDS_DIR = PROJECT_ROOT / "racecards"

SOURCE_API = "theracingapi"
# Capture as soon as the race is inside TARGET+EARLY_TOLERANCE of the jump;
# keep capturing (retry / late first sight) until POST_JUMP_GRACE after it.
TARGET_SECONDS = 300          # aim: T-5min
EARLY_TOLERANCE_SECONDS = 60  # first opportunity: T-6min
POST_JUMP_GRACE_SECONDS = 120 # give up 2min after the jump
API_SLEEP_SECONDS = 0.5       # rate-limit courtesy between market pulls


def _api_get(endpoint, params=None):
    """Reuse the existing Racing API client (session, retries, auth).

    Returns None when the client stack can't even be imported (e.g. no DB
    driver in a stripped-down environment) — the caller treats that as a
    failed pull and simply retries next run."""
    try:
        from odds_movement import api_get
    except Exception as e:
        print(f"  [LATE_ODDS] API client unavailable: {e}", file=sys.stderr)
        return None
    return api_get(endpoint, params=params)


def _is_trial(race: Dict[str, Any]) -> bool:
    if race.get("is_trial") or race.get("is_jump_out"):
        return True
    text = f"{race.get('class') or ''} {race.get('race_name') or ''}".upper()
    return "TRIAL" in text or "JUMP OUT" in text or "JUMPOUT" in text or " BT" in text


def load_schedule_from_racecard(date_str: str) -> List[Dict[str, Any]]:
    """Race schedule from the cached racecard file, if present."""
    path = RACECARDS_DIR / f"racecard_{date_str}.json"
    if not path.exists():
        return []
    meetings = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(meetings, dict):
        meetings = [meetings]
    schedule = []
    for meet in meetings:
        course = meet.get("course") or meet.get("track") or ""
        meet_id = meet.get("meet_id")
        for race in meet.get("races", []) or []:
            if _is_trial(race):
                continue
            jump = parse_timestamp(race.get("off_time") or race.get("offTime"))
            try:
                race_number = int(race.get("race_number"))
            except (TypeError, ValueError):
                continue
            if jump is None:
                continue
            schedule.append({
                "track": course,
                "meet_id": race.get("meet_id") or meet_id,
                "race_number": race_number,
                "jump_time": jump,
            })
    return schedule


def load_schedule_from_api(date_str: str) -> List[Dict[str, Any]]:
    """Fallback: schedule straight from the Racing API (no cached racecard)."""
    meets = _api_get("/v1/australia/meets", {"date": date_str})
    if not meets:
        return []
    meets_list = meets.get("meets", meets) if isinstance(meets, dict) else meets
    if not isinstance(meets_list, list):
        return []
    schedule = []
    for meet in meets_list:
        meet_id = meet.get("meet_id") or meet.get("id")
        course = meet.get("course") or meet.get("track") or ""
        if not meet_id:
            continue
        races_resp = _api_get(f"/v1/australia/meets/{meet_id}/races")
        races_list = (races_resp.get("races", races_resp)
                      if isinstance(races_resp, dict) else races_resp)
        if not isinstance(races_list, list):
            continue
        for race in races_list:
            if _is_trial(race):
                continue
            jump = parse_timestamp(race.get("off_time") or race.get("offTime"))
            try:
                race_number = int(race.get("race_number") or race.get("number"))
            except (TypeError, ValueError):
                continue
            if jump is None:
                continue
            schedule.append({
                "track": course,
                "meet_id": meet_id,
                "race_number": race_number,
                "jump_time": jump,
            })
        time.sleep(API_SLEEP_SECONDS)
    return schedule


def load_schedule(date_str: str) -> List[Dict[str, Any]]:
    schedule = load_schedule_from_racecard(date_str)
    if schedule:
        return schedule
    return load_schedule_from_api(date_str)


def find_due_races(schedule: List[Dict[str, Any]], now: datetime,
                   target_seconds: int = TARGET_SECONDS,
                   early_tolerance: int = EARLY_TOLERANCE_SECONDS,
                   post_jump_grace: int = POST_JUMP_GRACE_SECONDS) -> List[Dict[str, Any]]:
    """Races inside the capture window at `now`.

    Window: [jump - (target + tolerance), jump + post_jump_grace]. The job is
    expected to run every 1-2 minutes, so the first sighting lands within a
    minute of T-5; a race first seen late (or whose earlier capture failed)
    is still captured with its actual offset recorded in seconds_to_jump.
    """
    due = []
    for race in schedule:
        stj = (now - race["jump_time"]).total_seconds()  # negative pre-jump
        if stj < -(target_seconds + early_tolerance):
            continue  # too early — wait for T-5
        if stj > post_jump_grace:
            continue  # window missed entirely
        due.append(race)
    return due


def fetch_race_market(meet_id: Any, race_number: int) -> List[Dict[str, Any]]:
    """Current per-bookmaker market for one race from the Racing API."""
    if not meet_id:
        return []
    details = _api_get(f"/v1/australia/meets/{meet_id}/races/{int(race_number)}")
    if not details:
        return []
    runners = details.get("runners", [])
    return runners if isinstance(runners, list) else []


def already_captured_race_ids(conn, date_str: str) -> set:
    """race_ids that already hold late_t5 rows for the date. On any DB
    failure, return an empty set — a duplicate capture is harmless (new
    captured_at, append-only), a skipped capture is lost data."""
    if conn is None:
        return set()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT race_id FROM runner_odds_snapshots "
            "WHERE race_date = %s AND snapshot_kind = 'late_t5'",
            (date_str,),
        )
        ids = {row[0] for row in cur.fetchall()}
        cur.close()
        return ids
    except Exception as e:
        print(f"  [LATE_ODDS] dedup check failed (non-fatal): {e}", file=sys.stderr)
        try:
            conn.rollback()
        except Exception:
            pass
        return set()


def run(date_str: str, dry_run: bool = False, now: Optional[datetime] = None,
        target_seconds: int = TARGET_SECONDS) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    schedule = load_schedule(date_str)
    if not schedule:
        print(f"[LATE_ODDS] No race schedule for {date_str}", file=sys.stderr)
        return {"date": date_str, "due": 0, "captured": 0, "rows": 0}

    due = find_due_races(schedule, now, target_seconds=target_seconds)
    print(f"[LATE_ODDS] {date_str}: {len(schedule)} races scheduled, "
          f"{len(due)} inside capture window", file=sys.stderr)
    if not due:
        return {"date": date_str, "scheduled": len(schedule), "due": 0,
                "captured": 0, "rows": 0}

    conn = None
    captured_ids: set = set()
    if not dry_run and snapshot_write_enabled():
        try:
            conn = _connect()
        except Exception as e:
            print(f"  [LATE_ODDS] DB connect failed (non-fatal): {e}", file=sys.stderr)
            conn = None
        captured_ids = already_captured_race_ids(conn, date_str)

    summary = {"date": date_str, "scheduled": len(schedule), "due": len(due),
               "captured": 0, "rows": 0, "skipped_already_captured": 0,
               "races": []}
    for race in due:
        race_id = make_race_id(date_str, race["track"], race["race_number"],
                               race.get("meet_id"))
        label = f"{race['track']} R{race['race_number']}"
        if race_id in captured_ids:
            summary["skipped_already_captured"] += 1
            continue

        captured_at = datetime.now(timezone.utc)
        stj = compute_seconds_to_jump(race["jump_time"], captured_at)
        runners = fetch_race_market(race["meet_id"], race["race_number"])
        time.sleep(API_SLEEP_SECONDS)
        if not runners:
            print(f"  [LATE_ODDS] {label}: market pull failed — will retry "
                  f"next run (T{stj:+d}s)", file=sys.stderr)
            continue

        rows = build_snapshot_rows(
            race_date=date_str, track=race["track"],
            race_number=race["race_number"], runners=runners,
            snapshot_kind="late_t5", captured_at=captured_at,
            source_api=SOURCE_API, meet_id=race.get("meet_id"),
            jump_time=race["jump_time"])
        if not rows:
            print(f"  [LATE_ODDS] {label}: no priced runners at T{stj:+d}s",
                  file=sys.stderr)
            continue

        if dry_run:
            print(f"  [DRY-RUN] {label}: would insert {len(rows)} late_t5 rows "
                  f"at T{stj:+d}s", file=sys.stderr)
            for row in rows[:3]:
                print(f"    {row['horse_name']}: {row['bookmaker']} "
                      f"${row['decimal_odds']:.2f}", file=sys.stderr)
            summary["captured"] += 1
            summary["rows"] += len(rows)
            continue

        if conn is None:
            print(f"  [LATE_ODDS] {label}: {len(rows)} rows built, no DB — skipped",
                  file=sys.stderr)
            continue

        result = persist_rows(conn, rows)
        if result["written"] > 0:
            summary["captured"] += 1
            summary["rows"] += result["written"]
            captured_ids.add(race_id)
            print(f"  [LATE_ODDS] {label}: {result['written']} rows at "
                  f"T{stj:+d}s", file=sys.stderr)

    return summary


WATCH_INTERVAL_SECONDS = 120   # poll cadence in --watch mode (matches the 1-2 min design)
WATCH_MAX_HOURS = 14.0         # safety cap so a stray watcher can never run forever


def _watch_pid_path(date_str: str) -> Path:
    return Path(__file__).resolve().parent / "logs" / f"late_odds_{date_str}.pid"


def acquire_watch_lock(date_str: str) -> bool:
    """One watcher per date, whichever path launched it.

    The tips pipeline self-launches a watcher and run_full_pipeline launches
    one too; whichever starts second must no-op rather than double-poll the
    market API all day. A pidfile with a liveness probe is enough here — a
    stale file from a crashed watcher is reclaimed, a live one wins.
    """
    pid_path = _watch_pid_path(date_str)
    existing = None
    try:
        existing = int(pid_path.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        pass  # no lock, or corrupt — reclaim
    if existing is not None:
        try:
            os.kill(existing, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:  # not ours, but definitely alive
            alive = True
        except OSError:
            alive = False
        if alive:
            print(f"[LATE_ODDS][watch] another watcher (pid {existing}) already "
                  f"covers {date_str} — exiting", file=sys.stderr)
            return False
    try:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()))
    except OSError as e:  # the lock is best-effort; capturing beats crashing
        print(f"[LATE_ODDS][watch] could not write pidfile (continuing): {e}",
              file=sys.stderr)
    return True


def release_watch_lock(date_str: str) -> None:
    """Remove our pidfile; never remove another live watcher's lock."""
    pid_path = _watch_pid_path(date_str)
    try:
        if int(pid_path.read_text().strip()) == os.getpid():
            pid_path.unlink()
    except (FileNotFoundError, ValueError, OSError):
        pass


def watch(date_str: str, interval: int = WATCH_INTERVAL_SECONDS,
          max_hours: float = WATCH_MAX_HOURS,
          target_seconds: int = TARGET_SECONDS) -> int:
    """Self-scheduling loop for hosts with no external cron.

    Calls ``run`` every ``interval`` seconds until every race for the day is past
    its post-jump grace (or ``max_hours`` elapses), then exits. Each iteration is
    isolated in try/except — a capture job must never crash mid-day and abandon
    the remaining races. Intended to be launched fire-and-forget by the daily
    pipeline; the existing one-shot mode still works for a cron-per-tick host.
    """
    if not acquire_watch_lock(date_str):
        return 0
    try:
        start = datetime.now(timezone.utc)
        deadline = start + timedelta(hours=max_hours)
        schedule = load_schedule(date_str)
        if not schedule:
            print(f"[LATE_ODDS][watch] no schedule for {date_str} — nothing to watch",
                  file=sys.stderr)
            return 0
        last_jump = max(r["jump_time"] for r in schedule)
        print(f"[LATE_ODDS][watch] watching {len(schedule)} races for {date_str}; "
              f"last jump {last_jump.isoformat()}, poll every {interval}s", file=sys.stderr)
        while True:
            now = datetime.now(timezone.utc)
            try:
                run(date_str, target_seconds=target_seconds, now=now)
            except Exception as e:  # never let one bad pull kill the day's capture
                print(f"[LATE_ODDS][watch] iteration failed (non-fatal): {e}", file=sys.stderr)
            now = datetime.now(timezone.utc)
            if now > last_jump + timedelta(seconds=POST_JUMP_GRACE_SECONDS):
                print("[LATE_ODDS][watch] all races past post-jump grace — exiting",
                      file=sys.stderr)
                return 0
            if now >= deadline:
                print(f"[LATE_ODDS][watch] {max_hours}h cap reached — exiting", file=sys.stderr)
                return 0
            time.sleep(interval)
    finally:
        release_watch_lock(date_str)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture late_t5 odds snapshots for races near their jump.")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                        help="Race date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build and report rows without writing to the DB")
    parser.add_argument("--target-seconds", type=int, default=TARGET_SECONDS,
                        help="Aim this many seconds before the jump (default: 300)")
    parser.add_argument("--watch", action="store_true",
                        help="Self-scheduling loop: capture each race near its jump "
                             "through the day, then exit (for hosts with no cron)")
    parser.add_argument("--interval", type=int, default=WATCH_INTERVAL_SECONDS,
                        help="Seconds between --watch iterations (default: 120)")
    parser.add_argument("--max-hours", type=float, default=WATCH_MAX_HOURS,
                        help="Safety cap on total --watch duration (default: 14)")
    args = parser.parse_args()

    if args.watch:
        return watch(args.date, interval=args.interval, max_hours=args.max_hours,
                     target_seconds=args.target_seconds)

    summary = run(args.date, dry_run=args.dry_run, target_seconds=args.target_seconds)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
