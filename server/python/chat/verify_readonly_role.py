#!/usr/bin/env python3
"""Prove the chat's database role cannot write.

    STRIDE_CHAT_DATABASE_URL=postgresql://stride_chat_ro:...@host/db python -m chat.verify_readonly_role

Connects AS THE CHAT ROLE and tries to write. Exit 0 only if every attempt
is refused for lack of privilege. The apply-migration workflow runs this in
the same job that creates the role, so the role exists in the account only
alongside evidence that it is what the migration says it is.

Why the proof is shaped the way it is. The migration sets
default_transaction_read_only = on for the role, and a naive test would
INSERT under that default, see the transaction refuse (SQLSTATE 25006) and
call the role read-only. That check passes for a role with full write
grants, which is the exact failure it is meant to catch: a setting any
session can override standing in for a privilege. So phase B opens an
explicit READ WRITE transaction first and then writes. Only "permission
denied" (SQLSTATE 42501) counts there. Everything runs inside transactions
that are rolled back, so a check that unexpectedly succeeds leaves no row
and no table behind.

Offline: the checks are data (CHECKS), the executor takes any connection
with cursor(), and chat/tests cover the verdict logic with a fake.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

INSUFFICIENT_PRIVILEGE = "42501"
READ_ONLY_TRANSACTION = "25006"
DEFAULT_ROLE = "stride_chat_ro"

# (name, sql, accepted SQLSTATEs). An empty tuple means the statement must
# succeed. Phase A runs under the role's own defaults; phase B first forces
# a read-write transaction so only a missing privilege can refuse the write.
PHASE_A: List[Tuple[str, str, Tuple[str, ...]]] = [
    ("select works", "SELECT 1 AS one", ()),
    ("reads a real table", "SELECT 1 FROM race_results_history LIMIT 1", ()),
    ("insert refused under defaults",
     "INSERT INTO selections (track, race_number, race_date, horse_name, win_percentage, model_probability) "
     "VALUES ('__stride_chat_ro_probe__', 1, '1900-01-01', '__probe__', 0, 0)",
     (INSUFFICIENT_PRIVILEGE, READ_ONLY_TRANSACTION)),
]
PHASE_B: List[Tuple[str, str, Tuple[str, ...]]] = [
    ("insert refused in a READ WRITE transaction",
     "INSERT INTO selections (track, race_number, race_date, horse_name, win_percentage, model_probability) "
     "VALUES ('__stride_chat_ro_probe__', 1, '1900-01-01', '__probe__', 0, 0)",
     (INSUFFICIENT_PRIVILEGE,)),
    ("update refused in a READ WRITE transaction",
     "UPDATE selections SET track = track WHERE FALSE", (INSUFFICIENT_PRIVILEGE,)),
    ("delete refused in a READ WRITE transaction",
     "DELETE FROM selection_ledger WHERE FALSE", (INSUFFICIENT_PRIVILEGE,)),
    ("insert into race_results_history refused",
     "INSERT INTO race_results_history (horse_id, horse_name, race_id, track, race_date) "
     "VALUES ('__probe__', '__probe__', '__probe__', '__probe__', '1900-01-01')",
     (INSUFFICIENT_PRIVILEGE,)),
    ("create table refused",
     "CREATE TABLE __stride_chat_ro_probe (x int)", (INSUFFICIENT_PRIVILEGE,)),
]
PRIVILEGE_CHECKS = [
    ("selections", "SELECT", True), ("selections", "INSERT", False), ("selections", "UPDATE", False),
    ("selections", "DELETE", False), ("race_results_history", "SELECT", True),
    ("race_results_history", "INSERT", False), ("selection_ledger", "SELECT", True),
    ("selection_ledger", "UPDATE", False),
]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    hard: bool = True


@dataclass
class Report:
    role: str
    checks: List[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks if c.hard)

    def add(self, name: str, ok: bool, detail: str = "", hard: bool = True) -> None:
        self.checks.append(Check(name, ok, detail, hard))

    def text(self) -> str:
        lines = [f"read-only proof for role {self.role}:"]
        for c in self.checks:
            mark = "ok  " if c.ok else ("FAIL" if c.hard else "warn")
            lines.append(f"  {mark} {c.name}" + (f" ({c.detail})" if c.detail else ""))
        lines.append("VERDICT: " + ("PASS: the role cannot write" if self.passed
                                    else "FAIL: the role is not read-only as the migration intends"))
        return "\n".join(lines)


def _exec(cur, sql: str):
    """(rows, sqlstate, message). Rows are None for a statement with no result."""
    try:
        cur.execute(sql)
    except Exception as e:  # noqa: BLE001 - any driver error is a verdict input
        return None, getattr(e, "pgcode", None) or "", str(e).strip().split("\n")[0]
    try:
        rows = cur.fetchall()
    except Exception:  # noqa: BLE001 - no result set
        rows = None
    return rows, None, None


def _attempt(cur, report: Report, name: str, sql: str, accepted: Tuple[str, ...]) -> None:
    """Run one statement inside a savepoint so a refusal does not abort the
    transaction for the checks that follow."""
    _exec(cur, "SAVEPOINT probe")
    rows, code, msg = _exec(cur, sql)
    if code is not None:
        _exec(cur, "ROLLBACK TO SAVEPOINT probe")
    else:
        _exec(cur, "RELEASE SAVEPOINT probe")
    if not accepted:
        report.add(name, code is None, msg or "")
        return
    if code is None:
        report.add(name, False, "the statement SUCCEEDED (rolled back); the role can write")
    elif code in accepted:
        report.add(name, True, f"refused with SQLSTATE {code}")
    else:
        report.add(name, False, f"refused, but with SQLSTATE {code or '?'} ({msg}); expected {'/'.join(accepted)}")


def verify(conn, role: str = DEFAULT_ROLE) -> Report:
    report = Report(role=role)
    conn.autocommit = False

    # Phase A: the role's own defaults.
    with conn.cursor() as cur:
        rows, code, msg = _exec(cur, "SELECT current_user AS u")
        user = (rows[0][0] if rows and rows[0] else None) if isinstance(rows[0], (tuple, list)) else \
            (rows[0].get("u") if rows and isinstance(rows[0], dict) else None)
        report.add("connected as the chat role", user == role, f"current_user={user!r}")
        rows, code, msg = _exec(cur, "SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication "
                                     "FROM pg_roles WHERE rolname = current_user")
        flags = list(rows[0]) if rows and not isinstance(rows[0], dict) else \
            (list(rows[0].values()) if rows else [])
        report.add("no superuser/createrole/createdb/bypassrls/replication",
                   bool(flags) and not any(flags), f"flags={flags}")
        rows, code, msg = _exec(cur, "SHOW default_transaction_read_only")
        val = (rows[0][0] if rows and not isinstance(rows[0], dict) else
               (next(iter(rows[0].values())) if rows else None))
        report.add("default_transaction_read_only is on", str(val).lower() == "on", f"value={val!r}")
        for name, sql, accepted in PHASE_A:
            _attempt(cur, report, name, sql, accepted)
        for table, priv, expected in PRIVILEGE_CHECKS:
            rows, code, msg = _exec(cur, f"SELECT has_table_privilege(current_user, '{table}', '{priv}') AS p")
            got = (rows[0][0] if rows and not isinstance(rows[0], dict) else
                   (rows[0].get("p") if rows else None))
            report.add(f"has_table_privilege({table}, {priv}) is {str(expected).lower()}",
                       code is None and bool(got) == expected,
                       f"got={got!r}" + (f" error={msg}" if code else ""))
    conn.rollback()

    # Phase B: an explicit READ WRITE transaction, so the session default
    # cannot be what refuses the write.
    with conn.cursor() as cur:
        rows, code, msg = _exec(cur, "SET TRANSACTION READ WRITE")
        report.add("could open a READ WRITE transaction (so the next refusals are privilege, not mode)",
                   code is None, msg or "")
        for name, sql, accepted in PHASE_B:
            _attempt(cur, report, name, sql, accepted)
    conn.rollback()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="Connection string (default STRIDE_CHAT_DATABASE_URL).")
    parser.add_argument("--role", default=DEFAULT_ROLE, help="Expected current_user.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    url = args.url or os.environ.get("STRIDE_CHAT_DATABASE_URL", "").strip()
    if not url:
        print("STRIDE_CHAT_DATABASE_URL is not set (or pass --url).", file=sys.stderr)
        return 2
    import psycopg2
    try:
        conn = psycopg2.connect(url, connect_timeout=10, application_name="stride-chat-ro-proof")
    except Exception as e:  # noqa: BLE001
        print(f"could not connect as the chat role: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    try:
        report = verify(conn, role=args.role)
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
    if args.json:
        print(json.dumps({"role": report.role, "passed": report.passed,
                          "checks": [c.__dict__ for c in report.checks]}, indent=2))
    else:
        print(report.text())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
