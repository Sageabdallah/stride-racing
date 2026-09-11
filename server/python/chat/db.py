"""Read-only Neon access for the chat tools.

Same shape as run_tips_pipeline.db_connect: a five-second connect timeout and
a fifteen-second statement timeout, so a hung query fails the turn instead of
the Lambda. Two more things the pipeline does not do, because the chat faces
untrusted input:

* Every session is opened read-only on the client side as well
  (conn.set_session(readonly=True)). The role from
  migrations/chat_readonly_role.sql has no write privilege, which is the real
  boundary; this is the belt over those braces and costs nothing.
* The URL is STRIDE_CHAT_DATABASE_URL, never DATABASE_URL (config.py).

psycopg2 is imported inside connect() so the module imports without it. Rows
come back as plain dicts, which is what the tools serialise and what a fake
in the tests returns.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import config

CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_MS = 15000


class DatabaseUnavailable(RuntimeError):
    """No URL configured, or the connection or query failed."""


@dataclass
class Database:
    """One lazily-opened connection, reopened once on failure.

    A Lambda container that stays warm reuses it across turns; a container
    that Neon has disconnected in the meantime gets one reconnect, not a
    retry storm.
    """

    url: Optional[str]
    application_name: str = "stride-chat"
    _conn: Any = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> "Database":
        return cls(url=config.database_url())

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def _open(self):
        if not self.url:
            raise DatabaseUnavailable(
                "STRIDE_CHAT_DATABASE_URL is not set; the chat has no database")
        import psycopg2  # lazy
        import psycopg2.extras
        try:
            conn = psycopg2.connect(
                self.url,
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
                application_name=self.application_name,
            )
        except Exception as e:  # noqa: BLE001
            raise DatabaseUnavailable(f"connect failed: {type(e).__name__}: {e}") from e
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout TO {STATEMENT_TIMEOUT_MS}")
        return conn

    def _connection(self):
        if self._conn is None or getattr(self._conn, "closed", 0):
            self._conn = self._open()
        return self._conn

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        """Run one read and return rows as dicts.

        A failure closes the connection so the next call reconnects; the
        error is re-raised as DatabaseUnavailable with the driver's own text,
        which the tool turns into an honest "the database did not answer".
        """
        import psycopg2.extras  # lazy
        last_err: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                conn = self._connection()
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, tuple(params))
                    return [dict(r) for r in cur.fetchall()]
            except DatabaseUnavailable:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                self.close()
                # A privilege or syntax error is a fact about the query and
                # comes back identical on retry; only retry connection loss.
                if not _looks_like_connection_loss(e):
                    break
                print(f"[chat.db] attempt {attempt} failed: {type(e).__name__}: {e}",
                      file=sys.stderr)
        raise DatabaseUnavailable(f"query failed: {type(last_err).__name__}: {last_err}")

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _looks_like_connection_loss(err: Exception) -> bool:
    name = type(err).__name__
    if name in ("OperationalError", "InterfaceError"):
        return True
    text = str(err).lower()
    return "server closed the connection" in text or "connection" in text and "lost" in text


def relation_missing(err: Exception) -> bool:
    """True when the failure is 'relation does not exist' (SQLSTATE 42P01).

    lookup_horse reads blackbook_entries, which stride-app owns and creates at
    runtime. Its absence is a fact to report, not a failure of the horse
    lookup, and this is how the tool tells the two apart.
    """
    code = getattr(err, "pgcode", None)
    if code == "42P01":
        return True
    return "does not exist" in str(err) and "relation" in str(err)
