"""shadow_flip_review computes the registered gate-3 flip criteria.

Before it, gate_status counted evidence DAYS and nothing computed the criteria
in docs/roi-roadmap/shadow-flip-criteria.md; a flip review meant reading every
day file by hand. These tests pin the thresholds to the document's numbers,
the dirty-day restart rule, the per-race single-race rule (with the proxy for
evidence emitted before per-race field sizes existed), the store roundtrip and
the additive n_runners field in shadow_calibrator_compare.
"""

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

SERVER_PYTHON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_PYTHON))

import shadow_flip_review as sfr  # noqa: E402


def _day(rng, n_races=6, flip_every=50, scale=1.0):
    return sfr._synthetic_serve_day(rng, "d", n_races=n_races, scale=scale, flip_every=flip_every)


def test_thresholds_are_the_registered_ones():
    assert sfr.MIN_CLEAN_DAYS == 5
    assert sfr.TOP3_FLIP_RATE_MAX == 0.15
    assert sfr.TRANSITION_RATE_MAX == 0.05
    assert sfr.SINGLE_RACE_TRANSITION_MAX == 0.25
    assert sfr.SUM_TOLERANCE == 1e-6
    assert sfr.OUTLIER_FACTOR == 10.0


def test_streak_counts_back_from_the_latest_day_and_restarts_on_dirty():
    days = [{"date": f"2026-08-0{i}", "dirty": False} for i in range(1, 8)]
    assert sfr._streak(days) == 7
    days[3]["dirty"] = True
    assert sfr._streak(days) == 3
    days[-1]["dirty"] = True
    assert sfr._streak(days) == 0


def test_missing_empty_and_malformed_days_are_dirty_with_reasons():
    assert sfr.summarise_serve_day("d", None)["reason"].startswith("day file missing")
    assert sfr.summarise_serve_day("d", {"not": "a list"})["reason"].startswith("day file is not a list")
    assert sfr.summarise_serve_day("d", [])["reason"].startswith("no scored runners")
    assert sfr.summarise_serve_day("d", [{"track": "X", "race_number": 1, "runners": []}])["dirty"]


def test_serve_day_summary_numbers():
    blocks = [{"track": "T", "race_number": 1, "runners": [
        {"horse": "a", "delta_pp": 2.0, "tier_change": True},
        {"horse": "b", "delta_pp": -2.0, "tier_change": False},
        {"horse": "c", "delta_pp": 6.0, "tier_change": False},
        {"horse": "bad", "delta_pp": "n/a"},   # skipped, not counted
    ]}]
    s = sfr.summarise_serve_day("2026-08-10", blocks)
    assert not s["dirty"]
    assert s["n_races"] == 1 and s["n_runners"] == 3 and s["tier_changes"] == 1
    assert s["mean_delta_pp"] == 2.0 and s["max_abs_delta_pp"] == 6.0
    assert s["races"][0]["max_abs_delta_pp"] == 6.0


def test_fewer_than_five_clean_days_is_wait_not_fail():
    rng = np.random.default_rng(1)
    days = {f"2026-08-1{i}": _day(rng) for i in range(4)}
    rep = sfr.review_serve_liveness(days)
    c = {x["criterion"]: x for x in rep["criteria"]}
    assert c["clean_days"]["status"] == sfr.WAIT and c["clean_days"]["streak"] == 4
    assert rep["auto_verdict"] == sfr.WAIT


def test_five_clean_days_pass_and_flip_rate_is_judged_on_clean_days():
    rng = np.random.default_rng(2)
    days = {f"2026-08-1{i}": _day(rng) for i in range(5)}
    rep = sfr.review_serve_liveness(days)
    c = {x["criterion"]: x for x in rep["criteria"]}
    assert c["clean_days"]["status"] == sfr.PASS
    assert c["top3_flip_rate"]["status"] == sfr.PASS
    total = sum(sfr.summarise_serve_day(d, b)["n_runners"] for d, b in days.items())
    assert c["top3_flip_rate"]["n_runners"] == total
    assert c["delta_stability"]["status"] == sfr.REVIEW, "stability is the reviewer's call"
    assert rep["auto_verdict"] == sfr.PASS


