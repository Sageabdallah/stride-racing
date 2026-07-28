#!/usr/bin/env python3
"""Tests for betfair_snapshot_coverage_audit.py — pure-function coverage math
on fixture rows, and the schema-missing failure message. No DB."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import betfair_snapshot_coverage_audit as audit


# --- verdict thresholds ----------------------------------------------------

def test_verdict_feasible_at_70():
    assert audit.verdict_for_coverage(70.0) == "RETRO-ABLATION: FEASIBLE (70.0% coverage)"
    assert audit.verdict_for_coverage(100.0).startswith("RETRO-ABLATION: FEASIBLE")


def test_verdict_partial_band():
    assert audit.verdict_for_coverage(69.9).startswith("RETRO-ABLATION: PARTIAL")
    assert audit.verdict_for_coverage(40.0).startswith("RETRO-ABLATION: PARTIAL")


def test_verdict_not_feasible_below_40():
    assert audit.verdict_for_coverage(39.9).startswith("RETRO-ABLATION: NOT_FEASIBLE")
    assert audit.verdict_for_coverage(0.0).startswith("RETRO-ABLATION: NOT_FEASIBLE")


# --- schema introspection failure ------------------------------------------

def test_missing_required_column_named_in_failure():
    available = {"race_date", "track", "race_number", "snapshot_type"}  # no back_price
    missing = audit.missing_required_columns(available)
    assert missing == ["back_price"]
    msg = audit.schema_failure_message("betfair_odds_snapshots", missing)
    assert "back_price" in msg
    assert "betfair_odds_snapshots" in msg


def test_no_missing_columns_when_schema_complete():
    available = set(audit.REQUIRED_COLUMNS) | {"horse_name_norm", "snapshot_time"}
    assert audit.missing_required_columns(available) == []


# --- race usability ---------------------------------------------------------

def test_race_usable_needs_half_of_runners_priced():
    # 10 selections runners, 50% floor -> 5 priced runners required
    assert audit.race_is_usable(5, 10) is True
    assert audit.race_is_usable(4, 10) is False


def test_race_usable_falls_back_to_any_priced_row_without_denominator():
    assert audit.race_is_usable(1, 0) is True
    assert audit.race_is_usable(0, 0) is False


# --- window coverage math on fixture rows -----------------------------------

def _fixtures():
    r1 = ("2026-03-07", "flemington", 1)
    r2 = ("2026-03-07", "flemington", 2)
    r3 = ("2026-03-14", "randwick", 5)
    r4 = ("2026-04-18", "caulfield", 7)
    scheduled = {r1, r2, r3, r4}
    priced = {r1: 8, r2: 3, r3: 0}          # r4: no morning rows at all
    selections = {r1: 10, r2: 10, r3: 12}   # r4: no selections denominator
    return scheduled, priced, selections


def test_window_coverage_counts_and_verdict():
    scheduled, priced, selections = _fixtures()
    out = audit.compute_window_coverage(scheduled, priced, selections)
    # r1: 8/10 priced -> usable. r2: 3/10 -> not. r3: 0 -> not. r4: none -> not.
    assert out["scheduled_races"] == 4
    assert out["usable_races"] == 1
    assert out["coverage_pct"] == 25.0
    assert out["verdict"] == "RETRO-ABLATION: NOT_FEASIBLE (25.0% coverage)"
    # every usable race is listed — no silent truncation
    assert out["usable_race_keys"] == [["2026-03-07", "flemington", "1"]]


def test_window_coverage_feasible_fixture():
    r1 = ("2026-03-07", "flemington", 1)
    r2 = ("2026-03-07", "flemington", 2)
    out = audit.compute_window_coverage(
        {r1, r2}, {r1: 9, r2: 6}, {r1: 10, r2: 10})
    assert out["usable_races"] == 2
    assert out["coverage_pct"] == 100.0
    assert out["verdict"].startswith("RETRO-ABLATION: FEASIBLE")


def test_window_coverage_empty_window_is_zero_not_error():
    out = audit.compute_window_coverage(set(), {}, {})
    assert out["scheduled_races"] == 0
    assert out["coverage_pct"] == 0.0
    assert out["verdict"].startswith("RETRO-ABLATION: NOT_FEASIBLE")


def test_runner_coverage_pct_none_without_denominator():
    assert audit.runner_coverage_pct(7, 12) == 58.3
    assert audit.runner_coverage_pct(7, 0) is None
