"""capture_late_odds must not be able to fail silently.

The audited failure mode: DB connect fails -> conn = None -> every due race
logs "no DB — skipped" -> exit 0; or the connection dies -> persist_rows
(which never raises, by design) returns written=0 per race -> exit 0. Either
way the job was green with zero data and no alarm. These pin the loud paths
and the two deliberate quiet ones (no due races, no DB configured).
"""

import sys
from pathlib import Path

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import capture_late_odds as late


SCHEDULE = [{"track": "Flemington", "race_number": 1,
             "jump_time": None, "meet_id": "m1"}]


class SpyConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _arm(monkeypatch, connect, persist):
    monkeypatch.setattr(late, "load_schedule", lambda d: SCHEDULE)
    monkeypatch.setattr(late, "find_due_races",
                        lambda s, now, target_seconds: SCHEDULE)
    monkeypatch.setattr(late, "snapshot_write_enabled", lambda: True)
    monkeypatch.setattr(late, "_connect", connect)
    monkeypatch.setattr(late, "already_captured_race_ids",
                        lambda conn, d: set())
    monkeypatch.setattr(late, "compute_seconds_to_jump", lambda jt, at: -300)
    monkeypatch.setattr(late, "fetch_race_market",
                        lambda d, r: ([{"horse": "X"}], "betfair"))
    monkeypatch.setattr(late, "build_snapshot_rows", lambda **kw: [
        {"horse_name": "X", "bookmaker": "BF", "decimal_odds": 2.0}])
    monkeypatch.setattr(late, "persist_rows", persist)


def test_connect_failure_is_fatal_when_races_are_due(monkeypatch):
    def dead_connect():
        raise psycopg2.OperationalError("Neon unreachable")

    _arm(monkeypatch, dead_connect, lambda conn, rows: {"written": 1})
    with pytest.raises(psycopg2.OperationalError):
        late.run("2026-08-08")


def test_rows_built_but_zero_written_raises(monkeypatch):
    conn = SpyConn()
    _arm(monkeypatch, lambda: conn, lambda c, rows: {"written": 0})
    with pytest.raises(RuntimeError, match="wrote 0"):
        late.run("2026-08-08")
    assert conn.closed is True  # closed even on the failure path


def test_successful_write_closes_conn(monkeypatch):
    conn = SpyConn()
    _arm(monkeypatch, lambda: conn, lambda c, rows: {"written": 3})
    summary = late.run("2026-08-08")
    assert summary["captured"] == 1
    assert summary["rows"] == 3
    assert conn.closed is True


def test_quiet_window_is_still_a_clean_exit(monkeypatch):
    # Zero due races is the normal state most of the day for a 5-min job —
    # it must stay exit-0 and must not even dial the DB.
    dialled = []
    monkeypatch.setattr(late, "load_schedule", lambda d: SCHEDULE)
    monkeypatch.setattr(late, "find_due_races",
                        lambda s, now, target_seconds: [])
    monkeypatch.setattr(late, "_connect",
                        lambda: dialled.append(1) or SpyConn())
    summary = late.run("2026-08-08")
    assert summary["captured"] == 0
    assert dialled == []


def test_no_db_configured_still_degrades_quietly(monkeypatch):
    # _connect() returning None is the documented no-DATABASE_URL dev
    # escape: rows are built, skipped, and the run stays green.
    _arm(monkeypatch, lambda: None, lambda c, rows: {"written": 0})
    summary = late.run("2026-08-08")
    assert summary["rows"] == 0
