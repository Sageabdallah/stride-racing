#!/usr/bin/env python3
"""
Compute form features from race_results_history for ML training and inference.

Used by:
  - retrain_v2.py (batch mode: all horses at once)
  - run_tips_pipeline.py (per-race mode: single race field)
"""

import numpy as np
import pandas as pd
from typing import Dict, Optional

from result_margins import beaten_margins_frame


def _race_anchor(race_date_str: Optional[str]) -> pd.Timestamp:
    """Anchor for recency features: the subject race date when known, else the
    wall clock (live serve path, where now ~= race day). Never raises — bad or
    missing input falls back to now() so serving is never blocked by a bad date."""
    if race_date_str:
        ts = pd.to_datetime(race_date_str, errors="coerce")
        if pd.notna(ts):
            return ts
    return pd.Timestamp.now()


def _normalise_going(going_str: str) -> str:
    """Normalise going description to one of: firm, good, soft, heavy, synthetic."""
    g = (going_str or "").lower().strip()
    if "heavy" in g:
        return "heavy"
    if "soft" in g:
        return "soft"
    if "firm" in g:
        return "firm"
    if "synth" in g:
        return "synthetic"
    return "good"


def compute_single_horse_features(
    prior_runs: pd.DataFrame,
    current_track: str,
    current_distance_m: int,
    current_class_level: Optional[int] = None,
    current_jockey: Optional[str] = None,
    current_weight_kg: Optional[float] = None,
    current_going: Optional[str] = None,
    prior_sectionals: Optional[pd.DataFrame] = None,
    jockey_win_rates: Optional[Dict[str, float]] = None,
    prior_sectional_ranks: Optional[pd.DataFrame] = None,
    race_date_str: Optional[str] = None,
    prior_trials: Optional[pd.DataFrame] = None,
    trial_field_quality: Optional[dict] = None,
) -> Dict[str, float]:
    """
    Compute form features for one horse given their prior runs (already filtered
    to runs before the current race date, sorted by race_date DESC).

    Returns dict mapping FEATURE_COLUMNS names to values.
    """
    features = {}

    # Winners carry their winning margin in rows written by Punting Form and
    # NULL in older rows. Every margin rule below was written for NULL, so
    # normalise first or the same win scores differently by era.
    prior_runs = beaten_margins_frame(prior_runs)

    if prior_runs.empty:
        features["days_since_run"] = 999
        features["is_first_up"] = 1
        features["is_second_up"] = 0
        features["course_strike_rate"] = 0
        features["distance_strike_rate"] = 0
        features["weighted_form_score"] = 0
        features["is_class_drop"] = 0
        features["is_class_rise"] = 0
        features["class_movement"] = 0
        features["improvement_score"] = 0
        features["is_improving"] = 0
        features["is_in_form_cycle"] = 0
        features["has_dominant_win"] = 0
        features["is_winning_combo"] = 0
        features["jockey_trainer_strike_rate"] = 0
        features["is_first_time_stakes"] = 0
        features["form_direction_slope"] = 0
        features["speed_rating_trajectory"] = 0
        features["sectional_trajectory"] = float("nan")
        features["campaign_run_number"] = 1
        features["weight_change"] = 0
        features["jockey_booking_change"] = 0
        features["fresh_x_trajectory"] = 0
        features["first_up_win_rate"] = 0.5
        features["second_up_win_rate"] = 0.5
        features["consistency_score"] = 0.5
        features["trainer_momentum_score"] = 50
        features["going_suitability"] = 0.5
        features["is_bounce_candidate"] = 0
        features["bounce_severity"] = 0.0
        features["runs_since_peak"] = float("nan")
        features["has_sectional_data"] = 0
        trial_feats = _compute_trial_features(
            prior_trials, race_date_str, career_starts=0,
            trial_field_quality=trial_field_quality,
        )
        features.update(trial_feats)
        features["trainer_trial_pattern"] = 1.0
        features["trial_quality_score"] = trial_feats.get("trial_quality_score", 0.0)
        return features

    last_run_date = pd.to_datetime(prior_runs.iloc[0]["race_date"], errors="coerce")
    if pd.notna(last_run_date):
        days = 999
        try:
            # Anchor to the subject race, not the wall clock: at train time
            # now() is the retrain date, which inflates days_since_run for
            # every historical row by however long ago the retrain runs.
            anchor = _race_anchor(race_date_str)
            days = max(0, (anchor - last_run_date).days)
        except Exception:
            pass
        features["days_since_run"] = min(days, 999)
    else:
        features["days_since_run"] = 999

    features["is_first_up"] = 1 if features["days_since_run"] > 60 or len(prior_runs) == 0 else 0

    # Second-up: exactly 1 run in last 60 days and the one before that was >60 days gap
    if len(prior_runs) >= 2 and features["days_since_run"] <= 60:
        second_date = pd.to_datetime(prior_runs.iloc[1]["race_date"], errors="coerce")
        if pd.notna(second_date) and pd.notna(last_run_date):
            gap_before = (last_run_date - second_date).days
            features["is_second_up"] = 1 if gap_before > 60 else 0
        else:
            features["is_second_up"] = 0
    else:
        features["is_second_up"] = 0

    track_lower = current_track.lower().strip()
    course_runs = prior_runs[prior_runs["track"].str.lower().str.strip() == track_lower]
    if len(course_runs) >= 5:
        course_wins = (course_runs["position"] == 1).sum()
        sr = course_wins / len(course_runs)
        confidence = min(1.0, len(course_runs) / 10.0)  # Full confidence at 10+ runs
        features["course_strike_rate"] = round(sr * confidence, 4)
    else:
        features["course_strike_rate"] = 0

    if current_distance_m and current_distance_m > 0:
        dist_runs = prior_runs[
            (prior_runs["distance_m"] >= current_distance_m - 100)
            & (prior_runs["distance_m"] <= current_distance_m + 100)
        ]
        if len(dist_runs) >= 5:
            dist_wins = (dist_runs["position"] == 1).sum()
            sr = dist_wins / len(dist_runs)
            confidence = min(1.0, len(dist_runs) / 10.0)
            features["distance_strike_rate"] = round(sr * confidence, 4)
        else:
            features["distance_strike_rate"] = 0
    else:
        features["distance_strike_rate"] = 0

    last5 = prior_runs.head(5)
    positions = last5["position"].dropna().values
    if len(positions) > 0:
        # Exponential recency decay (stronger than linear)
        weights = np.array([0.75 ** i for i in range(len(positions))])

        # Non-linear position scoring: punish bad finishes more
        def _score_pos(pos):
            if pos <= 2:   return 11 - pos
            elif pos <= 4: return 8 - (pos - 2) * 0.5
            elif pos <= 6: return 6 - (pos - 4) * 1.0
            else:          return max(0, 3 - (pos - 6) * 0.5)
        scores = np.array([_score_pos(p) for p in positions])

        # Margin adjustment (3B) — reward dominant wins, penalize narrow wins
        margins = last5["margin_lengths"].values
        for i in range(min(len(scores), len(margins))):
            m = margins[i]
            if pd.notna(m):
                m = float(m)
                if positions[i] == 1:
                    if abs(m) >= 3:   scores[i] += 1.5
                    elif abs(m) < 0.3: scores[i] -= 0.5
                elif positions[i] <= 3:
                    if abs(m) < 1.0:  scores[i] += 0.5
                    elif abs(m) > 5:  scores[i] -= 1.0

        features["weighted_form_score"] = round(float(np.average(scores, weights=weights)), 2)
    else:
        features["weighted_form_score"] = 0

    last3_class = prior_runs.head(3)["class_level"].dropna().values
    if current_class_level and len(last3_class) > 0:
        avg_prior_class = np.mean(last3_class)
        class_diff = current_class_level - avg_prior_class
        features["class_movement"] = round(float(class_diff), 2)
        features["is_class_drop"] = 1 if class_diff < -0.5 else 0
        features["is_class_rise"] = 1 if class_diff > 0.5 else 0
    else:
        features["class_movement"] = 0
        features["is_class_drop"] = 0
        features["is_class_rise"] = 0

    last3_pos = prior_runs.head(3)["position"].dropna().values
    if len(last3_pos) >= 2:
        improvements = np.diff(last3_pos)
        features["improvement_score"] = round(float(np.mean(improvements)), 2)
        features["is_improving"] = 1 if all(d > 0 for d in improvements) else 0
    else:
        features["improvement_score"] = 0
        features["is_improving"] = 0

    last5_pos = prior_runs.head(5)["position"].dropna().values
    features["is_in_form_cycle"] = 1 if len(last5_pos) >= 3 and (last5_pos[:3] <= 3).sum() >= 2 else 0

    last5_margins = prior_runs.head(5)["margin_lengths"].dropna().values
    last5_wins = prior_runs.head(5)
    dominant = False
    for _, run in last5_wins.iterrows():
        if run.get("position") == 1 and pd.notna(run.get("margin_lengths")):
            if run["margin_lengths"] <= -2.0 or run["margin_lengths"] >= 2.0:
                dominant = True
                break
    features["has_dominant_win"] = 1 if dominant else 0

    if current_jockey and isinstance(current_jockey, str):
        jockey_lower = current_jockey.lower().strip()
        jockey_col = prior_runs["jockey"].fillna("").astype(str)
        jockey_runs = prior_runs[jockey_col.str.lower().str.strip() == jockey_lower]
        if len(jockey_runs) > 0:
            jockey_wins = (jockey_runs["position"] == 1).sum()
            features["is_winning_combo"] = 1 if jockey_wins > 0 else 0
            if len(jockey_runs) >= 3:
                features["jockey_trainer_strike_rate"] = round(jockey_wins / len(jockey_runs), 4)
            else:
                features["jockey_trainer_strike_rate"] = 0
        else:
            features["is_winning_combo"] = 0
            features["jockey_trainer_strike_rate"] = 0
    else:
        features["is_winning_combo"] = 0
        features["jockey_trainer_strike_rate"] = 0

    features["is_first_time_stakes"] = 0


    last3 = prior_runs.head(3)
    last3_pos = last3["position"].dropna().values
    last3_fs = last3["field_size"].dropna().values
    if len(last3_pos) >= 3 and len(last3_fs) >= 3:
        norm_pos = [float(last3_pos[i]) / max(float(last3_fs[i]), 1)
                    for i in range(3)]
        # polyfit: x=0 is most recent, x=2 is oldest
        slope = np.polyfit([0, 1, 2], norm_pos, 1)[0]
        features["form_direction_slope"] = round(float(slope), 4)
    else:
        features["form_direction_slope"] = 0

    if len(last3_pos) >= 3:
        try:
            from speed_ratings import estimate_speed_rating_from_form
            ratings_3 = []
            for i in range(3):
                pos_val = int(last3_pos[i])
                cl = last3["class_level"].iloc[i] if "class_level" in last3.columns else 50
                cl = int(cl) if pd.notna(cl) else 50
                fs = int(last3_fs[i]) if i < len(last3_fs) and pd.notna(last3_fs[i]) else 10
                r, _, _ = estimate_speed_rating_from_form([pos_val], avg_field_size=fs, class_level=cl)
                ratings_3.append(r)
            # slope: x=0 most recent, x=2 oldest
            sr_slope = np.polyfit([0, 1, 2], ratings_3, 1)[0]
            features["speed_rating_trajectory"] = round(float(sr_slope), 4)
        except Exception:
            features["speed_rating_trajectory"] = 0
    else:
        features["speed_rating_trajectory"] = 0

    if prior_sectionals is not None and not prior_sectionals.empty:
        sect_times = prior_sectionals.head(3)["last_200m_time"].dropna().values
        if len(sect_times) >= 3:
            slope = np.polyfit([0, 1, 2], sect_times[:3], 1)[0]
            features["sectional_trajectory"] = round(float(slope), 4)
        else:
            features["sectional_trajectory"] = float("nan")
    else:
        features["sectional_trajectory"] = float("nan")

    _band = max(100, min(300, int(current_distance_m * 0.12))) if current_distance_m else 200

    if prior_sectionals is not None and not prior_sectionals.empty and current_distance_m:
        _sect_dm = prior_sectionals.dropna(subset=["last_200m_time", "distance_m"])
        _similar = _sect_dm[
            (_sect_dm["distance_m"] >= current_distance_m - _band) &
            (_sect_dm["distance_m"] <= current_distance_m + _band)
        ].sort_values("race_date", ascending=False).head(5)
        if len(_similar) >= 2:
            _times = _similar["last_200m_time"].values.astype(float)
            _x = list(range(len(_times)))
            _sl = np.polyfit(_x, _times, 1)[0]
            features["dist_sectional_slope"] = round(float(-_sl), 4)
        else:
            features["dist_sectional_slope"] = float("nan")
    else:
        features["dist_sectional_slope"] = float("nan")

    if len(prior_runs) >= 1 and current_distance_m:
        _last_dist = prior_runs.iloc[0].get("distance_m")
        if pd.notna(_last_dist) and int(_last_dist) > 0:
            _diff = current_distance_m - int(_last_dist)
            if _diff > 100:
                features["distance_direction_flag"] = 1
            elif _diff < -100:
                features["distance_direction_flag"] = -1
            else:
                features["distance_direction_flag"] = 0
        else:
            features["distance_direction_flag"] = 0
    else:
        features["distance_direction_flag"] = 0

    if (prior_sectionals is not None and not prior_sectionals.empty
            and current_distance_m):
        _sect_rw = prior_sectionals.dropna(subset=["last_200m_time", "distance_m"]).copy()
        if len(_sect_rw) >= 2:
            _today = _race_anchor(race_date_str)

            def _recency_w(rd):
                d = (_today - rd).days if pd.notna(rd) else 365
                if d <= 90: return 1.0
                elif d <= 180: return 0.7
                else: return 0.4

            _sect_rw["_w"] = _sect_rw["race_date"].apply(_recency_w)
            if current_going:
                _gn = _normalise_going(current_going)
                _sect_rw.loc[
                    _sect_rw["track_config"].fillna("").apply(_normalise_going) == _gn,
                    "_w"
                ] *= 1.5

            _sim = _sect_rw[
                (_sect_rw["distance_m"] >= current_distance_m - _band) &
                (_sect_rw["distance_m"] <= current_distance_m + _band)
            ]
            _dif = _sect_rw[
                (_sect_rw["distance_m"] < current_distance_m - _band) |
                (_sect_rw["distance_m"] > current_distance_m + _band)
            ]
            if len(_sim) >= 1 and len(_dif) >= 1:
                _sa = np.average(_sim["last_200m_time"].astype(float), weights=_sim["_w"])
                _da = np.average(_dif["last_200m_time"].astype(float), weights=_dif["_w"])
                features["dist_sectional_recency_weighted"] = round(float(-(_sa - _da)), 4)
            else:
                features["dist_sectional_recency_weighted"] = 0.0
        else:
            features["dist_sectional_recency_weighted"] = 0.0
    else:
        features["dist_sectional_recency_weighted"] = 0.0

    if prior_sectional_ranks is not None and not prior_sectional_ranks.empty and current_distance_m:
        _at_dist = prior_sectional_ranks[
            (prior_sectional_ranks["distance_m"] >= current_distance_m - _band) &
            (prior_sectional_ranks["distance_m"] <= current_distance_m + _band)
        ].sort_values("race_date", ascending=False).head(5)
        if len(_at_dist) >= 1:
            features["sectional_rank_at_distance"] = round(
                1.0 - float(_at_dist["closing_rank_pct"].mean()), 4)
        else:
            features["sectional_rank_at_distance"] = float("nan")
    else:
        features["sectional_rank_at_distance"] = float("nan")

    if (prior_sectionals is not None and not prior_sectionals.empty
            and len(prior_runs) >= 2 and current_distance_m):
        # Rename sectional columns to avoid collision with prior_runs columns
        _sect_for_merge = prior_sectionals[["race_date", "track", "last_200m_time"]].copy()
        _merged = prior_runs.head(5).merge(
            _sect_for_merge,
            on=["race_date", "track"],
            how="inner"
        ).dropna(subset=["last_200m_time", "position", "distance_m"])
        _merged = _merged.sort_values("race_date", ascending=False)
        if len(_merged) >= 2:
            _lat = _merged.iloc[0]
            _prev = _merged.iloc[1]
            _sect_imp = float(_lat["last_200m_time"]) < float(_prev["last_200m_time"])
            _pos_worse = int(_lat["position"]) > int(_prev["position"])
            _dist_chg = abs(int(_lat["distance_m"]) - int(_prev["distance_m"])) > 100
            if _sect_imp and _pos_worse and _dist_chg:
                features["sectional_result_divergence"] = round(
                    float(_prev["last_200m_time"]) - float(_lat["last_200m_time"]), 4)
            else:
                features["sectional_result_divergence"] = 0.0
        else:
            features["sectional_result_divergence"] = 0.0
    else:
        features["sectional_result_divergence"] = 0.0

    if current_distance_m and len(prior_runs) >= 1:
        _runs_at = prior_runs[
            (prior_runs["distance_m"] >= current_distance_m - 100) &
            (prior_runs["distance_m"] <= current_distance_m + 100)
        ]
        if len(_runs_at) == 0:
            _wb = max(200, min(400, int(current_distance_m * 0.2)))
            _adj = prior_sectional_ranks[
                (prior_sectional_ranks["distance_m"] >= current_distance_m - _wb) &
                (prior_sectional_ranks["distance_m"] <= current_distance_m + _wb)
            ] if (prior_sectional_ranks is not None
                  and not prior_sectional_ranks.empty) else pd.DataFrame()
            if len(_adj) >= 1:
                _ar = 1.0 - float(_adj["closing_rank_pct"].mean())
                features["first_at_distance_sectional_quality"] = round(_ar, 4) if _ar >= 0.6 else 0.0
            else:
                features["first_at_distance_sectional_quality"] = 0.0
        else:
            features["first_at_distance_sectional_quality"] = 0.0
    else:
        features["first_at_distance_sectional_quality"] = 0.0

    dates = pd.to_datetime(prior_runs["race_date"], errors="coerce").dropna()
    if len(dates) >= 1:
        campaign_count = 1
        for i in range(len(dates) - 1):
            gap = (dates.iloc[i] - dates.iloc[i + 1]).days
            if gap <= 60:
                campaign_count += 1
            else:
                break
        # If first_up, campaign starts fresh
        if features.get("is_first_up"):
            campaign_count = 1
        features["campaign_run_number"] = campaign_count
    else:
        features["campaign_run_number"] = 1

    if current_weight_kg and current_weight_kg > 0:
        last_weight = prior_runs.iloc[0].get("weight_kg")
        if pd.notna(last_weight) and float(last_weight) > 0:
            features["weight_change"] = round(float(current_weight_kg) - float(last_weight), 1)
        else:
            features["weight_change"] = 0
    else:
        features["weight_change"] = 0

    last_jockey_raw = prior_runs.iloc[0].get("jockey")
    last_jockey = str(last_jockey_raw).lower().strip() if pd.notna(last_jockey_raw) else ""
    curr_jockey_lower = current_jockey.lower().strip() if current_jockey and isinstance(current_jockey, str) else ""
    if curr_jockey_lower and last_jockey and curr_jockey_lower != last_jockey and jockey_win_rates:
        curr_wr = jockey_win_rates.get(curr_jockey_lower, 0)
        prev_wr = jockey_win_rates.get(last_jockey, 0)
        if prev_wr > 0 and curr_wr > prev_wr * 1.2:
            features["jockey_booking_change"] = 1
        elif curr_wr > 0 and curr_wr < prev_wr * 0.8:
            features["jockey_booking_change"] = -1
        else:
            features["jockey_booking_change"] = 0
    else:
        features["jockey_booking_change"] = 0

    dsr = features.get("days_since_run", 999)
    fds = features.get("form_direction_slope", 0)
    fresh_flag = 1.0 if 14 <= dsr <= 35 else 0.0
    features["fresh_x_trajectory"] = round(fds * fresh_flag, 4)

    if len(dates) >= 2:
        first_up_runs = []
        for i in range(len(dates) - 1):
            gap = (dates.iloc[i] - dates.iloc[i + 1]).days if i + 1 < len(dates) else 999
            if gap > 60:
                pos_val = prior_runs.iloc[i].get("position")
                if pd.notna(pos_val):
                    first_up_runs.append(int(pos_val) == 1)
        n_fu = len(first_up_runs)
        if n_fu > 0:
            raw_rate = sum(first_up_runs) / n_fu
            confidence = min(1.0, n_fu / 5.0)
            features["first_up_win_rate"] = round(0.5 + (raw_rate - 0.5) * confidence, 4)
        else:
            features["first_up_win_rate"] = 0.5
    else:
        features["first_up_win_rate"] = 0.5

    if len(dates) >= 3:
        second_up_runs = []
        for i in range(len(dates) - 2):
            gap_to_prev = (dates.iloc[i] - dates.iloc[i + 1]).days if i + 1 < len(dates) else 0
            gap_before = (dates.iloc[i + 1] - dates.iloc[i + 2]).days if i + 2 < len(dates) else 0
            if gap_to_prev <= 60 and gap_before > 60:
                pos_val = prior_runs.iloc[i].get("position")
                if pd.notna(pos_val):
                    second_up_runs.append(int(pos_val) == 1)
        n_su = len(second_up_runs)
        if n_su > 0:
            raw_rate = sum(second_up_runs) / n_su
            confidence = min(1.0, n_su / 5.0)
            features["second_up_win_rate"] = round(0.5 + (raw_rate - 0.5) * confidence, 4)
        else:
            features["second_up_win_rate"] = 0.5
    else:
        features["second_up_win_rate"] = 0.5

    last5_pos_arr = prior_runs.head(5)["position"].dropna().values
    last5_fs_arr = prior_runs.head(5)["field_size"].dropna().values
    n_consist = min(len(last5_pos_arr), len(last5_fs_arr))
    if n_consist >= 3:
        norm_positions = [float(last5_pos_arr[i]) / max(float(last5_fs_arr[i]), 1)
                          for i in range(n_consist)]
        std = float(np.std(norm_positions))
        features["consistency_score"] = round(max(0, 1.0 - std), 4)
    else:
        features["consistency_score"] = 0.5

    # --- Trainer momentum score (placeholder — populated by caller at training/inference) ---
    features["trainer_momentum_score"] = 50

    if current_going and "going" in prior_runs.columns:
        going_norm = _normalise_going(current_going)
        prior_going = prior_runs["going"].fillna("").apply(_normalise_going)
        going_runs = prior_runs[prior_going == going_norm]
        n_going = len(going_runs)
        if n_going == 0:
            features["going_suitability"] = 0.5  # untested = neutral
        else:
            wins = (going_runs["position"] == 1).sum()
            places = ((going_runs["position"] >= 1) & (going_runs["position"] <= 3)).sum()
            raw = (wins + 0.5 * places) / n_going
            if n_going < 3:
                confidence = n_going / 5.0
            else:
                confidence = min(1.0, n_going / 10.0)
            features["going_suitability"] = round(0.5 + (raw - 0.5) * confidence, 4)
    else:
        features["going_suitability"] = 0.5

    # --- Phase 4: Career-best bounce detection ---
    bounce = _compute_bounce_features(prior_runs)
    features.update(bounce)

    features["has_sectional_data"] = 1 if (prior_sectionals is not None and not prior_sectionals.empty) else 0

    trial_feats = _compute_trial_features(
        prior_trials, race_date_str, career_starts=len(prior_runs),
        trial_field_quality=trial_field_quality,
    )
    features.update(trial_feats)

    features["trainer_trial_pattern"] = 1.0

    return features


