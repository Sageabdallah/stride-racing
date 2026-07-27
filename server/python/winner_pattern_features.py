"""Winner-pattern gap features (STRIDE feature roadmap, priority 1-4).

Turns the `winner_pattern_gap` research verdict into production model features.
The research package only *discovers and documents* the gaps; this module fits the
priors and emits the recommended features so the model can actually exploit them.

Four features are implemented (flagship + roadmap priority 1-4):

  1. prior_pb_close_underreaction  (flagship, S-001)
       Prior start was a personal-best last-200m close, prior finish 3rd-5th, and
       today's price sits in the 6.0-12.0 overlay band. Binary flag.
  2. cohort_fast_close_prior       (sectional A-family)
       How far the horse's best prior last-200m beats the cohort's fast-close bar
       (25th-percentile of last_200m_time for track x distance x going x class).
  3. pos400_win_prior              (400m in-run position A-family)
       Historical win-rate uplift for the horse's usual 400m in-run position
       bucket, in today's track x distance x going cohort.
  4. jockey_wet_residual           (jockey B-family)
       Today's jockey's wet-minus-dry strike-rate delta, applied only on wet going.

Leakage safety is the central design constraint: every feature is computed from a
runner's PRIOR starts plus fields known before the jump (today's track, distance,
going, class, price, jockey). No same-race sectional or finishing information leaks
into a runner's own feature values. A horse's first start therefore yields neutral /
NaN priors by construction.

The bucketing helpers are imported from the research package so the priors are fit
on exactly the same cohorts the verdict was measured on.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

# Reuse the research package's cohort definitions so priors match the verdict exactly.
try:
    from research.winner_pattern_gap.config import (
        canonical_track,
        class_bucket,
        distance_bucket,
        normalize_going_group,
    )
    from research.winner_pattern_gap.utils import classify_position_bucket, derive_marker_position
except Exception:  # pragma: no cover - path fallback when not launched from server/python
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from research.winner_pattern_gap.config import (  # type: ignore
        canonical_track,
        class_bucket,
        distance_bucket,
        normalize_going_group,
    )
    from research.winner_pattern_gap.utils import classify_position_bucket, derive_marker_position  # type: ignore


FEATURE_NAMES = [
    "prior_pb_close_underreaction",
    "cohort_fast_close_prior",
    "pos400_win_prior",
    "jockey_wet_residual",
]

# Features whose absence is informative — tree models should see NaN, not a filled 0.
NAN_PRESERVE = ["cohort_fast_close_prior", "pos400_win_prior"]

WET_GOING_GROUPS = {"Soft", "Soft 6-7", "Heavy", "Heavy 8-10"}

# Fit gates — mirror the research thresholds that produced the findings.
_FAST_CLOSE_MIN_COHORT = 100
_FAST_CLOSE_MIN_WINS = 5
_FAST_CLOSE_QUANTILE = 0.25
_POS400_MIN_COHORT = 20
_JOCKEY_MIN_RIDES = 30

# Flagship cohort bounds (synthesis.py sectional_market_underreaction test).
_PB_PRIOR_FINISH_LO, _PB_PRIOR_FINISH_HI = 3, 5
_PB_ODDS_LO, _PB_ODDS_HI = 6.0, 12.0
_EPS = 1e-9


def _ensure_dict(value: Any) -> Any:
    """splits_json arrives as jsonb (dict) from psycopg2 but as a string from CSV."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def _key(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


def _pos_bucket(position: Any, field_size: Any) -> str:
    """classify_position_bucket, made NaN-safe on field_size (int(NaN) would raise)."""
    fs = None if pd.isna(field_size) else field_size
    return classify_position_bucket(position, fs)


