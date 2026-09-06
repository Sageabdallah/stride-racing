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
import subprocess
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

SOURCE_API = "betfair_delayed"  # refined per pull by betfair_prices
# Capture as soon as the race is inside TARGET+EARLY_TOLERANCE of the jump;
# keep capturing (retry / late first sight) until POST_JUMP_GRACE after it.
TARGET_SECONDS = 300          # aim: T-5min
EARLY_TOLERANCE_SECONDS = 60  # first opportunity: T-6min
POST_JUMP_GRACE_SECONDS = 120 # give up 2min after the jump
API_SLEEP_SECONDS = 0.5       # rate-limit courtesy between market pulls


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


def load_schedule_from_db(date_str: str) -> List[Dict[str, Any]]:
    """Fallback: schedule from race_schedule (seeded by the tips pipeline)."""
    try:
        conn = _connect()
    except Exception as e:
        print(f"  [LATE_ODDS] DB connect failed for schedule: {e}", file=sys.stderr)
        return []
    if conn is None:
        return []
    schedule = []
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT track, race_number, off_time FROM race_schedule "
            "WHERE race_date = %s ORDER BY track, race_number", (date_str,))
        for track, race_number, off_time in cur.fetchall():
            jump = parse_timestamp(off_time)
            if jump is None or race_number is None:
                continue
            schedule.append({"track": track, "meet_id": None,
                             "race_number": int(race_number),
                             "jump_time": jump})
        cur.close()
    except Exception as e:
        print(f"  [LATE_ODDS] race_schedule read failed: {e}", file=sys.stderr)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return schedule


def load_schedule(date_str: str) -> List[Dict[str, Any]]:
    schedule = load_schedule_from_racecard(date_str)
    if schedule:
        return schedule
    return load_schedule_from_db(date_str)


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


