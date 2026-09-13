"""run_readonly_sql: the operator's escape hatch. Off unless STRIDE_CHAT_SQL_TOOL=true.

Not a default tool, for the reasons in the plan (§4): it contradicts the
injection suite, it hands the model joins the repository already solves, and
model-written SQL can pull pf_raw_payloads into the context or spend the
statement timeout on a cross join. When it is on, it is still one SELECT,
over an allowlist of tables, wrapped in a LIMIT, on the read-only role.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ..db import DatabaseUnavailable
from ._common import Context, ToolError, ToolSpec, cap_list, failure, miss, result

ALLOWED_TABLES = frozenset({
    "race_results_history", "sectional_times", "franking_scores", "selections",
    "selection_ledger", "selection_results", "stride_tip_results", "prediction_audit",
    "consensus_scores", "consensus_mentions", "market_signal_scores", "betfair_odds_snapshots",
    "runner_odds_snapshots", "race_schedule", "track_day_bias", "source_accuracy",
    "convergence_output", "blackbook_entries", "blackbook_entry_runs",
})
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|copy|call|vacuum|"
    r"analyze|analyse|lock|comment|security|reset|listen|notify|refresh|cluster|reindex|"
    r"pg_sleep|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|"
    r"dblink|pg_catalog|information_schema|pg_roles|pg_authid|pg_shadow|current_setting|"
    r"set_config)\b", re.IGNORECASE)
# Every identifier that follows FROM or JOIN. Subqueries are fine: their own
# FROM clauses are checked by the same scan.
TABLE_REF = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_.]*)", re.IGNORECASE)
ROW_LIMIT = 200


def validate(sql: str) -> str:
    text = str(sql or "").strip().rstrip(";").strip()
    if not text:
        raise ToolError("sql is empty")
    if ";" in text:
        raise ToolError("one statement only")
    if "--" in text or "/*" in text:
        raise ToolError("comments are not allowed")
    head = text.split(None, 1)[0].lower()
    if head not in ("select", "with"):
        raise ToolError("only SELECT (or WITH ... SELECT) is allowed")
    bad = FORBIDDEN.search(text)
    if bad:
        raise ToolError(f"{bad.group(0)!r} is not allowed")
    refs = {m.group(1).lower() for m in TABLE_REF.finditer(text)}
    # A CTE name referenced after FROM is not a table; strip declared CTEs.
    ctes = {m.group(1).lower() for m in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(",
                                                    text, re.IGNORECASE)}
    unknown = sorted(r for r in refs - ctes if r.split(".")[-1] not in ALLOWED_TABLES)
    if unknown:
        raise ToolError(f"tables not on the allowlist: {', '.join(unknown)}")
    return f"SELECT * FROM ({text}) q LIMIT {ROW_LIMIT}"


def run_readonly_sql(ctx: Context, sql: Any) -> Dict[str, Any]:
    if not ctx.sql_tool_enabled:
        return failure("run_readonly_sql is disabled on this deployment.", source="neon")
    wrapped = validate(sql)
    if ctx.db is None:
        return failure("No database is configured.", source="neon")
    try:
        rows = ctx.db.query(wrapped, ())
    except DatabaseUnavailable as e:
        return failure(f"The query failed: {e}", source="neon")
    if not rows:
        return miss("The query returned no rows.", source="neon")
    shown, more = cap_list(rows, ROW_LIMIT)
    return result({"rows": shown, "row_count": len(shown)}, source="neon", truncated=more)


SPEC = ToolSpec(
    name="run_readonly_sql",
    description=("Run one read-only SELECT over STRIDE's tables (operator escape hatch; "
                 "at most 200 rows). Prefer the typed tools; use this only when none fits."),
    input_schema={
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "A single SELECT statement."}},
        "required": ["sql"],
        "additionalProperties": False,
    },
    fn=run_readonly_sql,
)
