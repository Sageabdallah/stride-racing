"""Synthetic-data tests for winner_pattern_features (no DB required).

Run: .venv/bin/python server/python/tests/test_winner_pattern_features.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # server/python on path

from winner_pattern_features import (  # noqa: E402
    FEATURE_NAMES,
    WinnerPatternPriors,
    _prepare_runner_frame,
    compute_features,
    fit_priors,
)


def _rrh_row(rid, horse_id, name, rnum, pos, odds, jockey, going="Good", track="Randwick",
             dist=1500, rclass="BM70", clvl=70, field=10, barrier=5, date="2025-01-01"):
    return dict(id=rid, horse_id=horse_id, horse_name=name, track=track, race_date=date,
                race_number=rnum, distance_m=dist, race_class=rclass, class_level=clvl,
                going=going, position=pos, barrier=barrier, sp_odds=odds, field_size=field,
                jockey=jockey)


def _sect_row(rid, last200, pos400):
    return dict(race_results_history_id=rid, last_200m_time=last200,
                splits_json={"400m": {"position": pos400}})


def test_sp_derived_flagship_feature_stays_neutral(capsys):
    # This history would have fired the old SP-banded feature on start three.
    rrh = pd.DataFrame([
        _rrh_row(1, 1, "Alpha", 1, 8, 5.0, "J1", date="2025-01-01"),
        _rrh_row(2, 1, "Alpha", 1, 4, 9.0, "J1", date="2025-01-08"),   # PB close (10.5<11.0), finished 4th
        _rrh_row(3, 1, "Alpha", 1, 1, 8.0, "J1", date="2025-01-15"),
        _rrh_row(4, 1, "Alpha", 1, 2, 20.0, "J1", date="2025-01-22"),
    ])
    st = pd.DataFrame([
        _sect_row(1, 11.0, 6),
        _sect_row(2, 10.5, 3),
        _sect_row(3, 10.8, 2),
        _sect_row(4, 10.6, 1),
    ])
    priors = WinnerPatternPriors()  # empty priors: isolates the flag logic
    frame = _prepare_runner_frame(rrh, st)
    feats = compute_features(frame, priors)
    # align back to id order
    feats = feats.set_index(frame["id"].values)
    f1 = feats["prior_pb_close_underreaction"]
    assert f1.isna().all()
    warning = capsys.readouterr().err
    assert "prior_pb_close_underreaction is neutral" in warning
    assert "own-race final sp_odds" in warning
    assert "global rule 13" in warning

    # Leakage-safety: a horse's FIRST start has no prior sectionals -> NaN priors.
    fc = feats["cohort_fast_close_prior"]
    assert np.isnan(fc.loc[1]), "first start must have NaN fast-close prior (no prior data)"
    print("PASS test_sp_derived_flagship_feature_stays_neutral")


def test_fast_close_and_pos400_lookup():
    rrh = pd.DataFrame([
        _rrh_row(1, 7, "Bravo", 1, 3, 4.0, "J2", date="2025-02-01"),
        _rrh_row(2, 7, "Bravo", 2, 5, 4.0, "J2", date="2025-02-08"),
    ])
    st = pd.DataFrame([
        _sect_row(1, 11.20, 2),  # prior start: last200=11.20, 400m position 2
        _sect_row(2, 11.00, 1),
    ])
    key = "|".join(["Randwick", "Middle 1400-1800", "Good", "Handicap/BM"])
    priors = WinnerPatternPriors(
        fast_close_thresholds={key: 11.50},
        pos400_uplift={"|".join(["Randwick", "Middle 1400-1800", "Good", "2-3"]): 7.9},
    )
    frame = _prepare_runner_frame(rrh, st)
    feats = compute_features(frame, priors).set_index(frame["id"].values)
    # Start 2 uses start 1 as prior: best prior last200 = 11.20 -> margin = 11.50 - 11.20 = 0.30
    assert abs(feats["cohort_fast_close_prior"].loc[2] - 0.30) < 1e-6, feats["cohort_fast_close_prior"].loc[2]
    # Prior avg 400m position = 2 -> bucket "2-3" -> uplift 7.9
    assert abs(feats["pos400_win_prior"].loc[2] - 7.9) < 1e-6, feats["pos400_win_prior"].loc[2]
    print("PASS test_fast_close_and_pos400_lookup")


def test_jockey_wet_residual_gating():
    rrh = pd.DataFrame([
        _rrh_row(1, 9, "Cara", 1, 4, 6.0, "WetKing", going="Heavy", date="2025-03-01"),
        _rrh_row(2, 9, "Cara", 2, 4, 6.0, "WetKing", going="Good", date="2025-03-08"),
    ])
    st = pd.DataFrame([_sect_row(1, 11.0, 5), _sect_row(2, 11.0, 5)])
    priors = WinnerPatternPriors(jockey_wet_delta={"WetKing": 7.45})
    frame = _prepare_runner_frame(rrh, st)
    feats = compute_features(frame, priors).set_index(frame["id"].values)
    assert abs(feats["jockey_wet_residual"].loc[1] - 7.45) < 1e-6, "wet ride should carry the residual"
    assert feats["jockey_wet_residual"].loc[2] == 0.0, "dry ride should be neutral"
    print("PASS test_jockey_wet_residual_gating")


def test_fit_priors_emits_tables():
    rng = np.random.default_rng(42)
    rows, sects = [], []
    rid = 1
    # One dense cohort (>=100 rows) so the fast-close threshold gate passes.
    for i in range(140):
        pos = int(rng.integers(1, 11))
        last200 = float(11.0 + rng.normal(0, 0.4))
        # one dense Good cohort so the >=100-row fast-close gate is exercised
        jockey = f"J{i % 7}"
        rows.append(_rrh_row(rid, 1000 + i, f"R{i}", (i % 8) + 1, pos, 5.0, jockey,
                             going="Good", date=f"2025-04-{(i % 27) + 1:02d}"))
        sects.append(_sect_row(rid, last200, int(rng.integers(1, 6))))
        rid += 1
    priors = fit_priors(pd.DataFrame(rows), pd.DataFrame(sects))
    assert priors.fast_close_thresholds, "expected at least one fast-close cohort threshold"
    assert priors.meta["runner_rows"] == 140
    thr = next(iter(priors.fast_close_thresholds.values()))
    assert 9.5 < thr < 12.5, f"threshold out of sane range: {thr}"
    assert set(priors.to_json().keys()) == {
        "fast_close_thresholds", "pos400_uplift", "jockey_wet_delta", "meta"
    }
    print("PASS test_fit_priors_emits_tables")


def test_nan_field_size_no_crash():
    # Regression: NaN field_size must not crash the 400m bucketer (int(NaN) would raise).
    rrh = pd.DataFrame([
        _rrh_row(1, 20, "Delta", 1, 3, 5.0, "J3", field=8, date="2025-05-01"),
        _rrh_row(2, 20, "Delta", 2, 4, 5.0, "J3", field=np.nan, date="2025-05-08"),  # today: field unknown
    ])
    st = pd.DataFrame([_sect_row(1, 11.0, 2), _sect_row(2, 11.0, 2)])
    priors = WinnerPatternPriors(
        pos400_uplift={"|".join(["Randwick", "Middle 1400-1800", "Good", "2-3"]): 5.0},
    )
    frame = _prepare_runner_frame(rrh, st)
    feats = compute_features(frame, priors).set_index(frame["id"].values)
    # prior avg 400m position = 2 -> bucket "2-3" even though today's field_size is NaN
    assert abs(feats["pos400_win_prior"].loc[2] - 5.0) < 1e-6, feats["pos400_win_prior"].loc[2]
    print("PASS test_nan_field_size_no_crash")


def test_non_unique_index_alignment():
    # Regression: a non-unique caller index must not break realignment.
    rrh = pd.DataFrame([
        _rrh_row(1, 1, "Alpha", 1, 8, 5.0, "J1", date="2025-01-01"),
        _rrh_row(2, 1, "Alpha", 1, 4, 9.0, "J1", date="2025-01-08"),
        _rrh_row(3, 1, "Alpha", 1, 1, 8.0, "J1", date="2025-01-15"),
        _rrh_row(4, 1, "Alpha", 1, 2, 20.0, "J1", date="2025-01-22"),
    ])
    st = pd.DataFrame([_sect_row(1, 11.0, 6), _sect_row(2, 10.5, 3),
                       _sect_row(3, 10.8, 2), _sect_row(4, 10.6, 1)])
    priors = WinnerPatternPriors()
    frame = _prepare_runner_frame(rrh, st)
    ref = compute_features(frame, priors)  # unique-index baseline
    dup = frame.copy()
    dup.index = [0, 0, 0, 0]               # pathological non-unique index
    got = compute_features(dup, priors)
    assert list(got.index) == [0, 0, 0, 0] and len(got) == 4
    # values must match the baseline row-for-row (order preserved)
    np.testing.assert_equal(
        got["prior_pb_close_underreaction"].values,
        ref["prior_pb_close_underreaction"].values,
    )
    print("PASS test_non_unique_index_alignment")


def test_round_trip_serialisation(tmp_path=Path("/tmp")):
    priors = WinnerPatternPriors(fast_close_thresholds={"a": 1.2}, jockey_wet_delta={"J": 3.0},
                                 meta={"runner_rows": 1})
    out = tmp_path / "wp_priors_roundtrip.json"
    priors.save(out)
    back = WinnerPatternPriors.load(out)
    assert back.fast_close_thresholds == {"a": 1.2}
    assert back.jockey_wet_delta == {"J": 3.0}
    print("PASS test_round_trip_serialisation")


if __name__ == "__main__":
    test_fast_close_and_pos400_lookup()
    test_jockey_wet_residual_gating()
    test_nan_field_size_no_crash()
    test_non_unique_index_alignment()
    test_fit_priors_emits_tables()
    test_round_trip_serialisation()
    assert FEATURE_NAMES == [
        "prior_pb_close_underreaction", "cohort_fast_close_prior",
        "pos400_win_prior", "jockey_wet_residual",
    ]
    print("\nALL TESTS PASSED")
