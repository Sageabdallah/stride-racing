#!/usr/bin/env python3
"""What every STRIDE_* flag is set to right now, and whether it could be set at all.

Motivated by a question this repository could not answer about itself: *which of
our written, tested, default-off fixes are actually live in production?* Nothing
printed the resolved flag set, so the answer was inferred from source — and the
inference was wrong twice. Production environment comes from AWS Secrets Manager
(``infra/jobs/handler.py:_load_secrets``), which is not in the repository, so a
checkout cannot answer it and neither could a grep.

Two independent halves, because they fail independently:

  * **Static — is there a delivery path?** A flag whose ON branch is written and
    tested is still dead if nothing can set it in the container. Three delivery
    paths exist and only the first is editable without a deploy:
    the ``stride/prod`` secret blob (``infra/01_secrets.sh`` KEYS), a literal
    assignment in ``infra/jobs/handler.py`` (image path — needs a rebuild), and
    a ``containerOverrides`` entry in a workflow (ad-hoc dispatch). A flag on
    none of these cannot be turned on, whatever its default says.
  * **Runtime — what is it set to here?** The resolved value in *this* process,
    plus where it came from. ``_load_secrets`` merges with ``setdefault``, so the
    task environment beats the secret and the merged view cannot distinguish
    them after the fact. This reports that ambiguity as ambiguity rather than
    guessing (``--secret`` resolves it by re-reading the blob).

**Scanner discipline.** The repo already owns a static audit that under-counts
because it knows one code shape: ``feature_liveness_audit.py`` credits a feature
as served on evidence that only executes behind a default-off flag, and contains
no reference to flags at all. This scanner has the same hazard in a different
place — flags are read through five shapes, and a scanner that knows only the
first missed **30 of the 74 flags that then had a production reader** — measured
when shapes 2 and 3 were added, so read it as the case for parsing them and not
as a current total, which has moved since — among them every
``STRIDE_CTX_MULT_*``, ``STRIDE_LEDGER_WRITE`` and ``STRIDE_EV_GATE_AT_PRICE``:

  1. direct       ``os.environ.get("STRIDE_X", "false")`` / ``os.environ["STRIDE_X"]``
  2. helper       ``_flag_enabled("STRIDE_X")`` — the dominant idiom here
  3. const        ``F = "STRIDE_X"`` … ``os.environ.setdefault(F, "true")``
  4. seq          ``for f in FLAGS: _flag_enabled(f)`` — FLAGS a module-level literal
  5. fstring      ``_stride_flag(f"STRIDE_RACE_FILTER_{suffix}")`` over a rules table

Shapes 4 and 5 name a flag the source never spells out in full. Missing them is
not under-counting sites, it is reporting a live control as dead: all four
``STRIDE_RACE_FILTER_*`` rule flags read ``n_read_sites: 0`` while setting one
genuinely declines races, and two of them were dropped from the table entirely,
because a name with no reader and no writer is not printed. A loop variable is
resolved only against a *literal* collection; a name this scanner cannot reduce
to literals is recorded by ``unresolved_dynamic`` and fails ``--self-test``
rather than silently reading as dead.

All five are parsed, every flag reports which shape found it, and ``--self-test``
fails on a named flag per shape rather than on a count. A count would pass on
exactly the regression it is meant to catch.

Two further precision rules, each written after the first draft got it wrong:
a call is a read only when its receiver is ``os.environ`` (``.get`` is the
commonest method in Python, and matching it on any object made
``_style_ordinal(name, 1)`` a flag helper); and a name is a flag only when it
is a bare identifier, because ``print("STRIDE_LEARNED_BLEND ignored …")``
otherwise invents a flag named after the sentence.

What this does NOT do: it reports the flags a *reader* consumes and the paths a
*setter* could use. It cannot tell you the branch behind a flag is correct, and
it never claims a flag is live in production from a local run — ``scope`` says
which environment the runtime column describes.

Usage:
  python flag_state.py                    # table for this process
  python flag_state.py --json             # machine-readable
  python flag_state.py --secret           # also compare against stride/prod (needs AWS creds)
  python flag_state.py --self-test        # scanner tripwires
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

PREFIX = "STRIDE_"

# The three delivery inputs, each at the repo path first and the container
# path second. infra/Dockerfile copies `infra/jobs` to /var/task/JOBS, not
# /var/task/infra/jobs, so the repo spelling misses it in the one environment
# this job exists to describe. Resolved rather than assumed, and the result is
# reported: a source that cannot be read is NOT evidence that a flag has no
# delivery path, and reporting it as one inverts the finding.
_SECRETS_CANDIDATES = (REPO / "infra" / "01_secrets.sh", REPO / "01_secrets.sh")
_HANDLER_CANDIDATES = (REPO / "infra" / "jobs" / "handler.py", REPO / "jobs" / "handler.py")
_WORKFLOW_CANDIDATES = (REPO / ".github" / "workflows", REPO / "workflows")


def _resolve(candidates) -> Optional[Path]:
    for path in candidates:
        if path.exists():
            return path
    return None


SECRETS_SH = _resolve(_SECRETS_CANDIDATES) or _SECRETS_CANDIDATES[0]
HANDLER_PY = _resolve(_HANDLER_CANDIDATES) or _HANDLER_CANDIDATES[0]
WORKFLOW_DIR = _resolve(_WORKFLOW_CANDIDATES) or _WORKFLOW_CANDIDATES[0]

# Flags that name a value rather than gate a branch: reporting them as
# "never turned on" would be noise. They still get a row; they are just not
# counted as undeliverable fixes in the summary.
VALUE_FLAGS = {
    "STRIDE_DATE", "STRIDE_TRACKS", "STRIDE_TARGET_TRACKS", "STRIDE_JOB",
    "STRIDE_SECRET_ID", "STRIDE_STATE_TABLE", "STRIDE_EVIDENCE_BUCKET",
    "STRIDE_EVIDENCE_PREFIX", "STRIDE_MODELS_BUCKET", "STRIDE_ALERT_TOPIC_ARN",
    "STRIDE_IMAGE_SHA", "STRIDE_IMAGE_DIGEST", "STRIDE_REPO", "STRIDE_ENV_FILE",
    "STRIDE_RELEASE_ID", "STRIDE_RELEASE_MANIFEST_KEY",
    "STRIDE_RELEASE_MANIFEST_SHA256", "STRIDE_ENSEMBLE_ARTIFACT",
    "STRIDE_COMMISSION_RATE", "STRIDE_MODEL_WEIGHT", "STRIDE_PREREG_ACK",
    "STRIDE_MC_SEED_SALT", "STRIDE_DEVIG", "STRIDE_TRAIN_ODDS_SOURCE",
    "STRIDE_CAL_MIN_COVERAGE", "STRIDE_MIN_PROB_PCT", "STRIDE_ACCURACY_WEIGHTS",
}


# --------------------------------------------------------------------------
# Static half 1 — who reads each flag, and what the code default is
# --------------------------------------------------------------------------

def _env_accessor(func: ast.AST) -> Optional[str]:
    """'read' / 'write' if this callee is an ``os.environ`` accessor, else None.

    The receiver has to be checked, not just the method name: ``.get`` is the
    commonest method in the language, and accepting it on any object made
    ``_style_ordinal(name, 1)`` look like a flag helper in the first draft.
    Accepts ``os.environ.get``, ``os.getenv``, and the ``from os import``
    spellings.
    """
    def is_environ(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute):
            return node.attr == "environ" and isinstance(node.value, ast.Name) \
                and node.value.id == "os"
        return isinstance(node, ast.Name) and node.id == "environ"

    if isinstance(func, ast.Attribute):
        if func.attr in ("get", "setdefault", "pop") and is_environ(func.value):
            return "read" if func.attr == "get" else "write"
        if func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os":
            return "read"
    elif isinstance(func, ast.Name) and func.id == "getenv":
        return "read"
    return None


def _literal(node: ast.AST) -> str:
    try:
        return repr(ast.literal_eval(node))
    except Exception:
        return "<expr>"


def _is_flag_name(text: str) -> bool:
    """A flag name, not a sentence that happens to start with one.

    ``print(f"STRIDE_LEARNED_BLEND ignored ...")`` passes a string starting
    with the prefix as a call's first argument. Accepting it invents flags
    named after error messages, which the first draft of this scanner did.
    """
    return bool(re.fullmatch(r"STRIDE_[A-Z0-9]+(?:_[A-Z0-9]+)*", text)) and len(text) > len(PREFIX)


def _fstring_flag_head(node: ast.AST) -> bool:
    """An f-string whose literal head could begin a STRIDE_ name.

    The head must be flag-shaped all the way through, not merely start with
    the prefix. ``f"STRIDE_ names the scanner could not resolve: {x}"`` is a
    sentence, and accepting it made this scanner's own failure message look
    like an unresolved flag read -- the sentence-as-flag mistake
    ``_is_flag_name`` exists to prevent, re-made one level up.
    """
    if not (isinstance(node, ast.JoinedStr) and node.values):
        return False
    head = node.values[0]
    return isinstance(head, ast.Constant) and isinstance(head.value, str) \
        and bool(re.fullmatch(r"STRIDE_[A-Z0-9_]*", head.value))


def _literal_strings(node: ast.AST) -> List[str]:
    """The string literals in a collection display, or [] if it is not one.

    A dict contributes its keys, because the repo's idiom for a family of
    flags is a rules table keyed by suffix (``selection_policy`` 's
    ``RACE_FILTER_RULES``). Non-string members are skipped rather than
    disqualifying the collection: ``{"MAIDEN": (...), **extra}`` still tells
    us about MAIDEN.
    """
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        elts = list(node.elts)
    elif isinstance(node, ast.Dict):
        elts = [k for k in node.keys if k is not None]
    else:
        return []
    return [e.value for e in elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)]


def discover_helpers(repo: Path = REPO) -> Dict[str, List[str]]:
    """Functions that turn a flag name into its value, and the default each uses.

    The dominant read idiom here is not ``os.environ.get`` but a one-line
    module helper (``_flag_enabled``, ``_stride_flag``, ``_flag_on`` …).
    Hard-coding those names would rot; instead any function whose body reads
    ``os.environ``/``os.getenv`` keyed on its own first parameter is a helper,
    and its literal fallback is the default every caller inherits.
    """
    cache_key = f"helpers::{repo}"
    if cache_key in _SCAN_CACHE:
        return _SCAN_CACHE[cache_key]
    helpers: Dict[str, Set[str]] = {}
    for path in sorted(repo.rglob("*.py")):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError):
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not fn.args.args:
                continue
            param = fn.args.args[0].arg
            for call in ast.walk(fn):
                if not isinstance(call, ast.Call) or not call.args:
                    continue
                if _env_accessor(call.func) != "read":
                    continue
                a0 = call.args[0]
                if isinstance(a0, ast.Name) and a0.id == param:
                    dflt = _literal(call.args[1]) if len(call.args) > 1 else "<none inline>"
                    helpers.setdefault(fn.name, set()).add(dflt)
    resolved = {k: sorted(v) for k, v in helpers.items()}
    _SCAN_CACHE[cache_key] = resolved
    return resolved


def _scan_module(path: Path, rel: str, out: Dict[str, Dict[str, Any]],
                 helpers: Dict[str, List[str]],
                 unresolved: Optional[List[str]] = None) -> None:
    """Record every read and write of a STRIDE_* flag in one module.

    Only the shapes that actually resolve a flag count. A call is a read when
    it is an env accessor or a discovered helper — never merely because its
    first argument is a string beginning with the prefix.

    Shape 3 needs the module-level ``NAME = "STRIDE_X"`` binding pass; without
    it ``handler.py``'s ``setdefault(CTX_MULT_DIAG_FLAG, "true")`` is invisible,
    and that is a live production setter.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return

    const_names: Dict[str, str] = {}
    const_seqs: Dict[str, List[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) \
                and _is_flag_name(node.value.value):
            for name in targets:
                const_names[name] = node.value.value
        else:
            members = _literal_strings(node.value)
            if members:
                for name in targets:
                    const_seqs[name] = members

    # Shape 5 needs a name the source never spells out. `race_type_filter`
    # reads four live flags as f"STRIDE_RACE_FILTER_{suffix}" over a
    # module-level rules table, and every one reported zero read sites while
    # setting it genuinely declines races. Binding a loop variable to the
    # literal collection it iterates recovers the names the interpreter would
    # build. Anything not reducible to literals is NOT guessed at -- it is
    # recorded in `unresolved`, because a flag the scanner cannot resolve and
    # does not mention is indistinguishable from one nothing reads.
    binds: Dict[str, List[Any]] = {}      # name -> [(lo, hi, values, origin)]

    def _seq_of(it: ast.AST) -> Optional[List[str]]:
        if isinstance(it, ast.Name):
            return const_seqs.get(it.id)
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute) \
                and it.func.attr in ("items", "keys") and isinstance(it.func.value, ast.Name):
            return const_seqs.get(it.func.value.id)
        return None

    def _bind(target: ast.AST, values: List[str], lo: int, hi: int, origin: str) -> None:
        # `for suffix, (...) in TABLE.items()` binds the key in position 0.
        if isinstance(target, ast.Tuple):
            target = target.elts[0] if target.elts else target
        if isinstance(target, ast.Name):
            binds.setdefault(target.id, []).append((lo, hi, values, origin))

    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            values = _seq_of(node.iter)
            if values:
                _bind(node.target, values, node.lineno,
                      getattr(node, "end_lineno", node.lineno), "seq")
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in node.generators:
                values = _seq_of(gen.iter)
                if values:
                    _bind(gen.target, values, node.lineno,
                          getattr(node, "end_lineno", node.lineno), "seq")

    def _bound(name: str, line: int) -> Optional[Any]:
        """The innermost binding of `name` covering `line`, or None."""
        best = None
        for lo, hi, values, origin in binds.get(name, []):
            if lo <= line <= hi and (best is None or (hi - lo) < best[0]):
                best = (hi - lo, values, origin)
        return (best[1], best[2]) if best else None

    def _fstring(node: ast.JoinedStr, line: int) -> Optional[List[str]]:
        """Every name this f-string can produce, or None if not all literal."""
        parts: List[List[str]] = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                parts.append([piece.value])
            elif isinstance(piece, ast.FormattedValue) and isinstance(piece.value, ast.Name):
                # !r and a format spec change the text the interpreter builds:
                # f"STRIDE_X_{s!r}" is STRIDE_X_'MAIDEN', and f"{s:>10}" pads.
                # Substituting the bare value would record a flag name that is
                # never read -- inventing a site, which is worse than missing
                # one. Neither is resolvable here, so both go to `unresolved`.
                if piece.conversion != -1 or piece.format_spec is not None:
                    return None
                found = _bound(piece.value.id, line)
                if found is None:
                    return None
                parts.append(found[0])
            else:
                return None
        built = [""]
        for part in parts:
            built = [a + b for a in built for b in part]
            if len(built) > 64:      # a family this wide is not a flag table
                return None
        return built

    # `flag = f"STRIDE_RACE_FILTER_{suffix}"` then `_stride_flag(flag)`: the
    # call's argument is a plain Name, so the f-string has to be followed to
    # the variable it lands in before the call can be resolved.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.JoinedStr):
            names = _fstring(node.value, node.lineno)
            interpolated = any(isinstance(v, ast.FormattedValue) for v in node.value.values)
            if names and not interpolated:
                # `F = f"STRIDE_X"` with nothing to substitute is shape 3 wearing
                # an f prefix. Binding it to a loop span would scope it to its
                # own line and lose every later read -- a silent miss, which is
                # the failure this whole resolver exists to remove.
                if len(names) == 1 and _is_flag_name(names[0]):
                    const_names.setdefault(node.targets[0].id, names[0])
            elif names:
                enclosing = [(lo, hi) for entries in binds.values()
                             for lo, hi, _v, _o in entries if lo <= node.lineno <= hi]
                lo, hi = min(enclosing, key=lambda s: s[1] - s[0]) if enclosing else \
                    (node.lineno, getattr(tree, "end_lineno", node.lineno) or node.lineno)
                binds.setdefault(node.targets[0].id, []).append((lo, hi, names, "fstring"))
            elif _fstring_flag_head(node.value) and unresolved is not None:
                unresolved.append(f"{rel}:{node.lineno}")

    # A name rebound inside its loop stops meaning what the loop bound it to.
    # Without this, `flag = f"STRIDE_X_{s}"` ... `flag = other` ... read(flag)
    # credits the second read to the table's names -- inventing a site, which
    # is the failure the conversion/format-spec guard above also exists to
    # avoid. Truncate the binding at the first unresolvable rebinding instead
    # of guessing; the later read then resolves to nothing, like any other
    # variable whose value the source does not determine.
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        entries = binds.get(node.targets[0].id)
        if not entries:
            continue
        if isinstance(node.value, ast.JoinedStr) \
                and _fstring(node.value, node.lineno) is not None:
            continue                      # the resolvable assignment itself
        binds[node.targets[0].id] = [
            (lo, min(hi, node.lineno - 1), values, origin)
            if lo <= node.lineno <= hi else (lo, hi, values, origin)
            for lo, hi, values, origin in entries
        ]

    # Test code reads flags to assert on them, and pops them first to prove the
    # default. Letting those sites contribute to "the code default" invents
    # disagreements: feature_interactions.py's --self-test asserts the unset
    # case through a default-off helper, while the production reader in
    # serve_features.py is default-on. Both are correct; only one is the default.
    file_is_test = Path(rel).name.startswith("test_") or "tests" in Path(rel).parts
    spans: List[Any] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.append((node.lineno, getattr(node, "end_lineno", node.lineno), node.name))

    def in_test_context(line: int) -> bool:
        if file_is_test:
            return True
        enclosing = [n for s, e, n in spans if s <= line <= e]
        return any("self_test" in n or n.startswith("test_") for n in enclosing)

    def rec_for(flag: str) -> Dict[str, Any]:
        return out.setdefault(flag, {"sites": [], "defaults": set(), "shapes": set(),
                                     "writers": [], "value_flag": flag in VALUE_FLAGS})

    def record_read(flag: str, line: int, default: str, shape: str) -> None:
        rec = rec_for(flag)
        is_test = in_test_context(line)
        rec["sites"].append({"at": f"{rel}:{line}", "default": default,
                             "shape": shape, "test": is_test})
        if not is_test:
            rec["defaults"].add(default)
            rec["shapes"].add(shape)

    def record_write(flag: str, line: int) -> None:
        rec_for(flag)["writers"].append(f"{rel}:{line}")

    write_lines: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Subscript) and isinstance(tgt.slice, ast.Constant) \
                        and isinstance(tgt.slice.value, str) and _is_flag_name(tgt.slice.value):
                    record_write(tgt.slice.value, node.lineno)
                    write_lines.add(tgt.lineno)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            first = node.args[0]
            fname = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else "")
            kind = _env_accessor(node.func)
            # A mention is not a read, and an unresolved mention is not a
            # missing reader: print(f"STRIDE_X_{n} ignored") resolves nothing
            # and needs nothing resolved.
            is_read_call = kind is not None or fname in helpers
            origin = None
            if isinstance(first, ast.Constant) and isinstance(first.value, str) \
                    and _is_flag_name(first.value):
                flags, via_const = [first.value], False
            elif isinstance(first, ast.Name) and first.id in const_names:
                flags, via_const = [const_names[first.id]], True
            elif isinstance(first, ast.Name) \
                    and (found := _bound(first.id, node.lineno)) is not None:
                values, origin = found
                flags, via_const = [v for v in values if _is_flag_name(v)], False
            elif isinstance(first, ast.JoinedStr):
                built = _fstring(first, node.lineno)
                if built is None:
                    if is_read_call and _fstring_flag_head(first) and unresolved is not None:
                        unresolved.append(f"{rel}:{node.lineno}")
                    continue
                flags, via_const, origin = [v for v in built if _is_flag_name(v)], False, "fstring"
            else:
                continue
            if not flags:
                continue

            for flag in flags:
                if kind == "write":
                    record_write(flag, node.lineno)
                elif kind == "read":
                    # No second argument is not the same as a required key:
                    # `environ.get(x)` yields None and the caller supplies the
                    # fallback (roi_stats.commission_rate_from_env takes it as a
                    # parameter). Only a subscript actually raises when unset.
                    dflt = _literal(node.args[1]) if len(node.args) > 1 else "<none inline>"
                    shape = origin or ("const" if via_const else "direct")
                    record_read(flag, node.lineno, dflt, shape)
                elif fname in helpers:
                    dflts = helpers[fname]
                    dflt = dflts[0] if len(dflts) == 1 else "|".join(dflts)
                    shape = f"{origin}:{fname}" if origin else f"helper:{fname}"
                    record_read(flag, node.lineno, dflt, shape)
            # Anything else (print, raise, log) mentions the name; it does not read it.

        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and isinstance(node.slice.value, str) and _is_flag_name(node.slice.value) \
                and node.lineno not in write_lines:
            record_read(node.slice.value, node.lineno, "<required>", "subscript")


