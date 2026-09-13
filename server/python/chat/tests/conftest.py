"""Fakes for the chat tests. No network, no database, no SDK.

Every backend the tools touch is replaced by an object with the same two or
three methods the code calls, so a test states exactly what the backend
would have returned and asserts what the tool made of it. The real
PuntingForm facade is used over a fake pf_client so the cache, the window
and the meeting lookup are exercised for real.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import pytest

from chat.artifacts import ArtifactMissing
from chat.db import DatabaseUnavailable
from chat.pf import PuntingForm
from chat.tools import Context

import pf_client  # flat module; real exception classes for the facade


# -- database ---------------------------------------------------------------

class FakeDB:
    """query(sql, params) answered by the first handler whose key is a
    substring of the SQL. A handler is a row list, a callable(sql, params)
    or an exception to raise. Unmatched SQL returns no rows."""

    def __init__(self, handlers: Optional[Dict[str, Any]] = None):
        self.handlers: Dict[str, Any] = dict(handlers or {})
        self.calls: List[Tuple[str, Tuple[Any, ...]]] = []

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        self.calls.append((sql, tuple(params)))
        for key, handler in self.handlers.items():
            if key in sql:
                if isinstance(handler, Exception):
                    raise handler
                if callable(handler):
                    return handler(sql, params)
                return [dict(r) for r in handler]
        return []

    def sql_containing(self, needle: str) -> List[str]:
        return [s for s, _ in self.calls if needle in s]


def relation_missing_error(table: str) -> DatabaseUnavailable:
    return DatabaseUnavailable(f'query failed: UndefinedTable: relation "{table}" does not exist')


# -- artifacts ----------------------------------------------------------------

class FakeArtifacts:
    def __init__(self, files: Optional[Dict[str, Any]] = None):
        self.files: Dict[str, Any] = dict(files or {})
        self.reads: List[str] = []

    def get_json(self, rel_path: str):
        self.reads.append(rel_path)
        if rel_path not in self.files:
            raise ArtifactMissing(rel_path)
        return self.files[rel_path], f"fake:{rel_path}"

    def describe(self) -> str:
        return "fake artifacts"


# -- punting form -------------------------------------------------------------

class FakePFClient:
    """A pf_client stand-in: the same function names and the real exception
    classes, answering from dicts and counting calls."""

    PFError = pf_client.PFError
    PFAuthError = pf_client.PFAuthError

    def __init__(self, meetings: Optional[Dict[str, list]] = None, details: Optional[Dict[Any, dict]] = None,
                 results: Optional[Dict[Any, list]] = None, scratchings: Optional[list] = None,
                 conditions: Optional[list] = None, speedmaps: Optional[list] = None,
                 ratings: Optional[list] = None, strike_rates: Optional[list] = None,
                 raise_on: Optional[Exception] = None):
        self.meetings = meetings or {}
        self.details = details or {}
        self.results = results or {}
        self._scratchings = scratchings or []
        self._conditions = conditions or []
        self._speedmaps = speedmaps or []
        self._ratings = ratings or []
        self._strike_rates = strike_rates or []
        self.raise_on = raise_on
        self.calls: List[Tuple[str, tuple]] = []

    def _hit(self, name, *args):
        self.calls.append((name, args))
        if self.raise_on is not None:
            raise self.raise_on

    def meetings_for_date(self, iso_date):
        self._hit("meetings_for_date", iso_date)
        if iso_date not in self.meetings:
            raise pf_client.PFError(f"/form/meetingslist: HTTP 400: before the wall")
        return self.meetings[iso_date]

    def results_for_meeting(self, meeting_id):
        self._hit("results_for_meeting", meeting_id)
        return self.results.get(meeting_id, [])

    def meeting_detail(self, meeting_id):
        self._hit("meeting_detail", meeting_id)
        return self.details.get(meeting_id)

    def scratchings(self, jurisdiction=None):
        self._hit("scratchings", jurisdiction)
        return self._scratchings

    def conditions(self, jurisdiction=None):
        self._hit("conditions", jurisdiction)
        return self._conditions

    def speedmaps_for_meeting(self, meeting_id, race_no=0):
        self._hit("speedmaps_for_meeting", meeting_id, race_no)
        return self._speedmaps

    def ratings_for_meeting(self, meeting_id):
        self._hit("ratings_for_meeting", meeting_id)
        return self._ratings

    def strike_rates(self, entity_type=None, jurisdiction=None):
        self._hit("strike_rates", entity_type, jurisdiction)
        return self._strike_rates


def pf_meeting(meeting_id: int, track: str, state: str = "NSW") -> dict:
    return {"meetingId": meeting_id, "track": {"name": track, "state": state, "country": "AUS"},
            "tabMeeting": True, "isBarrierTrial": False, "railPosition": "True",
            "expectedCondition": "Good 4"}


# -- anthropic ---------------------------------------------------------------

def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def tool_use_block(tool_id: str, name: str, args: dict):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=args)


def response(content, stop_reason="end_turn", input_tokens=100, output_tokens=20,
             cache_read=0, cache_creation=0):
    return SimpleNamespace(
        content=list(content), stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                              cache_read_input_tokens=cache_read,
                              cache_creation_input_tokens=cache_creation))


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs):
        # Snapshot the message list: the loop appends to it after the call,
        # and the real SDK serialises at call time, so a recorded call must
        # show what the API saw, not what the list became.
        self.calls.append(dict(kwargs, messages=list(kwargs.get("messages") or [])))
        if not self.script:
            raise AssertionError("no scripted response left for messages.create")
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class FakeAnthropic:
    def __init__(self, script):
        self.messages = FakeMessages(script)


# -- payloads ------------------------------------------------------------------

def pick(horse: str, **kw) -> dict:
    base = {"rank": 1, "horse": horse, "barrier": 3, "jockey": "J Smith", "trainer": "T Jones",
            "odds": 4.5, "has_real_market_odds": True, "fair_odds": 3.6, "win_pct": 27.8,
            "raw_model_pct": 25.0, "edge_pct": 5.6, "confidence": "high", "selection_score": 14.2,
            "staking": "2u", "value_rating": "Excellent", "convergence_tier": "CONFIRMED",
            "convergence_score": 71.0, "consensus_score": 62.0, "consensus_mentions": 4,
            "market_signal_score": 58.0, "selection_origin": "model_backed",
            "selection_origin_reason": "Raw model leader clears the edge threshold.",
            "should_bet": True, "key_factors": ["Best closing 600m", "Drops in class", "Inside draw"],
            "ai_insight": "THE FORM: " + "x" * 900}
    base.update(kw)
    return base


def tips_payload(date: str = "2026-04-12") -> dict:
    return {
        "date": date, "generated_at": f"{date}T08:05:00",
        "summary": {"total_races": 2, "total_selections": 2, "positive_edge": 1, "high_confidence": 1,
                    "total_units": 2},
        "convergence_summary": {"confirmed": 1, "crowd_only": 0, "model_only": 1, "rejected": 3,
                                "gated_no_bet": 1},
        "selection_contract": {"version": "v2-explicit-bet-coverage", "bet_races": 1, "no_bet_races": 1},
        "best_bets": [pick("Pride Of Jenni")],
        "races": [
            {"track": "Royal Randwick", "race_number": 5, "race_name": "The Big Handicap", "distance": "1600m",
             "going": "Soft 5", "race_class": "BM88", "field_size": 12, "bet_status": "BET",
             "bet_status_reason": "Raw model leader sits in the validated value band.",
             "bet_pick": pick("Pride Of Jenni"), "coverage_pick": pick("Pride Of Jenni"),
             "top_picks": [pick("Pride Of Jenni"), pick("Mr Brightside", rank=2, odds=6.0, edge_pct=1.2,
                                                        confidence="medium", should_bet=False),
                           pick("Third Horse", rank=3, should_bet=False)],
             "full_field": [{"horse": "Pride Of Jenni", "saddle_number": 1, "barrier": 3, "odds": 4.5,
                             "win_pct": 27.8, "edge_pct": 5.6, "form": "1x213", "confidence": "high",
                             "is_tipped": True, "tip_rank": 1, "ai_insight": "y" * 500}
                            for _ in range(26)]},
            {"track": "Royal Randwick", "race_number": 6, "race_name": "The Sprint", "distance": "1200m",
             "going": "Soft 5", "race_class": "G3", "field_size": 10, "bet_status": "NO_BET",
             "bet_status_reason": "Crowd gate: no consensus support for the model leader.",
             "bet_pick": None, "coverage_pick": pick("Imperatriz", odds=2.2, edge_pct=-1.0,
                                                     confidence="low", should_bet=False),
             "top_picks": [pick("Imperatriz", odds=2.2, edge_pct=-1.0, confidence="low", should_bet=False)],
             "full_field": []},
            {"track": "Flemington", "race_number": 7, "race_name": "The Cup Prelude", "distance": "2000m",
             "going": "Good 4", "race_class": "G2", "field_size": 9, "bet_status": "BET",
             "bet_status_reason": "Model leader, consensus aligned.",
             "bet_pick": pick("Amelia's Jewel", odds=3.1, edge_pct=7.0),
             "coverage_pick": pick("Amelia's Jewel", odds=3.1, edge_pct=7.0),
             "top_picks": [pick("Amelia's Jewel", odds=3.1, edge_pct=7.0)], "full_field": []},
        ],
    }


def racecard_payload(date: str = "2026-04-12") -> list:
    return [{
        "date": date, "meet_id": "m1", "course": "Royal Randwick",
        "races": [
            {"course": "Royal Randwick", "date": date, "distance": "1600m", "going": "Soft 5",
             "class": "BM88", "race_name": "The Big Handicap", "race_number": 5, "off_time": "15:10",
             "race_status": "Final", "runners": [
                 {"horse_id": "hrs_1", "horse": "Pride Of Jenni", "draw": 3, "form": "1x213", "jockey": "J Smith",
                  "number": 1, "odds": [{"bookmaker": "tab", "decimal": 4.5}], "scratched": False,
                  "trainer": "T Jones", "weight": 57.0, "age": 6, "sex": "Mare",
                  "stats": {"career_win_percent": 35.7, "career_place_percent": 64.3}},
                 {"horse_id": "hrs_2", "horse": "Mr Brightside", "draw": 8, "form": "2111", "jockey": "C Williams",
                  "number": 2, "odds": [], "scratched": False, "trainer": "B Hayes", "weight": 58.5},
             ]},
            {"course": "Royal Randwick", "date": date, "distance": "1200m", "going": "Soft 5",
             "class": "G3", "race_name": "The Sprint", "race_number": 6, "off_time": "15:50",
             "race_status": "Final", "runners": [
                 {"horse": "Imperatriz", "draw": 1, "number": 4, "scratched": False, "odds": []},
                 {"horse": "Late Scratch", "draw": 2, "number": 5, "scratched": True, "odds": []},
             ]},
        ],
    }]


def consensus_payload() -> dict:
    return {
        "randwick_R5": {
            "Pride Of Jenni": {"consensus_score": 72.0, "crowd_score": 68.0, "vote_pct": 62.5,
                               "total_mentions": 5, "independent_mentions": 3, "commercial_mentions": 2,
                               "market_alignment": True, "tipsters_polled": 8, "bucket_spread": 3,
                               "high_confidence_mentions": 2, "buckets": ["a", "b", "c"],
                               "independent_source_rate": 0.6, "reasoning_alignment": "maps to lead",
                               "sources": ["Source A", "Source B", "Source C"]},
            "Mr Brightside": {"consensus_score": 41.0, "crowd_score": 40.0, "vote_pct": 12.5,
                              "total_mentions": 1, "independent_mentions": 1, "commercial_mentions": 0,
                              "market_alignment": False, "tipsters_polled": 8, "bucket_spread": 1,
                              "high_confidence_mentions": 0, "buckets": ["a"], "sources": ["Source A"]},
            "Nobody Likes": {"consensus_score": 35.0, "total_mentions": 0, "sources": []},
        },
        "flemington_R7": {"Amelia's Jewel": {"consensus_score": 80.0, "total_mentions": 6, "sources": ["S"]}},
    }


def market_payload() -> dict:
    return {
        "randwick_R5": {
            "Pride Of Jenni": {"market_signal_score": 78.0, "signal_type": "STEAM", "baseline_price": 5.5,
                               "morning_price": 4.5, "movement_pct": 18.18},
            "Mr Brightside": {"market_signal_score": 50.0, "signal_type": "STABLE", "baseline_price": 6.0,
                              "morning_price": 6.0, "movement_pct": 0.0},
            "Drifter": {"market_signal_score": 30.0, "signal_type": "STRONG_DRIFT", "baseline_price": 8.0,
                        "morning_price": 12.0, "movement_pct": -50.0},
        },
        "randwick_R6": {"Imperatriz": {"market_signal_score": 50.0, "signal_type": "UNKNOWN",
                                       "baseline_price": None, "morning_price": 2.2, "movement_pct": 0.0}},
    }


# -- fixtures --------------------------------------------------------------------

@pytest.fixture
def artifacts() -> FakeArtifacts:
    return FakeArtifacts({
        "racecards/tips_2026-04-12.json": tips_payload(),
        "server/python/racecards/racecard_2026-04-12.json": racecard_payload(),
        "server/python/intelligence/consensus_2026-04-12.json": consensus_payload(),
        "server/python/intelligence/market_signals_2026-04-12.json": market_payload(),
        "server/python/intelligence/consensus_2026-04-13.json": {},
    })


@pytest.fixture
def pf_client_fake() -> FakePFClient:
    return FakePFClient(
        meetings={"2026-09-10": [pf_meeting(101, "Randwick"), pf_meeting(102, "Caulfield", "VIC")],
                  "2026-04-12": [pf_meeting(201, "Royal Randwick")]},
        details={101: {"track": {"name": "Randwick"}, "races": [
            {"raceNumber": 1, "raceName": "Opener", "distance": 1200, "trackCondition": "Good 4",
             "runners": [{"tabNo": 1, "name": "Fast Horse", "barrier": 2, "weight": 58.0,
                          "jockey": {"fullName": "J Smith"}, "trainer": {"fullName": "T Jones"},
                          "last10": "1x213", "winPct": 35.7, "placePct": 64.3}]}]}},
        results={101: [{"meetingId": 101, "track": {"name": "Randwick"}, "raceResults": [
            {"raceNumber": 1, "trackConditionLabel": "Good 4", "officialRaceTime": "1:09.80",
             "runners": [{"position": 2, "runner": "Second Horse", "margin": 1.5, "price": 6.0, "tabNo": 3},
                         {"position": 1, "runner": "Fast Horse", "margin": 0.0, "price": 2.8, "tabNo": 1}]}]}]},
        scratchings=[{"meetingDate": "2026-09-10T00:00:00", "track": "Randwick", "raceNo": 3, "tabNo": 7,
                      "runnerId": 9, "timeStamp": "2026-09-10T06:00:00", "deduction": 0.05}],
        conditions=[{"meetingDate": "2026-09-10T00:00:00", "track": "Randwick", "trackCondition": "Good 4",
                     "weather": "Fine", "rail": "True", "penetrometer": 4.8}],
    )


@pytest.fixture
def pf(pf_client_fake) -> PuntingForm:
    return PuntingForm(client=pf_client_fake, today="2026-09-10")


@pytest.fixture
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture
def ctx(artifacts, pf, db) -> Context:
    return Context(artifacts=artifacts, pf=pf, db=db, today="2026-09-10")
