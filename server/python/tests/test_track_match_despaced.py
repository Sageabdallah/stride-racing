"""The one matcher must match courses in STORED form, not just display form.

The odds pipeline writes plain_norm'd course names into its snapshot rows
("Eagle Farm" -> "eaglefarm"), and odds_movement filters those rows through
target_tracks.matches. Seven of its targets carry a space, so the raw
substring test could never match them: the venue's prices were captured,
then silently dropped, and its market pillar read neutral. Proven live on
2026-08-08 — Eagle Farm, 9 races, 459 price rows captured, 0 signals.

These tests pin the property that broke: every target must match its own
plain_norm'd spelling, for both production target lists.
"""

import re

import odds_movement
import target_tracks as tt


def _plain(s: str) -> str:
    # Mirrors betfair_markets.plain_norm — the form snapshot rows store.
    return re.sub(r"[^a-z0-9]+", "", s.lower())


# The seven odds_movement targets a raw substring test could never reach:
# each contains a space, and no shorter target rescues its despaced form.
DEAD_BEFORE_FIX = [
    "eagle farm", "moonee valley", "warwick farm", "murray bridge",
    "gold coast", "sunshine coast", "kembla grange",
]


def test_every_market_pillar_target_matches_its_stored_form():
    for t in odds_movement.TARGET_TRACKS:
        assert tt.matches(_plain(t), odds_movement.TARGET_TRACKS), (
            f"target {t!r} cannot match its own stored form {_plain(t)!r}")


def test_every_builtin_card_target_matches_its_stored_form():
    targets = tt.target_tracks()
    for t in targets:
        assert tt.matches(_plain(t), targets), (
            f"target {t!r} cannot match its own stored form {_plain(t)!r}")


def test_the_seven_previously_dead_venues():
    for t in DEAD_BEFORE_FIX:
        stored = _plain(t)
        # The regression case: stored form differs from the raw target...
        assert stored != t
        # ...and now matches anyway.
        assert tt.matches(stored, odds_movement.TARGET_TRACKS), t


def test_display_forms_still_match():
    # Stripping both sides must not lose any match the raw test made.
    for course in ("Eagle Farm", "Royal Randwick", "Caulfield Heath",
                   "Belmont Park", "morphettville", "Moonee Valley"):
        assert tt.matches(course, odds_movement.TARGET_TRACKS), course


def test_non_targets_stay_out():
    for course in ("wangaratta", "te rapa", "Wagga", ""):
        assert not tt.matches(course, odds_movement.TARGET_TRACKS), course


def test_empty_target_never_matches_everything():
    # A target that strips to nothing ("", "-") must not match every course.
    assert not tt.matches("eaglefarm", ["", "-", "zzz"])
