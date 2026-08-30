#!/usr/bin/env python3
"""JSON bridge between the Node app server and the Punting Form provider.

Called by server/pfProvider.ts via child_process — the same pattern as
mc_api.py — so the production-tested provider mapping (racecard field
contract, scratchings fold, horse-id bridge) stays single-sourced with the
pipeline instead of being re-implemented in TypeScript.

Input: one JSON object on stdin:  {"action": ..., ...params}
Output: one JSON object on stdout (import-time warnings from the provider
stack may precede it; the Node side extracts the last JSON line).

Actions:
  meets       {date}                     -> {"meets": [{meet_id, course}]}
  racecard    {date, track?}             -> {"meets": [racecard-contract meets]}
  results     {date}                     -> {"results": [...]}
  speedmaps   {meeting_id, race_no?}     -> {"speedmaps": [...]}
  scratchings {}                         -> {"scratchings": [...]}
  conditions  {}                         -> {"conditions": [...]}

Without PUNTINGFORM_API_KEY the bridge answers {"error": ...} with exit 0 so
the app degrades exactly the way it did when live-card credentials were
absent under the old provider.
"""

import json
import os
import sys


def _fail(message):
    print(json.dumps({"error": message}))
    return 0


def main():
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return _fail("invalid JSON on stdin")

    action = request.get("action")
    if not (os.environ.get("PUNTINGFORM_API_KEY") or "").strip():
        return _fail("PUNTINGFORM_API_KEY not configured")

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pf_client
    from providers.puntingform import PuntingFormProvider

    provider = PuntingFormProvider()
    try:
        if action == "meets":
            out = {"meets": provider.fetch_meets(request["date"])}
        elif action == "racecard":
            date = request["date"]
            wanted = (request.get("track") or "").strip().lower()
            meets = []
            for meet in provider.fetch_meets(date):
                course = meet.get("course") or ""
                if wanted and wanted not in course.lower() and course.lower() not in wanted:
                    continue
                races = provider.fetch_detailed_races(meet["meet_id"], date, course)
                meets.append({
                    "course": course,
                    "track": course,
                    "meet_id": meet["meet_id"],
                    "date": date,
                    "races": races,
                })
            out = {"meets": meets}
        elif action == "results":
            out = {"results": provider.fetch_results(request["date"])}
        elif action == "speedmaps":
            out = {"speedmaps": pf_client.speedmaps_for_meeting(
                request["meeting_id"], int(request.get("race_no") or 0))}
        elif action == "scratchings":
            out = {"scratchings": pf_client.scratchings()}
        elif action == "conditions":
            out = {"conditions": pf_client.conditions()}
        else:
            out = {"error": f"unknown action: {action!r}"}
    except Exception as exc:  # bridge reports; the Node side decides
        out = {"error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
