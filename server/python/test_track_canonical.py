"""Tests for the track canonicaliser consolidation + pf_track_dedup.
All mocked, zero network/DB."""
import sys

import pytest

import auto_results_collector as arc
import pf_results_mapper as mapper
import pf_track_dedup as dedup
from pf_results_mapper import TRACK_ALIASES, canonical_track_key


# ------------------------------------------------------- canonicaliser pin

def test_canonicaliser_preexisting_collector_cases():
    # the map as it lived in auto_results_collector.py — behaviour pinned
    assert canonical_track_key("Rosehill") == "rosehillgardens"
    assert canonical_track_key("Rosehill Gardens") == "rosehillgardens"
    assert canonical_track_key("Royal Randwick") == "randwick"
    assert canonical_track_key("Sandown") == "sandownhillside"
    assert canonical_track_key("Sandown-Lakeside") == "sandownlakeside"
    assert canonical_track_key("MRC") == "caulfield"
    assert canonical_track_key("Ascot") == "ascotwa"
    assert canonical_track_key("Morphettville Parks") == "morphettville"
    assert canonical_track_key("Wagga") == "wagga"           # passthrough
    assert canonical_track_key("") == ""
    assert canonical_track_key(None) == ""


K5_PAIRS = [
    ("Bairnsdale", "bet365 Bairnsdale"),
    ("Ballarat Synthetic", "Sportsbet-Ballarat Synthetic"),
    ("Beaudesert", "Aquis Beaudesert"),
    ("Belmont Park", "Belmont"),
    ("Canterbury", "Canterbury Park"),
    ("Devonport Synthetic", "Devonport Tapeta Synthetic"),
    ("Fannie Bay", "Darwin"),
    ("Geelong", "Ladbrokes Geelong"),
    ("Gold Coast", "Aquis Park Gold Coast"),
    ("Hamilton", "bet365 Hamilton"),
    ("Mt Gambier", "Mount Gambier"),
    ("Mt Isa", "Sportsbet Mount Isa"),
    ("Murray Bridge GH", "Thomas Farms RC Murray Bridge"),
    ("Pakenham Synthetic", "Southside Pakenham Synthetic"),
    ("Pinjarra", "Pinjarra Park"),
    ("Pioneer Park", "Ladbrokes Pioneer Park"),
    ("Randwick", "Royal Randwick"),
    ("Randwick-Kensington", "Kensington"),
    ("Rosehill", "Rosehill Gardens"),
    ("Sandown-Lakeside", "Sportsbet Sandown Lakeside"),
    ("Wangaratta", "Sportsbet-Wangaratta"),
]


@pytest.mark.parametrize("pf,old", K5_PAIRS)
def test_canonicaliser_k5_alias_pairs_share_a_key(pf, old):
    assert canonical_track_key(pf) == canonical_track_key(old)


def test_canonicaliser_distinct_tracks_stay_distinct():
    # false-positive merges corrupt more than a missed alias
    assert canonical_track_key("Ballarat") != canonical_track_key("Ballarat Synthetic")
    assert canonical_track_key("Gold Coast") != canonical_track_key("Aquis Park Gold Coast Poly")
    assert canonical_track_key("Pinjarra") != canonical_track_key("Pinjarra Scarpside")
    assert canonical_track_key("Sandown-Hillside") != canonical_track_key("Sandown-Lakeside")
    assert canonical_track_key("Pakenham") != canonical_track_key("Southside Pakenham Synthetic")
    assert canonical_track_key("Randwick") != canonical_track_key("Kensington")


def test_canonicaliser_idempotent():
    for key, target in TRACK_ALIASES.items():
        assert canonical_track_key(target) == target, key
        assert canonical_track_key(canonical_track_key(key)) == canonical_track_key(key)


def test_collector_imports_canonicaliser_back():
    assert arc.canonical_track_key is mapper.canonical_track_key
    assert not hasattr(arc, "TRACK_ALIASES")


