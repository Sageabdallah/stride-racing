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
    monkeypatch.setattr(arc.pf_client, "meetings_for_date",
                        lambda d: list(PF_MEETINGS))
    monkeypatch.setattr(arc.pf_client, "results_for_meeting",
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
    def boom(date):
        raise pf_client.PFError("/form/meetingslist: 400 Bad Request")
    monkeypatch.setattr(arc.pf_client, "meetings_for_date", boom)
    assert arc.fetch_results_for_date("2026-07-19") == []


def test_fetch_results_per_meeting_error_skips_meeting(monkeypatch):
    monkeypatch.setattr(arc.pf_client, "meetings_for_date",
                        lambda d: list(PF_MEETINGS[:1]))

    def boom(mid):
        raise pf_client.PFError("/form/results: 500 Server Error")
    monkeypatch.setattr(arc.pf_client, "results_for_meeting", boom)
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
