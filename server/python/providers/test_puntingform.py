#!/usr/bin/env python3
"""Offline tests for the Punting Form racecard serve path.

No network and no database: pf_client's fetchers are monkeypatched with
recorded-shape fixtures (payload shapes per pf_client.py docstrings and
PUNTINGFORM_MIGRATION.md Phase A) and the horse-ID bridge cursor is faked.
The golden files pin the exact bytes download_racecards writes, because
downstream consumers read the card file blind — any schema drift must fail
a test, not surface in the tips pipeline.

Regenerate goldens after a deliberate schema change:
    PF_REGEN_GOLDENS=1 python3 -m pytest server/python/providers/test_puntingform.py
then review the fixture diff before committing it.
"""

import json
import os
import sys
from pathlib import Path

import pytest

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import download_racecards
import pf_client
from pf_backfill_results import load_horse_id_bridge
from providers import get_provider
from providers.puntingform import PuntingFormProvider

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Rows as BRIDGE_SQL returns them: (horse_name, horse_id), one row per name —
# the SQL's DISTINCT ON + race_date DESC already resolved duplicates.
BRIDGE_ROWS = [
    ("Fast Horse", "hrs_aus_105246150001"),
    ("Midnight Glory", "hrs_aus_105246150003"),
    ("Ocean Deep (NZ)", "hrs_aus_105246150002"),
    ("Silver Comet", "hrs_aus_105246150004"),
]


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchall(self):
        return list(self.rows)


class FakeConn:
    def close(self):
        pass


def _fixture(name):
    with open(FIXTURES / name) as f:
        return json.load(f)


def _wire(monkeypatch, tmp_path, meetings_fn, detail_fn, bridge_rows=BRIDGE_ROWS):
    """Point the whole serve path at fixtures: env, pf_client, DB cursor,
    output dir. Returns the directory download_racecards will write into."""
    monkeypatch.setenv("PUNTINGFORM_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fixture/only")
    monkeypatch.delenv("RACING_DATA_PROVIDER", raising=False)
    monkeypatch.setattr(pf_client, "meetings_for_date", meetings_fn)
    monkeypatch.setattr(pf_client, "meeting_detail", detail_fn)
    monkeypatch.setattr(
        PuntingFormProvider, "_open_cursor",
        lambda self, url: (FakeConn(), FakeCursor(bridge_rows)),
    )
    out_dir = tmp_path / "racecards"
    monkeypatch.setattr(download_racecards, "OUTPUT_DIR", out_dir)
    return out_dir


def _run_download(monkeypatch, date_str):
    monkeypatch.setattr(sys, "argv", ["download_racecards.py", "--date", date_str])
    download_racecards.main()


def _assert_matches_golden(actual_path, golden_name):
    golden = FIXTURES / golden_name
    actual = actual_path.read_bytes()
    if os.environ.get("PF_REGEN_GOLDENS"):
        golden.write_bytes(actual)
    assert golden.exists(), f"golden {golden_name} missing — regenerate with PF_REGEN_GOLDENS=1"
    assert actual == golden.read_bytes(), (
        f"racecard bytes drifted from {golden_name} — downstream consumers "
        "read this file blind, so any change must be deliberate"
    )


# ----------------------------------------------------------------------
# golden files
# ----------------------------------------------------------------------

def test_golden_racecard_full_day(monkeypatch, tmp_path):
    """Meetings discovery -> detail -> bridge -> byte-stable card on disk.
    Wagga (AUS, non-target) and Te Rapa (NZ) must be filtered out."""
    meetings = _fixture("pf_meetingslist_2026-08-01.json")
    detail = _fixture("pf_meeting_detail_195431.json")

    def detail_fn(meeting_id):
        # Any request beyond the target meeting is a filtering regression.
        assert str(meeting_id) == "195431", f"unexpected meeting_detail({meeting_id})"
        return detail

    out_dir = _wire(monkeypatch, tmp_path, lambda d: meetings, detail_fn)
    _run_download(monkeypatch, "2026-08-01")

    card_path = out_dir / "racecard_2026-08-01.json"
    assert card_path.exists()
    _assert_matches_golden(card_path, "racecard_golden_2026-08-01.json")

    card = json.loads(card_path.read_text())
    assert [m["course"] for m in card] == ["Randwick"]
    runners = card[0]["races"][0]["runners"]
    assert runners[0]["horse_id"] == "hrs_aus_105246150001"
    # "(NZ)" name in the card matched the DB row carrying the same suffix
    assert runners[1]["horse_id"] == "hrs_aus_105246150002"


