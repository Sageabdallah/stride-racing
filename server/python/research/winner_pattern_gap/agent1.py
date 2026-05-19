from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from .config import TIGHT_TURNING_TRACKS
from .coverage import CoverageArtifacts
from .db import csv_safe_write, json_write, markdown_write
from .utils import (
    classify_position_bucket,
    compute_window_stats,
    derive_base_dataframe_fields,
    derive_marker_position,
    derive_opening_600m,
    make_join_key,
    markdown_from_findings,
    normalize_name_column,
    pick_top_candidates,
    prep_race_date_column,
    safe_rate,
)


def _priority_from_edge(edge_pct: float, sample_size: int) -> int:
    if edge_pct >= 12 and sample_size >= 150:
        return 1
    if edge_pct >= 8 and sample_size >= 100:
        return 2
    if edge_pct >= 5 and sample_size >= 75:
        return 3
    if edge_pct >= 3 and sample_size >= 50:
        return 4
    return 5


def _merge_results_and_sectionals(coverage: CoverageArtifacts) -> pd.DataFrame:
    rrh = coverage.rrh_df.copy()
    st = coverage.sectional_df.copy()

    rrh["join_key"] = make_join_key(rrh["track"], rrh["race_date"], rrh["race_number"], rrh["horse_name"])
    st["join_key"] = make_join_key(st["track"], st["race_date"], st["race_number"], st["horse_name"])

    primary = rrh.merge(
        st,
        how="left",
        left_on="id",
        right_on="race_results_history_id",
        suffixes=("", "_st"),
    )
    unmatched_mask = primary["race_results_history_id"].isna()
    fallback_lookup = (
        st.sort_values(["race_date", "track", "race_number"])
        .drop_duplicates(subset=["join_key"], keep="first")
        .set_index("join_key")
    )
    if unmatched_mask.any():
        unmatched = primary.loc[unmatched_mask].copy()
        fallback_rows = fallback_lookup.reindex(unmatched["join_key"])
        for col in st.columns:
            if col in {"join_key"}:
                continue
            primary.loc[unmatched_mask, col] = primary.loc[unmatched_mask, col].where(
                primary.loc[unmatched_mask, col].notna(),
                fallback_rows[col].values if col in fallback_rows.columns else None,
            )

    primary["win"] = primary["position"].fillna(0).astype(int) == 1
    primary["market_odds"] = primary["sp_odds"]
    primary = derive_base_dataframe_fields(primary)
    primary["horse_key"] = primary["horse_id"].fillna("").astype(str)
    primary.loc[primary["horse_key"].eq(""), "horse_key"] = normalize_name_column(primary.loc[primary["horse_key"].eq(""), "horse_name"])

    primary["pos_400m"] = primary["splits_json"].apply(lambda s: derive_marker_position(s, "400m"))
    primary["pos_600m"] = primary["splits_json"].apply(lambda s: derive_marker_position(s, "600m"))
    primary["pos_800m"] = primary["splits_json"].apply(lambda s: derive_marker_position(s, "800m"))
    primary["opening_600m"] = primary["splits_json"].apply(derive_opening_600m)
    primary["pos_400m_bucket"] = primary.apply(
        lambda row: classify_position_bucket(row.get("pos_400m"), row.get("field_size")), axis=1
    )
    primary["pos_600m_bucket"] = primary.apply(
        lambda row: classify_position_bucket(row.get("pos_600m"), row.get("field_size")), axis=1
    )
    primary["pace_role_600m"] = np.select(
        [
            primary["pos_600m"].fillna(99) <= 2,
            primary["pos_600m"].fillna(99) >= 4,
        ],
        ["contested", "off_pace"],
        default="midfield",
    )
    primary["wet_group"] = np.where(primary["going_group"].isin(["Soft", "Soft 6-7", "Heavy", "Heavy 8-10"]), "Wet", "Dry")
    primary["barrier_segment"] = pd.cut(
        primary["barrier"].fillna(-1),
        bins=[-1, 4, 8, 99],
        labels=["1-4", "5-8", "9+"],
    ).astype("string")
    primary["field_size_bucket"] = primary["field_size_bucket"].fillna("Unknown")
    return primary


