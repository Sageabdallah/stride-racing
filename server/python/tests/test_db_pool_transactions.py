"""Audit 2026-09-06 H2: mc_api's process-wide psycopg2.connect pool vs the
tips pipeline's transaction semantics.

mc_api replaces ``psycopg2.connect`` for the whole process with a wrapper that
hands out one long-lived AUTOCOMMIT connection per DSN. run_tips_pipeline loads
mc_api in-process (run_mc_simulation), so every ``db_connect()`` after the first
MC race got that pool — and ``store_selections_in_db`` ran its
deactivate-then-insert on a connection where ``rollback()`` was a no-op.
These tests pin the repair on both sides.
"""

from __future__ import annotations

import io
import sys

import pytest

import mc_api
import run_tips_pipeline as pipeline


# ---------------------------------------------------------------------------
# The wrapper
# ---------------------------------------------------------------------------

class _RawConn:
    def __init__(self):
        self.autocommit = True
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def cursor(self, *a, **kw):
        raise AssertionError("not used")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = 1


class TestSharedDbConnection:
    def test_attribute_writes_reach_the_real_connection(self):
        raw = _RawConn()
        shared = mc_api._SharedDbConnection(raw)
        shared.autocommit = False
        assert raw.autocommit is False, (
            "conn.autocommit = False landed on the wrapper, not the wire — "
            "the caller believes it has a transaction and does not")
        assert shared.autocommit is False  # reads still forward too

    def test_context_manager_mirrors_psycopg2(self):
        raw = _RawConn()
        shared = mc_api._SharedDbConnection(raw)
        with shared:
            pass
        assert (raw.commits, raw.rollbacks) == (1, 0)
        with pytest.raises(RuntimeError):
            with shared:
                raise RuntimeError("boom")
        assert (raw.commits, raw.rollbacks) == (1, 1)

    def test_close_keeps_the_pooled_connection_hot(self):
        raw = _RawConn()
        shared = mc_api._SharedDbConnection(raw)
        shared.close()
        assert raw.closed == 0
        shared.real_close()
        assert raw.closed == 1


# ---------------------------------------------------------------------------
# The opt-out
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not mc_api.PSYCOPG2_AVAILABLE, reason="psycopg2 not installed")
class TestPoolOptOut:
    def test_installed_connect_publishes_the_real_driver(self):
        import psycopg2
        assert psycopg2.connect.__name__ == "_shared_psycopg2_connect"
        real = psycopg2.connect.__wrapped__
        assert real is mc_api._REAL_PSYCOPG2_CONNECT
        assert getattr(real, "__name__", "") == "connect"
        assert not hasattr(real, "__wrapped__"), "the pool must not pool the pool"

    def test_pipeline_unpools_for_transactional_work(self):
        import psycopg2
        assert pipeline._unpooled_psycopg2_connect() is psycopg2.connect.__wrapped__

    def test_db_connect_uses_the_real_driver_only_when_asked(self, monkeypatch):
        import psycopg2
        seen = []

        def _fake_real(*a, **kw):
            seen.append("real")
            return object()

        def _fake_pool(*a, **kw):
            seen.append("pool")
            return object()

        monkeypatch.setattr(pipeline, "get_db_url", lambda: "postgresql://x")
        monkeypatch.setattr(pipeline, "_unpooled_psycopg2_connect", lambda: _fake_real)
        monkeypatch.setattr(psycopg2, "connect", _fake_pool)
        pipeline.db_connect(transactional=True)
        pipeline.db_connect()
        assert seen == ["real", "pool"]


# ---------------------------------------------------------------------------
# store_selections_in_db: one transaction, savepoint per pick
# ---------------------------------------------------------------------------

class _Cursor:
    def __init__(self, fail_on=None, fail_savepoint_rollback=False):
        self.calls = []
        self.fail_on = fail_on or set()
        self.fail_savepoint_rollback = fail_savepoint_rollback
        self.closed = False

    def execute(self, query, params=None):
        self.calls.append((query.strip(), params))
        if "INSERT INTO selections" in query and params and params.get("v06") in self.fail_on:
            raise RuntimeError(f"insert refused for {params['v06']}")
        if query.strip() == "ROLLBACK TO SAVEPOINT stride_pick" and self.fail_savepoint_rollback:
            raise RuntimeError("connection gone")

    def close(self):
        self.closed = True


