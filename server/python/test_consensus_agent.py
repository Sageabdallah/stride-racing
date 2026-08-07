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

import pytest

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


# ---------------------------------------------------------------------------
# DB mirror retry: redial on connection failure, never re-call on a dead one
# ---------------------------------------------------------------------------

class RetryConn:
    """A connection stand-in with psycopg2's closed flag (0 = open)."""

    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed = 1


def _stub_spend_modules(monkeypatch):
    """run_consensus_agent imports dotenv/tavily/anthropic inside the
    function, and the CI venv has none of them. Stub construction only —
    the tests drive every actual call through their own fakes, so these
    never reach a network."""
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *a, **k: None
    tavily = types.ModuleType("tavily")
    tavily.TavilyClient = lambda **k: object()
    anthropic_mod = types.ModuleType("anthropic")
    anthropic_mod.Anthropic = lambda **k: object()
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)
    monkeypatch.setitem(sys.modules, "tavily", tavily)
    monkeypatch.setitem(sys.modules, "anthropic", anthropic_mod)


def test_retry_redials_after_a_connection_drop():
    """The 2026-08-06 failure inverted: attempt 1 dies with OperationalError,
    attempt 2 must run on a NEW connection, not the corpse of the old one."""
    import psycopg2
    calls = {"factory": 0, "writes": 0}

    def factory():
        calls["factory"] += 1
        conn = RetryConn()
        conn.fail = calls["factory"] == 1
        return conn

    def write(conn, payload):
        if conn.fail:
            raise psycopg2.OperationalError("server closed the connection")
        calls["writes"] += 1
        return payload

    out = ca._store_with_retry(write, factory, {"x": 1}, base_delay=0.01)
    assert out == {"x": 1}
    assert calls["factory"] == 2, "the retry must redial, not re-call"
    assert calls["writes"] == 1


def test_retry_raises_the_last_error_and_redials_every_attempt():
    import psycopg2
    factory_calls = []

    def factory():
        conn = RetryConn()
        factory_calls.append(conn)
        return conn

    def always_fail(conn):
        raise psycopg2.OperationalError(f"drop {len(factory_calls)}")

    with pytest.raises(psycopg2.OperationalError) as e:
        ca._store_with_retry(always_fail, factory, base_delay=0.01)
    assert "drop 3" in str(e.value)          # the LAST error, not the first
    assert len(factory_calls) == 3           # one live socket per attempt
    assert all(c.closed == 1 for c in factory_calls)


def test_retry_does_not_redial_on_a_non_connection_error():
    factory_calls = []

    def factory():
        conn = RetryConn()
        factory_calls.append(conn)
        return conn

    def bad_logic(conn):
        raise ValueError("not a connection problem")

    with pytest.raises(ValueError):
        ca._store_with_retry(bad_logic, factory, base_delay=0.01)
    assert len(factory_calls) == 1   # a code bug must not spin up new sockets


def test_no_connection_is_held_open_across_the_research_phase(monkeypatch):
    """Lifetime guard for the 2026-08-06 incident.

    The run opened its mirror connection BEFORE ~11 minutes of web research;
    Neon reaped the idle socket and the write phase woke a dead line. The
    factory counter must still be 0 while research runs — connections appear
    at the write site, and only there.
    """
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("DATABASE_URL", "test-not-a-real-dsn")
    monkeypatch.delenv("STRIDE_ACCURACY_WEIGHTS", raising=False)
    _stub_spend_modules(monkeypatch)
    monkeypatch.setattr(ca, "extraction_model", lambda: "test-model")
    monkeypatch.setattr(ca, "preflight_extraction_model", lambda *a, **k: None)
    monkeypatch.setattr(ca, "load_racecard_meetings", lambda d: {"fake": True})
    race = {"track": "Randwick", "track_key": "randwick", "race_number": 1,
            "race_name": "Race 1", "distance_m": 1200, "race_class": "BM78",
            "runners": [{"horse": "Fast Horse"}]}
    monkeypatch.setattr(ca, "flatten_races", lambda meetings: [race])
    monkeypatch.setattr(ca, "load_tipster_panel", lambda: {"sources": []})
    monkeypatch.setattr(ca, "fetch_panel_pages", lambda *a, **k: ([], []))
    monkeypatch.setattr(ca, "_load_usage", lambda d: {})
    monkeypatch.setattr(ca.time, "sleep", lambda *_: None)
    monkeypatch.setattr(ca, "_write_output", lambda *a, **k: None)
    monkeypatch.setattr(ca, "_write_health", lambda *a, **k: None)

    factory_calls = []

    def factory():
        conn = RetryConn()
        factory_calls.append(conn)
        return conn

    monkeypatch.setattr(ca, "get_connection", factory)

    def research(*a, **k):
        assert factory_calls == [], \
            "a DB connection was opened before the research phase"
        return {"horses_found": []}

    monkeypatch.setattr(ca, "claude_research_race", research)

    writes = []
    monkeypatch.setattr(
        ca, "store_mentions_batch",
        lambda conn, mentions: writes.append(("mentions", len(mentions))))
    monkeypatch.setattr(
        ca, "store_consensus_scores_batch",
        lambda conn, d, scores: writes.append(("scores", len(scores))))

    ca.run_consensus_agent("2026-08-06")
    assert [w[0] for w in writes] == ["mentions", "scores"]
    assert len(factory_calls) == 2, "connections open at the write site only"
    assert all(c.closed == 1 for c in factory_calls), "and are not leaked"


def test_missing_database_url_fails_before_any_research_spend(monkeypatch):
    """A misconfigured run must not burn a day's LLM budget finding out.

    The check is a presence check on the env var — it must NOT open a
    connection (that would reintroduce the early connection #98 removed),
    and it must fire before preflight, before the research loop: every
    spend counter stays at zero.
    """
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _stub_spend_modules(monkeypatch)

    def boom():
        raise AssertionError("get_connection called by a presence check")

    monkeypatch.setattr(ca, "get_connection", boom)

    spends = {"preflight": 0, "research": 0}
    monkeypatch.setattr(ca, "preflight_extraction_model",
                        lambda *a, **k: spends.__setitem__("preflight", 1))
    monkeypatch.setattr(ca, "claude_research_race",
                        lambda *a, **k: spends.__setitem__("research", 1))

    with pytest.raises(RuntimeError) as e:
        ca.run_consensus_agent("2026-08-06")
    assert "DATABASE_URL" in str(e.value)
    assert spends == {"preflight": 0, "research": 0}
