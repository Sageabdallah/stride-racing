"""Byte-identical pin for the pf_results_mapper extraction (DM-K2).

The FIRST commit of this branch adds this test against the untouched
pf_backfill_results.build_rows; the SECOND commit moves the mapping logic
into pf_results_mapper.py and imports it back into the backfill. This file
is identical in both commits — if the refactor changes any output byte
(row values, opponents_json formatting, races_seen), it fails.

The fixture is a recorded-shape PF day (two meetings: one full-detail race
with a scratched + a non-starter runner, one race with no meeting detail).
It also pins the horse-ID bridge: known normalised name -> existing id
("Known Hero", and "New Face (NZ)" matching bridge key "newface" — the
country suffix is stripped), unknown name -> pf<runnerId> ("Mystery Miss").

Zero network, zero database.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pf_backfill_results import build_rows, norm_name


FIXTURE_DAY = {
    "date": "2026-07-19",
    "meetings": [
        {
            "meeting_id": 991,
            "track": "Flemington",
            "results": [
                {"meetingId": 991, "track": "Flemington", "meetingDate": "2026-07-19",
                 "raceResults": [
                     {"raceId": "r991_1", "raceNumber": 1, "trackConditionLabel": "Good 4",
                      "runners": [
                          {"position": 1, "margin": None, "tabNo": 4, "runner": "Known Hero",
                           "runnerId": 111, "jockey": "J. Rider", "jockeyId": 9001,
                           "barrier": 3, "weight": 59.5, "price": 4.2},
                          {"position": 2, "margin": 1.25, "tabNo": 7, "runner": "New Face (NZ)",
                           "runnerId": 222, "jockey": "K. Hop", "jockeyId": 9002,
                           "barrier": 5, "weight": 57.0, "price": 8.5},
                          {"position": 3, "margin": 2.75, "tabNo": 9, "runner": "Mystery Miss",
                           "runnerId": 333, "jockey": None, "jockeyId": None,
                           "barrier": 8, "weight": 55.5, "price": 12.0},
                          {"position": None, "margin": None, "tabNo": 2, "runner": "Late Scratch",
                           "runnerId": 444, "jockey": "S. Cratch", "jockeyId": 9003,
                           "barrier": 2, "weight": 56.0, "price": 6.0},
                          {"position": 0, "margin": None, "tabNo": 11, "runner": "Non Starter",
                           "runnerId": 555, "jockey": "N. Starter", "jockeyId": 9004,
                           "barrier": 11, "weight": 56.0, "price": 21.0},
                      ]},
                     # no race number: skipped entirely
                     {"raceNumber": None,
                      "runners": [{"position": 1, "runner": "Ghost", "runnerId": 999}]},
                 ]},
            ],
            "detail_races": {1: {"name": "Test Handicap", "raceClass": "BM70",
                                 "distance": "1400m"}},
            "last10": {"111": "1x21", "222": "4231"},
        },
        {
            "meeting_id": 992,
            "track": "Randwick",
            "results": [
                {"meetingId": 992, "track": "Randwick", "meetingDate": "2026-07-19",
                 "raceResults": [
                     {"raceId": "r992_3", "raceNumber": 3, "trackConditionLabel": "Soft 5",
                      "runners": [
                          {"position": 1, "margin": None, "runner": "Solo Star",
                           "runnerId": 666, "jockey": "A. One", "jockeyId": 9100,
                           "barrier": 1, "weight": 58.0, "price": 2.1},
                      ]},
                 ]},
            ],
            "detail_races": {},
            "last10": {},
        },
    ],
    "raw": [],
}

FIXTURE_BRIDGE = {
    "knownhero": "hrs_aus_123",
    "newface": "hrs_aus_999",
    "solostar": "hrs_aus_555",
}

_OPP = '{{"horse_id": null, "horse_name": "{}", "position": {}, "margin": {}}}'
_A_BC = "[" + _OPP.format("New Face (NZ)", 2, 1.25) + ", " + _OPP.format("Mystery Miss", 3, 2.75) + "]"
_B_AC = "[" + _OPP.format("Known Hero", 1, "null") + ", " + _OPP.format("Mystery Miss", 3, 2.75) + "]"
_C_AB = "[" + _OPP.format("Known Hero", 1, "null") + ", " + _OPP.format("New Face (NZ)", 2, 1.25) + "]"

EXPECTED_ROWS = [
    ("hrs_aus_123", "Known Hero", "pf991_1", "Flemington", "2026-07-19",
     1400, "BM70", 5, "Good 4", 1, None, 59.5, "J. Rider", "pf9001", 3,
     4.2, 3, "Test Handicap", 1, "1x21", _A_BC),
    ("hrs_aus_999", "New Face (NZ)", "pf991_1", "Flemington", "2026-07-19",
     1400, "BM70", 5, "Good 4", 2, 1.25, 57.0, "K. Hop", "pf9002", 5,
     8.5, 3, "Test Handicap", 1, "4231", _B_AC),
    ("pf333", "Mystery Miss", "pf991_1", "Flemington", "2026-07-19",
     1400, "BM70", 5, "Good 4", 3, 2.75, 55.5, None, None, 8,
     12.0, 3, "Test Handicap", 1, None, _C_AB),
    ("hrs_aus_555", "Solo Star", "pf992_3", "Randwick", "2026-07-19",
     None, "", 1, "Soft 5", 1, None, 58.0, "A. One", "pf9100", 1,
     2.1, 1, None, 3, None, "[]"),
]

EXPECTED_RACES_SEEN = [
    ("2026-07-19", "flemington", 1),
    ("2026-07-19", "randwick", 3),
]


def test_fixture_day_maps_to_byte_identical_rows():
    unknown_keys = set()
    rows, races_seen = build_rows(FIXTURE_DAY, FIXTURE_BRIDGE, unknown_keys)
    assert rows == EXPECTED_ROWS
    assert races_seen == EXPECTED_RACES_SEEN
    assert unknown_keys == set()


def test_bridge_known_name_matches_existing_id():
    rows, _ = build_rows(FIXTURE_DAY, FIXTURE_BRIDGE, set())
    by_name = {r[1]: r[0] for r in rows}
    assert by_name["Known Hero"] == "hrs_aus_123"


def test_bridge_strips_country_suffix_before_matching():
    rows, _ = build_rows(FIXTURE_DAY, FIXTURE_BRIDGE, set())
    by_name = {r[1]: r[0] for r in rows}
    # "New Face (NZ)" normalises to "newface" and hits the existing id
    assert by_name["New Face (NZ)"] == "hrs_aus_999"


def test_bridge_unknown_name_gets_pf_runner_id():
    rows, _ = build_rows(FIXTURE_DAY, FIXTURE_BRIDGE, set())
    by_name = {r[1]: r[0] for r in rows}
    assert by_name["Mystery Miss"] == "pf333"


def test_scratched_and_no_position_runners_excluded():
    rows, _ = build_rows(FIXTURE_DAY, FIXTURE_BRIDGE, set())
    names = {r[1] for r in rows}
    assert "Late Scratch" not in names
    assert "Non Starter" not in names
    assert "Ghost" not in names
    # and field_size counts only placed runners
    assert all(r[16] == 3 for r in rows if r[2] == "pf991_1")


def test_norm_name_rules():
    assert norm_name("New Face (NZ)") == "newface"
    assert norm_name("Horse (GB)") == "horse"
    assert norm_name("  O'Brien's Best!  ") == "obriensbest"
    assert norm_name("42 Wallaby Way") == "42wallabyway"
    assert norm_name("") == ""
    assert norm_name(None) == ""


class _SqlAwareCursor:
    """Reproduces Postgres faithfully for load_bridge: the projected name is
    lowercased iff the SELECT list projects LOWER(horse_name). The buggy
    projection hands norm_name a pre-lowercased "(nz)" it cannot strip — this
    fake makes that regression fail a test instead of forking horses in prod."""

    def __init__(self, rows):
        self._rows = rows
        self._result = []

    def execute(self, sql, params=None):
        text = " ".join(sql.split()).lower()
        projection = text.split(" from ")[0].split(
            "distinct on (lower(horse_name))", 1)[-1]
        lowered = "lower(horse_name)" in projection
        self._result = [(n.lower() if lowered else n, h) for n, h in self._rows]

    def fetchall(self):
        return self._result


def test_load_bridge_bridges_country_suffixed_db_names():
    # The DB side of the suffix rule: race_results_history stores
    # "Ocean Deep (NZ)" in original case, and load_bridge's SQL must keep that
    # case so norm_name yields "oceandeep" — the same key the card side
    # produces. The 2026-07-31 backfill ran with a LOWER() projection here and
    # forked every country-suffixed horse; this pins the fix.
    from pf_results_mapper import load_bridge
    bridge = load_bridge(_SqlAwareCursor([("Ocean Deep (NZ)", "hrs_aus_7")]))
    assert bridge == {"oceandeep": "hrs_aus_7"}