def _compute_bounce_features(prior_runs: pd.DataFrame) -> Dict[str, float]:
    """
    Detect career-best bounce risk. Horses that ran at or near their career-best
    performance regress ~60-70% of the time at the next start.

    Requires >= 5 prior runs for a meaningful career baseline.
    Returns: is_bounce_candidate (0/1), bounce_severity (0-1), runs_since_peak (int or NaN).
    """
    defaults = {
        "is_bounce_candidate": 0,
        "bounce_severity": 0.0,
        "runs_since_peak": float("nan"),
    }

    if len(prior_runs) < 5:
        return defaults

    positions = prior_runs["position"].values
    field_sizes = prior_runs["field_size"].values
    class_levels = prior_runs["class_level"].values if "class_level" in prior_runs.columns else None

    perf_scores = []
    for i in range(len(prior_runs)):
        pos = positions[i]
        fs = field_sizes[i] if i < len(field_sizes) else None
        cl = class_levels[i] if class_levels is not None and i < len(class_levels) else None

        if pd.isna(pos) or pd.isna(fs) or not fs or float(fs) == 0:
            perf_scores.append(float("nan"))
            continue

        pos_pctl = 1.0 - (float(pos) / float(fs))
        class_norm = (float(cl) / 100.0) if (cl is not None and pd.notna(cl) and float(cl) > 0) else 0.5
        perf_scores.append(pos_pctl * 0.6 + class_norm * 0.4)

    perf_arr = np.array(perf_scores, dtype=float)
    valid_mask = ~np.isnan(perf_arr)
    if valid_mask.sum() < 5:
        return defaults

    valid_perf = perf_arr[valid_mask]
    career_best = float(np.nanmax(valid_perf))
    career_median = float(np.nanmedian(valid_perf))

    # Check last two starts (not just last) for near-career-best performance
    recent_perfs = [perf_arr[i] for i in range(min(2, len(perf_arr))) if not np.isnan(perf_arr[i])]
    if not recent_perfs:
        return defaults

    best_recent = max(recent_perfs)
    threshold = career_best * 0.90

    is_bounce = 1 if best_recent >= threshold else 0

    # Bounce severity: how far above median was the recent peak
    denom = career_best - career_median + 1e-6
    severity = max(0.0, min(1.0, (best_recent - career_median) / denom))

    # Runs since career-best peak (index in date-sorted list, 0 = last start)
    peak_idx = None
    for i in range(len(perf_arr)):
        if not np.isnan(perf_arr[i]) and perf_arr[i] == career_best:
            peak_idx = i
            break
    runs_since = float(peak_idx) if peak_idx is not None else float("nan")

    return {
        "is_bounce_candidate": is_bounce,
        "bounce_severity": round(severity, 4),
        "runs_since_peak": runs_since,
    }


