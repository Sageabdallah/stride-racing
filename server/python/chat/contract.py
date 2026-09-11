"""The wire contract: what comes in, what goes out.

Mirrors stride-app/shared/schema.ts so ChatInterface.tsx and ChatTracePanel.tsx
need no change:

  in   ChatCompletionRequest   message, sessionId?, turnId?, modes?, raceContext?
  out  ChatCompletionResponse  response, mode, answerSource, trace, citations,
                               warnings, turnId?, promptVersion?

Two additive fields are appended to the response and documented in the plan
(§5): `toolCalls`, the tool names called this turn in order (what the eval
harness asserts tools_any_of against), and `usage`, the token accounting the
operator pays for. Old clients ignore both.

The link audit at the bottom is the anti-phishing invariant from the eval
suite (inj-link-*) enforced in code: a markdown link whose URL is not in
citations[] is reduced to its label text and reported in warnings. This
backend has no citations, so every link is stripped.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import PROMPT_VERSION
from .config import MAX_MESSAGE_CHARS


class ChatRequestError(ValueError):
    """The request is malformed. The transport maps this to HTTP 400."""
    status = 400


@dataclass
class ChatRequest:
    message: str
    session_id: Optional[str] = None
    turn_id: Optional[str] = None
    brain: bool = False
    search: bool = False
    race_context: Optional[Dict[str, Any]] = None


@dataclass
class ToolCallRecord:
    name: str
    args: Dict[str, Any]
    ok: bool = True
    found: bool = True
    source: str = ""
    ms: int = 0
    error: Optional[str] = None
    notes: List[str] = field(default_factory=list)


def _bool(value: Any, name: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise ChatRequestError(f"{name} must be a boolean")


def parse_request(payload: Any) -> ChatRequest:
    if not isinstance(payload, dict):
        raise ChatRequestError("request body must be a JSON object")
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ChatRequestError("message must be a non-empty string")
    if len(message) > MAX_MESSAGE_CHARS:
        raise ChatRequestError(f"message is too long ({len(message)} chars; max {MAX_MESSAGE_CHARS})")
    session_id = payload.get("sessionId")
    if session_id is not None and (not isinstance(session_id, str) or len(session_id) > 128):
        raise ChatRequestError("sessionId must be a string of at most 128 characters")
    turn_id = payload.get("turnId")
    if turn_id is not None and not isinstance(turn_id, str):
        raise ChatRequestError("turnId must be a string")
    modes = payload.get("modes") or {}
    if not isinstance(modes, dict):
        raise ChatRequestError("modes must be an object")
    rc = payload.get("raceContext")
    race_context = None
    if rc is not None:
        if not isinstance(rc, dict):
            raise ChatRequestError("raceContext must be an object")
        for k in ("date", "track"):
            if not isinstance(rc.get(k), str) or not rc.get(k).strip():
                raise ChatRequestError(f"raceContext.{k} must be a non-empty string")
        rn = rc.get("raceNumber")
        if not isinstance(rn, int) or isinstance(rn, bool) or rn < 1:
            raise ChatRequestError("raceContext.raceNumber must be a positive integer")
        race_context = {k: rc.get(k) for k in ("date", "track", "raceNumber", "raceName",
                                               "distance", "going") if rc.get(k) is not None}
    return ChatRequest(
        message=message.strip(),
        session_id=session_id.strip() if isinstance(session_id, str) and session_id.strip() else None,
        turn_id=turn_id,
        brain=_bool(modes.get("brain"), "modes.brain"),
        search=_bool(modes.get("search"), "modes.search"),
        race_context=race_context,
    )


# -- link audit ------------------------------------------------------------

_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)")
_BARE_URL = re.compile(r"(?<![\(\w])https?://[^\s)\]]+")


def _normalize_url(u: str) -> str:
    return u.strip().rstrip("/").lower()


def strip_unverified_links(text: str, citations: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """Reduce every link not in citations[] to its label; return removed URLs."""
    allowed = {_normalize_url(c.get("url", "")) for c in citations if isinstance(c, dict)}
    removed: List[str] = []

    def _md(m: "re.Match[str]") -> str:
        url = m.group(2)
        if _normalize_url(url) in allowed:
            return m.group(0)
        removed.append(url)
        return m.group(1)

    out = _MD_LINK.sub(_md, text or "")

    def _bare(m: "re.Match[str]") -> str:
        url = m.group(0)
        trail = ""
        while url and url[-1] in ".,;:!?":  # sentence punctuation is not part of the URL
            trail = url[-1] + trail
            url = url[:-1]
        if _normalize_url(url) in allowed:
            return url + trail
        removed.append(url)
        return "[link removed]" + trail

    out = _BARE_URL.sub(_bare, out)
    return out, removed


# -- trace and response ----------------------------------------------------

def _args_summary(args: Dict[str, Any]) -> str:
    parts = []
    for k, v in (args or {}).items():
        s = str(v)
        parts.append(f"{k}={s[:40]}" + ("..." if len(s) > 40 else ""))
    return ", ".join(parts)


def build_trace(brain: bool, tool_calls: List[ToolCallRecord], warnings: List[str],
                rounds: int, answer_text: str) -> Dict[str, Any]:
    called = [t for t in tool_calls]
    sources = []
    for t in called:
        if t.source and t.source not in sources:
            sources.append(t.source)
    steps = [f"Called {t.name}({_args_summary(t.args)}): "
             + ("no answer from the source" if not t.ok else
                ("found" if t.found else "nothing found")) + f" in {t.ms} ms"
             for t in called]
    misses = [t for t in called if t.ok and not t.found]
    errors = [t for t in called if not t.ok]
    label = "Deep Thought Summary" if brain else "Answer flow"
    if not called:
        headline = "Answered without consulting the records."
    elif errors:
        headline = f"Consulted {len(called)} tool call{'s' if len(called) != 1 else ''}; {len(errors)} source{'s' if len(errors) != 1 else ''} did not answer."
    else:
        headline = f"Consulted STRIDE's records through {len(called)} tool call{'s' if len(called) != 1 else ''} over {rounds} round{'s' if rounds != 1 else ''}."
    summary = (answer_text or "").strip().split("\n")[0][:240]
    return {
        "label": label,
        "headline": headline,
        "summary": summary,
        "sections": [
            {"id": "thinking", "kind": "thinking", "title": "What it focused on",
             "summary": ("Deep Thought cross-checked the pick against results, consensus and market."
                         if brain else "Resolved the question to the tools that hold the answer."),
             "bullets": [f"Tools chosen: {', '.join(dict.fromkeys(t.name for t in called))}."] if called
             else ["No tool was needed for this turn."]},
            {"id": "steps", "kind": "steps", "title": "What it did",
             "summary": f"{len(called)} tool call{'s' if len(called) != 1 else ''} in {rounds} round{'s' if rounds != 1 else ''}.",
             "bullets": steps or ["Answered directly."]},
            {"id": "sources", "kind": "sources", "title": "Sources and tools",
             "summary": (f"This turn read {len(sources)} source{'s' if len(sources) != 1 else ''}."
                         if sources else "This turn read no source."),
             "bullets": sources + list(warnings)},
            {"id": "why", "kind": "why", "title": "Why it landed here",
             "summary": ("Some of what was asked is not in the records; the answer says so."
                         if misses else "The answer is drawn from the tool results above."),
             "bullets": ([f"{t.name}: {n}" for t in misses for n in t.notes[:1]] or
                         [f"{t.name}: {t.error}" for t in errors] or
                         ["Grounded in the tool results; nothing was inferred beyond them."])},
        ],
        "searchQueries": [],
        "searchResults": [],
        "finalWhy": (misses[0].notes[0] if misses and misses[0].notes else
                     (errors[0].error if errors else "Grounded in STRIDE's records.")),
    }


def build_response(text: str, request: ChatRequest, tool_calls: List[ToolCallRecord],
                   warnings: List[str], usage: Dict[str, int], rounds: int,
                   answer_source: str = "local", citations: Optional[List[Dict[str, Any]]] = None,
                   model: str = "") -> Dict[str, Any]:
    citations = citations or []
    clean, removed = strip_unverified_links(text, citations)
    warnings = list(warnings)
    if removed:
        warnings.append(f"Removed {len(removed)} unverified link{'s' if len(removed) != 1 else ''} from the answer.")
    return {
        "response": clean,
        "mode": {"brain": bool(request.brain), "search": bool(request.search)},
        "answerSource": answer_source,
        "trace": build_trace(request.brain, tool_calls, warnings, rounds, clean),
        "citations": citations,
        "warnings": warnings,
        "turnId": request.turn_id or uuid.uuid4().hex,
        "promptVersion": PROMPT_VERSION,
        # Additive (plan §5): what the eval harness and the operator read.
        "toolCalls": [t.name for t in tool_calls],
        "usage": dict(usage, rounds=rounds, model=model),
    }
