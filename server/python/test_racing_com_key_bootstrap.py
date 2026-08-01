#!/usr/bin/env python3
"""Key bootstrap + paged backfill for the racing.com sectionals collector.
No network, no DB: urlopen and the per-meeting write path are stubbed."""

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import racing_com_sectionals_collector as rc


PAGE_OK = 'var cfg = { headerAPIKey: "da2-abc123def456", other: 1 };'


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    monkeypatch.setattr(rc, "_API_KEY_CACHE", None)
    monkeypatch.delenv("RACING_COM_API_KEY", raising=False)


class _Resp:
    def __init__(self, body):
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_page(monkeypatch, body, calls=None):
    def _open(req, timeout=None):
        if calls is not None:
            calls.append(getattr(req, "full_url", req))
        return _Resp(body)
    monkeypatch.setattr(rc.urllib.request, "urlopen", _open)


class TestKeyBootstrap:
    def test_env_var_wins_and_skips_network(self, monkeypatch):
        monkeypatch.setenv("RACING_COM_API_KEY", "da2-operatoroverride")

        def _boom(*a, **k):
            raise AssertionError("must not fetch the page when env is set")
        monkeypatch.setattr(rc.urllib.request, "urlopen", _boom)
        assert rc.resolve_api_key() == "da2-operatoroverride"

    def test_extracts_from_page(self, monkeypatch):
        _stub_page(monkeypatch, PAGE_OK)
        assert rc.resolve_api_key() == "da2-abc123def456"

    def test_cached_after_first_call(self, monkeypatch):
        calls = []
        _stub_page(monkeypatch, PAGE_OK, calls)
        rc.resolve_api_key()
        rc.resolve_api_key()
        assert len(calls) == 1

    def test_missing_pattern_fails_loudly_naming_the_url(self, monkeypatch):
        _stub_page(monkeypatch, "<html>no key here</html>")
        with pytest.raises(RuntimeError) as e:
            rc.resolve_api_key()
        assert rc.API_KEY_PAGE in str(e.value)
        assert "RACING_COM_API_KEY" in str(e.value)

    def test_fetch_failure_fails_loudly(self, monkeypatch):
        def _boom(*a, **k):
            raise OSError("connection reset")
        monkeypatch.setattr(rc.urllib.request, "urlopen", _boom)
        with pytest.raises(RuntimeError) as e:
            rc.resolve_api_key()
        assert rc.API_KEY_PAGE in str(e.value)

    def test_never_returns_empty(self, monkeypatch):
        # A blank env var must fall through to extraction, not serve "".
        monkeypatch.setenv("RACING_COM_API_KEY", "   ")
        _stub_page(monkeypatch, PAGE_OK)
        assert rc.resolve_api_key() == "da2-abc123def456"


def _meet(mid, date):
    return {"id": mid, "date": date, "venue": "Flemington", "racesCount": 8}


class TestPagedBackfill:
    """The API caps each response, so paging must cover the whole range
    without refetching or wedging."""

    def _run(self, monkeypatch, windows, from_date, processed):
        calls = {"n": 0}

        def _get_meetings(days_back, states="VIC|SA", verbose=False):
            i = calls["n"]
            calls["n"] += 1
            return windows[i] if i < len(windows) else []

        monkeypatch.setattr(rc, "get_meetings", _get_meetings)
        monkeypatch.setattr(rc, "process_meeting",
                            lambda m, db, v: processed.append(m["id"]) or [{"r": 1}])
        monkeypatch.setattr(rc, "match_to_race_results", lambda r, db: (1, 0))
        monkeypatch.setattr(rc, "import_to_database", lambda r, db: (1, 0))
        monkeypatch.setattr(rc.time, "sleep", lambda s: None)
        return rc.collect_from_date(from_date, db_url="x"), calls["n"]

    def test_pages_forward_without_reprocessing(self, monkeypatch):
        today = datetime.now().date()
        d1 = (today - timedelta(days=9)).isoformat()
        d2 = (today - timedelta(days=5)).isoformat()
        d3 = (today - timedelta(days=1)).isoformat()
        processed = []
        imported, n_calls = self._run(
            monkeypatch,
            [[_meet("m1", d1), _meet("m2", d2)],   # window 1
             [_meet("m2", d2), _meet("m3", d3)],   # window 2 overlaps m2
             []],
            (today - timedelta(days=10)).isoformat(), processed)
        assert processed == ["m1", "m2", "m3"]     # m2 seen once, not twice
        assert imported == 3

    def test_no_advance_still_terminates(self, monkeypatch):
        # Every window returns the same single day: the cursor must nudge
        # forward anyway rather than loop forever.
        today = datetime.now().date()
        same = (today - timedelta(days=3)).isoformat()
        processed = []
        imported, n_calls = self._run(
            monkeypatch, [[_meet("m1", same)]] * 50,
            (today - timedelta(days=3)).isoformat(), processed)
        assert processed == ["m1"]
        assert n_calls <= 5   # 3 days of range -> at most a few windows

    def test_ignores_out_of_range_dates(self, monkeypatch):
        today = datetime.now().date()
        processed = []
        future = (today + timedelta(days=5)).isoformat()
        stale = (today - timedelta(days=90)).isoformat()
        self._run(monkeypatch,
                  [[_meet("future", future), _meet("stale", stale),
                    _meet("ok", (today - timedelta(days=1)).isoformat())], []],
                  (today - timedelta(days=2)).isoformat(), processed)
        assert processed == ["ok"]

    def test_window_guard_stops_and_says_where(self, monkeypatch, capsys):
        today = datetime.now().date()
        monkeypatch.setattr(rc, "get_meetings", lambda *a, **k: [])
        monkeypatch.setattr(rc.time, "sleep", lambda s: None)
        rc.collect_from_date((today - timedelta(days=500)).isoformat(),
                             db_url="x", max_windows=3)
        out = capsys.readouterr().out
        assert "STOPPED at the 3-window guard" in out
        assert "--from-date" in out


