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


# ------------------------------------------- ordering, not just statefulness

def _fargate_crons() -> dict:
    """{task family: (hour, minute)} parsed from the real writer.

    Keyed on the FAMILY, not the schedule name: the name carries the time as
    a label and would make these tests pass or fail on a rename rather than
    on when the job actually fires. Six fields, EventBridge style:
    `cron(M H D M ? Y)`.
    """
    import re
    src = (LIB.parent / "07b_fargate_schedules.sh").read_text()
    out = {}
    for cron, family in re.findall(
            r'^sched_ecs\s+\S+\s+"([^"]+)"\s+(\S+)', src, re.M):
        minute, hour = cron.split()[0], cron.split()[1]
        out[family] = (int(hour), int(minute))
    return out


def _scheduled_names() -> set:
    import re
    src = (LIB.parent / "07b_fargate_schedules.sh").read_text()
    return set(re.findall(r'^sched_ecs\s+(\S+)', src, re.M))


def test_card_dependent_schedules_all_fire_after_racecard_collect():
    """Every job that calls _require_racecard needs the 05:30 task's output.

    racecard-collect is the only writer of racecards/racecard_<today>.json
    AND — via seed_race_schedule.py in the same task — of race_schedule for
    today. betfair_prices maps the Betfair catalogue THROUGH race_schedule,
    so a card-dependent job scheduled before 05:30 has neither: it raises in
    _require_racecard, the task fails, SNS fires, every day, on healthy days
    included.

    This is not hypothetical. baseline-night was first written for 00:30 —
    the time scheduler.ts used on the Mac, where download_racecards_wednesday
    put a week of cards on disk every Wednesday 4 PM. That premise did not
    survive the move to Fargate, and nothing in the suite would have caught
    it: the unit tests stub _prepare_racecard, so they cannot see a clock.
    """
    crons = _fargate_crons()
    card_dependent = ["stride-baseline-night", "stride-intelligence-build",
                      "stride-consensus-agent", "stride-morning-odds",
                      "stride-tips-pipeline"]
    collect = crons["stride-racecard-collect"]
    for family in card_dependent:
        assert family in crons, f"{family} is not scheduled"
        assert crons[family] > collect, (
            f"{family} fires at "
            f"{crons[family][0]:02d}:{crons[family][1]:02d}, before "
            f"racecard-collect at {collect[0]:02d}:{collect[1]:02d} — "
            f"there is no card and no race_schedule row for today at that "
            f"hour, so _require_racecard raises every day")


def test_the_morning_chain_runs_in_dependency_order():
    """Each job must fire after the one whose output it syncs down.

    Fargate tasks share no filesystem, so "after" is not a nicety: a job that
    starts before its producer finishes reads YESTERDAY's file out of S3 and
    reports success on it. baseline-night before the morning check because
    compute_market_signals diffs MORNING_CHECK against BASELINE_NIGHT, and a
    baseline captured afterwards measures nothing — which is how
    market_signal_scores went empty from 2026-04-18 without an alarm.
    """
    c = _fargate_crons()
    chain = [("stride-racecard-collect", "stride-intelligence-build"),
             ("stride-intelligence-build", "stride-consensus-agent"),
             ("stride-consensus-agent", "stride-tips-pipeline"),
             ("stride-baseline-night", "stride-morning-odds"),
             ("stride-morning-odds", "stride-tips-pipeline")]
    for before, after in chain:
        assert c[before] < c[after], (
            f"{after} at {c[after][0]:02d}:{c[after][1]:02d} does not run "
            f"after {before} at {c[before][0]:02d}:{c[before][1]:02d}")


def test_every_retimed_schedule_retires_the_name_it_replaced():
    """A re-timed job must delete its old schedule, not just add a new one.

    The name encodes the fire time, so re-timing renames. create-schedule on
    the new name leaves the OLD name enabled and untouched: the job then runs
    twice a day, and the later run overwrites the earlier one's output from a
    stale sync-down. Nothing else in the deploy would notice — schedule-audit
    reports state, not whether a name should still exist.
    """
    import re
    src = (LIB.parent / "07b_fargate_schedules.sh").read_text()
    retired = set(re.findall(r'^retire_schedule\s+(\S+)', src, re.M))
    for name in retired:
        assert name not in _scheduled_names(), (
            f"{name} is both retired and scheduled — the delete runs before "
            f"the create, so this just re-creates what it deleted")


def test_every_schedule_name_states_the_time_it_actually_fires():
    """The -HHMM suffix must match the cron, or the name is a lie.

    This is the guard against the exact class of bug that cost four months:
    a time carried in a NAME (or a docstring) that no longer matches
    reality. baseline-night was written for 00:30 because
    betfair_snapshot_coverage_audit.py still said "~00:30" long after the
    Mac stopped running it. A schedule renamed without a re-time, or
    re-timed without a rename, plants the same trap for the next reader.
    """
    import re
    src = (LIB.parent / "07b_fargate_schedules.sh").read_text()
    for name, cron in re.findall(r'^sched_ecs\s+(\S+)\s+"([^"]+)"', src, re.M):
        suffix = name.rsplit("-", 1)[-1]
        assert re.fullmatch(r"\d{4}", suffix), (
            f"{name}: schedule names must end in -HHMM so the name states "
            f"when the job fires")
        minute, hour = cron.split()[0], cron.split()[1]
        assert suffix == f"{int(hour):02d}{int(minute):02d}", (
            f"{name} says {suffix} but fires at "
            f"{int(hour):02d}:{int(minute):02d}")
