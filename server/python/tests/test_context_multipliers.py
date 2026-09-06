"""Audit 2026-09-06 H3/H4 (and SYSTEM_MAP §7b.2): the three context
multipliers in calibrate_and_score.

Flag off must be today's exact arithmetic — inert or mis-scaled as it is —
because every downstream raw-probability threshold was tuned against it.
Flag on must realise the documented bands from the inputs mc_api actually
publishes.
"""

from __future__ import annotations

import pytest

import run_tips_pipeline as pipeline

FLAGS = ("STRIDE_CTX_MULT_FITNESS", "STRIDE_CTX_MULT_BIAS", "STRIDE_CTX_MULT_JOCKEY",
         "STRIDE_CTX_MULT_DIAG")


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for flag in FLAGS:
        monkeypatch.delenv(flag, raising=False)


def _mc_horse(points=0, readiness=0.5, jockey=1.0):
    """The shape mc_api publishes: points at top level, readiness nested."""
    return {
        "trackBiasPoints": points,
        "fitnessData": {"fitnessReadinessScore": readiness},
        "jockeyMomentumAdjustment": jockey,
    }


class TestFlagsOffAreTodaysArithmetic:
    def test_fitness_is_inert_because_the_key_is_never_at_top_level(self):
        f, _, _ = pipeline._context_multipliers(_mc_horse(readiness=1.0))
        assert f == pytest.approx(1.00)
        f, _, _ = pipeline._context_multipliers(_mc_horse(readiness=0.0))
        assert f == pytest.approx(1.00)

    def test_bias_divides_points_by_100_so_neutral_is_a_five_percent_cut(self):
        _, b0, _ = pipeline._context_multipliers(_mc_horse(points=0))
        _, b25, _ = pipeline._context_multipliers(_mc_horse(points=25))
        _, b49, _ = pipeline._context_multipliers(_mc_horse(points=49))
        _, bneg, _ = pipeline._context_multipliers(_mc_horse(points=-18))
        assert b0 == pytest.approx(0.95)
        assert b25 == pytest.approx(0.975)
        assert b49 == pytest.approx(0.999)
        assert bneg == pytest.approx(0.932)
        # the only way to score x1.00 today is to not have been scored at all
        _, b_missing, _ = pipeline._context_multipliers({})
        assert b_missing == pytest.approx(1.00)

    def test_jockey_is_inert_because_the_feature_never_reached_the_result(self):
        _, _, j = pipeline._context_multipliers(_mc_horse(jockey=1.15))
        assert j == pytest.approx(1.00)
        # the legacy key, were it ever present, is still honoured and clamped
        _, _, j = pipeline._context_multipliers({"jockey_momentum_adjustment": 1.5})
        assert j == pytest.approx(1.20)


class TestFitnessFlag:
    def test_reads_the_nested_readiness_on_its_real_scale(self, monkeypatch):
        monkeypatch.setenv("STRIDE_CTX_MULT_FITNESS", "true")
        assert pipeline._context_multipliers(_mc_horse(readiness=0.0))[0] == pytest.approx(0.95)
        assert pipeline._context_multipliers(_mc_horse(readiness=0.5))[0] == pytest.approx(1.00)
        assert pipeline._context_multipliers(_mc_horse(readiness=1.0))[0] == pytest.approx(1.05)

    def test_absent_or_malformed_is_neutral_not_a_penalty(self, monkeypatch):
        monkeypatch.setenv("STRIDE_CTX_MULT_FITNESS", "true")
        assert pipeline._context_multipliers({})[0] == pytest.approx(1.00)
        assert pipeline._context_multipliers({"fitnessData": None})[0] == pytest.approx(1.00)
        assert pipeline._context_multipliers({"fitnessData": {"fitnessReadinessScore": "n/a"}})[0] == pytest.approx(1.00)

    def test_only_the_fitness_multiplier_moves(self, monkeypatch):
        monkeypatch.setenv("STRIDE_CTX_MULT_FITNESS", "true")
        _, b, j = pipeline._context_multipliers(_mc_horse(points=0, jockey=1.15))
        assert (b, j) == (pytest.approx(0.95), pytest.approx(1.00))


class TestBiasFlag:
    def test_neutral_is_one_and_the_band_is_realised(self, monkeypatch):
        monkeypatch.setenv("STRIDE_CTX_MULT_BIAS", "true")
        assert pipeline._context_multipliers(_mc_horse(points=0))[1] == pytest.approx(1.00)
        assert pipeline._context_multipliers(_mc_horse(points=25))[1] == pytest.approx(1.05)
        assert pipeline._context_multipliers(_mc_horse(points=49))[1] == pytest.approx(1.05)  # clamped
        assert pipeline._context_multipliers(_mc_horse(points=-10))[1] == pytest.approx(0.98)
        assert pipeline._context_multipliers(_mc_horse(points=-18))[1] == pytest.approx(0.964)
        assert pipeline._context_multipliers(_mc_horse(points=-30))[1] == pytest.approx(0.95)  # clamped

    def test_monotone_in_points(self, monkeypatch):
        monkeypatch.setenv("STRIDE_CTX_MULT_BIAS", "true")
        mults = [pipeline._context_multipliers(_mc_horse(points=p))[1] for p in range(-18, 50)]
        assert mults == sorted(mults)

    def test_unscored_runner_is_neutral(self, monkeypatch):
        monkeypatch.setenv("STRIDE_CTX_MULT_BIAS", "true")
        assert pipeline._context_multipliers({})[1] == pytest.approx(1.00)


class TestJockeyFlag:
    def test_reads_the_published_key_and_clamps(self, monkeypatch):
        monkeypatch.setenv("STRIDE_CTX_MULT_JOCKEY", "true")
        assert pipeline._context_multipliers(_mc_horse(jockey=1.15))[2] == pytest.approx(1.15)
        assert pipeline._context_multipliers(_mc_horse(jockey=1.50))[2] == pytest.approx(1.20)
        assert pipeline._context_multipliers(_mc_horse(jockey=0.50))[2] == pytest.approx(0.85)
        assert pipeline._context_multipliers({})[2] == pytest.approx(1.00)


class TestDiagLine:
    def test_reports_min_mean_max_and_flags(self, monkeypatch):
        line = pipeline._ctx_mult_diag_line([(0.95, 1.0, 1.0), (1.05, 1.0, 1.2)])
        assert line.startswith("[CTX_MULT] n=2 ")
        assert "fitness=0.950/1.000/1.050" in line
        assert "jockey=1.000/1.100/1.200" in line
        assert line.endswith("flags=none")
        monkeypatch.setenv("STRIDE_CTX_MULT_BIAS", "true")
        assert pipeline._ctx_mult_diag_line([(1, 1, 1)]).endswith("flags=STRIDE_CTX_MULT_BIAS")
        assert pipeline._ctx_mult_diag_line([]) == "[CTX_MULT] no runners"
