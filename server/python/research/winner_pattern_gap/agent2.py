from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from .config import DSLR_BUCKETS, METRO_TRACK_ORDER, PRICE_BRACKETS, dslr_bucket, field_size_bucket, price_bracket
from .coverage import CoverageArtifacts
from .db import ResearchDB, csv_safe_write, json_write, markdown_write
from .utils import (
    compute_implied_prob,
    compute_window_stats,
    derive_base_dataframe_fields,
    make_join_key,
    markdown_from_findings,
    normalize_name_column,
    parse_prob,
    pick_top_candidates,
    safe_rate,
)


CLASS_RANKS = {
    "Maiden": 1,
    "BM58-64": 2,
    "BM65-70": 3,
    "BM71-78": 4,
    "BM79-84": 5,
    "BM85-90": 6,
    "Listed": 7,
    "Group 3": 8,
    "Group 2": 9,
    "Group 1": 10,
    "Open/Other": 6,
}


def _priority_from_edge(edge_pct: float, sample_size: int) -> int:
    if edge_pct >= 10 and sample_size >= 200:
        return 1
    if edge_pct >= 7 and sample_size >= 125:
        return 2
    if edge_pct >= 4 and sample_size >= 100:
        return 3
    if edge_pct >= 2 and sample_size >= 75:
        return 4
    return 5


def _market_efficiency_note(edge_pct: float) -> str:
    if edge_pct >= 3.0:
        return "systematically wrong"
    if edge_pct >= 1.0:
        return "partially right"
    if edge_pct <= -3.0:
        return "overpricing this cohort materially"
    return "largely correct"


def _class_rank(row: pd.Series) -> float:
    if pd.notna(row.get("class_level")):
        try:
            return float(row["class_level"])
        except Exception:
            pass
    return float(CLASS_RANKS.get(row.get("class_bucket"), np.nan))


def _prepare_rrh_history(coverage: CoverageArtifacts) -> pd.DataFrame:
    rrh = coverage.rrh_df.copy()
    rrh["join_key"] = make_join_key(rrh["track"], rrh["race_date"], rrh["race_number"], rrh["horse_name"])
    rrh = derive_base_dataframe_fields(rrh)
    rrh["horse_key"] = rrh["horse_id"].fillna("").astype(str)
    rrh.loc[rrh["horse_key"].eq(""), "horse_key"] = normalize_name_column(rrh.loc[rrh["horse_key"].eq(""), "horse_name"])
    rrh["class_rank"] = rrh.apply(_class_rank, axis=1)
    rrh["sp_odds_numeric"] = pd.to_numeric(rrh["sp_odds"], errors="coerce")
    rrh["win"] = rrh["position"].fillna(0).astype(int) == 1
    rrh = rrh.sort_values(["horse_key", "race_date_dt", "race_number", "id"]).reset_index(drop=True)
    rrh["prev_race_date_dt"] = rrh.groupby("horse_key", dropna=False)["race_date_dt"].shift(1)
    rrh["days_since_last_run"] = (rrh["race_date_dt"] - rrh["prev_race_date_dt"]).dt.days
    rrh["prev_position"] = rrh.groupby("horse_key", dropna=False)["position"].shift(1)
    rrh["prev2_position"] = rrh.groupby("horse_key", dropna=False)["position"].shift(2)
    rrh["prev3_position"] = rrh.groupby("horse_key", dropna=False)["position"].shift(3)
    rrh["prev_class_rank"] = rrh.groupby("horse_key", dropna=False)["class_rank"].shift(1)
    rrh["prev_days_since_last_run"] = rrh.groupby("horse_key", dropna=False)["days_since_last_run"].shift(1)
    rrh["dslr_bucket"] = rrh["days_since_last_run"].apply(dslr_bucket)
    rrh["first_up"] = rrh["days_since_last_run"].ge(60)
    rrh["second_up"] = rrh["prev_days_since_last_run"].ge(60)
    rrh["quick_backup"] = rrh["days_since_last_run"].between(1, 6, inclusive="both")
    rrh["field_size_bucket"] = rrh["field_size"].apply(field_size_bucket)
    rrh["class_move"] = np.select(
        [
            rrh["prev_class_rank"].notna() & rrh["class_rank"].notna() & (rrh["class_rank"] < rrh["prev_class_rank"]),
            rrh["prev_class_rank"].notna() & rrh["class_rank"].notna() & (rrh["class_rank"] > rrh["prev_class_rank"]),
        ],
        ["drop", "rise"],
        default="same_or_unknown",
    )
    rrh["class_drop_levels"] = np.where(
        rrh["prev_class_rank"].notna() & rrh["class_rank"].notna(),
        rrh["prev_class_rank"] - rrh["class_rank"],
        np.nan,
    )
    rrh["placement_sequence"] = (
        rrh["prev3_position"].fillna(0).astype(int).astype(str)
        + "-"
        + rrh["prev2_position"].fillna(0).astype(int).astype(str)
        + "-"
        + rrh["prev_position"].fillna(0).astype(int).astype(str)
    )
    rrh["wet_track"] = rrh["going_group"].isin(["Soft", "Soft 6-7", "Heavy", "Heavy 8-10"])
    rrh["weekday"] = rrh["race_date_dt"].dt.dayofweek
    rrh["meeting_type"] = np.select(
        [
            rrh["canonical_track"].isin(METRO_TRACK_ORDER) & rrh["weekday"].eq(5),
            rrh["canonical_track"].isin(METRO_TRACK_ORDER) & rrh["weekday"].isin([1, 2, 3, 4]),
        ],
        ["Saturday metro", "Midweek metro"],
        default="Other",
    )
    return rrh


