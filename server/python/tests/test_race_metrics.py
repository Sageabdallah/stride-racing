"""Per-race metrics: the number the product sells, measured in the trainer.

Pins the definitions the 2026-09-05 pre-registration amendment fixes — the
tip-time favourite is the baseline, the SP favourite is hindsight, a favourite
needs a price on every runner, a usable race has >= 4 runners and one winner —
and that retrain_v2's walk-forward CV carries them per fold and pooled exactly.
"""

import math
import os
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SERVER_PYTHON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_PYTHON))

import race_metrics as rmx  # noqa: E402


def test_favourite_requires_a_price_on_every_runner():
    assert rmx.favourite_index([2.0, 3.0, 9.0]) == 0
    assert rmx.favourite_index([2.0, np.nan, 9.0]) is None
    assert rmx.favourite_index([2.0, 0.0, 9.0]) is None
    assert rmx.favourite_index([]) is None
    assert rmx.favourite_index(None) is None


def test_tip_time_favourite_and_sp_favourite_are_kept_apart():
    keys = ["R"] * 4
    y = [0, 1, 0, 0]
    p = [0.1, 0.5, 0.3, 0.1]
    tt = [2.0, 3.0, 4.0, 9.0]   # tip-time fav = runner 0 (loses)
    sp = [3.0, 2.0, 4.0, 9.0]   # SP fav = runner 1 (wins) — the closing line knew
    m = rmx.per_race_metrics(p, y, keys, tt, sp)
    assert m["model_top1_hit"] == 1.0
    assert m["fav_tip_time_hit"] == 0.0 and m["fav_sp_hit"] == 1.0
    assert m["fav_tip_time_coverage"] == 1.0 and m["fav_sp_coverage"] == 1.0


def test_unusable_races_are_counted_but_not_scored():
    p = [0.9, 0.1] + [0.4, 0.3, 0.2, 0.1] + [0.25] * 4
    y = [1, 0] + [1, 1, 0, 0] + [0, 0, 0, 0]
    k = ["two"] * 2 + ["deadheat"] * 4 + ["nowinner"] * 4
    m = rmx.per_race_metrics(p, y, k)
    assert m["n_races_total"] == 3 and m["n_races_used"] == 0
    assert m["model_top1_hit"] is None and m["race_logloss"] is None


def test_h2h_needs_full_field_stored_coverage():
    keys = ["A"] * 4 + ["B"] * 4
    y = [1, 0, 0, 0, 0, 1, 0, 0]
    p = [0.4, 0.3, 0.2, 0.1, 0.4, 0.3, 0.2, 0.1]
    st = [0.2, 0.5, 0.2, 0.1, 0.3, np.nan, 0.2, 0.1]
    m = rmx.per_race_metrics(p, y, keys, stored=st)
    assert m["h2h"]["n"] == 1
    assert m["h2h"]["model_hit"] == 1.0 and m["h2h"]["stored_hit"] == 0.0
    assert m["h2h"]["fav_tip_time_hit"] is None and m["h2h"]["fav_tip_time_n"] == 0


def test_race_logloss_is_within_race_normalised():
    p = [0.3, 0.1, 0.1, 0.1]  # sums to 0.6 -> winner's share 0.5
    m = rmx.per_race_metrics(p, [1, 0, 0, 0], ["r"] * 4)
    assert abs(m["race_logloss"] - (-math.log(0.5))) < 1e-12


def test_pool_is_exact_not_a_mean_of_rates():
    f1 = rmx.per_race_metrics([0.5, 0.2, 0.2, 0.1], [1, 0, 0, 0], ["a"] * 4)           # 1/1
    f2 = rmx.per_race_metrics([0.1, 0.5, 0.2, 0.2] * 3, [1, 0, 0, 0] * 3,
                              ["b"] * 4 + ["c"] * 4 + ["d"] * 4)                          # 0/3
    pooled = rmx.pool([f1, f2])
    assert pooled["n_races_used"] == 4 and pooled["model_top1_hit"] == 0.25   # not (1.0 + 0.0)/2


def test_length_mismatch_is_an_error_not_a_silent_truncation():
    with pytest.raises(ValueError):
        rmx.per_race_metrics([0.5, 0.5], [1, 0, 0], ["a", "a", "a"])
    with pytest.raises(ValueError):
        rmx.per_race_metrics([0.5, 0.5, 0.5, 0.5], [1, 0, 0, 0], ["a"] * 4, tip_time_odds=[2.0, 3.0])


