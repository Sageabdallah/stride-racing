"""STRIDE chat: the tool layer the chatbot runs on, as a library.

This package is the shared layer named in docs/chat/CHAT_INTEGRATION_PLAN.md:
typed tool functions over Neon, the S3 artifact relay and Punting Form, plus
the Anthropic tool loop and the prompt that drive them. It is deliberately
transport-agnostic. Nothing in here knows whether it is being called from a
Lambda handler, a FastAPI app, the CLI in chat/cli.py or an MCP server, and
nothing in here imports boto3, psycopg2 or anthropic at module scope: each is
imported inside the function that needs it, so the package imports cleanly in
CI (which installs none of them) and so a test can inject a fake in its place.

Layout:
  config.py      environment contract (model id, read-only DB URL, bucket)
  artifacts.py   day artifacts: S3 relay first, local checkout as fallback
  db.py          read-only Neon access with the timeouts run_tips_pipeline uses
  pf.py          Punting Form through pf_client, with a TTL cache and the wall
  tools/         the typed tools, named as evals/chat/golden.jsonl names them
  prompt.py      system prompt v3.0 (ported from stridePrompts.ts v2.2)
  loop.py        the Anthropic tool loop and the ChatCompletionResponse mapping
  contract.py    request validation and the response shape the React client reads
  session.py     conversation memory (12 messages, 30 minutes) behind a protocol
  cli.py         python -m chat.cli, for local runs and the eval runner
  eval_runner.py the golden and injection suites, ported from scripts/eval_chat.ts
"""

from __future__ import annotations

from . import _paths  # noqa: F401  (puts server/python on sys.path for the flat modules)

PROMPT_VERSION = "v3.0"

__all__ = ["PROMPT_VERSION"]
