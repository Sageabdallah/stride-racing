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
place — flags are read through three shapes, and a scanner that knows only the
first misses **30 of the 74 flags that have a production reader** (measured),
among them every ``STRIDE_CTX_MULT_*``, ``STRIDE_LEDGER_WRITE`` and
``STRIDE_EV_GATE_AT_PRICE``:

  1. direct       ``os.environ.get("STRIDE_X", "false")`` / ``os.environ["STRIDE_X"]``
  2. helper       ``_flag_enabled("STRIDE_X")`` — the dominant idiom here
  3. const        ``F = "STRIDE_X"`` … ``os.environ.setdefault(F, "true")``

All three are parsed, every flag reports which shape found it, and ``--self-test``
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
SECRETS_SH = REPO / "infra" / "01_secrets.sh"
HANDLER_PY = REPO / "infra" / "jobs" / "handler.py"
WORKFLOW_DIR = REPO / ".github" / "workflows"

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
                 helpers: Dict[str, List[str]]) -> None:
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
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str) and _is_flag_name(node.value.value):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    const_names[tgt.id] = node.value.value

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
            if isinstance(first, ast.Constant) and isinstance(first.value, str) \
                    and _is_flag_name(first.value):
                flag, via_const = first.value, False
            elif isinstance(first, ast.Name) and first.id in const_names:
                flag, via_const = const_names[first.id], True
            else:
                continue

            fname = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else "")
            kind = _env_accessor(node.func)

            if kind == "write":
                record_write(flag, node.lineno)
            elif kind == "read":
                # No second argument is not the same as a required key:
                # `environ.get(x)` yields None and the caller supplies the
                # fallback (roi_stats.commission_rate_from_env takes it as a
                # parameter). Only a subscript actually raises when unset.
                dflt = _literal(node.args[1]) if len(node.args) > 1 else "<none inline>"
                record_read(flag, node.lineno, dflt, "const" if via_const else "direct")
            elif fname in helpers:
                dflts = helpers[fname]
                dflt = dflts[0] if len(dflts) == 1 else "|".join(dflts)
                record_read(flag, node.lineno, dflt, f"helper:{fname}")
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
    for path in sorted(repo.rglob("*.py")):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        _scan_module(path, str(path.relative_to(repo)), out, helpers)
    for rec in out.values():
        rec["defaults"] = sorted(rec["defaults"])
        rec["shapes"] = sorted(rec["shapes"])
        rec["writers"] = sorted(set(rec["writers"]))
    _SCAN_CACHE[cache_key] = out
    return out


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
            "value_flag": name in VALUE_FLAGS,
            "resolved": live,
            "provenance": provenance,
            "effective": live if live is not None else (
                defaults[0] if len(defaults) == 1 else None),
        }

    gated = [n for n, f in flags.items()
             if f["n_read_sites"] and not f["delivery"] and not f["value_flag"]]
    # A key the deploy ships, or the image sets, that nothing consumes: a
    # delivery slot spent on a control that does not exist. The inverse of
    # `gated`, and the one worth acting on — prose mentions are not, so the
    # scan that produced them is not reported.
    unread = [n for n, f in flags.items() if f["delivery"] and not f["n_read_sites"]]
    return {
        "scope": _scope(),
        "secret_compared": secret_values is not None,
        "origin_recorded": secret_origin is not None,
        "counts": {
            "names_seen": len(flags),
            "with_read_sites": sum(1 for f in flags.values() if f["n_read_sites"]),
            "in_secret_blob": len(in_secret),
            "set_by_image": len(by_handler),
            "no_delivery_path": len(gated),
            "deliverable_but_unread": len(unread),
        },
        "secret_stride_keys": sorted(in_secret),
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
    print("flag_state self-test: OK (4 read shapes, image setter, secret identity, "
          "delivery verdicts, scope honesty)")
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
