"""The daily probe must land BEFORE the card it is protecting.

#204 gave the web-research leg a one-query check and nothing ran it. That is
the gap both Perplexity outages fell through — 2026-09-02..09 (#176) and again
2026-09-16, when the balance was alive at 05:30 and gone by 05:52. Each was
found from an SNS alert after the spend.

Two properties decide whether this workflow is worth having, and neither is
visible from "the YAML parses":

  * it runs with real lead time on the 19:30Z consensus slot. GitHub's
    scheduler drifts LATE and only late, by up to 2h51m on this repository's
    daily crons, so a probe scheduled close to the slot reports on a card
    already spent. This file's first version got that wrong and is the reason
    the threshold below is measured rather than chosen.
  * it does not fire and forget. `gh workflow run` returns 204 whether or not
    the run it asked for ever happens, which is the silent no-op class exactly.

Pinned as text, like test_morning_watch_wake.py: CI has no YAML parser.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WF = ROOT / ".github" / "workflows" / "search-proof-daily.yml"

# The consensus slot, in UTC minutes past midnight. 05:30 Sydney (AEST, UTC+10).
CONSENSUS_SLOT_UTC_MIN = 19 * 60 + 30
# The worst scheduler drift measured on this repository's daily crons, plus an
# hour. This threshold started at 90 and was WRONG: the workflow shipped with a
# two-hour lead and its first firing landed 2h21m late, 21 minutes after the
# consensus job it exists to warn (run 35388256393). A probe that reports after
# the card is spent is not a probe.
#
#     search-proof-daily     17:30Z -> 19:51Z    2h21m
#     pf-morning-racecards   19:30Z -> 22:15Z    2h45m
#     pf-morning-racecards   19:30Z -> 22:21Z    2h51m
#     morning-watch          22:50Z -> 00:49Z    1h59m
#     morning-watch          22:50Z -> 00:54Z    2h04m
MIN_LEAD_MINUTES = 240


def _text():
    return WF.read_text(encoding="utf-8")


def test_the_workflow_exists():
    assert WF.is_file(), "the daily probe workflow is missing"


def test_it_runs_far_enough_ahead_of_the_consensus_slot():
    """The teeth. A cron 30 minutes before 19:30Z looks careful and is not:
    the scheduler's drift is larger than the margin."""
    crons = re.findall(r'^\s*- cron:\s*"([^"]+)"', _text(), re.M)
    assert crons, "no cron: this is a daily probe or it is nothing"

    minute, hour = crons[0].split()[0], crons[0].split()[1]
    fires = int(hour) * 60 + int(minute)
    lead = CONSENSUS_SLOT_UTC_MIN - fires
    assert lead >= MIN_LEAD_MINUTES, (
        f"probe fires {lead} min before the 19:30Z consensus slot; "
        f"observed scheduler drift needs at least {MIN_LEAD_MINUTES}")


def test_it_asks_for_search_proof_and_nothing_else():
    """jobs=search-proof is the whole point. Any other job here would spend
    real money on a schedule."""
    text = _text()
    assert "-f jobs=search-proof" in text
    for costly in ("jobs=consensus-agent", "jobs=tips-pipeline", "jobs=tips-proof"):
        assert costly not in text, f"{costly} must never run on this schedule"


def test_it_waits_for_the_run_it_dispatched():
    """Dispatch-and-hope is the defect this repository keeps finding. The
    workflow must read a conclusion it actually observed."""
    text = _text()
    assert "gh run view" in text, "nothing reads the dispatched run's outcome"
    assert "--json conclusion" in text or "conclusion" in text


def test_it_proves_the_run_is_its_own():
    """Reading 'the newest verify-jobs run' without capturing the id before
    dispatching would report someone else's dispatch as this morning's probe."""
    text = _text()
    assert "BEFORE=" in text
    assert '"$CANDIDATE" != "$BEFORE"' in text


def test_a_dark_leg_files_an_issue():
    text = _text()
    assert "gh issue create" in text
    assert "auto-triage" in text, "the label the triage tooling reads"


def test_it_does_not_file_a_duplicate_every_morning():
    """A multi-day outage must not produce a daily stream of identical issues;
    that is how a watcher stops being read."""
    text = _text()
    assert "gh issue list" in text
    assert "already filed" in text


def test_it_can_be_run_by_hand():
    assert "workflow_dispatch:" in _text()


def test_it_asks_for_no_more_permission_than_it_uses():
    text = _text()
    head = text.split("jobs:")[0]
    assert "issues: write" in head
    assert "actions: write" in head
    # It reads the repo and writes nothing back.
    assert "contents: read" in head
    assert "contents: write" not in head