def _prepare_runner_frame(rrh_df: pd.DataFrame, sectional_df: pd.DataFrame) -> pd.DataFrame:
    """Join results to sectionals and derive the cohort keys used by every prior."""
    rrh = rrh_df.copy()
    st = sectional_df.copy()

    # Sectionals attach to a result row by its primary key; that is the reliable link.
    sect_cols = ["race_results_history_id", "last_200m_time", "splits_json"]
    for col in sect_cols:
        if col not in st.columns:
            st[col] = pd.NA
    st = st[sect_cols].dropna(subset=["race_results_history_id"])
    st = st.drop_duplicates(subset=["race_results_history_id"], keep="first")
    merged = rrh.merge(st, how="left", left_on="id", right_on="race_results_history_id")

    merged["canonical_track"] = merged["track"].map(canonical_track)
    merged["race_date_dt"] = pd.to_datetime(merged["race_date"], errors="coerce")
    merged["distance_bucket"] = merged["distance_m"].map(distance_bucket)
    merged["going_group"] = merged.apply(
        lambda r: normalize_going_group(r.get("going")), axis=1
    )
    merged["class_bucket"] = merged.apply(
        lambda r: class_bucket(r.get("race_class"), r.get("class_level")), axis=1
    )
    merged["win"] = pd.to_numeric(merged["position"], errors="coerce").fillna(0).astype(int) == 1
    merged["is_wet"] = merged["going_group"].isin(WET_GOING_GROUPS)
    merged["last_200m_time"] = pd.to_numeric(merged.get("last_200m_time"), errors="coerce")
    merged["field_size"] = pd.to_numeric(merged.get("field_size"), errors="coerce")
    merged["pos_400m"] = merged.get("splits_json").map(
        lambda s: derive_marker_position(_ensure_dict(s), "400m")
    )
    merged["pos_400m"] = pd.to_numeric(merged["pos_400m"], errors="coerce")

    # Stable per-horse identity: prefer the id, fall back to the name.
    horse_key = merged.get("horse_id")
    if horse_key is None:
        horse_key = pd.Series([None] * len(merged), index=merged.index)
    horse_key = horse_key.astype("object").where(horse_key.notna() & (horse_key.astype(str) != ""), None)
    merged["horse_key"] = horse_key.fillna(
        merged["horse_name"].fillna("").astype(str).str.lower().str.replace(r"[^a-z0-9]+", "", regex=True)
    ).astype(str)
    return merged


# ---------------------------------------------------------------------------
# Fitting the priors
# ---------------------------------------------------------------------------


@dataclass
class WinnerPatternPriors:
    """Fitted lookup tables for the winner-pattern features."""

    fast_close_thresholds: Dict[str, float] = field(default_factory=dict)
    pos400_uplift: Dict[str, float] = field(default_factory=dict)
    jockey_wet_delta: Dict[str, float] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {
            "fast_close_thresholds": self.fast_close_thresholds,
            "pos400_uplift": self.pos400_uplift,
            "jockey_wet_delta": self.jockey_wet_delta,
            "meta": self.meta,
        }

    @classmethod
    def from_json(cls, payload: Dict[str, Any]) -> "WinnerPatternPriors":
        return cls(
            fast_close_thresholds={str(k): float(v) for k, v in payload.get("fast_close_thresholds", {}).items()},
            pos400_uplift={str(k): float(v) for k, v in payload.get("pos400_uplift", {}).items()},
            jockey_wet_delta={str(k): float(v) for k, v in payload.get("jockey_wet_delta", {}).items()},
            meta=payload.get("meta", {}),
        )

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "WinnerPatternPriors":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def _fit_fast_close_thresholds(frame: pd.DataFrame) -> Dict[str, float]:
    base = frame.dropna(subset=["last_200m_time"])
    out: Dict[str, float] = {}
    group_cols = ["canonical_track", "distance_bucket", "going_group", "class_bucket"]
    for keys, group in base.groupby(group_cols, dropna=False):
        if len(group) < _FAST_CLOSE_MIN_COHORT or group["win"].sum() < _FAST_CLOSE_MIN_WINS:
            continue
        threshold = group["last_200m_time"].quantile(_FAST_CLOSE_QUANTILE)
        if pd.notna(threshold):
            out[_key(*keys)] = round(float(threshold), 4)
    return out