# ---------------------------------------------------------------- dedup key

def _row(track, race_id="pf1_3"):
    r = [None] * 21
    r[3], r[4], r[18] = track, "2026-07-08", 3
    r[2] = race_id
    return tuple(r)


def test_row_race_key_equal_across_alias_spellings():
    assert mapper.row_race_key(_row("Belmont")) == mapper.row_race_key(_row("Belmont Park"))
    assert mapper.row_race_key(_row("Belmont")) == ("2026-07-08", "belmontpark", 3)


def test_load_existing_keys_canonicalises():
    class Cur:
        def execute(self, sql, params=None):
            self.sql = sql
        def fetchall(self):
            return [("2026-07-08", "Belmont", 3),
                    ("2026-07-08", "Fannie Bay", 1)]
    keys = mapper.load_existing_keys(Cur(), ["2026-07-08"])
    assert keys == {("2026-07-08", "belmontpark", 3),
                    ("2026-07-08", "fanniebay", 1)}


# ------------------------------------------------------------- find/surviv

def _groups(*sets):
    """sets: (date, track, rnum, race_id, n, created)"""
    return list(sets)


def test_find_doubles_requires_two_spellings():
    rows = _groups(
        ("2026-07-08", "Belmont", 3, "met_aus_1_3", 9, "2026-07-08 20:00"),
        ("2026-07-08", "Belmont Park", 3, "pf240001_3", 9, "2026-07-31 10:00"),
        # same spelling, two race_ids — NOT a double per the spec rule
        ("2026-07-09", "Flemington", 1, "met_aus_9_1", 8, "2026-07-09 20:00"),
        ("2026-07-09", "Flemington", 1, "pf999_1", 8, "2026-07-31 10:00"),
        ("2026-07-09", "Caulfield", 2, "met_aus_8_2", 7, "2026-07-09 20:00"),
    )
    doubles = dedup.find_doubles(rows)
    assert len(doubles) == 1
    assert doubles[0]["key"] == ("2026-07-08", "belmontpark", 3)
    assert len(doubles[0]["sets"]) == 2


def test_survivor_prefers_old_pipeline_set():
    sets = [("Belmont Park", "pf240001_3", 9, "2026-07-31 10:00"),
            ("Belmont", "met_aus_1_3", 9, "2026-07-08 20:00")]
    survivor, deletes = dedup.choose_survivor(sets)
    assert survivor[1] == "met_aus_1_3"
    assert [d[1] for d in deletes] == ["pf240001_3"]


def test_survivor_earliest_inserted_when_both_pf():
    sets = [("Mt Isa", "pf200_2", 8, "2026-07-31 12:00"),
            ("Sportsbet Mount Isa", "pf100_2", 8, "2026-07-30 09:00")]
    survivor, deletes = dedup.choose_survivor(sets)
    assert survivor[1] == "pf100_2"
    assert [d[1] for d in deletes] == ["pf200_2"]


# ------------------------------------------------------- report/apply fake

class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        self.conn.queries.append(s)
        if s.startswith("set session"):
            return
        if "group by 1, 2, 3, 4" in s:
            self._rows = [(d, t, r, rid, len(names), created)
                          for (d, t, r, rid), (created, names)
                          in self.conn.state["rows"].items()]
        elif s.startswith("select distinct horse_name"):
            self._rows = [(n,) for n in
                          self.conn.state["rows"][tuple(params)][1][:3]]
        elif s.startswith("create table if not exists pf_track_dedup_backup"):
            self.conn.state["backup_created"] = True
            self._rows = []
        elif s.startswith("insert into pf_track_dedup_backup"):
            assert self.conn.state.get("backup_created"), \
                "backup insert before backup table DDL"
            key = tuple(params)
            _created, names = self.conn.state["rows"][key]
            self.conn.state["backup"].extend((key, n) for n in names)
            self._rows = []
        elif s.startswith("delete from race_results_history"):
            self.conn.state["rows"].pop(tuple(params))
            self._rows = []
        else:
            self._rows = []

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, rows):
        # rows: {(date, track, rnum, race_id): (created, [horse names])}
        self.state = {"rows": dict(rows), "backup": [],
                      "backup_created": False}
        self.queries = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