def _usable_market_rollup(coverage: CoverageArtifacts) -> pd.DataFrame:
    usable = coverage.usable_market_df.copy()
    if usable.empty:
        return pd.DataFrame(columns=["join_key", "usable_market_odds", "usable_minutes_to_jump"])
    usable["join_key"] = make_join_key(usable["track"], usable["meeting_date"], usable["race_number"], usable["horse_name"])
    usable["market_odds"] = pd.to_numeric(usable["market_odds"], errors="coerce")
    usable = usable[usable["market_odds"].notna()].copy()
    if usable.empty:
        return pd.DataFrame(columns=["join_key", "usable_market_odds", "usable_minutes_to_jump"])
    rollup = (
        usable.groupby("join_key", dropna=False)
        .agg(
            usable_market_odds=("market_odds", "median"),
            usable_minutes_to_jump=("minutes_to_jump", "min"),
        )
        .reset_index()
    )
    return rollup


def _merge_model_view(history: pd.DataFrame, coverage: CoverageArtifacts) -> pd.DataFrame:
    tv2 = coverage.tv2_df.copy()
    tv2["join_key"] = make_join_key(tv2["track"], tv2["race_date"], tv2["race_number"], tv2["horse_name"])
    tv2 = tv2.sort_values(["race_date_dt", "race_number"]).drop_duplicates(subset=["join_key"], keep="last")
    tv2 = tv2[
        [
            "join_key",
            "market_odds",
            "model_probability",
            "predicted_win_prob",
            "expected_value",
            "prior_z_200m",
            "prior_z_400m",
            "prior_z_600m",
            "prior_z_800m",
            "prior_sectional_date",
            "running_style",
            "confidence",
        ]
    ].copy()

    usable = _usable_market_rollup(coverage)

    base = history.merge(tv2, how="left", on="join_key", suffixes=("", "_tv2"))
    base = base.merge(usable, how="left", on="join_key")
    base["market_odds"] = pd.to_numeric(base["market_odds"], errors="coerce").combine_first(
        pd.to_numeric(base["usable_market_odds"], errors="coerce")
    )
    base["model_probability_value"] = base["model_probability"].apply(parse_prob)
    base["predicted_win_prob_value"] = base["predicted_win_prob"].apply(parse_prob)
    base["model_prob"] = base["model_probability_value"].combine_first(base["predicted_win_prob_value"])
    base["expected_value"] = pd.to_numeric(base["expected_value"], errors="coerce")
    base["implied_prob"] = base["market_odds"].apply(compute_implied_prob)
    base["price_bracket"] = base["market_odds"].apply(price_bracket)
    base["missed_winner"] = base["win"] & (
        base["expected_value"].le(0).fillna(False)
        | (base["model_prob"].notna() & base["implied_prob"].notna() & (base["model_prob"] < base["implied_prob"]))
    )
    return base


def _fetch_market_window_prices(
    db: ResearchDB,
    coverage: CoverageArtifacts,
    *,
    date_from: str,
    date_to: str,
) -> pd.DataFrame:
    if coverage.betfair_map_df.empty or coverage.eligible_market_cohorts.empty:
        return pd.DataFrame()
    eligible = coverage.betfair_map_df.copy()
    eligible["meeting_date_dt"] = pd.to_datetime(eligible["meeting_date"], errors="coerce")
    eligible["month"] = eligible["meeting_date_dt"].dt.to_period("M").astype(str)
    eligible = eligible.merge(
        coverage.eligible_market_cohorts[["canonical_track", "month"]],
        how="inner",
        left_on=["canonical_track", "month"] if "canonical_track" in eligible.columns else ["track", "month"],
        right_on=["canonical_track", "month"],
    )
    if eligible.empty:
        return pd.DataFrame()
    eligible["runner_key"] = eligible["market_id"].astype(str) + "|" + eligible["selection_id"].astype(str)
    eligible["market_time_ts"] = pd.to_datetime(eligible["market_time"], errors="coerce", utc=True)

    market_frames: List[pd.DataFrame] = []
    market_ids = eligible["market_id"].dropna().astype(str).unique().tolist()
    chunk_size = 150
    for start in range(0, len(market_ids), chunk_size):
        chunk = market_ids[start : start + chunk_size]
        if not chunk:
            continue
        frame = db.fetch_df(
            f"agent2_market_data_chunk_{start // chunk_size + 1}",
            """
            SELECT market_id::text AS market_id,
                   selection_id::text AS selection_id,
                   timestamp,
                   price
            FROM market_data
            WHERE market_id = ANY(%(market_ids)s)
            """,
            {"market_ids": chunk},
            note="Chunked raw Betfair market snapshot pull; bounded by eligible mapped market_ids only.",
        )
        if frame.empty:
            continue
        market_frames.append(frame)
    if not market_frames:
        return pd.DataFrame()

    market = pd.concat(market_frames, ignore_index=True)
    market["runner_key"] = market["market_id"].astype(str) + "|" + market["selection_id"].astype(str)
    market["timestamp"] = pd.to_datetime(market["timestamp"], errors="coerce", utc=True)
    market["price"] = pd.to_numeric(market["price"], errors="coerce")
    market = market.merge(
        eligible[["runner_key", "join_key", "market_time_ts"]].drop_duplicates(),
        how="inner",
        on="runner_key",
    )
    market = market[market["timestamp"].notna() & market["market_time_ts"].notna() & market["price"].notna()].copy()
    if market.empty:
        return pd.DataFrame()
    market["minutes_to_jump"] = (market["market_time_ts"] - market["timestamp"]).dt.total_seconds() / 60.0
    market = market[market["minutes_to_jump"].between(-2, 60, inclusive="both")].copy()
    if market.empty:
        return pd.DataFrame()

    def _window_price(series: pd.Series) -> float:
        if series.empty:
            return np.nan
        return float(series.median())

    rows = []
    for join_key, group in market.groupby("join_key", dropna=False):
        rows.append(
            {
                "join_key": join_key,
                "price_30m": _window_price(group.loc[group["minutes_to_jump"].between(25, 35, inclusive="both"), "price"]),
                "price_10m": _window_price(group.loc[group["minutes_to_jump"].between(8, 12, inclusive="both"), "price"]),
                "price_2m": _window_price(group.loc[group["minutes_to_jump"].between(0, 3, inclusive="both"), "price"]),
            }
        )
    window_df = pd.DataFrame(rows)
    if window_df.empty:
        return window_df
    window_df["steam_pct"] = np.where(
        window_df["price_30m"].gt(0) & window_df["price_2m"].gt(0),
        (window_df["price_30m"] - window_df["price_2m"]) / window_df["price_30m"],
        np.nan,
    )
    window_df["movement_class"] = np.select(
        [
            window_df["steam_pct"] >= 0.20,
            window_df["steam_pct"] <= -0.20,
            window_df["steam_pct"].abs() < 0.10,
        ],
        ["steam", "drift", "hold"],
        default="other",
    )
    return window_df


