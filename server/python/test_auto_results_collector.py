"""Interface pin + fetch-layer tests for auto_results_collector.py (DM-K2).

The stride-app scheduler and other callers depend on the public functions,
their signatures, and their return shapes — the Punting Form fetch-layer
swap must be invisible to them. Zero network, zero database: pf_client is
stubbed; process_pending_races is exercised with a fake connection that has
no pending races.
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pf_client
import auto_results_collector as arc


# ---------------------------------------------------------------- fixtures

PF_MEETINGS = [
    {"meetingId": 991, "isBarrierTrial": False,
     "track": {"name": "Flemington", "country": "AUS"}},
    {"meetingId": 995, "isBarrierTrial": True,
     "track": {"name": "Trialville", "country": "AUS"}},
]

PF_RESULTS = [
    {"meetingId": 991, "track": "Flemington", "meetingDate": "2026-07-19",
     "raceResults": [
         {"raceId": "r1", "raceNumber": 4, "trackConditionLabel": "Good 4",
          "runners": [
              {"position": 1, "runner": "Known Hero", "runnerId": 111,
               "price": 4.2},
              {"position": 2, "runner": "Mystery Miss", "runnerId": 333,
               "price": 8.5},
              {"position": None, "runner": "Late Scratch", "runnerId": 444,
               "price": 6.0},
          ]},
         {"raceId": "r2", "raceNumber": 5, "trackConditionLabel": "Good 4",
          "runners": [  # no placed runner: race not resulted, excluded
              {"position": None, "runner": "Ghost", "runnerId": 999},
          ]},
     ]},
]


def _stub_pf(monkeypatch):
    # The fetch logic lives in providers/puntingform now; the collector only
    # routes through get_provider(). pf_client is one shared module object,
    # so patching it here patches the provider's view of it too. RACING_DATA_
    # PROVIDER is cleared so the factory default (puntingform) is what routes.
    monkeypatch.delenv("RACING_DATA_PROVIDER", raising=False)
    monkeypatch.setattr(pf_client, "meetings_for_date",
                        lambda d: list(PF_MEETINGS))
    monkeypatch.setattr(pf_client, "results_for_meeting",
                        lambda mid: list(PF_RESULTS))


# ---------------------------------------------------------- interface pin

def _sig(fn):
    # Python 3.14 renders Optional[str] as 'str | None'; pin the interface,
    # not the interpreter's spelling of it.
    return (str(inspect.signature(fn))
            .replace("str | None", "Optional[str]")
            .replace("Dict | None", "Optional[Dict]"))


def test_public_signatures_unchanged():
    assert _sig(arc.fetch_results_for_date) == \
        "(race_date: str) -> List[Dict]"
    assert _sig(arc.process_pending_races) == \
        "(target_date: Optional[str] = None) -> Dict"
    assert _sig(arc.get_pending_races) == \
        "(conn, target_date: Optional[str] = None) -> List[Dict]"
    assert _sig(arc.run_daemon) == "(check_interval: int = 5)"
    assert _sig(arc.find_race_in_results) == \
        "(track: str, race_number: int, results: List[Dict]) -> Optional[Dict]"
    assert _sig(arc.update_prediction_audit) == \
        "(conn, track: str, race_number: int, race_date: str, race_result: Dict) -> int"


def test_fetch_layer_has_no_racing_api_left():
    assert not hasattr(arc, "get_api_credentials")
    assert not hasattr(arc, "RACING_API_BASE_URL")
    assert "requests" not in dir(arc)


# --------------------------------------------------------- fetch-layer shape

def test_fetch_results_for_date_return_shape(monkeypatch):
    _stub_pf(monkeypatch)
    results = arc.fetch_results_for_date("2026-07-19")

    assert isinstance(results, list)
    assert len(results) == 1                    # unresulted race 5 excluded
    race = results[0]
    for key in ("course", "track", "race_number", "race_name", "distance", "runners"):
        assert key in race
    assert race["course"] == "Flemington"
    assert race["track"] == "Flemington"
    assert race["race_number"] == 4
    assert len(race["runners"]) == 3            # scratched runner stays listed
    runner = race["runners"][0]
    assert runner["horse_name"] == "Known Hero"
    assert runner["horse"] == "Known Hero"      # get_runner_name fallback key
    assert runner["position"] == 1
    assert runner["sp"] == 4.2


def test_fetch_output_flows_through_existing_matcher(monkeypatch):
    _stub_pf(monkeypatch)
    results = arc.fetch_results_for_date("2026-07-19")
    found = arc.find_race_in_results("Flemington", 4, results)
    assert found is results[0]
    assert arc.find_race_in_results("Flemington", 9, results) is None
    names = [arc.get_runner_name(r) for r in found["runners"]]
    assert names == ["known hero", "mystery miss", "late scratch"]
    assert arc.extract_runner_result(found["runners"][0], field_size=2) == \
        {"position": 1, "starting_price": 4.2}


def test_fetch_results_error_returns_empty_list(monkeypatch):
    monkeypatch.delenv("RACING_DATA_PROVIDER", raising=False)

    def boom(date):
        raise pf_client.PFError("/form/meetingslist: 400 Bad Request")
    monkeypatch.setattr(pf_client, "meetings_for_date", boom)
    assert arc.fetch_results_for_date("2026-07-19") == []


def test_fetch_results_per_meeting_error_skips_meeting(monkeypatch):
    monkeypatch.delenv("RACING_DATA_PROVIDER", raising=False)
    monkeypatch.setattr(pf_client, "meetings_for_date",
                        lambda d: list(PF_MEETINGS[:1]))

    def boom(mid):
        raise pf_client.PFError("/form/results: 500 Server Error")
    monkeypatch.setattr(pf_client, "results_for_meeting", boom)
    assert arc.fetch_results_for_date("2026-07-19") == []


# --------------------------------------------- process_pending_races shape

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self):
        self.commits = 0

    def cursor(self, cursor_factory=None):
        return _FakeCursor([])   # no pending races

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def test_process_pending_races_empty_shape(monkeypatch):
    monkeypatch.setattr(arc, "get_db_connection", lambda: _FakeConn())
    result = arc.process_pending_races("2026-07-19")
    assert result == {
        "success": True,
        "message": "No pending races to process",
        "races_checked": 0,
        "results_collected": 0,
    }


# ------------------------------------------- a broken fetch must be LOUD
#
# The collector settles prediction_audit.actual_position, which is what the
# calibrator's shadow evidence and gate 4 are computed from. A fetch that
# fails outright used to be indistinguishable from "nothing resulted yet":
# both returned [], both incremented retry_count, and the process exited 0.
# A missing PUNTINGFORM_API_KEY would therefore have settled nothing, every
# night, reporting success. These pin the distinction.

class _PendingConn(_FakeConn):
    def cursor(self, cursor_factory=None):
        return _FakeCursor([{"id": "s1", "track": "Flemington",
                             "race_number": 4, "race_date": "2026-07-19"}])


def _pending_env(monkeypatch):
    monkeypatch.setattr(arc, "get_db_connection", lambda: _PendingConn())
    monkeypatch.setattr(arc, "_ensure_sp_backfill_index", lambda conn: None)
    monkeypatch.setattr(arc, "increment_retry_count", lambda conn, sid: None)


def test_provider_failure_is_reported_as_failure(monkeypatch):
    _pending_env(monkeypatch)
    monkeypatch.setattr(arc, "fetch_results_for_date_checked",
                        lambda d: ([], "provider error: no api key"))
    result = arc.process_pending_races("2026-07-19")
    assert result["success"] is False
    assert result["fetch_errors"] == {"2026-07-19": "provider error: no api key"}


def test_unresulted_races_are_not_a_failure(monkeypatch):
    # Fetch worked, the day simply has nothing resulted yet: retry territory,
    # not an alarm. Distinguishing this from the case above is the point.
    _pending_env(monkeypatch)
    monkeypatch.setattr(arc, "fetch_results_for_date_checked",
                        lambda d: ([], None))
    result = arc.process_pending_races("2026-07-19")
    assert result["success"] is True
    assert result["fetch_errors"] == {}
    assert result["failed"] == 1


def test_checked_fetch_reports_reason_on_provider_error(monkeypatch):
    monkeypatch.delenv("RACING_DATA_PROVIDER", raising=False)

    def boom(date):
        raise pf_client.PFError("/form/meetingslist: 400 Bad Request")
    monkeypatch.setattr(pf_client, "meetings_for_date", boom)
    results, reason = arc.fetch_results_for_date_checked("2026-07-19")
    assert results == []
    assert reason and "400 Bad Request" in reason


def test_checked_fetch_reports_no_reason_on_success(monkeypatch):
    _stub_pf(monkeypatch)
    results, reason = arc.fetch_results_for_date_checked("2026-07-19")
    assert len(results) == 1
    assert reason is None


def test_main_exits_nonzero_when_fetch_failed(monkeypatch):
    # The scheduled job reads the exit code and nothing else.
    monkeypatch.setattr(sys, "argv", ["auto_results_collector.py",
                                      "--date", "2026-07-19"])
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
    monkeypatch.setattr(arc, "process_pending_races",
                        lambda d: {"success": False,
                                   "fetch_errors": {"2026-07-19": "boom"}})
    with pytest.raises(SystemExit) as excinfo:
        arc.main()
    assert excinfo.value.code == 1


def test_main_exits_zero_on_success(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["auto_results_collector.py",
                                      "--date", "2026-07-19"])
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
    monkeypatch.setattr(arc, "process_pending_races",
                        lambda d: {"success": True, "fetch_errors": {}})
    arc.main()   # must not raise SystemExit