def test_flip_rate_boundary_at_fifteen_percent():
    runners = [{"horse": str(i), "delta_pp": 1.0, "tier_change": i < 3} for i in range(20)]
    day = [{"track": "T", "race_number": 1, "runners": runners}]
    rep = sfr.review_serve_liveness({"2026-08-10": day})
    c = {x["criterion"]: x for x in rep["criteria"]}
    assert c["top3_flip_rate"]["rate"] == 0.15 and c["top3_flip_rate"]["status"] == sfr.PASS
    runners[3]["tier_change"] = True
    rep = sfr.review_serve_liveness({"2026-08-10": day})
    assert {x["criterion"]: x for x in rep["criteria"]}["top3_flip_rate"]["status"] == sfr.FAIL


def test_order_of_magnitude_race_fails_stability():
    rng = np.random.default_rng(3)
    days = {f"2026-08-1{i}": _day(rng) for i in range(6)}
    for r in days["2026-08-12"][2]["runners"]:
        r["delta_pp"] = round(r["delta_pp"] * 80, 2)
    rep = sfr.review_serve_liveness(days)
    c = {x["criterion"]: x for x in rep["criteria"]}
    assert c["delta_stability"]["status"] == sfr.FAIL
    assert c["delta_stability"]["outlier_races"][0]["race_number"] == 3
    assert rep["auto_verdict"] == sfr.FAIL


def test_largest_delta_races_are_listed_for_the_reviewer():
    rng = np.random.default_rng(4)
    days = {f"2026-08-1{i}": _day(rng) for i in range(5)}
    rep = sfr.review_serve_liveness(days)
    c = {x["criterion"]: x for x in rep["criteria"]}["delta_stability"]
    largest = c["largest_delta_races"]
    assert 1 <= len(largest) <= sfr.N_LARGEST_DELTA_RACES
    assert largest == sorted(largest, key=lambda r: -r["max_abs_delta_pp"])


def test_dirty_day_limitation_is_stated():
    rng = np.random.default_rng(5)
    rep = sfr.review_serve_liveness({"2026-08-10": _day(rng)})
    c = {x["criterion"]: x for x in rep["criteria"]}["no_errored_day"]
    assert "stderr" in c["limitation"]


# ------------------------------------------------------------- renormalisation

def _renorm_fixture(n_days=6, brier_cur=0.0850, brier_ren=0.0845):
    days = {f"2026-08-1{i}": sfr._synthetic_renorm_day(f"2026-08-1{i}", brier_cur, brier_ren, 200, 180, 5)
            for i in range(n_days)}
    pooled = {"status": "ok", "n_rows": 200 * n_days,
              "variants": {"current": {"brier": brier_cur, "race_sums": {"within_tolerance": False}},
                           "current_renormalised": {"brier": brier_ren,
                                                    "race_sums": {"within_tolerance": True}}},
              "tier_transitions": {"available": True, "base": "current",
                                   "pairs": {sfr.RENORM_PAIR: {"n_compared": 1080, "n_transitions": 30,
                                                               "matrix": {"medium>low": 30},
                                                               "races": []}}}}
    return days, pooled


def test_renorm_pass_path_uses_pooled_file():
    days, pooled = _renorm_fixture()
    rep = sfr.review_renormalisation(days, pooled)
    c = {x["criterion"]: x for x in rep["criteria"]}
    assert c["pooled_brier_not_worse"]["status"] == sfr.PASS
    assert c["pooled_brier_not_worse"]["source"] == "calibrator_compare_pooled.json"
    assert c["field_sums_unity"]["status"] == sfr.PASS
    assert c["transition_rate"]["status"] == sfr.PASS and abs(c["transition_rate"]["rate"] - 30 / 1080) < 1e-12
    assert c["single_race_signoff"]["status"] == sfr.PASS
    assert c["transition_matrix_reviewed"]["status"] == sfr.REVIEW
    assert rep["auto_verdict"] == sfr.PASS


