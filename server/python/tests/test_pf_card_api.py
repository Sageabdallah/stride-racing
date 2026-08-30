"""pf_card_api bridge contract — stdin/stdout JSON, no network.

The bridge is what server/pfProvider.ts spawns; these tests pin the two
properties the Node side depends on: a keyless run answers with an error
JSON (exit 0), and every answer is a single JSON object on stdout.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1] / "pf_card_api.py"


def _run(payload, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k != "PUNTINGFORM_API_KEY"}
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, str(BRIDGE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return proc


def _last_json_line(stdout):
    lines = [l for l in stdout.splitlines() if l.strip().startswith("{")]
    assert lines, f"no JSON line in stdout: {stdout!r}"
    return json.loads(lines[-1])


def test_keyless_run_returns_error_json_with_exit_zero():
    proc = _run({"action": "meets", "date": "2026-08-01"})
    assert proc.returncode == 0
    out = _last_json_line(proc.stdout)
    assert "PUNTINGFORM_API_KEY" in out["error"]


def test_unknown_action_reports_error_not_crash():
    proc = _run({"action": "nonsense"}, env_extra={"PUNTINGFORM_API_KEY": "test-key"})
    assert proc.returncode == 0
    out = _last_json_line(proc.stdout)
    assert "unknown action" in out["error"]


def test_invalid_stdin_reports_error():
    proc = subprocess.run(
        [sys.executable, str(BRIDGE)],
        input="not json",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    out = _last_json_line(proc.stdout)
    assert "invalid JSON" in out["error"]
