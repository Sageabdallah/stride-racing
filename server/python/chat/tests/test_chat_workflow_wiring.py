"""The workflows that run the chat's entry points invoke them the way they run.

verify_readonly_role.py does `from .config import load_dotenv_once` inside
main(), so it only runs as a module (`python -m chat.verify_readonly_role`,
as its own docstring says). apply-migration.yml ran it by path -- `python
server/python/chat/verify_readonly_role.py` -- which dies on "attempted
relative import with no known parent package" before it connects. Loudly,
under set -euo pipefail, so not a silent pass; but at the one step whose job
is to prove the role cannot write, and on the next role apply or password
rotation rather than now, because that step is conditional on the migration
being chat_readonly_role.sql and has not run since the role was created.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = ROOT / ".github" / "workflows" / "apply-migration.yml"


def test_apply_migration_runs_the_verifier_as_a_module():
    body = WORKFLOW.read_text()
    assert "python -m chat.verify_readonly_role" in body
    # And from the directory that makes `chat` importable.
    assert "cd server/python && python -m chat.verify_readonly_role" in body


def test_apply_migration_does_not_run_the_verifier_by_path():
    for line in WORKFLOW.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "python server/python/chat/verify_readonly_role.py" not in stripped, line
