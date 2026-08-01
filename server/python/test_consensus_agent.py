"""Tests for consensus_agent.py (DM-H3b).

Pins the tipster-accuracy window in load_accuracy_multipliers: the query
must carry BOTH the lower bound (race_date >= CURRENT_DATE - INTERVAL) and
the upper bound (race_date < CURRENT_DATE), so an evening tips RERUN —
after the results import and source_accuracy_tracker have run — cannot
fold today's settled results into today's tipster multipliers (PF-5).

Zero network, zero database: `requests` is stubbed (imported at module
top, absent from the test venv) and get_connection is faked with a cursor
that captures the emitted SQL.
"""
import os
import sys
import types

sys.modules.setdefault("requests", types.ModuleType("requests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import consensus_agent as ca


class CapturingCursor:
    def __init__(self):
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append(sql)

    def fetchall(self):
        return []

    def close(self):
        pass


class FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def close(self):
        pass


def test_accuracy_window_has_lower_and_upper_bound(monkeypatch):
    monkeypatch.setenv("STRIDE_ACCURACY_WEIGHTS", "true")
    cur = CapturingCursor()
    monkeypatch.setattr(ca, "get_connection", lambda: FakeConn(cur))
    ca.load_accuracy_multipliers()
    assert len(cur.queries) == 1
    sql = " ".join(cur.queries[0].split())
    assert "race_date >= (CURRENT_DATE - INTERVAL '120 days')" in sql
    assert "race_date < CURRENT_DATE" in sql


def test_flag_off_touches_no_connection(monkeypatch):
    # default behavior unchanged: with the flag off (the morning-path
    # default) the function returns {} without opening any connection
    monkeypatch.delenv("STRIDE_ACCURACY_WEIGHTS", raising=False)

    def boom():
        raise AssertionError("get_connection called with the flag off")

    monkeypatch.setattr(ca, "get_connection", boom)
    assert ca.load_accuracy_multipliers() == {}
