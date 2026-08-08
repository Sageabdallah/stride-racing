#!/usr/bin/env python3
"""The list of tracks STRIDE builds racecards for, and how a run resolves it.

Why this is its own module
--------------------------
The list was a literal inside download_racecards.py, which meant changing what
STRIDE covers required a code change, a review, a merge and — on the AWS side —
a container image rebuild. That is a heavy process for a decision that wants to
be tried, watched for a week and reverted if the tips get worse.

It also meant the list could only be found by grepping. There are six other
track lists in this repository and no two are identical: odds_movement.py and
download_historical.py both carry `pakenham`, and odds_movement.py and
download_training_data.py both carry `sunshine coast`, while the download list
here carries neither. So the odds layer and the history layer already expect
tracks the racecard cannot produce. Measured against race_results_history,
those two tracks appear on 10 of the 28 days in the 75 to 2026-08-03 that had
no target-track racing at all.

That divergence is a real inconsistency, and it is deliberately NOT resolved by
changing the default here. Widening the download list widens what gets tipped:
run_tips_pipeline.py's load_racecard has no default track filter, and
seed_race_schedule, consensus_agent, the intelligence builders and
odds_movement.read_racecard_odds all re-filter nothing. The card is the single
choke point, so anything added flows straight into race_schedule, consensus,
the ledger and the UI. That is a modelling decision, and it should be made by
watching output rather than by editing a constant in a reliability fix.

STRIDE_TARGET_TRACKS is what makes watching cheap. Set it in the AWS secret,
give it a week, then promote into DEFAULT_TARGET_TRACKS in a reviewed PR if the
tips hold up.

Resolution
----------
    STRIDE_TARGET_TRACKS="a,b,c"   replace the list
    STRIDE_TARGET_TRACKS="all"     no filter — every AUS meeting the provider lists
    STRIDE_TARGET_TRACKS="*"       same
    unset, or empty                the built-in list below
    parses to zero entries         empty list; callers must treat that as a
                                   config error, never as "nothing raced today"

Env var and not a config file: the AWS handler loads Secrets Manager into the
environment before any repo import, so an operator widens the list by editing
one secret with no code change, no deploy and no image rebuild. A JSON file
baked into the image would need a rebuild to change, and its path would have to
resolve identically from three different working directories (repo root on the
GitHub runner, server/python on Fargate, and the Mac).

Matching
--------
A target name must appear inside the course name. The test is deliberately
loose in that direction — "ascot" also matches "Ascot WA", "sandown" matches
"Sandown Hillside", "kensington" matches "Randwick Kensington" — because the
provider sponsors, abbreviates and re-spells course names constantly, and a
card that quietly contains fewer meetings is worse than one that contains too
many: it still looks green.

Replacing this with canonical-key matching was measured against the 95 stored
cards in racecards/ and rejected: it would silently drop seven spellings across
19 of those card-days, including both Sandown Lakeside spellings (because
"sandown" canonicalises to "sandownhillside" via the alias map).

What it does NOT do is match in reverse. The original tested
`t in course or course in t`, and the second half admits any course whose name
is a prefix of a target — Punting Form's 2026-08-04 card listed `Warwick`, the
Queensland country track, which matched the Sydney metro target `warwick farm`
and had nine races built against it. Removing that direction was measured over
312 distinct course spellings (every `course` in the stored cards plus every
distinct `track` in race_results_history) and changes nothing else: 40 matched
before, 40 after, zero disagreements. All seven canonical-key casualties above
survive, because each contains its target rather than being contained by it.

See test_a_course_that_is_a_prefix_of_a_target_is_not_a_target, which asserts
the direction rather than the single name.
"""

from __future__ import annotations

import os
import re

# The list as it stood in download_racecards.py, byte for byte. Do not edit
# this to try a wider set — use STRIDE_TARGET_TRACKS, see above.
DEFAULT_TARGET_TRACKS = [
    "flemington", "caulfield", "caulfield heath", "moonee valley", "sandown",
    "randwick", "royal randwick", "rosehill", "warwick farm", "canterbury",
    "eagle farm", "doomben", "ascot", "belmont", "pinjarra",
    "kensington", "randwick kensington", "gold coast", "aquis park gold coast",
    "geelong", "ladbrokes geelong", "ballarat", "cranbourne", "ipswich", "newcastle",
    "morphettville",
]

