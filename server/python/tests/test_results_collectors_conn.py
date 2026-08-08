"""Results collectors: no connection held across network phases, and
connection-level failures abort loudly instead of being swallowed.

The 2026-08-08 pattern: a Neon socket held idle across minutes of API or
subprocess work dies; the collectors then either crashed at the next
cursor() or — worse — swallowed the error, reported success, and burned
race retry budget on an infrastructure fault.
"""

import inspect
import sys
from pathlib import Path

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auto_results_collector as arc
import stride_results_collector as src


class SpyCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


class SpyConn:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.closed = False
        self.commits = 0

    def cursor(self, cursor_factory=None):
        return SpyCursor(self.rows)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        self.closed = True


PENDING_ROW = {"id": "s1", "track": "Flemington",
               "race_number": 4, "race_date": "2026-07-19"}


def test_no_connection_held_across_fetch(monkeypatch):
    # The conn opened for get_pending_races must be closed before the
    # provider fetch loop runs, and the write loop must get a fresh one.
    conns = []

    def factory():
        conn = SpyConn(rows=[PENDING_ROW])
        conns.append(conn)
        return conn

    monkeypatch.setattr(arc, "get_db_connection", factory)
    monkeypatch.setattr(arc, "_ensure_sp_backfill_index", lambda conn: None)
    monkeypatch.setattr(arc, "collect_sectional_times", lambda dates: 0)
    monkeypatch.setattr(arc, "collect_racing_com_sectionals", lambda dates: 0)

    state = {}

    def fake_fetch(date):
        state["first_conn_closed_at_fetch"] = conns[0].closed
        return [], None  # fetch fine, nothing resulted yet

    monkeypatch.setattr(arc, "fetch_results_for_date_checked", fake_fetch)
    monkeypatch.setattr(arc, "increment_retry_count", lambda conn, sid: None)

    result = arc.process_pending_races("2026-07-19")
    assert state["first_conn_closed_at_fetch"] is True
    assert len(conns) == 2
    assert conns[1].closed is True
    assert result["success"] is True


def test_conn_error_does_not_burn_retry_count(monkeypatch):
    # A dead socket in the write path must propagate, and must NOT spend the
    # race's retry budget (5 dead-socket nights would silently drop it).
    increments = []
    monkeypatch.setattr(arc, "get_db_connection",
                        lambda: SpyConn(rows=[PENDING_ROW]))
    monkeypatch.setattr(arc, "_ensure_sp_backfill_index", lambda conn: None)
    monkeypatch.setattr(arc, "fetch_results_for_date_checked",
                        lambda d: ([{"track": "Flemington", "race_number": 4}], None))
    monkeypatch.setattr(arc, "find_race_in_results",
                        lambda track, num, results: {"winner": "x"})

    def dead_update(conn, track, num, date, result):
        raise psycopg2.InterfaceError("connection already closed")

    monkeypatch.setattr(arc, "update_prediction_audit", dead_update)
    monkeypatch.setattr(arc, "increment_retry_count",
                        lambda conn, sid: increments.append(sid))

    with pytest.raises(psycopg2.InterfaceError):
        arc.process_pending_races("2026-07-19")
    assert increments == []


def test_data_error_still_burns_retry_count(monkeypatch):
    # The existing semantics for bad race data must survive the change.
    increments = []
    monkeypatch.setattr(arc, "get_db_connection",
                        lambda: SpyConn(rows=[PENDING_ROW]))
    monkeypatch.setattr(arc, "_ensure_sp_backfill_index", lambda conn: None)
    monkeypatch.setattr(arc, "fetch_results_for_date_checked",
                        lambda d: ([{"track": "Flemington", "race_number": 4}], None))
    monkeypatch.setattr(arc, "find_race_in_results",
                        lambda track, num, results: {"winner": "x"})
    monkeypatch.setattr(arc, "update_prediction_audit",
                        lambda *a: (_ for _ in ()).throw(ValueError("bad row")))
    monkeypatch.setattr(arc, "increment_retry_count",
                        lambda conn, sid: increments.append(sid))
    monkeypatch.setattr(arc, "collect_sectional_times", lambda dates: 0)
    monkeypatch.setattr(arc, "collect_racing_com_sectionals", lambda dates: 0)

    result = arc.process_pending_races("2026-07-19")
    assert increments == ["s1"]
    assert result["results_collected"] == 0


def test_db_phase_closes_on_success_and_error(monkeypatch):
    conns = []

    def factory():
        conn = SpyConn()
        conns.append(conn)
        return conn

    monkeypatch.setattr(src, "_get_connection", factory)

    with src._db_phase() as conn:
        assert conn is conns[0]
    assert conns[0].closed is True

    with pytest.raises(RuntimeError):
        with src._db_phase():
            raise RuntimeError("step blew up")
    assert conns[1].closed is True


def test_fetch_and_store_results_owns_its_connection():
    # Signature pin: callers no longer supply a connection opened before the
    # (minutes-long) fetch loop; the function dials fresh at import time.
    params = list(inspect.signature(src.fetch_and_store_results).parameters)
    assert params == ["needed", "parallel"]


def test_franking_reraises_conn_errors():
    class DeadConn:
        def cursor(self):
            raise psycopg2.InterfaceError("connection already closed")

    with pytest.raises(psycopg2.InterfaceError):
        src.trigger_franking_updates(DeadConn(), ["2026-08-08"])