def _finding_card(
    *,
    finding_id: str,
    category: str,
    pattern_description: str,
    query_used: str,
    race_types_affected: str,
    conditions: str,
    winner_win_rate: float,
    field_average_win_rate: float,
    betfair_implied_win_rate: float,
    edge_magnitude: float,
    sample_size: int,
    market_efficiency_note: str,
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
        "race_types_affected": race_types_affected,
        "conditions": conditions,
        "winner_win_rate": round(winner_win_rate, 2),
        "field_average_win_rate": round(field_average_win_rate, 2),
        "betfair_implied_win_rate": round(betfair_implied_win_rate, 2),
        "edge_magnitude": round(edge_magnitude, 2),
        "sample_size": int(sample_size),
        "market_efficiency_note": market_efficiency_note,
        "model_gap_explanation": model_gap_explanation,
        "feature_engineering_fix": feature_engineering_fix,
        "backtestable_rule": backtestable_rule,
        "priority": _priority_from_edge(edge_magnitude, sample_size),
        "window_stats": windows["windows"],
        "temporal_stability_score": windows["stability_score"],
    }


def _dslr_findings(base: pd.DataFrame, export_dir: Path) -> List[Dict[str, Any]]:
    df = base[base["days_since_last_run"].notna() & base["market_odds"].notna()].copy()
    if df.empty:
        return []
    summary = (
        df.groupby("dslr_bucket", dropna=False)
        .agg(
            runners=("win", "count"),
            winners=("win", "sum"),
            implied_prob=("implied_prob", "mean"),
        )
        .reset_index()
    )
    summary["winner_win_rate"] = summary.apply(lambda r: safe_rate(r["winners"], r["runners"]) * 100.0, axis=1)
    summary["betfair_implied_win_rate"] = summary["implied_prob"].fillna(0.0) * 100.0
    summary["edge_magnitude"] = summary["winner_win_rate"] - summary["betfair_implied_win_rate"]
    ordered = []
    for bucket, _, _ in DSLR_BUCKETS:
        match = summary[summary["dslr_bucket"] == bucket]
        if not match.empty:
            ordered.append(match.iloc[0].to_dict())
    summary_df = pd.DataFrame(ordered if ordered else summary.to_dict("records"))
    csv_safe_write(summary_df, export_dir / "dslr_bucket_summary.csv")

    candidates = summary_df[(summary_df["runners"] >= 100) & (summary_df["edge_magnitude"] >= 1.5)].copy()
    candidates = candidates.sort_values(["edge_magnitude", "runners"], ascending=[False, False]).head(6)
    findings: List[Dict[str, Any]] = []
    field_rate = safe_rate(df["win"].sum(), len(df)) * 100.0
    for idx, row in candidates.iterrows():
        mask = df["dslr_bucket"] == row["dslr_bucket"]
        findings.append(
            _finding_card(
                finding_id=f"B-{idx + 1:03d}",
                category="Form Cycle",
                pattern_description=f"Runners in the {row['dslr_bucket']} DSLR bucket outperform their Betfair-implied win rate.",
                query_used="Group runners by DSLR bucket from race_results_history sequence, then compare actual win% to mean Betfair-implied win% from market_odds/training_view_v2.",
                race_types_affected="All race types with mapped market coverage",
                conditions=f"DSLR bucket = {row['dslr_bucket']}",
                winner_win_rate=float(row["winner_win_rate"]),
                field_average_win_rate=field_rate,
                betfair_implied_win_rate=float(row["betfair_implied_win_rate"]),
                edge_magnitude=float(row["edge_magnitude"]),
                sample_size=int(row["runners"]),
                market_efficiency_note=_market_efficiency_note(float(row["edge_magnitude"])),
                model_gap_explanation="The model likely learns recency continuously but not as explicit prep-cycle buckets with distinct market-mispricing behavior.",
                feature_engineering_fix=f"Add DSLR bucket target-encoded feature for {row['dslr_bucket']} and interact it with class/going.",
                backtestable_rule=f"IF DSLR bucket = {row['dslr_bucket']} AND mapped market available THEN actual win rate exceeds Betfair implied by {row['edge_magnitude']:.2f} points.",
                cohort_mask=mask,
                base_df=df,
            )
        )
    return findings


