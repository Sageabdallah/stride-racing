"""Mocked tests for DM-K3: download_historical.py + backfill_barrier_trials.py
on the PF window. Zero network, zero database: pf_client is stubbed, the
wall probe is stubbed or driven by a fake 400/200 boundary.

Covers the spec cases: fixture trial meeting -> trials rows (validated
through the REAL import_barrier_trials_to_db.load_rows so the 19 target
columns are preserved exactly) + excluded from results mapping; the
results/trials partition; straddling range -> correct skip list + exit 3;
fully-before-wall -> exit 3 with zero fetches beyond the probe.
"""
import datetime
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pf_client
import pf_results_mapper as mapper
import pf_window
import download_historical as dh
import backfill_barrier_trials as bbt
import import_barrier_trials_to_db as btdb

D = datetime.date

# ---------------------------------------------------------------- fixtures

MIXED_MEETINGS = [
    {"meetingId": 991, "isBarrierTrial": False,
     "track": {"name": "Flemington", "country": "AUS"}},
    {"meetingId": 992, "isBarrierTrial": True,
     "track": {"name": "Randwick", "country": "AUS"}},
    {"meetingId": 993, "isBarrierTrial": False,
     "track": {"name": "Ellerslie", "country": "NZL"}},
]

TRIAL_RESULTS = [
    {"meetingId": 992, "track": "Randwick", "meetingDate": "2026-07-20",
     "raceResults": [
         {"raceId": "t1", "raceNumber": 2, "trackConditionLabel": "Soft 5",
          "officialRaceTime": "1:02.34",
          "runners": [
              {"position": 1, "margin": None, "runner": "Trial Hero",
               "runnerId": 111, "trainer": "A. Trainer", "trainerId": 21,
               "jockey": "J. Rider", "jockeyId": 31, "barrier": 4,
               "weight": None},
              {"position": 2, "margin": 0.75, "runner": "New Face (NZ)",
               "runnerId": 222, "trainer": "B. Trainer", "trainerId": 22,
               "jockey": "K. Hop", "jockeyId": 32, "barrier": 1,
               "weight": None},
          ]},
     ]},
]

TRIAL_DETAIL = {"races": [
    {"raceNumber": 2, "name": "2YO Jump Out", "distance": "1000m", "runners": []},
]}


def _stub_bbt_pf(monkeypatch):
    monkeypatch.setattr(bbt.pf_client, "meetings_for_date",
                        lambda d: list(MIXED_MEETINGS))
    monkeypatch.setattr(bbt.pf_client, "results_for_meeting",
                        lambda mid: list(TRIAL_RESULTS))
    monkeypatch.setattr(bbt.pf_client, "meeting_detail",
                        lambda mid: dict(TRIAL_DETAIL))


def _stub_wall(monkeypatch, boundary):
    """Drive the REAL find_wall with a fake 400/200 boundary; returns the
    probe-call counter."""
    calls = {"probe": 0}

    def probe(iso):
        calls["probe"] += 1
        return datetime.date.fromisoformat(iso) >= boundary
    monkeypatch.setattr(pf_window, "probe_date", probe)
    monkeypatch.setattr(pf_window, "today_aest", lambda: D(2026, 8, 1))
    return calls


# ------------------------------------------------------------- partition

def test_results_trials_partition():
    results = mapper.aus_race_meetings(MIXED_MEETINGS)
    trials = mapper.aus_trial_meetings(MIXED_MEETINGS)
    assert [m["meetingId"] for m in results] == [991]
    assert [m["meetingId"] for m in trials] == [992]
    assert mapper.assert_trial_partition(MIXED_MEETINGS) == MIXED_MEETINGS[:2]


def test_partition_assert_fails_loudly_when_broken(monkeypatch):
    monkeypatch.setattr(mapper, "aus_trial_meetings",
                        lambda ms: list(ms))  # broken: grabs everything
    with pytest.raises(AssertionError, match="partition broken"):
        mapper.assert_trial_partition(MIXED_MEETINGS)


# ------------------------------------------- fixture trial -> trials rows

