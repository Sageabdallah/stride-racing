"""The three-way verdict, pinned.

Two outcomes could not describe what was measured: a source that answers on
one probe and times out on the next is neither alive nor dead, and calling it
either loses the finding. Every count stated about this panel before retries
existed was a single observation read as a fact.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

MOD = (Path(__file__).resolve().parents[1] / "panel_liveness.py")


@pytest.fixture(scope="module")
def pl():
    spec = importlib.util.spec_from_file_location("panel_liveness", MOD)
    m = importlib.util.module_from_spec(spec)
    sys.modules["panel_liveness"] = m
    spec.loader.exec_module(m)
    return m


def _attempts(pl, monkeypatch, seq):
    it = iter(seq)
    monkeypatch.setattr(pl, "_attempt", lambda url: next(it))


def test_all_succeed_is_alive(pl, monkeypatch):
    _attempts(pl, monkeypatch, [(True, "HTTP 200")] * 3)
    verdict, detail = pl.probe("http://x", 3)
    assert verdict == pl.ALIVE
    assert "3/3" in detail


def test_all_fail_is_dead_and_names_every_distinct_reason(pl, monkeypatch):
    """An intermittent 429 among 404s is a different diagnosis from three
    consistent 404s, and a single-shot probe reports them identically."""
    _attempts(pl, monkeypatch,
              [(False, "HTTP 404"), (False, "HTTP 429"), (False, "HTTP 404")])
    verdict, detail = pl.probe("http://x", 3)
    assert verdict == pl.DEAD
    assert "HTTP 404" in detail and "HTTP 429" in detail


def test_one_success_in_three_is_flaky_not_dead(pl, monkeypatch):
    """The Back A Winner case: EMPTY at 26,296ms, then 122,750 chars."""
    _attempts(pl, monkeypatch,
              [(False, "ConnectTimeout"), (True, "HTTP 200"),
               (False, "ConnectTimeout")])
    verdict, detail = pl.probe("http://x", 3)
    assert verdict == pl.FLAKY
    assert "1/3 ok" in detail
    assert "ConnectTimeout" in detail


def test_two_successes_in_three_is_still_flaky_not_alive(pl, monkeypatch):
    """FLAKY is any disagreement, NOT a 2-of-3 majority.

    A majority rule would relabel a source that fails one morning in three as
    healthy. The panel is fetched once a day, so that is a third of race days
    with the source contributing nothing — the exact thing worth seeing.
    """
    _attempts(pl, monkeypatch,
              [(True, "HTTP 200"), (True, "HTTP 200"), (False, "HTTP 503")])
    verdict, _ = pl.probe("http://x", 3)
    assert verdict == pl.FLAKY, "a 2-of-3 majority must not read as ALIVE"


def test_verdicts_are_distinct_values(pl):
    assert len({pl.ALIVE, pl.FLAKY, pl.DEAD}) == 3


def test_the_default_attempt_count_can_actually_produce_flaky(pl):
    """Pin the DEFAULT, not just the parameter.

    Every test above passes `attempts` explicitly, so all five stayed green
    against ATTEMPTS = 1 — a default that makes FLAKY unreachable and returns
    the pre-flight to the single-shot go/no-go this replaced. A three-way
    classification whose middle case cannot occur is a two-way one with extra
    documentation.
    """
    assert pl.ATTEMPTS >= 2, (
        f"ATTEMPTS={pl.ATTEMPTS} cannot distinguish FLAKY from ALIVE/DEAD; "
        f"09c_upload_panel.sh would be back to deciding on one observation")
