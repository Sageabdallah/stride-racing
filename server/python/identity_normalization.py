"""Canonical STRIDE identity keys shared by ingestion, snapshots and SQL.

These functions deliberately normalise identity only. They do not merge
distinct racing circuits (for example Sandown Hillside and Lakeside).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from textwrap import dedent
from typing import Any


_COUNTRY_SUFFIX = re.compile(r"\([A-Z]{2,3}\)\s*$", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_alnum_key(value: Any) -> str:
    """Return a stable lowercase ASCII letters-and-digits key."""
    return _NON_ALNUM.sub("", str(value or "").strip().lower())


def normalize_runner_key(value: Any) -> str:
    """Runner identity, excluding a trailing country suffix such as ``(NZ)``."""
    without_country = _COUNTRY_SUFFIX.sub("", str(value or "").strip())
    return normalize_alnum_key(without_country)


def normalize_track_key(value: Any) -> str:
    """Physical-track key with the existing, explicitly approved aliases."""
    key = normalize_alnum_key(value)
    exact = {
        "royalrandwick": "randwick",
        "randwick": "randwick",
        "randwickkensington": "kensington",
        "kensington": "kensington",
        "rosehillgardens": "rosehill",
        "rosehill": "rosehill",
        "ascot": "ascotwa",
        "ascotwa": "ascotwa",
    }
    if key in exact:
        return exact[key]
    contains = (
        ("mooneevalley", "mooneevalley"),
        ("eaglefarm", "eaglefarm"),
        ("morphettville", "morphettville"),
        ("caulfield", "caulfield"),
        ("flemington", "flemington"),
        ("shatin", "shatin"),
        ("happyvalley", "happyvalley"),
    )
    for fragment, canonical in contains:
        if fragment in key:
            return canonical
    return key


def normalize_race_number(value: Any) -> int:
    """Return a positive integer race number or raise ``ValueError``."""
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid race number: {value!r}") from exc
    if number < 1:
        raise ValueError(f"invalid race number: {value!r}")
    return number


def canonical_race_key(race_date: Any, track: Any, race_number: Any) -> str:
    """Canonical date/track/race key used across snapshot and replay paths."""
    if isinstance(race_date, (date, datetime)):
        date_key = race_date.isoformat()[:10]
    else:
        date_key = str(race_date or "").strip()[:10]
    if not date_key:
        raise ValueError("race date is required")
    return f"{date_key}|{normalize_track_key(track)}|R{normalize_race_number(race_number)}"


def source_race_key(race_date: Any, track: Any, race_number: Any) -> str:
    """Legacy-compatible stored race ID without cross-source track aliasing.

    Existing snapshot primary keys use the source spelling normalised to
    alphanumeric text. Joins use the canonical component columns; changing
    historical ID shape in Phase −1 would fork those append-only records.
    """
    if isinstance(race_date, (date, datetime)):
        date_key = race_date.isoformat()[:10]
    else:
        date_key = str(race_date or "").strip()[:10]
    if not date_key:
        raise ValueError("race date is required")
    return f"{date_key}|{normalize_alnum_key(track)}|R{normalize_race_number(race_number)}"


# PostgreSQL regex ranges are case-sensitive. Lower-casing before deleting
# characters prevents uppercase letters from silently disappearing.
DDL_FUNCTIONS = dedent(
    r"""
    CREATE OR REPLACE FUNCTION stride_norm_name(src text)
    RETURNS text
    LANGUAGE sql
    IMMUTABLE
    AS $$
      SELECT regexp_replace(
        lower(regexp_replace(btrim(coalesce(src, '')), '\([a-z]{2,3}\)\s*$', '', 'i')),
        '[^a-z0-9]+', '', 'g'
      );
    $$;

    CREATE OR REPLACE FUNCTION stride_norm_track(src text)
    RETURNS text
    LANGUAGE sql
    IMMUTABLE
    AS $$
      WITH normalised AS (
        SELECT regexp_replace(lower(btrim(coalesce(src, ''))), '[^a-z0-9]+', '', 'g') AS key
      )
      SELECT CASE
        WHEN key IN ('royalrandwick', 'randwick') THEN 'randwick'
        WHEN key IN ('randwickkensington', 'kensington') THEN 'kensington'
        WHEN key IN ('rosehillgardens', 'rosehill') THEN 'rosehill'
        WHEN key IN ('ascot', 'ascotwa') THEN 'ascotwa'
        WHEN key LIKE '%mooneevalley%' THEN 'mooneevalley'
        WHEN key LIKE '%eaglefarm%' THEN 'eaglefarm'
        WHEN key LIKE '%morphettville%' THEN 'morphettville'
        WHEN key LIKE '%caulfield%' THEN 'caulfield'
        WHEN key LIKE '%flemington%' THEN 'flemington'
        WHEN key LIKE '%shatin%' THEN 'shatin'
        WHEN key LIKE '%happyvalley%' THEN 'happyvalley'
        ELSE key
      END
      FROM normalised;
    $$;
    """
).strip()
