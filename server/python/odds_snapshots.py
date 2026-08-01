#!/usr/bin/env python3
"""As-of-prediction-time odds snapshot capture (ROI roadmap task 04).

The model trains on SP mapped into ``market_odds`` while live inference only
sees the morning racecard price. This module persists what the market
actually was at the moments that matter — tip time and ~T-5min — so the
honest retrain (task 12) and late-movement features (task 14) have data.

Design rules (mirrors selection_ledger.py):

  * Row building is pure and DB-free; persistence is the caller's decision.
  * Writing degrades to a no-op without a database — nothing here may ever
    delay or break the tips pipeline. Every failure path returns a count
    dict; nothing raises out of the persist/capture helpers.
  * One row per runner per bookmaker per capture. The first-bookmaker choice
    in ``run_tips_pipeline.extract_odds`` is a derived view, not stored data.
  * Prospective only: rows are created by live capture, never backfilled.

Writes are controlled by ``STRIDE_ODDS_SNAPSHOT_WRITE`` (default ON — this is
measurement-only and is a graceful no-op without a DB; set to 0/false/off to
disable). Rollback = stop the capture jobs; captured data is harmless.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SNAPSHOT_KINDS = ("tip_time", "late_t5", "morning", "baseline")
SOURCE_API = "theracingapi"

# Table/column contract — migrations/runner_odds_snapshots.sql
SNAPSHOT_COLUMNS = (
    "race_id", "runner_id", "snapshot_kind", "bookmaker", "captured_at",
    "decimal_odds", "source_api", "seconds_to_jump",
    "race_date", "track", "race_number", "horse_name", "horse_name_norm",
)

# Append-only: re-captures land under a new captured_at; a genuine collision
# (same instant, same bookmaker) is a duplicate, not an update.
SNAPSHOT_INSERT_SQL = (
    "INSERT INTO runner_odds_snapshots ({cols}) VALUES ({placeholders}) "
    "ON CONFLICT (race_id, runner_id, snapshot_kind, bookmaker, captured_at) "
    "DO NOTHING"
).format(cols=", ".join(SNAPSHOT_COLUMNS),
         placeholders=", ".join(["%s"] * len(SNAPSHOT_COLUMNS)))


def snapshot_write_enabled() -> bool:
    """STRIDE_ODDS_SNAPSHOT_WRITE — default ON, explicit off values disable."""
    return os.environ.get("STRIDE_ODDS_SNAPSHOT_WRITE", "true").strip().lower() not in (
        "0", "false", "no", "off")


def validate_snapshot_kind(kind: str) -> str:
    if kind not in SNAPSHOT_KINDS:
        raise ValueError(
            f"invalid snapshot_kind {kind!r} — must be one of {SNAPSHOT_KINDS}")
    return kind


def normalize_horse_name(name: Any) -> str:
    """Lowercase-alnum normalisation, matching stride_norm_name() in the DB."""
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def normalize_track_name(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def parse_decimal_odds(value: Any) -> Optional[float]:
    """Coerce an odds value ('$12.00', '12', 12.0) to a decimal price > 1."""
    if value is None:
        return None
    try:
        dec = float(str(value).replace("$", "").strip())
    except (ValueError, TypeError):
        return None
    return dec if dec > 1.0 else None


def extract_bookmaker_odds(runner: Dict[str, Any]) -> List[Tuple[str, float]]:
    """ALL (bookmaker, decimal_odds) quotes on a runner, deduped per book.

    The racecard contract is a list of {bookmaker, win_odds, place_odds}; be
    liberal about key names ('decimal', 'odds') and plain numeric entries
    (stored under bookmaker 'unknown'). SP/fallback keys on the runner are
    deliberately NOT used — snapshots record live-captured market quotes only.
    """
    out: List[Tuple[str, float]] = []
    seen = set()
    odds_arr = runner.get("odds", [])
    if isinstance(odds_arr, list):
        for entry in odds_arr:
            if isinstance(entry, dict):
                book = str(entry.get("bookmaker") or entry.get("book") or "unknown").strip() or "unknown"
                dec = None
                for key in ("win_odds", "decimal", "odds"):
                    dec = parse_decimal_odds(entry.get(key))
                    if dec is not None:
                        break
                if dec is not None and book not in seen:
                    seen.add(book)
                    out.append((book, dec))
            else:
                dec = parse_decimal_odds(entry)
                if dec is not None and "unknown" not in seen:
                    seen.add("unknown")
                    out.append(("unknown", dec))
    return out


def make_race_id(race_date: Any, track: Any, race_number: Any,
                 meet_id: Any = None) -> str:
    """Stable race identity. The racecard carries no API race id, so key on
    the (date, track, race_number) triple the rest of the schema uses; the
    API meet_id is prepended when present for cross-source joins."""
    rn = int(race_number)
    base = f"{race_date}|{normalize_track_name(track)}|R{rn}"
    return f"{meet_id}|{base}" if meet_id else base


def make_runner_id(runner: Dict[str, Any]) -> str:
    """horse_id when the API supplied one, else the normalised name."""
    hid = str(runner.get("horse_id") or "").strip()
    if hid:
        return hid
    return normalize_horse_name(runner.get("horse"))


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse ISO-ish timestamps ('2026-03-28T01:15:00.000Z' etc.) to an aware
    UTC datetime. Returns None rather than raising — capture must never fail
    on a missing off_time."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(value.strip(), fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    else:
        return None
    if dt.tzinfo is None:
        # Naive timestamps from this pipeline are UTC (API emits ...Z).
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utcnow() -> datetime:
    # Module-level so tests can pin the clock without patching datetime.
    return datetime.now(timezone.utc)


def compute_seconds_to_jump(jump_time: Any, captured_at: Any) -> Optional[int]:
    """Actual seconds between capture and scheduled jump. Negative = pre-jump
    (a T-5 capture records ~-300; a T-15 fallback honestly records ~-900)."""
    jump = parse_timestamp(jump_time)
    captured = parse_timestamp(captured_at)
    if jump is None or captured is None:
        return None
    return int(round((captured - jump).total_seconds()))


def build_snapshot_rows(*, race_date: Any, track: Any, race_number: Any,
                        runners: Sequence[Dict[str, Any]],
                        snapshot_kind: str,
                        captured_at: Any = None,
                        source_api: str = SOURCE_API,
                        meet_id: Any = None,
                        jump_time: Any = None) -> List[Dict[str, Any]]:
    """One row per runner per bookmaker for a single capture event.

    Pure and DB-free. Scratched runners are skipped (they have no live
    market); runners with no valid quotes produce no rows — coverage honesty
    comes from comparing rows against the field, not from padding.
    """
    validate_snapshot_kind(snapshot_kind)
    captured = parse_timestamp(captured_at) or datetime.now(timezone.utc)
    if isinstance(race_date, datetime):
        race_date_val: Any = race_date.date()
    elif isinstance(race_date, date):
        race_date_val = race_date
    else:
        parsed = parse_timestamp(str(race_date))
        race_date_val = parsed.date() if parsed else None

    try:
        race_number_val = int(race_number)
    except (TypeError, ValueError):
        raise ValueError(f"unusable race_number: {race_number!r}")

    race_id = make_race_id(race_date_val or race_date, track, race_number_val, meet_id)
    rows: List[Dict[str, Any]] = []
    for runner in runners:
        if runner.get("scratched"):
            continue
        horse = str(runner.get("horse") or "").strip()
        if not horse:
            continue
        quotes = extract_bookmaker_odds(runner)
        if not quotes:
            continue
        runner_id = make_runner_id(runner)
        for bookmaker, dec in quotes:
            rows.append({
                "race_id": race_id,
                "runner_id": runner_id,
                "snapshot_kind": snapshot_kind,
                "bookmaker": bookmaker,
                "captured_at": captured,
                "decimal_odds": round(float(dec), 2),
                "source_api": source_api,
                "seconds_to_jump": compute_seconds_to_jump(jump_time, captured),
                "race_date": race_date_val,
                "track": normalize_track_name(track),
                "race_number": race_number_val,
                "horse_name": horse,
                "horse_name_norm": normalize_horse_name(horse),
            })
    return rows


def _row_to_tuple(row: Dict[str, Any]) -> tuple:
    return tuple(row.get(col) for col in SNAPSHOT_COLUMNS)


def persist_rows(conn, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Insert snapshot rows. Returns a count dict — never raises.

    Unlike selection_ledger.persist_rows this does not raise on zero writes:
    capture is measurement-only and must degrade silently (task 04 guardrail:
    never block tipping). Failures are reported in the dict and logged.
    """
    if not snapshot_write_enabled():
        return {"written": 0, "skipped": len(rows),
                "reason": "STRIDE_ODDS_SNAPSHOT_WRITE is off"}
    if not rows:
        return {"written": 0, "skipped": 0, "reason": "no rows"}

    payload = [_row_to_tuple(r) for r in rows]
    cur = None
    try:
        cur = conn.cursor()
        cur.executemany(SNAPSHOT_INSERT_SQL, payload)
        written = cur.rowcount if cur.rowcount is not None else 0
        conn.commit()
        return {"written": int(max(written, 0)), "skipped": 0, "reason": None}
    except Exception as e:  # graceful no-op — measurement must never break tipping
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"  [ODDS_SNAP] persist failed (non-fatal): {e}", file=sys.stderr)
        return {"written": 0, "skipped": len(rows), "reason": str(e)}
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Fire-and-forget capture helpers (used by run_tips_pipeline / capture_late_odds)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    env_path = _PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