def test_renorm_equal_brier_passes_worse_fails():
    days, pooled = _renorm_fixture(brier_cur=0.0850, brier_ren=0.0850)
    assert {x["criterion"]: x for x in sfr.review_renormalisation(days, pooled)["criteria"]}[
        "pooled_brier_not_worse"]["status"] == sfr.PASS
    days, pooled = _renorm_fixture(brier_cur=0.0850, brier_ren=0.0851)
    rep = sfr.review_renormalisation(days, pooled)
    assert {x["criterion"]: x for x in rep["criteria"]}["pooled_brier_not_worse"]["status"] == sfr.FAIL
    assert rep["auto_verdict"] == sfr.FAIL


def test_renorm_without_pooled_file_aggregates_day_files_row_weighted():
    days, _ = _renorm_fixture()
    days["2026-08-15"] = sfr._synthetic_renorm_day("2026-08-15", 0.0900, 0.0800, 600, 500, 10)
    rep = sfr.review_renormalisation(days, None)
    c = {x["criterion"]: x for x in rep["criteria"]}
    rows = 200 * 5 + 600
    expect_cur = (0.0850 * 1000 + 0.0900 * 600) / rows
    expect_ren = (0.0845 * 1000 + 0.0800 * 600) / rows
    assert abs(c["pooled_brier_not_worse"]["brier_current"] - expect_cur) < 1e-12
    assert abs(c["pooled_brier_not_worse"]["brier_renormalised"] - expect_ren) < 1e-12
    assert c["pooled_brier_not_worse"]["source"].startswith("row-weighted")
    assert c["transition_rate"]["n_compared"] == 180 * 5 + 500


def test_renorm_transition_rate_bar_and_single_race_rule():
    days, pooled = _renorm_fixture()
    pair = pooled["tier_transitions"]["pairs"][sfr.RENORM_PAIR]
    pair["n_transitions"] = 55   # 55/1080 = 5.09% > 5%
    rep = sfr.review_renormalisation(days, pooled)
    assert {x["criterion"]: x for x in rep["criteria"]}["transition_rate"]["status"] == sfr.FAIL
    pair["n_transitions"] = 54   # exactly 5.0%
    pair["races"] = [
        {"race_key": ["d", "t", "1"], "n_runners": 8, "n_compared": 8, "transitions": [{}, {}, {}]},
        {"race_key": ["d", "t", "2"], "n_runners": 4, "n_compared": 4, "transitions": [{}]},  # 25% exactly: not above
    ]
    rep = sfr.review_renormalisation(days, pooled)
    c = {x["criterion"]: x for x in rep["criteria"]}
    assert c["transition_rate"]["status"] == sfr.PASS
    assert c["single_race_signoff"]["status"] == sfr.REVIEW
    assert [r["race_key"] for r in c["single_race_signoff"]["races"]] == [["d", "t", "1"]]
    assert not c["single_race_signoff"]["proxy_used"]
    assert rep["auto_verdict"] == sfr.PASS, "sign-off is a REVIEW item, not an auto failure"


def test_renorm_single_race_proxy_for_legacy_evidence_without_field_sizes():
    days, pooled = _renorm_fixture()
    pooled["tier_transitions"]["pairs"][sfr.RENORM_PAIR]["races"] = [
        {"race_key": ["d", "t", "1"], "transitions": [{}, {}]},
        {"race_key": ["d", "t", "2"], "transitions": [{}, {}, {}]},
    ]
    c = {x["criterion"]: x for x in sfr.review_renormalisation(days, pooled)["criteria"]}["single_race_signoff"]
    assert c["proxy_used"]
    assert [r["race_key"] for r in c["races"]] == [["d", "t", "2"]]
    assert "n_runners" in c["detail"]


def test_renorm_dirty_day_detection():
    assert sfr.summarise_renorm_day("d", None)["dirty"]
    assert sfr.summarise_renorm_day("d", {"date": "d", "day": {"status": "no_data", "n_rows": 0}})["dirty"]
    ok = sfr._synthetic_renorm_day("d", 0.1, 0.1, 10, 9, 0)
    assert not sfr.summarise_renorm_day("d", ok)["dirty"]


