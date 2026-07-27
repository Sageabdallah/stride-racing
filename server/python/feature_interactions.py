#!/usr/bin/env python3
"""Canonical interaction-feature formulas — one definition, both paths.

Five interaction features were written twice: once vectorised for training in
`retrain_v2._add_phase4_interactions` and once inline per-runner at inference in
`run_tips_pipeline` (~:2306-2320). Duplicated formulas drift, and one already
had:

    barrier_x_pace_inv
        training   barrier_advantage * pace_pressure_score
        inference  barrier_advantage * (1 - pace_pressure_score)

The two agree at `pace_pressure_score == 0.5` and nowhere else, and 0.5 is the
default the inference path substitutes when the key is absent
(`feat[k] = runner.get(k, 0.5)`). So the mismatch is invisible whenever pace
data is missing and maximal when pace pressure is extreme — the model is served
the inverse of the relationship it was fitted on, precisely in the races where
pace matters most.

**These functions reproduce the TRAINING formulas**, because the training
formula is what the artifact learned. Serving anything else feeds the model a
feature whose meaning it was never shown. That the name says "inv" while the
training formula is not inverted is a separate question about intent, recorded
below but deliberately not "fixed" here: changing training changes the model,
which is a retrain decision, not a parity decision.

Wiring this into inference changes which horses get tipped, so it sits behind
`STRIDE_INTERACTION_PARITY`, default off. With the flag unset the pipeline
keeps its current behaviour exactly.

Run `python feature_interactions.py` for the self-test.
"""

import math
import os
from typing import Any, Dict, Optional

# The five features defined in both places. `step_up_x_dist_slope` is
# deliberately absent: it is computed at inference (`run_tips_pipeline` ~:2319)
# but excluded from training ("excluded (RED coverage)",
# retrain_v2._add_phase4_interactions) and is NOT in FEATURE_COLUMNS, so the
# value is discarded by `prepare_features`. Computing it is harmless but dead.
INTERACTION_FEATURES = (
    "fitness_x_distance",
    "barrier_x_pace_inv",
    "sectional_x_going",
    "class_drop_x_trajectory",
    "campaign_run_x_fitness",
)

# Variance floor below which training falls back to a constant 0.5 for
# pace_pressure_score (retrain_v2: `if _pps.std() >= 0.05`). Inference has no
# equivalent and cannot have one — it sees a single runner, not a distribution.
PACE_PRESSURE_STD_FLOOR = 0.05


def _stride_flag(name: str) -> bool:
    """Default-off env flag, Variant B idiom (see run_tips_pipeline.py:591)."""
    return os.environ.get(name, "false").strip().lower() in ("true", "1", "yes")


def _num(value: Any, default: float = 0.0) -> float:
    """Coerce to float, mapping None/NaN/unparseable to `default`.

    The inference path used `dict.get(key, default)`, which returns None when a
    key is present but null — so a missing value and a null value behaved
    differently. Training used `.fillna(...)`, which treats them the same. This
    matches training.
    """
    if value is None:
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f


def fitness_proxy(campaign_run_number: Any) -> float:
    """Campaign-fitness proxy peaking at run 3, floored at 0."""
    crn = _num(campaign_run_number, 1.0)
    return max(0.0, 1.0 - abs(crn - 3.0) * 0.15)


def campaign_freshness(campaign_run_number: Any) -> float:
    """Freshness decaying only after run 5, floored at 0."""
    crn = _num(campaign_run_number, 1.0)
    return max(0.0, 1.0 - max(0.0, crn - 5.0) * 0.2)


def fitness_x_distance(campaign_run_number: Any, distance_strike_rate: Any) -> float:
    return fitness_proxy(campaign_run_number) * _num(distance_strike_rate, 0.0)