def _race_level_pace_labels(df: pd.DataFrame) -> pd.DataFrame:
    pace = (
        df.dropna(subset=["opening_600m"])
        .groupby(["canonical_track", "race_date", "race_number", "distance_bucket", "going_group"], dropna=False)["opening_600m"]
        .mean()
        .reset_index(name="race_opening_600m")
    )
    if pace.empty:
        return pace
    pace["pace_fast_q25"] = pace.groupby(["canonical_track", "distance_bucket", "going_group"], dropna=False)["race_opening_600m"].transform(
        lambda s: s.quantile(0.25) if len(s) >= 8 else np.nan
    )
    pace["pace_slow_q75"] = pace.groupby(["canonical_track", "distance_bucket", "going_group"], dropna=False)["race_opening_600m"].transform(
        lambda s: s.quantile(0.75) if len(s) >= 8 else np.nan
    )
    pace["pace_class"] = np.select(
        [
            pace["race_opening_600m"] <= pace["pace_fast_q25"],
            pace["race_opening_600m"] >= pace["pace_slow_q75"],
        ],
        ["hot", "slow"],
        default="genuine",
    )
    return pace


def _finding_card(
    *,
    finding_id: str,
    category: str,
    pattern_description: str,
    query_used: str,
    tracks_affected: str,
    conditions: str,
    winner_win_rate: float,
    field_average_win_rate: float,
    edge_magnitude: float,
    sample_size: int,
    sectionals_involved: str,
    data_quality_note: str,
    model_gap_explanation: str,
    feature_engineering_fix: str,
    backtestable_rule: str,
    cohort_mask: pd.Series,
    base_df: pd.DataFrame,
) -> Dict[str, Any]:
    windows = compute_window_stats(base_df, cohort_mask, win_column="win")
    return {
        "finding_id": finding_id,
        "category": category,
        "pattern_description": pattern_description,
        "query_used": query_used,
        "tracks_affected": tracks_affected,
        "conditions": conditions,
        "winner_win_rate": round(winner_win_rate, 2),
        "field_average_win_rate": round(field_average_win_rate, 2),
        "edge_magnitude": round(edge_magnitude, 2),
        "sample_size": int(sample_size),
        "sectionals_involved": sectionals_involved,
        "data_quality_note": data_quality_note,
        "model_gap_explanation": model_gap_explanation,
        "feature_engineering_fix": feature_engineering_fix,
        "backtestable_rule": backtestable_rule,
        "priority": _priority_from_edge(edge_magnitude, sample_size),
        "window_stats": windows["windows"],
        "temporal_stability_score": windows["stability_score"],
    }


def _closing_sectional_findings(df: pd.DataFrame, export_dir: Path) -> List[Dict[str, Any]]:
    base = df.dropna(subset=["last_200m_time"]).copy()
    if base.empty:
        return []
    candidates = []
    group_cols = ["canonical_track", "distance_bucket", "going_group", "class_bucket"]
    for keys, group in base.groupby(group_cols, dropna=False):
        if len(group) < 100 or group["win"].sum() < 5:
            continue
        threshold = group["last_200m_time"].quantile(0.25)
        cohort = group[group["last_200m_time"] <= threshold]
        if len(cohort) < 20:
            continue
        win_rate = safe_rate(cohort["win"].sum(), len(cohort)) * 100.0
        base_rate = safe_rate(group["win"].sum(), len(group)) * 100.0
        edge = win_rate - base_rate
        if edge <= 2.0:
            continue
        candidates.append(
            {
                "canonical_track": keys[0],
                "distance_bucket": keys[1],
                "going_group": keys[2],
                "class_bucket": keys[3],
                "threshold": round(float(threshold), 3),
                "sample_size": int(len(cohort)),
                "winner_win_rate": win_rate,
                "field_average_win_rate": base_rate,
                "edge_magnitude": edge,
            }
        )
    candidates_df = pick_top_candidates(pd.DataFrame(candidates), ["edge_magnitude", "sample_size"], limit=12)
    if candidates_df.empty:
        return []
    csv_safe_write(candidates_df, export_dir / "closing_sectional_candidates.csv")
    findings = []
    for idx, row in candidates_df.iterrows():
        mask = (
            (base["canonical_track"] == row["canonical_track"])
            & (base["distance_bucket"] == row["distance_bucket"])
            & (base["going_group"] == row["going_group"])
            & (base["class_bucket"] == row["class_bucket"])
            & (base["last_200m_time"] <= row["threshold"])
        )
        findings.append(
            _finding_card(
                finding_id=f"A-{idx + 1:03d}",
                category="Sectional",
                pattern_description=(
                    f"At {row['canonical_track']} in {row['distance_bucket']} races on {row['going_group']}, "
                    f"runners closing their last 200m in {row['threshold']:.2f}s or faster win far more often than the field."
                ),
                query_used=(
                    "Runner-level SQL extract from race_results_history + sectional_times, then grouped in pandas by "
                    "track/distance_bucket/going_group/class_bucket; threshold set at cohort 25th percentile of last_200m_time."
                ),
                tracks_affected=row["canonical_track"],
                conditions=f"{row['distance_bucket']} | {row['going_group']} | {row['class_bucket']}",
                winner_win_rate=row["winner_win_rate"],
                field_average_win_rate=row["field_average_win_rate"],
                edge_magnitude=row["edge_magnitude"],
                sample_size=row["sample_size"],
                sectionals_involved="Yes - last_200m_time",
                data_quality_note="Direct sectional timing from sectional_times; no lane-position data required.",
                model_gap_explanation=(
                    "The model can underweight track- and going-specific late-speed thresholds when it only uses broad speed/pace features."
                ),
                feature_engineering_fix=(
                    "Add cohort-normalized fast-close flag: last_200m_time <= 25th percentile for track x distance bucket x going."
                ),
                backtestable_rule=(
                    f"IF track={row['canonical_track']} AND distance_bucket={row['distance_bucket']} AND going={row['going_group']} "
                    f"AND last_200m_time <= {row['threshold']:.2f}s THEN win rate is {row['edge_magnitude']:.2f}pp above base."
                ),
                cohort_mask=mask,
                base_df=base,
            )
        )
    return findings


