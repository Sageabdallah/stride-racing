"""Every root-level module the runtime loads by path must be in the image.

mc_api.py resolves racing_system_v8.3_mc.py against PROJECT_ROOT — the repo
root, /var/task in the container — and exec_module()s it at import time. The
Dockerfile copied server/python, migrations and infra/jobs, so the file was
absent from every image ever built. In the cloud the import raised
FileNotFoundError, run_mc_simulation caught it and returned None, and the race
loop turned that into a bare `continue`. The 10:00 tips job exited 0 having
scored nothing: 31 races in, 0 selections out on 2026-08-05, 8 in and 0 out on
2026-08-02, with a green run-state row both days.

Nothing local could catch it. The file is tracked in git and present in every
checkout and every CI runner, so the pipeline works everywhere except the one
place it has to. The only evidence was a stderr line, and _run_ok discards
stderr on a clean exit.

This reads infra/Dockerfile rather than trusting a comment in it, and holds the
set of root-level modules loaded by path to the ones actually copied in — so
adding a second one without a COPY line fails here instead of in a race-day
run.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "infra" / "Dockerfile"
RUNTIME_DIRS = (ROOT / "server" / "python", ROOT / "infra" / "jobs")

# The two idioms in this repo: os.path.join(PROJECT_ROOT, "x.py") and
# PROJECT_ROOT / "x.py". Both are matched with an optional leading underscore
# because odds_snapshots.py names its copy _PROJECT_ROOT.
_JOINED = re.compile(r"""os\.path\.join\(\s*_?PROJECT_ROOT\s*,\s*['"]([^'"]+\.py)['"]""")
_DIVIDED = re.compile(r"""_?PROJECT_ROOT\s*/\s*['"]([^'"]+\.py)['"]""")


def _root_modules_loaded_by_path():
    """{module filename: {source files that load it}} across the runtime tree."""
    found = {}
    for directory in RUNTIME_DIRS:
        for src in sorted(directory.rglob("*.py")):
            if "tests" in src.parts or "__pycache__" in src.parts:
                continue
            text = src.read_text(encoding="utf-8", errors="replace")
            for match in list(_JOINED.finditer(text)) + list(_DIVIDED.finditer(text)):
                found.setdefault(match.group(1), set()).add(
                    str(src.relative_to(ROOT)))
    return found


def _copied_sources():
    """Source paths the Dockerfile copies into the image."""
    copied = set()
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("COPY "):
            continue
        # drop the COPY keyword, any --flags, and the destination
        parts = [p for p in stripped.split()[1:] if not p.startswith("--")]
        if len(parts) >= 2:
            copied.update(parts[:-1])
    return copied


def test_root_modules_loaded_by_path_are_copied_into_the_image():
    loaded = _root_modules_loaded_by_path()
    # A zero-match run would pass this test vacuously forever. If the idiom
    # moves, that is a reason to update the patterns, not to stop checking.
    assert loaded, (
        "no PROJECT_ROOT-relative module loads found — the idiom changed, so "
        "these patterns no longer guard anything. Update them.")

    missing = {module: sorted(users)
               for module, users in loaded.items()
               if module not in _copied_sources()}
    assert not missing, (
        f"loaded at runtime but never COPYed into the image by "
        f"infra/Dockerfile: {missing}. Every local run and every CI checkout "
        f"has these files, so only the cloud fails — with FileNotFoundError "
        f"swallowed into an empty result. Add a COPY line.")


def test_root_modules_loaded_by_path_exist_in_the_repo():
    """A COPY of a file that has been renamed away fails the build, not the run,
    but the reverse — a rename that leaves the loader pointing at nothing —
    reproduces the original defect exactly."""
    for module, users in sorted(_root_modules_loaded_by_path().items()):
        assert (ROOT / module).is_file(), (
            f"{module} is loaded by {sorted(users)} but does not exist at the "
            f"repo root")
