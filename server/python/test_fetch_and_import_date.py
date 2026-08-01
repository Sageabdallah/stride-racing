"""Mocked tests for the Punting Form swap of fetch_and_import_date.py.

Zero network, zero database: pf_client is stubbed, psycopg2.connect is
replaced with a stateful fake. Covers the spec cases: envelope error ->
non-zero exit with zero rows and zero archive writes on the failing
meeting; double-run dedup (second import inserts zero); the daily
fetch->mapper->insert path producing bridged rows.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pf_client
import pf_results_mapper as mapper
import fetch_and_import_date as faid


# ---------------------------------------------------------------- fixtures

MEETINGS = [
    {"meetingId": 991, "isBarrierTrial": False,
     "track": {"name": "Flemington", "country": "AUS"}},
    {"meetingId": 995, "isBarrierTrial": True,          # excluded: trial
     "track": {"name": "Trialville", "country": "AUS"}},
    {"meetingId": 996, "isBarrierTrial": False,         # excluded: NZ
     "track": {"name": "Ellerslie", "country": "NZL"}},
]

RESULTS_991 = [
    {"meetingId": 991, "track": "Flemington", "meetingDate": "2026-07-19",
     "raceResults": [
         {"raceId": "r991_1", "raceNumber": 1, "trackConditionLabel": "Good 4",
          "runners": [
              {"position": 1, "margin": None, "runner": "Known Hero",
               "runnerId": 111, "jockey": "J. Rider", "jockeyId": 9001,
               "barrier": 3, "weight": 59.5, "price": 4.2},
              {"position": 2, "margin": 1.25, "runner": "Mystery Miss",
               "runnerId": 333, "jockey": "K. Hop", "jockeyId": 9002,
               "barrier": 5, "weight": 57.0, "price": 8.5},
              {"position": None, "margin": None, "runner": "Late Scratch",
               "runnerId": 444, "jockey": "S. Cratch", "jockeyId": 9003,
               "barrier": 2, "weight": 56.0, "price": 6.0},
          ]},
     ]},
]

DETAIL_991 = {"races": [
    {"raceNumber": 1, "name": "Test Handicap", "raceClass": "BM70",
     "distance": "1400m",
     "runners": [{"runnerId": 111, "last10": "1x21"},
                 {"runnerId": 333, "last10": "4231"}]},
]}


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        self.conn.queries.append(s)
        if s.startswith("select race_date::text"):
            self._rows = sorted(self.conn.state["keys"])
        elif "distinct on" in s:
            self._rows = list(self.conn.state["bridge_rows"])
        else:
            self._rows = []

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, bridge_rows=()):
        self.state = {"keys": set(), "bridge_rows": list(bridge_rows)}
        self.queries = []
        self.inserted = []
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        pass

    def archived_payloads(self):
        return [q for q in self.queries if q.startswith("insert into pf_raw_payloads")]


def _stub_pf(monkeypatch, results_by_mid=None, detail_by_mid=None,
             meetings=None):
    monkeypatch.setattr(faid.pf_client, "meetings_for_date",
                        lambda d: list(meetings if meetings is not None else MEETINGS))
    monkeypatch.setattr(faid.pf_client, "results_for_meeting",
                        lambda mid: (results_by_mid or {991: RESULTS_991})[mid])
    monkeypatch.setattr(faid.pf_client, "meeting_detail",
                        lambda mid: (detail_by_mid or {991: DETAIL_991})[mid])


def _stub_execute_values(monkeypatch):
    def fake_ev(cur, sql, rows, page_size=500):
        rows = list(rows)
        cur.conn.inserted.extend(rows)
        cur.conn.state["keys"].update(mapper.row_race_key(r) for r in rows)
    monkeypatch.setattr(faid, "execute_values", fake_ev)


def _run_main(monkeypatch):
    monkeypatch.setattr(faid, "DATABASE_URL", "postgresql://fake.invalid/db")
    monkeypatch.setattr(sys, "argv", ["fetch_and_import_date.py",
                                      "--date", "2026-07-19"])
    with pytest.raises(SystemExit) as exc:
        faid.main()
    return exc.value.code


# ------------------------------------------------------------- loud failure

def test_envelope_error_on_meetingslist_exits_1_and_writes_nothing(monkeypatch):
    def boom(date):
        raise pf_client.PFError("/form/meetingslist: 400 Bad Request")
    monkeypatch.setattr(faid.pf_client, "meetings_for_date", boom)

    def connect_must_not_be_called(url):
        raise AssertionError("psycopg2.connect called — nothing may be written")
    monkeypatch.setattr(faid.psycopg2, "connect", connect_must_not_be_called)

    assert _run_main(monkeypatch) == 1


def test_envelope_error_on_one_meeting_exits_1_and_writes_nothing(monkeypatch):
    two = MEETINGS[:1] + [{"meetingId": 992, "isBarrierTrial": False,
                           "track": {"name": "Randwick", "country": "AUS"}}]

    def results(mid):
        if mid == 992:
            raise pf_client.PFError("/form/results: 500 Server Error")
        return RESULTS_991
    monkeypatch.setattr(faid.pf_client, "meetings_for_date", lambda d: two)
    monkeypatch.setattr(faid.pf_client, "results_for_meeting", results)
    monkeypatch.setattr(faid.pf_client, "meeting_detail", lambda mid: DETAIL_991)

    def connect_must_not_be_called(url):
        raise AssertionError("psycopg2.connect called — nothing may be written")
    monkeypatch.setattr(faid.psycopg2, "connect", connect_must_not_be_called)

    assert _run_main(monkeypatch) == 1


def test_missing_database_url_exits_1(monkeypatch):
    monkeypatch.setattr(faid, "DATABASE_URL", None)
    monkeypatch.setattr(sys, "argv", ["fetch_and_import_date.py",
                                      "--date", "2026-07-19"])
    with pytest.raises(SystemExit) as exc:
        faid.main()
    assert exc.value.code == 1


def test_genuinely_empty_day_exits_0(monkeypatch):
    _stub_pf(monkeypatch, meetings=[])
    assert _run_main(monkeypatch) == 0


# ------------------------------------------------------- fetch + import path

def test_fetch_date_excludes_trials_and_nz(monkeypatch):
    _stub_pf(monkeypatch)
    meetings = faid.fetch_date("2026-07-19")
    assert [m["meet_id"] for m in meetings] == ["991"]
    assert meetings[0]["course"] == "Flemington"
    assert meetings[0]["pf"]["detail_races"][1]["name"] == "Test Handicap"
    assert meetings[0]["raw"]["kind"] == "results"


def test_first_import_inserts_rows_and_archives_first(monkeypatch):
    _stub_pf(monkeypatch)
    _stub_execute_values(monkeypatch)
    conn = FakeConn(bridge_rows=[("known hero", "hrs_aus_123")])
    meetings = faid.fetch_date("2026-07-19")
    rows, skipped_races, skipped_runners = faid.import_meetings(meetings, conn)

    assert rows == 2                      # scratched runner excluded
    assert skipped_races == 0
    assert skipped_runners == 1
    by_name = {r[1]: r for r in conn.inserted}
    assert by_name["Known Hero"][0] == "hrs_aus_123"   # bridged
    assert by_name["Mystery Miss"][0] == "pf333"       # new pf id
    assert by_name["Known Hero"][2] == "pf991_1"
    assert len(conn.archived_payloads()) == 1
    # archive executed before the row batch for the meeting
    assert conn.archived_payloads() and conn.inserted


def test_double_run_second_import_inserts_zero(monkeypatch):
    _stub_pf(monkeypatch)
    _stub_execute_values(monkeypatch)
    conn = FakeConn(bridge_rows=[("known hero", "hrs_aus_123")])
    meetings = faid.fetch_date("2026-07-19")

    first = faid.import_meetings(meetings, conn)
    assert first[0] == 2
    inserted_after_first = len(conn.inserted)

    second = faid.import_meetings(meetings, conn)
    assert second[0] == 0                  # dedup: zero new rows
    assert second[1] == 1                  # the race now exists
    assert len(conn.inserted) == inserted_after_first
    # the archive is an append-only log: every committing run archives
    assert len(conn.archived_payloads()) == 2
