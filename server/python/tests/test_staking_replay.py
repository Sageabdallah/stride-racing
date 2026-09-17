"""Replay refuses absent evidence and never rewrites the source rows."""
import copy
import json
import pytest
from staking_replay import (INSUFFICIENT_SAMPLE, METRICS, RULES, _synthetic_rows,
                            main, path_metrics, prepare_rows, replay)


def test_requires_explicit_bankroll_even_with_file():
    with pytest.raises(SystemExit) as exc:
        main(["--ledger-json", "unused.json"])
    assert exc.value.code == 2


def test_missing_database_refuses_instead_of_reporting_empty(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["--bankroll", "10000"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "REFUSED"
    assert report["reason"] == "DATABASE_URL unset and no --ledger-json supplied; no real ledger rows available"


def test_paired_floor_cannot_pass_on_settled_count_alone():
    rows = _synthetic_rows(501)
    rows[0]["shadow_kelly_json"] = None
    rows[1]["shadow_kelly_json"] = None
    before = copy.deepcopy(rows)
    report = replay(rows, bankroll=100, n_boot=20)
    assert report["eligible_settled_bets"] == 501 and report["rows_with_plan"] == 499
    assert report["status"] == INSUFFICIENT_SAMPLE
    assert "499/501" in report["reason"]
    for metrics in report["rules"].values():
        assert all(metrics[name] == INSUFFICIENT_SAMPLE for name in METRICS)
    assert rows == before


def test_json_export_replays_without_recomputing_plan(tmp_path, capsys):
    rows = _synthetic_rows(500)
    path = tmp_path / "rows.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    assert main(["--ledger-json", str(path), "--bankroll", "100", "--bootstrap-n", "20"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "OK" and report["applied"] is False
    assert report["bankroll"] == 100 and report["min_bets"] == 500
    assert report["input_row_conventions"][0]["ledger_implied_bankroll"] == 10000
    assert report["rules"][RULES[0]]["total_staked"] == 500
    assert report["ci95_race_day_bootstrap"]["race_days"] == 100


@pytest.mark.parametrize("plan", [{}, {"applied": True}, {"applied": False, "stake_pct": 3, "max_stake_pct": 2}])
def test_malformed_plans_fail_loudly(plan):
    rows = _synthetic_rows(1)
    rows[0]["shadow_kelly_json"] = plan
    with pytest.raises(ValueError):
        prepare_rows(rows)


def test_refusals_scratches_and_phantom_prices_are_excluded():
    rows = _synthetic_rows(4)
    rows[0]["refused"] = True
    rows[1]["settled"] = False
    rows[2]["race_date"] = "2026-08-06"
    paired, counts, _ = prepare_rows(rows)
    assert len(paired) == 1
    assert counts["excluded_unsettled_or_nonbet"] == 2
    assert counts["excluded_phantom_price_fence"] == 1


def test_zero_fraction_is_cash_not_missing_evidence():
    rows = _synthetic_rows(10)
    for row in rows:
        row["shadow_kelly_json"].update(stake_pct=0, stake=0)
    paired, counts, _ = prepare_rows(rows)
    metrics, detail = path_metrics(paired, 100, RULES[1])
    assert counts["rows_with_plan"] == 10
    assert detail["bets_staked"] == 0 and detail["ending_bankroll"] == 100
    assert metrics["mean_log_growth_per_selection"] == 0
    assert metrics["max_drawdown"] == 0 and metrics["net_roi_pct"] is None


def test_initial_losses_and_variable_stake_roi_use_shared_metrics():
    import math
    rows = [{"net_return": -1, "fraction": 0.01}, {"net_return": 2, "fraction": 0.02}]
    metrics, detail = path_metrics(rows, 100, RULES[1])
    assert metrics["max_drawdown"] == pytest.approx(1.0)
    assert metrics["max_drawdown_pct"] == pytest.approx(1.0)
    assert metrics["longest_losing_streak"] == 1
    assert metrics["net_roi_pct"] == pytest.approx(round(2.96 / 2.98 * 100, 2))
    assert metrics["mean_log_growth_per_selection"] == pytest.approx(math.log(1.0296)/2)


def test_clv_is_for_the_paired_window_and_missing_coverage_is_explicit():
    rows = _synthetic_rows(10)
    rows[0]["clv_pct"] = None
    report = replay(rows, bankroll=100, min_bets=10, n_boot=20)
    assert report["comparison"]["paired_clv_rows"] == 9
    assert report["comparison"]["paired_mean_clv_pct"] == 1
    assert report["comparison"]["positive_clv_over_full_paired_window"] is None


def test_bootstrap_preserves_unequal_day_blocks_and_undefined_samples():
    import numpy as np
    from roi_stats import race_day_bootstrap_ci
    seen = []
    def statistic(indices):
        values = list(map(int, indices))
        rest = values[:]
        while rest:
            if rest[0] == 0:
                assert rest[:2] == [0, 1]
                rest = rest[2:]
            else:
                assert rest[:3] == [2, 3, 4]
                rest = rest[3:]
        seen.append(values)
        return [sum(values), None]
    out = race_day_bootstrap_ci(["A", "A", "B", "B", "B"], statistic, n_boot=100)
    assert any(v == [0, 1, 0, 1] for v in seen)
    assert any(v == [2, 3, 4, 2, 3, 4] for v in seen)
    assert out["ci95"][1] == [None, None]
    assert out["nonfinite_resamples"] == [0, 100]
