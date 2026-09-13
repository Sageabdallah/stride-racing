"""The write-proof's verdict logic, against a scripted connection."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from chat.verify_readonly_role import verify


class PGError(Exception):
    def __init__(self, pgcode, msg):
        super().__init__(msg)
        self.pgcode = pgcode


class FakeCursor:
    def __init__(self, script: List[Tuple[str, object]], log: List[str]):
        self.script = script
        self.log = log
        self._rows = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        self.log.append(sql)
        for pattern, outcome in self.script:
            if re.search(pattern, sql, re.IGNORECASE):
                if isinstance(outcome, Exception):
                    raise outcome
                self._rows = outcome
                return
        self._rows = None

    def fetchall(self):
        if self._rows is None:
            raise RuntimeError("no results")
        return self._rows


class FakeConn:
    def __init__(self, script):
        self.script = script
        self.log: List[str] = []
        self.rollbacks = 0
        self.autocommit = True

    def cursor(self):
        return FakeCursor(self.script, self.log)

    def rollback(self):
        self.rollbacks += 1


def good_role_script(create_ok: bool = False):
    denied = PGError("42501", "permission denied for table selections")
    return [
        (r"^SELECT current_user", [("stride_chat_ro",)]),
        (r"FROM pg_roles", [(False, False, False, False, False)]),
        (r"^SHOW default_transaction_read_only", [("on",)]),
        (r"^SELECT 1 AS one", [(1,)]),
        (r"FROM race_results_history LIMIT 1", [(1,)]),
        (r"has_table_privilege\(current_user, '\w+', 'SELECT'\)", [(True,)]),
        (r"has_table_privilege\(current_user, '\w+', '(INSERT|UPDATE|DELETE)'\)", [(False,)]),
        (r"^INSERT INTO selections", PGError("25006", "cannot execute INSERT in a read-only transaction")),
        (r"^INSERT INTO race_results_history", denied),
        (r"^UPDATE ", denied), (r"^DELETE ", denied),
        (r"^CREATE TABLE", None if create_ok else denied),
        (r"^SET TRANSACTION READ WRITE", None),
        (r"SAVEPOINT|RELEASE|ROLLBACK", None),
    ]


def test_role_that_cannot_write_passes():
    conn = FakeConn(good_role_script())
    # Phase B inserts see 42501 once the transaction is read-write: script it.
    conn.script.insert(0, (r"^INSERT INTO selections", PGError("42501", "permission denied")))
    report = verify(conn)
    assert report.passed, report.text()
    assert conn.autocommit is False and conn.rollbacks == 2
    assert "SET TRANSACTION READ WRITE" in conn.log
    from chat.verify_readonly_role import PHASE_A, PHASE_B
    assert conn.log.count("SAVEPOINT probe") == len(PHASE_A) + len(PHASE_B)


def test_read_only_default_alone_does_not_pass():
    """A role with full grants but default_transaction_read_only=on: phase A
    refuses with 25006, phase B (READ WRITE) lets the INSERT through."""
    script = [(r"^INSERT INTO selections", PGError("25006", "read-only transaction"))] + good_role_script()
    script = [(p, o) for p, o in script if not re.search("has_table_privilege", p)]
    script.insert(0, (r"has_table_privilege\(current_user, '\w+', '(INSERT|UPDATE|DELETE)'\)", [(True,)]))
    script.insert(0, (r"has_table_privilege\(current_user, '\w+', 'SELECT'\)", [(True,)]))
    conn = FakeConn(script)
    report = verify(conn)
    assert not report.passed
    failed = [c.name for c in report.checks if not c.ok]
    assert "insert refused in a READ WRITE transaction" in failed
    assert any("has_table_privilege(selections, INSERT) is false" == c.name and not c.ok for c in report.checks)
    # Phase A's 25006 refusal is accepted there, as documented: it proves the default, not the grant.
    assert next(c for c in report.checks if c.name == "insert refused under defaults").ok


def test_create_table_success_is_a_failure_and_wrong_user_is_a_failure():
    conn = FakeConn(good_role_script(create_ok=True))
    conn.script.insert(0, (r"^INSERT INTO selections", PGError("42501", "permission denied")))
    report = verify(conn)
    assert not report.passed
    bad = next(c for c in report.checks if c.name == "create table refused")
    assert not bad.ok and "SUCCEEDED" in bad.detail
    conn = FakeConn([(r"^SELECT current_user", [("neondb_owner",)])] + good_role_script())
    conn.script.insert(0, (r"^INSERT INTO selections", PGError("42501", "permission denied")))
    report = verify(conn, role="stride_chat_ro")
    assert not next(c for c in report.checks if c.name == "connected as the chat role").ok


def test_report_text_states_the_verdict():
    conn = FakeConn(good_role_script())
    conn.script.insert(0, (r"^INSERT INTO selections", PGError("42501", "permission denied")))
    text = verify(conn).text()
    assert text.startswith("read-only proof for role stride_chat_ro:") and text.endswith("PASS: the role cannot write")
