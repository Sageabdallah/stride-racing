"""get_performance: strike rate and P&L, from the ledger first.

selection_ledger is the one place bets are settled net of commission with
both prices, so it answers first. stride_tip_results, which settles gross at
the tipped odds, answers only when the ledger has nothing in the window, and
the tool says which one it used. Grouping is by a fixed set of dimensions;
there is no free-form grouping expression and the model cannot supply one.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..db import DatabaseUnavailable
from ._common import (Context, ToolError, ToolSpec, cap_list, failure, miss, norm_track,
                      parse_iso_date, result)

WINDOWS = ("this_year", "last_7_days", "last_30_days", "last_90_days", "all", "custom")
GROUPS = ("overall", "track", "confidence", "odds_band", "month", "going")

# Group expressions per source. Fixed strings, never built from input.
LEDGER_GROUP = {
    "overall": "'all'",
    "track": "track",
    "confidence": "COALESCE(confidence, 'unknown')",
    "odds_band": ("CASE WHEN price_taken IS NULL THEN 'no price' "
                  "WHEN price_taken < 2.5 THEN 'short (< $2.50)' "
                  "WHEN price_taken < 5 THEN '$2.50 to $4.99' "
                  "WHEN price_taken < 10 THEN '$5 to $9.99' "
                  "ELSE 'double figures ($10+)' END"),
    "month": "to_char(race_date, 'YYYY-MM')",
}
TIPRES_GROUP = {
    "overall": "'all'",
    "track": "track",
    "confidence": "COALESCE(confidence, 'unknown')",
    "odds_band": ("CASE WHEN tipped_odds IS NULL THEN 'no price' "
                  "WHEN tipped_odds < 2.5 THEN 'short (< $2.50)' "
                  "WHEN tipped_odds < 5 THEN '$2.50 to $4.99' "
                  "WHEN tipped_odds < 10 THEN '$5 to $9.99' "
                  "ELSE 'double figures ($10+)' END"),
    "month": "substr(race_date, 1, 7)",
    "going": "COALESCE(going, 'unknown')",
}


def _window(ctx: Context, window: str, date_from: Any, date_to: Any) -> Tuple[str, str]:
    today = datetime.strptime(ctx.today, "%Y-%m-%d") if ctx.today else datetime.utcnow()
    if window == "custom":
        if not date_from or not date_to:
            raise ToolError("window=custom needs date_from and date_to")
        a, b = parse_iso_date(date_from, "date_from"), parse_iso_date(date_to, "date_to")
        if b < a:
            raise ToolError("date_to is before date_from")
        return a, b
    end = today.strftime("%Y-%m-%d")
    if window == "this_year":
        return today.strftime("%Y-01-01"), end
    if window == "last_7_days":
        return (today - timedelta(days=7)).strftime("%Y-%m-%d"), end
    if window == "last_30_days":
        return (today - timedelta(days=30)).strftime("%Y-%m-%d"), end
    if window == "last_90_days":
        return (today - timedelta(days=90)).strftime("%Y-%m-%d"), end
    return "2000-01-01", end


def _ledger(db, group_by: str, a: str, b: str) -> List[Dict[str, Any]]:
    expr = LEDGER_GROUP[group_by]
    return db.query(
        f"SELECT {expr} AS bucket, COUNT(*) AS bets, "
        "SUM(CASE WHEN won THEN 1 ELSE 0 END) AS wins, "
        "SUM(COALESCE(stake, 0)) AS staked, "
        "SUM(COALESCE(settled_pnl, pnl, 0)) AS net_pnl, "
        "AVG(clv_pct) AS avg_clv_pct, AVG(price_taken) AS avg_price_taken "
        "FROM selection_ledger WHERE settled = TRUE AND refused = FALSE "
        "AND COALESCE(should_bet, TRUE) = TRUE AND race_date >= %s::date AND race_date <= %s::date "
        "GROUP BY 1 ORDER BY bets DESC LIMIT 60", (a, b))


def _ledger_context(db, a: str, b: str) -> Dict[str, Any]:
    rows = db.query(
        "SELECT SUM(CASE WHEN refused THEN 1 ELSE 0 END) AS refused_races, "
        "SUM(CASE WHEN NOT refused AND COALESCE(should_bet, TRUE) = FALSE THEN 1 ELSE 0 END) AS coverage_only, "
        "SUM(CASE WHEN NOT refused AND COALESCE(should_bet, TRUE) AND NOT settled THEN 1 ELSE 0 END) AS unsettled "
        "FROM selection_ledger WHERE race_date >= %s::date AND race_date <= %s::date", (a, b))
    return dict(rows[0]) if rows else {}


def _tip_results(db, group_by: str, a: str, b: str) -> List[Dict[str, Any]]:
    expr = TIPRES_GROUP[group_by]
    return db.query(
        f"SELECT {expr} AS bucket, COUNT(*) AS bets, "
        "SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) AS wins, "
        "SUM(CASE WHEN result IN ('WIN', 'PLACE') THEN 1 ELSE 0 END) AS placed, "
        "COUNT(*) AS staked, SUM(COALESCE(profit_loss, 0)) AS gross_pnl, "
        "AVG(tipped_odds) AS avg_tipped_odds "
        "FROM stride_tip_results WHERE tip_type = 'BET' AND result IN ('WIN', 'PLACE', 'LOSS') "
        "AND race_date >= %s AND race_date <= %s GROUP BY 1 ORDER BY bets DESC LIMIT 60", (a, b))


def _shape(rows: List[Dict[str, Any]], pnl_key: str, group_by: str) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        bets = int(r.get("bets") or 0)
        wins = int(r.get("wins") or 0)
        staked = float(r.get("staked") or 0)
        pnl = float(r.get(pnl_key) or 0)
        row = {
            "bucket": (norm_track(r.get("bucket")) if group_by == "track" else r.get("bucket")),
            "bets": bets, "wins": wins,
            "strike_rate_pct": round(100.0 * wins / bets, 1) if bets else None,
            "staked_units": round(staked, 2),
            "pnl_units": round(pnl, 2),
            "roi_pct": round(100.0 * pnl / staked, 1) if staked else None,
        }
        if group_by == "track":
            row["track"] = r.get("bucket")
        for k in ("placed", "avg_clv_pct", "avg_price_taken", "avg_tipped_odds"):
            if r.get(k) is not None:
                row[k] = round(float(r[k]), 2)
        out.append(row)
    return out


def get_performance(ctx: Context, window: Any = "this_year", group_by: Any = "overall",
                    date_from: Any = None, date_to: Any = None) -> Dict[str, Any]:
    window = str(window or "this_year").strip().lower()
    group_by = str(group_by or "overall").strip().lower()
    if window not in WINDOWS:
        raise ToolError(f"window must be one of {', '.join(WINDOWS)}")
    if group_by not in GROUPS:
        raise ToolError(f"group_by must be one of {', '.join(GROUPS)}")
    a, b = _window(ctx, window, date_from, date_to)
    if ctx.db is None:
        return failure("No database is configured, so performance figures are unavailable.",
                       source="neon")
    try:
        if group_by in LEDGER_GROUP:
            rows = _ledger(ctx.db, group_by, a, b)
            if rows and any(int(r.get("bets") or 0) for r in rows):
                shaped, more = cap_list(_shape(rows, "net_pnl", group_by), 40)
                data = {"window": window, "date_from": a, "date_to": b, "group_by": group_by,
                        "rows": shaped, "context": _ledger_context(ctx.db, a, b)}
                return result(data, source="neon:selection_ledger", truncated=more, notes=[
                    "Bets only (should_bet true, not refused, settled). P&L is net of commission "
                    "at the recorded rate; staked and pnl are in units.",
                    "Coverage-only picks and refused races are counted in context, not in rows."])
        rows = _tip_results(ctx.db, group_by, a, b)
    except DatabaseUnavailable as e:
        return failure(f"The database did not answer: {e}", source="neon:selection_ledger")
    if not rows or not any(int(r.get("bets") or 0) for r in rows):
        return miss(f"No settled STRIDE bets between {a} and {b} in the ledger or the tip results.",
                    source="neon:selection_ledger,stride_tip_results")
    shaped, more = cap_list(_shape(rows, "gross_pnl", group_by), 40)
    data = {"window": window, "date_from": a, "date_to": b, "group_by": group_by, "rows": shaped}
    return result(data, source="neon:stride_tip_results", truncated=more, notes=[
        "From stride_tip_results (BET tips settled WIN/PLACE/LOSS). P&L is GROSS at the tipped "
        "odds, one unit per bet, not net of commission; the ledger had no settled bets for this "
        + ("grouping" if group_by == "going" else "window") + "."])


SPEC = ToolSpec(
    name="get_performance",
    description=(
        "STRIDE's betting performance: bets, wins, strike rate, units staked, P&L and ROI, "
        "grouped overall or by track, confidence tier, odds band, month or going, over a "
        "window (this_year, last_7_days, last_30_days, last_90_days, all, or custom with "
        "date_from and date_to). Ledger figures are net of commission."),
    input_schema={
        "type": "object",
        "properties": {
            "window": {"type": "string", "enum": list(WINDOWS)},
            "group_by": {"type": "string", "enum": list(GROUPS)},
            "date_from": {"type": "string", "description": "YYYY-MM-DD, with window=custom."},
            "date_to": {"type": "string", "description": "YYYY-MM-DD, with window=custom."},
        },
        "required": [],
        "additionalProperties": False,
    },
    fn=get_performance,
)
