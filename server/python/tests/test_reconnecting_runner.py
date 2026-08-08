"""ReconnectingRunner: the intelligence agents' answer to Neon idle drops.

The 2026-08-08 failure: Agent 2 held one connection for its whole run, Neon
closed the idle socket, and the build died at the next cursor() with
InterfaceError. The runner must redial and re-run the step, reuse a healthy
connection across steps, and never retry non-connection errors.
"""

import sys
from pathlib import Path

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intelligence_common import CONN_ERRORS, ReconnectingRunner


class FakeConn:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed = 1


def make_factory(conns):
    """Factory that hands out the given connections in order."""
    it = iter(conns)

    def connect():
        return next(it)

    return connect


def test_conn_errors_covers_the_aug8_failure():
    assert psycopg2.InterfaceError in CONN_ERRORS
    assert psycopg2.OperationalError in CONN_ERRORS


def test_healthy_step_runs_once_and_reuses_connection():
    conns = [FakeConn()]
    runner = ReconnectingRunner(connect=make_factory(conns))
    seen = []

    assert runner.run("a.json", lambda c: seen.append(c) or "a") == "a"
    assert runner.run("b.json", lambda c: seen.append(c) or "b") == "b"
    # one dial, same socket both steps
    assert seen == [conns[0], conns[0]]


def test_dropped_connection_redials_and_reruns_step(monkeypatch):
    monkeypatch.setattr("intelligence_common.time.sleep", lambda s: None)
    dead, live = FakeConn(), FakeConn()
    runner = ReconnectingRunner(connect=make_factory([dead, live]))
    attempts = []

    def step(conn):
        attempts.append(conn)
        if conn is dead:
            raise psycopg2.InterfaceError("connection already closed")
        return "built"

    assert runner.run("prep_cycles.json", step) == "built"
    assert attempts == [dead, live]
    assert dead.closed  # the poisoned socket was discarded, not kept


def test_next_step_gets_fresh_socket_after_midstep_drop(monkeypatch):
    # The Aug 8 shape: the drop happens inside step N, step N+1 must not
    # inherit the dead socket.
    monkeypatch.setattr("intelligence_common.time.sleep", lambda s: None)
    dead, also_dead, fresh = FakeConn(), FakeConn(), FakeConn()
    runner = ReconnectingRunner(connect=make_factory([dead, also_dead, fresh]))

    def dies_after_marking(conn):
        conn.closed = 1  # server hung up mid-step
        raise psycopg2.OperationalError("SSL connection has been closed unexpectedly")

    with pytest.raises(psycopg2.OperationalError):
        # both attempts get the same deterministic failure and it propagates
        runner.run("form_franking.json", dies_after_marking)

    assert runner.run("sectional_trends.json", lambda c: c) is fresh


def test_persistent_conn_failure_raises_after_attempts(monkeypatch):
    monkeypatch.setattr("intelligence_common.time.sleep", lambda s: None)
    conns = [FakeConn() for _ in range(2)]
    runner = ReconnectingRunner(connect=make_factory(conns), attempts=2)
    calls = []

    def step(conn):
        calls.append(conn)
        raise psycopg2.OperationalError("still down")

    with pytest.raises(psycopg2.OperationalError):
        runner.run("x.json", step)
    assert calls == conns  # exactly `attempts` tries, fresh socket each


def test_non_connection_error_is_not_retried():
    runner = ReconnectingRunner(connect=make_factory([FakeConn()]))
    calls = []

    def step(conn):
        calls.append(conn)
        raise ValueError("bad data, retrying would not help")

    with pytest.raises(ValueError):
        runner.run("x.json", step)
    assert len(calls) == 1


def test_close_is_idempotent_and_safe_before_first_run():
    runner = ReconnectingRunner(connect=make_factory([]))
    runner.close()
    runner.close()