def _position_findings(df: pd.DataFrame, export_dir: Path, start_index: int) -> List[Dict[str, Any]]:
    base = df.dropna(subset=["pos_400m"]).copy()
    if base.empty:
        return []
    grouped = (
        base.groupby(["canonical_track", "distance_bucket", "going_group", "pos_400m_bucket"], dropna=False)
        .agg(sample_size=("win", "size"), wins=("win", "sum"))
        .reset_index()
    )
    track_base = (
        base.groupby(["canonical_track", "distance_bucket", "going_group"], dropna=False)
        .agg(base_size=("win", "size"), base_wins=("win", "sum"))
        .reset_index()
    )
    merged = grouped.merge(track_base, on=["canonical_track", "distance_bucket", "going_group"], how="left")
    merged["winner_win_rate"] = merged["wins"] / merged["sample_size"] * 100.0
    merged["field_average_win_rate"] = merged["base_wins"] / merged["base_size"] * 100.0
    merged["edge_magnitude"] = merged["winner_win_rate"] - merged["field_average_win_rate"]
    merged = merged[(merged["sample_size"] >= 20) & (merged["edge_magnitude"] > 2.5)]
    merged = pick_top_candidates(merged, ["edge_magnitude", "sample_size"], limit=12)
    if merged.empty:
        return []
    csv_safe_write(merged, export_dir / "position_400m_candidates.csv")
    findings = []
    for offset, row in merged.iterrows():
        mask = (
            (base["canonical_track"] == row["canonical_track"])
            & (base["distance_bucket"] == row["distance_bucket"])
            & (base["going_group"] == row["going_group"])
            & (base["pos_400m_bucket"] == row["pos_400m_bucket"])
        )
        findings.append(
            _finding_card(
                finding_id=f"A-{start_index + offset:03d}",
                category="Sectional",
                pattern_description=(
                    f"At {row['canonical_track']} in {row['distance_bucket']} races on {row['going_group']}, horses sitting "
                    f"{row['pos_400m_bucket']} at the 400m marker win above the local base rate."
                ),
                query_used=(
                    "Runner-level SQL extract from race_results_history + sectional_times; 400m positions read from splits_json['400m'].position "
                    "and grouped in pandas by track/distance_bucket/going_group/pos_400m_bucket."
                ),
                tracks_affected=row["canonical_track"],
                conditions=f"{row['distance_bucket']} | {row['going_group']} | 400m position {row['pos_400m_bucket']}",
                winner_win_rate=row["winner_win_rate"],
                field_average_win_rate=row["field_average_win_rate"],
                edge_magnitude=row["edge_magnitude"],
                sample_size=row["sample_size"],
                sectionals_involved="Yes - 400m in-run position from splits_json",
                data_quality_note="Requires splits_json position fields; cohorts without 400m positions are excluded.",
                model_gap_explanation=(
                    "A generic running-style label can miss track- and tempo-specific strike zones at the 400m marker."
                ),
                feature_engineering_fix=(
                    "Add 400m position bucket win-rate priors by track x distance bucket x going."
                ),
                backtestable_rule=(
                    f"IF track={row['canonical_track']} AND distance_bucket={row['distance_bucket']} AND going={row['going_group']} "
                    f"AND pos_400m_bucket={row['pos_400m_bucket']} THEN win rate is {row['edge_magnitude']:.2f}pp above base."
                ),
                cohort_mask=mask,
                base_df=base,
            )
        )
    return findings