def _first_second_up_findings(base: pd.DataFrame, start_index: int) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    field_rate = safe_rate(base["win"].sum(), len(base)) * 100.0 if len(base) else 0.0
    first_up = base[base["first_up"] & base["market_odds"].notna()].copy()
    second_up = base[base["second_up"] & base["market_odds"].notna()].copy()

    if len(first_up) >= 100:
        summary = (
            first_up.groupby(["canonical_track", "class_bucket"], dropna=False)
            .agg(runners=("win", "count"), winners=("win", "sum"), implied_prob=("implied_prob", "mean"))
            .reset_index()
        )
        summary["winner_win_rate"] = summary.apply(lambda r: safe_rate(r["winners"], r["runners"]) * 100.0, axis=1)
        summary["betfair_implied_win_rate"] = summary["implied_prob"].fillna(0.0) * 100.0
        summary["edge_magnitude"] = summary["winner_win_rate"] - summary["betfair_implied_win_rate"]
        best = summary[(summary["runners"] >= 60) & (summary["edge_magnitude"] >= 2.0)].sort_values(
            ["edge_magnitude", "runners"], ascending=[False, False]
        ).head(3)
        for _, row in best.iterrows():
            mask = first_up["canonical_track"].eq(row["canonical_track"]) & first_up["class_bucket"].eq(row["class_bucket"])
            findings.append(
                _finding_card(
                    finding_id=f"B-{start_index + len(findings):03d}",
                    category="Form Cycle",
                    pattern_description=f"First-up runners at {row['canonical_track']} in {row['class_bucket']} races beat market expectation.",
                    query_used="Filter first-up runners (DSLR >= 60) and compare actual win% vs implied Betfair win% by track and class bucket.",
                    race_types_affected=str(row["class_bucket"]),
                    conditions=f"First-up, {row['canonical_track']}",
                    winner_win_rate=float(row["winner_win_rate"]),
                    field_average_win_rate=field_rate,
                    betfair_implied_win_rate=float(row["betfair_implied_win_rate"]),
                    edge_magnitude=float(row["edge_magnitude"]),
                    sample_size=int(row["runners"]),
                    market_efficiency_note=_market_efficiency_note(float(row["edge_magnitude"])),
                    model_gap_explanation="Fresh-run intent appears venue/class specific rather than a single national first-up effect.",
                    feature_engineering_fix="Add first-up × track × class interaction and trainer-intent placeholder once trainer history is added.",
                    backtestable_rule=f"IF first-up AND track = {row['canonical_track']} AND class bucket = {row['class_bucket']} THEN actual win rate beats implied by {row['edge_magnitude']:.2f} points.",
                    cohort_mask=mask,
                    base_df=first_up,
                )
            )

    if len(second_up) >= 100:
        second_rate = safe_rate(second_up["win"].sum(), len(second_up)) * 100.0
        first_rate = safe_rate(first_up["win"].sum(), len(first_up)) * 100.0 if len(first_up) else 0.0
        second_implied = second_up["implied_prob"].mean() * 100.0 if second_up["implied_prob"].notna().any() else 0.0
        edge = second_rate - second_implied
        if second_rate > first_rate and edge >= 1.0:
            mask = base["second_up"].fillna(False)
            findings.append(
                _finding_card(
                    finding_id=f"B-{start_index + len(findings):03d}",
                    category="Form Cycle",
                    pattern_description="Second-up runners outperform their own first-up baseline and still beat the market.",
                    query_used="Compare second-up vs first-up cohorts from consecutive race history, then benchmark second-up win% against Betfair-implied probability.",
                    race_types_affected="All race types with mapped market coverage",
                    conditions="Second-up after a 60+ day spell",
                    winner_win_rate=second_rate,
                    field_average_win_rate=field_rate,
                    betfair_implied_win_rate=second_implied,
                    edge_magnitude=edge,
                    sample_size=int(len(second_up)),
                    market_efficiency_note=_market_efficiency_note(edge),
                    model_gap_explanation="The model likely captures recency but not the classic second-up fitness bounce as a discrete state transition.",
                    feature_engineering_fix="Add spell_state categorical feature: fresh, second-up, third-up+ with DSLR interaction.",
                    backtestable_rule=f"IF second-up after 60+ day spell THEN win rate = {second_rate:.2f}% vs implied {second_implied:.2f}%.",
                    cohort_mask=mask,
                    base_df=base,
                )
            )
    return findings


def _quick_backup_findings(base: pd.DataFrame, start_index: int) -> List[Dict[str, Any]]:
    df = base[base["quick_backup"] & base["market_odds"].notna()].copy()
    if len(df) < 75:
        return []
    df["prev_placed"] = df["prev_position"].between(2, 5, inclusive="both")
    summary = (
        df.groupby(["prev_placed", "going_group"], dropna=False)
        .agg(runners=("win", "count"), winners=("win", "sum"), implied_prob=("implied_prob", "mean"))
        .reset_index()
    )
    summary["winner_win_rate"] = summary.apply(lambda r: safe_rate(r["winners"], r["runners"]) * 100.0, axis=1)
    summary["betfair_implied_win_rate"] = summary["implied_prob"].fillna(0.0) * 100.0
    summary["edge_magnitude"] = summary["winner_win_rate"] - summary["betfair_implied_win_rate"]
    candidates = summary[(summary["runners"] >= 40) & (summary["edge_magnitude"] >= 2.0)].sort_values(
        ["edge_magnitude", "runners"], ascending=[False, False]
    ).head(4)
    findings = []
    field_rate = safe_rate(base["win"].sum(), len(base)) * 100.0
    for _, row in candidates.iterrows():
        mask = df["prev_placed"].eq(row["prev_placed"]) & df["going_group"].eq(row["going_group"])
        findings.append(
            _finding_card(
                finding_id=f"B-{start_index + len(findings):03d}",
                category="Form Cycle",
                pattern_description="Quick back-up runners show an overlay when their prior run profile and today's surface align.",
                query_used="Filter DSLR 1-6, split by previous placing and current going group, then compare actual vs implied win rates.",
                race_types_affected="All race types with mapped market coverage",
                conditions=f"Quick backup; prev_placed={bool(row['prev_placed'])}; going={row['going_group']}",
                winner_win_rate=float(row["winner_win_rate"]),
                field_average_win_rate=field_rate,
                betfair_implied_win_rate=float(row["betfair_implied_win_rate"]),
                edge_magnitude=float(row["edge_magnitude"]),
                sample_size=int(row["runners"]),
                market_efficiency_note=_market_efficiency_note(float(row["edge_magnitude"])),
                model_gap_explanation="Quick back-ups are often treated as a generic fatigue risk, but the previous-run finishing pattern changes the recovery outlook materially.",
                feature_engineering_fix="Add quick_backup × prev_finish_band × going interaction.",
                backtestable_rule=f"IF DSLR 1-6 AND prev_placed={bool(row['prev_placed'])} AND going={row['going_group']} THEN actual win rate beats implied by {row['edge_magnitude']:.2f} points.",
                cohort_mask=mask,
                base_df=df,
            )
        )
    return findings