def _fit_pos400_uplift(frame: pd.DataFrame) -> Dict[str, float]:
    base = frame.dropna(subset=["pos_400m"]).copy()
    if base.empty:
        return {}
    base["pos_400m_bucket"] = base.apply(
        lambda r: _pos_bucket(r.get("pos_400m"), r.get("field_size")), axis=1
    )
    cohort_cols = ["canonical_track", "distance_bucket", "going_group"]
    cohort = (
        base.groupby(cohort_cols + ["pos_400m_bucket"], dropna=False)
        .agg(size=("win", "size"), wins=("win", "sum"))
        .reset_index()
    )
    cohort_base = (
        base.groupby(cohort_cols, dropna=False)
        .agg(base_size=("win", "size"), base_wins=("win", "sum"))
        .reset_index()
    )
    merged = cohort.merge(cohort_base, on=cohort_cols, how="left")
    merged = merged[merged["size"] >= _POS400_MIN_COHORT]
    out: Dict[str, float] = {}
    for _, row in merged.iterrows():
        uplift = (row["wins"] / row["size"] - row["base_wins"] / row["base_size"]) * 100.0
        out[_key(row["canonical_track"], row["distance_bucket"], row["going_group"], row["pos_400m_bucket"])] = round(
            float(uplift), 4
        )
    return out


def _fit_jockey_wet_delta(frame: pd.DataFrame) -> Dict[str, float]:
    df = frame[frame["jockey"].notna()].copy()
    if df.empty:
        return {}
    summary = (
        df.groupby(["jockey", "is_wet"], dropna=False)["win"].agg(["size", "sum"]).reset_index()
    )
    pivot = summary.pivot_table(index="jockey", columns="is_wet", values=["size", "sum"], fill_value=0)
    pivot.columns = [f"{a}_{b}" for a, b in pivot.columns]
    for col in ("size_True", "sum_True", "size_False", "sum_False"):
        if col not in pivot.columns:
            pivot[col] = 0
    pivot = pivot.reset_index()
    out: Dict[str, float] = {}
    for _, row in pivot.iterrows():
        if row["size_True"] < _JOCKEY_MIN_RIDES or row["size_False"] < _JOCKEY_MIN_RIDES:
            continue
        wet_rate = row["sum_True"] / row["size_True"] * 100.0
        dry_rate = row["sum_False"] / row["size_False"] * 100.0
        out[str(row["jockey"])] = round(float(wet_rate - dry_rate), 4)
    return out


def fit_priors(rrh_df: pd.DataFrame, sectional_df: pd.DataFrame) -> WinnerPatternPriors:
    """Fit all lookup tables from historical results + sectionals."""
    frame = _prepare_runner_frame(rrh_df, sectional_df)
    priors = WinnerPatternPriors(
        fast_close_thresholds=_fit_fast_close_thresholds(frame),
        pos400_uplift=_fit_pos400_uplift(frame),
        jockey_wet_delta=_fit_jockey_wet_delta(frame),
    )
    dates = frame["race_date_dt"].dropna()
    priors.meta = {
        "runner_rows": int(len(frame.index)),
        "date_from": str(dates.min().date()) if not dates.empty else None,
        "date_to": str(dates.max().date()) if not dates.empty else None,
        "fast_close_cohorts": len(priors.fast_close_thresholds),
        "pos400_cohorts": len(priors.pos400_uplift),
        "jockeys": len(priors.jockey_wet_delta),
        "fit_params": {
            "fast_close_min_cohort": _FAST_CLOSE_MIN_COHORT,
            "fast_close_quantile": _FAST_CLOSE_QUANTILE,
            "pos400_min_cohort": _POS400_MIN_COHORT,
            "jockey_min_rides": _JOCKEY_MIN_RIDES,
        },
    }
    return priors


# ---------------------------------------------------------------------------
# Computing the features (leakage-safe)
# ---------------------------------------------------------------------------