def test_fixture_trial_meeting_produces_trials_rows(monkeypatch):
    _stub_bbt_pf(monkeypatch)
    bridge = {"trialhero": "hrs_aus_42", "newface": "hrs_aus_99"}
    trial_meets, raw = bbt.fetch_trials_for_date("2026-07-20", bridge=bridge)

    assert len(trial_meets) == 1
    meet = trial_meets[0]
    assert meet["meet_id"] == "992"
    assert meet["course"] == "Randwick"
    assert meet["is_barrier_trial"] is True
    race = meet["races"][0]
    assert race["race_number"] == 2
    assert race["race_name"] == "2YO Jump Out"
    assert race["is_jump_out"] is True          # name-based detection
    assert race["distance"] == "1000m"
    assert race["going"] == "Soft 5"
    assert race["winning_time"] == "1:02.34"
    r1, r2 = race["runners"]
    assert r1["horse_id"] == "hrs_aus_42"       # bridged
    assert r2["horse_id"] == "hrs_aus_99"       # (NZ) stripped, bridged
    assert r1["jockey_id"] == "pf31"
    assert r1["trainer_id"] == "pf21"
    assert r2["position"] == 2
    assert r2["margin"] == 0.75
    assert raw[0]["kind"] == "trials"


def test_trials_json_matches_importer_contract_exactly(monkeypatch, tmp_path):
    """Feed the PF-produced trials JSON through the REAL
    import_barrier_trials_to_db.load_rows — the 19 target columns of
    barrier_trial_results must come out exactly."""
    _stub_bbt_pf(monkeypatch)
    bridge = {"trialhero": "hrs_aus_42", "newface": "hrs_aus_99"}
    trial_meets, _ = bbt.fetch_trials_for_date("2026-07-20", bridge=bridge)

    monkeypatch.setattr(btdb, "TRIALS_DIR", tmp_path)
    (tmp_path / "trials_2026-07-20.json").write_text(json.dumps(trial_meets))
    rows, files_processed, skipped = btdb.load_rows()

    assert files_processed == 1
    assert skipped == 0
    assert len(rows) == 2
    collected_at = rows[0][18]
    assert rows[0] == (
        "hrs_aus_42", "Trial Hero", "pf21", "A. Trainer", "pf31", "J. Rider",
        "2026-07-20", "Randwick", "2YO Jump Out", 2, "1000m", "Soft 5",
        1, 2, None, "1:02.34", True, "992", collected_at,
    )
    assert rows[1][0] == "hrs_aus_99"
    assert rows[1][13] == 2          # position
    assert rows[1][14] == 0.75       # margin parsed by the importer
    assert len(rows[0]) == 19        # the importer's exact column count


# ------------------------------------ trials excluded from results mapping

def test_trial_meeting_excluded_from_results_fetch(monkeypatch):
    results_calls = []
    monkeypatch.setattr(dh.pf_client, "meetings_for_date",
                        lambda d: list(MIXED_MEETINGS))
    monkeypatch.setattr(dh.pf_client, "results_for_meeting",
                        lambda mid: results_calls.append(mid) or list(TRIAL_RESULTS))
    monkeypatch.setattr(dh.pf_client, "meeting_detail", lambda mid: dict(TRIAL_DETAIL))

    day = dh.fetch_day("2026-07-20")
    # only the non-trial AUS meeting was fetched (and it is a target track)
    assert results_calls == [991]
    assert [m["meeting_id"] for m in day["meetings"]] == [991]


# ------------------------------------------------------------- wall honesty

def test_wall_binary_search_on_stubbed_boundary():
    boundary = D(2026, 7, 1)
    calls = {"n": 0}

    def probe(iso):
        calls["n"] += 1
        return datetime.date.fromisoformat(iso) >= boundary
    wall, lo, used = pf_window.find_wall(probe, D(2026, 4, 3), D(2026, 8, 1))
    assert wall == boundary
    assert lo == boundary - datetime.timedelta(days=1)
    assert used <= pf_window.WALL_PROBE_MAX_CALLS


def test_wall_probe_unservable_top_raises():
    with pytest.raises(RuntimeError, match="cannot bound"):
        pf_window.find_wall(lambda iso: False, D(2026, 4, 1), D(2026, 8, 1))


