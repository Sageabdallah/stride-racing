#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import time
from textwrap import dedent

from dotenv import load_dotenv
import psycopg2


DDL_FUNCTIONS = dedent(
    """
    CREATE OR REPLACE FUNCTION stride_norm_name(src text)
    RETURNS text
    LANGUAGE sql
    IMMUTABLE
    AS $$
      SELECT lower(regexp_replace(coalesce(src, ''), '[^a-z0-9]+', '', 'g'));
    $$;

    CREATE OR REPLACE FUNCTION stride_norm_track(src text)
    RETURNS text
    LANGUAGE sql
    IMMUTABLE
    AS $$
      SELECT CASE
        WHEN src IS NULL OR btrim(src) = '' THEN ''
        WHEN lower(src) IN ('royal randwick', 'randwick') THEN 'randwick'
        WHEN lower(src) IN ('randwick-kensington', 'kensington') THEN 'kensington'
        WHEN lower(src) IN ('rosehill gardens', 'rosehill') THEN 'rosehill'
        WHEN lower(src) IN ('ascot', 'ascot wa') THEN 'ascotwa'
        WHEN lower(src) LIKE '%moonee valley%' THEN 'mooneevalley'
        WHEN lower(src) LIKE '%eagle farm%' THEN 'eaglefarm'
        WHEN lower(src) LIKE '%morphettville%' THEN 'morphettville'
        WHEN lower(src) LIKE '%caulfield%' THEN 'caulfield'
        WHEN lower(src) LIKE '%flemington%' THEN 'flemington'
        WHEN lower(src) LIKE '%shatin%' OR lower(src) LIKE '%sha tin%' THEN 'shatin'
        WHEN lower(src) LIKE '%happy valley%' THEN 'happyvalley'
        ELSE lower(regexp_replace(src, '[^a-z0-9]+', '', 'g'))
      END;
    $$;
    """
).strip()


DDL_SUPPORT_INDEXES = dedent(
    """
    CREATE INDEX IF NOT EXISTS idx_sectional_times_norm_horse_date
        ON sectional_times (stride_norm_name(horse_name), race_date DESC)
        WHERE (lambda_decay IS NOT NULL OR z_200m IS NOT NULL);

    CREATE INDEX IF NOT EXISTS idx_race_results_history_norm_keys
        ON race_results_history (race_date, race_number, stride_norm_track(track), stride_norm_name(horse_name))
        WHERE position IS NOT NULL;

    CREATE INDEX IF NOT EXISTS idx_selections_norm_keys
        ON selections (race_number, stride_norm_track(track), stride_norm_name(horse_name))
        WHERE race_date IS NOT NULL AND race_number IS NOT NULL AND horse_name IS NOT NULL;

    CREATE INDEX IF NOT EXISTS idx_training_data_norm_keys
        ON training_data (race_number, stride_norm_track(track), stride_norm_name(horse_name))
        WHERE race_date IS NOT NULL AND race_number IS NOT NULL AND horse_name IS NOT NULL AND predicted_win_prob IS NOT NULL;

    CREATE INDEX IF NOT EXISTS idx_prediction_audit_norm_keys
        ON prediction_audit (race_number, stride_norm_track(track), stride_norm_name(horse_name))
        WHERE race_date IS NOT NULL AND race_number IS NOT NULL AND horse_name IS NOT NULL AND predicted_win_prob IS NOT NULL;
    """
).strip()


