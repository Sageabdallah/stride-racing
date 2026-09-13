"""Build the Context and the engine from the environment.

The one place the environment is turned into objects, so the CLI, the eval
runner and the Lambda handler (phase 3A) construct the same thing the same
way. Nothing here opens a connection: the database connects on first query,
the artifact store on first read, the Anthropic client on first turn.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from . import config
from .artifacts import ArtifactStore
from .db import Database
from .loop import ChatEngine
from .pf import PuntingForm
from .session import InMemorySessionStore, SessionStore
from .tools import Context


def build_context(today: Optional[str] = None, database: Optional[Any] = None,
                  artifacts: Optional[Any] = None, pf: Optional[Any] = None) -> Context:
    today = today or config.today_sydney()
    if database is None and config.database_url():
        database = Database.from_env()
    return Context(
        artifacts=artifacts if artifacts is not None else ArtifactStore.from_env(),
        pf=pf if pf is not None else PuntingForm(today=today),
        db=database,
        today=today,
        sql_tool_enabled=config.sql_tool_enabled(),
    )


def build_engine(ctx: Optional[Context] = None, client_factory: Optional[Callable[[], Any]] = None,
                 sessions: Optional[SessionStore] = None, model: Optional[str] = None) -> ChatEngine:
    ctx = ctx or build_context()
    kwargs = {"ctx": ctx, "sessions": sessions or InMemorySessionStore()}
    if client_factory is not None:
        kwargs["client_factory"] = client_factory
    if model:
        kwargs["model"] = model
    return ChatEngine(**kwargs)
