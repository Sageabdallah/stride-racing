"""Tests for pf_fork_repair.py (DM-G1).

Zero network, zero database: a stateful SQL-aware fake cursor reproduces
Postgres faithfully for every statement the repair issues (same pattern as
test_pf_results_mapper_pin.py — the projected horse_name is lowercased iff
the SELECT list projects LOWER(horse_name), so a regression to the buggy
projection fails here instead of forking horses in prod).

Fixture world:
  * hrs_aus_7 "Ocean Deep (NZ)" — existing horse, forked TWICE by the
    pre-fix writes (pf111, pf222): two pf ids -> one real id is valid.
  * hrs_aus_9 "Plain Jane" — existing horse, never forked.
  * hrs_aus_11 "Two Names (GB)" — existing horse whose name collides with
    ONE of the two stored names of anomalous pf id pf444.
  * pf333 "Mystery Miss" — genuinely new horse (unsuffixed, no bridge
    match): untouched.
  * pf444 — pf id carrying two distinct stored names: report-only anomaly,
    never remapped even though a key would resolve.
  * tips (has race_id) — one pf-race row to remap, one NON-pf-race row
    carrying pf111 that must survive apply untouched.
  * horse_aliases (NO race_id) — holds pf222; excluded from apply unless
    explicitly named.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pf_fork_repair as pfr


RRH_ROWS = [
    {"horse_id": "hrs_aus_7", "horse_name": "Ocean Deep (NZ)",
     "race_id": "rr_100_1", "race_date": "2026-05-10"},
    {"horse_id": "hrs_aus_9", "horse_name": "Plain Jane",
     "race_id": "rr_100_2", "race_date": "2026-05-10"},
    {"horse_id": "hrs_aus_11", "horse_name": "Two Names (GB)",
     "race_id": "rr_100_3", "race_date": "2026-05-10"},
    {"horse_id": "pf111", "horse_name": "Ocean Deep (NZ)",
     "race_id": "pf500_3", "race_date": "2026-07-20"},
    {"horse_id": "pf222", "horse_name": "Ocean Deep (NZ)",
     "race_id": "pf501_1", "race_date": "2026-07-21"},
    {"horse_id": "pf333", "horse_name": "Mystery Miss",
     "race_id": "pf500_3", "race_date": "2026-07-20"},
    {"horse_id": "pf444", "horse_name": "Two Names",
     "race_id": "pf500_4", "race_date": "2026-07-20"},
    {"horse_id": "pf444", "horse_name": "Two Names (GB)",
     "race_id": "pf501_2", "race_date": "2026-07-21"},
]

TIPS_ROWS = [
    {"horse_id": "pf111", "race_id": "pf500_3", "note": "pf race row"},
    {"horse_id": "pf111", "race_id": "tip_9", "note": "non-pf race row"},
    {"horse_id": "hrs_aus_7", "race_id": "tip_1", "note": "old row"},
]

ALIAS_ROWS = [
    {"horse_id": "pf222", "alias": "Ocean Deep"},
]

SCHEMA = [
    ("horse_aliases", False),
    ("race_results_history", True),
    ("tips", True),
]


def fresh_tables():
    return {
        "race_results_history": [dict(r) for r in RRH_ROWS],
        "tips": [dict(r) for r in TIPS_ROWS],
        "horse_aliases": [dict(r) for r in ALIAS_ROWS],
    }


class FakeCursor:
    """Stateful SQL-aware fake: one cursor over in-memory tables."""

    def __init__(self, schema, tables):
        self.schema = schema
        self.tables = tables
        self.backup = []
        self.log = []
        self._result = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        if params is not None:
            # psycopg2 interpolates client-side and delivers a literal %%
            # to the server as one % — match on the server-visible form
            text = text.replace("%%", "%")
        low = text.lower()
        self.log.append((text, params))
        self.rowcount = 0
        if "information_schema.columns" in low:
            self._result = list(self.schema)
        elif low.startswith("select distinct on"):
            # The reference bridge. Postgres lowercases the projected name
            # iff the SELECT list projects LOWER(horse_name); the buggy
            # projection hands norm_name a "(nz)" it cannot strip.
            projection = low.split(" from ")[0].split(
                "distinct on (lower(horse_name))", 1)[-1]
            lowered = "lower(horse_name)" in projection
            nonpf_only = "race_id not like 'pf%'" in low
            chosen = {}
            for row in sorted(self.tables["race_results_history"],
                              key=lambda r: r["race_date"]):
                if nonpf_only and str(row["race_id"]).startswith("pf"):
                    continue
                name = row["horse_name"]
                chosen[name.lower()] = (name.lower() if lowered else name,
                                        row["horse_id"])
            self._result = list(chosen.values())
        elif low.startswith("select distinct horse_id, horse_name"):
            seen, keys = [], set()
            for row in self.tables["race_results_history"]:
                if str(row["horse_id"]).startswith("pf"):
                    k = (row["horse_id"], row["horse_name"])
                    if k not in keys:
                        keys.add(k)
                        seen.append(k)
            self._result = seen
        elif "count(*) filter" in low:
            rows = [r for r in self.tables["race_results_history"]
                    if str(r["race_id"]).startswith("pf")]
            new = sum(1 for r in rows if str(r["horse_id"]).startswith("pf"))
            self._result = [(new, len(rows))]
        elif low.startswith("select count(*) from"):
            name = re.search(r'from "([^"]+)"', low).group(1)
            rows = self.tables[name]
            if "horse_id = any(" in low:
                ids = set(params[0])
                rows = [r for r in rows if r["horse_id"] in ids]
                if "race_id like 'pf%'" in low:
                    rows = [r for r in rows
                            if str(r.get("race_id", "")).startswith("pf")]
            else:
                rows = [r for r in rows
                        if str(r["horse_id"]).startswith("pf")]
            self._result = [(len(rows),)]
        elif low.startswith("select horse_name, race_date::text"):
            rid = params[0]
            cands = [r for r in self.tables["race_results_history"]
                     if r["horse_id"] == rid
                     and not str(r["race_id"]).startswith("pf")]
            cands.sort(key=lambda r: r["race_date"], reverse=True)
            self._result = ([(cands[0]["horse_name"], cands[0]["race_date"])]
                            if cands else [])
        elif low.startswith("create table"):
            self._result = []
        elif "row_to_json" in low:
            name, ids = params[0], set(params[1])
            restrict = "race_id like 'pf%'" in low
            for row in self.tables[name]:
                if row["horse_id"] in ids and (
                        not restrict
                        or str(row.get("race_id", "")).startswith("pf")):
                    self.backup.append((name, dict(row)))
                    self.rowcount += 1
            self._result = []
        elif low.startswith("update"):
            name = re.search(r'update "([^"]+)"', low).group(1)
            mapping = dict(zip(params[0], params[1]))
            restrict = "race_id like 'pf%'" in low
            for row in self.tables[name]:
                if row["horse_id"] in mapping and (
                        not restrict
                        or str(row.get("race_id", "")).startswith("pf")):
                    row["horse_id"] = mapping[row["horse_id"]]
                    self.rowcount += 1
            self._result = []
        else:
            raise AssertionError(f"unexpected SQL: {text}")

    def fetchall(self):
        return self._result


class InterpolatingFakeCursor(FakeCursor):
    """Adds psycopg2-style client-side interpolation checking.

    psycopg2 interpolates %-placeholders whenever params is passed; a
    literal % in such a statement must be escaped as %%. This fake raises
    on any unescaped literal % in a parametrized statement, mimicking the
    driver: escaped pairs are stripped first, then any remaining % not
    starting a %s or %(name)s placeholder is a defect.
    """

    def execute(self, sql, params=None):
        if params is not None and re.search(
                r"%(?![s(])", str(sql).replace("%%", "")):
            raise ValueError("unescaped literal %")
        return super().execute(sql, params)


class FakeConn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def make_cursor():
    return FakeCursor(SCHEMA, fresh_tables())


def silent(*_args):
    pass


# ------------------------------------------------------------ pure detection

def test_reference_bridge_sql_derives_from_mapper_and_excludes_pf_rows():
    sql = pfr.reference_bridge_sql()
    # built on the mapper's BRIDGE_SQL (original-case projection intact,
    # LOWER only in DISTINCT ON / ORDER BY — never re-derived)
    assert pfr.BRIDGE_SQL.split("ORDER BY")[0] in sql
    assert "NOT LIKE 'pf%'" in sql
    projection = " ".join(sql.split()).lower().split(" from ")[0]
    assert "lower(horse_name)" not in projection.split(
        "distinct on (lower(horse_name))", 1)[-1]


def test_reference_bridge_keeps_case_and_cannot_match_a_fork_to_itself():
    cur = make_cursor()
    bridge = pfr.load_reference_bridge(cur)
    # original case survives -> norm_name strips "(NZ)" -> the REAL id; a
    # LOWER() projection would corrupt this to "oceandeepnz" (the prod bug)
    assert bridge["oceandeep"] == "hrs_aus_7"
    assert "oceandeepnz" not in bridge
    # non-pf rows only: no pf id is a bridge target, so a forked id's own
    # PF-written name can never "match" itself
    assert not any(str(v).startswith("pf") for v in bridge.values())


def test_suffixed_fork_detected():
    remap, anomalies, clean = pfr.compute_remap(
        {"pf111": {"Ocean Deep (NZ)"}}, {"oceandeep": "hrs_aus_7"})
    assert remap["pf111"]["real_id"] == "hrs_aus_7"
    assert remap["pf111"]["suffix"] == "(NZ)"
    assert anomalies == [] and clean == []


def test_unsuffixed_genuine_new_untouched():
    remap, anomalies, clean = pfr.compute_remap(
        {"pf333": {"Mystery Miss"}}, {"oceandeep": "hrs_aus_7"})
    assert remap == {} and anomalies == []
    assert clean == ["pf333"]


def test_two_pf_ids_remapping_to_one_real_id_is_valid():
    remap, _, _ = pfr.compute_remap(
        {"pf111": {"Ocean Deep (NZ)"}, "pf222": {"Ocean Deep (NZ)"}},
        {"oceandeep": "hrs_aus_7"})
    assert remap["pf111"]["real_id"] == "hrs_aus_7"
    assert remap["pf222"]["real_id"] == "hrs_aus_7"


def test_pf_id_with_two_names_reported_not_remapped():
    # even though "Two Names (GB)" would resolve to hrs_aus_11
    remap, anomalies, clean = pfr.compute_remap(
        {"pf444": {"Two Names", "Two Names (GB)"}}, {"twonames": "hrs_aus_11"})
    assert remap == {} and clean == []
    assert anomalies == [("pf444", ["Two Names", "Two Names (GB)"])]


def test_suffix_breakdown_buckets():
    assert pfr.suffix_of("Ocean Deep (NZ)") == "(NZ)"
    assert pfr.suffix_of("Two Names (GB)") == "(GB)"
    assert pfr.suffix_of("Dublin Bay (IRE)") == "(IRE)"
    assert pfr.suffix_of("Paris Lane (FR)") == "other"
    assert pfr.suffix_of("Mystery Miss") == "none"


def test_full_detection_over_fixture_world():
    cur = make_cursor()
    report = pfr.detect(cur)
    assert report["pf_ids_scanned"] == 4
    assert report["forks"] == 2          # pf111, pf222
    assert report["clean"] == 1          # pf333
    assert len(report["anomalies"]) == 1  # pf444
    assert set(report["remap"]) == {"pf111", "pf222"}
    assert report["suffix_breakdown"]["(NZ)"] == 2
    # per-table: rrh pf rows 5 (2 fork rows), tips pf rows 2 (1 fork row),
    # aliases pf rows 1 (1 fork row, no race_id -> unrestricted count)
    assert report["per_table"]["race_results_history"]["pf_rows"] == 5
    assert report["per_table"]["race_results_history"]["fork_rows"] == 2
    assert report["per_table"]["tips"]["fork_rows"] == 1
    assert report["per_table"]["horse_aliases"]["fork_rows"] == 1
    assert report["per_table"]["horse_aliases"]["has_race_id"] is False


# ------------------------------------------------------- report vs apply

def test_report_mode_writes_nothing(monkeypatch):
    monkeypatch.setattr(pfr, "git_grep_horse_id_files", lambda: [])
    cur = make_cursor()
    pfr.run(cur, FakeConn(), apply=False, out=silent)
    writes = [t for t, _ in cur.log
              if t.lower().startswith(("update", "insert", "create"))]
    assert writes == []
    assert all(r["horse_id"].startswith(("pf", "hrs"))
               for r in cur.tables["race_results_history"])
    # original ids unchanged
    assert cur.tables["race_results_history"] == fresh_tables()[
        "race_results_history"]


def test_apply_backs_up_before_any_update(monkeypatch):
    monkeypatch.setattr(pfr, "git_grep_horse_id_files", lambda: [])
    cur = make_cursor()
    conn = FakeConn()
    pfr.run(cur, conn, apply=True, out=silent)
    assert conn.commits == 1
    # the FIRST write statement of the run creates the backup table
    first_write = next(t for t, _ in cur.log
                       if t.lower().startswith(("update", "insert", "create")))
    assert first_write.lower().startswith("create table")
    for table in ("race_results_history", "tips"):
        backup_idx = next(i for i, (t, p) in enumerate(cur.log)
                          if t.lower().startswith("insert")
                          and p[0] == table)
        update_idx = next(i for i, (t, _) in enumerate(cur.log)
                          if t.lower().startswith(f'update "{table}"'))
        assert backup_idx < update_idx
    # every remapped row pre-imaged with its ORIGINAL horse_id
    backed = {(t, r["horse_id"]) for t, r in cur.backup}
    assert ("race_results_history", "pf111") in backed
    assert ("race_results_history", "pf222") in backed
    assert ("tips", "pf111") in backed


def test_apply_touches_only_pf_race_id_rows(monkeypatch):
    monkeypatch.setattr(pfr, "git_grep_horse_id_files", lambda: [])
    cur = make_cursor()
    pfr.run(cur, FakeConn(), apply=True, out=silent)
    tips = cur.tables["tips"]
    by_note = {r["note"]: r["horse_id"] for r in tips}
    assert by_note["pf race row"] == "hrs_aus_7"        # remapped
    assert by_note["non-pf race row"] == "pf111"        # NOT touched
    assert by_note["old row"] == "hrs_aus_7"
    # rrh: forked pf rows remapped, anomaly + genuine-new untouched
    rrh = {(r["horse_id"], r["race_id"]) for r in cur.tables["race_results_history"]}
    assert ("hrs_aus_7", "pf500_3") in rrh
    assert ("hrs_aus_7", "pf501_1") in rrh
    assert ("pf333", "pf500_3") in rrh
    assert ("pf444", "pf500_4") in rrh
    # and ONLY the fork rows were backed up from tips (non-pf row excluded)
    assert [r["note"] for t, r in cur.backup if t == "tips"] == ["pf race row"]


def test_no_race_id_table_excluded_unless_operator_names_it(monkeypatch):
    monkeypatch.setattr(pfr, "git_grep_horse_id_files", lambda: [])
    cur = make_cursor()
    pfr.run(cur, FakeConn(), apply=True, out=silent)
    assert cur.tables["horse_aliases"][0]["horse_id"] == "pf222"
    # named via --include-table -> repaired too
    cur2 = make_cursor()
    pfr.run(cur2, FakeConn(), apply=True,
            include_tables=["horse_aliases"], out=silent)
    assert cur2.tables["horse_aliases"][0]["horse_id"] == "hrs_aus_7"
    assert any(t == "horse_aliases" for t, _ in cur2.backup)


def test_apply_is_idempotent_and_post_verification_finds_zero(monkeypatch):
    monkeypatch.setattr(pfr, "git_grep_horse_id_files", lambda: [])
    cur = make_cursor()
    report = pfr.run(cur, FakeConn(), apply=True, out=silent)
    assert report["post_apply"]["forks_remaining"] == 0
    assert report["post_apply"]["bridged_vs_new"]["bridged_pct"] > 0
    # second apply: zero forks detected, no UPDATE issued, nothing backed up
    log_len, backup_len = len(cur.log), len(cur.backup)
    report2 = pfr.run(cur, FakeConn(), apply=True, out=silent)
    assert report2["forks"] == 0
    new_sql = [t for t, _ in cur.log[log_len:]]
    assert not any(t.lower().startswith(("update", "insert", "create"))
                   for t in new_sql)
    assert len(cur.backup) == backup_len


# --------------------------------------- DM-H1: literal-% interpolation blind spot

ONE_FORK_SCHEMA = [
    ("race_results_history", True),
    ("tips", True),
]

ONE_FORK_RRH = [
    # stored suffixed name on the REAL id ...
    {"horse_id": "hrs_aus_7", "horse_name": "Ocean Deep (NZ)",
     "race_id": "rr_100_1", "race_date": "2026-05-10"},
    # ... whose PF row created the fork id pf111
    {"horse_id": "pf111", "horse_name": "Ocean Deep (NZ)",
     "race_id": "pf500_3", "race_date": "2026-07-20"},
]

ONE_FORK_TIPS = [
    {"horse_id": "pf111", "race_id": "pf500_3", "note": "pf race row"},
]


def one_fork_cursor():
    return InterpolatingFakeCursor(
        ONE_FORK_SCHEMA,
        {"race_results_history": [dict(r) for r in ONE_FORK_RRH],
         "tips": [dict(r) for r in ONE_FORK_TIPS]})


class TestLiteralPercentInterpolation:
    """Drive every parametrized statement the tool issues through a cursor
    that interpolates like psycopg2. Any literal % left single-escaped in a
    parametrized statement raises here instead of mid-crash in prod — the
    blind spot the G2 audit found (four sites with LIKE 'pf%' + params)."""

    def test_full_report_path_interpolates_cleanly(self, monkeypatch):
        monkeypatch.setattr(pfr, "git_grep_horse_id_files", lambda: [])
        cur = one_fork_cursor()
        report = pfr.run(cur, FakeConn(), apply=False, out=silent)
        # the one fork was found and every report statement executed:
        # per_table_counts fork-rows branch (params + race_id LIKE) ...
        assert report["forks"] == 1
        assert report["per_table"]["race_results_history"]["fork_rows"] == 1
        assert report["per_table"]["tips"]["fork_rows"] == 1
        # ... and eyeball_sample -> RECENT_NONPF_NAME_SQL (params + NOT LIKE)
        assert report["eyeball_sample"] == [
            {"pf_id": "pf111", "pf_name": "Ocean Deep (NZ)",
             "real_id": "hrs_aus_7", "recent_nonpf_name": "Ocean Deep (NZ)",
             "recent_nonpf_date": "2026-05-10"}]

    def test_dry_apply_remap_interpolates_cleanly(self):
        cur = one_fork_cursor()
        tables = [{"table": t, "has_race_id": h} for t, h in ONE_FORK_SCHEMA]
        remap = {"pf111": {"real_id": "hrs_aus_7",
                           "name": "Ocean Deep (NZ)", "suffix": "(NZ)"}}
        updated = pfr.apply_remap(cur, FakeConn(), tables, remap, out=silent)
        # backup INSERT then UPDATE, both with {race_filter} + params
        assert updated == {"race_results_history": 1, "tips": 1}
        backed = {(t, r["horse_id"]) for t, r in cur.backup}
        assert backed == {("race_results_history", "pf111"),
                          ("tips", "pf111")}
        assert cur.tables["race_results_history"][1]["horse_id"] == "hrs_aus_7"
        assert cur.tables["tips"][0]["horse_id"] == "hrs_aus_7"

    def test_escaped_pairs_pass_and_lone_percent_raises(self):
        cur = one_fork_cursor()
        sql_ok = ("SELECT horse_name, race_date::text FROM race_results_history "
                  "WHERE horse_id = %s AND race_id NOT LIKE 'pf%%'")
        # %% and %s are both legal in a parametrized statement
        cur.execute(sql_ok, ("hrs_aus_7",))
        with pytest.raises(ValueError, match="unescaped literal %"):
            cur.execute(sql_ok.replace("'pf%%'", "'pf%'"), ("hrs_aus_7",))