_SCAN_CACHE: Dict[str, Any] = {}


def scan_readers(repo: Path = REPO,
                 helpers: Optional[Dict[str, List[str]]] = None) -> Dict[str, Dict[str, Any]]:
    # Source does not change inside one process, and a full parse of ~270
    # modules is seconds. build_report is called repeatedly (tests, --json
    # after a table); re-parsing each time made the suite 3x its runtime.
    cache_key = f"readers::{repo}"
    if helpers is None and cache_key in _SCAN_CACHE:
        return _SCAN_CACHE[cache_key]
    helpers = discover_helpers(repo) if helpers is None else helpers
    out: Dict[str, Dict[str, Any]] = {}
    unresolved: List[str] = []
    for path in sorted(repo.rglob("*.py")):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        _scan_module(path, str(path.relative_to(repo)), out, helpers, unresolved)
    _SCAN_CACHE[f"unresolved::{repo}"] = sorted(set(unresolved))
    for rec in out.values():
        rec["defaults"] = sorted(rec["defaults"])
        rec["shapes"] = sorted(rec["shapes"])
        rec["writers"] = sorted(set(rec["writers"]))
    _SCAN_CACHE[cache_key] = out
    return out


def unresolved_dynamic(repo: Path = REPO) -> List[str]:
    """Sites building a STRIDE_ name the scanner could not reduce to literals.

    Empty is the only acceptable value and --self-test enforces it. A site
    here reads a flag under a name this scanner cannot name, so the flag gets
    no read site and reads as dead -- the failure that hid four live
    race filters behind `n_read_sites: 0`. Resolve it by giving the name a
    literal source, or teach the resolver the new shape; do not silence it.
    """
    scan_readers(repo)
    return _SCAN_CACHE.get(f"unresolved::{repo}", [])