ENV_VAR = "STRIDE_TARGET_TRACKS"
_ALL = {"all", "*"}


def _raw() -> str:
    return (os.environ.get(ENV_VAR) or "").strip()


def collect_all() -> bool:
    """True when the run is configured to take every meeting the provider
    lists for the date, with no track filter at all."""
    return _raw().lower() in _ALL


def target_tracks() -> list:
    """The resolved list, lowercased. Empty only when an override was given
    and parsed to nothing — which callers must treat as a config error, since
    an empty list silently matches nothing and makes every day look quiet."""
    raw = _raw()
    if not raw:
        return list(DEFAULT_TARGET_TRACKS)
    if raw.lower() in _ALL:
        return []
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def matches(course: str, targets) -> bool:
    """Does `course` name one of `targets`?

    Takes the list as an argument so the other two track lists in this
    repository can share the matcher without sharing the list. They are
    different universes on purpose — odds_movement covers the market pillar,
    download_historical covers ingestion, and neither is the card — but there
    is no reason for them to each carry their own copy of the *comparison*.

    Three copies is how the Warwick defect survived its first fix: the
    correction went into is_target_track, download_racecards had an inline
    duplicate, and the card kept being built from the duplicate while the
    suite stayed green. One matcher means the next correction lands
    everywhere at once.
    """
    # Both sides are stripped to bare alphanumerics before the substring
    # test. The odds pipeline stores plain_norm'd course names ("eaglefarm"),
    # while seven of odds_movement's targets carry a space — "eagle farm",
    # "moonee valley", "warwick farm", "murray bridge", "gold coast",
    # "sunshine coast", "kembla grange" — so a raw substring test could never
    # match them and each lost its entire market pillar whenever it raced.
    # Proven live 2026-08-08: Eagle Farm's 9 races had 459 price rows
    # captured and then dropped at this comparison; the four venues that
    # survived did so only because a shorter single-word target happened to
    # be a substring ("caulfield", "belmont"). Display-form courses
    # ("Eagle Farm") match exactly as before, since stripping both sides
    # preserves every match the raw test made.
    course_plain = re.sub(r"[^a-z0-9]+", "", (course or "").lower())
    for t in targets:
        t_plain = re.sub(r"[^a-z0-9]+", "", str(t).lower())
        # An empty target must never match everything.
        if t_plain and t_plain in course_plain:
            return True
    return False


def target_source() -> str:
    """Which of the two answers above is in force. Recorded in the quiet-day
    sentinel so a zero-meeting day is always attributable to a known list."""
    if not _raw():
        return "builtin"
    return f"env:{ENV_VAR}"


def is_target_track(course: str) -> bool:
    """Substring match in one direction only: a target name must appear inside
    the course name.

    The original matcher tested both directions — `t in course or course in t`.
    The second half admits any course whose name is a *prefix* of a target, and
    the tracks it lets in are not the tracks it looks like it lets in. Punting
    Form's card for 2026-08-04 listed a meeting named exactly `Warwick`. That is
    the Queensland country track (its results-side spelling is
    `Picklebet Park Warwick`); `warwick` is a substring of the target
    `warwick farm`, which is Sydney metro. Nine races were built and seeded
    under a target the course has nothing to do with.

    Removing the reversed direction was measured before it was applied, over
    312 distinct course spellings — every `course` in the stored
    `racecards/racecard_*.json` plus every distinct `track` in
    `race_results_history`. **The two matchers disagree on none of them**: 40
    matched before, 40 after. It keeps all seven spellings a canonical-key
    rewrite was previously measured to drop (`Sportsbet-Ballarat`,
    `Pinjarra Scarpside`, both Sandown spellings, `Southside Cranbourne`,
    `Aquis Park Gold Coast Poly`, `Sandown-Lakeside`), because those all
    *contain* their target rather than being contained by it.

    So the reversed direction earned nothing on real data and cost one wrong
    track. The five other courses that match by containment — Caulfield,
    Geelong, Gold Coast, Kensington, Randwick — are all target entries in their
    own right and match on the forward direction regardless.
    """
    if collect_all():
        return True
    return matches(course, target_tracks())