def _compute_trial_features(
    prior_trials: Optional[pd.DataFrame],
    race_date_str: Optional[str],
    career_starts: int = 0,
    trial_field_quality: Optional[dict] = None,
) -> Dict[str, float]:
    """
    Compute barrier trial features for a single horse.

    trial_recency       — days since most recent trial before this race. 999 = no trial.
    trial_count_60d     — number of trials in the 60 days before this race.
    trial_x_experience  — best trial position percentile × (1/career_starts). First-starter signal.
    trial_quality_score — composite: quality_raw × field_multiplier × volume_factor.
                          field_multiplier = min(field_quality / 0.10, 3.0)
                          volume_factor    = min(count_60 / 3.0, 1.0)
    """
    defaults = {
        "trial_recency": 999.0,
        "trial_count_60d": 0.0,
        "trial_x_experience": 0.0,
        "trial_quality_score": 0.0,
    }

    if prior_trials is None or prior_trials.empty or not race_date_str:
        return defaults

    try:
        race_dt = pd.to_datetime(race_date_str, errors="coerce")
        if pd.isna(race_dt):
            return defaults

        trials = prior_trials.copy()
        trials["trial_date_dt"] = pd.to_datetime(trials["trial_date"], errors="coerce")
        trials = trials[trials["trial_date_dt"] < race_dt].dropna(subset=["trial_date_dt"])

        if trials.empty:
            return defaults

        most_recent = trials["trial_date_dt"].max()
        recency = max(0, (race_dt - most_recent).days)

        cutoff_60 = race_dt - pd.Timedelta(days=60)
        window_trials = trials[trials["trial_date_dt"] >= cutoff_60]
        count_60 = int(len(window_trials))

        # Best position percentile in 60-day window: (field_size - position) / field_size
        # 1st in field of 8 → 0.875, last → 0.0. Track the event key of the best trial.
        quality_raw = 0.0
        best_event_key = None
        for _, row in window_trials.iterrows():
            pos = row.get("position")
            fs = row.get("field_size")
            if pos is not None and fs is not None and fs > 1:
                pct = (fs - pos) / fs
                if pct > quality_raw:
                    quality_raw = pct
                    best_event_key = (
                        str(row.get("trial_date", "")),
                        str(row.get("course", "")),
                        str(row.get("race_name", "")),
                    )

        trial_x_exp = quality_raw / max(career_starts, 1)

        field_quality = 0.10
        if trial_field_quality is not None and best_event_key is not None:
            fq = trial_field_quality.get(best_event_key)
            if fq is not None:
                field_quality = float(fq)

        field_multiplier = min(field_quality / 0.10, 3.0)
        volume_factor = min(count_60 / 3.0, 1.0)
        trial_quality_score = quality_raw * field_multiplier * volume_factor

        return {
            "trial_recency": float(min(recency, 999)),
            "trial_count_60d": float(count_60),
            "trial_x_experience": float(trial_x_exp),
            "trial_quality_score": float(trial_quality_score),
        }
    except Exception:
        return defaults


