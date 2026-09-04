"""The BSP corpus stores every runner, not only the ones we bet.

bsp_settlement fills sp on selection_ledger rows, which answers "what did my
bet close at". Calibration asks a different question — does a stated 19.5%
win probability actually occur 19.5% of the time — and that needs the horses
we passed over as well. These tests pin the parts where a corpus quietly goes
wrong: the publication lag, the date belt-filter, a renamed column, and a
backfill that stores nothing while exiting 0.
"""

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bsp_corpus


HEADER = ("event_id,menu_hint,event_name,event_dt,selection_id,selection_name,"
          "win_lose,bsp,ppwap,morningwap,ppmax,ppmin,ipmax,ipmin,morningtradedvol,"
          "pptradedvol,iptradedvol")


def _row(menu_hint, event_name, selection, bsp, win_lose=0):
    return (f"1,{menu_hint},{event_name},04-09-2026 04:00,2,{selection},"
            f"{win_lose},{bsp},0,0,0,0,0,0,0,0,0")


def _csv(*rows):
    return "\n".join([HEADER, *rows]) + "\n"


AUS = "AUS / Randwick (AUS) 3rd Sep"
AUS_NEXT = "AUS / Randwick (AUS) 4th Sep"


def test_the_file_stamp_carries_the_publication_lag():
    """The file stamped D holds the races of D-1. Getting this backwards
    attributes every race to the wrong day, silently."""
    assert bsp_corpus._file_stamp("2026-09-03") == "04092026"
    assert bsp_corpus._file_stamp("2026-12-31") == "01012027"


def test_rows_from_the_neighbouring_day_are_filtered_out():
    text = _csv(_row(AUS, "R1 1200m Hcap", "1. Alpha", 4.5),
                _row(AUS_NEXT, "R1 1200m Hcap", "1. Beta", 3.2))
    entries = bsp_corpus.parse_bsp_csv(text, race_date="2026-09-03")
    assert [e["horse"] for e in entries] == ["Alpha"], \
        "a boundary row must never cross-match into the adjacent race date"


def test_harness_races_are_excluded_from_a_thoroughbred_corpus():
    text = _csv(_row(AUS, "R7 1720m Trot M", "1. Trotter", 6.0),
                _row(AUS, "R7 1720m Pace M", "2. Pacer", 7.0),
                _row(AUS, "R2 1400m Hcap", "3. Galloper", 5.0))
    entries = bsp_corpus.parse_bsp_csv(text, race_date="2026-09-03")
    assert [e["horse"] for e in entries] == ["Galloper"]


def test_a_renamed_column_is_detected_rather_than_ingested_as_nulls():
    """The parser's contract was verified against 2026 files; a backfill walks
    years of them. A silently renamed column would otherwise land as a table
    full of NULL bsp that still looks successfully ingested."""
    good = _csv(_row(AUS, "R1 1200m Hcap", "1. Alpha", 4.5))
    assert bsp_corpus.verify_columns(good) == []

    renamed = good.replace("bsp,", "starting_price,", 1)
    assert "bsp" in bsp_corpus.verify_columns(renamed)


def test_ingest_raises_on_a_renamed_column_instead_of_recording_a_quiet_day(monkeypatch):
    renamed = _csv(_row(AUS, "R1 1200m Hcap", "1. Alpha", 4.5)).replace("bsp,", "sp,", 1)
    monkeypatch.setattr(bsp_corpus, "fetch_csv", lambda d, **k: ("OK", renamed))
    with pytest.raises(RuntimeError, match="missing required column"):
        bsp_corpus.ingest_date(None, "2026-09-03", commit=False)


def test_an_unpublished_file_is_a_status_not_a_crash(monkeypatch):
    monkeypatch.setattr(bsp_corpus, "fetch_csv",
                        lambda d, **k: ("FILE_NOT_PUBLISHED", None))
    rec = bsp_corpus.ingest_date(None, "2026-09-03", commit=False)
    assert rec["status"] == "FILE_NOT_PUBLISHED"
    assert rec["rows_written"] == 0


