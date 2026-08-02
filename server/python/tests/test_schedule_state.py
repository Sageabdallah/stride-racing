"""A deploy must never silently re-enable a disabled schedule.

`aws scheduler update-schedule` is full-replacement: an omitted --state
reverts to ENABLED. Both schedule writers omitted it, so every deploy
undid an operator's "disable this one while we investigate". Scheduler has
no enable/disable API — update IS the only writer — so the preservation
has to live in the writer, and therefore has to be tested there.

These drive infra/lib_schedule.sh with a stubbed `aws` on PATH: no AWS
account, no credentials, no network.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

LIB = (Path(__file__).resolve().parents[3] / "infra" / "lib_schedule.sh")


def _run(tmp_path, aws_body, func="sched_state_to_keep", name="stride-x"):
    """Run one lib function with a fake `aws` whose behaviour is aws_body."""
    binn = tmp_path / "bin"
    binn.mkdir(exist_ok=True)
    fake = binn / "aws"
    fake.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(aws_body))
    fake.chmod(0o755)

    env = dict(os.environ, PATH=f"{binn}:{os.environ['PATH']}",
               STATE_DIR=str(tmp_path))
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; {func} "{name}"'],
        capture_output=True, text=True, env=env, timeout=120)


# A schedule that does not exist yet has no operator intent to preserve.
def test_absent_schedule_is_created_enabled(tmp_path):
    r = _run(tmp_path, """
        echo "ResourceNotFoundException: no schedule" >&2
        exit 254
    """)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ENABLED"


def test_enabled_stays_enabled(tmp_path):
    r = _run(tmp_path, 'echo ENABLED; exit 0')
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ENABLED"


def test_disabled_is_preserved_and_announced(tmp_path):
    # The whole point: DISABLED survives the deploy...
    r = _run(tmp_path, 'echo DISABLED; exit 0')
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "DISABLED"
    # ...and is never quiet about it. A schedule silently staying off is as
    # bad as one silently coming back on.
    assert "DISABLED" in r.stderr
    assert "will NOT fire" in r.stderr


def test_unreadable_state_fails_rather_than_guessing(tmp_path):
    # AccessDenied means "cannot know". Guessing ENABLED here is exactly how
    # a deliberate disable gets undone by a deploy that never knew.
    r = _run(tmp_path, """
        echo "AccessDeniedException: not authorized" >&2
        exit 254
    """)
    assert r.returncode != 0
    assert "refusing to guess" in r.stderr


def test_expired_token_fails_rather_than_guessing(tmp_path):
    r = _run(tmp_path, """
        echo "ExpiredTokenException: session expired" >&2
        exit 254
    """)
    assert r.returncode != 0
    assert "cannot read current schedule state" in r.stderr


def test_throttling_is_retried_not_fatal(tmp_path):
    # The write path already retries 10x/10s for IAM propagation; a read
    # with no retries would invent a new deploy failure mode.
    counter = tmp_path / "n"
    counter.write_text("0")
    r = _run(tmp_path, f"""
        N=$(cat "{counter}")
        N=$((N + 1)); echo "$N" > "{counter}"
        if [ "$N" -lt 2 ]; then
          echo "ThrottlingException: rate exceeded" >&2; exit 254
        fi
        echo DISABLED; exit 0
    """)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "DISABLED"
    assert counter.read_text().strip() == "2"


def test_garbage_state_is_fatal(tmp_path):
    # A State that is neither ENABLED nor DISABLED means the API contract
    # moved; passing it straight back into --state would be worse.
    r = _run(tmp_path, 'echo SOMETHING_NEW; exit 0')
    assert r.returncode != 0
    assert "not ENABLED/DISABLED" in r.stderr


# ------------------------------------------------- the wiring, not the lib

@pytest.mark.parametrize("script", ["06_schedules.sh", "07b_fargate_schedules.sh"])
def test_every_update_schedule_call_passes_state(script):
    """The helper is worthless if a writer forgets to use it.

    This is the assertion that actually pins the defect: it reads the real
    scripts and fails if any update-schedule invocation omits --state.
    """
    src = (LIB.parent / script).read_text()
    assert "lib_schedule.sh" in src, f"{script} does not source the helper"
    # Each update-schedule call is a backslash-continued block; split on the
    # verb and check the argument list that follows it.
    blocks = src.split("aws scheduler update-schedule")[1:]
    assert blocks, f"{script} has no update-schedule call to check"
    for b in blocks:
        stanza = b.split(">/dev/null")[0]
        assert "--state" in stanza, (
            f"{script}: an update-schedule call omits --state; "
            f"update is full-replacement, so State reverts to ENABLED")