_LEAGUE_AVG_FIRST_UP_WIN_RATE = 0.10  # Australian thoroughbred first-up baseline


def _load_trainer_trial_patterns(conn, as_of_date=None) -> dict:
    """
    Compute trainer_trial_pattern lookup: {trainer_name_lower: float}.

    Value = (post_trial_win_rate / own_first_up_win_rate) * min(post_trial_count / 10, 1.0)

    post_trial_win_rate : wins in first race 0-45 days after a barrier trial, per trainer
    own_first_up_win_rate: from selections.is_first_up = true, fallback to league avg (0.10)
    Minimum 5 post-trial starts per trainer to appear in lookup.

    as_of_date (str 'YYYY-MM-DD' or None): if set, only aggregate data strictly
    before this date — required for backfilling historical training rows without leakage.
    None means "use everything" — fine for live inference, unsafe for training rows.
    Returns {} on failure; callers default to 1.0.
    """
    import psycopg2.extras
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cutoff = as_of_date
        cur.execute("""
            WITH horse_trainers AS (
                -- Map horse_id → most recent trainer via race_results_history → training_data
                SELECT DISTINCT ON (rrh.horse_id)
                    rrh.horse_id,
                    LOWER(TRIM(td.trainer)) AS trainer_key
                FROM race_results_history rrh
                JOIN training_data td
                    ON LOWER(TRIM(td.horse_name)) = LOWER(TRIM(rrh.horse_name))
                WHERE td.trainer IS NOT NULL
                  AND (%(cutoff)s::date IS NULL OR td.race_date::date < %(cutoff)s::date)
                ORDER BY rrh.horse_id, td.race_date DESC
            ),
            post_trial_starts AS (
                -- First race per (horse, trial) within 0-45 days after the trial
                SELECT DISTINCT ON (btr.horse_id, btr.trial_date)
                    ht.trainer_key,
                    rrh2.position
                FROM barrier_trial_results btr
                JOIN horse_trainers ht ON ht.horse_id = btr.horse_id
                JOIN race_results_history rrh2 ON rrh2.horse_id = btr.horse_id
                    AND rrh2.race_date::date > btr.trial_date::date
                    AND rrh2.race_date::date <= btr.trial_date::date + INTERVAL '45 days'
                WHERE rrh2.position IS NOT NULL
                  AND (%(cutoff)s::date IS NULL OR rrh2.race_date::date < %(cutoff)s::date)
                  AND (%(cutoff)s::date IS NULL OR btr.trial_date::date < %(cutoff)s::date)
                ORDER BY btr.horse_id, btr.trial_date, rrh2.race_date ASC
            ),
            post_trial_summary AS (
                SELECT
                    trainer_key,
                    COUNT(*) AS post_trial_count,
                    SUM(CASE WHEN position = 1 THEN 1 ELSE 0 END) AS post_trial_wins
                FROM post_trial_starts
                GROUP BY trainer_key
                HAVING COUNT(*) >= 5
            ),
            first_up_summary AS (
                -- Trainer's own first-up win rate from selections (is_first_up flag)
                SELECT
                    LOWER(TRIM(trainer)) AS trainer_key,
                    COUNT(*) AS first_up_count,
                    SUM(CASE WHEN result_position = 1 THEN 1 ELSE 0 END) AS first_up_wins
                FROM selections
                WHERE is_first_up = true
                  AND result_position IS NOT NULL
                  AND trainer IS NOT NULL
                  AND (%(cutoff)s::date IS NULL OR race_date::date < %(cutoff)s::date)
                GROUP BY 1
                HAVING COUNT(*) >= 5
            )
            SELECT
                pt.trainer_key,
                pt.post_trial_count,
                pt.post_trial_wins::float / pt.post_trial_count AS post_trial_win_rate,
                COALESCE(
                    fu.first_up_wins::float / NULLIF(fu.first_up_count, 0),
                    NULL
                ) AS first_up_win_rate
            FROM post_trial_summary pt
            LEFT JOIN first_up_summary fu ON pt.trainer_key = fu.trainer_key
        """, {"cutoff": cutoff})
        rows = cur.fetchall()
        cur.close()
        result = {}
        for row in rows:
            tk = row["trainer_key"]
            post_rate = float(row["post_trial_win_rate"])
            post_count = int(row["post_trial_count"])
            fu_rate = row["first_up_win_rate"]
            fu_rate = float(fu_rate) if fu_rate is not None else _LEAGUE_AVG_FIRST_UP_WIN_RATE
            if fu_rate == 0:
                fu_rate = _LEAGUE_AVG_FIRST_UP_WIN_RATE
            ratio = post_rate / fu_rate
            credibility = min(post_count / 10.0, 1.0)
            result[tk] = round(ratio * credibility, 4)
        return result
    except Exception as e:
        print(f"  WARNING: trainer_trial_patterns load failed: {e}")
        return {}


