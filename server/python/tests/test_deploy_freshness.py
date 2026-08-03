"""The deployed image must not be able to drift from main unnoticed.

deploy-infra is dispatch-only, so a merge that changes job behaviour does not
reach AWS until someone runs it. On 2026-08-03 the quiet-day work sat on main
for an hour while Fargate ran the previous image: the GitHub crons had the fix,
the Fargate jobs did not, and the two monitors disagreed about the same morning
with neither of them wrong.

These pin the two properties that make the check worth having rather than
worth muting:

  it fires on code that reaches the image, and
  it stays silent on everything else.

A check that alarms on a CLAUDE.md commit gets ignored within a week, and then
it is not a check.
"""

import json
import re
import urllib.error
from pathlib import Path

import pytest

import deploy_preflight as dp

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "infra" / "Dockerfile"
GREEN, RED, AMBER = dp.GREEN, dp.RED, dp.AMBER


def _compare(files, ahead_by=1):
    """A GitHub compare payload with just the fields check_deployment reads."""
    return lambda repo, base, head, timeout: {
        "ahead_by": ahead_by,
        "files": [{"filename": f} for f in files],
    }


def _stamp(monkeypatch, sha="a" * 40):
    monkeypatch.setenv("STRIDE_IMAGE_SHA", sha)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


# ------------------------------------------------------- the path set itself

def test_image_paths_match_what_the_dockerfile_actually_copies():
    """The check is only as good as this list. If a COPY is added to the
    Dockerfile and not here, that code can change on main and the gate will
    call it irrelevant — a false green on the exact question it exists to
    answer. Parsed from the Dockerfile rather than restated."""
    copied = set()
    for line in DOCKERFILE.read_text().splitlines():
        if not line.startswith("COPY "):
            continue
        parts = line.split()[1:]
        src = parts[0]
        if src.startswith("--"):          # e.g. COPY --from=x
            src = parts[1]
        copied.add(src)

    declared = {p.rstrip("/") for p in dp.IMAGE_PATHS}
    missing = {c for c in copied if c.rstrip("/") not in declared}
    assert not missing, (
        f"infra/Dockerfile COPYs {sorted(missing)} into the image but "
        "deploy_preflight.IMAGE_PATHS does not list them — changes to those "
        "paths would be reported as not affecting the image")

    # The Dockerfile is not COPYed but does define the image.
    assert "infra/Dockerfile" in dp.IMAGE_PATHS


@pytest.mark.parametrize("path,relevant", [
    ("server/python/download_racecards.py", True),
    ("server/python/providers/puntingform.py", True),
    ("infra/jobs/handler.py", True),
    ("migrations/007_x.sql", True),
    ("requirements.txt", True),
    ("infra/entrypoint.sh", True),
    ("infra/Dockerfile", True),
    # Outside the image: these move on main constantly and must stay silent.
    ("CLAUDE.md", False),
    ("README.md", False),
    (".github/workflows/morning-watch.yml", False),
    ("infra/06_schedules.sh", False),
    ("docs/validation/registry.md", False),
    ("server/routes.ts", False),
    # Near-misses: prefix matching must not over-reach.
    ("requirements.lock.txt", False),
    ("server/python_notes.md", False),
])
def test_image_relevance(path, relevant):
    assert dp._image_relevant(path) is relevant, path


# --------------------------------------------------------- the verdict logic

def test_code_change_since_the_image_is_red(monkeypatch):
    _stamp(monkeypatch)
    row, = dp.check_deployment(fetch=_compare(["server/python/download_racecards.py"]))
    assert row["status"] == RED
    assert "deploy-infra" in row["detail"]


def test_docs_only_change_since_the_image_is_green(monkeypatch):
    """The 2026-08-03 case exactly: main moved to a CLAUDE.md-only commit after
    the deploy. The image was behaviourally identical and must read green, or
    the gate cries wolf on routine commits."""
    _stamp(monkeypatch)
    row, = dp.check_deployment(fetch=_compare(["CLAUDE.md"]))
    assert row["status"] == GREEN
    assert "none touching the image" in row["detail"]