def compute_features(history_df: pd.DataFrame, priors: WinnerPatternPriors) -> pd.DataFrame:
    """Emit the 4 features for every runner row, using only that runner's PRIOR starts.

    Input must be a runner-level frame with the race_results_history + sectional_times
    columns (as produced by `_prepare_runner_frame`, or the raw join). Output is a
    DataFrame indexed like the input with the FEATURE_NAMES columns.
    """
    if "horse_key" not in history_df.columns:
        frame = _prepare_runner_frame(history_df, history_df.iloc[0:0])
    else:
        frame = history_df.copy()

    # Capture original positions, then sort into per-horse chronological order on a fresh
    # unique index so the groupby/shift logic is robust to whatever index the caller passed
    # (a non-unique index would otherwise break the final realignment). NaT dates sort first
    # so an undated run is treated as earliest rather than most-recent.
    frame = frame.reset_index(drop=True)
    frame["_wp_pos"] = np.arange(len(frame))
    frame = frame.sort_values(
        ["horse_key", "race_date_dt", "race_number", "id"], na_position="first"
    ).reset_index(drop=True)
    grp = frame.groupby("horse_key", dropna=False)

    # Prior-run quantities (all shifted so a runner never sees its own current start).
    frame["_prev_last200"] = grp["last_200m_time"].shift(1)
    frame["_prev_position"] = grp["position"].shift(1)
    frame["_prior_best_before_prev"] = grp["last_200m_time"].transform(
        lambda s: s.expanding().min().shift(2)
    )
    frame["_prior_best_last200"] = grp["last_200m_time"].transform(
        lambda s: s.expanding().min().shift(1)
    )
    frame["_prior_avg_pos400"] = grp["pos_400m"].transform(
        lambda s: s.expanding().mean().shift(1)
    )

    today_odds = pd.to_numeric(frame.get("sp_odds"), errors="coerce")
    prev_pos = pd.to_numeric(frame["_prev_position"], errors="coerce")
    prev_pb_close = (
        frame["_prev_last200"].notna()
        & frame["_prior_best_before_prev"].notna()
        & (frame["_prev_last200"] <= frame["_prior_best_before_prev"] + _EPS)
    )

    # F1 — flagship prior-PB-close market underreaction flag.
    f1 = (
        prev_pb_close
        & prev_pos.between(_PB_PRIOR_FINISH_LO, _PB_PRIOR_FINISH_HI, inclusive="both")
        & today_odds.between(_PB_ODDS_LO, _PB_ODDS_HI, inclusive="both")
    ).astype(float)

    # F2 — cohort fast-close prior: prior best close vs the cohort's 25th-pctile bar.
    def _fast_close(row: pd.Series) -> float:
        best = row["_prior_best_last200"]
        if pd.isna(best):
            return np.nan
        thr = priors.fast_close_thresholds.get(
            _key(row["canonical_track"], row["distance_bucket"], row["going_group"], row["class_bucket"])
        )
        if thr is None:
            return np.nan
        return float(thr - best)  # positive => prior closing beats the fast-close bar

    f2 = frame.apply(_fast_close, axis=1)

    # F3 — 400m position win-rate uplift for the horse's usual in-run bucket.
    def _pos400(row: pd.Series) -> float:
        avg = row["_prior_avg_pos400"]
        if pd.isna(avg):
            return np.nan
        bucket = _pos_bucket(round(float(avg)), row.get("field_size"))
        uplift = priors.pos400_uplift.get(
            _key(row["canonical_track"], row["distance_bucket"], row["going_group"], bucket)
        )
        return np.nan if uplift is None else float(uplift)

    f3 = frame.apply(_pos400, axis=1)

    # F4 — jockey wet residual, only meaningful on wet going.
    jockey = frame.get("jockey")
    if jockey is None:
        f4 = pd.Series(0.0, index=frame.index)
    else:
        f4 = frame.apply(
            lambda r: float(priors.jockey_wet_delta.get(str(r.get("jockey")), 0.0)) if r["is_wet"] else 0.0,
            axis=1,
        )

    out = pd.DataFrame(
        {
            "prior_pb_close_underreaction": f1,
            "cohort_fast_close_prior": f2,
            "pos400_win_prior": f3,
            "jockey_wet_residual": f4,
        },
        index=frame.index,
    )
    # Realign to the caller's original order/labels positionally (robust to non-unique index).
    out["_wp_pos"] = frame["_wp_pos"].values
    out = out.sort_values("_wp_pos").drop(columns="_wp_pos")
    out.index = history_df.index
    return out


# ---------------------------------------------------------------------------
# Database-backed fitting + retrain integration
# ---------------------------------------------------------------------------