def _load_trial_field_quality(conn, as_of_date=None) -> dict:
    """
    Compute field quality for every unique trial event.

    Returns {(trial_date_str, course, race_name): float} where the value is
    the average career win rate of all horses that ran in that event.
    Career win rate = wins / starts from race_results_history (min 1 start).

    as_of_date (str 'YYYY-MM-DD' or None): if set, both career win rates and
    trial events are computed from data strictly before this date — required
    for backfilling historical training rows without leakage.

    Returns {} on failure; callers default field_quality to 0.10 (league avg).
    """
    import psycopg2.extras
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            WITH horse_career_winrates AS (
                SELECT
                    horse_id,
                    SUM(CASE WHEN position = 1 THEN 1.0 ELSE 0.0 END)
                        / NULLIF(COUNT(*), 0) AS win_rate
                FROM race_results_history
                WHERE position IS NOT NULL
                  AND (%(cutoff)s::date IS NULL OR race_date::date < %(cutoff)s::date)
                GROUP BY horse_id
            ),
            event_field_quality AS (
                SELECT
                    btr.trial_date::text AS trial_date,
                    btr.course,
                    btr.race_name,
                    AVG(COALESCE(hcw.win_rate, 0.0)) AS field_quality
                FROM barrier_trial_results btr
                LEFT JOIN horse_career_winrates hcw ON hcw.horse_id = btr.horse_id
                WHERE btr.horse_id IS NOT NULL
                  AND (%(cutoff)s::date IS NULL OR btr.trial_date::date < %(cutoff)s::date)
                GROUP BY btr.trial_date::text, btr.course, btr.race_name
            )
            SELECT trial_date, course, race_name, field_quality
            FROM event_field_quality
        """, {"cutoff": as_of_date})
        rows = cur.fetchall()
        cur.close()
        result = {}
        for row in rows:
            key = (row["trial_date"], row["course"], row["race_name"])
            result[key] = float(row["field_quality"]) if row["field_quality"] is not None else 0.0
        return result
    except Exception as e:
        print(f"  WARNING: trial_field_quality load failed: {e}")
        return {}


def _load_jockey_win_rates(conn, as_of_date=None) -> dict:
    """
    Return {jockey_key_lower: win_rate} for jockeys with >= 10 rides before as_of_date.

    as_of_date None → use all of history (safe for live inference only).
    """
    import psycopg2.extras
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT LOWER(TRIM(jockey)) AS jockey_key,
                   COUNT(*) AS rides,
                   SUM(CASE WHEN position = 1 THEN 1 ELSE 0 END) AS wins
            FROM race_results_history
            WHERE jockey IS NOT NULL
              AND position IS NOT NULL
              AND (%(cutoff)s::date IS NULL OR race_date::date < %(cutoff)s::date)
            GROUP BY LOWER(TRIM(jockey))
            HAVING COUNT(*) >= 10
        """, {"cutoff": as_of_date})
        result = {row["jockey_key"]: row["wins"] / row["rides"] for row in cur.fetchall()}
        cur.close()
        return result
    except Exception as e:
        print(f"  WARNING: jockey_win_rates load failed: {e}")
        return {}