def test_image_current_with_main_is_green(monkeypatch):
    _stamp(monkeypatch)
    row, = dp.check_deployment(fetch=_compare([], ahead_by=0))
    assert row["status"] == GREEN
    assert "current with main" in row["detail"]


def test_mixed_change_is_red_on_the_code_half(monkeypatch):
    _stamp(monkeypatch)
    row, = dp.check_deployment(
        fetch=_compare(["CLAUDE.md", "infra/jobs/handler.py", "README.md"]))
    assert row["status"] == RED
    assert "infra/jobs/handler.py" in row["detail"]
    assert "1 image-relevant" in row["detail"]


# ------------------------------------------------- cannot-verify vs is-stale

def test_unstamped_image_is_red_not_amber(monkeypatch):
    """An image with no stamp predates the gate, so it is by definition older
    than the commit that introduced the gate. RED is the true answer and it
    clears itself on the next deploy."""
    monkeypatch.setenv("STRIDE_IMAGE_SHA", "")
    row, = dp.check_deployment(fetch=_compare([]))
    assert row["status"] == RED
    assert "unset" in row["detail"]


def test_dirty_build_is_amber(monkeypatch):
    """A dirty tree stamps '<sha>-dirty'. The SHA no longer describes what was
    built, so comparing against it would be worse than not comparing."""
    _stamp(monkeypatch, sha="b" * 40 + "-dirty")
    row, = dp.check_deployment(fetch=_compare(["server/python/x.py"]))
    assert row["status"] == AMBER
    assert "not a clean commit" in row["detail"]


def test_github_unreachable_is_amber_never_red(monkeypatch):
    """A network blip is not evidence of staleness. RED here would alarm on
    every DNS hiccup and train the operator to ignore the board."""
    _stamp(monkeypatch)

    def boom(repo, base, head, timeout):
        raise OSError("connection reset")

    row, = dp.check_deployment(fetch=boom)
    assert row["status"] == AMBER
    assert "not assumed" in row["detail"]


def test_unknown_commit_is_amber(monkeypatch):
    _stamp(monkeypatch)

    def four_oh_four(repo, base, head, timeout):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    row, = dp.check_deployment(fetch=four_oh_four)
    assert row["status"] == AMBER
    assert "404" in row["detail"]


def test_truncated_compare_is_red_not_green(monkeypatch):
    """Past the API's file cap the response cannot show the drift is
    irrelevant. Reading a truncated list as 'no code changed' would be a green
    produced by missing data — the silent no-op class, in a checker."""
    _stamp(monkeypatch)
    row, = dp.check_deployment(
        fetch=_compare(["docs/f%d.md" % i for i in range(dp._COMPARE_FILE_CAP)]))
    assert row["status"] == RED
    assert "cap" in row["detail"]


# ------------------------------------------------------------- wiring checks

def test_dockerfile_stamps_both_a_label_and_an_env():
    """The label is readable from outside via ECR; the ENV is readable from
    inside, which is where this check runs. A label alone cannot be read by
    the running container, so both are required."""
    body = DOCKERFILE.read_text()
    assert "ARG GIT_SHA" in body
    assert "LABEL org.opencontainers.image.revision=$GIT_SHA" in body
    assert "ENV STRIDE_IMAGE_SHA=$GIT_SHA" in body
    # After the COPY layers, or every commit busts the pip cache.
    assert body.index("ARG GIT_SHA") > body.index("RUN pip install")


def test_build_script_passes_the_stamp():
    body = (ROOT / "infra" / "04_ecr_image.sh").read_text()
    assert "--build-arg GIT_SHA=" in body
    assert "GITHUB_SHA" in body
    assert "-dirty" in body
