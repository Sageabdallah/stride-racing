"""The tool registry: the surface the model sees, in a fixed order.

Order matters twice. It is the order the tool definitions are rendered in
the request, so a stable order is what lets the prompt cache hold across
turns; and it is the order the evals name them in, which is a small kindness
to anyone reading a trace. run_readonly_sql joins the list only when the
context says the operator turned it on.

dispatch() is the one place a tool is called from the loop. It never raises:
a bad argument, a dead backend or an unexpected exception all come back as
an envelope with ok=False, which the loop turns into a tool_result with
is_error=true, so the model can say what happened rather than the turn
dying.
"""

from __future__ import annotations

import sys
import traceback
from typing import Any, Dict, List

from ..db import DatabaseUnavailable
from ._common import Context, ToolError, ToolSpec, failure
from . import consensus, horse, market, performance, puntingform, racecard, readonly_sql, results, tips

CORE_SPECS: List[ToolSpec] = [
    tips.SPEC, racecard.SPEC, horse.SPEC, results.SPEC, performance.SPEC,
    consensus.SPEC, market.SPEC, puntingform.SPEC,
]
OPTIONAL_SPECS: List[ToolSpec] = [readonly_sql.SPEC]


def active_specs(ctx: Context) -> List[ToolSpec]:
    specs = list(CORE_SPECS)
    if ctx.sql_tool_enabled:
        specs.extend(OPTIONAL_SPECS)
    return specs


def api_tools(specs: List[ToolSpec]) -> List[Dict[str, Any]]:
    """The `tools` parameter, with the cache breakpoint on the last one so the
    whole definition block is one cached prefix."""
    out = [s.to_api() for s in specs]
    if out:
        out[-1] = dict(out[-1], cache_control={"type": "ephemeral"})
    return out


def spec_by_name(name: str, specs: List[ToolSpec]) -> ToolSpec:
    for s in specs:
        if s.name == name:
            return s
    raise KeyError(name)


def dispatch(ctx: Context, name: str, args: Dict[str, Any], specs: List[ToolSpec] = None) -> Dict[str, Any]:
    specs = specs if specs is not None else active_specs(ctx)
    try:
        spec = spec_by_name(name, specs)
    except KeyError:
        return failure(f"unknown tool {name!r}")
    if not isinstance(args, dict):
        return failure(f"{name}: arguments must be an object")
    try:
        return spec.fn(ctx, **args)
    except ToolError as e:
        return failure(f"{name}: {e}")
    except TypeError as e:
        # An argument the schema does not declare, or a missing required one.
        return failure(f"{name}: bad arguments: {e}")
    except DatabaseUnavailable as e:
        return failure(f"{name}: the database did not answer: {e}", source="neon")
    except Exception as e:  # noqa: BLE001 - the loop must survive any tool
        print(f"[chat.tools] {name} raised {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return failure(f"{name} failed: {type(e).__name__}: {e}")


__all__ = ["CORE_SPECS", "OPTIONAL_SPECS", "Context", "ToolSpec", "active_specs",
           "api_tools", "dispatch", "spec_by_name"]
