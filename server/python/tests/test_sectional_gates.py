"""The three sectional gates must ask whether data landed, not who landed it.

All three sources (nsw, qld, racing.com) are also collected daily by the
results pipeline, so the weekly backfill legitimately inserts zero rows when
the daily path got there first. 9f0ade3 fixed the qld gate to fail only when
the trailing window itself is empty; #144 and #146 are the nsw and racing-com
gates re-filing the same false alarm because that fix was never ported to
them. These pin the condition in all three gates so the old shape cannot come
back, and cannot ship in a fourth source's gate unnoticed.

Parsed as text, not YAML: the CI dependency set carries no yaml parser, and
the properties pinned here are literal lines inside the gate heredocs.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "sectional-schedules.yml"

GATE_NAMES = [
    "Gate — this run landed new NSW rows",
    "Gate — QLD sectional data is flowing for the window",
    "Gate — this run landed new racing.com rows",
]


def _gate_script(name):
    """The step body from its `- name:` line to the next step's."""
    text = WORKFLOW.read_text()
    start = text.index(name)
    rest = text[start:]
    nxt = re.search(r"\n      - name: ", rest)
    return rest[: nxt.start()] if nxt else rest


def test_all_three_gates_exist():
    text = WORKFLOW.read_text()
    for name in GATE_NAMES:
        assert name in text, f"gate step missing or renamed: {name}"


def test_gates_fail_only_when_the_window_is_empty():
    for name in GATE_NAMES:
        script = _gate_script(name)
        assert "if post <= pre and window == 0:" in script, (
            f"{name}: red on this run's delta alone. The daily collector "
            f"landing the window's rows first makes the weekly backfill a "
            f"guaranteed false alarm (#125, #144, #146).")
        assert re.search(r"if post <= pre:\s*\n(?!.*SystemExit)", script), (
            f"{name}: lost the benign-overlap branch — a zero delta with a "
            f"populated window should be explained, not silent.")


def test_gates_still_fail_loud_when_nothing_landed_at_all():
    for name in GATE_NAMES:
        script = _gate_script(name)
        assert "SystemExit(1)" in script, (
            f"{name}: an empty trailing window must still exit 1 — that case "
            f"is the silent no-op class this gate exists to catch.")


def test_ported_gates_warn_when_the_window_itself_goes_stale():
    """The benign-overlap pass must not hide the daily collector AND the
    backfill both dying: rows only age out of the window, so the ported
    gates check the newest window row on every zero-delta pass. (QLD carries
    its own per-meeting coverage report; these two have no track universe to
    check against, so freshness is the source-agnostic equivalent.)"""
    for name in (GATE_NAMES[0], GATE_NAMES[2]):
        script = _gate_script(name)
        assert "MAX(race_date)" in script and "::warning::" in script, (
            f"{name}: zero-delta pass lost its staleness advisory")
