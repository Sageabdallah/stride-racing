"""Audit 2026-09-06 small engine defects, pinned on both sides where cheap:

M4  mc_compute_staking published LIMIT with a positive stake and kelly_pct.
L1  an exact 0.0 edge/overlay was published as None (falsy-zero).
L2  wilson_interval accepted ``alpha`` and ignored it.
L6  MonteCarloEngine.simulate_meeting made seed=0 nondeterministic.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from statistics import NormalDist

import pytest

import mc_api

racing_system = mc_api.racing_system

ROOT = Path(__file__).resolve().parents[3]


def _load_root_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestWilsonAlpha:
    def test_shipped_alpha_is_byte_identical(self):
        lo, hi = racing_system.wilson_interval(120, 1000, alpha=0.10)
        z = 1.6448536269514722
        phat = 0.12
        denom = 1 + z ** 2 / 1000
        centre = phat + z ** 2 / 2000
        margin = z * math.sqrt((phat * (1 - phat) + z ** 2 / 4000) / 1000)
        assert (lo, hi) == ((centre - margin) / denom, (centre + margin) / denom)
        assert racing_system.MC_SIM_LIMITS["ci_alpha"] == 0.10

    def test_other_alphas_are_honoured(self):
        lo10, hi10 = racing_system.wilson_interval(120, 1000, alpha=0.10)
        lo05, hi05 = racing_system.wilson_interval(120, 1000, alpha=0.05)
        assert lo05 < lo10 and hi05 > hi10, "a 95% interval must be wider than a 90% one"
        z = NormalDist().inv_cdf(0.975)
        phat = 0.12
        denom = 1 + z ** 2 / 1000
        centre = phat + z ** 2 / 2000
        margin = z * math.sqrt((phat * (1 - phat) + z ** 2 / 4000) / 1000)
        assert (lo05, hi05) == pytest.approx(((centre - margin) / denom, (centre + margin) / denom))

    def test_zero_trials(self):
        assert racing_system.wilson_interval(0, 0, alpha=0.05) == (0.0, 0.0)


class TestStakingLimitCarriesNoMoney:
    @staticmethod
    def _candidate():
        return {"win_prob_sim": 0.20, "market_odds": 6.0, "stability_score": 70.0, "ev": 0.2}

    def test_capped_out_bet_is_limit_with_zero_stake(self):
        out = racing_system.mc_compute_staking(
            self._candidate(), "fractional_kelly", base_unit=10.0, bankroll=1000.0,
            daily_remaining=0, track_remaining=5)
        assert out["status"] == "LIMIT"
        assert out["units"] == 0
        assert out["stake"] == 0
        assert out["kelly_pct"] == 0.0

    def test_track_cap_alone_also_zeroes(self):
        out = racing_system.mc_compute_staking(
            self._candidate(), "kelly", base_unit=10.0, bankroll=1000.0,
            daily_remaining=5, track_remaining=0)
        assert (out["status"], out["stake"], out["kelly_pct"]) == ("LIMIT", 0, 0.0)

    def test_uncapped_bet_keeps_the_kelly_overlay(self):
        out = racing_system.mc_compute_staking(
            self._candidate(), "fractional_kelly", base_unit=10.0, bankroll=1000.0,
            daily_remaining=5, track_remaining=5)
        assert out["status"] == "BET"
        assert out["units"] == 3
        assert out["kelly_pct"] > 0
        assert out["stake"] == max(30.0, out["kelly_pct"] / 100.0 * 1000.0)

    def test_watch_stays_watch(self):
        out = racing_system.mc_compute_staking(
            {"win_prob_sim": 0.05, "market_odds": 6.0, "stability_score": 70.0},
            "fractional_kelly", base_unit=10.0, bankroll=1000.0,
            daily_remaining=5, track_remaining=5)
        assert (out["status"], out["stake"], out["kelly_pct"]) == ("WATCH", 0, 0.0)


class TestExactZeroEdgeIsNotNone:
    def test_analyze_publishes_a_zero_edge(self):
        # value_edge = (model_prob - 1/sp) * 100; the expression is the
        # published one, evaluated exactly as analyze() does.
        model_prob, sp = 0.25, 4.0
        value_edge = (model_prob - 1 / sp) * 100
        assert value_edge == 0.0
        published = round(value_edge, 2) if value_edge is not None else None
        assert published == 0.0
        src = (ROOT / "racing_system_v8.3_mc.py").read_text(encoding="utf-8")
        assert "'value_edge': round(value_edge, 2) if value_edge is not None else None" in src
        assert "'value_edge': round(value_edge, 2) if value_edge else None" not in src

    def test_monte_carlo_engine_publishes_zero_edge_and_overlay(self):
        src = (ROOT / "monte_carlo.py").read_text(encoding="utf-8")
        assert "value_edge=round(value_edge, 4) if value_edge is not None else None" in src
        assert "overlay_percent=round(overlay_pct, 2) if overlay_pct is not None else None" in src


class TestMeetingSeedZero:
    def test_seed_zero_is_deterministic(self):
        mc = _load_root_module("monte_carlo_under_test", "monte_carlo.py")
        seen = []

        class _Engine(mc.MonteCarloEngine):
            def __init__(self):
                pass

            def simulate_race(self, race, n_sims=None, seed=None, with_exotics=False):
                seen.append(seed)
                return None

        _Engine().simulate_meeting(["r1", "r2", "r3"], seed=0)
        assert seen == [0, 1, 2], "seed=0 must not degrade to None"
        seen.clear()
        _Engine().simulate_meeting(["r1", "r2"], seed=None)
        assert seen == [None, None]
