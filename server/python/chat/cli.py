"""python -m chat.cli: the chat from a terminal, and the tools without the chat.

    python -m chat.cli "What did STRIDE tip at Randwick on 12 April 2026?"
    python -m chat.cli --brain --json "How did our top pick at Flemington go on 2026-04-11?"
    python -m chat.cli --repl                      # one session, follow-ups work
    python -m chat.cli --tool get_stride_tips --args '{"date": "2026-04-12", "track": "Randwick"}'
    python -m chat.cli --preflight                 # is ANTHROPIC_CHAT_MODEL callable?

--tool runs one tool against the configured data plane and prints its
envelope. It costs no Anthropic tokens, which makes it the way to prove the
data plane before spending on a turn, and the shape of the chat-proof smoke
in phase 3A: a tool call that returns rows is evidence; prose is not.

Environment: STRIDE_CHAT_DATABASE_URL (read-only role), STRIDE_EVIDENCE_BUCKET
(optional; local artifacts otherwise), PUNTINGFORM_API_KEY (only when a tool
reaches Punting Form), ANTHROPIC_API_KEY and ANTHROPIC_CHAT_MODEL for turns.
A .env at the repository root is loaded if python-dotenv is installed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

from . import PROMPT_VERSION, config
from ._paths import REPO_ROOT
from .contract import ChatRequestError, parse_request
from .loop import ChatModelUnavailable, ChatTurnError
from .runtime import build_context, build_engine
from .tools import dispatch


def _load_dotenv() -> None:
    path = os.path.join(REPO_ROOT, ".env")
    if not os.path.isfile(path):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=False)
    except ImportError:
        pass


def _print_turn(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, default=str))
        return
    print(result["response"])
    tools = result.get("toolCalls") or []
    usage = result.get("usage") or {}
    print("", file=sys.stderr)
    print(f"[tools: {', '.join(tools) or 'none'} | rounds: {usage.get('rounds')} | "
          f"in {usage.get('input_tokens')} out {usage.get('output_tokens')} "
          f"cache-read {usage.get('cache_read_input_tokens')} | prompt {result.get('promptVersion')}]",
          file=sys.stderr)
    for w in result.get("warnings") or []:
        print(f"[warning] {w}", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m chat.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("message", nargs="?", help="The question. Omit with --repl, --tool or --preflight.")
    parser.add_argument("--date", help="Override today's date (YYYY-MM-DD), like STRIDE_DATE.")
    parser.add_argument("--brain", action="store_true", help="Deep Thought mode.")
    parser.add_argument("--search", action="store_true", help="Request search mode (not available here; warns).")
    parser.add_argument("--session", help="Session id for follow-ups across invocations of --repl.")
    parser.add_argument("--track", help="raceContext track.")
    parser.add_argument("--race", type=int, help="raceContext race number.")
    parser.add_argument("--json", action="store_true", help="Print the full ChatCompletionResponse.")
    parser.add_argument("--repl", action="store_true", help="Interactive session.")
    parser.add_argument("--tool", help="Run one tool by name and print its envelope (no Anthropic call).")
    parser.add_argument("--args", default="{}", help="JSON arguments for --tool.")
    parser.add_argument("--preflight", action="store_true", help="Check the chat model is callable.")
    parser.add_argument("--model", help="Model id for this run (default ANTHROPIC_CHAT_MODEL).")
    args = parser.parse_args(argv)

    _load_dotenv()
    if args.date:
        os.environ["STRIDE_DATE"] = args.date
    ctx = build_context()

    if args.tool:
        try:
            tool_args = json.loads(args.args)
        except ValueError as e:
            print(f"--args is not JSON: {e}", file=sys.stderr)
            return 2
        envelope = dispatch(ctx, args.tool, tool_args)
        print(json.dumps(envelope, indent=2, default=str))
        return 0 if envelope.get("ok") else 1

    engine = build_engine(ctx, model=args.model)
    if args.preflight:
        try:
            engine.preflight()
        except ChatModelUnavailable as e:
            print(f"PREFLIGHT FAILED: {e}", file=sys.stderr)
            return 3
        print(f"preflight ok: model {engine.model}, prompt {PROMPT_VERSION}, "
              f"today {ctx.today}, data plane {ctx.artifacts.describe()}, "
              f"database {'configured' if ctx.db is not None else 'NOT configured'}")
        return 0

    race_context = None
    if args.track and args.race:
        race_context = {"date": ctx.today, "track": args.track, "raceNumber": args.race}

    def one_turn(message: str, session_id: str) -> int:
        try:
            request = parse_request({"message": message, "sessionId": session_id,
                                     "modes": {"brain": args.brain, "search": args.search},
                                     "raceContext": race_context})
        except ChatRequestError as e:
            print(f"bad request: {e}", file=sys.stderr)
            return 2
        try:
            result = engine.run_turn(request)
        except ChatTurnError as e:
            print(f"turn failed: {e}", file=sys.stderr)
            return 4
        _print_turn(result, args.json)
        return 0

    if args.repl:
        session_id = args.session or f"cli-{uuid.uuid4().hex[:8]}"
        print(f"STRIDE chat ({engine.model}, prompt {PROMPT_VERSION}, today {ctx.today}). "
              f"Session {session_id}. Ctrl-D to exit.", file=sys.stderr)
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("", file=sys.stderr)
                return 0
            if not line:
                continue
            one_turn(line, session_id)

    if not args.message:
        parser.print_usage(sys.stderr)
        return 2
    return one_turn(args.message, args.session or f"cli-{uuid.uuid4().hex[:8]}")


if __name__ == "__main__":
    sys.exit(main())