def mentioned_names(repo: Path = REPO, code_only: bool = True) -> Set[str]:
    """STRIDE_* tokens appearing in code and config (prose excluded by default).

    The gap between this and the reader set is the point: a name that appears
    in a workflow or a shell script but that no code reads is a control which
    does not exist. Markdown is excluded because prose mentions of a flag are
    documentation, not a claim that the flag is wired.
    """
    suffixes = (".py", ".sh", ".yml", ".yaml", ".json", ".example")
    if not code_only:
        suffixes += (".md",)
    names: Set[str] = set()
    for path in sorted(repo.rglob("*")):
        if path.is_dir() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.suffix not in suffixes:
            continue
        try:
            found = re.findall(r"STRIDE_[A-Z0-9_]+", path.read_text(
                encoding="utf-8", errors="replace"))
        except OSError:
            continue
        names |= {n for n in found if _is_flag_name(n)}
    return names


# --------------------------------------------------------------------------
# Static half 2 — could anything set it?
# --------------------------------------------------------------------------

def secret_keys(path: Path = SECRETS_SH) -> Set[str]:
    """The STRIDE_* keys the deploy ships into the stride/prod blob.

    ``put-secret-value`` replaces the whole blob, so a key absent here is
    erased from the secret on the next deploy even if set by hand — the
    reason a flag can be "on" and still never reach a task
    (``infra/01_secrets.sh`` says so in its own comment).
    """
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"KEYS\s*=\s*\[(.*?)\]", text, re.S)
    if not match:
        return set()
    return {k for k in re.findall(r"[\"']([A-Z0-9_]+)[\"']", match.group(1))
            if k.startswith(PREFIX)}


