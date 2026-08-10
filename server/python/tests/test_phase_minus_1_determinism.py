"""The simulator must replay exactly under the persisted race seed."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("stride_mc_replay", ROOT / "racing_system_v8.3_mc.py")
MC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MC)


class _Race:
    is_valid = True
    course = "Randwick"
    going = "Good"


class _Model:
    horse_history = {}
    calculated_leader_rates = {}
    supp_stats = None

    @staticmethod
    def _get_horse_key(_runner):
        return ""


ANALYSIS = [
    {"horse": "A", "number": 1, "model_prob": 46.0, "style": "leader", "runner_obj": None},
    {"horse": "B", "number": 2, "model_prob": 32.0, "style": "on_pace", "runner_obj": None},
    {"horse": "C", "number": 3, "model_prob": 22.0, "style": "backmarker", "runner_obj": None},
]


def test_same_seed_replays_full_monte_carlo_result():
    first = MC.simulate_race_monte_carlo(_Race(), ANALYSIS, _Model(), mc_sims=750, seed=9173)
    second = MC.simulate_race_monte_carlo(_Race(), ANALYSIS, _Model(), mc_sims=750, seed=9173)
    assert first == second


def test_different_seed_changes_simulated_result():
    first = MC.simulate_race_monte_carlo(_Race(), ANALYSIS, _Model(), mc_sims=750, seed=9173)
    second = MC.simulate_race_monte_carlo(_Race(), ANALYSIS, _Model(), mc_sims=750, seed=9174)
    assert first != second
