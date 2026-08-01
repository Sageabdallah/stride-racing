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


def scratchings(jurisdiction=None):
    """All upcoming scratchings (Starter tier). Each item: {meetingId, raceId,
    runnerId, meetingDate, meetingDateUTC, track, country, code, raceNo,
    tabNo, timeStamp (when scratched), deduction (betting adjustment)}.
    jurisdiction: JurisdictionType enum int (0/1/2), omitted when None."""
    params = {}
    if jurisdiction is not None:
        params["jurisdiction"] = jurisdiction
    return get("/Updates/Scratchings", params) or []


def conditions(jurisdiction=None):
    """Track grading + weather for upcoming meetings (Starter tier). Each
    item: {meetingDate, meetingDateUTC, meetingId, track, trackCondition,
    trackConditionNumber, weather, wind, windDirection, abandonded (sic —
    their spelling), rail, penetrometer, irrigation, rainfall, comment,
    source, lastUpdate}."""
    params = {}
    if jurisdiction is not None:
        params["jurisdiction"] = jurisdiction
    return get("/Updates/Conditions", params) or []


def speedmaps_for_meeting(meeting_id, race_no=0):
    """Punting Form speed maps (Starter tier). race_no=0 returns every race
    in the meeting. Each map: {meetingDate, track, meetingId, raceId,
    trackId, raceNo, items:[{runnerId, runnerName, tabNo, pfScore,
    neuralPrice, neuralPriceRank, pfaiScore, pfaiPrice, pfaiRank,
    assessedPrice, speed, settle, barrier, mapA2E, jockeyA2E, ratedRunStyle,
    ratedSettle}]} — settling position / pace pressure inputs."""
    return get("/User/Speedmaps", {"meetingId": meeting_id, "raceNo": race_no}) or []


def ratings_for_meeting(meeting_id):
    """Punting Form standard ratings for a meeting (Starter tier — same
    apiKey query auth as everything else; the reference's 'token based
    authentication' is this same parameter). Payload: array of Ratings
    objects (40+ fields incl. neuralPrice, pfScore, runStyle, time ranks)."""
    return get("/Ratings/MeetingRatings", {"meetingId": meeting_id}) or []


def strike_rates(entity_type=None, jurisdiction=None):
    """Trainer/jockey strike rates (Starter tier), career + last-100 with
    actual-vs-expected. Each item: {startDate, entityId, entityName,
    careerWins/Starts/Seconds/Thirds, last100Wins/Starts/Seconds/Thirds,
    careerExpectedWins, careerPL, careerTurnvoer (sic), last100ExpectedWins,
    last100PL, last100Turnvoer}. entity_type: EntityType enum int
    (jockey/trainer), jurisdiction: JurisdictionType enum int; each omitted
    when None."""
    params = {}
    if entity_type is not None:
        params["entityType"] = entity_type
    if jurisdiction is not None:
        params["jurisdiction"] = jurisdiction
    return get("/form/strikerate", params) or []