def _pace_interaction_findings(df: pd.DataFrame, export_dir: Path, start_index: int) -> List[Dict[str, Any]]:
    race_pace = _race_level_pace_labels(df)
    if race_pace.empty:
        return []
    base = df.merge(
        race_pace[["canonical_track", "race_date", "race_number", "pace_class"]],
        how="left",
        on=["canonical_track", "race_date", "race_number"],
    )
    candidates = []
    for pace_class, role in [("hot", "off_pace"), ("slow", "contested")]:
        subset = base[(base["pace_class"] == pace_class) & (base["pace_role_600m"] == role)]
        grouped = (
            subset.groupby(["canonical_track", "distance_bucket", "going_group"], dropna=False)
            .agg(sample_size=("win", "size"), wins=("win", "sum"))
            .reset_index()
        )
        pace_base = (
            base[base["pace_class"] == pace_class]
            .groupby(["canonical_track", "distance_bucket", "going_group"], dropna=False)
            .agg(base_size=("win", "size"), base_wins=("win", "sum"))
            .reset_index()
        )
        merged = grouped.merge(pace_base, on=["canonical_track", "distance_bucket", "going_group"], how="left")
        if merged.empty:
            continue
        merged["winner_win_rate"] = merged["wins"] / merged["sample_size"] * 100.0
        merged["field_average_win_rate"] = merged["base_wins"] / merged["base_size"] * 100.0
        merged["edge_magnitude"] = merged["winner_win_rate"] - merged["field_average_win_rate"]
        merged["pace_class"] = pace_class
        merged["pace_role"] = role
        candidates.append(merged)
    if not candidates:
        return []
    candidates_df = pd.concat(candidates, ignore_index=True)
    candidates_df = candidates_df[(candidates_df["sample_size"] >= 20) & (candidates_df["edge_magnitude"] > 2.0)]
    candidates_df = pick_top_candidates(candidates_df, ["edge_magnitude", "sample_size"], limit=10)
    if candidates_df.empty:
        return []
    csv_safe_write(candidates_df, export_dir / "pace_interaction_candidates.csv")
    findings = []
    for offset, row in candidates_df.iterrows():
        mask = (
            (base["canonical_track"] == row["canonical_track"])
            & (base["distance_bucket"] == row["distance_bucket"])
            & (base["going_group"] == row["going_group"])
            & (base["pace_class"] == row["pace_class"])
            & (base["pace_role_600m"] == row["pace_role"])
        )
        findings.append(
            _finding_card(
                finding_id=f"A-{start_index + offset:03d}",
                category="Sectional",
                pattern_description=(
                    f"In {row['pace_class']} early-pace races at {row['canonical_track']}, {row['pace_role']} runners in "
                    f"{row['distance_bucket']} events outperform the race base rate."
                ),
                query_used=(
                    "Race-level opening_600m derived from splits_json segment ladder; hot/slow pace labels assigned by 25th/75th cohort "
                    "quantiles, then runner win rates compared by pace_role_600m."
                ),
                tracks_affected=row["canonical_track"],
                conditions=f"{row['distance_bucket']} | {row['going_group']} | pace={row['pace_class']} | role={row['pace_role']}",
                winner_win_rate=row["winner_win_rate"],
                field_average_win_rate=row["field_average_win_rate"],
                edge_magnitude=row["edge_magnitude"],
                sample_size=row["sample_size"],
                sectionals_involved="Yes - opening 600m derived from split ladder + 600m in-run position",
                data_quality_note="Opening 600m is approximated from segment times; off-pace/contested uses 600m position as a pace proxy.",
                model_gap_explanation=(
                    "Static running-style features miss the interaction between race-level tempo and where the horse sat when pressure developed."
                ),
                feature_engineering_fix=(
                    "Add pace-scenario interaction features: hot/slow race flag x 600m pace-role bucket."
                ),
                backtestable_rule=(
                    f"IF track={row['canonical_track']} AND pace_class={row['pace_class']} AND pace_role={row['pace_role']} "
                    f"THEN win rate is {row['edge_magnitude']:.2f}pp above same-race base."
                ),
                cohort_mask=mask,
                base_df=base,
            )
        )
    return findings