def test_build_race_meta_key_and_nan_semantics():
    df = pd.DataFrame({
        "race_date": ["2026-03-14", "2026-03-14", "2026-03-15"],
        "track": ["Flemington", " flemington", "Randwick"],
        "race_number": [5, 5, "7"],
        "sp_odds": [3.0, 4.0, None],
        "predicted_win_prob": [0.3, 0.2, 0.5],
    })
    meta = rmx.build_race_meta(df)
    assert list(meta["race_key"]) == ["2026-03-14|flemington|5", "2026-03-14|flemington|5",
                                      "2026-03-15|randwick|7"]
    assert meta["tip_time_odds"].isna().all(), "no tip_time_odds column -> NaN, never SP"
    assert math.isnan(meta["sp_odds"][2])
    assert meta["predicted_win_prob"].tolist() == [0.3, 0.2, 0.5]


def test_self_test_runs():
    rmx._self_test()


# --------------------------------------------------------------- retrain_v2

@pytest.fixture(scope="module")
def retrain():
    fake = types.ModuleType("psycopg2")
    extras = types.ModuleType("psycopg2.extras")
    fake.extras = extras
    sys.modules.setdefault("psycopg2", fake)
    sys.modules.setdefault("psycopg2.extras", extras)
    os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost/unused")
    import retrain_v2
    return retrain_v2


def _synthetic(n_days=130, races_per_day=2, seed=11):
    rng = np.random.default_rng(seed)
    rows = []
    base = pd.Timestamp("2026-01-01")
    for d in range(n_days):
        for r in range(races_per_day):
            n = int(rng.integers(6, 10))
            strength = rng.normal(0, 1, n)
            prob = np.exp(1.2 * strength) / np.exp(1.2 * strength).sum()
            w = int(rng.choice(n, p=prob))
            odds = 1.0 / np.clip(prob + rng.normal(0, 0.02, n), 0.02, None) * 1.12
            for i in range(n):
                rows.append({
                    "race_date": (base + pd.Timedelta(days=d)).strftime("%Y-%m-%d"),
                    "track": "Synth", "race_number": r + 1,
                    "f1": strength[i] + rng.normal(0, 0.5), "f2": strength[i] + rng.normal(0, 1.0),
                    "f3": rng.normal(), "f4": rng.normal(), "f5": float(odds[i]),
                    "is_winner": int(i == w),
                    "tip_time_odds": float(odds[i]), "sp_odds": float(odds[i] * 0.97),
                    "predicted_win_prob": float(prob[i]),
                })
    return pd.DataFrame(rows)


def test_walk_forward_cv_reports_per_race_metrics(retrain):
    df = _synthetic()
    feats = ["f1", "f2", "f3", "f4", "f5"]
    X = df[feats].reset_index(drop=True)
    y = df["is_winner"].astype(int).reset_index(drop=True)
    dates = pd.to_datetime(df["race_date"]).reset_index(drop=True)
    meta = retrain.build_race_meta(df)
    res = retrain.run_walk_forward_cv(X, y, dates, feats, label="t", race_meta=meta)
    assert res["n_folds"] >= 1
    for f in res["folds"]:
        assert "race" in f and f["race"]["n_races_used"] > 0
        assert f["race"]["fav_tip_time_coverage"] == 1.0
    pooled = res["race_metrics"]
    assert pooled["n_races_used"] == sum(f["race"]["n_races_used"] for f in res["folds"])
    assert pooled["h2h"]["n"] == pooled["n_races_used"], "stored prob covers every synthetic field"
    # The synthetic model sees a noisy version of the same strength the odds
    # encode; it must land well above random and the favourite must be strong.
    avg_field = len(df) / (130 * 2)
    assert pooled["model_top1_hit"] > 1.5 / avg_field
    assert pooled["fav_tip_time_hit"] > 2 / avg_field
    assert pooled["race_logloss"] is not None


def test_walk_forward_cv_without_meta_is_unchanged(retrain):
    df = _synthetic(n_days=100)
    feats = ["f1", "f2", "f3", "f4", "f5"]
    X = df[feats].reset_index(drop=True)
    y = df["is_winner"].astype(int).reset_index(drop=True)
    dates = pd.to_datetime(df["race_date"]).reset_index(drop=True)
    res = retrain.run_walk_forward_cv(X, y, dates, feats, label="t")
    assert res["race_metrics"] is None
    assert all("race" not in f for f in res["folds"])
