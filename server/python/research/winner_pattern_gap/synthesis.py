from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd

from .config import METRO_TRACK_ORDER
from .coverage import CoverageArtifacts
from .db import csv_safe_write, json_write, markdown_write
from .utils import (
    compute_implied_prob,
    feature_markdown,
    make_join_key,
    markdown_from_findings,
    normalize_name_column,
    safe_rate,
)


CONDITION_TOKENS = {
    "wet",
    "soft",
    "heavy",
    "good",
    "sprint",
    "middle",
    "staying",
    "first-up",
    "second-up",
    "quick",
    "backup",
    "leader",
    "off-pace",
    "closer",
    "large",
    "field",
    "favourite",
    "class",
    "drop",
}


def _extract_tracks(*values: str) -> List[str]:
    text = " ".join([v for v in values if v]).lower()
    matches = [track for track in METRO_TRACK_ORDER if track.lower() in text]
    return sorted(set(matches))


def _extract_condition_tokens(*values: str) -> List[str]:
    text = " ".join([v for v in values if v]).lower()
    tokens = set(re.findall(r"[a-z0-9\-+]+", text))
    return sorted(tokens & CONDITION_TOKENS)


def _cross_match_findings(agent1_findings: List[Dict[str, Any]], agent2_findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for a in agent1_findings:
        a_tracks = set(_extract_tracks(a.get("tracks_affected", ""), a.get("conditions", ""), a.get("pattern_description", "")))
        a_conditions = set(_extract_condition_tokens(a.get("conditions", ""), a.get("pattern_description", ""), a.get("category", "")))
        for b in agent2_findings:
            b_tracks = set(_extract_tracks(b.get("race_types_affected", ""), b.get("conditions", ""), b.get("pattern_description", "")))
            b_conditions = set(_extract_condition_tokens(b.get("conditions", ""), b.get("pattern_description", ""), b.get("category", "")))
            track_overlap = sorted(a_tracks & b_tracks)
            condition_overlap = sorted(a_conditions & b_conditions)
            score = len(track_overlap) * 3 + len(condition_overlap) * 2
            if score == 0 and ("Wet" in a.get("conditions", "") and "wet" in b_conditions):
                score += 2
            if score == 0:
                continue
            matches.append(
                {
                    "agent1_finding_id": a["finding_id"],
                    "agent2_finding_id": b["finding_id"],
                    "track_overlap": track_overlap,
                    "condition_overlap": condition_overlap,
                    "score": score,
                    "combined_priority_score": float(a.get("priority", 5)) + float(b.get("priority", 5)),
                    "agent1_pattern": a.get("pattern_description"),
                    "agent2_pattern": b.get("pattern_description"),
                }
            )
    if not matches:
        return []
    matches_df = pd.DataFrame(matches).sort_values(["score", "combined_priority_score"], ascending=[False, True])
    return matches_df.head(15).to_dict("records")


def _sectional_market_underreaction_test(coverage: CoverageArtifacts, agent2_base: pd.DataFrame) -> Dict[str, Any]:
    rrh = coverage.rrh_df.copy()
    st = coverage.sectional_df.copy()
    rrh["join_key"] = make_join_key(rrh["track"], rrh["race_date"], rrh["race_number"], rrh["horse_name"])
    st["join_key"] = make_join_key(st["track"], st["race_date"], st["race_number"], st["horse_name"])

    primary = rrh.merge(
        st[["race_results_history_id", "join_key", "last_200m_time"]],
        how="left",
        left_on="id",
        right_on="race_results_history_id",
        suffixes=("", "_st"),
    )
    fallback = (
        st[["join_key", "last_200m_time"]]
        .dropna(subset=["join_key"])
        .drop_duplicates(subset=["join_key"], keep="first")
        .set_index("join_key")
    )
    missing = primary["last_200m_time"].isna()
    if missing.any() and not fallback.empty:
        primary.loc[missing, "last_200m_time"] = fallback.reindex(primary.loc[missing, "join_key"])["last_200m_time"].values

    base = primary.merge(
        agent2_base[
            [
                "join_key",
                "horse_key",
                "race_date_dt",
                "position",
                "win",
                "market_odds",
                "implied_prob",
                "model_prob",
                "expected_value",
            ]
        ].drop_duplicates(subset=["join_key"], keep="last"),
        how="inner",
        on="join_key",
        suffixes=("", "_agent2"),
    )
    if base.empty:
        return {
            "rule_name": "sectional_market_underreaction",
            "sample_size": 0,
            "actual_win_rate": 0.0,
            "betfair_implied_win_rate": 0.0,
            "model_probability_mean": 0.0,
            "edge_vs_market": 0.0,
            "notes": ["No joined sectional + market/model rows were available for the underreaction test."],
        }

    base = base.sort_values(["horse_key", "race_date_dt", "race_number", "id"]).reset_index(drop=True)
    base["prev_last_200m_time"] = base.groupby("horse_key", dropna=False)["last_200m_time"].shift(1)
    base["prev_position"] = base.groupby("horse_key", dropna=False)["position"].shift(1)
    base["prior_best_before_prev"] = (
        base.groupby("horse_key", dropna=False)["last_200m_time"]
        .transform(lambda s: s.expanding().min().shift(2))
    )
    base["prev_pb_close"] = (
        base["prev_last_200m_time"].notna()
        & base["prior_best_before_prev"].notna()
        & (base["prev_last_200m_time"] <= base["prior_best_before_prev"] + 1e-9)
    )

    cohort = base[
        base["prev_pb_close"]
        & base["prev_position"].between(3, 5, inclusive="both")
        & base["market_odds"].between(6.0, 12.0, inclusive="both")
        & base["implied_prob"].notna()
    ].copy()
    if cohort.empty:
        return {
            "rule_name": "sectional_market_underreaction",
            "sample_size": 0,
            "actual_win_rate": 0.0,
            "betfair_implied_win_rate": 0.0,
            "model_probability_mean": 0.0,
            "edge_vs_market": 0.0,
            "notes": ["No runners met the prior-PB closing split + 3rd-5th last-start + 6.0-12.0 price cohort in this scope."],
        }

    actual = safe_rate(cohort["win"].sum(), len(cohort)) * 100.0
    implied = cohort["implied_prob"].mean() * 100.0
    model_prob = cohort["model_prob"].dropna().mean() * 100.0 if cohort["model_prob"].notna().any() else 0.0
    missed = int(cohort["win"].sum() - ((cohort["expected_value"] > 0).fillna(False) & cohort["win"]).sum())
    return {
        "rule_name": "sectional_market_underreaction",
        "sample_size": int(len(cohort)),
        "actual_win_rate": round(actual, 2),
        "betfair_implied_win_rate": round(implied, 2),
        "model_probability_mean": round(model_prob, 2),
        "edge_vs_market": round(actual - implied, 2),
        "missed_winner_count": missed,
        "conditions": "Prior run = personal-best last-200m close, prior finish 3rd-5th, current Betfair odds 6.0-12.0",
        "notes": [
            "This is the highest-value synthesis test because it combines latent sectional fitness with explicit market underreaction.",
        ],
    }


def _combined_priority_table(
    agent1_findings: List[Dict[str, Any]],
    agent2_findings: List[Dict[str, Any]],
    underreaction_test: Dict[str, Any],
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for finding in agent1_findings:
        rows.append(
            {
                "source": "agent1",
                "finding_id": finding["finding_id"],
                "category": finding.get("category"),
                "pattern_description": finding.get("pattern_description"),
                "edge_magnitude": finding.get("edge_magnitude", 0.0),
                "sample_size": finding.get("sample_size", 0),
                "priority": finding.get("priority", 5),
                "temporal_stability_score": finding.get("temporal_stability_score", 0.0),
            }
        )
    for finding in agent2_findings:
        rows.append(
            {
                "source": "agent2",
                "finding_id": finding["finding_id"],
                "category": finding.get("category"),
                "pattern_description": finding.get("pattern_description"),
                "edge_magnitude": finding.get("edge_magnitude", 0.0),
                "sample_size": finding.get("sample_size", 0),
                "priority": finding.get("priority", 5),
                "temporal_stability_score": finding.get("temporal_stability_score", 0.0),
            }
        )
    if underreaction_test.get("sample_size", 0) > 0:
        rows.append(
            {
                "source": "synthesis",
                "finding_id": "S-001",
                "category": "Sectional x Market",
                "pattern_description": underreaction_test["conditions"],
                "edge_magnitude": underreaction_test["edge_vs_market"],
                "sample_size": underreaction_test["sample_size"],
                "priority": 1 if underreaction_test["edge_vs_market"] >= 3 else 2,
                "temporal_stability_score": np.nan,
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values(
        ["priority", "edge_magnitude", "sample_size", "temporal_stability_score"],
        ascending=[True, False, False, False],
        na_position="last",
    ).reset_index(drop=True)


def _feature_roadmap(
    priority_table: pd.DataFrame,
    cross_matches: List[Dict[str, Any]],
    underreaction_test: Dict[str, Any],
) -> List[Dict[str, Any]]:
    features: List[Dict[str, Any]] = []
    if underreaction_test.get("sample_size", 0) > 0:
        features.append(
            {
                "feature_name": "prior_pb_close_3to5_market_underreaction",
                "description": "Flag for runners whose prior start was a personal-best last-200m close, prior finish was 3rd-5th, and current market price sits in the 6.0-12.0 overlay band.",
                "derived_from": "race_results_history + sectional_times.last_200m_time + training_view_v2.market_odds/model_probability",
                "expected_impact": "Directly addresses the sectional × market underreaction synthesis rule.",
                "implementation_complexity": "Medium",
                "priority_rank": 1,
            }
        )
    rank = len(features) + 1
    for _, row in priority_table.head(8).iterrows():
        feature_name = (
            str(row["category"]).lower().replace(" ", "_").replace("/", "_")
            + "_"
            + str(row["finding_id"]).lower().replace("-", "_")
        )
        description = str(row["pattern_description"])
        if any(feature["feature_name"] == feature_name for feature in features):
            continue
        features.append(
            {
                "feature_name": feature_name,
                "description": description,
                "derived_from": "See corresponding finding card query path.",
                "expected_impact": f"Captures {row['finding_id']} ({row['source']}) in the next STRIDE model sprint.",
                "implementation_complexity": "Low" if row["source"] != "synthesis" else "Medium",
                "priority_rank": rank,
            }
        )
        rank += 1
    for match in cross_matches[:3]:
        features.append(
            {
                "feature_name": f"cross_signal_{match['agent1_finding_id'].lower()}_{match['agent2_finding_id'].lower()}",
                "description": f"Composite feature combining {match['agent1_finding_id']} and {match['agent2_finding_id']} where track/condition overlap is strongest.",
                "derived_from": "Agent 1 + Agent 2 source tables for the matched cohorts.",
                "expected_impact": "Elevates multi-angle patterns that the model is likely missing simultaneously.",
                "implementation_complexity": "Medium",
                "priority_rank": rank,
            }
        )
        rank += 1
    return features


def run_synthesis(
    coverage: CoverageArtifacts,
    agent1_result: Dict[str, Any],
    agent2_result: Dict[str, Any],
    *,
    export_dir: Path,
) -> Dict[str, Any]:
    synthesis_dir = export_dir / "synthesis"
    agent1_findings = agent1_result.get("findings", [])
    agent2_findings = agent2_result.get("findings", [])

    cross_matches = _cross_match_findings(agent1_findings, agent2_findings)
    underreaction_test = _sectional_market_underreaction_test(coverage, agent2_result.get("base_df", pd.DataFrame()))
    priority_table = _combined_priority_table(agent1_findings, agent2_findings, underreaction_test)
    features = _feature_roadmap(priority_table, cross_matches, underreaction_test)

    if not priority_table.empty:
        csv_safe_write(priority_table, synthesis_dir / "combined_priority_table.csv")
    json_write(
        {
            "cross_matches": cross_matches,
            "sectional_market_underreaction": underreaction_test,
            "feature_roadmap": features,
        },
        synthesis_dir / "synthesis_report.json",
    )
    json_write(features, synthesis_dir / "feature_roadmap.json")
    csv_safe_write(pd.DataFrame(features), synthesis_dir / "feature_roadmap.csv")

    report_lines = [
        "# Synthesis Report",
        "",
        "## Cross-Agent Pattern Matches",
    ]
    if cross_matches:
        for match in cross_matches[:10]:
            report_lines.append(
                f"- `{match['agent1_finding_id']}` + `{match['agent2_finding_id']}` | score={match['score']} | tracks={', '.join(match['track_overlap']) or 'none'} | conditions={', '.join(match['condition_overlap']) or 'none'}"
            )
    else:
        report_lines.append("- No strong cross-agent overlap was detected in this run.")
    report_lines.extend(
        [
            "",
            "## Sectional x Market Underreaction Test",
            f"- Sample size: {underreaction_test.get('sample_size', 0)}",
            f"- Actual win rate: {underreaction_test.get('actual_win_rate', 0.0)}%",
            f"- Betfair implied win rate: {underreaction_test.get('betfair_implied_win_rate', 0.0)}%",
            f"- Mean model probability: {underreaction_test.get('model_probability_mean', 0.0)}%",
            f"- Edge vs market: {underreaction_test.get('edge_vs_market', 0.0)}pp",
            "",
        ]
    )
    if not priority_table.empty:
        report_lines.append("## Combined Priority Table")
        for _, row in priority_table.head(15).iterrows():
            report_lines.append(
                f"- `{row['finding_id']}` ({row['source']}) | priority={row['priority']} | edge={row['edge_magnitude']} | sample={row['sample_size']} | {row['pattern_description']}"
            )
        report_lines.append("")
    markdown_write("\n".join(report_lines) + "\n", synthesis_dir / "synthesis_report.md")
    markdown_write(feature_markdown(features), synthesis_dir / "feature_roadmap.md")

    return {
        "cross_matches": cross_matches,
        "sectional_market_underreaction": underreaction_test,
        "priority_table": priority_table.to_dict("records"),
        "feature_roadmap": features,
    }