def barrier_x_pace_inv(barrier_advantage: Any, pace_pressure_score: Any,
                       low_variance: bool = False) -> float:
    """Barrier advantage scaled by pace pressure — NOT by (1 - pace pressure).

    `low_variance=True` reproduces training's fallback for a dataset whose
    pace_pressure_score barely varies. Inference should leave it False: it
    scores one runner and has no distribution to measure.
    """
    ba = _num(barrier_advantage, 0.0)
    if low_variance:
        return ba * 0.5
    return ba * _num(pace_pressure_score, 0.5)


def sectional_x_going(z_200m: Any, going_suitability: Any) -> float:
    return _num(z_200m, 0.0) * _num(going_suitability, 0.5)


def class_drop_x_trajectory(is_class_drop: Any, form_direction_slope: Any) -> float:
    return _num(is_class_drop, 0.0) * _num(form_direction_slope, 0.0)


def campaign_run_x_fitness(campaign_run_number: Any) -> float:
    crn = _num(campaign_run_number, 1.0)
    return crn * campaign_freshness(crn)


def compute_interactions(values: Dict[str, Any],
                         low_pace_variance: bool = False) -> Dict[str, float]:
    """All five, from one feature dict. Keys absent from `values` use defaults."""
    crn = values.get("campaign_run_number")
    return {
        "fitness_x_distance": fitness_x_distance(crn, values.get("distance_strike_rate")),
        "barrier_x_pace_inv": barrier_x_pace_inv(
            values.get("barrier_advantage"), values.get("pace_pressure_score"),
            low_variance=low_pace_variance),
        "sectional_x_going": sectional_x_going(
            values.get("z_200m"), values.get("going_suitability")),
        "class_drop_x_trajectory": class_drop_x_trajectory(
            values.get("is_class_drop"), values.get("form_direction_slope")),
        "campaign_run_x_fitness": campaign_run_x_fitness(crn),
    }


def legacy_inference_barrier_x_pace_inv(barrier_advantage: Any,
                                        pace_pressure_score: Any) -> float:
    """The pre-parity inference formula, kept so the divergence stays testable.

    Not for production use — it is what the pipeline computes with
    STRIDE_INTERACTION_PARITY off, retained purely so the self-test can prove
    the two disagree and quantify by how much.
    """
    return _num(barrier_advantage, 0.0) * (1.0 - _num(pace_pressure_score, 0.5))


