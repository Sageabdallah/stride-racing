"""The tool loop: one chat turn, from request to ChatCompletionResponse.

Shape of a turn:

  1. history (text only) + the user's message
  2. messages.create with the cached system block, the tool definitions and
     adaptive thinking; no temperature (rejected on current models)
  3. stop_reason tool_use: run every tool_use block, return every result in
     ONE user message (a failed tool is is_error=true, never dropped), go to 2
  4. stop_reason end_turn: the text is the answer
     max_tokens: keep the partial text and warn
     refusal: a fixed decline, and warn
     pause_turn: resend and continue
  5. at most MAX_TOOL_ROUNDS rounds; past that, answer with what there is

The Anthropic client is created by a factory so the SDK is imported only
when a real turn runs; tests pass a fake with the same two attributes the
loop touches (messages.create and the response's content, stop_reason and
usage). Assistant content blocks are passed back to the API as the SDK
returned them, which is what carries thinking blocks across a tool round.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import PROMPT_VERSION
from .config import Limits, chat_effort, chat_model
from .contract import ChatRequest, ToolCallRecord, build_response
from .prompt import system_blocks
from .session import InMemorySessionStore, SessionStore
from .tools import Context, active_specs, api_tools, dispatch
from .tools._common import frame_for_model

REFUSAL_TEXT = ("I can't help with that one. I can only help with Australian racing and "
                "STRIDE's records; ask me about a race, a horse, a result or how STRIDE has gone.")
REQUEST_TIMEOUT_SECONDS = 120.0


class ChatModelUnavailable(RuntimeError):
    """The configured model cannot be called. Fatal by design at cold start."""


class ChatTurnError(RuntimeError):
    """The turn could not be completed (API failure). The transport maps it to 500."""


def default_client_factory() -> Callable[[], Any]:
    def make():
        import anthropic  # lazy: not installed in CI
        return anthropic.Anthropic()
    return make


def preflight_model(client: Any, model: str, effort: Optional[str] = None) -> None:
    """One tiny call with the same parameter shape as a real turn.

    consensus_agent.preflight_extraction_model exists because a retired id
    and a rejected parameter both took weeks to notice. The chat gets the
    same check, with its own parameter shape (adaptive thinking, cached
    system block, a tool definition), so a change to any of them is loud
    at deploy rather than silent per turn.
    """
    kwargs: Dict[str, Any] = dict(
        model=model, max_tokens=16, thinking={"type": "adaptive"}, timeout=30,
        system=[{"type": "text", "text": "Reply with the single word ok.",
                 "cache_control": {"type": "ephemeral"}}],
        tools=[{"name": "noop", "description": "Does nothing.",
                "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}}],
        messages=[{"role": "user", "content": "ok"}],
    )
    if effort:
        kwargs["output_config"] = {"effort": effort}
    try:
        client.messages.create(**kwargs)
    except Exception as e:  # noqa: BLE001
        raise ChatModelUnavailable(
            f"chat model {model!r} is not callable: {type(e).__name__}: {e}. "
            f"Set ANTHROPIC_CHAT_MODEL to a live model id.") from e


def _block_type(block: Any) -> str:
    return getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else "") or ""


def _block_attr(block: Any, name: str) -> Any:
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


def _text_of(content: List[Any]) -> str:
    return "".join((_block_attr(b, "text") or "") for b in (content or []) if _block_type(b) == "text").strip()


def _usage_of(response: Any) -> Dict[str, int]:
    u = getattr(response, "usage", None)
    out = {}
    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        v = _block_attr(u, k) if u is not None else None
        out[k] = int(v or 0)
    return out


@dataclass
class ChatEngine:
    ctx: Context
    client_factory: Callable[[], Any] = field(default_factory=default_client_factory)
    model: str = field(default_factory=chat_model)
    effort: Optional[str] = field(default_factory=chat_effort)
    sessions: SessionStore = field(default_factory=InMemorySessionStore)
    limits: Limits = field(default_factory=Limits)
    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS
    _client: Any = field(default=None, repr=False)

    def client(self) -> Any:
        if self._client is None:
            self._client = self.client_factory()
        return self._client

    def preflight(self) -> None:
        preflight_model(self.client(), self.model, self.effort)

    # -- the turn ----------------------------------------------------------

    def run_turn(self, request: ChatRequest) -> Dict[str, Any]:
        started = time.time()
        specs = active_specs(self.ctx)
        tools = api_tools(specs)
        system = system_blocks(self.ctx.today, request.brain, request.search,
                               request.race_context, request.message)
        messages: List[Dict[str, Any]] = self.sessions.history(request.session_id)
        messages.append({"role": "user", "content": request.message})

        warnings: List[str] = []
        if request.search:
            warnings.append("Web search is not available on this backend; answered from STRIDE's records.")
        calls: List[ToolCallRecord] = []
        usage = {"input_tokens": 0, "output_tokens": 0,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        text = ""
        answer_source = "local"
        rounds = 0

        while True:
            rounds += 1
            response = self._create(system, tools, messages)
            for k, v in _usage_of(response).items():
                usage[k] += v
            content = list(getattr(response, "content", None) or [])
            stop = getattr(response, "stop_reason", None)
            text = _text_of(content) or text

            if stop == "tool_use":
                uses = [b for b in content if _block_type(b) == "tool_use"]
                messages.append({"role": "assistant", "content": content})
                results = []
                for b in uses:
                    record, framed = self._run_tool(b, specs)
                    calls.append(record)
                    results.append({"type": "tool_result", "tool_use_id": _block_attr(b, "id"),
                                    "content": framed, "is_error": not record.ok})
                messages.append({"role": "user", "content": results})
                if rounds >= self.limits.max_tool_rounds:
                    warnings.append(f"Stopped after {rounds} tool rounds; the answer may be incomplete.")
                    # One last call with no tools, so the model must write an answer.
                    response = self._create(system, [], messages)
                    for k, v in _usage_of(response).items():
                        usage[k] += v
                    text = _text_of(list(getattr(response, "content", None) or [])) or text
                    break
                continue

            if stop == "pause_turn":
                messages.append({"role": "assistant", "content": content})
                if rounds >= self.limits.max_tool_rounds:
                    warnings.append("The model paused repeatedly; the answer may be incomplete.")
                    break
                continue

            if stop == "max_tokens":
                warnings.append("The answer was cut short by the output limit.")
            elif stop == "refusal":
                text = REFUSAL_TEXT
                warnings.append("The model declined this request.")
            break

        if not text.strip():
            text = ("I couldn't put together an answer for that. Ask again with a date, a track "
                    "or a horse name and I'll pull the records.")
            warnings.append("The model returned no text.")

        self.sessions.append(request.session_id, request.message, text)
        result = build_response(text, request, calls, warnings, usage, rounds,
                                answer_source=answer_source, model=self.model)
        self._log(request, calls, usage, rounds, started)
        return result

    # -- pieces ------------------------------------------------------------

    def _create(self, system: List[Dict[str, Any]], tools: List[Dict[str, Any]],
                messages: List[Dict[str, Any]]) -> Any:
        kwargs: Dict[str, Any] = dict(
            model=self.model, max_tokens=self.limits.max_output_tokens, system=system,
            messages=messages, thinking={"type": "adaptive"}, timeout=self.timeout_seconds,
        )
        if tools:
            kwargs["tools"] = tools
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        try:
            return self.client().messages.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            raise ChatTurnError(f"model call failed: {type(e).__name__}: {e}") from e

    def _run_tool(self, block: Any, specs) -> "tuple[ToolCallRecord, str]":
        name = str(_block_attr(block, "name") or "")
        raw = _block_attr(block, "input")
        args = raw if isinstance(raw, dict) else {}
        if isinstance(raw, str):
            try:
                args = json.loads(raw)
            except ValueError:
                args = {}
        t0 = time.time()
        envelope = dispatch(self.ctx, name, args, specs)
        ms = int((time.time() - t0) * 1000)
        record = ToolCallRecord(
            name=name, args=args, ok=bool(envelope.get("ok")), found=bool(envelope.get("found")),
            source=str(envelope.get("source") or ""), ms=ms, error=envelope.get("error"),
            notes=[str(n) for n in (envelope.get("notes") or [])])
        return record, frame_for_model(name, envelope, self.limits.max_tool_result_chars)

    def _log(self, request: ChatRequest, calls: List[ToolCallRecord], usage: Dict[str, int],
             rounds: int, started: float) -> None:
        # One structured line per turn, low-cardinality only: never a horse or
        # race dimension, never the message text.
        line = {"event": "chat_turn", "model": self.model, "prompt_version": PROMPT_VERSION,
                "rounds": rounds, "tools": [c.name for c in calls],
                "tool_errors": sum(1 for c in calls if not c.ok),
                "tool_misses": sum(1 for c in calls if c.ok and not c.found),
                "usage": usage, "ms": int((time.time() - started) * 1000),
                "brain": request.brain, "search": request.search,
                "session": bool(request.session_id)}
        print(json.dumps(line, sort_keys=True), file=sys.stderr)
