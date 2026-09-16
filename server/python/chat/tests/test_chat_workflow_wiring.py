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

Two things are pinned, and neither is a phrase match on the YAML alone. The
module form is actually executed here, from the directory the workflow uses,
and must reach the script's own URL check; and the workflow's live (not
commented-out) shell must invoke that form and not the by-path one.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SERVER_PYTHON = ROOT / "server" / "python"
WORKFLOW = ROOT / ".github" / "workflows" / "apply-migration.yml"

MODULE_FORM = "python -m chat.verify_readonly_role"
PATH_FORM = "python server/python/chat/verify_readonly_role.py"


def _live_lines(text: str):
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield stripped


def test_the_module_form_runs_from_server_python_and_reaches_its_url_check():
    """What the workflow line must do, run as the workflow runs it: from
    server/python, as a module, with no URL. The by-path form dies on the
    relative import before this message; the module form gets to it."""
    # Set to "" rather than removed: load_dotenv_once() uses override=False,
    # so a variable already in the environment keeps a developer's .env from
    # supplying a real URL and turning this into a live connection attempt.
    env = dict(os.environ, STRIDE_CHAT_DATABASE_URL="")
    proc = subprocess.run(
        [sys.executable, "-m", "chat.verify_readonly_role"],
        cwd=SERVER_PYTHON, env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "STRIDE_CHAT_DATABASE_URL is not set" in proc.stderr, proc.stderr
    assert "relative import" not in proc.stderr, proc.stderr


def test_apply_migration_invokes_the_module_form_from_server_python():
    live = list(_live_lines(WORKFLOW.read_text()))
    invoking = [l for l in live if MODULE_FORM in l]
    assert invoking, "apply-migration.yml has no live line running the module form"
    for line in invoking:
        assert "cd server/python" in line, (
            f"the module form must run from server/python so `chat` imports: {line}")


def test_apply_migration_does_not_run_the_verifier_by_path():
    for line in _live_lines(WORKFLOW.read_text()):
        assert PATH_FORM not in line, line
