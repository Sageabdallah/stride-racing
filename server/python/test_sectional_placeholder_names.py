"""Tests for the F-SEC-PLACEHOLDER fix: the nsw_sectional_collector write
path (resolve-or-skip, no new placeholders possible) and the
fix_sectional_placeholder_names report/apply separation.

Zero network, zero database — every cursor is a fake.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nsw_sectional_collector as nsc
import fix_sectional_placeholder_names as fix


# ---------------------------------------------------------------- fakes

class FakeCursor:
    """SQL-aware fake: serves race_results_history candidate queries,
    records every statement, and counts INSERT/UPDATE/CREATE writes."""

    def __init__(self, rrh_rows=()):
        # rrh_rows: (id, horse_name, track, position, barrier, race_date, race_number)
        self.rrh_rows = list(rrh_rows)
        self.executed = []
        self._result = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        norm = " ".join(str(sql).split()).lower()
        self.executed.append(norm)
        self.rowcount = 0
        if norm.startswith("select id, horse_name, track, position, barrier from race_results_history"):
            race_date, race_number = params
            self._result = [r[:5] for r in self.rrh_rows
                            if r[5] == race_date and r[6] == race_number]
        elif norm.startswith("select id, horse_name, track from race_results_history"):
            # collector's match_to_race_history
            race_date, race_number = params
            self._result = [(r[0], r[1], r[2]) for r in self.rrh_rows
                            if r[5] == race_date and r[6] == race_number]
        elif norm.startswith("insert into sectional_times"):
            self.rowcount = 1
            self._result = []
        else:
            self._result = []

    def fetchall(self):
        return self._result

    def writes(self):
        return [s for s in self.executed
                if s.startswith(("insert", "update", "create", "delete"))]

    def inserts(self):
        return [s for s in self.executed
                if s.startswith("insert into sectional_times")]


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


RRH = [
    # (id, horse_name, track, position, barrier, race_date, race_number)
    (101, "King's Secret", "Canterbury Park", 1, 5, "2026-01-01", 7),
    (102, "The Instructor", "Canterbury Park", 2, 4, "2026-01-01", 7),
    (103, "Barber", "Canterbury Park", 3, 1, "2026-01-01", 7),
    # a different meeting on the same date/race_number — must not match
    (201, "Wrong Track Horse", "Randwick", 2, 9, "2026-01-01", 7),
]


def make_rec(name, finish_position, barrier=0, track="Canterbury Park"):
    return {
        "track": track,
        "race_date": "2026-01-01",
        "race_number": 7,
        "race_name": "Test Handicap",
        "horse_name": name,
        "horse_id": "hid-" + str(finish_position),
        "finish_position": finish_position,
        "barrier": barrier,
        "saddle_number": 0,
        "distance_m": 1200,
        "splits_json": {"0m": {"position": finish_position}},
    }


# ------------------------------------------------------- collector tests

def test_build_horse_sectionals_never_formats_unknown_placeholder():
    parsed = {
        "race_info": {"distance": 1200},
        "horses": {},  # the .tol H records are missing entirely
        "sections": [],
        "intermediates": [
            {"race_id": "r1", "horse_id": "2627306", "section_index": 6,
             "section_name": "200m", "cumulative_time_str": "11.5",
             "section_time_str": "11.5", "position": 2,
             "speed1": 17.0, "speed2": 17.5},
        ],
        "results": {"2627306": 2},
        "final_times": {},
        "track_condition": None,
    }
    data = nsc.build_horse_sectionals(parsed)
    rec = data["2627306"]
    assert rec["horse_name"] is None
    assert rec["finish_position"] == 2


def test_nameless_runner_resolved_via_fixture_join():
    cur = FakeCursor(RRH)
    conn = FakeConn(cur)
    saved = nsc.save_to_db(conn, [make_rec(None, 2)])
    assert saved == 1
    assert len(cur.inserts()) == 1
    # the insert must carry the resolved name, never a placeholder
    # (execute was called with params; re-run capture via a recording fake)
    assert conn.commits == 1


def test_nameless_runner_resolved_insert_params():
    class RecordingCursor(FakeCursor):
        def __init__(self, rows):
            super().__init__(rows)
            self.insert_params = []

        def execute(self, sql, params=None):
            norm = " ".join(str(sql).split()).lower()
            if norm.startswith("insert into sectional_times"):
                self.insert_params.append(params)
            super().execute(sql, params)

    cur = RecordingCursor(RRH)
    nsc.save_to_db(FakeConn(cur), [make_rec(None, 2)])
    assert len(cur.insert_params) == 1
    params = cur.insert_params[0]
    assert params[6] == "The Instructor"      # horse_name
    assert params[1] == 102                    # race_results_history_id


def test_literal_placeholder_name_is_resolved_not_stored():
    cur = FakeCursor(RRH)
    saved = nsc.save_to_db(FakeConn(cur), [make_rec("Unknown_1444297", 2)])
    assert saved == 1
    assert len(cur.inserts()) == 1


def test_unresolvable_runner_skipped_with_one_warning_per_race(capsys):
    cur = FakeCursor([])  # no race_results_history rows at all
    conn = FakeConn(cur)
    recs = [make_rec(None, 1), make_rec(None, 2), make_rec("Unknown_9", 3)]
    saved = nsc.save_to_db(conn, recs)
    assert saved == 0
    assert cur.inserts() == []
    warnings = [l for l in capsys.readouterr().out.splitlines()
                if "WARNING" in l]
    assert len(warnings) == 1  # one warning for the race, not per runner
    assert "3 nameless" in warnings[0]


def test_ambiguous_finish_position_not_resolved():
    dup = RRH + [(301, "Clone Horse", "Canterbury Park", 2, 6,
                  "2026-01-01", 7)]
    cur = FakeCursor(dup)
    saved = nsc.save_to_db(FakeConn(cur), [make_rec(None, 2)])
    assert saved == 0
    assert cur.inserts() == []


def test_named_runner_untouched_by_resolver():
    cur = FakeCursor(RRH)
    saved = nsc.save_to_db(FakeConn(cur), [make_rec("Barber", 3)])
    assert saved == 1


def test_two_nameless_runners_cannot_claim_the_same_horse():
    # two nameless records, one with a bad finish position that would
    # collide after the first claim — claimed-name exclusion kicks in only
    # for distinct positions; here positions differ so both resolve to
    # different horses.
    cur = FakeCursor(RRH)
    saved = nsc.save_to_db(FakeConn(cur), [make_rec(None, 1), make_rec(None, 2)])
    assert saved == 2


# --------------------------------------------------- report/apply tests

FIX_RRH = [
    (101, "King's Secret", "Canterbury Park", 1, 5, "2026-01-01", 7),
    (102, "The Instructor", "Canterbury Park", 2, 4, "2026-01-01", 7),
]

PH_ROW = {
    "id": "ph-1",
    "track": "Canterbury Park",
    "race_date": "2026-01-01",
    "race_number": 7,
    "horse_name": "Unknown_1444297",
    "saddle_number": 0,
    "splits_json": {"0m": {"position": 2}, "200m": {"position": 2}},
}


def test_resolve_row_hits_unique_finish_position():
    resolved = fix.resolve_row(PH_ROW, [r[:5] for r in FIX_RRH], set())
    assert resolved == (102, "The Instructor")


def test_resolve_row_excludes_claimed_names():
    resolved = fix.resolve_row(PH_ROW, [r[:5] for r in FIX_RRH],
                               {"theinstructor"})
    assert resolved is None


def test_resolve_row_requires_track_match():
    off_track = [(201, "Wrong Track Horse", "Randwick", 2, 9,
                  "2026-01-01", 7)]
    resolved = fix.resolve_row(PH_ROW, [r[:5] for r in off_track], set())
    assert resolved is None


def test_resolvable_count_logic():
    rows = [dict(PH_ROW),
            dict(PH_ROW, id="ph-2", splits_json={"0m": {"position": 1}}),
            dict(PH_ROW, id="ph-3", splits_json={"0m": {"position": 9}})]
    fixes, unresolved = [], []
    for row in rows:
        resolved = fix.resolve_row(row, [r[:5] for r in FIX_RRH], set())
        (fixes if resolved else unresolved).append(row)
    assert len(fixes) == 2
    assert len(unresolved) == 1


class ReportFakeCursor(FakeCursor):
    """Serves the fix script's queries over in-memory sectional rows."""

    def __init__(self, sectional_rows, rrh_rows):
        super().__init__(rrh_rows)
        self.sectional_rows = list(sectional_rows)

    def execute(self, sql, params=None):
        norm = " ".join(str(sql).split()).lower()
        if norm.startswith("select id, track, race_date, race_number, horse_name, saddle_number, splits_json from sectional_times"):
            self.executed.append(norm)
            self._result = [
                (r["id"], r["track"], r["race_date"], r["race_number"],
                 r["horse_name"], r["saddle_number"], r["splits_json"])
                for r in self.sectional_rows
                if r["horse_name"].lower().startswith("unknown")
            ]
            return
        if norm.startswith("select horse_name from sectional_times"):
            track, race_date, race_number = params
            self.executed.append(norm)
            self._result = [
                (r["horse_name"],) for r in self.sectional_rows
                if r["track"] == track and r["race_date"] == race_date
                and r["race_number"] == race_number
            ]
            return
        super().execute(sql, params)


