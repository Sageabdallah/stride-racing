"""Every script the job handler shells out to must accept the arguments the
handler actually passes.

This repo mixes positional-date and --date CLIs (CLAUDE.md calls the
confusion out by name), and a mismatch is invisible until the job runs in
the cloud: argparse exits 2, the wrapper raises, and the only signal is an
alarm at 11:05 on a race day. Parsing the handler and each target's parser
turns that into a test failure here.
"""

import ast
import re
import sys
from pathlib import Path

import pytest

PY = Path(__file__).resolve().parents[1]
HANDLER = PY.parent.parent / "infra" / "jobs" / "handler.py"


def _handler_calls():
    """(script, args) for every _run/_run_ok call with literal arguments."""
    tree = ast.parse(HANDLER.read_text())
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in ("_run", "_run_ok") or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and
                str(first.value).endswith(".py")):
            continue
        args = []
        for a in node.args[1:]:
            if isinstance(a, ast.Constant):
                args.append(str(a.value))
            else:
                args.append("<runtime>")   # a date/param, always positional-safe
        calls.append((first.value, args))
    return calls


def _script_cli(script: str):
    """(flags, has_positional) parsed from the target's own argparse setup."""
    path = PY / script
    src = path.read_text()
    flags = set(re.findall(r'add_argument\(\s*["\'](--[a-z0-9-]+)["\']', src))
    positional = bool(re.search(r'add_argument\(\s*["\'][a-z_]+["\']', src))
    return flags, positional


CALLS = _handler_calls()


def test_handler_calls_were_discovered():
    assert len(CALLS) >= 10, "handler parse found too few calls — parser broke"


@pytest.mark.parametrize("script,args", CALLS,
                         ids=[f"{s}:{' '.join(a)}" for s, a in CALLS])
def test_each_call_matches_the_target_cli(script, args):
    assert (PY / script).exists(), f"{script} does not exist"
    flags, positional = _script_cli(script)

    for i, a in enumerate(args):
        if a.startswith("--"):
            assert a in flags, (
                f"{script} has no {a} flag; handler passes it "
                f"(argparse would exit 2)")
        elif a == "<runtime>" or not a.startswith("-"):
            # A bare value: either it fills a flag that precedes it, or the
            # script must define a positional to receive it.
            preceded_by_flag = i > 0 and args[i - 1].startswith("--")
            if not preceded_by_flag:
                assert positional, (
                    f"{script} defines no positional argument, but the "
                    f"handler passes {a!r} positionally (argparse exits 2)")


def test_date_flag_scripts_are_never_called_positionally():
    """The specific defect this file exists for: a --date script handed a
    bare date. capture_late_odds.py shipped exactly that way."""
    offenders = []
    for script, args in CALLS:
        flags, positional = _script_cli(script)
        if "--date" in flags and not positional:
            for i, a in enumerate(args):
                if not a.startswith("--") and not (
                        i > 0 and args[i - 1].startswith("--")):
                    offenders.append(f"{script} <- {a}")
    assert not offenders, f"--date scripts called positionally: {offenders}"