def test_straddling_range_skips_pre_wall_dates_and_exits_3(monkeypatch, capsys):
    _stub_wall(monkeypatch, D(2026, 7, 25))
    fetched = []
    monkeypatch.setattr(dh.pf_client, "meetings_for_date",
                        lambda d: fetched.append(d) or [])
    monkeypatch.setattr(sys, "argv",
                        ["download_historical.py", "--days", "12", "--dry-run"])

    rc = dh.main()
    out = capsys.readouterr().out

    assert rc == 3
    for skipped in ("2026-07-20", "2026-07-21", "2026-07-22",
                    "2026-07-23", "2026-07-24"):
        assert f"SKIP {skipped}" in out
    assert "unrecoverable without the archive purchase" in out
    # only the reachable dates were fetched (end = today-1 = 2026-07-31)
    assert sorted(fetched) == ["2026-07-25", "2026-07-26", "2026-07-27",
                               "2026-07-28", "2026-07-29", "2026-07-30",
                               "2026-07-31"]


def test_fully_before_wall_exits_3_with_zero_fetches_beyond_probe(monkeypatch, capsys):
    wall_calls = _stub_wall(monkeypatch, D(2026, 7, 25))
    fetched = []
    monkeypatch.setattr(dh.pf_client, "meetings_for_date",
                        lambda d: fetched.append(d) or [])
    monkeypatch.setattr(sys, "argv",
                        ["download_historical.py", "--days", "25",
                         "--start-offset", "21", "--dry-run"])  # 07-01..07-10

    rc = dh.main()
    out = capsys.readouterr().out

    assert rc == 3
    assert "Nothing reachable in the requested range." in out
    assert fetched == []                                   # zero day fetches
    assert 0 < wall_calls["probe"] <= pf_window.WALL_PROBE_MAX_CALLS


def test_trials_straddling_range_exits_3_and_fetches_only_reachable(monkeypatch, capsys, tmp_path):
    _stub_wall(monkeypatch, D(2026, 7, 25))
    monkeypatch.setattr(bbt, "OUTPUT_DIR", tmp_path)
    fetched = []
    monkeypatch.setattr(bbt.pf_client, "meetings_for_date",
                        lambda d: fetched.append(d) or [])

    rc = bbt.backfill(datetime.datetime(2026, 7, 20), datetime.datetime(2026, 7, 26), dry_run=True)
    out = capsys.readouterr().out

    assert rc == 3
    for skipped in ("2026-07-20", "2026-07-21", "2026-07-22",
                    "2026-07-23", "2026-07-24"):
        assert f"SKIP {skipped}" in out
    assert fetched == ["2026-07-25", "2026-07-26"]


def test_trials_fully_before_wall_exits_3_no_fetches(monkeypatch, capsys):
    _stub_wall(monkeypatch, D(2026, 7, 25))
    fetched = []
    monkeypatch.setattr(bbt.pf_client, "meetings_for_date",
                        lambda d: fetched.append(d) or [])
    rc = bbt.backfill(datetime.datetime(2026, 7, 1), datetime.datetime(2026, 7, 10), dry_run=True)
    assert rc == 3
    assert "Nothing reachable in the requested range." in capsys.readouterr().out
    assert fetched == []


def test_within_window_exits_0(monkeypatch):
    _stub_wall(monkeypatch, D(2026, 7, 1))
    monkeypatch.setattr(dh.pf_client, "meetings_for_date", lambda d: [])
    monkeypatch.setattr(sys, "argv",
                        ["download_historical.py", "--days", "3", "--dry-run"])
    assert dh.main() == 0


def test_pf_error_exits_1(monkeypatch):
    _stub_wall(monkeypatch, D(2026, 7, 1))

    def boom(d):
        raise pf_client.PFError("/form/meetingslist: failed after 3 attempts")
    monkeypatch.setattr(dh.pf_client, "meetings_for_date", boom)
    monkeypatch.setattr(sys, "argv",
                        ["download_historical.py", "--days", "3", "--dry-run"])
    assert dh.main() == 1