def _load_horse_trainer_mapping(conn, as_of_date=None) -> dict:
    """
    Return {horse_key_lower: trainer_key_lower} using the most recent trainer per horse
    from training_data where race_date < as_of_date (or all history if None).
    """
    import psycopg2.extras
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT DISTINCT ON (LOWER(TRIM(horse_name)))
                LOWER(TRIM(horse_name)) AS horse_key,
                LOWER(TRIM(trainer))    AS trainer_key
            FROM training_data
            WHERE trainer IS NOT NULL
              AND (%(cutoff)s::date IS NULL OR race_date::date < %(cutoff)s::date)
            ORDER BY LOWER(TRIM(horse_name)), race_date DESC
        """, {"cutoff": as_of_date})
        result = {row["horse_key"]: row["trainer_key"] for row in cur.fetchall()}
        cur.close()
        return result
    except Exception as e:
        print(f"  WARNING: horse-trainer mapping load failed: {e}")
        return {}


def batch_compute_form_features(training_df: pd.DataFrame, conn) -> pd.DataFrame:
    """
    Batch compute form features for all rows in training_df.
    Loads race_results_history once, groups by horse, vectorizes.

    training_df must have columns: horse_name, track, race_date, distance_m
    Optionally: class_level, jockey, weight_kg, going

    Returns DataFrame with same index as training_df, columns = form feature names.
    """
    import psycopg2.extras

    print("  Loading race_results_history for batch form features...")
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT horse_name, horse_id, track, race_date, distance_m, class_level,
               position, margin_lengths, jockey, sp_odds, field_size,
               weight_kg, going
        FROM race_results_history
        WHERE position IS NOT NULL
        ORDER BY horse_name, race_date DESC
    """)
    history_rows = cur.fetchall()
    cur.close()

    history_df = beaten_margins_frame(pd.DataFrame(history_rows))
    if history_df.empty:
        print("  WARNING: race_results_history is empty!")
        return pd.DataFrame(index=training_df.index)

    history_df["race_date"] = pd.to_datetime(history_df["race_date"], errors="coerce")
    history_df["horse_key"] = history_df["horse_name"].str.lower().str.strip()

    grouped = {k: v for k, v in history_df.groupby("horse_key")}

    name_to_horse_id = {}
    if "horse_id" in history_df.columns:
        name_to_horse_id = (
            history_df.dropna(subset=["horse_id"])
            .groupby("horse_key")["horse_id"]
            .first()
            .to_dict()
        )

    print(f"  {len(history_df)} history rows, {len(grouped)} unique horses")

    trial_grouped = {}
    try:
        all_horse_ids = list(set(name_to_horse_id.values()))
        if all_horse_ids:
            cur_t = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur_t.execute("""
                SELECT horse_id, trial_date, position, field_size, course, race_name
                FROM barrier_trial_results
                WHERE horse_id = ANY(%s)
                ORDER BY horse_id, trial_date DESC
            """, (all_horse_ids,))
            trial_rows = cur_t.fetchall()
            cur_t.close()
            if trial_rows:
                trial_df_batch = pd.DataFrame(trial_rows)
                trial_grouped = {k: v for k, v in trial_df_batch.groupby("horse_id")}
                print(f"  {len(trial_rows)} trial rows, {len(trial_grouped)} horses with trial history")
    except Exception as e:
        print(f"  WARNING: barrier_trial_results load failed: {e}")

    # --- Build per-month cache of time-filtered lookups ---
    # Each training row uses the lookup keyed by the first day of its race_date month,
    # so aggregations only include data strictly before that month boundary.
    # At most ~30 days of recency slop per row; zero leakage from the row's own month forward.
    months = sorted(
        training_df["race_date"]
        .pipe(pd.to_datetime, errors="coerce")
        .dropna()
        .dt.strftime("%Y-%m-01")
        .unique()
        .tolist()
    )
    if not months:
        print("  WARNING: training_df has no valid race_dates for time-aware form features")
        # Wall clock is intentional here: this bucket is only a fallback when no
        # row has a race_date (live-serve shape), so "latest month" == current month.
        months = [pd.Timestamp.utcnow().strftime("%Y-%m-01")]

    print(f"  Building time-aware lookups for {len(months)} month buckets "
          f"({months[0]} → {months[-1]})")

    monthly_trainer_patterns: dict = {}
    monthly_trial_field_quality: dict = {}
    monthly_horse_to_trainer: dict = {}
    monthly_jockey_win_rates: dict = {}
    for i, cutoff in enumerate(months, 1):
        monthly_trainer_patterns[cutoff] = _load_trainer_trial_patterns(conn, as_of_date=cutoff)
        monthly_trial_field_quality[cutoff] = _load_trial_field_quality(conn, as_of_date=cutoff)
        monthly_horse_to_trainer[cutoff] = _load_horse_trainer_mapping(conn, as_of_date=cutoff)
        monthly_jockey_win_rates[cutoff] = _load_jockey_win_rates(conn, as_of_date=cutoff)
        if i == 1 or i == len(months) or i % 6 == 0:
            print(f"    [{i}/{len(months)}] {cutoff}  "
                  f"trainers={len(monthly_trainer_patterns[cutoff])}  "
                  f"events={len(monthly_trial_field_quality[cutoff])}  "
                  f"horse→trainer={len(monthly_horse_to_trainer[cutoff])}  "
                  f"jockeys={len(monthly_jockey_win_rates[cutoff])}")

    print("  Loading sectional_times for trajectory features...")
    sect_grouped = {}
    try:
        cur2 = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur2.execute("""
            SELECT horse_name, race_date, last_200m_time, distance_m, track_config, track
            FROM sectional_times
            WHERE last_200m_time IS NOT NULL
            ORDER BY horse_name, race_date DESC
        """)
        sect_rows = cur2.fetchall()
        cur2.close()
        if sect_rows:
            sect_df = pd.DataFrame(sect_rows)
            sect_df["race_date"] = pd.to_datetime(sect_df["race_date"], errors="coerce")
            sect_df["horse_key"] = sect_df["horse_name"].str.lower().str.strip()
            sect_grouped = {k: v for k, v in sect_df.groupby("horse_key")}
            print(f"  {len(sect_df)} sectional rows, {len(sect_grouped)} unique horses")
    except Exception as e:
        print(f"  WARNING: sectional_times load failed: {e}")

    # (Jockey win rates are now computed per-month in the monthly lookup cache above.)

    print("  Loading field-level sectional ranks...")
    ranks_grouped = {}
    try:
        cur4 = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur4.execute("""
            SELECT horse_name, track, race_date, race_number, distance_m,
                   last_200m_speed,
                   PERCENT_RANK() OVER (
                       PARTITION BY track, race_date, race_number
                       ORDER BY last_200m_speed DESC
                   ) as closing_rank_pct
            FROM sectional_times
            WHERE last_200m_speed IS NOT NULL
              AND race_date::date >= CURRENT_DATE - INTERVAL '3 years'
            ORDER BY horse_name, race_date DESC
        """)
        ranks_rows = cur4.fetchall()
        cur4.close()
        if ranks_rows:
            ranks_df = pd.DataFrame(ranks_rows)
            ranks_df["race_date"] = pd.to_datetime(ranks_df["race_date"], errors="coerce")
            ranks_df["horse_key"] = ranks_df["horse_name"].str.lower().str.strip()
            ranks_grouped = {k: v for k, v in ranks_df.groupby("horse_key")}
            print(f"  {len(ranks_df)} rank rows, {len(ranks_grouped)} unique horses")
    except Exception as e:
        print(f"  WARNING: sectional ranks load failed: {e}")

    all_features = []
    # Default bucket for any row missing race_date: use the latest month so live
    # inference paths (which go through compute_race_form_features, not this one)
    # still fall back to full-history lookups.
    default_bucket = months[-1]
    for idx, row in training_df.iterrows():
        horse_key = str(row.get("horse_name", "")).lower().strip()
        race_date = pd.to_datetime(row.get("race_date"), errors="coerce")
        track = str(row.get("track", ""))
        dist = int(row.get("distance_m", 0) or 0)
        class_lvl = row.get("class_level")
        jockey = row.get("jockey")
        weight_kg = row.get("weight_kg")
        going = row.get("going")
        # Guard against NaN floats in string fields
        if not isinstance(jockey, str):
            jockey = None
        if not isinstance(class_lvl, (int, float)) or pd.isna(class_lvl):
            class_lvl = None
        if not isinstance(weight_kg, (int, float)) or pd.isna(weight_kg):
            weight_kg = None
        if not isinstance(going, str):
            going = None

        bucket = race_date.strftime("%Y-%m-01") if pd.notna(race_date) else default_bucket
        trainer_pattern_lookup = monthly_trainer_patterns.get(bucket, {})
        trial_field_quality = monthly_trial_field_quality.get(bucket, {})
        horse_to_trainer = monthly_horse_to_trainer.get(bucket, {})
        jockey_win_rates = monthly_jockey_win_rates.get(bucket, {})

        if horse_key in grouped and pd.notna(race_date):
            horse_hist = grouped[horse_key]
            prior = horse_hist[horse_hist["race_date"] < race_date]
        else:
            prior = pd.DataFrame()

        prior_sect = None
        if horse_key in sect_grouped and pd.notna(race_date):
            horse_sect = sect_grouped[horse_key]
            prior_sect = horse_sect[horse_sect["race_date"] < race_date]

        prior_ranks = None
        if horse_key in ranks_grouped and pd.notna(race_date):
            horse_ranks = ranks_grouped[horse_key]
            prior_ranks = horse_ranks[horse_ranks["race_date"] < race_date]

        prior_trials = None
        if trial_grouped:
            hid = name_to_horse_id.get(horse_key)
            if hid and hid in trial_grouped:
                prior_trials = trial_grouped[hid]

        feats = compute_single_horse_features(
            prior, track, dist,
            current_class_level=class_lvl,
            current_jockey=jockey,
            current_weight_kg=float(weight_kg) if weight_kg else None,
            current_going=going,
            prior_sectionals=prior_sect,
            jockey_win_rates=jockey_win_rates,
            prior_sectional_ranks=prior_ranks,
            race_date_str=str(race_date.date()) if pd.notna(race_date) else None,
            prior_trials=prior_trials,
            trial_field_quality=trial_field_quality,
        )
        # Override trainer_trial_pattern from batch-computed lookup
        trainer_key = horse_to_trainer.get(horse_key, "")
        feats["trainer_trial_pattern"] = trainer_pattern_lookup.get(trainer_key, 1.0)
        all_features.append(feats)

    result = pd.DataFrame(all_features, index=training_df.index)
    non_zero = {col: (result[col] != 0).sum() for col in result.columns}
    print(f"  Form features computed. Non-zero counts:")
    for col, cnt in sorted(non_zero.items(), key=lambda x: -x[1]):
        print(f"    {col:30s} {cnt:>6}")

    return result


