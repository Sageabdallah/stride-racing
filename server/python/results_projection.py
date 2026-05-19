#!/usr/bin/env python3
"""
Projection helpers for canonical race result ingestion.

Canonical source table: prediction_audit
Derived compatibility tables: race_schedule, analytics/training
"""

from typing import Dict, Optional


def ensure_race_schedule_from_prediction_audit(conn, race_date: Optional[str] = None) -> int:
    """
    Seed race_schedule from pending prediction_audit rows.
    This keeps auto_results_collector as the only fetch/match layer.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO race_schedule (
                track, race_number, race_date, off_time, result_due_at, result_status
            )
            SELECT
                src.track,
                src.race_number,
                src.race_date,
                src.off_time_ts,
                src.result_due_at,
                'pending'
            FROM (
                SELECT
                    pa.track,
                    pa.race_number,
                    pa.race_date,
                    MAX(
                        CASE
                            WHEN pa.off_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                            THEN pa.off_time::timestamptz
                            ELSE NULL
                        END
                    ) AS off_time_ts,
                    COALESCE(
                        MAX(
                            CASE
                                WHEN pa.off_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                                THEN pa.off_time::timestamptz + INTERVAL '30 minutes'
                                ELSE NULL
                            END
                        ),
                        NOW()
                    ) AS result_due_at
                FROM prediction_audit pa
                WHERE COALESCE(pa.result_status, 'pending') = 'pending'
                  AND (%s IS NULL OR pa.race_date = %s)
                GROUP BY pa.track, pa.race_number, pa.race_date
            ) src
            WHERE NOT EXISTS (
                SELECT 1
                FROM race_schedule rs
                WHERE rs.track = src.track
                  AND rs.race_number = src.race_number
                  AND rs.race_date = src.race_date
            )
            """,
            (race_date, race_date),
        )
        return max(cur.rowcount, 0)


def project_resulted_prediction_audit(
    conn,
    race_date: Optional[str] = None,
    project_selection_results: bool = True,
    project_training_data: bool = True,
) -> Dict[str, int]:
    """Materialize derived analytics/training tables from prediction_audit."""
    counts = {"selection_results_inserted": 0, "training_data_inserted": 0}

    with conn.cursor() as cur:
        if project_selection_results:
            cur.execute(
                """
                INSERT INTO selection_results (
                    selection_id, race_id, track, race_number, race_date, horse_name,
                    predicted_win_prob, predicted_place_prob, market_odds, opening_odds,
                    confidence, edge, actual_position, field_size, won, placed,
                    starting_price, stake, return_amount, profit_loss,
                    steam_drift_pct, is_sharp_money, market_move_category
                )
                SELECT
                    pa.selection_id,
                    COALESCE(s.race_id::text, pa.selection_id) AS race_id,
                    pa.track,
                    pa.race_number,
                    pa.race_date,
                    pa.horse_name,
                    pa.predicted_win_prob,
                    pa.predicted_place_prob,
                    pa.market_odds,
                    s.opening_odds,
                    pa.confidence,
                    pa.edge,
                    pa.actual_position,
                    pa.field_size,
                    COALESCE(pa.won, false) AS won,
                    COALESCE(pa.placed, false) AS placed,
                    pa.starting_price,
                    100.0::double precision AS stake,
                    CASE
                        WHEN COALESCE(pa.won, false) AND COALESCE(pa.starting_price, 0) > 0
                        THEN 100.0::double precision * pa.starting_price
                        ELSE 0.0::double precision
                    END AS return_amount,
                    COALESCE(
                        pa.profit_loss,
                        CASE
                            WHEN COALESCE(pa.won, false) AND COALESCE(pa.starting_price, 0) > 0
                            THEN (100.0::double precision * pa.starting_price) - 100.0::double precision
                            ELSE -100.0::double precision
                        END
                    ) AS profit_loss,
                    s.steam_drift_pct,
                    s.is_sharp_money,
                    s.market_move_category
                FROM prediction_audit pa
                LEFT JOIN selections s
                  ON s.id = pa.selection_id
                WHERE COALESCE(pa.result_status, 'pending') = 'resulted'
                  AND pa.actual_position IS NOT NULL
                  AND pa.selection_id IS NOT NULL
                  AND (%s IS NULL OR pa.race_date = %s)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM selection_results sr
                      WHERE sr.selection_id = pa.selection_id
                        AND sr.race_date = pa.race_date
                  )
                """,
                (race_date, race_date),
            )
            counts["selection_results_inserted"] = max(cur.rowcount, 0)

        if project_training_data:
            cur.execute(
                """
                INSERT INTO training_data (
                    id, race_id, track, race_number, race_date, distance,
                    going, race_class, horse_name, horse_number, barrier,
                    jockey, trainer, weight, predicted_win_prob, predicted_place_prob,
                    predicted_position, confidence, expected_value, market_odds,
                    actual_position, won, placed, starting_price, archived_at, selection_id
                )
                SELECT
                    gen_random_uuid()::text AS id,
                    COALESCE(
                        s.race_id::text,
                        r_meta.race_id,
                        CONCAT(pa.track, '_', pa.race_date, '_R', pa.race_number)
                    ) AS race_id,
                    pa.track,
                    pa.race_number,
                    pa.race_date,
                    COALESCE(s.distance, r_meta.distance, '') AS distance,
                    COALESCE(r_meta.going, '') AS going,
                    COALESCE(r_meta.race_class, '') AS race_class,
                    pa.horse_name,
                    s.horse_number,
                    CASE
                        WHEN s.barrier ~ '^[0-9]+$' THEN s.barrier::integer
                        ELSE NULL
                    END AS barrier,
                    s.jockey,
                    s.trainer,
                    NULL::double precision AS weight,
                    pa.predicted_win_prob,
                    pa.predicted_place_prob,
                    s.expected_position,
                    pa.confidence,
                    s.expected_value,
                    pa.market_odds,
                    pa.actual_position,
                    CASE WHEN COALESCE(pa.won, false) THEN 1 ELSE 0 END AS won,
                    CASE WHEN COALESCE(pa.placed, false) THEN 1 ELSE 0 END AS placed,
                    pa.starting_price,
                    NOW() AS archived_at,
                    pa.selection_id
                FROM prediction_audit pa
                LEFT JOIN selections s
                  ON s.id = pa.selection_id
                LEFT JOIN LATERAL (
                    SELECT r.race_id, r.distance, r.going, r.race_class
                    FROM races r
                    WHERE LOWER(r.track) = LOWER(pa.track)
                      AND r.race_number = pa.race_number
                      AND r.race_date = pa.race_date
                    ORDER BY r.updated_at DESC NULLS LAST, r.created_at DESC NULLS LAST
                    LIMIT 1
                ) r_meta ON TRUE
                WHERE COALESCE(pa.result_status, 'pending') = 'resulted'
                  AND pa.actual_position IS NOT NULL
                  AND (%s IS NULL OR pa.race_date = %s)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM training_data td
                      WHERE td.race_date = pa.race_date
                        AND td.race_number = pa.race_number
                        AND LOWER(td.track) = LOWER(pa.track)
                        AND LOWER(td.horse_name) = LOWER(pa.horse_name)
                  )
                """,
                (race_date, race_date),
            )
            counts["training_data_inserted"] = max(cur.rowcount, 0)

    return counts
