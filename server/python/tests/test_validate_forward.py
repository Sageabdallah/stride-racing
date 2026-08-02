"""Task 09 acceptance: synthetic PASS and FAIL, gate blocks quoting, NO_BET
fallback on FAIL."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ship_criteria import gate_registry_pass
from validate_forward import REGISTRY, verdict_for


def _row(date, won, price, source="betfair", clv=1.0):
    return {"race_date": date, "settled": True, "refused": False, "won": won,
            "stake": 1.0, "price_taken": price, "price_source": source,
            "clv_pct": clv}


ENTRY = REGISTRY["VR-001"]


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
