"""The #123 fence must be enforced by the shadow P/L report, not remembered.

Decision on #123 (2026-08-10): the rows captured between the first cloud
Betfair capture (2026-08-05) and the deploy of the capture guard (#119) and
book-coherence judge (#122) on 2026-08-10 stay in the database as evidence,
and no measurement may count them. Until this module existed nothing enforced
that: `report` summed every settled row it had, so the 2026-08-08 card (48 of
56 STRONG_DRIFT signals fabricated from a bot's standing $209 offer, staked
cohort -7.50u) counted against every tier it touched.

These pin the window, that both measuring queries carry it, that the report
says what it left out, and that the rows themselves are still written.
"""

import re
from datetime import date

import pytest

import shadow_pl_tracker as spt


class RecordingCursor:
    """Answers the report's queries by shape and keeps every (sql, params)."""

    def __init__(self, tiers, totals, fenced):
        self.calls = []
        self._tiers, self._totals, self._fenced = tiers, totals, fenced
        self._last = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._last = sql

    def fetchall(self):
        assert "GROUP BY convergence_tier" in self._last
        return self._tiers

    def fetchone(self):
        if "MIN(race_date)" in self._last:
            return self._totals
        assert "race_date BETWEEN" in self._last, "fetchone on an unexpected query"
        return self._fenced

    def close(self):
        pass


class RecordingConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def close(self):
        pass


def _measuring_queries(cur):
    """The queries whose numbers reach the report, i.e. everything that is
    not the schema migration or the fenced-count query."""
    return [(sql, params) for sql, params in cur.calls
            if "SELECT" in sql and "NOT BETWEEN" in sql]


def _run_report(monkeypatch, tiers, totals, fenced):
    cur = RecordingCursor(tiers, totals, fenced)
    monkeypatch.setattr(spt, "_get_connection", lambda: RecordingConn(cur))
    monkeypatch.setattr(spt, "ensure_schema", lambda conn: None)
    spt.cmd_report()
    return cur


def test_fence_is_the_window_decided_on_123():
    lo, hi = spt.PHANTOM_PRICE_FENCE
    assert lo == "2026-08-05", "the first cloud capture is where the phantom prices begin"
    assert hi == "2026-08-10", "deploy-infra #31/#32 carried #119 and #122 on 2026-08-10"
    assert date.fromisoformat(lo) <= date.fromisoformat(hi)
    assert date.fromisoformat(lo) <= date(2026, 8, 8) <= date.fromisoformat(hi), \
        "the 2026-08-08 card is the one the decision names; it must sit inside the fence"


def test_every_measuring_query_excludes_the_fence(monkeypatch):
    cur = _run_report(
        monkeypatch,
        tiers=[("LOCK", 10, 4, 2.5, 3.0, 4.2, 1)],
        totals=(10, 4, 2.5, 1, "2026-08-11", "2026-09-01"),
        fenced=(12, -7.5, 0),
    )
    measuring = _measuring_queries(cur)
    assert len(measuring) == 2, "the tier breakdown and the total are the two figures reported"
    for sql, params in measuring:
        assert re.search(r"race_date NOT BETWEEN %s AND %s", sql), sql
        assert params == spt.PHANTOM_PRICE_FENCE


def test_report_says_what_the_fence_held_back(monkeypatch, capsys):
    _run_report(
        monkeypatch,
        tiers=[("LOCK", 10, 4, 2.5, 3.0, 4.2, 1)],
        totals=(10, 4, 2.5, 1, "2026-08-11", "2026-09-01"),
        fenced=(12, -7.5, 0),
    )
    out = capsys.readouterr().out
    assert "Fenced (#123): 2026-08-05 to 2026-08-10" in out
    assert "12 settled bet(s) worth -7.50u" in out
    # The fenced rows must not have leaked into the total line either.
    assert re.search(r"TOTAL\s+10\s+4\s", out), out


def test_fenced_count_is_inclusive_of_both_ends(monkeypatch):
    cur = _run_report(
        monkeypatch,
        tiers=[("LOCK", 1, 0, -1.0, 2.0, 3.0, 0)],
        totals=(1, 0, -1.0, 0, "2026-08-11", "2026-08-11"),
        fenced=(0, 0, 0),
    )
    fenced = [(sql, params) for sql, params in cur.calls
              if "SELECT" in sql and re.search(r"race_date BETWEEN %s AND %s", sql)]
    assert len(fenced) == 1
    assert fenced[0][1] == spt.PHANTOM_PRICE_FENCE


def test_all_rows_inside_the_fence_is_reported_as_fenced_not_as_no_data(monkeypatch, capsys):
    _run_report(monkeypatch, tiers=[], totals=None, fenced=(12, -7.5, 3))
    err = capsys.readouterr().err
    assert "inside the #123 fence" in err
    assert "No tier data available" not in err


def test_recording_is_untouched_by_the_fence():
    """The rows are the evidence for the open lone-order versus
    virtualisation question; `record` must keep writing them."""
    import inspect
    assert "PHANTOM_PRICE_FENCE" not in inspect.getsource(spt.cmd_record)
    assert "PHANTOM_PRICE_FENCE" not in inspect.getsource(spt.cmd_results)