class _Conn:
    def __init__(self, cursor, fail_commit=False):
        self._cursor = cursor
        self.fail_commit = fail_commit
        self.committed = 0
        self.rolled_back = 0
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("commit refused")
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


def _pick(horse):
    return {
        "horse": horse, "rank": 1, "odds": 4.0, "has_real_market_odds": True,
        "edge_pct": 4.0, "raw_model_pct": 20.0, "win_pct": 20.0,
        "confidence": "high", "should_bet": True, "staking": "1u",
    }


def _race(horse, race_number):
    return {"track": "Randwick", "race_number": race_number, "bet_status": "BET",
            "bet_pick": _pick(horse)}


def _run(monkeypatch, conn):
    calls = {}

    def _db_connect(**kw):
        calls.update(kw)
        return conn

    monkeypatch.setattr(pipeline, "db_connect", _db_connect)
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)
    pipeline.store_selections_in_db([_race("A", 1), _race("B", 2)], "2026-09-06")
    return calls, err.getvalue()


def _sql(cursor):
    return [q.split("\n", 1)[0] for q, _ in cursor.calls]


class TestSelectionStoreTransaction:
    def test_happy_path_is_one_transaction_with_a_savepoint_per_pick(self, monkeypatch):
        cur = _Cursor()
        conn = _Conn(cur)
        calls, out = _run(monkeypatch, conn)
        assert calls == {"transactional": True}, "the store must not run on the autocommit pool"
        sql = _sql(cur)
        assert sql[0].startswith("UPDATE selections SET is_active = false")
        assert sql[1:] == [
            "SAVEPOINT stride_pick", "INSERT INTO selections (", "RELEASE SAVEPOINT stride_pick",
            "SAVEPOINT stride_pick", "INSERT INTO selections (", "RELEASE SAVEPOINT stride_pick",
        ]
        assert (conn.committed, conn.rolled_back, conn.closed) == (1, 0, True)
        assert "Stored 2 selections" in out

    def test_failed_insert_is_skipped_without_losing_the_deactivation(self, monkeypatch):
        cur = _Cursor(fail_on={"A"})
        conn = _Conn(cur)
        _, out = _run(monkeypatch, conn)
        sql = _sql(cur)
        assert "ROLLBACK TO SAVEPOINT stride_pick" in sql
        assert conn.rolled_back == 0, (
            "a whole-transaction rollback here discards the deactivation and "
            "every earlier insert, then the final commit lands the remainder "
            "beside the still-active previous picks")
        assert conn.committed == 1
        inserted = [p["v06"] for q, p in cur.calls if "INSERT INTO selections" in q]
        assert inserted == ["A", "B"]  # attempted both
        assert "Stored 1 selections" in out
        assert "Insert failed for A" in out

    def test_unrecoverable_transaction_aborts_without_committing(self, monkeypatch):
        cur = _Cursor(fail_on={"A"}, fail_savepoint_rollback=True)
        conn = _Conn(cur)
        _, out = _run(monkeypatch, conn)
        assert conn.committed == 0
        assert conn.rolled_back == 1
        assert conn.closed is True
        assert "ABORTED" in out and "Stored" not in out

    def test_failed_commit_is_loud_and_rolls_back(self, monkeypatch):
        cur = _Cursor()
        conn = _Conn(cur, fail_commit=True)
        _, out = _run(monkeypatch, conn)
        assert conn.rolled_back == 1
        assert "ABORTED" in out and "commit failed" in out
        assert "Stored" not in out, "a failed commit must never report a store"

    def test_deactivation_failure_still_aborts_before_any_insert(self, monkeypatch):
        class _DeactRefuses(_Cursor):
            def execute(self, query, params=None):
                if query.strip().startswith("UPDATE selections SET is_active = false"):
                    raise RuntimeError("update refused")
                super().execute(query, params)

        cur = _DeactRefuses()
        conn = _Conn(cur)
        _, out = _run(monkeypatch, conn)
        assert not any("INSERT" in q for q, _ in cur.calls)
        assert (conn.committed, conn.rolled_back) == (0, 1)
        assert "ABORTED" in out
