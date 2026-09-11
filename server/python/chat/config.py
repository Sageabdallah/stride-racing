"""The chat's environment contract, in one place.

Read lazily, never at import: a Lambda loads its secrets into the environment
after the module is imported, and a test sets what it needs with monkeypatch.

Two things are deliberately NOT the pipeline's variables:

* The database URL is STRIDE_CHAT_DATABASE_URL, the read-only role from
  migrations/chat_readonly_role.sql, never DATABASE_URL. The chat is the one
  component that faces untrusted input, and DATABASE_URL is the owner role.
  Locally, an operator who has not yet created the role can point
  STRIDE_CHAT_DATABASE_URL at anything they are willing to expose to a
  read-only session; db.py opens every session read-only regardless.
* The model id is ANTHROPIC_CHAT_MODEL, which infra/01_secrets.sh has shipped
  since the chat plan was first written and nothing read until now.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

DEFAULT_CHAT_MODEL = "claude-opus-5"

# Hard limits. These are contract, not tuning: the request cap is what
# inj-size-01 asserts (a 4,001-character message is HTTP 400), the round cap is
# the loop's spend bound, and the result cap keeps a tool from pushing a
# 130,000-row table into the context.
MAX_MESSAGE_CHARS = 4000
MAX_TOOL_ROUNDS = 8
MAX_TOOL_RESULT_CHARS = 24000
MAX_OUTPUT_TOKENS = 8000

# Session memory: the constants strideChatService.ts uses.
SESSION_TTL_SECONDS = 30 * 60
SESSION_MAX_MESSAGES = 12

# Punting Form Starter serves about 31 days back (pf_window.py measures the
# wall at runtime; this is the documented figure used to answer without
# spending a call that would 400).
PUNTINGFORM_WINDOW_DAYS = 31


def chat_model() -> str:
    return (os.environ.get("ANTHROPIC_CHAT_MODEL") or "").strip() or DEFAULT_CHAT_MODEL


def chat_effort() -> str | None:
    """Optional output_config.effort. Unset means the API default."""
    value = (os.environ.get("STRIDE_CHAT_EFFORT") or "").strip().lower()
    return value or None


def database_url() -> str | None:
    return (os.environ.get("STRIDE_CHAT_DATABASE_URL") or "").strip() or None


def artifact_bucket() -> str | None:
    return (os.environ.get("STRIDE_EVIDENCE_BUCKET") or "").strip() or None


def sql_tool_enabled() -> bool:
    """run_readonly_sql is an operator escape hatch, off unless asked for."""
    return (os.environ.get("STRIDE_CHAT_SQL_TOOL") or "").strip().lower() in ("1", "true", "yes")


def _sydney_tz():
    """Australia/Sydney when tzdata is present; the estate's fixed +10 otherwise.

    infra/jobs/handler.py uses a fixed +10 and says so ("display only;
    schedules own DST"). A chat asked "what's on today" at 23:30 in daylight
    time would be a day behind on +10, so prefer the real zone and fall back
    to the estate's convention rather than to UTC.
    """
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Australia/Sydney")
    except Exception:  # tzdata absent on a slim image
        return timezone(timedelta(hours=10))


def today_sydney() -> str:
    forced = (os.environ.get("STRIDE_DATE") or "").strip()
    if forced:
        datetime.strptime(forced, "%Y-%m-%d")  # a typo is not a request
        return forced
    return datetime.now(_sydney_tz()).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class Limits:
    max_message_chars: int = MAX_MESSAGE_CHARS
    max_tool_rounds: int = MAX_TOOL_ROUNDS
    max_tool_result_chars: int = MAX_TOOL_RESULT_CHARS
    max_output_tokens: int = MAX_OUTPUT_TOKENS
