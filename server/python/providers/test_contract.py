#!/usr/bin/env python3
"""Contract tests for the racing data provider layer.

Run directly (python3 providers/test_contract.py from server/python) or via
pytest. No network: the adapter's transport is stubbed with canned payloads,
so these verify normalization and validation — the contract every future
provider adapter must satisfy.
"""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers import get_provider, validate_meet_cards, validate_results
from providers.theracingapi import TheRacingAPIProvider


class FakeTransportProvider(TheRacingAPIProvider):
    detail_delay = 0

    def __init__(self, responses):
        super().__init__()
        self.responses = responses
        self.calls = []

    def _api_get(self, endpoint, params=None):
        self.calls.append(endpoint)
        return self.responses.get(endpoint)


def _sample_runner(name="Fast Horse", **extra):
    runner = {
        "horse": name, "number": 1, "draw": 4, "jockey": "J Smith",
        "trainer": "T Jones", "weight": "57kg", "form": "1x21",
        "scratched": False, "odds": [{"bookmaker": "tab", "win_odds": 3.5}],
    }
    runner.update(extra)
    return runner


def _sample_meet_card(date="2026-08-01"):
    return {
        "date": date, "meet_id": "m1", "course": "Randwick",
        "races": [{
            "race_number": 1, "race_name": "Test Hcp", "class": "BM78",
            "distance": "1200m", "going": "Good", "off_time": "2026-08-01T13:00:00",
            "course": "Randwick", "date": date,
            "runners": [_sample_runner(), _sample_runner("Second Horse", number=2, draw=7)],
        }],
    }


def test_factory_default_and_unknown():
    assert get_provider().name == "theracingapi"
    assert get_provider("racing_api").name == "theracingapi"
    try:
        get_provider("nonexistent")
        assert False, "unknown provider must raise"
    except ValueError as e:
        assert "nonexistent" in str(e)


def test_fetch_meets_normalizes_wrapper_and_fallback_keys():
    provider = FakeTransportProvider({
        "/v1/australia/meets": {"meets": [
            {"meet_id": 42, "course": "Flemington"},
            {"id": "abc", "track": "Doomben"},
            {"course": "No Id Track"},
        ]},
    })
    meets = provider.fetch_meets("2026-08-01")
    assert meets == [
        {"meet_id": "42", "course": "Flemington"},
        {"meet_id": "abc", "course": "Doomben"},
    ]


def test_fetch_meets_empty_on_upstream_failure():
    assert FakeTransportProvider({}).fetch_meets("2026-08-01") == []


def test_fetch_detailed_races_fills_course_and_date():
    provider = FakeTransportProvider({
        "/v1/australia/meets/m1/races": {"races": [
            {"race_number": 1},
            {"number": 2},
            {"race_name": "no number — skipped"},
        ]},
        "/v1/australia/meets/m1/races/1": {"race_number": 1, "runners": [_sample_runner()]},
        "/v1/australia/meets/m1/races/2": {"race_number": 2, "course": "Kensington", "runners": []},
    })
    races = provider.fetch_detailed_races("m1", "2026-08-01", "Randwick")
    assert len(races) == 2
    assert races[0]["course"] == "Randwick"
    assert races[0]["date"] == "2026-08-01"
    # Provider-supplied course wins over the meet-level fallback
    assert races[1]["course"] == "Kensington"


def test_fetch_results_filters_unfinished_and_positionless():
    provider = FakeTransportProvider({
        "/v1/australia/meets": {"meets": [{"meet_id": "m1", "track": "Eagle Farm"}]},
        "/v1/australia/meets/m1/races": {"races": [
            {"race_number": 1, "race_status": "Results",
             "runners": [_sample_runner(position=1), _sample_runner("Second Horse", position=2)]},
            {"race_number": 2, "race_status": "Open", "runners": [_sample_runner()]},
            {"race_number": 3, "race_status": "Final", "runners": [_sample_runner()]},
        ]},
    })
    results = provider.fetch_results("2026-08-01")
    assert [r["race_number"] for r in results] == [1]
    assert results[0]["track"] == "Eagle Farm"
    assert results[0]["course"] == "Eagle Farm"


def test_validate_meet_cards_accepts_valid_card():
    report = validate_meet_cards([_sample_meet_card()], "2026-08-01")
    assert report.ok, report.problems
    assert not report.warnings


def test_validate_meet_cards_rejects_missing_identity():
    card = _sample_meet_card()
    del card["meet_id"]
    card["races"][0].pop("race_number")
    report = validate_meet_cards([card], "2026-08-01")
    assert not report.ok
    assert any("meet_id" in p for p in report.problems)
    assert any("race_number" in p for p in report.problems)


def test_validate_meet_cards_rejects_all_empty_races():
    card = _sample_meet_card()
    card["races"][0]["runners"] = []
    report = validate_meet_cards([card], "2026-08-01")
    assert not report.ok
    assert any("zero runners" in p for p in report.problems)


def test_validate_meet_cards_rejects_nameless_runners():
    card = _sample_meet_card()
    card["races"][0]["runners"] = [_sample_runner(horse=""), _sample_runner("Ok Horse")]
    report = validate_meet_cards([card], "2026-08-01")
    assert not report.ok
    assert any("no horse name" in p for p in report.problems)


def test_validate_meet_cards_warns_on_sparse_early_declarations():
    card = _sample_meet_card()
    for runner in card["races"][0]["runners"]:
        runner["draw"] = None
        runner["jockey"] = ""
    report = validate_meet_cards([card], "2026-08-01")
    assert report.ok
    assert any("barrier draw" in w for w in report.warnings)
    assert any("jockey" in w for w in report.warnings)


def test_validate_results():
    valid = [{
        "course": "Randwick", "track": "Randwick", "race_number": 1,
        "race_name": "Test", "distance": "1200m",
        "runners": [{"horse_name": "Fast Horse", "position": 1, "sp": 3.5}],
    }]
    assert validate_results(valid, "2026-08-01").ok

    empty_report = validate_results([], "2026-08-01")
    assert empty_report.ok and empty_report.warnings

    broken = [{"race_number": "abc", "runners": []}]
    report = validate_results(broken, "2026-08-01")
    assert not report.ok
    assert any("track" in p for p in report.problems)
    assert any("race_number" in p for p in report.problems)
    assert any("no runners" in p for p in report.problems)


def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} contract tests passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