def _placement_sequence_findings(base: pd.DataFrame, export_dir: Path, start_index: int) -> List[Dict[str, Any]]:
    df = base[
        base["prev_position"].notna()
        & base["prev2_position"].notna()
        & base["prev3_position"].notna()
        & base["market_odds"].notna()
    ].copy()
    if df.empty:
        return []
    summary = (
        df.groupby("placement_sequence", dropna=False)
        .agg(runners=("win", "count"), winners=("win", "sum"), implied_prob=("implied_prob", "mean"))
        .reset_index()
    )
    summary["winner_win_rate"] = summary.apply(lambda r: safe_rate(r["winners"], r["runners"]) * 100.0, axis=1)
    summary["betfair_implied_win_rate"] = summary["implied_prob"].fillna(0.0) * 100.0
    summary["edge_magnitude"] = summary["winner_win_rate"] - summary["betfair_implied_win_rate"]
    candidates = summary[(summary["runners"] >= 75) & (summary["edge_magnitude"] >= 2.0)].sort_values(
        ["edge_magnitude", "runners"], ascending=[False, False]
    ).head(5)
    csv_safe_write(summary.sort_values(["edge_magnitude", "runners"], ascending=[False, False]), export_dir / "placement_sequence_summary.csv")
    findings = []
    field_rate = safe_rate(df["win"].sum(), len(df)) * 100.0
    for _, row in candidates.iterrows():
        mask = df["placement_sequence"].eq(row["placement_sequence"])
        findings.append(
            _finding_card(
                finding_id=f"B-{start_index + len(findings):03d}",
                category="Form Cycle",
                pattern_description=f"The finishing sequence {row['placement_sequence']} is a stronger win signal than the market prices.",
                query_used="Build three-run placement sequences from race_results_history and compare actual win% vs mean implied win% by sequence.",
                race_types_affected="All race types with mapped market coverage",
                conditions=f"Prior 3-run sequence = {row['placement_sequence']}",
                winner_win_rate=float(row["winner_win_rate"]),
                field_average_win_rate=field_rate,
                betfair_implied_win_rate=float(row["betfair_implied_win_rate"]),
                edge_magnitude=float(row["edge_magnitude"]),
                sample_size=int(row["runners"]),
                market_efficiency_note=_market_efficiency_note(float(row["edge_magnitude"])),
                model_gap_explanation="Form strings are present, but the model may not be explicitly learning higher-order prep sequences as distinct latent states.",
                feature_engineering_fix="Add trailing three-run finish-sequence encoding or sequence embedding.",
                backtestable_rule=f"IF prior finish sequence = {row['placement_sequence']} THEN actual win rate beats implied by {row['edge_magnitude']:.2f} points.",
                cohort_mask=mask,
                base_df=df,
            )
        )
    return findings


def _class_drop_findings(base: pd.DataFrame, start_index: int) -> List[Dict[str, Any]]:
    df = base[base["class_drop_levels"].notna() & base["market_odds"].notna()].copy()
    if df.empty:
        return []
    df["class_drop_bucket"] = np.select(
        [
            df["class_drop_levels"] >= 2,
            df["class_drop_levels"] >= 1,
            df["class_drop_levels"] <= -1,
        ],
        ["drop_2_plus", "drop_1", "rise"],
        default="same",
    )
    summary = (
        df.groupby(["class_drop_bucket", "first_up"], dropna=False)
        .agg(runners=("win", "count"), winners=("win", "sum"), implied_prob=("implied_prob", "mean"))
        .reset_index()
    )
    summary["winner_win_rate"] = summary.apply(lambda r: safe_rate(r["winners"], r["runners"]) * 100.0, axis=1)
    summary["betfair_implied_win_rate"] = summary["implied_prob"].fillna(0.0) * 100.0
    summary["edge_magnitude"] = summary["winner_win_rate"] - summary["betfair_implied_win_rate"]
    candidates = summary[(summary["runners"] >= 60) & (summary["edge_magnitude"] >= 1.5)].sort_values(
        ["edge_magnitude", "runners"], ascending=[False, False]
    ).head(5)
    findings = []
    field_rate = safe_rate(df["win"].sum(), len(df)) * 100.0
    for _, row in candidates.iterrows():
        mask = df["class_drop_bucket"].eq(row["class_drop_bucket"]) & df["first_up"].eq(row["first_up"])
        findings.append(
            _finding_card(
                finding_id=f"B-{start_index + len(findings):03d}",
                category="Connections",
                pattern_description="Class relief, especially when paired with freshness, remains underpriced by the market.",
                query_used="Compare win% vs implied win% for class movement buckets derived from current vs previous class ranks, split by first-up flag.",
                race_types_affected="Handicap / benchmark / black-type transitions where class rank exists",
                conditions=f"class_move={row['class_drop_bucket']}, first_up={bool(row['first_up'])}",
                winner_win_rate=float(row["winner_win_rate"]),
                field_average_win_rate=field_rate,
                betfair_implied_win_rate=float(row["betfair_implied_win_rate"]),
                edge_magnitude=float(row["edge_magnitude"]),
                sample_size=int(row["runners"]),
                market_efficiency_note=_market_efficiency_note(float(row["edge_magnitude"])),
                model_gap_explanation="The model likely sees class rank and recency separately but underweights the combined 'targeted drop-in-grade' intent signal.",
                feature_engineering_fix="Add class_drop_levels and class_drop × spell_state interaction features.",
                backtestable_rule=f"IF class movement bucket = {row['class_drop_bucket']} AND first_up={bool(row['first_up'])} THEN actual win rate beats implied by {row['edge_magnitude']:.2f} points.",
                cohort_mask=mask,
                base_df=df,
            )
        )
    return findings


