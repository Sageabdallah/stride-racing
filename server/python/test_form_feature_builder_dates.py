#!/usr/bin/env python3
"""
Regression tests for the days_since_run wall-clock bug (train-side feature
corruption): days-since-last-run must be anchored to the subject race date,
not to whenever the retrain happens to run.
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from form_feature_builder import compute_single_horse_features


def _prior_runs(last_run_date: str) -> pd.DataFrame:
    """Single-run history with the minimum columns the builder touches."""
    return pd.DataFrame([{
        "race_date": last_run_date,
        "track": "Flemington",
        "position": 3,
        "distance_m": 1400,
        "class_level": 50,
        "margin_lengths": 1.5,
        "jockey": "J McDonald",
        "field_size": 10,
        "weight_kg": 58.0,
        "going": "Good 4",
    }])


def _days_since(prior: pd.DataFrame, race_date_str) -> float:
    feats = compute_single_horse_features(
        prior, "Flemington", 1400, race_date_str=race_date_str,
    )
    return feats["days_since_run"]


def test_days_since_run_anchored_to_race_date():
    # Race date deliberately far from today so the old wall-clock code
    # provably fails (it would return years, not 10).
    prior = _prior_runs("2020-06-05")
    assert _days_since(prior, "2020-06-15") == 10


def test_race_date_none_falls_back_to_now():
    # Serve path: no race date supplied, so wall clock is the anchor.
    last_run = (pd.Timestamp.now() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    assert _days_since(_prior_runs(last_run), None) == 7


def test_malformed_race_date_does_not_raise():
    last_run = (pd.Timestamp.now() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    # Must not raise, and must fall back to the wall-clock anchor.
    assert _days_since(_prior_runs(last_run), "not-a-date") == 7


def test_regression_old_wallclock_behavior_gone():
    # Race exactly one year ago, last run 5 days before it. The old code
    # returned ~365; correct answer is 5.
    race_date = (pd.Timestamp.now() - pd.Timedelta(days=365)).normalize()
    last_run = (race_date - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    race_date_str = race_date.strftime("%Y-%m-%d")
    assert _days_since(_prior_runs(last_run), race_date_str) == 5
