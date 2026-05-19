from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import pandas as pd
import psycopg2
import psycopg2.extras


def load_database_url() -> str:
    env_path = Path(__file__).resolve().parents[4] / ".env"
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        return database_url
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                database_url = line.split("=", 1)[1].strip()
                break
    if not database_url:
        raise RuntimeError("DATABASE_URL not found in environment or project .env")
    os.environ["DATABASE_URL"] = database_url
    return database_url


class QueryLogger:
    def __init__(self, export_dir: Path) -> None:
        self.path = export_dir / "query_log.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        *,
        name: str,
        query: str,
        params: Optional[Dict[str, Any]] = None,
        row_count: Optional[int] = None,
        duration_ms: Optional[float] = None,
        note: Optional[str] = None,
    ) -> None:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "name": name,
            "query": query.strip(),
            "params": params or {},
            "row_count": row_count,
            "duration_ms": round(duration_ms or 0.0, 2),
            "note": note,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=True) + "\n")


@dataclass
class ResearchDB:
    export_dir: Path
    statement_timeout_ms: int = 120000

    def __post_init__(self) -> None:
        self.database_url = load_database_url()
        self.query_logger = QueryLogger(self.export_dir)
        self._conn = None

    @property
    def conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.database_url)
            self._conn.autocommit = True
            with self._conn.cursor() as cur:
                cur.execute(f"SET statement_timeout TO {int(self.statement_timeout_ms)}")
                cur.execute("SET idle_in_transaction_session_timeout TO 0")
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()

    def fetch_df(
        self,
        name: str,
        query: str,
        params: Optional[Dict[str, Any]] = None,
        note: Optional[str] = None,
    ) -> pd.DataFrame:
        start = time.time()
        df = pd.read_sql_query(query, self.conn, params=params)
        duration_ms = (time.time() - start) * 1000
        self.query_logger.log(
            name=name,
            query=query,
            params=params,
            row_count=len(df.index),
            duration_ms=duration_ms,
            note=note,
        )
        return df

    def fetch_rows(
        self,
        name: str,
        query: str,
        params: Optional[Dict[str, Any]] = None,
        note: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        start = time.time()
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params or {})
            rows = [dict(row) for row in cur.fetchall()]
        duration_ms = (time.time() - start) * 1000
        self.query_logger.log(
            name=name,
            query=query,
            params=params,
            row_count=len(rows),
            duration_ms=duration_ms,
            note=note,
        )
        return rows

    def execute_sql(
        self,
        name: str,
        query: str,
        params: Optional[Dict[str, Any]] = None,
        note: Optional[str] = None,
    ) -> None:
        start = time.time()
        with self.conn.cursor() as cur:
            cur.execute(query, params or {})
        duration_ms = (time.time() - start) * 1000
        self.query_logger.log(
            name=name,
            query=query,
            params=params,
            row_count=None,
            duration_ms=duration_ms,
            note=note,
        )


def csv_safe_write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def json_write(payload: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=True, default=str)


def markdown_write(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
