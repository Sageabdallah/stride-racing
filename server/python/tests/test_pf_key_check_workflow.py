"""The daily Punting Form key check must stay wired to its alarm.

The subscription lapsed overnight on 2026-09-03 and the first alarm was the
04:00 racecard task dying on Fargate. The puntingform-probe workflow now
makes one meetings-list call at 03:15 AEST and auto-triage files an issue
when it fails. Three things make that useful, and each can be lost by an
innocent-looking edit: the schedule itself, the job that runs on it, and
the entry in auto-triage's watch list. Pinned as text because CI has no
YAML parser.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROBE = ROOT / ".github" / "workflows" / "puntingform-probe.yml"
TRIAGE = ROOT / ".github" / "workflows" / "auto-triage.yml"


def test_probe_workflow_runs_the_key_check_on_a_daily_schedule():
    text = PROBE.read_text()
    assert re.search(r'^\s+- cron: "15 17 \* \* \*"', text, re.M), \
        "daily 03:15 AEST (17:15 UTC) schedule is missing"
    assert "key-check:" in text
    assert "github.event_name == 'schedule'" in text
    assert "pf_client.PFAuthError" in text, \
        "the key check must report a rejected key as the reason, not a generic failure"


def test_key_check_failure_reaches_auto_triage():
    text = TRIAGE.read_text()
    watched = re.search(r"workflows:\n((?:\s+- \".*\n)+)", text)
    assert watched, "auto-triage watch list not found"
    assert '"puntingform-probe"' in watched.group(1), \
        "puntingform-probe is not in the auto-triage watch list; a rejected key would fail silently"
