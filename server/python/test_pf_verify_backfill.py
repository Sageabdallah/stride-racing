"""Mocked tests for pf_verify_backfill.py — zero network, zero database.

Covers: per-day expected-count logic from fixture payloads, the wall
binary-search on a stubbed 400/200 boundary, the NULL-rate query against a
fake cursor returning known rows, and the scratching-slack rule.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pf_verify_backfill as v


# ---------------------------------------------------------------- fixtures

def _runner(pos):
    return {"position": pos, "runner": f"Horse {pos}", "runnerId": 1000 + (pos or 0)}


FIXTURE_PAYLOAD = [
    {  # meeting 1: two races
        "meetingId": 501, "track": "Flemington", "meetingDate": "2026-07-19",
        "raceResults": [
            {"raceNumber": 1, "runners": [_runner(1), _runner(2), _runner(3),
                                          _runner(None), _runner(0)]},
            {"raceNumber": 2, "runners": [_runner(1), _runner(2), _runner(99),
                                          _runner(100), _runner(-1)]},
        ],
    },
    {  # meeting 2: one race + one race block with no placed runners (ignored)
        "meetingId": 502, "track": "Randwick", "meetingDate": "2026-07-19",
        "raceResults": [
            {"raceNumber": 5, "runners": [_runner(1), _runner(2), _runner(3), _runner(4)]},
            {"raceNumber": 6, "runners": [_runner(None), _runner(0)]},
            {"raceNumber": None, "runners": [_runner(1)]},  # no race number: skipped
        ],
    },
]


class FakeCursor:
    """Minimal cursor double: records SQL, serves one canned row."""

    def __init__(self, row):
        self.row = row
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append(sql)

    def fetchone(self):
        return self.row


# ------------------------------------------------- per-day count logic

def test_expected_counts_from_fixture_payload():
    races, runners = v.expected_from_results_payload(FIXTURE_PAYLOAD)
    # race 1: positions 1,2,3 count (None and 0 are scratched); 3 runners
    # race 2: 1,2,99 count (100 and -1 are invalid); 3 runners
    # race 5: 4 runners; race 6: none placed -> race dropped; None-numbered race skipped
    assert races == 3
    assert runners == 10


def test_expected_counts_empty_payload():
    assert v.expected_from_results_payload([]) == (0, 0)
    assert v.expected_from_results_payload(None) == (0, 0)


def test_aus_race_meetings_filter():
    meetings = [
        {"meetingId": 1, "track": {"country": "AUS", "name": "Flemington"}},
        {"meetingId": 2, "track": {"country": "AUS", "name": "Trialsville"},
         "isBarrierTrial": True},
        {"meetingId": 3, "track": {"country": "NZL", "name": "Ellerslie"}},
    ]
    kept = v.aus_race_meetings(meetings)
    assert [m["meetingId"] for m in kept] == [1]


# ------------------------------------------------------- slack rule

def test_slack_rule_two_runner_gap_passes():
    # deficit of exactly 2 runners per race = scratching-explainable
    flags = v.assess_day(pf_meetings=2, pf_races=4, pf_runners=20,
                         db_meetings=2, db_races=4, db_rows=12)
    assert flags == []


def test_slack_rule_three_runner_gap_flags():
    flags = v.assess_day(pf_meetings=2, pf_races=4, pf_runners=20,
                         db_meetings=2, db_races=4, db_rows=8)
    assert len(flags) == 1
    assert "runner deficit" in flags[0]


def test_slack_rule_missing_race_flags():
    flags = v.assess_day(pf_meetings=2, pf_races=8, pf_runners=60,
                         db_meetings=2, db_races=7, db_rows=58)
    assert any("missing race" in f for f in flags)


def test_slack_rule_missing_meeting_flags():
    flags = v.assess_day(pf_meetings=3, pf_races=8, pf_runners=60,
                         db_meetings=2, db_races=8, db_rows=60)
    assert any("missing meeting" in f for f in flags)


def test_slack_rule_unknown_expected_never_flags_on_counts():
    flags = v.assess_day(pf_meetings=None, pf_races=None, pf_runners=None,
                         db_meetings=0, db_races=0, db_rows=0)
    assert flags == []


# ------------------------------------------------------ wall binary search

def test_wall_binary_search_stubbed_boundary():
    boundary = datetime.date(2026, 6, 9)  # served on/after this date
    calls = {"n": 0}

    def probe(iso):
        calls["n"] += 1
        return datetime.date.fromisoformat(iso) >= boundary

    hi_ok = datetime.date(2026, 8, 1)
    lo_bad = hi_ok - datetime.timedelta(days=v.WALL_SEARCH_SPAN_DAYS)
    wall, lo, used = v.find_wall(probe, lo_bad, hi_ok)
    assert wall == boundary
    assert lo == boundary - datetime.timedelta(days=1)
    assert used <= v.WALL_PROBE_MAX_CALLS
    assert calls["n"] == used


def test_wall_binary_search_budget_exhaustion_is_honest():
    boundary = datetime.date(2026, 6, 9)

    def probe(iso):
        return datetime.date.fromisoformat(iso) >= boundary

    hi_ok = datetime.date(2026, 8, 1)
    lo_bad = hi_ok - datetime.timedelta(days=120)
    wall, lo, used = v.find_wall(probe, lo_bad, hi_ok, max_calls=3)
    assert used == 3
    assert (wall - lo).days > 1           # not resolved
    assert lo < boundary <= wall          # but the true wall is bracketed


def test_wall_binary_search_unservable_top_raises():
    try:
        v.find_wall(lambda iso: False,
                    datetime.date(2026, 4, 1), datetime.date(2026, 8, 1))
    except RuntimeError as e:
        assert "cannot bound" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


# ------------------------------------------------------- NULL-rate SQL

def test_null_rates_against_fake_cursor():
    # 1000 pf rows; canned per-column NULL counts
    canned = (1000, 0, 12, 0, 34, 56, 7, 0, 890)
    cur = FakeCursor(canned)
    total, rates = v.null_rates(cur)
    assert total == 1000
    by_col = {r["column"]: r for r in rates}
    assert by_col["distance_m"]["pct"] == 0.0
    assert by_col["race_class"]["pct"] == 1.2
    assert by_col["going"]["pct"] == 3.4
    assert by_col["opponents_json"]["pct"] == 89.0
    sql = cur.queries[0]
    assert "race_id LIKE 'pf%'" in sql
    for col in v.NULL_COLUMNS:
        assert col in sql


def test_null_rates_zero_rows_no_divide_by_zero():
    cur = FakeCursor((0, 0, 0, 0, 0, 0, 0, 0, 0))
    total, rates = v.null_rates(cur)
    assert total == 0
    assert all(r["pct"] == 0.0 for r in rates)


# ------------------------------------------------------- gap-day split

def test_classify_gap_days_lost_vs_recoverable():
    zero = ["2026-06-05", "2026-07-31", "2026-08-01"]
    au_racing = {"2026-07-31"}  # 08-01 has zero rows AND no AU meeting: not missing
    lost, recoverable = v.classify_gap_days(zero, "2026-06-09", au_racing)
    assert lost == ["2026-06-05"]
    assert recoverable == ["2026-07-31"]


def test_classify_gap_days_none_lost():
    lost, recoverable = v.classify_gap_days(["2026-07-31"], "2026-06-09",
                                            {"2026-07-31"})
    assert lost == []
    assert recoverable == ["2026-07-31"]
