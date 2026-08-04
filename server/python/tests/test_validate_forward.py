"""Task 09 acceptance: synthetic PASS and FAIL, gate blocks quoting, NO_BET
fallback on FAIL."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import date

from ship_criteria import gate_registry_pass
from validate_forward import REGISTRY, verdict_for, window_read_allowed


def _row(date, won, price, source="betfair", clv=1.0):
    return {"race_date": date, "settled": True, "refused": False, "won": won,
            "stake": 1.0, "price_taken": price, "price_source": source,
            "clv_pct": clv}


ENTRY = REGISTRY["VR-001"]

# The fixed-date guard mechanics used to be asserted against VR-001. VR-001 is
# now INVALIDATED and refuses at every date, so testing date arithmetic through
# it would assert the wrong thing. The mechanics get a fixture that is only a
# date window; VR-001's own behaviour is asserted separately below.
FIXED_DATE_ENTRY = {
    "window_b": ("2026-08-02", "2026-09-13"),
    "price_min": 2.0, "price_max": 15.0,
    "price_sources": ENTRY["price_sources"], "min_bets": 200,
}


class TestWindowGuard:
    """The no-peeking guard is mechanical: outcomes are unreadable until the
    day after window B closes, however the runner is invoked."""

    def test_locked_during_window(self):
        allowed, why = window_read_allowed(FIXED_DATE_ENTRY, today=date(2026, 8, 15))
        assert not allowed and "2026-09-14" in why

    def test_locked_on_close_day_itself(self):
        allowed, _ = window_read_allowed(FIXED_DATE_ENTRY, today=date(2026, 9, 13))
        assert not allowed

    def test_open_from_the_day_after_close(self):
        allowed, why = window_read_allowed(FIXED_DATE_ENTRY, today=date(2026, 9, 14))
        assert allowed and why == ""


class TestInvalidatedEntryNeverReads:
    """VR-001-C1 invalidated the entry. Before this was in REGISTRY the
    invalidation lived only in markdown, so `validate_forward.py VR-001` on
    2026-09-14 would have emitted a verdict over a window that never validly
    ran."""

    def test_refuses_inside_the_window(self):
        allowed, why = window_read_allowed(ENTRY, today=date(2026, 8, 15))
        assert not allowed and "INVALIDATED" in why

    def test_refuses_after_the_window_closes(self):
        # The date the old guard would have opened it.
        allowed, why = window_read_allowed(ENTRY, today=date(2026, 9, 14))
        assert not allowed and "INVALIDATED" in why

    def test_refuses_far_in_the_future(self):
        allowed, _ = window_read_allowed(ENTRY, today=date(2027, 1, 1))
        assert not allowed

    def test_names_its_successor(self):
        assert "VR-002" in ENTRY["invalidated"]


class TestDraftEntryNeverReads:
    """VR-002 is DRAFT until the open date is filled at the deploy that
    changes the bet population. An unfilled open date must refuse, not scan
    from an assumed start."""

    def test_vr002_is_registered_and_draft(self):
        assert "VR-002" in REGISTRY
        assert REGISTRY["VR-002"]["window_b"][0] is None

    def test_refuses_while_draft(self):
        allowed, why = window_read_allowed(REGISTRY["VR-002"],
                                           today=date(2026, 8, 15))
        assert not allowed and "DRAFT" in why


class TestSampleClose:
    """Amended 2026-08-04 before open: window B closes on the 200th
    qualifying settled bet or at the hard stop, because 26 observation days
    x 6 bets/day cannot reach 200 inside 42 days."""

    def _opened(self, **kw):
        e = dict(REGISTRY["VR-002"])
        e["window_b"] = ("2026-08-05", "2026-12-31")
        e.update(kw)
        return e

    def test_locked_below_the_minimum(self):
        allowed, why = window_read_allowed(self._opened(), today=date(2026, 10, 1),
                                           n_settled=199)
        assert not allowed and "199" in why

    def test_opens_on_reaching_the_minimum(self):
        allowed, why = window_read_allowed(self._opened(), today=date(2026, 10, 1),
                                           n_settled=200)
        assert allowed and why == ""

    def test_opens_at_the_hard_stop_even_when_short(self):
        allowed, _ = window_read_allowed(self._opened(), today=date(2027, 1, 1),
                                         n_settled=12)
        assert allowed

    def test_unknown_n_stays_locked(self):
        allowed, why = window_read_allowed(self._opened(), today=date(2026, 10, 1))
        assert not allowed and "unknown" in why


class TestVerdicts:
    def test_synthetic_pass(self):
        # 60 percent winners at 2.5: hugely profitable, positive CLV.
        rows = [_row("2026-08-10", i % 5 < 3, 2.5) for i in range(300)]
        out = verdict_for(ENTRY, rows)
        assert out["verdict"] == "PASS", out

    def test_synthetic_fail(self):
        # 10 percent winners at 2.5: catastrophic ROI, negative CLV.
        rows = [_row("2026-08-10", i % 10 == 0, 2.5, clv=-2.0)
                for i in range(300)]
        out = verdict_for(ENTRY, rows)
        assert out["verdict"] == "FAIL", out

    def test_insufficient_sample(self):
        rows = [_row("2026-08-10", True, 2.5) for _ in range(10)]
        assert verdict_for(ENTRY, rows)["verdict"] == "INSUFFICIENT_SAMPLE"

    def test_rule_filters_are_fixed(self):
        # Out-of-band price, wrong source, outside window B: none selectable.
        rows = ([_row("2026-08-10", True, 30.0)] +
                [_row("2026-08-10", True, 2.5, source="racecard_legacy")] +
                [_row("2026-10-01", True, 2.5)])
        assert verdict_for(ENTRY, rows)["n_bets"] == 0


class TestPromotionGate:
    def test_no_pass_no_quote(self):
        assert gate_registry_pass("REGISTERED")["quotable"] is False
        assert gate_registry_pass(None)["quotable"] is False

    def test_pass_quotes(self):
        assert gate_registry_pass("PASS") == {"quotable": True,
                                              "action": "quote"}

    def test_fail_falls_back_to_no_bet(self):
        out = gate_registry_pass("FAIL")
        assert out["quotable"] is False
        assert out["action"] == "no_bet_fallback"