class _BatchCursor:
    """Records statements; mimics rowcount for a multi-row ON CONFLICT insert."""

    def __init__(self, already_present=0):
        self.already_present = already_present
        self.statements = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def close(self):
        pass


class _BatchConn:
    def __init__(self, cur):
        self._cur = cur
        self.commits = 0

    def cursor(self):
        return self._cur

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def _records(n, date="2026-08-01"):
    return [{"race_date": date, "track": "Flemington", "race_number": 1,
             "horse_name": f"Horse {i}", "splits_json": [{"distance": 200}]}
            for i in range(n)]


class TestBatchedImport:
    def _patch(self, monkeypatch, cur, captured):
        import types
        fake_pg = types.ModuleType("psycopg2")
        fake_pg.connect = lambda url: _BatchConn(cur)
        fake_extras = types.ModuleType("psycopg2.extras")

        def _execute_values(c, sql, values, page_size=None):
            captured.append((sql, values))
            c.statements.append((sql, values))
            c.rowcount = len(values) - cur.already_present
        fake_extras.execute_values = _execute_values
        fake_pg.extras = fake_extras
        monkeypatch.setitem(sys.modules, "psycopg2", fake_pg)
        monkeypatch.setitem(sys.modules, "psycopg2.extras", fake_extras)

    def test_single_statement_per_batch_not_per_record(self, monkeypatch):
        cur = _BatchCursor()
        captured = []
        self._patch(monkeypatch, cur, captured)
        imported, skipped = rc.import_to_database(_records(80), "postgres://x")
        assert len(captured) == 1          # one round trip, not 160
        assert imported == 80 and skipped == 0

    def test_no_per_record_existence_select(self, monkeypatch):
        cur = _BatchCursor()
        self._patch(monkeypatch, cur, [])
        rc.import_to_database(_records(10), "postgres://x")
        assert not any("SELECT id FROM sectional_times" in str(s)
                       for s, _ in cur.statements)

    def test_conflict_clause_preserved(self, monkeypatch):
        cur = _BatchCursor()
        captured = []
        self._patch(monkeypatch, cur, captured)
        rc.import_to_database(_records(3), "postgres://x")
        sql = captured[0][0]
        assert "ON CONFLICT (race_date, track, race_number, horse_name) DO NOTHING" in sql
        assert "VALUES %s" in sql

    def test_duplicates_in_payload_collapse(self, monkeypatch):
        cur = _BatchCursor()
        captured = []
        self._patch(monkeypatch, cur, captured)
        recs = _records(2) + _records(2)   # same four conflict keys twice
        imported, skipped = rc.import_to_database(recs, "postgres://x")
        assert len(captured[0][1]) == 2    # deduped before the statement
        assert imported == 2
        assert skipped == len(recs) - imported

    def test_existing_rows_counted_as_skipped(self, monkeypatch):
        cur = _BatchCursor(already_present=30)
        self._patch(monkeypatch, cur, [])
        imported, skipped = rc.import_to_database(_records(50), "postgres://x")
        assert imported == 20 and skipped == 30

    def test_empty_records_short_circuit(self):
        assert rc.import_to_database([], "postgres://x") == (0, 0)


class TestSplitDistanceTypes:
    """The feed types split distances inconsistently; a raw subtraction cost
    a whole meeting ('bet365 Camperdown', 2024-02-06)."""

    def _entry(self, dist):
        return {"splitTimes": [{"distance": dist, "time": "23.5"}]}

    def test_string_distance_does_not_raise(self):
        out = rc.extract_split_data(self._entry("1000"), 1400)
        assert isinstance(out, dict)

    def test_string_and_int_distances_agree(self):
        as_str = rc.extract_split_data(self._entry("1000"), 1400)
        as_int = rc.extract_split_data(self._entry(1000), 1400)
        assert as_str.get("last_400m_time") == as_int.get("last_400m_time")
        assert as_str.get("last_400m_time") is not None   # 1400-400 = 1000

    def test_junk_distance_is_ignored_not_fatal(self):
        assert isinstance(rc.extract_split_data(self._entry("n/a"), 1400), dict)
        assert isinstance(rc.extract_split_data(self._entry(None), 1400), dict)

    def test_800m_site_also_handles_strings(self):
        out = rc.extract_split_data(self._entry("600"), 1400)   # 1400-800 = 600
        assert out.get("last_800m_time") is not None