def _price_bracket_findings(base: pd.DataFrame, export_dir: Path, start_index: int) -> List[Dict[str, Any]]:
    df = base[base["market_odds"].notna()].copy()
    if df.empty:
        return []
    summary = (
        df.groupby("price_bracket", dropna=False)
        .agg(runners=("win", "count"), winners=("win", "sum"), implied_prob=("implied_prob", "mean"))
        .reset_index()
    )
    summary["winner_win_rate"] = summary.apply(lambda r: safe_rate(r["winners"], r["runners"]) * 100.0, axis=1)
    summary["betfair_implied_win_rate"] = summary["implied_prob"].fillna(0.0) * 100.0
    summary["edge_magnitude"] = summary["winner_win_rate"] - summary["betfair_implied_win_rate"]
    ordered = []
    for label, _, _ in PRICE_BRACKETS:
        match = summary[summary["price_bracket"] == label]
        if not match.empty:
            ordered.append(match.iloc[0].to_dict())
    summary_df = pd.DataFrame(ordered if ordered else summary.to_dict("records"))
    csv_safe_write(summary_df, export_dir / "price_bracket_summary.csv")
    candidates = summary_df[(summary_df["runners"] >= 100) & (summary_df["edge_magnitude"].abs() >= 1.5)].sort_values(
        ["edge_magnitude", "runners"], ascending=[False, False]
    ).head(6)
    findings = []
    field_rate = safe_rate(df["win"].sum(), len(df)) * 100.0
    for _, row in candidates.iterrows():
        mask = df["price_bracket"].eq(row["price_bracket"])
        findings.append(
            _finding_card(
                finding_id=f"B-{start_index + len(findings):03d}",
                category="Market",
                pattern_description=f"The Betfair price bracket {row['price_bracket']} is miscalibrated versus actual win frequency.",
                query_used="Bucket runners by Betfair price bracket and compare actual win% vs average implied win% within each bracket.",
                race_types_affected="All mapped race types",
                conditions=f"Betfair price bracket = {row['price_bracket']}",
                winner_win_rate=float(row["winner_win_rate"]),
                field_average_win_rate=field_rate,
                betfair_implied_win_rate=float(row["betfair_implied_win_rate"]),
                edge_magnitude=float(row["edge_magnitude"]),
                sample_size=int(row["runners"]),
                market_efficiency_note=_market_efficiency_note(float(row["edge_magnitude"])),
                model_gap_explanation="If the model is calibrated too closely to market odds, it will inherit these bracket-level biases instead of exploiting them.",
                feature_engineering_fix="Add market price bracket residual calibration feature, track-specific where coverage is strong.",
                backtestable_rule=f"IF Betfair price bracket = {row['price_bracket']} THEN actual win rate differs from implied by {row['edge_magnitude']:.2f} points.",
                cohort_mask=mask,
                base_df=df,
            )
        )
    return findings


def _favourite_vulnerability_findings(base: pd.DataFrame, start_index: int) -> List[Dict[str, Any]]:
    fav = base[(base["market_odds"].notna()) & (base["market_odds"] < 2.5)].copy()
    if len(fav) < 75:
        return []
    summary = (
        fav.groupby(["canonical_track", "going_group"], dropna=False)
        .agg(runners=("win", "count"), winners=("win", "sum"), implied_prob=("implied_prob", "mean"))
        .reset_index()
    )
    summary["winner_win_rate"] = summary.apply(lambda r: safe_rate(r["winners"], r["runners"]) * 100.0, axis=1)
    summary["betfair_implied_win_rate"] = summary["implied_prob"].fillna(0.0) * 100.0
    summary["edge_magnitude"] = summary["winner_win_rate"] - summary["betfair_implied_win_rate"]
    candidates = summary[(summary["runners"] >= 30) & (summary["edge_magnitude"] <= -2.0)].sort_values(
        ["edge_magnitude", "runners"], ascending=[True, False]
    ).head(5)
    findings = []
    field_rate = safe_rate(fav["win"].sum(), len(fav)) * 100.0
    for _, row in candidates.iterrows():
        mask = fav["canonical_track"].eq(row["canonical_track"]) & fav["going_group"].eq(row["going_group"])
        edge = abs(float(row["edge_magnitude"]))
        findings.append(
            _finding_card(
                finding_id=f"B-{start_index + len(findings):03d}",
                category="Market",
                pattern_description=f"Short-priced favourites underperform materially at {row['canonical_track']} on {row['going_group']} ground.",
                query_used="Filter Betfair favourites under 2.50 and compare actual vs implied win rates by track and going group.",
                race_types_affected="Favourite cohorts under 2.50",
                conditions=f"{row['canonical_track']}, {row['going_group']} ground",
                winner_win_rate=float(row["winner_win_rate"]),
                field_average_win_rate=field_rate,
                betfair_implied_win_rate=float(row["betfair_implied_win_rate"]),
                edge_magnitude=float(row["edge_magnitude"]),
                sample_size=int(row["runners"]),
                market_efficiency_note="systematically wrong",
                model_gap_explanation="If STRIDE anchors too close to Betfair in favourite-heavy scenarios, it will miss profitable favourite-opposition setups.",
                feature_engineering_fix="Add favourite vulnerability adjustment by track × going × distance bucket.",
                backtestable_rule=f"IF favourite < 2.50 at {row['canonical_track']} on {row['going_group']} ground THEN actual win rate undershoots implied by {edge:.2f} points.",
                cohort_mask=mask,
                base_df=fav,
            )
        )
    return findings