def _previous_run_sectional_findings(df: pd.DataFrame, export_dir: Path, start_index: int) -> List[Dict[str, Any]]:
    base = df.dropna(subset=["race_date_dt"]).sort_values(["horse_key", "race_date_dt", "race_number"]).copy()
    if base.empty:
        return []

    def enrich(group: pd.DataFrame) -> pd.DataFrame:
        group = group.copy()
        group["prev_finish_pos"] = group["position"].shift(1)
        group["prev_last200_time"] = group["last_200m_time"].shift(1)
        group["prior_best_before_prev"] = group["last_200m_time"].expanding().min().shift(2)
        group["prev_was_pb_last200"] = group["prev_last200_time"].notna() & (
            group["prior_best_before_prev"].isna() | (group["prev_last200_time"] <= group["prior_best_before_prev"])
        )
        return group

    enriched = base.groupby("horse_key", group_keys=False).apply(enrich).reset_index(drop=True)
    qualified = enriched[
        enriched["prev_finish_pos"].between(2, 5, inclusive="both")
        & enriched["prev_was_pb_last200"].fillna(False)
    ].copy()
    if qualified.empty:
        return []
    grouped = (
        qualified.groupby(["canonical_track", "class_bucket"], dropna=False)
        .agg(sample_size=("win", "size"), wins=("win", "sum"))
        .reset_index()
    )
    base_group = (
        enriched[enriched["prev_finish_pos"].between(2, 5, inclusive="both")]
        .groupby(["canonical_track", "class_bucket"], dropna=False)
        .agg(base_size=("win", "size"), base_wins=("win", "sum"))
        .reset_index()
    )
    merged = grouped.merge(base_group, on=["canonical_track", "class_bucket"], how="left")
    merged["winner_win_rate"] = merged["wins"] / merged["sample_size"] * 100.0
    merged["field_average_win_rate"] = merged["base_wins"] / merged["base_size"] * 100.0
    merged["edge_magnitude"] = merged["winner_win_rate"] - merged["field_average_win_rate"]
    merged = merged[(merged["sample_size"] >= 15) & (merged["edge_magnitude"] > 2.0)]
    merged = pick_top_candidates(merged, ["edge_magnitude", "sample_size"], limit=8)
    if merged.empty:
        return []
    csv_safe_write(merged, export_dir / "previous_run_pb_candidates.csv")
    findings = []
    for offset, row in merged.iterrows():
        mask = (
            (enriched["canonical_track"] == row["canonical_track"])
            & (enriched["class_bucket"] == row["class_bucket"])
            & enriched["prev_finish_pos"].between(2, 5, inclusive="both")
            & enriched["prev_was_pb_last200"].fillna(False)
        )
        findings.append(
            _finding_card(
                finding_id=f"A-{start_index + offset:03d}",
                category="Sectional",
                pattern_description=(
                    f"At {row['canonical_track']}, horses coming off a 2nd-5th finish with a personal-best last-200m split "
                    f"win next start above the cohort base rate."
                ),
                query_used=(
                    "Runner history ordered by horse_key/race_date; previous-run PB last_200m flagged in pandas, then next-start win rates "
                    "compared against all runners coming off prior 2nd-5th finishes."
                ),
                tracks_affected=row["canonical_track"],
                conditions=f"class={row['class_bucket']} | prior finish 2nd-5th | prior PB last-200m",
                winner_win_rate=row["winner_win_rate"],
                field_average_win_rate=row["field_average_win_rate"],
                edge_magnitude=row["edge_magnitude"],
                sample_size=row["sample_size"],
                sectionals_involved="Yes - prior-run last_200m_time",
                data_quality_note="Uses horse_key continuity and available prior sectional history only.",
                model_gap_explanation=(
                    "The current stack can miss latent fitness when a runner’s best closing split came in a losing run rather than a win."
                ),
                feature_engineering_fix=(
                    "Add previous-run PB closing-split flag plus prior finishing-position interaction."
                ),
                backtestable_rule=(
                    f"IF prior_finish in 2-5 AND prior_run_last200_is_personal_best AND track={row['canonical_track']} "
                    f"THEN next-start win rate is {row['edge_magnitude']:.2f}pp above base."
                ),
                cohort_mask=mask,
                base_df=enriched,
            )
        )
    return findings


