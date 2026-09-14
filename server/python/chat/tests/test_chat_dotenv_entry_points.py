"""Every entry point loads .env, so a value set there reaches all of them.

chat.cli has always honoured a repository-root .env. PR #181 gave
verify_readonly_role.py the same treatment through config.load_dotenv_once(),
"so the two scripts can no longer drift apart on which one honours .env" --
but there were three entry points, not two, and eval_runner.py had never
called it. The symptom was not the verifier's loud "is not set (or pass
--url)": runtime.build_context() leaves Context.db as None when
config.database_url() is empty, so --live-cli with STRIDE_CHAT_DATABASE_URL
set only in .env built a context with no database and every database-backed
case answered "No database is configured" from the tools' own guards.

The failure mode is an entry point nobody thought to check, so this pins the
contract for all three at once rather than for the one that was missed.
"""

from __future__ import annotations

import pytest

from chat import cli, config, eval_runner, verify_readonly_role


@pytest.fixture
def dotenv_calls(monkeypatch):
    """Count load_dotenv_once() calls, with the URL out of the real
    environment so no entry point can reach a live connection."""
    calls = []
    monkeypatch.setattr(config, "load_dotenv_once", lambda: calls.append(1))
    monkeypatch.delenv("STRIDE_CHAT_DATABASE_URL", raising=False)
    return calls


def test_cli_loads_dotenv(dotenv_calls, monkeypatch, capsys):
    # The context is never read on this path: --args is invalid JSON, so
    # main() returns before the tool is dispatched.
    monkeypatch.setattr(cli, "build_context", lambda *a, **k: None)
    assert cli.main(["--tool", "lookup_horse", "--args", "{"]) == 2
    capsys.readouterr()
    assert dotenv_calls == [1]


def test_eval_runner_loads_dotenv(dotenv_calls, capsys):
    assert eval_runner.main(["--self-test"]) == 0
    capsys.readouterr()
    assert dotenv_calls == [1]


def test_verify_readonly_role_loads_dotenv(dotenv_calls, capsys):
    assert verify_readonly_role.main([]) != 0
    capsys.readouterr()
    assert dotenv_calls == [1]
