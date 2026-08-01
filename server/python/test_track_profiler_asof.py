#!/usr/bin/env python3
"""
Tests for as-of (monthly-bucketed) track-distance profiles — the fix for the
td_* aggregate leak where each training row's own result sat inside the
profile it was scored against. No DB: connections are faked, rows are fixtures.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _fake_psycopg2(monkeypatch):
    """No DB driver in the test env; build_profiles only needs the import to resolve."""
    import types
    fake = types.ModuleType("psycopg2")
    extras = types.ModuleType("psycopg2.extras")
    extras.RealDictCursor = object
    fake.extras = extras
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    monkeypatch.setitem(sys.modules, "psycopg2.extras", extras)


import track_profiler
from track_profiler import (
    build_profiles,
    build_profiles_buckets,
    load_profiles_asof,
    load_td_profiles_for_training,
    lookup_profile,
    select_bucket,
)


class FakeCursor:
    """Records executed SQL + params, returns canned rows for fetchall."""

    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params or {}))

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows):
        self.cur = FakeCursor(rows)

    def cursor(self, cursor_factory=None):
        return self.cur


def _profile(pace_bias):
    return {
        "insufficient_data": False,
        "sample_size": 500,
        "pace_bias": pace_bias,
        "upset_rate": 0.2,
        "closing_speed_bias": 0.0,
        "barrier_heatmap": {},
    }


def test_as_of_upper_bound_is_strict():
    """A race ON the as_of date must be excluded: strict < in the executed SQL,
    and the window anchored to as_of rather than CURRENT_DATE."""
    conn = FakeConn(rows=[])
    build_profiles(conn, as_of="2026-03-01")

    results_q, results_params = conn.cur.queries[0]
    assert "race_date::date < %(as_of)s::date" in results_q
    assert "%(as_of)s::date - INTERVAL '2 years'" in results_q
    assert "CURRENT_DATE" not in results_q
    assert results_params == {"as_of": "2026-03-01"}

    sect_q, sect_params = conn.cur.queries[1]
    assert "st.race_date::date < %(as_of)s::date" in sect_q
    assert "CURRENT_DATE" not in sect_q
    assert sect_params == {"as_of": "2026-03-01"}


def test_no_as_of_keeps_legacy_window():
    conn = FakeConn(rows=[])
    build_profiles(conn)
    results_q, results_params = conn.cur.queries[0]
    assert "CURRENT_DATE - INTERVAL '2 years'" in results_q
    assert "%(as_of)s" not in results_q
    assert results_params == {}


def test_bucket_routing_excludes_own_month_and_future():
    """A row dated 2026-03-15 reads bucket '2026-03' (races < 2026-03-01).
    A poisoned value in the '2026-04' bucket (which is where a 2026-03-20
    result would first appear) must not influence it."""
    buckets = {
        "2026-03": {"flemington_1400": _profile(0.42)},
        "2026-04": {"flemington_1400": _profile(0.99)},  # poisoned
    }
    row_profiles = select_bucket(buckets, "2026-03-15")
    feats = lookup_profile(row_profiles, "Flemington", 1400)
    assert feats["td_pace_bias"] == 0.42
    assert feats["td_pace_bias"] != 0.99

    # Unknown month bucket and unparseable date -> no-data defaults.
    assert select_bucket(buckets, "2026-07-04") == {}
    assert select_bucket(buckets, "not-a-date") == {}
    feats_default = lookup_profile(select_bucket(buckets, "2026-07-04"), "Flemington", 1400)
    assert feats_default == {
        "td_pace_bias": 0.5,
        "td_upset_rate": 0.2,
        "td_closing_speed_bias": 0,
        "td_barrier_style_edge": 0,
    }


def test_meta_written(tmp_path):
    conn = FakeConn(rows=[])
    combined = build_profiles_buckets(conn, "2026-01", "2026-03")
    assert combined["_meta"]["as_of_rule"] == (
        "bucket M contains only races with race_date < M-01"
    )
    assert combined["_meta"]["built_at"]
    assert set(combined) == {"2026-01", "2026-02", "2026-03", "_meta"}
    # Each bucket was built with as_of = first of its month.
    asofs = [p for _, p in conn.cur.queries if p]
    assert {"as_of": "2026-01-01"} in asofs
    assert {"as_of": "2026-03-01"} in asofs


def test_load_profiles_asof_strips_meta(tmp_path):
    p = tmp_path / "asof.json"
    p.write_text(json.dumps({"2026-03": {"x_1200": _profile(0.4)},
                             "_meta": {"as_of_rule": "..."}}))
    buckets = load_profiles_asof(str(p))
    assert "_meta" not in buckets
    assert set(buckets) == {"2026-03"}
    assert load_profiles_asof(str(tmp_path / "missing.json")) == {}


def _write_intel(tmp_path, asof=None, legacy=None):
    intel = tmp_path / "intelligence"
    intel.mkdir()
    if asof is not None:
        (intel / "track_distance_profiles_asof.json").write_text(json.dumps(asof))
    if legacy is not None:
        (intel / "track_distance_profiles.json").write_text(json.dumps(legacy))


def test_training_loader_prefers_asof(tmp_path, capsys):
    _write_intel(tmp_path,
                 asof={"2026-03": {"flemington_1400": _profile(0.4)}, "_meta": {}},
                 legacy={"flemington_1400": _profile(0.99)})
    asof, legacy = load_td_profiles_for_training(str(tmp_path))
    assert set(asof) == {"2026-03"}
    assert legacy == {}
    assert "known leak" not in capsys.readouterr().out


def test_training_loader_legacy_fallback_warns(tmp_path, capsys):
    _write_intel(tmp_path, legacy={"flemington_1400": _profile(0.5)})
    asof, legacy = load_td_profiles_for_training(str(tmp_path))
    assert asof == {}
    assert legacy["flemington_1400"]["pace_bias"] == 0.5
    assert ("WARNING: td profiles are NOT as-of (legacy snapshot) — known leak"
            in capsys.readouterr().out)


def test_training_loader_both_absent(tmp_path):
    (tmp_path / "intelligence").mkdir()
    asof, legacy = load_td_profiles_for_training(str(tmp_path))
    assert asof == {} and legacy == {}