def test_the_record_separates_what_the_file_held_from_what_was_stored(monkeypatch):
    """A row count alone cannot tell a genuinely quiet day from a truncated
    file. Both figures are recorded so the substitution is visible."""
    text = _csv(_row(AUS, "R1 1200m Hcap", "1. Alpha", 4.5, win_lose=1),
                _row(AUS, "R1 1200m Hcap", "2. Beta", 9.0),
                _row(AUS, "R3 1720m Trot M", "3. Trotter", 6.0))
    monkeypatch.setattr(bsp_corpus, "fetch_csv", lambda d, **k: ("OK", text))
    rec = bsp_corpus.ingest_date(None, "2026-09-03", commit=False)
    assert rec["rows_in_file"] == 3, "all three rows are hinted with the date"
    assert rec["rows_written"] == 2, "the harness runner is not a thoroughbred"
    assert rec["harness_skipped"] == 1
    assert rec["status"] == "OK"
    assert rec["source_file"] == "dwbfpricesauswin04092026.csv"


def test_daterange_is_inclusive_and_refuses_a_reversed_range():
    assert bsp_corpus.daterange("2026-09-01", "2026-09-03") == \
        ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert bsp_corpus.daterange("2026-09-01", "2026-09-01") == ["2026-09-01"]
    with pytest.raises(ValueError):
        bsp_corpus.daterange("2026-09-03", "2026-09-01")


def test_a_backfill_that_stored_nothing_exits_non_zero(monkeypatch, capsys):
    """Exit 0 having written no rows is the silent no-op class this repo keeps
    finding — a backfill is exactly where it would hide."""
    monkeypatch.setattr(bsp_corpus, "fetch_csv",
                        lambda d, **k: ("FILE_NOT_PUBLISHED", None))
    code = bsp_corpus.main(["--since", "2026-09-01", "--until", "2026-09-03"])
    assert code == 3
    assert "ROWS 0" in capsys.readouterr().out


def test_a_hard_failure_inside_the_range_is_reported_not_skipped(monkeypatch, capsys):
    def boom(d, **k):
        if d == "2026-09-02":
            raise RuntimeError("network exploded")
        return "OK", _csv(_row(AUS.replace("3rd Sep", "1st Sep"),
                               "R1 1200m Hcap", "1. Alpha", 4.5))
    monkeypatch.setattr(bsp_corpus, "fetch_csv", boom)
    code = bsp_corpus.main(["--since", "2026-09-01", "--until", "2026-09-02"])
    out = capsys.readouterr().out
    assert code == 4, "a failed date must not be indistinguishable from a quiet one"
    assert "FAILURES 1" in out


def test_sample_reports_the_format_and_writes_nothing(monkeypatch, capsys):
    text = _csv(_row(AUS, "R1 1200m Hcap", "1. Alpha", 4.5, win_lose=1),
                _row(AUS, "R1 1200m Hcap", "2. Beta", 9.0))
    monkeypatch.setattr(bsp_corpus, "fetch_csv", lambda d, **k: ("OK", text))
    code = bsp_corpus.sample("2026-09-03")
    out = capsys.readouterr().out
    assert code == 0
    assert "BSP_SAMPLE result=PASS" in out
    assert "BSP_SAMPLE missing_columns=none" in out
    assert HEADER in out, "the real header must be printed, not described"
    assert "rows_in_file=2" in out and "parsed_thoroughbred=2" in out


def test_sample_fails_loudly_when_the_columns_moved(monkeypatch, capsys):
    text = _csv(_row(AUS, "R1 1200m Hcap", "1. Alpha", 4.5)).replace("bsp,", "sp,", 1)
    monkeypatch.setattr(bsp_corpus, "fetch_csv", lambda d, **k: ("OK", text))
    code = bsp_corpus.sample("2026-09-03")
    out = capsys.readouterr().out
    assert code == 4
    assert "BSP_SAMPLE result=FAIL" in out
    assert "bsp" in out