# ----------------------------------------------------------------- the store

@pytest.fixture
def local_store(tmp_path, monkeypatch):
    import evidence_store
    monkeypatch.delenv("STRIDE_EVIDENCE_BUCKET", raising=False)
    monkeypatch.setattr(evidence_store, "local_dir", lambda: tmp_path)
    return evidence_store


def test_review_record_roundtrip_and_latest(local_store):
    rng = np.random.default_rng(6)
    for i in range(5):
        local_store.put_evidence(f"{sfr.SERVE_STEM}_2026-08-1{i}.json", json.dumps(_day(rng)))
    rep = sfr.run_review("serve")
    assert rep["auto_verdict"] == sfr.PASS and rep["n_days"] == 5
    assert sfr.latest_review("serve") is None
    from datetime import date
    sfr.emit_review("serve", rep, on=date(2026, 9, 4))
    sfr.emit_review("serve", rep, on=date(2026, 9, 5))
    assert local_store.list_evidence_dates(sfr.REVIEW_STEMS["serve"]) == ["2026-09-04", "2026-09-05"]
    got = sfr.latest_review("serve")
    assert got["generated_at"] and got["flag"] == "STRIDE_SERVE_LIVE_FEATURES"


def test_unreadable_day_in_store_is_dirty_not_silently_dropped(local_store):
    rng = np.random.default_rng(8)
    for i in range(5):
        local_store.put_evidence(f"{sfr.SERVE_STEM}_2026-08-1{i}.json", json.dumps(_day(rng)))
    local_store.put_evidence(f"{sfr.SERVE_STEM}_2026-08-15.json", "{not json")
    rep = sfr.run_review("serve")
    c = {x["criterion"]: x for x in rep["criteria"]}
    assert c["no_errored_day"]["dirty_days"] == ["2026-08-15"]
    assert c["clean_days"]["streak"] == 0
    assert rep["auto_verdict"] == sfr.FAIL


def test_cli_exit_code_reflects_verdict(local_store, capsys):
    rng = np.random.default_rng(9)
    for i in range(2):
        local_store.put_evidence(f"{sfr.SERVE_STEM}_2026-08-1{i}.json", json.dumps(_day(rng)))
    sys.argv = ["shadow_flip_review.py", "--flag", "serve"]
    assert sfr.main() == 1
    out = capsys.readouterr().out
    assert "AUTO VERDICT: WAIT" in out and "STRIDE_SERVE_LIVE_FEATURES" in out


def test_self_test_runs():
    sfr._self_test()


# --------------------------------------------------- shadow_calibrator_compare

def test_tier_transitions_carry_per_race_field_sizes():
    import shadow_calibrator_compare as scc

    rows = [
        {"race_date": "2026-08-10", "track": "T", "race_number": "1", "horse_name": h,
         "prob": p, "won": 0, "market_odds": o}
        for h, p, o in (("A", 0.40, 2.5), ("B", 0.30, 4.0), ("C", 0.20, 6.0), ("D", 0.10, 12.0))
    ]
    tiers = {"A": ("high", "low"), "B": ("medium", "medium"), "C": ("high", "medium"), "D": ("low", "low")}
    calls = {"n": 0}

    def tier_fn(feat):
        # alternate: first pass = base variant, second pass = renormalised
        name = [k for k, v in tiers.items()][calls["n"] % 4]
        idx = calls["n"] // 4
        calls["n"] += 1
        return tiers[name][idx]

    probs = [r["prob"] for r in rows]
    pairs = scc.tier_transitions(rows, {"current": probs, "current_renormalised": probs}, tier_fn)
    pair = pairs["current__current_renormalised"]
    assert pair["n_compared"] == 4 and pair["n_transitions"] == 2
    race = pair["races"][0]
    assert race["n_runners"] == 4 and race["n_compared"] == 4
    assert {t["runner"] for t in race["transitions"]} == {"A", "C"}