def _wet_spread_findings(df: pd.DataFrame, export_dir: Path, start_index: int) -> List[Dict[str, Any]]:
    base = df.dropna(subset=["last_200m_time"]).copy()
    if base.empty:
        return []
    spread_rows = []
    for going_group in ["Good", "Soft 6-7", "Heavy 8-10"]:
        group = base[base["going_group"] == going_group]
        if len(group) < 100:
            continue
        threshold = group["last_200m_time"].quantile(0.25)
        fast = group[group["last_200m_time"] <= threshold]
        spread_rows.append(
            {
                "going_group": going_group,
                "sample_size": len(group),
                "spread_sd": float(group["last_200m_time"].std(ddof=0)),
                "fast_quartile_win_rate": safe_rate(fast["win"].sum(), len(fast)) * 100.0 if len(fast) else 0.0,
                "base_win_rate": safe_rate(group["win"].sum(), len(group)) * 100.0,
                "threshold": float(threshold),
            }
        )
    spread_df = pd.DataFrame(spread_rows)
    if spread_df.empty or "Good" not in set(spread_df["going_group"]):
        return []
    csv_safe_write(spread_df, export_dir / "wet_spread_summary.csv")
    good_row = spread_df[spread_df["going_group"] == "Good"].iloc[0]
    findings = []
    for offset, (_, row) in enumerate(spread_df[spread_df["going_group"].isin(["Soft 6-7", "Heavy 8-10"])].iterrows()):
        edge = (row["fast_quartile_win_rate"] - row["base_win_rate"]) - (good_row["fast_quartile_win_rate"] - good_row["base_win_rate"])
        if edge <= 1.0:
            continue
        cohort_mask = (base["going_group"] == row["going_group"]) & (base["last_200m_time"] <= row["threshold"])
        findings.append(
            _finding_card(
                finding_id=f"A-{start_index + offset:03d}",
                category="Going",
                pattern_description=(
                    f"On {row['going_group']} ground, the fast-closing quartile separates from the field more than it does on Good ground."
                ),
                query_used=(
                    "Overall sectional spread and fast-quartile win rates grouped by going_group using last_200m_time."
                ),
                tracks_affected="Metro aggregate",
                conditions=f"going={row['going_group']}",
                winner_win_rate=row["fast_quartile_win_rate"],
                field_average_win_rate=row["base_win_rate"],
                edge_magnitude=edge,
                sample_size=int(row["sample_size"]),
                sectionals_involved="Yes - last_200m_time distribution spread",
                data_quality_note="Wet-track effect is aggregated because per-track wet samples are thinner than Good samples.",
                model_gap_explanation=(
                    "A model calibrated mostly on Good-ground sectionals can underweight how strongly wet tracks expose late-energy differences."
                ),
                feature_engineering_fix=(
                    "Increase wet-track sectional weighting or add wet-only fast-closing quartile flags."
                ),
                backtestable_rule=(
                    f"IF going_group={row['going_group']} AND runner is in the fastest quartile of last_200m_time THEN uplift is {edge:.2f}pp above the Good-track analogue."
                ),
                cohort_mask=cohort_mask,
                base_df=base,
            )
        )
    return findings