def _field_size_market_findings(base: pd.DataFrame, start_index: int) -> List[Dict[str, Any]]:
    df = base[
        base["market_odds"].between(6.0, 12.0, inclusive="both")
        & base["field_size_bucket"].isin(["Small (<=8)", "Medium (9-12)", "Large (13-16)", "Max field (17+)"])
    ].copy()
    if len(df) < 100:
        return []
    summary = (
        df.groupby("field_size_bucket", dropna=False)
        .agg(runners=("win", "count"), winners=("win", "sum"), implied_prob=("implied_prob", "mean"))
        .reset_index()
    )
    summary["winner_win_rate"] = summary.apply(lambda r: safe_rate(r["winners"], r["runners"]) * 100.0, axis=1)
    summary["betfair_implied_win_rate"] = summary["implied_prob"].fillna(0.0) * 100.0
    summary["edge_magnitude"] = summary["winner_win_rate"] - summary["betfair_implied_win_rate"]
    candidates = summary[(summary["runners"] >= 60) & (summary["edge_magnitude"] >= 1.5)].sort_values(
        ["edge_magnitude", "runners"], ascending=[False, False]
    ).head(4)
    findings = []
    field_rate = safe_rate(df["win"].sum(), len(df)) * 100.0
    for _, row in candidates.iterrows():
        mask = df["field_size_bucket"].eq(row["field_size_bucket"])
        findings.append(
            _finding_card(
                finding_id=f"B-{start_index + len(findings):03d}",
                category="Market",
                pattern_description="Mid-range odds runners in larger fields are being undervalued by the market.",
                query_used="Filter 6.0-12.0 runners and compare actual vs implied win rates across field-size buckets.",
                race_types_affected="All mapped race types with 6.0-12.0 Betfair prices",
                conditions=f"Field size bucket = {row['field_size_bucket']}",
                winner_win_rate=float(row["winner_win_rate"]),
                field_average_win_rate=field_rate,
                betfair_implied_win_rate=float(row["betfair_implied_win_rate"]),
                edge_magnitude=float(row["edge_magnitude"]),
                sample_size=int(row["runners"]),
                market_efficiency_note=_market_efficiency_note(float(row["edge_magnitude"])),
                model_gap_explanation="Large-field probability dilution can create mid-range overlays that disappear when the model leans too hard on the market prior.",
                feature_engineering_fix="Add price bracket × field size interaction with direct residual calibration.",
                backtestable_rule=f"IF price is 6.0-12.0 AND field size bucket = {row['field_size_bucket']} THEN actual win rate beats implied by {row['edge_magnitude']:.2f} points.",
                cohort_mask=mask,
                base_df=df,
            )
        )
    return findings


def _wet_jockey_findings(base: pd.DataFrame, start_index: int) -> List[Dict[str, Any]]:
    df = base[base["jockey"].notna() & base["market_odds"].notna() & base["implied_prob"].notna()].copy()
    if df.empty:
        return []
    summary = (
        df.groupby(["jockey", "wet_track"], dropna=False)["win"]
        .agg(["count", "sum"])
        .reset_index()
        .rename(columns={"count": "rides", "sum": "wins"})
    )
    pivot = summary.pivot_table(index="jockey", columns="wet_track", values=["rides", "wins"], fill_value=0)
    pivot.columns = [f"{a}_{b}" for a, b in pivot.columns]
    pivot = pivot.reset_index()
    for col in ["rides_True", "wins_True", "rides_False", "wins_False"]:
        if col not in pivot.columns:
            pivot[col] = 0
    pivot["wet_win_rate"] = np.where(pivot["rides_True"] > 0, pivot["wins_True"] / pivot["rides_True"] * 100.0, np.nan)
    pivot["dry_win_rate"] = np.where(pivot["rides_False"] > 0, pivot["wins_False"] / pivot["rides_False"] * 100.0, np.nan)
    pivot["wet_delta"] = pivot["wet_win_rate"] - pivot["dry_win_rate"]
    candidates = pivot[(pivot["rides_True"] >= 30) & (pivot["rides_False"] >= 30) & (pivot["wet_delta"] >= 4.0)].sort_values(
        ["wet_delta", "rides_True"], ascending=[False, False]
    ).head(5)
    findings = []
    wet_df = df[df["wet_track"]].copy()
    field_rate = safe_rate(wet_df["win"].sum(), len(wet_df)) * 100.0 if len(wet_df) else 0.0
    for _, row in candidates.iterrows():
        jockey_df = wet_df[wet_df["jockey"] == row["jockey"]].copy()
        if len(jockey_df) < 20:
            continue
        implied = jockey_df["implied_prob"].mean() * 100.0 if jockey_df["implied_prob"].notna().any() else 0.0
        actual = safe_rate(jockey_df["win"].sum(), len(jockey_df)) * 100.0
        edge = actual - implied
        if edge < 1.0:
            continue
        mask = wet_df["jockey"].eq(row["jockey"])
        findings.append(
            _finding_card(
                finding_id=f"B-{start_index + len(findings):03d}",
                category="Jockey",
                pattern_description=f"{row['jockey']} materially outperforms their own dry-track strike rate on wet going.",
                query_used="Compute jockey wet-track vs dry-track win rates from race_results_history, then compare wet actual win% to Betfair-implied wet win%.",
                race_types_affected="Wet-track races",
                conditions=f"Wet track rides for {row['jockey']}",
                winner_win_rate=actual,
                field_average_win_rate=field_rate,
                betfair_implied_win_rate=implied,
                edge_magnitude=edge,
                sample_size=int(len(jockey_df)),
                market_efficiency_note=_market_efficiency_note(edge),
                model_gap_explanation="The model likely captures jockey quality generally, but not wet-track-specialist deltas relative to each jockey's baseline.",
                feature_engineering_fix="Add jockey wet-track residual performance feature using rolling wet vs dry delta.",
                backtestable_rule=f"IF jockey = {row['jockey']} AND going is Soft/Heavy THEN actual win rate beats implied by {edge:.2f} points.",
                cohort_mask=mask,
                base_df=wet_df,
            )
        )
    return findings


