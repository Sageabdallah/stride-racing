"""The morning watch must wake the agent for the issue it just filed.

On 2026-09-03 the watch filed #153 (no racecard, no consensus) and then asked
GitHub for the newest open auto-triage issue to decide what to dispatch
claude.yml against. auto-triage had filed #152 three hours earlier for the
failed racecards run; the list came back with #152 first; the wake step read
its age as "already deduped" and did not dispatch. #153 carried an @claude
tag that no agent ever read.

The run knows what it filed. Each escalation step now hands its issue number
forward as a step output and the wake step dispatches for that, so the
guarantee no longer rests on GitHub's ordering or on a freshness heuristic.
Pinned as text because CI has no YAML parser.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WATCH = ROOT / ".github" / "workflows" / "morning-watch.yml"

# id -> the title each branch files under. Four branches file an @claude
# issue; the "unreachable" branch deliberately does not and must not wake.
ESCALATIONS = {
    "dead": "no racecard and no consensus",
    "noop": "consensus_mentions empty",
    "unclassified": "unclassified zero",
    "qbroken": "Morning watch query is broken",
}


def _steps():
    """Split the job into its steps, keyed by step name."""
    text = WATCH.read_text(encoding="utf-8")
    chunks = re.split(r"^      - name: ", text, flags=re.M)[1:]
    return {c.split("\n", 1)[0].strip(): c for c in chunks}


def _step_with_id(step_id):
    for name, body in _steps().items():
        if re.search(rf"^        id: {step_id}\n", body, re.M):
            return name, body
    raise AssertionError(f"no step carries id: {step_id}")


def test_every_escalation_branch_hands_its_issue_number_forward():
    for step_id, title in ESCALATIONS.items():
        name, body = _step_with_id(step_id)
        assert title in body, f"step {step_id} ({name}) does not file the expected title"
        assert "URL=$(gh issue create" in body, \
            f"step {step_id} ({name}) must capture the URL gh issue create prints"
        assert 'echo "issue=${URL##*/}" >> "$GITHUB_OUTPUT"' in body, \
            f"step {step_id} ({name}) must expose the filed issue number as an output"


def test_unreachable_branch_files_without_waking():
    body = _steps()["Watcher blind — notify, do not escalate"]
    assert "gh issue create" in body
    assert "$GITHUB_OUTPUT" not in body, \
        "the connectivity branch must not feed the wake step; an agent cannot fix a network path"


def test_wake_step_dispatches_for_what_this_run_filed():
    body = _steps()["Wake the agent"]
    for step_id in ESCALATIONS:
        assert f"steps.{step_id}.outputs.issue" in body, \
            f"wake step ignores the {step_id} branch's issue"
    assert 'issue_number="$ISSUE"' in body
    assert "gh workflow run claude.yml" in body


def test_wake_step_no_longer_guesses_from_the_issue_list():
    body = _steps()["Wake the agent"]
    run = body.split("run: |", 1)[1]
    assert "gh issue list" not in run, \
        "asking for the newest auto-triage issue is the proxy that skipped #153"
    assert "AGE" not in run, \
        "freshness stood in for 'did this run file it'; the outputs answer that directly"
