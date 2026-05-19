from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from .config import canonical_track, normalize_name
from .db import ResearchDB, csv_safe_write, json_write, markdown_write
from .utils import canonicalize_track_column, make_join_key, prep_race_date_column


@dataclass
class CoverageArtifacts:
    summary: Dict[str, Any]
    rrh_df: pd.DataFrame
    sectional_df: pd.DataFrame
    betfair_map_df: pd.DataFrame
    tv2_df: pd.DataFrame
    usable_market_df: pd.DataFrame
    eligible_market_cohorts: pd.DataFrame


def _load_base_tables(db: ResearchDB, date_from: str, date_to: str) -> Dict[str, pd.DataFrame]:
    rrh = db.fetch_df(
        "coverage_race_results_history",
        """
        SELECT id, horse_id, horse_name, track, race_date, race_number, distance_m, race_class, class_level,
               going, position, barrier, sp_odds, field_size, jockey
        FROM race_results_history
        WHERE race_date::date BETWEEN %(date_from)s AND %(date_to)s
        """,
        {"date_from": date_from, "date_to": date_to},
    )
    st = db.fetch_df(
        "coverage_sectional_times",
        """
        SELECT race_results_history_id, track, race_date, race_number, horse_name, track_config,
               last_200m_time, last_400m_time, last_600m_time, last_800m_time, splits_json
        FROM sectional_times
        WHERE race_date::date BETWEEN %(date_from)s AND %(date_to)s
        """,
        {"date_from": date_from, "date_to": date_to},
    )
    bmap = db.fetch_df(
        "coverage_betfair_map",
        """
        SELECT market_id, selection_id, meeting_date, track, race_number, horse_name, market_time
        FROM betfair_market_runner_map
        WHERE meeting_date BETWEEN %(date_from)s::date AND %(date_to)s::date
        """,
        {"date_from": date_from, "date_to": date_to},
    )
    tv2 = db.fetch_df(
        "coverage_training_view_v2",
        """
        SELECT horse_name, track, race_date, race_number, market_odds, model_probability,
               predicted_win_prob, expected_value, is_winner, prior_z_200m, prior_z_400m,
               prior_z_600m, prior_z_800m, prior_sectional_date, running_style, confidence
        FROM training_view_v2
        WHERE race_date::date BETWEEN %(date_from)s AND %(date_to)s
        """,
        {"date_from": date_from, "date_to": date_to},
    )
    usable_market = pd.DataFrame()
    try:
        usable_market = db.fetch_df(
            "coverage_betfair_labeled_training_view",
            """
            SELECT meeting_date, track, race_number, horse_name, market_odds, minutes_to_jump, is_t10_window
            FROM betfair_labeled_training_view
            WHERE meeting_date BETWEEN %(date_from)s::date AND %(date_to)s::date
            """,
            {"date_from": date_from, "date_to": date_to},
            note="Filtered labeled Betfair snapshot coverage only; used for usable market windows.",
        )
    except Exception as exc:
        db.query_logger.log(
            name="coverage_betfair_labeled_training_view_failed",
            query="SELECT ... FROM betfair_labeled_training_view WHERE meeting_date BETWEEN %(date_from)s AND %(date_to)s",
            params={"date_from": date_from, "date_to": date_to},
            row_count=0,
            duration_ms=0.0,
            note=f"usable market coverage query failed: {exc}",
        )
        usable_market = pd.DataFrame(
            columns=["meeting_date", "track", "race_number", "horse_name", "market_odds", "minutes_to_jump", "is_t10_window"]
        )
    return {
        "rrh": rrh,
        "sectional": st,
        "bmap": bmap,
        "tv2": tv2,
        "usable_market": usable_market,
    }