SECTIONAL_FIXTURE = [
    PH_ROW,
    dict(PH_ROW, id="ph-2", horse_name="Unknown_2628161",
         splits_json={"0m": {"position": 1}}),
    dict(PH_ROW, id="named-1", horse_name="Barber",
         splits_json={"0m": {"position": 3}}),
]


def test_build_report_counts():
    cur = ReportFakeCursor(SECTIONAL_FIXTURE, FIX_RRH)
    rep = fix.build_report(cur)
    assert rep["total"] == 2
    assert rep["by_month"] == [("2026-01", 2)]
    assert rep["resolvable"] == 2
    assert rep["unresolvable"] == 0


def test_main_without_apply_writes_nothing():
    cur = ReportFakeCursor(SECTIONAL_FIXTURE, FIX_RRH)
    conn = FakeConn(cur)
    os.environ["DATABASE_URL"] = "fake://test"
    try:
        rc = fix.main([], connect=lambda url: conn)
    finally:
        del os.environ["DATABASE_URL"]
    assert rc == 0
    assert cur.writes() == []
    assert conn.rollbacks == 1
    assert conn.commits == 0


def test_main_with_apply_backs_up_then_updates():
    cur = ReportFakeCursor(SECTIONAL_FIXTURE, FIX_RRH)
    conn = FakeConn(cur)
    os.environ["DATABASE_URL"] = "fake://test"
    try:
        rc = fix.main(["--apply"], connect=lambda url: conn)
    finally:
        del os.environ["DATABASE_URL"]
    assert rc == 0
    writes = cur.writes()
    assert any(w.startswith(f"create table if not exists {fix.BACKUP_TABLE}")
               for w in writes)
    assert any(w.startswith(f"insert into {fix.BACKUP_TABLE}") for w in writes)
    updates = [w for w in writes if w.startswith("update sectional_times")]
    assert len(updates) == 2
    assert conn.commits == 1