DDL_TRAINING_VIEW = dedent(
    """
    DROP MATERIALIZED VIEW IF EXISTS training_view_v2;

    CREATE MATERIALIZED VIEW training_view_v2 AS
    WITH selection_source AS (
        SELECT
            stride_norm_track(s.track) AS track_norm,
            NULLIF(s.race_date, '')::date AS race_date,
            s.race_number,
            stride_norm_name(s.horse_name) AS horse_name_norm,
            s.market_odds,
            COALESCE(s.model_probability, s.raw_model_prob, s.win_percentage) AS model_probability,
            COALESCE(s.blended_prob, s.win_prob_calibrated, s.calibrated_win_prob, s.win_percentage, s.model_probability) AS predicted_win_prob,
            s.expected_value,
            s.speed_rating,
            s.pace_score,
            s.weighted_form_score,
            s.franking_score,
            s.ground_suitability,
            s.distance_strike_rate,
            s.running_style,
            s.confidence,
            s.id::text AS selection_id,
            'selections'::text AS prediction_source,
            'live_model'::text AS prediction_quality,
            COALESCE(s.resulted_at, s.processed_at, s.created_at) AS source_timestamp
        FROM selections s
        WHERE s.race_date IS NOT NULL
          AND s.race_number IS NOT NULL
          AND s.horse_name IS NOT NULL
    ),
    training_data_source AS (
        SELECT
            stride_norm_track(td.track) AS track_norm,
            NULLIF(td.race_date, '')::date AS race_date,
            td.race_number,
            stride_norm_name(td.horse_name) AS horse_name_norm,
            COALESCE(sel.market_odds, td.market_odds) AS market_odds,
            COALESCE(sel.model_probability, sel.raw_model_prob, td.predicted_win_prob) AS model_probability,
            COALESCE(sel.blended_prob, sel.win_prob_calibrated, sel.calibrated_win_prob, td.predicted_win_prob) AS predicted_win_prob,
            COALESCE(sel.expected_value, td.expected_value) AS expected_value,
            sel.speed_rating,
            sel.pace_score,
            sel.weighted_form_score,
            sel.franking_score,
            sel.ground_suitability,
            sel.distance_strike_rate,
            sel.running_style,
            COALESCE(sel.confidence, td.confidence) AS confidence,
            td.selection_id,
            CASE WHEN td.selection_id IS NOT NULL THEN 'training_data_live' ELSE 'training_data_imported' END AS prediction_source,
            CASE WHEN td.selection_id IS NOT NULL THEN 'live_model' ELSE 'imported_historical' END AS prediction_quality,
            td.archived_at AS source_timestamp
        FROM training_data td
        LEFT JOIN selections sel
          ON sel.id = td.selection_id
        WHERE td.race_date IS NOT NULL
          AND td.race_number IS NOT NULL
          AND td.horse_name IS NOT NULL
          AND td.predicted_win_prob IS NOT NULL
    ),
    prediction_audit_source AS (
        SELECT
            stride_norm_track(pa.track) AS track_norm,
            NULLIF(pa.race_date, '')::date AS race_date,
            pa.race_number,
            stride_norm_name(pa.horse_name) AS horse_name_norm,
            COALESCE(sel.market_odds, pa.market_odds) AS market_odds,
            COALESCE(sel.model_probability, sel.raw_model_prob, pa.predicted_win_prob) AS model_probability,
            COALESCE(sel.blended_prob, sel.win_prob_calibrated, sel.calibrated_win_prob, pa.predicted_win_prob) AS predicted_win_prob,
            sel.expected_value,
            sel.speed_rating,
            sel.pace_score,
            sel.weighted_form_score,
            sel.franking_score,
            sel.ground_suitability,
            sel.distance_strike_rate,
            sel.running_style,
            COALESCE(pa.confidence, sel.confidence) AS confidence,
            pa.selection_id,
            'prediction_audit'::text AS prediction_source,
            CASE WHEN pa.selection_id IS NOT NULL THEN 'live_model' ELSE 'audit_only' END AS prediction_quality,
            COALESCE(pa.result_collected_at, pa.created_at) AS source_timestamp
        FROM prediction_audit pa
        LEFT JOIN selections sel
          ON sel.id = pa.selection_id
        WHERE pa.race_date IS NOT NULL
          AND pa.race_number IS NOT NULL
          AND pa.horse_name IS NOT NULL
          AND pa.predicted_win_prob IS NOT NULL
    ),
    prediction_union AS (
        SELECT * FROM selection_source
        UNION ALL
        SELECT * FROM training_data_source
        UNION ALL
        SELECT * FROM prediction_audit_source
    ),
    prediction_best AS (
        SELECT *
        FROM (
            SELECT
                pu.*,
                ROW_NUMBER() OVER (
                    PARTITION BY pu.track_norm, pu.race_date, pu.race_number, pu.horse_name_norm
                    ORDER BY
                        CASE pu.prediction_quality
                            WHEN 'live_model' THEN 1
                            WHEN 'audit_only' THEN 2
                            WHEN 'imported_historical' THEN 3
                            ELSE 4
                        END,
                        pu.source_timestamp DESC NULLS LAST
                ) AS rn
            FROM prediction_union pu
        ) ranked
        WHERE rn = 1
    ),
    results_norm AS (
        SELECT
            r.*,
            stride_norm_track(r.track) AS track_norm,
            stride_norm_name(r.horse_name) AS horse_name_norm
        FROM race_results_history r
        WHERE r.position IS NOT NULL
    ),
    -- ROI task 04 (additive): tip_time odds snapshots, one aggregate row per
    -- runner. tip_time_odds is the cross-bookmaker median at capture time.
    -- Requires migrations/runner_odds_snapshots.sql to have been applied.
    odds_snap AS (
        SELECT
            s.race_date,
            stride_norm_track(s.track) AS track_norm,
            s.race_number,
            s.horse_name_norm,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY s.decimal_odds)::double precision AS tip_time_odds,
            (ARRAY_AGG(s.seconds_to_jump ORDER BY s.captured_at ASC))[1] AS seconds_to_jump
        FROM runner_odds_snapshots s
        WHERE s.snapshot_kind = 'tip_time'
          AND s.race_date IS NOT NULL
          AND s.horse_name_norm IS NOT NULL
        GROUP BY 1, 2, 3, 4
    )
    SELECT
        r.id AS result_id,
        r.horse_name,
        r.track,
        r.race_date,
        r.race_number,
        r.distance_m,
        r.race_class,
        r.class_level,
        r.going,
        r.position,
        r.margin_lengths,
        r.weight_kg,
        r.jockey,
        r.barrier,
        r.sp_odds,
        r.field_size,
        r.form_string,
        (r.position = 1) AS is_winner,
        (r.position <= 3) AS is_placed,
        p.market_odds,
        p.model_probability,
        p.predicted_win_prob,
        p.expected_value,
        p.speed_rating,
        p.pace_score,
        p.weighted_form_score,
        p.franking_score,
        p.ground_suitability,
        p.distance_strike_rate,
        p.running_style,
        p.confidence,
        p.prediction_source,
        p.prediction_quality,
        p.selection_id AS prediction_selection_id,
        -- ROI task 04 (additive only): the odds actually knowable at
        -- prediction time. The existing market_odds / sp_odds mapping above
        -- is deliberately untouched (retrain switch is task 12). odds_source
        -- mirrors the has_real_market_odds semantics at inference
        -- (run_tips_pipeline.evaluate_bet_candidate): 'snapshot' = captured
        -- tip_time quote exists, 'racecard' = live market quote carried by
        -- the prediction join, 'sp_fallback' = only SP available.
        osn.tip_time_odds,
        CASE
            WHEN osn.tip_time_odds IS NOT NULL THEN 'snapshot'
            WHEN p.market_odds IS NOT NULL THEN 'racecard'
            WHEN r.sp_odds IS NOT NULL THEN 'sp_fallback'
            ELSE NULL
        END AS odds_source,
        osn.seconds_to_jump,
        ls.z_200m AS prior_z_200m,
        ls.z_400m AS prior_z_400m,
        ls.z_600m AS prior_z_600m,
        ls.z_800m AS prior_z_800m,
        ls.lambda_decay AS prior_lambda_decay,
        ls.svi AS prior_svi,
        ls.rsi AS prior_rsi,
        ls.trip_cost_seconds AS prior_trip_cost_seconds,
        ls.variant_adjusted_200m AS prior_variant_adjusted_200m,
        ls.sectional_date AS prior_sectional_date
    FROM results_norm r
    LEFT JOIN prediction_best p
      ON p.race_date = r.race_date::date
     AND p.race_number = r.race_number
     AND p.horse_name_norm = r.horse_name_norm
     AND p.track_norm = r.track_norm
    LEFT JOIN odds_snap osn
      ON osn.race_date = r.race_date::date
     AND osn.race_number = r.race_number
     AND osn.track_norm = r.track_norm
     AND osn.horse_name_norm = r.horse_name_norm
    LEFT JOIN LATERAL (
        SELECT
            st.race_date AS sectional_date,
            st.z_200m,
            st.z_400m,
            st.z_600m,
            st.z_800m,
            st.lambda_decay,
            st.svi,
            st.rsi,
            st.trip_cost_seconds,
            st.variant_adjusted_200m
        FROM sectional_times st
        WHERE stride_norm_name(st.horse_name) = r.horse_name_norm
          AND st.race_date < r.race_date
          AND (st.lambda_decay IS NOT NULL OR st.z_200m IS NOT NULL)
        ORDER BY st.race_date DESC
        LIMIT 1
    ) ls ON TRUE;

    CREATE INDEX IF NOT EXISTS idx_training_view_v2_race
        ON training_view_v2 (race_date, track, race_number);

    CREATE INDEX IF NOT EXISTS idx_training_view_v2_horse
        ON training_view_v2 (horse_name, race_date);

    CREATE INDEX IF NOT EXISTS idx_training_view_v2_prediction_quality
        ON training_view_v2 (prediction_quality, prediction_source);
    """
).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild training_view_v2 from the best available historical prediction sources.")
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    return parser.parse_args()