def _barrier_findings(df: pd.DataFrame, export_dir: Path, start_index: int) -> List[Dict[str, Any]]:
    base = df.copy()
    grouped = (
        base.groupby(["canonical_track", "distance_bucket", "wet_group", "barrier_segment", "field_size_bucket"], dropna=False)
        .agg(sample_size=("win", "size"), wins=("win", "sum"))
        .reset_index()
    )
    group_base = (
        base.groupby(["canonical_track", "distance_bucket", "wet_group", "field_size_bucket"], dropna=False)
        .agg(base_size=("win", "size"), base_wins=("win", "sum"))
        .reset_index()
    )
    merged = grouped.merge(group_base, on=["canonical_track", "distance_bucket", "wet_group", "field_size_bucket"], how="left")
    merged["winner_win_rate"] = merged["wins"] / merged["sample_size"] * 100.0
    merged["field_average_win_rate"] = merged["base_wins"] / merged["base_size"] * 100.0
    merged["edge_magnitude"] = merged["winner_win_rate"] - merged["field_average_win_rate"]
    merged = merged[(merged["sample_size"] >= 20) & (merged["edge_magnitude"] > 2.0)]
    merged = pick_top_candidates(merged, ["edge_magnitude", "sample_size"], limit=10)
    if merged.empty:
        return []
    csv_safe_write(merged, export_dir / "barrier_bias_candidates.csv")
    findings = []
    for offset, row in merged.iterrows():
        mask = (
            (base["canonical_track"] == row["canonical_track"])
            & (base["distance_bucket"] == row["distance_bucket"])
            & (base["wet_group"] == row["wet_group"])
            & (base["field_size_bucket"] == row["field_size_bucket"])
            & (base["barrier_segment"] == row["barrier_segment"])
        )
        findings.append(
            _finding_card(
                finding_id=f"A-{start_index + offset:03d}",
                category="Barrier",
                pattern_description=(
                    f"At {row['canonical_track']} in {row['distance_bucket']} races, barrier segment {row['barrier_segment']} "
                    f"wins above the local base rate when the surface is {row['wet_group']} and field size is {row['field_size_bucket']}."
                ),
                query_used=(
                    "Runner-level win rates grouped by track, distance bucket, wet/dry group, barrier segment, and field-size bucket."
                ),
                tracks_affected=row["canonical_track"],
                conditions=(
                    f"{row['distance_bucket']} | surface={row['wet_group']} | field_size={row['field_size_bucket']} | barrier={row['barrier_segment']}"
                ),
                winner_win_rate=row["winner_win_rate"],
                field_average_win_rate=row["field_average_win_rate"],
                edge_magnitude=row["edge_magnitude"],
                sample_size=row["sample_size"],
                sectionals_involved="No",
                data_quality_note="Historical rail-position data is unavailable, so this is a barrier-only effect without rail interaction.",
                model_gap_explanation=(
                    "Barrier features often get modelled too statically and miss the field-size x surface interaction."
                ),
                feature_engineering_fix=(
                    "Add barrier segment x field-size bucket x wet/dry interaction features by track."
                ),
                backtestable_rule=(
                    f"IF track={row['canonical_track']} AND barrier_segment={row['barrier_segment']} AND field_size_bucket={row['field_size_bucket']} "
                    f"AND wet_group={row['wet_group']} THEN win rate is {row['edge_magnitude']:.2f}pp above base."
                ),
                cohort_mask=mask,
                base_df=base,
            )
        )
    return findings


def _heavy_first_time_finding(df: pd.DataFrame, start_index: int) -> List[Dict[str, Any]]:
    base = df.dropna(subset=["race_date_dt"]).sort_values(["horse_key", "race_date_dt", "race_number"]).copy()
    heavy_mask = base["going_group"].isin(["Heavy", "Heavy 8-10"])
    base["prior_heavy_runs"] = base.groupby("horse_key", group_keys=False)["going_group"].apply(
        lambda s: s.isin(["Heavy", "Heavy 8-10"]).cumsum().shift(1).fillna(0)
    )
    first_time_heavy = heavy_mask & (base["prior_heavy_runs"] == 0)
    experienced_heavy = heavy_mask & (base["prior_heavy_runs"] > 0)
    if first_time_heavy.sum() < 20 or experienced_heavy.sum() < 20:
        return []
    first_win = safe_rate(base.loc[first_time_heavy, "win"].sum(), first_time_heavy.sum()) * 100.0
    exp_win = safe_rate(base.loc[experienced_heavy, "win"].sum(), experienced_heavy.sum()) * 100.0
    edge = first_win - exp_win
    if abs(edge) < 1.0:
        return []
    return [
        _finding_card(
            finding_id=f"A-{start_index:03d}",
            category="Going",
            pattern_description="First-time Heavy runners perform differently to wet-exposed runners and should be modelled as an uncertainty cohort, not a generic wet-track group.",
            query_used="Horse-history sequence from race_results_history grouped by horse_key; Heavy-going first-time exposure flagged in pandas.",
            tracks_affected="All tracks in scope",
            conditions="Heavy going only",
            winner_win_rate=first_win,
            field_average_win_rate=exp_win,
            edge_magnitude=edge,
            sample_size=int(first_time_heavy.sum()),
            sectionals_involved="No",
            data_quality_note="Uses only documented Heavy-going runs in the historical window; breeding-based wet ability is not available here.",
            model_gap_explanation="The model can overstate certainty on unknown wet-track ability if first-time Heavy runners are treated the same as proven wet trackers.",
            feature_engineering_fix="Add first_time_heavy flag plus interaction with track/price/class cohorts.",
            backtestable_rule=f"IF runner is first-time Heavy THEN win rate differs from heavy-exposed runners by {edge:.2f}pp.",
            cohort_mask=first_time_heavy,
            base_df=base,
        )
    ]


