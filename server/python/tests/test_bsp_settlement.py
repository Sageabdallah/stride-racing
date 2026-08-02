"""BSP settlement: parsing the real file format, matching into ledger rows,
the explicit-marker contract (never a silent NULL), CLV landing via the
shared formula, and the late-file backfill path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bsp_settlement
import evidence_store
from portfolio_risk import closing_line_value

# Header + rows exactly as promo.betfair.com serves them (2026-08-01 file),
# plus a harness row that must be filtered and a second Alpha to prove the
# race-number key disambiguates what a horse-only key cannot.
CSV = """event_id,menu_hint,event_name,event_dt,selection_id,selection_name,win_lose,bsp,ppwap,morningwap,ppmax,ppmin,ipmax,ipmin,morningtradedvol,pptradedvol,iptradedvol
260562390,Melton (AUS) 1st Aug,R7 1720m Trot M,01-08-2026 11:49,73405005,3. The Sky Is The Limit,1,4.45259479,5.17,3.03,5.70,5.70,1.00,1001.00,3243.89,1324.61,1515.68
260545993,Sandown (AUS) 1st Aug,R6 1600m CL1,01-08-2026 06:25,101163156,1. Alpha,1,4.0,4.1,4.2,4.5,3.9,4.4,3.8,353.56,247.63,105.93
260545994,Sandown (AUS) 1st Aug,R7 2100m CL2,01-08-2026 07:00,101163157,2. Alpha,0,6.5,6.4,6.6,7.0,6.0,6.9,5.9,100.0,90.0,80.0
260545995,Sandown (AUS) 1st Aug,R6 1600m CL1,01-08-2026 06:25,101163158,4. Beta,0,0,0,0,0,0,0,0,0,0,0
"""


def _index():
    return bsp_settlement.build_index(bsp_settlement.parse_bsp_csv(CSV))


class TestParseAndIndex:
    def test_harness_filtered_cloth_stripped(self):
        entries = bsp_settlement.parse_bsp_csv(CSV)
        assert all("Trot" not in e["horse"] for e in entries)
        assert len(entries) == 3  # Melton trot row is gone
        alpha = [e for e in entries if e["horse"] == "Alpha"]
        assert {e["race_number"] for e in alpha} == {6, 7}

    def test_duplicate_horse_needs_race_number(self):
        idx = _index()
        assert idx["by_horse"][bsp_settlement._norm("Alpha")] is bsp_settlement._AMBIGUOUS
        assert idx["by_key"][(6, bsp_settlement._norm("Alpha"))]["bsp"] == 4.0

    def test_race_date_filter_drops_adjacent_day_rows(self):
        # The file stamped D holds D-1's races; the menu_hint belt-filter
        # keeps a boundary row from the wrong day out of the index.
        entries = bsp_settlement.parse_bsp_csv(CSV, race_date="2026-08-01")
        assert len(entries) == 3
        assert bsp_settlement.parse_bsp_csv(CSV, race_date="2026-07-31") == []

    def test_file_stamp_is_publication_date(self):
        assert bsp_settlement._file_stamp("2026-08-01") == "02082026"


def _row(id, horse, race_no, taken=5.0, won=True):
    return {"id": id, "race_date": "2026-08-01", "track": "Sandown",
            "race_number": race_no, "horse_name": horse,
            "price_taken": taken, "won": won, "sp_status": None}


class TestApply:
    def test_matched_row_gets_sp_and_clv_via_shared_formula(self):
        out = bsp_settlement.apply_to_rows([_row(1, "Alpha", 6)], _index())
        (u,) = out["updates"]
        assert u["sp"] == 4.0 and u["sp_status"] == "BSP_MATCHED"
        assert u["clv_pct"] == round(closing_line_value(5.0, 4.0), 4)
        assert u["clv_pct"] == 25.0  # 5.0 taken vs 4.0 close

    def test_no_taken_price_means_null_clv_but_sp_lands(self):
        out = bsp_settlement.apply_to_rows(
            [_row(2, "Alpha", 6, taken=None)], _index())
        (u,) = out["updates"]
        assert u["sp"] == 4.0 and u["clv_pct"] is None

    def test_zero_bsp_is_marked_not_silent(self):
        out = bsp_settlement.apply_to_rows([_row(3, "Beta", 6, won=False)],
                                           _index())
        (u,) = out["updates"]
        assert u["sp"] is None and u["sp_status"] == "ZERO_BSP"

    def test_unknown_horse_is_marked_no_match(self):
        out = bsp_settlement.apply_to_rows([_row(4, "Gamma", 6)], _index())
        (u,) = out["updates"]
        assert u["sp"] is None and u["sp_status"] == "NO_MATCH_IN_FILE"

    def test_won_disagreement_is_counted_never_overwritten(self, capsys):
        out = bsp_settlement.apply_to_rows([_row(5, "Alpha", 6, won=False)],
                                           _index())
        assert out["stats"]["won_disagree"] == 1
        (u,) = out["updates"]
        assert u["sp"] == 4.0  # SP still lands; won is not our column
        assert "RESULT DISAGREEMENT" in capsys.readouterr().err


class TestLateFileBackfill:
    def test_404_then_published_then_cached(self, tmp_path, monkeypatch):
        monkeypatch.delenv("STRIDE_EVIDENCE_BUCKET", raising=False)
        monkeypatch.setattr(evidence_store, "local_dir", lambda: tmp_path)
        calls = {"n": 0}

        def day1(url):
            calls["n"] += 1
            return 404, None

        status, text = bsp_settlement.fetch_csv("2026-08-01", http_get=day1)
        assert status == "FILE_NOT_PUBLISHED" and text is None

        def day2(url):
            calls["n"] += 1
            return 200, CSV

        status, text = bsp_settlement.fetch_csv("2026-08-01", http_get=day2)
        assert status == "OK" and "Alpha" in text
        # Third run: served from the local cache, no HTTP at all.
        def never(url):
            raise AssertionError("cache miss")

        status, text = bsp_settlement.fetch_csv("2026-08-01", http_get=never)
        assert status == "OK" and calls["n"] == 2


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.conn.calls.append((" ".join(sql.split()), params))

    def close(self):
        pass


class _Conn:
    def __init__(self):
        self.calls, self.commits = [], 0

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


class TestWriteShapes:
    def test_matched_and_marker_updates_use_different_shapes(self):
        conn = _Conn()
        n = bsp_settlement.write_updates(conn, [
            {"id": 1, "sp": 4.0, "clv_pct": 25.0,
             "sp_source": "betfair_bsp_file", "sp_status": "BSP_MATCHED"},
            {"id": 2, "sp": None, "clv_pct": None,
             "sp_source": None, "sp_status": "NO_MATCH_IN_FILE"},
        ])
        assert n == 2 and conn.commits == 1
        full, marker = conn.calls
        assert "SET sp = %s, price_close = %s, clv_pct = %s" in full[0]
        assert full[1][:2] == (4.0, 4.0)  # sp and legacy alias get the same value
        assert "SET sp_source = %s, sp_status = %s" in marker[0]
        assert "clv_pct" not in marker[0]
