"""Explicit schema contract between ``training_view_v2`` and retraining."""

from __future__ import annotations

from typing import Iterable, Tuple


TRAINING_VIEW_REQUIRED_COLUMNS: Tuple[str, ...] = (
    "race_date", "is_winner", "position", "margin_lengths",
    "horse_name", "track", "jockey", "race_number",
    "barrier", "distance_m", "weight_kg", "field_size", "class_level",
    "form_string", "sp_odds", "market_odds", "tip_time_odds",
    "odds_source", "seconds_to_jump", "predicted_win_prob",
    "weighted_form_score", "ground_suitability", "distance_strike_rate",
    "prior_z_200m", "prior_z_400m", "prior_z_600m", "prior_z_800m",
    "prior_lambda_decay", "prior_svi", "prior_rsi",
    "prior_trip_cost_seconds",
)


class TrainingViewSchemaError(RuntimeError):
    """The deployed materialized view cannot satisfy the retraining query."""


def relation_columns(conn, relation: str = "training_view_v2") -> Tuple[str, ...]:
    """Read ordered columns for a table, view, or materialized view."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT a.attname
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = %s
              AND n.nspname = ANY(current_schemas(false))
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            (relation,),
        )
        return tuple(row[0] for row in cur.fetchall())
    finally:
        cur.close()


def missing_required_columns(columns: Iterable[str]) -> Tuple[str, ...]:
    present = set(columns)
    return tuple(c for c in TRAINING_VIEW_REQUIRED_COLUMNS if c not in present)


def validate_training_view_schema(conn) -> Tuple[str, ...]:
    """Fail before data loading with every missing consumer column named."""
    columns = relation_columns(conn)
    if not columns:
        raise TrainingViewSchemaError("training_view_v2 does not exist or is not visible")
    missing = missing_required_columns(columns)
    if missing:
        raise TrainingViewSchemaError(
            "training_view_v2 is incompatible with retrain_v2; missing: "
            + ", ".join(missing)
            + ". Rebuild it with refresh_training_view_v2.py before retraining."
        )
    return columns