def _load_history_from_db(conn, date_from: str, date_to: str):
    import pandas.io.sql as _  # noqa: F401 (ensures pandas sql shim present)

    rrh = pd.read_sql_query(
        """
        SELECT id, horse_id, horse_name, track, race_date, race_number, distance_m,
               race_class, class_level, going, position, barrier, sp_odds, field_size, jockey
        FROM race_results_history
        WHERE race_date::date BETWEEN %(date_from)s AND %(date_to)s
        """,
        conn,
        params={"date_from": date_from, "date_to": date_to},
    )
    st = pd.read_sql_query(
        """
        SELECT race_results_history_id, last_200m_time, splits_json
        FROM sectional_times
        WHERE race_date::date BETWEEN %(date_from)s AND %(date_to)s
        """,
        conn,
        params={"date_from": date_from, "date_to": date_to},
    )
    return rrh, st


def _open_conn():
    import psycopg2

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
                    break
    if not url:
        raise RuntimeError("DATABASE_URL not found in environment or project .env")
    return psycopg2.connect(url)


def fit_from_db(conn=None, date_from: str = "2000-01-01", date_to: str = "2100-01-01") -> WinnerPatternPriors:
    own = conn is None
    conn = conn or _open_conn()
    try:
        rrh, st = _load_history_from_db(conn, date_from, date_to)
        return fit_priors(rrh, st)
    finally:
        if own:
            conn.close()


def attach_features(training_df: pd.DataFrame, conn=None, priors: Optional[WinnerPatternPriors] = None) -> pd.DataFrame:
    """Populate FEATURE_NAMES on a training frame keyed by (track, date, race_number, horse).

    Designed to slot into retrain_v2.load_training_data alongside the form features.
    Fully defensive: on any failure the input frame is returned unchanged so a retrain
    can never be broken by this hook.
    """
    try:
        own = conn is None
        conn = conn or _open_conn()
        try:
            date_to = str(pd.to_datetime(training_df["race_date"], errors="coerce").max().date())
            rrh, st = _load_history_from_db(conn, "2000-01-01", date_to)
        finally:
            if own:
                conn.close()

        frame = _prepare_runner_frame(rrh, st)
        if priors is None:
            priors = fit_priors(rrh, st)
        feats = compute_features(frame, priors)
        keyed = frame[["canonical_track", "race_date_dt", "race_number", "horse_name"]].copy()
        keyed["_wp_join"] = (
            keyed["canonical_track"].astype(str).str.lower()
            + "|"
            + keyed["race_date_dt"].dt.date.astype(str)
            + "|"
            + pd.to_numeric(keyed["race_number"], errors="coerce").fillna(0).astype(int).astype(str)
            + "|"
            + keyed["horse_name"].fillna("").astype(str).str.lower().str.replace(r"[^a-z0-9]+", "", regex=True)
        )
        lookup = pd.concat([keyed["_wp_join"], feats], axis=1).drop_duplicates(subset=["_wp_join"], keep="last")

        tv_join = (
            training_df["track"].map(canonical_track).astype(str).str.lower()
            + "|"
            + pd.to_datetime(training_df["race_date"], errors="coerce").dt.date.astype(str)
            + "|"
            + pd.to_numeric(training_df["race_number"], errors="coerce").fillna(0).astype(int).astype(str)
            + "|"
            + training_df["horse_name"].fillna("").astype(str).str.lower().str.replace(r"[^a-z0-9]+", "", regex=True)
        )
        merged = pd.DataFrame({"_wp_join": tv_join}).merge(lookup, on="_wp_join", how="left")
        for name in FEATURE_NAMES:
            training_df[name] = merged[name].values
        matched = int(merged[FEATURE_NAMES[0]].notna().sum())
        print(f"    winner-pattern features attached: {matched:,}/{len(training_df):,} rows matched")
        return training_df
    except Exception as exc:  # pragma: no cover - defensive
        print(f"    WARNING: winner-pattern feature attach failed: {exc}")
        return training_df


def _main() -> int:
    parser = argparse.ArgumentParser(description="Fit and persist winner-pattern gap priors.")
    parser.add_argument("--date-from", default="2000-01-01")
    parser.add_argument("--date-to", default="2100-01-01")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent / "intelligence" / "winner_pattern_priors.json"),
    )
    args = parser.parse_args()
    priors = fit_from_db(date_from=args.date_from, date_to=args.date_to)
    priors.save(args.out)
    print(json.dumps({"status": "ok", "out": args.out, **priors.meta}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