def test_golden_racecard_unknown_horse(monkeypatch, tmp_path):
    """A horse with no race_results_history match gets the pf<runnerId> id;
    everything else still bridges. Byte-stable second golden."""
    meetings = _fixture("pf_meetingslist_2026-08-02.json")
    detail = _fixture("pf_meeting_detail_195502.json")

    out_dir = _wire(monkeypatch, tmp_path, lambda d: meetings, lambda mid: detail)
    _run_download(monkeypatch, "2026-08-02")

    card_path = out_dir / "racecard_2026-08-02.json"
    assert card_path.exists()
    _assert_matches_golden(card_path, "racecard_golden_2026-08-02.json")

    runners = json.loads(card_path.read_text())[0]["races"][0]["runners"]
    assert runners[0]["horse_id"] == "hrs_aus_105246150001"
    assert runners[1]["horse_id"] == "pf777001"


# ----------------------------------------------------------------------
# horse-ID bridge
# ----------------------------------------------------------------------

def test_bridge_known_name_matches_existing_id():
    bridge = load_horse_id_bridge(FakeCursor(BRIDGE_ROWS))
    provider = PuntingFormProvider()
    runner = provider._runner({"name": "Fast Horse", "runnerId": 620014}, bridge)
    assert runner["horse_id"] == "hrs_aus_105246150001"


def test_bridge_strips_country_suffix_both_sides():
    bridge = load_horse_id_bridge(FakeCursor([("Ocean Deep (NZ)", "hrs_aus_2")]))
    assert bridge == {"oceandeep": "hrs_aus_2"}
    provider = PuntingFormProvider()
    # Card name with and without the suffix resolves to the same identity.
    assert provider._runner({"name": "Ocean Deep (NZ)", "runnerId": 1}, bridge)["horse_id"] == "hrs_aus_2"
    assert provider._runner({"name": "Ocean Deep", "runnerId": 2}, bridge)["horse_id"] == "hrs_aus_2"


def test_bridge_most_recent_prior_row_wins():
    """Recency is resolved in SQL: DISTINCT ON one row per name, ordered
    race_date DESC, so the fetched id is the horse's most recent identity."""
    cursor = FakeCursor([("fast horse", "hrs_aus_latest")])
    bridge = load_horse_id_bridge(cursor)
    assert "DISTINCT ON (LOWER(horse_name))" in cursor.executed[0]
    assert "race_date DESC" in cursor.executed[0]
    assert bridge["fasthorse"] == "hrs_aus_latest"


def test_bridge_unknown_horse_gets_pf_runner_id():
    provider = PuntingFormProvider()
    runner = provider._runner({"name": "Total Stranger", "runnerId": 777001}, {})
    assert runner["horse_id"] == "pf777001"


def test_bridge_requires_database_url(monkeypatch):
    monkeypatch.setenv("PUNTINGFORM_API_KEY", "test-key")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    provider = PuntingFormProvider()
    with pytest.raises(SystemExit) as exc:
        provider.fetch_detailed_races("195431", "2026-08-01", "Randwick")
    assert exc.value.code == 1


# ----------------------------------------------------------------------
# loud failure — exit 1, write nothing
# ----------------------------------------------------------------------

def _boom(*args, **kwargs):
    raise pf_client.PFError("form/meetingslist: 500 upstream error")


def test_envelope_error_exits_1_and_writes_nothing(monkeypatch, tmp_path):
    out_dir = _wire(monkeypatch, tmp_path, _boom, _boom)
    with pytest.raises(SystemExit) as exc:
        _run_download(monkeypatch, "2026-08-01")
    assert exc.value.code == 1
    assert not list(out_dir.glob("racecard_*.json"))


def test_meeting_detail_error_aborts_before_writing(monkeypatch, tmp_path):
    meetings = _fixture("pf_meetingslist_2026-08-01.json")
    out_dir = _wire(monkeypatch, tmp_path, lambda d: meetings, _boom)
    with pytest.raises(pf_client.PFError):
        _run_download(monkeypatch, "2026-08-01")
    assert not list(out_dir.glob("racecard_*.json"))


def test_empty_meetings_list_exits_1_and_writes_nothing(monkeypatch, tmp_path):
    out_dir = _wire(monkeypatch, tmp_path, lambda d: [], lambda mid: {})
    with pytest.raises(SystemExit) as exc:
        _run_download(monkeypatch, "2026-08-01")
    assert exc.value.code == 1
    assert not list(out_dir.glob("racecard_*.json"))


# ----------------------------------------------------------------------
# factory
# ----------------------------------------------------------------------

def test_factory_default_is_puntingform(monkeypatch):
    monkeypatch.delenv("RACING_DATA_PROVIDER", raising=False)
    assert get_provider().name == "puntingform"


def test_factory_theracingapi_still_selectable(monkeypatch):
    monkeypatch.setenv("RACING_DATA_PROVIDER", "theracingapi")
    assert get_provider().name == "theracingapi"
    assert get_provider("theracingapi").name == "theracingapi"