DOUBLE_ROWS = {
    ("2026-07-08", "Belmont", 3, "met_aus_1_3"):
        ("2026-07-08 20:00", ["Old Alpha", "Old Beta"]),
    ("2026-07-08", "Belmont Park", 3, "pf240001_3"):
        ("2026-07-31 10:00", ["Old Alpha", "Old Beta"]),
    ("2026-07-31", "Mt Isa", 2, "pf100_2"):
        ("2026-07-30 09:00", ["Gamma"]),
    ("2026-07-31", "Sportsbet Mount Isa", 2, "pf200_2"):
        ("2026-07-31 12:00", ["Gamma"]),
    ("2026-07-09", "Flemington", 1, "met_aus_9_1"):
        ("2026-07-09 20:00", ["Solo"]),
}


def _run_main(monkeypatch, conn, argv):
    import psycopg2
    monkeypatch.setattr(psycopg2, "connect", lambda url: conn)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake.invalid/db")
    monkeypatch.setattr(sys, "argv", argv)
    return dedup.main()


def test_report_mode_writes_nothing(monkeypatch, capsys):
    conn = FakeConn(DOUBLE_ROWS)
    code = _run_main(monkeypatch, conn, ["pf_track_dedup.py"])
    assert code == 0
    writes = [q for q in conn.queries
              if q.startswith(("insert", "delete", "create"))]
    assert writes == []
    out = capsys.readouterr().out
    assert "doubled race-days" in out and "rows to delete: 3" in out
    assert "Report only" in out
    assert conn.commits == 0


def test_apply_backs_up_before_deleting_and_commits(monkeypatch):
    conn = FakeConn(DOUBLE_ROWS)
    code = _run_main(monkeypatch, conn, ["pf_track_dedup.py", "--apply"])
    assert code == 0
    create_i = next(i for i, q in enumerate(conn.queries)
                    if q.startswith("create table"))
    insert_is = [i for i, q in enumerate(conn.queries) if q.startswith("insert")]
    delete_is = [i for i, q in enumerate(conn.queries) if q.startswith("delete")]
    assert create_i < min(insert_is)          # DDL before any pre-image
    assert max(insert_is) < min(delete_is)    # ALL backups before ANY delete
    assert conn.commits == 1
    # 3 rows deleted across 2 doomed sets; survivors kept
    remaining = set(conn.state["rows"])
    assert ("2026-07-08", "Belmont", 3, "met_aus_1_3") in remaining      # old survives
    assert ("2026-07-08", "Belmont Park", 3, "pf240001_3") not in remaining
    assert ("2026-07-31", "Mt Isa", 2, "pf100_2") in remaining           # earlier pf survives
    assert ("2026-07-31", "Sportsbet Mount Isa", 2, "pf200_2") not in remaining
    assert ("2026-07-09", "Flemington", 1, "met_aus_9_1") in remaining   # untouched
    # every deleted row has a pre-image in the backup
    backed = {k for k, _n in conn.state["backup"]}
    assert backed == {("2026-07-08", "Belmont Park", 3, "pf240001_3"),
                      ("2026-07-31", "Sportsbet Mount Isa", 2, "pf200_2")}


def test_second_apply_is_idempotent(monkeypatch, capsys):
    conn = FakeConn(DOUBLE_ROWS)
    assert _run_main(monkeypatch, conn, ["pf_track_dedup.py", "--apply"]) == 0
    n_queries = len(conn.queries)
    capsys.readouterr()
    assert _run_main(monkeypatch, conn, ["pf_track_dedup.py", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "zero doubles" in out
    new_writes = [q for q in conn.queries[n_queries:]
                  if q.startswith(("insert", "delete", "create"))]
    assert new_writes == []
