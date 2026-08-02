"""Task 07 acceptance: with the gate-only flag on, no code path returns BET
where the EV gate returned NO_BET, across every crowd tier including the
unanimous-crowd case."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from consensus_blender import crowd_bet_decision
from run_tips_pipeline import apply_crowd_gate

ALL_TIERS = [
    ("CONFIRMED", 80), ("CONFIRMED", 40),
    ("CROWD_ONLY", 90), ("CROWD_ONLY", 71), ("CROWD_ONLY", 60),
    ("CROWD_ONLY", 50), ("CROWD_ONLY", 30),
    ("CROWD_ONLY_WEAK", 100), ("CROWD_ONLY_WEAK", 80),
    ("CROWD_ONLY_WEAK", 40),
    ("MODEL_ONLY", 0), ("REJECTED", 0), ("SKIP", 0),
]


class TestGateOnlyNeverPromotes:
    def test_no_tier_flips_no_bet_to_bet(self):
        for classification, cs in ALL_TIERS:
            gate_bet, _ = crowd_bet_decision(classification, cs)
            new_bet, action = apply_crowd_gate(False, gate_bet, gate_only=True)
            assert new_bet is False, (classification, cs)
            assert action in ("blocked", "none"), (classification, cs)

    def test_unanimous_crowd_case_specifically(self):
        gate_bet, _ = crowd_bet_decision("CROWD_ONLY_WEAK", 100)
        assert gate_bet is True  # the legacy path would have promoted
        new_bet, action = apply_crowd_gate(False, gate_bet, gate_only=True)
        assert (new_bet, action) == (False, "blocked")

    def test_veto_and_confirm_survive(self):
        assert apply_crowd_gate(True, False, gate_only=True) == (False, "veto")
        assert apply_crowd_gate(True, True, gate_only=True) == (True, "confirm")

    def test_flag_off_restores_legacy_promotion(self):
        for classification, cs in ALL_TIERS:
            gate_bet, _ = crowd_bet_decision(classification, cs)
            new_bet, action = apply_crowd_gate(False, gate_bet, gate_only=False)
            assert new_bet is gate_bet
            if gate_bet:
                assert action == "promote"