def _connect():
    """Short-timeout connection. Returns None when no DB is configured —
    capture is a graceful no-op in that environment."""
    url = _database_url()
    if not url:
        return None
    import psycopg2  # lazy: module must import cleanly without a DB driver
    conn = psycopg2.connect(url, connect_timeout=3)
    try:
        cur = conn.cursor()
        cur.execute("SET statement_timeout TO 5000")
        cur.close()
    except Exception:
        pass
    return conn


def capture_snapshots(*, snapshot_kind: str, race_date: Any, track: Any,
                      race_number: Any, runners: Sequence[Dict[str, Any]],
                      meet_id: Any = None, jump_time: Any = None,
                      captured_at: Any = None,
                      source_api: str = SOURCE_API) -> Dict[str, Any]:
    """Build rows and persist them, swallowing every failure.

    Fire-and-forget: the return dict is for logging only. This function must
    never raise and never do meaningful work when the flag is off or no DB is
    configured.
    """
    try:
        if not snapshot_write_enabled():
            return {"written": 0, "skipped": len(runners),
                    "reason": "STRIDE_ODDS_SNAPSHOT_WRITE is off"}
        rows = build_snapshot_rows(
            race_date=race_date, track=track, race_number=race_number,
            runners=runners, snapshot_kind=snapshot_kind,
            captured_at=captured_at, source_api=source_api,
            meet_id=meet_id, jump_time=jump_time)
        if not rows:
            return {"written": 0, "skipped": 0, "reason": "no priced runners"}
        conn = _connect()
        if conn is None:
            return {"written": 0, "skipped": len(rows), "reason": "no DATABASE_URL"}
        try:
            result = persist_rows(conn, rows)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        print(f"  [ODDS_SNAP] {snapshot_kind} {track} R{race_number}: "
              f"{result['written']}/{len(rows)} rows", file=sys.stderr)
        return result
    except Exception as e:  # last-resort guard — capture must never break tipping
        print(f"  [ODDS_SNAP] capture error (non-fatal): {e}", file=sys.stderr)
        return {"written": 0, "skipped": len(runners), "reason": str(e)}


def capture_tip_time_snapshots(*, track: Any, race_number: Any, date_str: Any,
                               runners: Sequence[Dict[str, Any]],
                               race: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Tip-time capture hook for run_tips_pipeline: one tip_time row per
    runner per bookmaker at the moment the pipeline prices the field."""
    race = race or {}
    jump_time = race.get("off_time") or race.get("offTime")
    jump = parse_timestamp(jump_time)
    now = _utcnow()
    if jump is not None and now > jump:
        # The snapshot table is append-only and this hook fires on every
        # run_tips invocation — a rerun of a past date would record post-jump
        # prices as "tip_time" and the training view would median them in.
        # Refusing here is the only place that poison can be stopped.
        print(f"  [ODDS_SNAP] SKIP tip_time {track} R{race_number}: "
              f"capture {now.isoformat()} is after advertised jump "
              f"{jump.isoformat()} — post-jump reruns never write tip_time")
        return {"written": 0, "skipped": len(runners), "reason": "post_jump"}
    return capture_snapshots(
        snapshot_kind="tip_time",
        race_date=date_str, track=track, race_number=race_number,
        runners=runners,
        meet_id=race.get("meet_id"),
        jump_time=jump_time,
    )