def fetch_race_market(date_str: str, race: Dict[str, Any]):
    """(runners, source_api) for one race, priced from Betfair.

    Runners come back in the racecard odds shape build_snapshot_rows already
    consumes: {"horse": name, "odds": [{"bookmaker": "betfair",
    "win_odds": price}]}. Direct Exchange when credentials work on this
    machine, freshest runner_odds_snapshots rows otherwise.
    """
    import betfair_prices
    try:
        price_map = betfair_prices.fetch_price_map(date_str)
    except Exception as e:
        print(f"  [LATE_ODDS] price fetch failed: {e}", file=sys.stderr)
        return [], SOURCE_API
    import betfair_markets
    key = (betfair_markets.norm_track(race.get("track")),
           int(race.get("race_number") or 0))
    market = price_map["races"].get(key)
    if not market:
        return [], price_map["source"]
    runners = [{"horse": q["horse"],
                "odds": [{"bookmaker": "betfair", "win_odds": q["price"]}]}
               for q in market["runners"].values() if q.get("price")]
    return runners, price_map["source"]


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
        # A connect FAILURE is fatal: with races due, every one would log
        # "no DB — skipped" and the job would still exit 0 — the
        # silent-no-op class. _connect() returning None (no DATABASE_URL
        # configured) keeps the graceful no-DB degrade for dev hosts, and
        # --watch isolates each iteration so a raise here cannot kill the
        # day's watcher.
        conn = _connect()
        captured_ids = already_captured_race_ids(conn, date_str)

    summary = {"date": date_str, "scheduled": len(schedule), "due": len(due),
               "captured": 0, "rows": 0, "skipped_already_captured": 0,
               "races": []}
    persist_attempts = 0
    try:
        for race in due:
            race_id = make_race_id(date_str, race["track"], race["race_number"],
                                   race.get("meet_id"))
            label = f"{race['track']} R{race['race_number']}"
            if race_id in captured_ids:
                summary["skipped_already_captured"] += 1
                continue

            captured_at = datetime.now(timezone.utc)
            stj = compute_seconds_to_jump(race["jump_time"], captured_at)
            runners, pull_source = fetch_race_market(date_str, race)
            if not runners:
                print(f"  [LATE_ODDS] {label}: market pull failed — will retry "
                      f"next run (T{stj:+d}s)", file=sys.stderr)
                continue

            rows = build_snapshot_rows(
                race_date=date_str, track=race["track"],
                race_number=race["race_number"], runners=runners,
                snapshot_kind="late_t5", captured_at=captured_at,
                source_api=pull_source, meet_id=race.get("meet_id"),
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

            persist_attempts += 1
            result = persist_rows(conn, rows)
            if result["written"] > 0:
                summary["captured"] += 1
                summary["rows"] += result["written"]
                captured_ids.add(race_id)
                print(f"  [LATE_ODDS] {label}: {result['written']} rows at "
                      f"T{stj:+d}s", file=sys.stderr)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # persist_rows never raises by design; without this, a dead connection
    # meant every race reported "written: 0" and the job still exited 0.
    if persist_attempts and summary["rows"] == 0:
        raise RuntimeError(
            f"built rows for {persist_attempts} race(s) but wrote 0 — "
            f"persist is failing (see [ODDS_SNAP] errors above)")

    return summary


WATCH_INTERVAL_SECONDS = 120   # poll cadence in --watch mode (matches the 1-2 min design)
WATCH_MAX_HOURS = 14.0         # safety cap so a stray watcher can never run forever


def _watch_pid_path(date_str: str) -> Path:
    return Path(__file__).resolve().parent / "logs" / f"late_odds_{date_str}.pid"


# --- watcher identity -------------------------------------------------------
#
# A pidfile holding only a pid answers "is there any process with this
# number", not "is the watcher that wrote this file still running". Pids are
# recycled. A crashed watcher leaves its file behind, and once the OS hands
# that number to an unrelated process the day's capture never starts: exit 0,
# nothing written (issue #168, the silent no-op class). So the file carries a
# second line, the start time of the process that wrote it on the platform's
# own clock. Two processes can share a pid across time; they cannot share a
# start time.

_WIN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WIN_STILL_ACTIVE = 259
_WIN_ERROR_ACCESS_DENIED = 5


def _win_kernel32():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetExitCodeProcess.restype = wintypes.BOOL
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetProcessTimes.restype = wintypes.BOOL
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return ctypes, wintypes, k32


def _win_process_state(pid: int):
    """(exists, start_token) for ``pid`` through the Win32 API.

    ``os.kill(pid, 0)`` is not a liveness probe on Windows. Zero is
    CTRL_C_EVENT; when GenerateConsoleCtrlEvent cannot deliver it, CPython's
    os_kill_impl falls through to OpenProcess + TerminateProcess
    (Modules/posixmodule.c). Probing a live watcher that way interrupts or
    kills the process the probe was meant to find.
    """
    ctypes, wintypes, k32 = _win_kernel32()
    handle = k32.OpenProcess(_WIN_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Access denied: it exists, we just cannot read it. Anything else
        # (invalid parameter is the usual one): no such process.
        return ctypes.get_last_error() == _WIN_ERROR_ACCESS_DENIED, None
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True, None
        if code.value != _WIN_STILL_ACTIVE:
            return False, None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return True, None
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return True, f"win-filetime:{created}"
    finally:
        k32.CloseHandle(handle)


def _win_pid_listed(pid: int) -> bool:
    """Last resort if the Win32 calls fail: tasklist, matched on the pid as a
    whole token so the (localised) prose around it does not matter."""
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True, timeout=10)
    return any(tok == str(pid) for tok in out.stdout.split())


def _linux_start_token(pid: int) -> str:
    with open(f"/proc/{pid}/stat", "rb") as fh:
        stat = fh.read().decode("ascii", "replace")
    # comm (field 2) may hold spaces or parentheses, so split after the LAST
    # ')' — what remains starts at field 3; starttime is field 22, in clock
    # ticks since boot, fixed for the life of the process.
    fields = stat[stat.rindex(")") + 2:].split()
    return f"linux-starttime:{fields[19]}"


def _ps_start_token(pid: int) -> Optional[str]:
    # macOS and the BSDs: ps prints the start time, nothing for a dead pid.
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                         capture_output=True, text=True, timeout=5)
    text = " ".join(out.stdout.split())
    return f"ps-lstart:{text}" if text else None