def handler_setters(path: Path = HANDLER_PY) -> Dict[str, List[str]]:
    """Flags the container image sets for itself (needs an image rebuild)."""
    out: Dict[str, List[str]] = {}
    if not path.exists():
        return out
    scan: Dict[str, Dict[str, Any]] = {}
    _scan_module(path, str(path.relative_to(REPO)), scan, discover_helpers())
    for flag, rec in scan.items():
        if rec["writers"]:
            out[flag] = sorted(set(rec["writers"]))
    return out


def workflow_overrides(dirpath: Path = WORKFLOW_DIR) -> Dict[str, List[str]]:
    """Flags a workflow can pass via ECS containerOverrides on ad-hoc dispatch."""
    out: Dict[str, List[str]] = {}
    if not dirpath.exists():
        return out
    for path in sorted(dirpath.glob("*.yml")) + sorted(dirpath.glob("*.yaml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "containerOverrides" not in text and "EXTRA_ENV" not in text:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if "name" not in line:
                continue
            for flag in re.findall(r"STRIDE_[A-Z0-9_]+", line):
                out.setdefault(flag, []).append(
                    f"{path.relative_to(REPO)}:{line_no}")
    return out


# --------------------------------------------------------------------------
# Runtime half — what is it set to in this process
# --------------------------------------------------------------------------

def fetch_secret_stride_keys(secret_id: Optional[str] = None) -> Optional[Dict[str, str]]:
    """STRIDE_* entries of the live secret, or None if unreadable.

    Only STRIDE_* keys are returned and only they are ever printed: the same
    blob carries DATABASE_URL and four API keys, and a diagnostic that dumps
    its environment is a credential leak, not a diagnostic.
    """
    try:
        import boto3  # noqa: PLC0415 - optional, only for --secret
    except ImportError:
        return None
    sid = secret_id or os.environ.get("STRIDE_SECRET_ID", "stride/prod")
    region = os.environ.get("AWS_REGION", "ap-southeast-2")
    try:
        client = boto3.client("secretsmanager", region_name=region)
        blob = json.loads(client.get_secret_value(SecretId=sid)["SecretString"])
    except Exception:
        return None
    return {k: v for k, v in blob.items() if k.startswith(PREFIX)}


def _scope() -> str:
    """Which environment the runtime column is describing.

    A local run says so. Reporting a laptop's flag set as production is the
    failure this script exists to end, so it never labels itself production
    on inference.
    """
    if os.environ.get("ECS_CONTAINER_METADATA_URI_V4") or os.environ.get("AWS_EXECUTION_ENV"):
        job = os.environ.get("STRIDE_JOB", "") or "unnamed"
        return f"container (STRIDE_JOB={job})"
    if os.environ.get("CI"):
        return "ci"
    return "local checkout — NOT production"


ORIGIN_ENV = "FLAG_STATE_SECRET_ORIGIN"


def secret_origin_from_env() -> Optional[Dict[str, str]]:
    """Exact provenance, when the process that merged the secret recorded it.

    ``handler._load_secrets`` merges with ``setdefault``, so after the merge
    the two layers are indistinguishable — comparing values only tells you
    they agree, not who supplied it. The merger is the one place that knows,
    so it records key -> "secret" | "task-env" and passes it down. Deliberately
    not a STRIDE_ name: this is plumbing for the report, not a flag, and
    naming it STRIDE_* would make the diagnostic list itself.
    """
    raw = os.environ.get(ORIGIN_ENV, "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(k): str(v) for k, v in parsed.items() if str(k).startswith(PREFIX)}


def build_report(secret_values: Optional[Dict[str, str]] = None,
                 secret_origin: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    readers = scan_readers()
    mentioned = mentioned_names()
    in_secret = secret_keys()
    by_handler = handler_setters()
    by_workflow = workflow_overrides()

    # Which delivery inputs were actually readable. Every one of these degrades
    # to empty on a missing path, and empty is indistinguishable from "this
    # flag has no delivery path" — which is the opposite claim. In the image
    # built by infra/Dockerfile none of the three is at its repo path, so an
    # unguarded run reported 47 undeliverable flags instead of 37 and named
    # every retrain-gate flag among them, with exit 0. Availability is data.
    #
    # `workflow` is optional: .github/ is deliberately not in the image (see
    # infra/Dockerfile), because baking CI config in would make every YAML edit
    # mark the image stale. That is only safe while no gate-able flag is
    # deliverable by workflow ALONE — an invariant checked below wherever the
    # source IS readable, so the environment that cannot see it stays safe by
    # construction rather than by luck.
    sources = {
        "secret": {"path": str(SECRETS_SH), "available": SECRETS_SH.exists(),
                   "required": True},
        "image": {"path": str(HANDLER_PY), "available": HANDLER_PY.exists(),
                  "required": True},
        "workflow": {"path": str(WORKFLOW_DIR), "available": WORKFLOW_DIR.exists(),
                     "required": False},
    }
    # The merge record is stronger evidence than the deploy script: it says
    # what the blob actually carried, where 01_secrets.sh only says what the
    # NEXT deploy would ship. When present it stands in for the file.
    if secret_origin:
        in_secret = in_secret | set(secret_origin)
        sources["secret"] = {"path": "recorded at the secret merge",
                             "available": True, "required": True}
    delivery_complete = all(s["available"] for s in sources.values() if s["required"])

    flags: Dict[str, Dict[str, Any]] = {}
    for name in sorted(set(readers) | mentioned | in_secret
                       | set(by_handler) | set(by_workflow)):
        rec = readers.get(name, {})
        delivery = []
        if name in in_secret:
            delivery.append("secret")
        if name in by_handler:
            delivery.append("image")
        if name in by_workflow:
            delivery.append("workflow")

        live = os.environ.get(name)
        if live is None:
            provenance = "unset"
        elif secret_origin is not None and name in secret_origin:
            # Recorded at the merge, so this is a fact rather than a comparison.
            provenance = ("task-env overrode secret"
                          if secret_origin[name] == "task-env" else "secret")
        elif secret_origin is not None:
            provenance = "env/image (secret does not carry it)"
        elif secret_values is None:
            provenance = "env (secret not read)"
        elif name not in secret_values:
            provenance = "env/image (absent from secret)"
        elif secret_values[name] == live:
            provenance = "secret or task-env (identical — ambiguous)"
        else:
            provenance = "task-env overrode secret"

        defaults = rec.get("defaults", [])
        sites = rec.get("sites", [])
        prod_sites = [s for s in sites if not s.get("test")]
        flags[name] = {
            "read_sites": sites,
            "n_read_sites": len(prod_sites),
            "n_test_sites": len(sites) - len(prod_sites),
            "writers": sorted(set(rec.get("writers", []))),
            "shapes": rec.get("shapes", []),
            "code_defaults": defaults,
            "defaults_disagree": len(defaults) > 1,
            "delivery": delivery,
            "delivery_known": delivery_complete,
            "value_flag": name in VALUE_FLAGS,
            "resolved": live,
            "provenance": provenance,
            "effective": live if live is not None else (
                defaults[0] if len(defaults) == 1 else None),
        }

    # Only assertable when every source was read. Half a delivery matrix is
    # not a shorter list of undeliverable flags, it is a wrong one.
    gated = [n for n, f in flags.items()
             if f["n_read_sites"] and not f["delivery"] and not f["value_flag"]
             ] if delivery_complete else []
    # A key the deploy ships, or the image sets, that nothing consumes: a
    # delivery slot spent on a control that does not exist. The inverse of
    # `gated`, and the one worth acting on — prose mentions are not, so the
    # scan that produced them is not reported.
    unread = [n for n, f in flags.items() if f["delivery"] and not f["n_read_sites"]]
    # The invariant that lets the image skip .github/: a gate-able flag whose
    # ONLY delivery is a workflow override would be misreported as undeliverable
    # wherever that source is absent. Recorded here and asserted by --self-test,
    # which runs in the checkout and in CI where the source IS readable.
    workflow_only = sorted(
        n for n, f in flags.items()
        if f["delivery"] == ["workflow"] and f["n_read_sites"] and not f["value_flag"]
    ) if sources["workflow"]["available"] else None
    return {
        "scope": _scope(),
        "secret_compared": secret_values is not None,
        "origin_recorded": secret_origin is not None,
        "delivery_sources": sources,
        "delivery_complete": delivery_complete,
        "workflow_only_gateable": workflow_only,
        "counts": {
            "names_seen": len(flags),
            "with_read_sites": sum(1 for f in flags.values() if f["n_read_sites"]),
            "in_secret_blob": len(in_secret),
            "set_by_image": len(by_handler),
            "no_delivery_path": len(gated),
            "deliverable_but_unread": len(unread),
        },
        "secret_stride_keys": sorted(in_secret),
        "dynamic_unresolved": unresolved_dynamic(),
        "no_delivery_path": sorted(gated),
        "deliverable_but_unread": sorted(unread),
        "flags": flags,
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render(report: Dict[str, Any], only_gated: bool = False) -> str:
    lines = [
        "=== STRIDE FLAG STATE ===",
        f"scope: {report['scope']}",
        f"provenance: {'exact (recorded at the secret merge)' if report.get('origin_recorded') else ('by comparison with the secret' if report['secret_compared'] else 'not resolved — env layer only')}",
        "",
    ]
    if not report.get("delivery_complete", True):
        lines += ["*** DELIVERY MATRIX INCOMPLETE — the columns below are NOT a",
                  "*** finding. These sources could not be read:"]
        for name, src in sorted(report["delivery_sources"].items()):
            if not src["available"]:
                lines.append(f"***   {name}: {src['path']}")
        lines.append("")
    if report.get("dynamic_unresolved"):
        lines += ["*** STRIDE_ NAMES BUILT AT RUNTIME THAT COULD NOT BE RESOLVED.",
                  "*** Each reads a flag under a name this scan cannot record, so",
                  "*** that flag reads as having no reader when it has one:"]
        for site in report["dynamic_unresolved"]:
            lines.append(f"***   {site}")
        lines.append("")
    c = report["counts"]
    lines.append(
        f"{c['names_seen']} names seen | {c['with_read_sites']} have a reader | "
        f"{c['in_secret_blob']} in secret blob | {c['set_by_image']} set by image | "
        f"{c['no_delivery_path']} readable-but-undeliverable | "
        f"{c['deliverable_but_unread']} deliverable-but-unread")
    lines.append("")

    rows = sorted(report["flags"].items())
    if only_gated:
        rows = [(n, f) for n, f in rows if n in report["no_delivery_path"]]

    head = f"{'FLAG':<38} {'DEFAULT':<12} {'RESOLVED':<10} {'DELIVERY':<20} SITES"
    lines += [head, "-" * len(head)]
    for name, f in rows:
        if not f["n_read_sites"] and not f["writers"]:
            continue
        default = ",".join(f["code_defaults"])[:11] or "-"
        if f["defaults_disagree"]:
            default = "!" + default[:10]
        resolved = f["resolved"] if f["resolved"] is not None else "(unset)"
        if not f.get("delivery_known", True):
            delivery = "?? UNKNOWN ??"
        else:
            delivery = "+".join(f["delivery"]) if f["delivery"] else "** NONE **"
        lines.append(f"{name:<38} {default:<12} {str(resolved)[:9]:<10} "
                     f"{delivery:<20} {f['n_read_sites']}")

    if report["no_delivery_path"]:
        lines += ["", "-- READABLE BUT UNDELIVERABLE "
                      "(no secret key, no image setter, no workflow override) --"]
        for name in report["no_delivery_path"]:
            f = report["flags"][name]
            first = f["read_sites"][0]["at"] if f["read_sites"] else "?"
            lines.append(f"  {name:<40} default={','.join(f['code_defaults']) or '-':<10} {first}")

    if report["deliverable_but_unread"]:
        lines += ["", "-- DELIVERABLE BUT READ BY NOTHING (a shipped key with no consumer) --"]
        for name in report["deliverable_but_unread"]:
            lines.append(f"  {name:<40} delivery={'+'.join(report['flags'][name]['delivery'])}")

    disagree = [n for n, f in report["flags"].items() if f["defaults_disagree"]]
    if disagree:
        lines += ["", "-- READ SITES DISAGREE ON THE DEFAULT --"]
        for name in sorted(disagree):
            f = report["flags"][name]
            lines.append(f"  {name}: {f['code_defaults']}")
            for site in f["read_sites"]:
                lines.append(f"      {site['at']}  default={site['default']}  ({site['shape']})")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Self-test — named tripwires, never a count
# --------------------------------------------------------------------------

def self_test() -> int:
    """Fail if the scanner regresses to a shape it used to see.

    Every assertion names a specific flag whose discovery depends on one
    parser feature. A count would pass while silently missing the two thirds
    of flags that are read through a helper.
    """
    failures: List[str] = []

    readers = scan_readers()

    def want(flag: str, why: str) -> None:
        if flag not in readers or not readers[flag]["sites"]:
            failures.append(f"MISSED {flag} — {why}")

    want("STRIDE_TRAIN_ODDS_SOURCE", "shape 1: os.environ.get with a literal")
    want("STRIDE_EV_GATE_AT_PRICE", "shape 2: read only via _flag_enabled(...)")
    want("STRIDE_RENORMALISE_FIELD", "shape 2: read only via _flag_enabled(...)")
    want("STRIDE_LEDGER_WRITE", "shape 2: read only via _stride_flag(...)")

    def want_shape(flag: str, shape: str, why: str) -> None:
        """Assert the shape, not just the flag.

        STRIDE_CTX_MULT_BIAS is read through shape 2 as well, so a presence
        check passes with shape 4 completely broken -- the count-shaped
        assertion this scanner exists to avoid.
        """
        rec = readers.get(flag)
        if not rec or not any(s["shape"] == shape for s in rec["sites"]):
            failures.append(f"MISSED {flag} via {shape} — {why}")

    want_shape("STRIDE_CTX_MULT_BIAS", "seq:_flag_enabled",
               "shape 4: iterated over the module-level _CTX_MULT_FLAGS tuple")
    want_shape("STRIDE_RACE_FILTER_MAIDEN", "fstring:_stride_flag",
               "shape 5: f-string over the RACE_FILTER_RULES table")
    want_shape("STRIDE_RACE_FILTER_HEAVY_GOING", "fstring:_stride_flag",
               "shape 5: has no literal spelling anywhere, so nothing else finds it")

    stranded = unresolved_dynamic()
    if stranded:
        failures.append(
            f"STRIDE_ names built at runtime that the scanner could not resolve: "
            f"{stranded}. Each reads a flag under a name nothing records, so it "
            f"reports zero readers and is indistinguishable from a dead control. "
            f"Give the name a literal source, or teach the resolver the shape.")

    handler = handler_setters()
    if "STRIDE_CTX_MULT_DIAG" not in handler:
        failures.append("MISSED STRIDE_CTX_MULT_DIAG as an image setter — "
                        "shape 3: setdefault(CONST, ...) via a module-level constant")

    keys = secret_keys()
    # Identity, not count: a count passes while the wrong nine are shipped.
    expected = {
        "STRIDE_BOOK_COHERENCE", "STRIDE_COMMISSION_RATE", "STRIDE_LEDGER_WRITE",
        "STRIDE_MODEL_WEIGHT", "STRIDE_RENORMALISE_FIELD", "STRIDE_SERVE_LIVE_FEATURES",
        "STRIDE_SERVE_LIVE_FEATURES_SHADOW", "STRIDE_SERVE_NAN_CONTRACT",
        "STRIDE_SHADOW_KELLY",
    }
    if keys != expected:
        failures.append(f"secret KEYS parse drifted: missing={sorted(expected - keys)} "
                        f"unexpected={sorted(keys - expected)}")

    report = build_report()

    # Every delivery source must have been read before any "no delivery path"
    # verdict below is worth anything. In the container none of the three was
    # at its repo path and all three degraded to empty, so the report inverted
    # its own headline finding and exited 0.
    if not report["delivery_complete"]:
        missing = {k: v["path"] for k, v in report["delivery_sources"].items()
                   if not v["available"]}
        failures.append(f"delivery sources unreadable: {missing} — every flag "
                        f"would report 'no delivery path'")
    if report["delivery_complete"] and not report["no_delivery_path"]:
        failures.append("delivery sources all read but nothing is undeliverable — "
                        "the repo has dozens; the delivery scan is not working")
    # Runs where .github/ IS readable, so the image — which has no .github/ by
    # design — is safe by proof rather than by assumption.
    if report["workflow_only_gateable"]:
        failures.append(
            f"gate-able flags deliverable ONLY by a workflow override: "
            f"{report['workflow_only_gateable']}. The image has no .github/, so "
            f"these would report 'no delivery path' there. Either add the flag "
            f"to the secret blob, or make the workflow source required and copy "
            f"it into the image.")

    for flag in ("STRIDE_EV_GATE_AT_PRICE", "STRIDE_ML_APPLY_ISOTONIC"):
        if flag not in report["no_delivery_path"]:
            failures.append(f"{flag} should report NO delivery path "
                            f"(got {report['flags'][flag]['delivery']})")
    if "STRIDE_SERVE_LIVE_FEATURES" in report["no_delivery_path"]:
        failures.append("STRIDE_SERVE_LIVE_FEATURES is in the secret blob; "
                        "reporting it undeliverable means the delivery scan broke")

    # The scanner must never claim to be describing a container it is not in.
    # Stated as "scope must contain 'NOT production'" this passed locally and
    # failed every CI run, because _scope() has a third answer ("ci") that the
    # assertion did not allow for — a tripwire that fired on a correct scope.
    # The real invariant is narrower: only claim `container` inside one.
    in_container = bool(os.environ.get("AWS_EXECUTION_ENV")
                        or os.environ.get("ECS_CONTAINER_METADATA_URI_V4"))
    if "container" in report["scope"] and not in_container:
        failures.append(f"scope claims a container from outside one: {report['scope']!r}")

    for line in failures:
        print(f"FAIL: {line}", file=sys.stderr)
    if failures:
        print(f"{len(failures)} self-test failure(s)", file=sys.stderr)
        return 1
    print("flag_state self-test: OK (6 read shapes, runtime-built names resolved, "
          "image setter, secret identity, delivery verdicts, scope honesty)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--secret", action="store_true",
                    help="also read stride/prod to resolve env-vs-secret provenance")
    ap.add_argument("--gated-only", action="store_true",
                    help="only flags with no delivery path")
    ap.add_argument("--evidence", metavar="FILENAME",
                    help="also write the full JSON report to the evidence store "
                         "under this name (the caller supplies the date, so this "
                         "module needs no clock)")
    ap.add_argument("--self-test", action="store_true", help="run scanner tripwires")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    secret_values = None
    if args.secret:
        secret_values = fetch_secret_stride_keys()
        if secret_values is None:
            print("  [flag_state] secret unreadable (no boto3, no creds, or denied) — "
                  "provenance stays ambiguous", file=sys.stderr)

    report = build_report(secret_values, secret_origin_from_env())

    if args.evidence:
        # Durable copy. A container's stdout is tail-bounded by the caller and
        # the container itself is gone minutes later, so the log is not a
        # record. Reported on stderr so --json stdout stays parseable.
        try:
            from evidence_store import put_evidence  # noqa: PLC0415 - optional
            stored = put_evidence(args.evidence,
                                  json.dumps(report, indent=1, sort_keys=True))
            print(f"  [flag_state] evidence: {stored}", file=sys.stderr)
        except Exception as e:  # never fail the report over its own filing
            print(f"  [flag_state] evidence write failed ({type(e).__name__}: {e}) — "
                  f"the report below is still complete", file=sys.stderr)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render(report, only_gated=args.gated_only))
    return 0


if __name__ == "__main__":
    sys.exit(main())