def _market_movement_findings(
    base: pd.DataFrame,
    market_windows: pd.DataFrame,
    export_dir: Path,
    start_index: int,
) -> List[Dict[str, Any]]:
    if market_windows.empty:
        return []
    df = base.merge(market_windows, how="inner", on="join_key")
    if df.empty:
        return []
    summary = (
        df.groupby("movement_class", dropna=False)
        .agg(runners=("win", "count"), winners=("win", "sum"), implied_prob=("implied_prob", "mean"))
        .reset_index()
    )
    summary["winner_win_rate"] = summary.apply(lambda r: safe_rate(r["winners"], r["runners"]) * 100.0, axis=1)
    summary["betfair_implied_win_rate"] = summary["implied_prob"].fillna(0.0) * 100.0
    summary["edge_magnitude"] = summary["winner_win_rate"] - summary["betfair_implied_win_rate"]
    csv_safe_write(summary.sort_values(["edge_magnitude", "runners"], ascending=[False, False]), export_dir / "market_movement_summary.csv")
    candidates = summary[(summary["runners"] >= 100) & (summary["edge_magnitude"].abs() >= 1.0)].sort_values(
        ["edge_magnitude", "runners"], ascending=[False, False]
    ).head(4)
    findings = []
    field_rate = safe_rate(df["win"].sum(), len(df)) * 100.0
    for _, row in candidates.iterrows():
        mask = df["movement_class"].eq(row["movement_class"])
        findings.append(
            _finding_card(
                finding_id=f"B-{start_index + len(findings):03d}",
                category="Market",
                pattern_description=f"Betfair {row['movement_class']} runners from 30m to 2m have a measurable calibration gap.",
                query_used="Use raw market_data filtered to eligible mapped market_ids, derive 30m/10m/2m median prices, classify steam/drift/hold, then compare actual vs implied win%.",
                race_types_affected="Mapped races with raw market snapshots",
                conditions=f"movement_class = {row['movement_class']}",
                winner_win_rate=float(row["winner_win_rate"]),
                field_average_win_rate=field_rate,
                betfair_implied_win_rate=float(row["betfair_implied_win_rate"]),
                edge_magnitude=float(row["edge_magnitude"]),
                sample_size=int(row["runners"]),
                market_efficiency_note=_market_efficiency_note(float(row["edge_magnitude"])),
                model_gap_explanation="Price velocity contains crowd information beyond static starting price, but only if STRIDE explicitly measures it.",
                feature_engineering_fix="Add steam/drift categorical feature and 30m→2m price-change percentage.",
                backtestable_rule=f"IF movement_class = {row['movement_class']} from 30m to 2m THEN actual win rate differs from implied by {row['edge_magnitude']:.2f} points.",
                cohort_mask=mask,
                base_df=df,
            )
        )
    return findings


def run_agent2_analysis(
    db: ResearchDB,
    coverage: CoverageArtifacts,
    *,
    date_from: str,
    date_to: str,
    export_dir: Path,
) -> Dict[str, Any]:
    agent_dir = export_dir / "agent2"
    history = _prepare_rrh_history(coverage)
    base = _merge_model_view(history, coverage)
    market_windows = _fetch_market_window_prices(db, coverage, date_from=date_from, date_to=date_to)

    findings: List[Dict[str, Any]] = []
    findings.extend(_dslr_findings(base, agent_dir))
    findings.extend(_first_second_up_findings(base, len(findings) + 1))
    findings.extend(_quick_backup_findings(base, len(findings) + 1))
    findings.extend(_placement_sequence_findings(base, agent_dir, len(findings) + 1))
    findings.extend(_class_drop_findings(base, len(findings) + 1))
    findings.extend(_price_bracket_findings(base, agent_dir, len(findings) + 1))
    findings.extend(_favourite_vulnerability_findings(base, len(findings) + 1))
    findings.extend(_field_size_market_findings(base, len(findings) + 1))
    findings.extend(_wet_jockey_findings(base, len(findings) + 1))
    findings.extend(_market_movement_findings(base, market_windows, agent_dir, len(findings) + 1))

    findings = sorted(findings, key=lambda item: (item["priority"], -item["edge_magnitude"], -item["sample_size"]))
    for idx, finding in enumerate(findings, start=1):
        finding["finding_id"] = f"B-{idx:03d}"

    capability_notes = list(coverage.summary.get("capability_notes", []))
    capability_notes.append(
        "Trainer-level first-up, trainer-jockey synergy, apprentice-claim, and stable-intent findings are limited because trainer and claim fields are not present in race_results_history/training_view_v2 at 18-month scale."
    )
    model_prob_rows = int(base["model_prob"].notna().sum())
    expected_value_rows = int(base["expected_value"].notna().sum())
    if model_prob_rows < max(100, int(len(base) * 0.10)):
        capability_notes.append(
            f"training_view_v2 model-probability coverage is sparse in this scope ({model_prob_rows}/{len(base)} rows), so 'missed winner' and EV-based findings are diagnostic only until the view is backfilled."
        )
    if expected_value_rows < max(100, int(len(base) * 0.10)):
        capability_notes.append(
            f"training_view_v2 expected_value coverage is sparse in this scope ({expected_value_rows}/{len(base)} rows), so model-underweight tagging is incomplete."
        )
    if coverage.eligible_market_cohorts.empty:
        capability_notes.append(
            "No track-month cohort passed the 85% Betfair mapping gate. Market findings are downgraded and mapping should be expanded via server/python/build_betfair_mapping.py before production use."
        )
    elif market_windows.empty:
        capability_notes.append(
            "Mapped runner coverage passed for at least one cohort, but raw market window snapshots were insufficient for a reliable steam/drift study in this run."
        )

    summary = {
        "inventory": {
            "runner_rows": int(len(base)),
            "winner_rows": int(base["win"].sum()),
            "missed_winners": int(base["missed_winner"].sum()),
            "mapped_market_rows": int(base["market_odds"].notna().sum()),
            "raw_market_window_rows": int(len(market_windows)),
        },
        "coverage_gate": {
            "eligible_track_months": int(len(coverage.eligible_market_cohorts)),
            "total_track_months": int(
                len(
                    coverage.rrh_df.assign(month=pd.to_datetime(coverage.rrh_df["race_date"], errors="coerce").dt.to_period("M").astype(str))
                    .groupby(["canonical_track", "month"], dropna=False)
                    .size()
                )
            )
            if "canonical_track" in coverage.rrh_df.columns
            else None,
        },
        "capability_notes": capability_notes,
    }

    json_write({"summary": summary, "findings": findings}, agent_dir / "agent2_findings.json")
    markdown_write(
        markdown_from_findings("Agent 2 Findings", findings, notes=capability_notes),
        agent_dir / "agent2_findings.md",
    )
    csv_safe_write(base.head(5000), agent_dir / "agent2_sample_base_rows.csv")

    return {"summary": summary, "findings": findings, "base_df": base}