def _tight_track_front_runner_findings(df: pd.DataFrame, start_index: int) -> List[Dict[str, Any]]:
    base = df.dropna(subset=["pos_600m"]).copy()
    front = base["pos_600m"] <= 2
    records = []
    for track in sorted(set(TIGHT_TURNING_TRACKS) & set(base["canonical_track"].unique())):
        track_df = base[base["canonical_track"] == track]
        front_mask = (track_df["pos_600m"] <= 2)
        front_rate = safe_rate(track_df.loc[front_mask, "win"].sum(), front_mask.sum()) * 100.0 if front_mask.sum() else 0.0
        base_rate = safe_rate(track_df["win"].sum(), len(track_df)) * 100.0 if len(track_df) else 0.0
        edge = front_rate - base_rate
        if front_mask.sum() >= 20 and edge > 2.0:
            records.append((track, front_mask.sum(), front_rate, base_rate, edge))
    findings = []
    for offset, (track, sample_size, front_rate, base_rate, edge) in enumerate(records):
        mask = (base["canonical_track"] == track) & (base["pos_600m"] <= 2)
        findings.append(
            _finding_card(
                finding_id=f"A-{start_index + offset:03d}",
                category="Configuration",
                pattern_description=f"{track} rewards horses racing in the first two at the 600m more than the general metro base rate.",
                query_used="Runner-level 600m in-run positions from sectional splits_json grouped by track; front-running defined as position <= 2 at 600m.",
                tracks_affected=track,
                conditions="Tight-turning metro track",
                winner_win_rate=front_rate,
                field_average_win_rate=base_rate,
                edge_magnitude=edge,
                sample_size=int(sample_size),
                sectionals_involved="Yes - 600m in-run position from splits_json",
                data_quality_note="Course-turn geometry is inferred from track identity; no lane-position data exists for straight-track lane bias analysis.",
                model_gap_explanation="Venue-specific front-runner advantages are often diluted when pace features are fit nationally.",
                feature_engineering_fix="Add track-specific front-runner advantage priors from 600m position data.",
                backtestable_rule=f"IF track={track} AND pos_600m <= 2 THEN win rate is {edge:.2f}pp above the local base.",
                cohort_mask=mask,
                base_df=base,
            )
        )
    return findings


def run_agent1_analysis(coverage: CoverageArtifacts, export_dir: Path) -> Dict[str, Any]:
    agent_dir = export_dir / "agent1"
    base = _merge_results_and_sectionals(coverage)
    notes = [
        "Historical rail-position data is not stored in the production DB, so rail-out analysis is skipped and documented as unavailable.",
        "Flemington straight lane-bias analysis is not emitted because there is no lateral lane/side-of-track column in the historical runner data.",
    ]

    findings: List[Dict[str, Any]] = []
    findings.extend(_closing_sectional_findings(base, agent_dir))
    findings.extend(_position_findings(base, agent_dir, start_index=len(findings) + 1))
    findings.extend(_pace_interaction_findings(base, agent_dir, start_index=len(findings) + 1))
    findings.extend(_previous_run_sectional_findings(base, agent_dir, start_index=len(findings) + 1))
    findings.extend(_wet_spread_findings(base, agent_dir, start_index=len(findings) + 1))
    findings.extend(_barrier_findings(base, agent_dir, start_index=len(findings) + 1))
    findings.extend(_heavy_first_time_finding(base, start_index=len(findings) + 1))
    findings.extend(_tight_track_front_runner_findings(base, start_index=len(findings) + 1))

    if findings:
        findings = sorted(findings, key=lambda item: (item["priority"], -item["edge_magnitude"], -item["sample_size"]))
        for idx, finding in enumerate(findings, start=1):
            finding["finding_id"] = f"A-{idx:03d}"

    summary = {
        "notes": notes,
        "base_runner_rows": int(len(base.index)),
        "findings_count": len(findings),
        "tracks_covered": sorted(t for t in base["canonical_track"].dropna().unique() if t),
    }

    json_write({"summary": summary, "findings": findings}, agent_dir / "agent1_findings.json")
    markdown_write(markdown_from_findings("Agent 1 Findings", findings, notes=notes), agent_dir / "agent1_findings.md")
    csv_safe_write(base.head(1000), agent_dir / "agent1_sample_base_rows.csv")
    return {"summary": summary, "findings": findings}
