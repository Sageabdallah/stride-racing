#!/usr/bin/env python3
"""Punting Form API client (Starter tier) — the data source that replaced
The Racing API for Australian racing. Endpoints and payload shapes were
confirmed live in the puntingform-probe workflow (runs 1-2, 2026-07-31);
see PUNTINGFORM_MIGRATION.md Phase A for the recorded facts.

Auth is the apiKey query parameter. Dates are ISO (yyyy-MM-dd). Every JSON
response arrives in an envelope: {statusCode, status, error, errors, payLoad}.
The client raises PFError on envelope errors so callers cannot silently
mistake an error body for data (lesson from selection_diagnostics).
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.puntingform.com.au/v2"
UA = "Mozilla/5.0 (compatible; StrideRacing/1.0)"


class PFError(RuntimeError):
    pass


def _api_key():
    key = (os.environ.get("PUNTINGFORM_API_KEY") or "").strip()
    if not key:
        raise PFError("PUNTINGFORM_API_KEY is not set")
    return key


def get(path, params=None, retries=3, timeout=60):
    """GET {BASE}{path} and return the payLoad. Retries transient failures
    with backoff; polite 0.4s pacing between calls (rate limits unpublished)."""
    q = dict(params or {})
    q["apiKey"] = _api_key()
    url = f"{BASE}{path}?{urllib.parse.urlencode(q)}"
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace"))
            if body.get("error") or (body.get("statusCode") and body["statusCode"] != 200):
                raise PFError(f"{path}: {body.get('statusCode')} {body.get('error')}")
            time.sleep(0.4)
            return body.get("payLoad")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise PFError(f"{path}: failed after {retries} attempts: {last_err}")


def meetings_for_date(iso_date):
    """All meetings for an ISO date. Meeting keys include meetingId, track
    {name,state,country,surface}, isBarrierTrial, isJumps, tabMeeting,
    resultsUpdated, railPosition, expectedCondition."""
    return get("/form/meetingslist", {"meetingDate": iso_date}) or []


def results_for_meeting(meeting_id):
    """Results payload: [{meetingId, track, meetingDate, raceResults:[{raceId,
    raceNumber, trackConditionLabel, officialRaceTime, runners:[{position,
    margin, tabNo, runner, runnerId, trainer(+Id), jockey(+Id), barrier,
    weight, inRun, flucs, price, gearChanges, formId, ...}]}]}]"""
    return get("/form/results", {"meetingId": meeting_id}) or []


def meeting_detail(meeting_id):
    """Full meeting payload: {track, races:[{..., runners:[{name, runnerId,
    last10, careerStarts/Wins/..., weight, barrier, tabNo, jockey{}, trainer{},
    winPct, placePct, ...}]}]} — racecard + per-runner form in one call."""
    return get("/form/meeting", {"meetingId": meeting_id})
