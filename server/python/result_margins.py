#!/usr/bin/env python3
"""Beaten margins for readers of race_results_history.

Two writers, two conventions. The importers that filled the table until
2026-07-13 stored NULL in margin_lengths for the winner. pf_results_mapper,
which has written every row since 2026-06-30, stores what Punting Form
sends: the winning margin. Every reader was written for the first
convention, in which a winner's margin contributes nothing. Read under the
second, a horse that won by three lengths looks three lengths beaten:
form_franking's pairwise Elo scores winner and runner-up as a dead heat,
build_prep_cycles averages the win into the prep trend as lost ground, and
form_feature_builder's win-margin bonus fires only for wins after June
2026, which the model never trained on.

beaten_margin() returns the margin as the readers have always meant it:
lengths behind the winner, undefined (None) for the winner. Apply it at the
read boundary. The stored column is left exactly as written, so nothing is
lost: the winning margin is the runner-up's beaten margin, which is how
mc_api already reads it, and the raw payload is archived in pf_raw_payloads.

Using the winning margin as a signal is a feature change, not a repair, and
belongs behind the walk-forward gate like any other model-facing change.
"""
from __future__ import annotations

from typing import Any, Optional


def beaten_margin(position: Any, margin_lengths: Any) -> Optional[float]:
    """Lengths behind the winner; None for the winner or when unknown."""
    if margin_lengths is None:
        return None
    try:
        if int(position) == 1:
            return None
    except (TypeError, ValueError):
        pass
    try:
        value = float(margin_lengths)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return value


def opponent_beaten_margin(opponent: dict) -> Optional[float]:
    """The same rule for an opponents_json entry ({position, margin, ...})."""
    if not isinstance(opponent, dict):
        return None
    return beaten_margin(opponent.get("position"), opponent.get("margin"))


def beaten_margins_frame(df):
    """A copy of a runs DataFrame with winners' margin_lengths set to NaN.

    Returns the frame unchanged (no copy) when there is nothing to do, so
    the historical rows, which already carry NULL for winners, cost nothing.
    """
    if df is None or getattr(df, "empty", True):
        return df
    columns = getattr(df, "columns", [])
    if "position" not in columns or "margin_lengths" not in columns:
        return df
    import pandas as pd

    position = pd.to_numeric(df["position"], errors="coerce")
    mask = (position == 1) & df["margin_lengths"].notna()
    if not bool(mask.any()):
        return df
    out = df.copy()
    out.loc[mask, "margin_lengths"] = float("nan")
    return out
