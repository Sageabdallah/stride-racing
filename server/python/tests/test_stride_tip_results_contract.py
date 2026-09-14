"""stride_tip_results has two writers that each carry a copy of its DDL and
its result vocabulary, and a settlement path that once ran only when a BET
pick existed. The 2026-09-14 audit found the results side dead in the cloud
(no tips file on the Fargate task), the two DDLs disagreeing on 'PENDING',
and the tracker's duplicate check keyed on the tier. These pin the fixes.

Faked cursors throughout: nothing here proves the SQL against the schema.
The live proof is the first race-day results-collect after deploy.
"""
import json
import re
from contextlib import contextmanager

import pytest

import shadow_pl_tracker as spt
import stride_results_collector as src

_CHECK = re.compile(r"CHECK \(result IN \(([^)]*)\)\)")


def _allowed(sql: str) -> set:
    m = _CHECK.search(sql)
    assert m, "no result CHECK constraint in the DDL"
    return {v.strip().strip("'") for v in m.group(1).split(",")}


# ------------------------------------------------------------ one vocabulary

def test_both_ddls_allow_the_same_result_values():
    """Postgres validates existing rows on ADD CONSTRAINT: whichever script
    runs its DDL second must accept every value the other one writes."""
    assert _allowed(src.CREATE_TABLE_SQL) == _allowed(spt.MIGRATION_SQL)


def test_the_shared_vocabulary_includes_pending():
    assert "PENDING" in _allowed(src.CREATE_TABLE_SQL)


# ------------------------------------------------- no file vs no scorable pick

@contextmanager
def _phase():
    yield object()


def test_a_zero_bet_card_still_settles_shadow_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(src, "RACECARDS_DIR", tmp_path)
    (tmp_path / "tips_2026-09-12.json").write_text(json.dumps({"races": [
        {"track": "Rosehill", "race_number": 1, "bet_status": "NO_BET",
         "bet_pick": {"horse": "Alpha"}}]}))
    calls = []
    monkeypatch.setattr(src, "_db_phase", _phase)
    monkeypatch.setattr(src, "ensure_table", lambda conn: calls.append("ensure_table"))
    monkeypatch.setattr(src, "settle_shadow_and_ledger",
                        lambda conn, dates: (calls.append(("settle", tuple(dates)))
                                             or {"shadow_matched": 3, "ledger_settled": 0}))
    out = src.collect_results(["2026-09-12"])
    assert out["status"] == "no_picks"
    assert out["tips_files"] == ["2026-09-12"]
    assert out["shadow_matched"] == 3
    assert calls == ["ensure_table", ("settle", ("2026-09-12",))]


def test_no_tips_is_only_the_answer_when_no_file_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(src, "RACECARDS_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(src, "settle_shadow_and_ledger",
                        lambda conn, dates: calls.append("settle"))
    out = src.collect_results(["2026-09-13"])
    assert out["status"] == "no_tips"
    assert calls == []


# --------------------------------------------- record: one row per horse

class _Cur:
    def __init__(self, existing, db_rows):
        self._existing, self._db_rows = existing, db_rows
        self.queries = []
        self._last = ""

    def execute(self, sql, params=None):
        self._last = sql
        self.queries.append((" ".join(sql.split()), params))

    def fetchall(self):
        if "convergence_output" in self._last:
            return list(self._db_rows)
        return list(self._existing)

    def close(self):
        pass


class _Conn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur


def _record(monkeypatch, existing, races, db_rows=()):
    import psycopg2.extras
    inserted = []
    monkeypatch.setattr(psycopg2.extras, "execute_values",
                        lambda cur, sql, rows: inserted.extend(rows))
    monkeypatch.setattr(spt, "_load_tips", lambda d: races)
    cur = _Cur(existing, db_rows)
    n = spt.cmd_record("2026-09-14", conn=_Conn(cur))
    return n, inserted, cur


_FIELD = [{"horse": "Alpha", "crowd_classification": "FLAG", "odds": 4.0}]


def test_record_skips_a_horse_already_recorded_whatever_its_tier(monkeypatch):
    """Keyed on the horse: a re-run after tier drift must not add a second
    row that the shadow report would count in two tiers."""
    races = [{"track": "Rosehill", "race_number": 1, "field_size": 8,
              "full_field": _FIELD}]
    n, inserted, _ = _record(monkeypatch, existing=[("Rosehill", 1, "Alpha")],
                             races=races)
    assert n == 0 and inserted == []


def test_record_skips_on_the_db_path_too(monkeypatch):
    db_rows = [("Rosehill", 1, "Alpha", "LOCK", 0.9, 0.8, 3, 8)]
    n, inserted, _ = _record(monkeypatch, existing=[("Rosehill", 1, "Alpha")],
                             races=[], db_rows=db_rows)
    assert n == 0 and inserted == []


def test_record_inserts_a_new_horse_once_per_run(monkeypatch):
    races = [{"track": "Rosehill", "race_number": 1, "field_size": 8,
              "full_field": _FIELD + _FIELD}]     # the same horse listed twice
    n, inserted, _ = _record(monkeypatch, existing=[], races=races)
    assert n == 1 and len(inserted) == 1
    assert inserted[0][4] == "Alpha" and inserted[0][6] == "FLAG"


def test_record_looks_for_duplicates_among_its_own_rows_only(monkeypatch):
    """The collector's BET and COVERAGE rows for the same horse are not the
    tracker's, and must not make it skip the tier row."""
    _, _, cur = _record(monkeypatch, existing=[], races=[])
    sql, params = next(q for q in cur.queries if "FROM stride_tip_results" in q[0])
    assert "NOT IN %s" in sql
    assert params == ("2026-09-14", spt.COLLECTOR_TIP_TYPES)
    assert set(spt.COLLECTOR_TIP_TYPES) == {"BET", "COVERAGE"}


def test_record_prints_the_summary_the_handler_parses(monkeypatch, capsys):
    _record(monkeypatch, existing=[("Rosehill", 1, "Alpha")], races=[])
    out = capsys.readouterr().out
    assert "RECORDED 0 rows for 2026-09-14 (1 duplicates skipped)" in out
