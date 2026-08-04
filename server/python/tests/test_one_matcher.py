"""There is one track matcher in this repository, and the lists stay separate.

The Warwick defect survived its own fix because the comparison was written out
three times. The correction went into target_tracks.is_target_track;
download_racecards had an inline duplicate and never called it; the card kept
being built from the duplicate and the suite stayed green, because the test
asserted on the helper rather than on the path.

So this pins two things that must not drift apart again:

  every module compares course names through target_tracks.matches, and
  every module keeps its own list.

The second half matters as much as the first. The three lists are different
universes on purpose — the card, the market pillar, ingestion — and collapsing
them would change what gets tipped, which is a modelling decision and not a
refactor. See target_tracks.matches.
"""

import ast
import re
from pathlib import Path

import pytest

import download_historical
import odds_movement
import target_tracks as tt

PY = Path(__file__).resolve().parents[1]

# Every module that decides "is this course one of mine?".
MATCHER_MODULES = [
    "download_racecards.py",
    "odds_movement.py",
    "download_historical.py",
    "target_tracks.py",
]

# The shape of the old duplicated test, in the several spellings it had.
BIDIRECTIONAL = re.compile(
    r"in\s+(course_lower|n|track_lower|course\.lower\(\))\s+or\s+"
    r"(course_lower|n|track_lower|course\.lower\(\))\s+in\s+")


def test_no_module_carries_its_own_bidirectional_matcher():
    offenders = []
    for name in MATCHER_MODULES:
        body = (PY / name).read_text()
        # Comments and docstrings describe the old behaviour on purpose.
        code = "\n".join(l for l in body.splitlines()
                         if not l.lstrip().startswith("#"))
        for node in ast.walk(ast.parse(body)):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                code = code.replace(node.value.value, "")
        if BIDIRECTIONAL.search(code):
            offenders.append(name)
    assert not offenders, (
        f"{offenders} still compare course names inline. Route them through "
        "target_tracks.matches — a second copy is how the Warwick fix missed "
        "the card.")


def test_every_module_routes_through_the_shared_matcher():
    for name in MATCHER_MODULES:
        if name == "target_tracks.py":
            continue
        body = (PY / name).read_text()
        assert re.search(r"(tt|target_tracks)\.(matches|is_target_track)\(", body), \
            f"{name} does not call the shared matcher"


# ------------------------------------------------------------- the behaviour

@pytest.mark.parametrize("module,fn", [
    (odds_movement, "_is_target_track"),
    (download_historical, "is_target_track"),
])
def test_prefix_courses_are_rejected_everywhere(module, fn):
    """The Warwick class, checked on the other two lists rather than assumed.

    Both carry `murray bridge`, `kembla grange`, `sunshine coast` — each of
    which has a first word that is not itself a target, and each of which the
    reversed match would have admitted as a bare course name.
    """
    check = getattr(module, fn)
    targets = module.TARGET_TRACKS
    for target in targets:
        head = target.split()[0]
        if head != target and head not in targets:
            assert not check(head), (
                f"{head!r} matched via {target!r} in {module.__name__}")


def test_the_lists_stay_separate():
    """Sharing the matcher must not quietly share the universe. These three
    differ deliberately; equality here would mean a modelling decision was
    made by a refactor."""
    card = set(tt.DEFAULT_TARGET_TRACKS)
    odds = set(odds_movement.TARGET_TRACKS)
    hist = set(download_historical.TARGET_TRACKS)

    assert card != odds and card != hist, (
        "a track list collapsed into another — the card, the market pillar "
        "and ingestion are different universes on purpose")
    # The known divergence, pinned so a change to it is deliberate and visible.
    assert (odds | hist) - card == {
        "bendigo", "gawler", "gosford", "hawkesbury", "kembla grange",
        "murray bridge", "pakenham", "randwick-kensington", "sunshine coast",
        "toowoomba", "wyong",
    }