def _prepare_coverage_frames(frames: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    rrh = frames["rrh"].copy()
    rrh["canonical_track"] = canonicalize_track_column(rrh["track"])
    rrh["race_date_dt"] = pd.to_datetime(rrh["race_date"], errors="coerce")
    rrh["month"] = rrh["race_date_dt"].dt.to_period("M").astype(str)
    rrh["join_key"] = make_join_key(rrh["track"], rrh["race_date"], rrh["race_number"], rrh["horse_name"])

    st = frames["sectional"].copy()
    st["canonical_track"] = canonicalize_track_column(st["track"])
    st["race_date_dt"] = pd.to_datetime(st["race_date"], errors="coerce")
    st["month"] = st["race_date_dt"].dt.to_period("M").astype(str)
    st["join_key"] = make_join_key(st["track"], st["race_date"], st["race_number"], st["horse_name"])

    bmap = frames["bmap"].copy()
    bmap["canonical_track"] = canonicalize_track_column(bmap["track"])
    bmap["meeting_date_dt"] = pd.to_datetime(bmap["meeting_date"], errors="coerce")
    bmap["month"] = bmap["meeting_date_dt"].dt.to_period("M").astype(str)
    bmap["join_key"] = make_join_key(bmap["track"], bmap["meeting_date"], bmap["race_number"], bmap["horse_name"])
    bmap["runner_key"] = bmap["market_id"].astype(str) + "|" + bmap["selection_id"].astype(str)

    tv2 = frames["tv2"].copy()
    tv2["canonical_track"] = canonicalize_track_column(tv2["track"])
    tv2["race_date_dt"] = pd.to_datetime(tv2["race_date"], errors="coerce")
    tv2["month"] = tv2["race_date_dt"].dt.to_period("M").astype(str)
    tv2["join_key"] = make_join_key(tv2["track"], tv2["race_date"], tv2["race_number"], tv2["horse_name"])

    usable = frames["usable_market"].copy()
    if not usable.empty:
        usable["canonical_track"] = canonicalize_track_column(usable["track"])
        usable["meeting_date_dt"] = pd.to_datetime(usable["meeting_date"], errors="coerce")
        usable["month"] = usable["meeting_date_dt"].dt.to_period("M").astype(str)
        usable["join_key"] = make_join_key(usable["track"], usable["meeting_date"], usable["race_number"], usable["horse_name"])
        usable["usable_snapshot"] = (
            usable["market_odds"].notna()
            & (
                (usable["is_t10_window"].fillna(0).astype(int) == 1)
                | usable["minutes_to_jump"].between(0, 2.5, inclusive="both")
            )
        )
    else:
        usable["usable_snapshot"] = pd.Series(dtype=bool)

    return {
        "rrh": rrh,
        "sectional": st,
        "bmap": bmap,
        "tv2": tv2,
        "usable_market": usable,
    }


def _apply_scope(df: pd.DataFrame, tracks: List[str], column: str = "canonical_track") -> pd.DataFrame:
    if not tracks:
        return df
    return df[df[column].isin(tracks)].copy()


def run_coverage_audit(
    db: ResearchDB,
    *,
    date_from: str,
    date_to: str,
    tracks: List[str],
    export_dir: Path,
) -> CoverageArtifacts:
    raw_frames = _load_base_tables(db, date_from, date_to)
    frames = _prepare_coverage_frames(raw_frames)

    rrh = _apply_scope(frames["rrh"], tracks)
    sectional = _apply_scope(frames["sectional"], tracks)
    bmap = _apply_scope(frames["bmap"], tracks)
    tv2 = _apply_scope(frames["tv2"], tracks)
    usable_market = _apply_scope(frames["usable_market"], tracks)

    rrh_month = rrh.groupby(["canonical_track", "month"], dropna=False).size().reset_index(name="results_rows")
    st_month = sectional.groupby(["canonical_track", "month"], dropna=False).size().reset_index(name="sectional_rows")
    bmap_month = bmap.groupby(["canonical_track", "month"], dropna=False).size().reset_index(name="mapped_runners")

    sectional_primary = rrh.merge(
        sectional[["race_results_history_id"]].dropna().rename(columns={"race_results_history_id": "id"}),
        how="left",
        on="id",
        indicator=True,
    )
    sectional_unmatched = sectional_primary[sectional_primary["_merge"] == "left_only"].drop(columns=["_merge"])
    sectional_fallback_keys = set(sectional["join_key"].dropna().astype(str))
    sectional_primary["sectional_match"] = sectional_primary["_merge"] == "both"
    sectional_primary.loc[sectional_primary["_merge"] == "left_only", "sectional_match"] = sectional_unmatched["join_key"].astype(str).isin(sectional_fallback_keys).values
    sectional_match_month = (
        sectional_primary.groupby(["canonical_track", "month"], dropna=False)["sectional_match"]
        .agg(total_results="count", matched_results="sum")
        .reset_index()
    )
    sectional_match_month["sectional_match_pct"] = (
        sectional_match_month["matched_results"] / sectional_match_month["total_results"] * 100.0
    ).round(2)

    bmap_keys = set(bmap["join_key"].dropna().astype(str))
    rrh["betfair_mapping_match"] = rrh["join_key"].astype(str).isin(bmap_keys)
    bmap_match_month = (
        rrh.groupby(["canonical_track", "month"], dropna=False)["betfair_mapping_match"]
        .agg(total_results="count", mapped_results="sum")
        .reset_index()
    )
    bmap_match_month["betfair_mapping_pct"] = (
        bmap_match_month["mapped_results"] / bmap_match_month["total_results"] * 100.0
    ).round(2)

    usable_market_month = pd.DataFrame(columns=["canonical_track", "month", "mapped_runners", "usable_market_runners", "usable_market_pct"])
    if not usable_market.empty:
        usable_market_rollup = (
            usable_market.groupby(["canonical_track", "month", "join_key"], dropna=False)["usable_snapshot"]
            .max()
            .reset_index()
        )
        usable_market_month = (
            usable_market_rollup.groupby(["canonical_track", "month"], dropna=False)["usable_snapshot"]
            .agg(mapped_runners="count", usable_market_runners="sum")
            .reset_index()
        )
        usable_market_month["usable_market_pct"] = (
            usable_market_month["usable_market_runners"] / usable_market_month["mapped_runners"] * 100.0
        ).round(2)

    coverage_month = rrh_month.merge(st_month, how="outer", on=["canonical_track", "month"])
    coverage_month = coverage_month.merge(bmap_month, how="outer", on=["canonical_track", "month"])
    coverage_month = coverage_month.merge(
        sectional_match_month[["canonical_track", "month", "sectional_match_pct"]],
        how="left",
        on=["canonical_track", "month"],
    )
    coverage_month = coverage_month.merge(
        bmap_match_month[["canonical_track", "month", "betfair_mapping_pct"]],
        how="left",
        on=["canonical_track", "month"],
    )
    if not usable_market_month.empty:
        coverage_month = coverage_month.merge(
            usable_market_month[["canonical_track", "month", "usable_market_pct"]],
            how="left",
            on=["canonical_track", "month"],
        )

    coverage_month = coverage_month.fillna(
        {
            "results_rows": 0,
            "sectional_rows": 0,
            "mapped_runners": 0,
            "sectional_match_pct": 0.0,
            "betfair_mapping_pct": 0.0,
            "usable_market_pct": 0.0,
        }
    )
    coverage_month["eligible_market_analysis"] = coverage_month["betfair_mapping_pct"] >= 85.0

    summary = {
        "date_from": date_from,
        "date_to": date_to,
        "tracks": tracks,
        "inventory": {
            "race_results_history_rows": int(len(rrh.index)),
            "sectional_times_rows": int(len(sectional.index)),
            "betfair_market_runner_map_rows": int(len(bmap.index)),
            "training_view_v2_rows": int(len(tv2.index)),
            "usable_market_rows": int(len(usable_market.index)),
        },
        "date_coverage": {
            "race_results_history": {
                "min_date": str(rrh["race_date_dt"].min().date()) if not rrh.empty and rrh["race_date_dt"].notna().any() else None,
                "max_date": str(rrh["race_date_dt"].max().date()) if not rrh.empty and rrh["race_date_dt"].notna().any() else None,
            },
            "sectional_times": {
                "min_date": str(sectional["race_date_dt"].min().date()) if not sectional.empty and sectional["race_date_dt"].notna().any() else None,
                "max_date": str(sectional["race_date_dt"].max().date()) if not sectional.empty and sectional["race_date_dt"].notna().any() else None,
            },
            "betfair_market_runner_map": {
                "min_date": str(bmap["meeting_date_dt"].min().date()) if not bmap.empty and bmap["meeting_date_dt"].notna().any() else None,
                "max_date": str(bmap["meeting_date_dt"].max().date()) if not bmap.empty and bmap["meeting_date_dt"].notna().any() else None,
            },
            "training_view_v2": {
                "min_date": str(tv2["race_date_dt"].min().date()) if not tv2.empty and tv2["race_date_dt"].notna().any() else None,
                "max_date": str(tv2["race_date_dt"].max().date()) if not tv2.empty and tv2["race_date_dt"].notna().any() else None,
            },
        },
        "capability_notes": [],
    }

    if usable_market.empty:
        summary["capability_notes"].append(
            "Usable T-10/SP-ish Betfair snapshot coverage query did not return rows in scope; advanced market-window analysis will be skipped or downgraded."
        )
    if rrh["jockey"].isna().all():
        summary["capability_notes"].append("Jockey field is missing in race_results_history for this scope.")
    summary["capability_notes"].append(
        "Historical rail-position columns are not present in the production DB; rail-position analysis is conditional and currently skipped."
    )
    summary["capability_notes"].append(
        "Trainer is not stored in race_results_history or training_view_v2 at 18-month scale; trainer-specific findings are marked unavailable unless a broader source is added."
    )

    coverage_dir = export_dir / "coverage"
    csv_safe_write(coverage_month.sort_values(["canonical_track", "month"]), coverage_dir / "track_month_coverage.csv")
    json_write(summary, coverage_dir / "coverage_audit.json")
    markdown_write(
        "\n".join(
            [
                "# Coverage Audit",
                "",
                f"- Date range: `{date_from}` to `{date_to}`",
                f"- Tracks: {', '.join(tracks) if tracks else 'all'}",
                f"- Results rows: {summary['inventory']['race_results_history_rows']}",
                f"- Sectional rows: {summary['inventory']['sectional_times_rows']}",
                f"- Betfair mapped runners: {summary['inventory']['betfair_market_runner_map_rows']}",
                f"- Training view rows: {summary['inventory']['training_view_v2_rows']}",
                "",
                "## Capability Notes",
                *[f"- {note}" for note in summary["capability_notes"]],
                "",
                "Coverage details written to `coverage/track_month_coverage.csv`.",
            ]
        )
        + "\n",
        coverage_dir / "coverage_audit.md",
    )

    eligible_market_cohorts = coverage_month[coverage_month["eligible_market_analysis"]].copy()

    return CoverageArtifacts(
        summary=summary,
        rrh_df=rrh,
        sectional_df=sectional,
        betfair_map_df=bmap,
        tv2_df=tv2,
        usable_market_df=usable_market,
        eligible_market_cohorts=eligible_market_cohorts,
    )
