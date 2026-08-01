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
