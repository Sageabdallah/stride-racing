#!/usr/bin/env python3
"""Offline tests for seed_race_schedule's exit contract.

No database: psycopg2.connect is replaced with a fake whose COUNT(*) answer
the test chooses, which is the only DB value the exit code depends on.

The case under test is the quiet day. seed_race_schedule runs as the step
straight after the racecard build in pf-morning-racecards, and it exits 1 when
race_schedule holds no rows for the date. On a day where the provider listed
meetings but none were on the target-track list there is correctly nothing to
seed — about a third of days — so that exit 1 turned a calendar fact into a
red morning. The fix has to stay narrow: only an empty table AND the quiet
sentinel together mean "correctly nothing", and anything else still exits 1.
"""

import sys
from pathlib import Path

import pytest

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import seed_race_schedule


class FakeCursor:
    def __init__(self, total):
        self._total = total
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return (self._total,)

    @property
    def rowcount(self):
        return 0


class FakeConn:
    def __init__(self, total):
        self.cur = FakeCursor(total)

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def close(self):
        pass


def _wire(monkeypatch, tmp_path, total_rows):
    """Point the script at an empty card directory and a fake DB holding
    `total_rows` schedule rows for the date."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://fixture/only")
    monkeypatch.setattr(seed_race_schedule, "CARD_DIRS", (tmp_path,))
    monkeypatch.setattr(seed_race_schedule.psycopg2, "connect",
                        lambda url, **kwargs: FakeConn(total_rows))
    # No card means main() falls back to the prediction_audit projection; stub
    # it so the fallback never needs a real connection.
    fake = type(sys)("results_projection")
    fake.ensure_race_schedule_from_prediction_audit = lambda conn, date=None: 0
    monkeypatch.setitem(sys.modules, "results_projection", fake)


def _run(monkeypatch, date_str="2026-08-03"):
    monkeypatch.setattr(sys, "argv", ["seed_race_schedule.py", date_str])
    return seed_race_schedule.main()


def test_quiet_day_with_no_rows_exits_0(monkeypatch, tmp_path):
    """The case this exists for: quiet sentinel on disk, table empty for the
    date, nothing to seed, and that is the correct outcome."""
    _wire(monkeypatch, tmp_path, total_rows=0)
    (tmp_path / "quiet_2026-08-03.json").write_text('{"status": "quiet"}')
    assert _run(monkeypatch) == 0


def test_no_rows_and_no_sentinel_still_exits_1(monkeypatch, tmp_path):
    """The narrowness that keeps this from being a weakened check: without a
    sentinel, an empty table is still a failure to report."""
    _wire(monkeypatch, tmp_path, total_rows=0)
    assert _run(monkeypatch) == 1


def test_sentinel_for_another_date_does_not_excuse_this_one(monkeypatch, tmp_path):
    """A stale sentinel from a previous quiet day must not suppress today's
    genuine failure."""
    _wire(monkeypatch, tmp_path, total_rows=0)
    (tmp_path / "quiet_2026-08-02.json").write_text('{"status": "quiet"}')
    assert _run(monkeypatch) == 1


def test_rows_present_exits_0_with_or_without_sentinel(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, total_rows=37)
    assert _run(monkeypatch) == 0
    (tmp_path / "quiet_2026-08-03.json").write_text('{"status": "quiet"}')
    assert _run(monkeypatch) == 0


def test_missing_database_url_is_still_config_error(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, total_rows=0)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / "quiet_2026-08-03.json").write_text('{"status": "quiet"}')
    assert _run(monkeypatch) == 2


def test_quiet_marker_searches_every_card_dir(monkeypatch, tmp_path):
    """The sentinel is found wherever a card would have been — the script is
    run from the repo root by CI and from server/python by the AWS handler."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setattr(seed_race_schedule, "CARD_DIRS", (a, b))
    assert seed_race_schedule.quiet_marker("2026-08-03") is None
    (b / "quiet_2026-08-03.json").write_text("{}")
    assert seed_race_schedule.quiet_marker("2026-08-03") == b / "quiet_2026-08-03.json"