def compute_race_form_features(
    horses: list,
    track: str,
    race_date: str,
    distance_m: int,
    conn,
    going: Optional[str] = None,
) -> list:
    """
    Compute form features for a list of horse dicts (inference time).
    Modifies horses in-place, adding form feature keys.

    Each horse dict should have at minimum: 'horse' (name).
    Optionally: 'jockey', 'class_level', 'weight'.
    """
    import psycopg2.extras

    horse_names = [h.get("horse", "").strip() for h in horses if h.get("horse")]
    if not horse_names:
        return horses

    trial_grouped = {}
    try:
        horse_ids = [h.get("horse_id") for h in horses if h.get("horse_id")]
        if horse_ids:
            cur_t = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur_t.execute("""
                SELECT horse_id, trial_date, position, field_size, course, race_name
                FROM barrier_trial_results
                WHERE horse_id = ANY(%s)
                ORDER BY horse_id, trial_date DESC
            """, (horse_ids,))
            trial_rows = cur_t.fetchall()
            cur_t.close()
            if trial_rows:
                trial_df = pd.DataFrame(trial_rows)
                trial_grouped = {k: v for k, v in trial_df.groupby("horse_id")}
    except Exception:
        pass

    trainer_pattern_lookup = _load_trainer_trial_patterns(conn)

    trial_field_quality = _load_trial_field_quality(conn)

    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    placeholders = ",".join(["%s"] * len(horse_names))
    cur.execute(f"""
        SELECT horse_name, track, race_date, distance_m, class_level,
               position, margin_lengths, jockey, sp_odds, field_size,
               weight_kg, going
        FROM race_results_history
        WHERE LOWER(TRIM(horse_name)) IN ({placeholders})
          AND position IS NOT NULL
        ORDER BY horse_name, race_date DESC
    """, [n.lower().strip() for n in horse_names])
    history_rows = cur.fetchall()
    cur.close()

    history_df = beaten_margins_frame(pd.DataFrame(history_rows)) if history_rows else pd.DataFrame()
    if not history_df.empty:
        history_df["race_date"] = pd.to_datetime(history_df["race_date"], errors="coerce")
        history_df["horse_key"] = history_df["horse_name"].str.lower().str.strip()
        grouped = {k: v for k, v in history_df.groupby("horse_key")}
    else:
        grouped = {}

    sect_grouped = {}
    try:
        cur2 = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur2.execute(f"""
            SELECT horse_name, race_date, last_200m_time, distance_m, track_config, track
            FROM sectional_times
            WHERE LOWER(TRIM(horse_name)) IN ({placeholders})
              AND last_200m_time IS NOT NULL
            ORDER BY horse_name, race_date DESC
        """, [n.lower().strip() for n in horse_names])
        sect_rows = cur2.fetchall()
        cur2.close()
        if sect_rows:
            sect_df = pd.DataFrame(sect_rows)
            sect_df["race_date"] = pd.to_datetime(sect_df["race_date"], errors="coerce")
            sect_df["horse_key"] = sect_df["horse_name"].str.lower().str.strip()
            sect_grouped = {k: v for k, v in sect_df.groupby("horse_key")}
    except Exception:
        pass

    jockey_win_rates = {}
    try:
        cur3 = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur3.execute("""
            SELECT LOWER(TRIM(jockey)) AS jockey_key,
                   COUNT(*) AS rides,
                   SUM(CASE WHEN position = 1 THEN 1 ELSE 0 END) AS wins
            FROM race_results_history
            WHERE jockey IS NOT NULL AND position IS NOT NULL
            GROUP BY LOWER(TRIM(jockey))
            HAVING COUNT(*) >= 10
        """)
        for row in cur3.fetchall():
            jockey_win_rates[row["jockey_key"]] = row["wins"] / row["rides"]
        cur3.close()
    except Exception:
        pass

    ranks_grouped = {}
    try:
        cur4 = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur4.execute(f"""
            SELECT horse_name, track, race_date, race_number, distance_m,
                   last_200m_speed,
                   PERCENT_RANK() OVER (
                       PARTITION BY track, race_date, race_number
                       ORDER BY last_200m_speed DESC
                   ) as closing_rank_pct
            FROM sectional_times
            WHERE LOWER(TRIM(horse_name)) IN ({placeholders})
              AND last_200m_speed IS NOT NULL
            ORDER BY horse_name, race_date DESC
        """, [n.lower().strip() for n in horse_names])
        ranks_rows = cur4.fetchall()
        cur4.close()
        if ranks_rows:
            ranks_df = pd.DataFrame(ranks_rows)
            ranks_df["race_date"] = pd.to_datetime(ranks_df["race_date"], errors="coerce")
            ranks_df["horse_key"] = ranks_df["horse_name"].str.lower().str.strip()
            ranks_grouped = {k: v for k, v in ranks_df.groupby("horse_key")}
    except Exception:
        pass

    race_dt = pd.to_datetime(race_date, errors="coerce")

    for h in horses:
        horse_key = h.get("horse", "").lower().strip()
        horse_id = h.get("horse_id")
        class_lvl = h.get("class_level")
        jockey = h.get("jockey")
        wt_raw = str(h.get("weight", "0")).replace("kg", "").strip()
        try:
            weight_kg = float(wt_raw) if wt_raw else None
        except (ValueError, TypeError):
            weight_kg = None

        if horse_key in grouped and pd.notna(race_dt):
            prior = grouped[horse_key]
            prior = prior[prior["race_date"] < race_dt]
        else:
            prior = pd.DataFrame()

        prior_sect = None
        if horse_key in sect_grouped and pd.notna(race_dt):
            horse_sect = sect_grouped[horse_key]
            prior_sect = horse_sect[horse_sect["race_date"] < race_dt]

        prior_ranks = None
        if horse_key in ranks_grouped and pd.notna(race_dt):
            horse_ranks = ranks_grouped[horse_key]
            prior_ranks = horse_ranks[horse_ranks["race_date"] < race_dt]

        prior_trials = trial_grouped.get(horse_id) if horse_id else None

        feats = compute_single_horse_features(
            prior, track, distance_m,
            current_class_level=class_lvl,
            current_jockey=jockey,
            current_weight_kg=weight_kg,
            current_going=going,
            prior_sectionals=prior_sect,
            jockey_win_rates=jockey_win_rates,
            prior_sectional_ranks=prior_ranks,
            race_date_str=race_date,
            prior_trials=prior_trials,
            trial_field_quality=trial_field_quality,
        )
        h.update(feats)
        trainer_key = (h.get("trainer") or "").lower().strip()
        h["trainer_trial_pattern"] = trainer_pattern_lookup.get(trainer_key, 1.0)

    return horses
