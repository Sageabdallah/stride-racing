from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from .config import (
    METRO_TRACK_ORDER,
    canonical_track,
    class_bucket,
    distance_bucket,
    dslr_bucket,
    field_size_bucket,
    normalize_going_group,
    normalize_name,
    price_bracket,
    quarter_label,
)


def canonicalize_track_column(series: pd.Series) -> pd.Series:
    return series.fillna("").map(canonical_track)


def normalize_name_column(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).map(normalize_name)


def prep_race_date_column(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.date.astype("string")


def make_join_key(track: pd.Series, race_date: pd.Series, race_number: pd.Series, horse_name: pd.Series) -> pd.Series:
    return (
        canonicalize_track_column(track).astype(str).str.lower()
        + "|"
        + prep_race_date_column(race_date).fillna("").astype(str)
        + "|"
        + race_number.fillna(0).astype(int).astype(str)
        + "|"
        + normalize_name_column(horse_name).astype(str)
    )


def parse_prob(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    if numeric > 1.0 and numeric <= 100.0:
        numeric = numeric / 100.0
    if numeric < 0:
        return None
    return min(numeric, 1.0)


def safe_rate(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)


def classify_position_bucket(position: Optional[float], field_size: Optional[float]) -> str:
    if position is None or math.isnan(position):
        return "Unknown"
    pos = int(position)
    field = int(field_size or 0)
    if pos == 1:
        return "1"
    if pos in (2, 3):
        return "2-3"
    if 4 <= pos <= 6:
        return "4-6"
    if field and pos >= max(1, field - 1):
        return "Last-2"
    if pos >= 7:
        return "7+"
    return str(pos)


def parse_split_mark(splits_json: Any, mark: str, field_name: str) -> Optional[float]:
    if not isinstance(splits_json, dict):
        return None
    segment = splits_json.get(mark)
    if not isinstance(segment, dict):
        return None
    value = segment.get(field_name)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def segment_keys_desc(splits_json: Any) -> List[int]:
    if not isinstance(splits_json, dict):
        return []
    keys = []
    for key in splits_json.keys():
        match = re.match(r"^(\d+)m$", str(key))
        if match:
            keys.append(int(match.group(1)))
    return sorted(keys, reverse=True)


def derive_opening_600m(splits_json: Any) -> Optional[float]:
    keys = [key for key in segment_keys_desc(splits_json) if key > 0]
    if len(keys) < 3 or not isinstance(splits_json, dict):
        return None
    total = 0.0
    for key in keys[:3]:
        segment = splits_json.get(f"{key}m", {})
        try:
            total += float(segment.get("time"))
        except (TypeError, ValueError):
            return None
    return total


def derive_marker_position(splits_json: Any, mark: str) -> Optional[int]:
    value = parse_split_mark(splits_json, mark, "position")
    if value is None:
        return None
    return int(value)


def derive_base_dataframe_fields(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["canonical_track"] = canonicalize_track_column(out["track"])
    out["race_date_dt"] = pd.to_datetime(out["race_date"], errors="coerce")
    out["distance_bucket"] = out["distance_m"].map(distance_bucket)
    out["going_group"] = out.apply(
        lambda row: normalize_going_group(row.get("going") or row.get("track_config")), axis=1
    )
    out["class_bucket"] = out.apply(
        lambda row: class_bucket(row.get("race_class"), row.get("class_level")), axis=1
    )
    out["field_size_bucket"] = out["field_size"].map(field_size_bucket)
    out["price_bracket"] = out["market_odds"].map(price_bracket) if "market_odds" in out.columns else "Unknown"
    return out


def compute_implied_prob(odds: Any) -> Optional[float]:
    if odds is None:
        return None
    try:
        odds_val = float(odds)
    except (TypeError, ValueError):
        return None
    if odds_val <= 1:
        return None
    return 1.0 / odds_val


def compute_window_stats(
    df: pd.DataFrame,
    cohort_mask: pd.Series,
    win_column: str = "win",
    implied_prob_column: Optional[str] = None,
) -> Dict[str, Any]:
    if "race_date_dt" not in df.columns or df["race_date_dt"].isna().all():
        return {"windows": [], "stability_score": 0.0}
    clean = df.loc[df["race_date_dt"].notna()].copy()
    if clean.empty:
        return {"windows": [], "stability_score": 0.0}
    thirds = pd.qcut(clean["race_date_dt"].rank(method="first"), q=3, labels=False, duplicates="drop")
    clean["_window"] = thirds
    windows = []
    consistent = 0
    total_windows = 0
    for window in sorted(clean["_window"].dropna().unique()):
        label = quarter_label(int(window))
        base_window = clean[clean["_window"] == window]
        cohort_window = base_window[cohort_mask.loc[base_window.index]]
        if base_window.empty or cohort_window.empty:
            continue
        total_windows += 1
        actual = safe_rate(cohort_window[win_column].sum(), len(cohort_window))
        base = safe_rate(base_window[win_column].sum(), len(base_window))
        edge = actual - base
        implied = None
        if implied_prob_column and implied_prob_column in cohort_window.columns:
            implied = float(cohort_window[implied_prob_column].dropna().mean()) if cohort_window[implied_prob_column].notna().any() else None
        windows.append(
            {
                "window": label,
                "sample_size": int(len(cohort_window)),
                "win_rate": round(actual * 100, 2),
                "base_rate": round(base * 100, 2),
                "edge_vs_base": round(edge * 100, 2),
                "implied_win_rate": round((implied or 0.0) * 100, 2) if implied is not None else None,
            }
        )
        if edge > 0:
            consistent += 1
    score = safe_rate(consistent, total_windows) if total_windows else 0.0
    return {"windows": windows, "stability_score": round(score, 3)}


def markdown_from_findings(title: str, findings: Iterable[Dict[str, Any]], notes: Optional[List[str]] = None) -> str:
    lines = [f"# {title}", ""]
    if notes:
        lines.append("## Notes")
        lines.extend([f"- {note}" for note in notes])
        lines.append("")
    for finding in findings:
        lines.append(f"## {finding.get('finding_id', 'Finding')}")
        for key, value in finding.items():
            if key == "finding_id":
                continue
            pretty_key = key.replace("_", " ").title()
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=True)
            lines.append(f"- **{pretty_key}:** {value}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def feature_markdown(features: Iterable[Dict[str, Any]]) -> str:
    lines = ["# STRIDE Feature Roadmap", ""]
    for feature in features:
        lines.append(f"## {feature['feature_name']}")
        for key, value in feature.items():
            if key == "feature_name":
                continue
            lines.append(f"- **{key.replace('_', ' ').title()}:** {value}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def pick_top_candidates(df: pd.DataFrame, sort_cols: List[str], limit: int = 20) -> pd.DataFrame:
    if df.empty:
        return df
    return df.sort_values(sort_cols, ascending=[False] * len(sort_cols)).head(limit).reset_index(drop=True)