def refresh_training_view_v2(db_url: str) -> dict[str, object]:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            conn = psycopg2.connect(db_url, connect_timeout=30)
            break
        except psycopg2.OperationalError as exc:
            last_error = exc
            if attempt == 3:
                raise
            time.sleep(2 * attempt)
    else:
        raise SystemExit(str(last_error))

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(DDL_FUNCTIONS)
                cur.execute(DDL_SUPPORT_INDEXES)
                cur.execute(DDL_TRAINING_VIEW)
                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS rows,
                        COUNT(model_probability) AS model_probability_rows,
                        COUNT(predicted_win_prob) AS predicted_win_prob_rows,
                        COUNT(expected_value) AS expected_value_rows,
                        COUNT(market_odds) AS market_odds_rows,
                        COUNT(tip_time_odds) AS tip_time_odds_rows
                    FROM training_view_v2
                    """
                )
                row = cur.fetchone()
                counts = {
                    "rows": row[0],
                    "model_probability_rows": row[1],
                    "predicted_win_prob_rows": row[2],
                    "expected_value_rows": row[3],
                    "market_odds_rows": row[4],
                    "tip_time_odds_rows": row[5],
                }
                cur.execute(
                    """
                    SELECT COALESCE(prediction_quality, 'none') AS prediction_quality, COUNT(*)
                    FROM training_view_v2
                    GROUP BY 1
                    ORDER BY 2 DESC
                    """
                )
                quality = cur.fetchall()
    finally:
        conn.close()

    return {
        "rows": counts["rows"],
        "model_probability_rows": counts["model_probability_rows"],
        "predicted_win_prob_rows": counts["predicted_win_prob_rows"],
        "expected_value_rows": counts["expected_value_rows"],
        "market_odds_rows": counts["market_odds_rows"],
        "tip_time_odds_rows": counts["tip_time_odds_rows"],
        "prediction_quality_breakdown": quality,
    }


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    args = parse_args()
    if not args.db_url:
        raise SystemExit("DATABASE_URL not set")

    result = refresh_training_view_v2(args.db_url)
    print("training_view_v2 rebuilt")
    print(
        {
            "rows": result["rows"],
            "model_probability_rows": result["model_probability_rows"],
            "predicted_win_prob_rows": result["predicted_win_prob_rows"],
            "expected_value_rows": result["expected_value_rows"],
            "market_odds_rows": result["market_odds_rows"],
            "tip_time_odds_rows": result["tip_time_odds_rows"],
        }
    )
    print("prediction_quality_breakdown", result["prediction_quality_breakdown"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
