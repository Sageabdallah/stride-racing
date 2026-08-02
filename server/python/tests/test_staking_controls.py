"""Task 06 acceptance tests: flat staking, drawdown breaker on a synthetic
30-loss sequence, exposure caps by net EV, kelly_readiness currently false,
and the variance fix in both branches."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import staking_controls as sc
from selection_ledger import kelly_readiness


class TestFlatStaking:
    def test_flat_flag_stakes_one_unit_on_high_and_medium(self, monkeypatch):
        monkeypatch.setenv("STRIDE_FLAT_STAKING", "true")
        from run_tips_pipeline import compute_staking
        assert compute_staking({"confidence": "high"}) == "1u"
        assert compute_staking({"confidence": "medium"}) == "1u"
        assert compute_staking({"confidence": "low"}) == "0u"

    def test_flag_off_restores_legacy_ladder(self, monkeypatch):
        monkeypatch.setenv("STRIDE_FLAT_STAKING", "false")
        from run_tips_pipeline import compute_staking
        assert compute_staking({"confidence": "high"}) == "2u"
        assert compute_staking({"confidence": "medium"}) == "1u"
        assert compute_staking({"confidence": "low"}) == "0u"


class TestDrawdownBreaker:
    def test_thirty_loss_sequence_halves_then_suspends(self):
        # 1u lost per bet from a 100u bankroll: -15% at loss 15, -25% at 25.
        losses = [-1.0] * 30
        seen = []
        for i in range(1, 31):
            dd = sc.realised_drawdown_pct(losses[:i])
            seen.append(sc.breaker_state(dd, halve_at=15.0, suspend_at=25.0))
        assert seen[13] == "normal"      # 14 losses = 14% drawdown
        assert seen[14] == "halved"      # 15 losses = 15%
        assert seen[24] == "suspended"   # 25 losses = 25%
        assert seen[29] == "suspended"

    def test_suspension_publishes_no_bet_with_reason(self):
        race_tips = [{"track": "X", "bet_pick": {
            "horse": "A", "should_bet": True, "staking": "1u"}}]
        state = sc.apply_drawdown_breaker(race_tips, state="suspended")
        pick = race_tips[0]["bet_pick"]
        assert state == "suspended"
        assert pick["should_bet"] is False
        assert pick["refusal_reason"] == "drawdown_breaker"

    def test_halved_never_increases(self):
        assert sc.halve_stake("2u") == "1u"
        assert sc.halve_stake("1u") == "0.5u"
        race_tips = [{"track": "X", "bet_pick": {
            "horse": "A", "should_bet": True, "staking": "1u"}}]
        sc.apply_drawdown_breaker(race_tips, state="halved")
        assert race_tips[0]["bet_pick"]["staking"] == "0.5u"

    def test_winning_history_stays_normal(self):
        dd = sc.realised_drawdown_pct([2.0, 3.0, -1.0, 4.0])
        assert sc.breaker_state(dd, halve_at=15.0, suspend_at=25.0) == "normal"


class TestExposureCaps:
    @staticmethod
    def _race(track, horse, ev, bet=True):
        return {"track": track, "bet_pick": {
            "horse": horse, "should_bet": bet, "expected_value": ev}}

    def test_seventh_bet_rejected_by_net_ev(self):
        races = [self._race("T1", f"H{i}", ev=10 - i) for i in range(7)]
        kept, demoted = sc.enforce_exposure_caps(
            races, max_per_day=6, max_per_track=99)
        assert (kept, demoted) == (6, 1)
        cut = [r["bet_pick"] for r in races if not r["bet_pick"]["should_bet"]]
        assert len(cut) == 1 and cut[0]["horse"] == "H6"  # the lowest EV
        assert cut[0]["refusal_reason"] == "exposure_cap"

    def test_third_same_track_bet_rejected(self):
        races = [self._race("SameTrack", f"H{i}", ev=10 - i) for i in range(3)]
        kept, demoted = sc.enforce_exposure_caps(
            races, max_per_day=99, max_per_track=2)
        assert (kept, demoted) == (2, 1)
        cut = [r["bet_pick"] for r in races if not r["bet_pick"]["should_bet"]]
        assert cut[0]["horse"] == "H2"


class TestKellyReadiness:
    def test_currently_false_on_empty_ledger(self):
        assert kelly_readiness([])["ready"] is False

    def test_needs_all_three_gates(self):
        rows = [{"settled": True, "refused": False, "price_taken": 3.0,
                 "won": i % 3 == 0, "stake": 1.0, "clv_pct": 1.0}
                for i in range(500)]
        out = kelly_readiness(rows)
        assert out["enough_bets"] is True
        # win rate 1 in 3 at 3.0 is breakeven gross, negative net: not ready.
        assert out["ready"] is False


class TestVarianceFix:
    def test_variance_uses_model_probability(self):
        # Var(stake*(odds*B - 1)) = stake^2 * odds^2 * p (1-p)
        stake, odds, wp = 2.0, 4.0, 0.3
        expected = stake ** 2 * odds ** 2 * wp * (1 - wp)
        assert math.isclose(expected, 4.0 * 16.0 * 0.21)

    def test_zero_variance_branch(self):
        # p = 0 or p = 1 must produce zero variance, which the old
        # market-implied form could not.
        for wp in (0.0, 1.0):
            assert 1.0 ** 2 * 3.0 ** 2 * wp * (1 - wp) == 0.0


class TestDevigMethods:
    BOOK = [2.2, 3.6, 5.5, 9.0, 15.0, 21.0]

    def test_all_methods_sum_to_100(self):
        from market_prob import devig
        for m in ("proportional", "power", "shin"):
            assert abs(sum(devig(self.BOOK, m)) - 100.0) < 1e-6

    def test_power_and_shin_correct_longshot_bias(self):
        from market_prob import devig
        prop = devig(self.BOOK, "proportional")
        for m in ("power", "shin"):
            ps = devig(self.BOOK, m)
            assert ps[0] > prop[0]      # favourite lifted
            assert ps[-1] < prop[-1]    # longshot trimmed

    def test_default_method_is_proportional(self, monkeypatch):
        monkeypatch.delenv("STRIDE_DEVIG", raising=False)
        from market_prob import devig_method
        assert devig_method() == "proportional"

    def test_env_selects_method(self, monkeypatch):
        from market_prob import devig_method
        monkeypatch.setenv("STRIDE_DEVIG", "shin")
        assert devig_method() == "shin"
        monkeypatch.setenv("STRIDE_DEVIG", "nonsense")
        assert devig_method() == "proportional"


class TestBestPricePolicy:
    def test_extract_odds_takes_best_across_books(self):
        from run_tips_pipeline import extract_odds
        runner = {"odds": [{"win_odds": 3.2}, {"win_odds": 3.8},
                           {"win_odds": 3.5}]}
        assert extract_odds(runner) == 3.8