def _posix_start_token(pid: int) -> Optional[str]:
    if sys.platform.startswith("linux"):
        return _linux_start_token(pid)
    return _ps_start_token(pid)


def _start_token(pid: int) -> Optional[str]:
    """An identity for this incarnation of ``pid``: an opaque string that is
    stable for the life of one process and differs across pid reuse. None
    when the process is gone or the platform cannot say — never a verdict."""
    try:
        if sys.platform == "win32":
            return _win_process_state(pid)[1]
        return _posix_start_token(pid)
    except Exception:
        return None


def _pid_exists(pid: int) -> bool:
    """Does any process with this number exist right now? Read-only on every
    platform: never a signal that could reach the process being asked about."""
    if sys.platform == "win32":
        try:
            return _win_process_state(pid)[0]
        except Exception:
            try:
                return _win_pid_listed(pid)
            except Exception:
                return True  # cannot tell: treat the lock as held, as before
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:  # not ours, but definitely there
        return True
    except OSError:
        return False


def _lock_holder_alive(pid: int, recorded_token: Optional[str]) -> bool:
    """Is the watcher that wrote (pid, token) still running?

    Existence of the pid is necessary. The token can only ever downgrade an
    "exists" verdict: when both sides are known and differ, the number lives
    on but the watcher that wrote it does not. With no token on either side
    (a pre-#168 pidfile, or a platform that cannot say) the answer is the old
    one, so nothing gets reclaimed on less evidence than before.
    """
    if not _pid_exists(pid):
        return False
    live_token = _start_token(pid)
    if recorded_token and live_token and live_token != recorded_token:
        return False
    return True


def _read_lock(pid_path: Path):
    """(pid, start_token) from the pidfile; (None, None) when absent or corrupt."""
    try:
        lines = pid_path.read_text().splitlines()
        pid = int(lines[0].strip())
    except (FileNotFoundError, ValueError, IndexError, OSError):
        return None, None
    token = lines[1].strip() if len(lines) > 1 and lines[1].strip() else None
    return pid, token


def _write_lock(pid_path: Path, pid: int, token: Optional[str]) -> None:
    # Line 1 stays the bare pid so `head -1 <pidfile>` still names the process
    # to stop (DEPLOY_RUNBOOK); the start token rides on line 2.
    pid_path.write_text(f"{pid}\n" + (f"{token}\n" if token else ""))


def acquire_watch_lock(date_str: str) -> bool:
    """One watcher per date, whichever path launched it.

    The tips pipeline self-launches a watcher and run_full_pipeline launches
    one too; whichever starts second must no-op rather than double-poll the
    market API all day. A pidfile with a liveness probe is enough here — a
    stale file from a crashed watcher is reclaimed, a live one wins. "Live"
    means the process that wrote the file, not merely its pid number: see
    _lock_holder_alive.
    """
    pid_path = _watch_pid_path(date_str)
    existing, recorded_token = _read_lock(pid_path)
    if existing is not None and _lock_holder_alive(existing, recorded_token):
        print(f"[LATE_ODDS][watch] another watcher (pid {existing}) already "
              f"covers {date_str} — exiting", file=sys.stderr)
        return False
    try:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        _write_lock(pid_path, os.getpid(), _start_token(os.getpid()))
    except OSError as e:  # the lock is best-effort; capturing beats crashing
        print(f"[LATE_ODDS][watch] could not write pidfile (continuing): {e}",
              file=sys.stderr)
    return True


def release_watch_lock(date_str: str) -> None:
    """Remove our pidfile; never remove another watcher's lock — not even one
    written under our own pid by an earlier incarnation of it."""
    pid_path = _watch_pid_path(date_str)
    pid, recorded_token = _read_lock(pid_path)
    if pid != os.getpid():
        return
    own_token = _start_token(os.getpid())
    if recorded_token and own_token and recorded_token != own_token:
        return
    try:
        pid_path.unlink()
    except OSError:
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