def _self_test():
    print("feature_interactions self-test")

    # --- scalar formulas must match training's vectorised versions ---------
    try:
        import pandas as pd
        import numpy as np
        have_pandas = True
    except ImportError:
        have_pandas = False

    if have_pandas:
        rng = np.random.default_rng(11)
        n = 500
        df = pd.DataFrame({
            "campaign_run_number": rng.integers(1, 12, n).astype(float),
            "distance_strike_rate": rng.random(n),
            "barrier_advantage": rng.normal(0, 1, n),
            "pace_pressure_score": rng.random(n),
            "z_200m": rng.normal(0, 1, n),
            "going_suitability": rng.random(n),
            "is_class_drop": rng.integers(0, 2, n).astype(float),
            "form_direction_slope": rng.normal(0, 1, n),
        })

        # Training's exact expressions, transcribed from
        # retrain_v2._add_phase4_interactions.
        crn = df["campaign_run_number"].fillna(1)
        train = pd.DataFrame({
            "fitness_x_distance":
                (1 - (crn - 3).abs() * 0.15).clip(lower=0) * df["distance_strike_rate"].fillna(0),
            "barrier_x_pace_inv":
                df["barrier_advantage"].fillna(0) * df["pace_pressure_score"],
            "sectional_x_going":
                df["z_200m"].fillna(0) * df["going_suitability"].fillna(0.5),
            "class_drop_x_trajectory":
                df["is_class_drop"].fillna(0) * df["form_direction_slope"].fillna(0),
            "campaign_run_x_fitness":
                crn * (1 - (crn - 5).clip(lower=0) * 0.2).clip(lower=0),
        })

        scalar = pd.DataFrame([compute_interactions(r) for r in df.to_dict("records")])
        for col in INTERACTION_FEATURES:
            delta = float((train[col] - scalar[col]).abs().max())
            assert delta < 1e-9, f"{col}: scalar and training differ by {delta}"
        print(f"  parity: all 5 features match training's vectorised form "
              f"over {n} rows (max |Δ| < 1e-9)")

        # pace_pressure_score has real variance here, so the divergence bites
        legacy = df["barrier_advantage"] * (1 - df["pace_pressure_score"])
        worst = float((train["barrier_x_pace_inv"] - legacy).abs().max())
        assert worst > 0.5, worst
        print(f"  divergence: legacy inference formula differs from training by "
              f"up to {worst:.3f} on the same rows")
    else:
        print("  (pandas absent — vectorised parity check skipped)")

    # --- the divergence, and the 0.5 blind spot that hid it ---------------
    for pps, agree in ((0.0, False), (0.25, False), (0.5, True), (0.75, False), (1.0, False)):
        canon = barrier_x_pace_inv(1.0, pps)
        legacy = legacy_inference_barrier_x_pace_inv(1.0, pps)
        same = abs(canon - legacy) < 1e-12
        assert same is agree, f"pps={pps}: expected agree={agree}, got {same}"
    print("  blind spot confirmed: the two formulas agree ONLY at "
          "pace_pressure_score=0.5, which is the inference default")

    # --- None and NaN are treated as training treats them ------------------
    assert barrier_x_pace_inv(None, None) == 0.0
    assert sectional_x_going(None, None) == 0.0
    assert sectional_x_going(2.0, None) == 1.0, "going_suitability must default to 0.5"
    assert fitness_x_distance(None, None) == 0.0
    assert compute_interactions({})["campaign_run_x_fitness"] == 1.0
    nan = float("nan")
    assert sectional_x_going(nan, 0.5) == 0.0, "NaN must behave as fillna(0)"
    assert barrier_x_pace_inv(1.0, nan) == 0.5, "NaN pace must fall back to 0.5"
    assert _num("junk", 3.0) == 3.0
    print("  null handling: None, NaN and unparseable all fall back like fillna()")

    # --- shape of the two proxies -----------------------------------------
    assert fitness_proxy(3) == 1.0 and fitness_proxy(1) < 1.0 and fitness_proxy(99) == 0.0
    assert campaign_freshness(1) == 1.0 and campaign_freshness(5) == 1.0
    assert campaign_freshness(6) < 1.0 and campaign_freshness(99) == 0.0
    assert campaign_run_x_fitness(3) == 3.0
    print("  proxies: fitness peaks at run 3, freshness flat to run 5 then decays, both floored")

    # --- training's low-variance fallback ----------------------------------
    assert barrier_x_pace_inv(2.0, 0.9, low_variance=True) == 1.0
    assert barrier_x_pace_inv(2.0, 0.9, low_variance=False) == 1.8
    print("  low-variance fallback reproduces training's constant-0.5 branch")

    # --- flag defaults off -------------------------------------------------
    prev = os.environ.pop("STRIDE_INTERACTION_PARITY", None)
    try:
        assert _stride_flag("STRIDE_INTERACTION_PARITY") is False
        os.environ["STRIDE_INTERACTION_PARITY"] = "true"
        assert _stride_flag("STRIDE_INTERACTION_PARITY") is True
        os.environ["STRIDE_INTERACTION_PARITY"] = "maybe"
        assert _stride_flag("STRIDE_INTERACTION_PARITY") is False
    finally:
        os.environ.pop("STRIDE_INTERACTION_PARITY", None)
        if prev is not None:
            os.environ["STRIDE_INTERACTION_PARITY"] = prev
    print("  flag: defaults off, parses true/1/yes, unparseable falls back to off")

    print("All tests completed successfully.")


if __name__ == "__main__":
    _self_test()
