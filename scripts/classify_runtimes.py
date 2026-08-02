#!/usr/bin/env python3
"""Derive each job's required runtime from its transitive import graph.

The Lambda/Fargate split was originally decided by reading each job's own
source for filesystem writes. That method missed calibrator-coverage,
whose fatal write lives three imports deep (shadow_calibrator_compare ->
fit_calibrator -> walk_forward_backtest, which calls os.makedirs at module
scope). Reading cannot find that; walking the graph can.

Two classes of finding, with very different severity:

  IMPORT-TIME write — executed the moment the module is imported, so the
  job dies on Lambda before running any of its own logic, unconditionally.
  This is disqualifying.

  RUNTIME write — inside a function. Only fatal if that path is taken, so
  it is a warning: the job may work until the day it takes that branch.

Model loads are reported too: a Lambda cannot stage artifacts (read-only
filesystem), so any job that loads a .pkl belongs on Fargate.

Writes under a tempfile/TMPDIR path, or guarded by the evidence store's
writability probe, are not counted — those are the sanctioned escape.

Usage:
    python scripts/classify_runtimes.py            # report
    python scripts/classify_runtimes.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / "server" / "python"
HANDLER = ROOT / "infra" / "jobs" / "handler.py"
LAMBDA_SH = ROOT / "infra" / "05_lambda_jobs.sh"
FARGATE_SH = ROOT / "infra" / "07_fargate_heavy.sh"

WRITE_FUNCS = {
    "makedirs", "mkdir", "write_text", "write_bytes", "touch",
    "to_csv", "to_json", "to_parquet", "savefig", "copy", "copy2",
    "copyfile", "move", "unlink", "rmtree", "dump",
}
# rename/replace are only writes on os/shutil/Path — datetime.replace and
# str.replace are far more common and would drown the report in noise.
QUALIFIED_WRITES = {"rename", "replace"}
QUALIFIED_RECEIVERS = ("os.", "shutil.", "Path(", "path.", "_path.", "p.")
# Paths that are legitimately writable in every runtime.
TMP_HINTS = ("/tmp", "tempfile", "gettempdir", "TemporaryDirectory",
             "NamedTemporaryFile", "mkdtemp", "MPLCONFIGDIR")
MODEL_HINTS = (".pkl", "joblib.load", "pickle.load", "ProbabilityCalibrator",
               "racing_ensemble")


def _job_to_scripts() -> Dict[str, List[str]]:
    """job name -> scripts it shells out to, from the handler's JOBS dict."""
    tree = ast.parse(HANDLER.read_text())
    fn_scripts: Dict[str, List[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            # EVERY .py string literal in the function body, not just those
            # in _run call position: nightly_etl holds its scripts in a
            # tuple of (script, args) pairs, and reading only call args
            # reported an empty import graph for it — the same
            # read-the-surface error that misclassified calibrator-coverage.
            scripts = []
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                        and sub.value.endswith(".py")):
                    scripts.append(sub.value)
            # Jobs that call other jobs (gap-heal) inherit their graphs.
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Name)
                        and sub.id.startswith("job_")
                        and sub.id != node.name):
                    scripts.append(f"@{sub.id}")
            fn_scripts[node.name] = scripts

    jobs: Dict[str, List[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "JOBS" for t in node.targets):
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Name):
                    jobs[k.value] = fn_scripts.get(v.id, [])
    return jobs


def _assignments() -> Tuple[Set[str], Set[str]]:
    """(lambda jobs, fargate jobs) as the infra scripts actually create them."""
    lam = set(re.findall(r'^\s*"([a-z-]+)\s+\d+\s+\d+"',
                         LAMBDA_SH.read_text(), re.M))
    far = set(re.findall(r'"([a-z-]+)\s+\d+\s+\d+"', FARGATE_SH.read_text()))
    return lam, far


def _local_imports(path: Path) -> Set[str]:
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, OSError):
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return {m for m in out if (PY / f"{m}.py").exists()}


def import_graph(entry: str) -> List[str]:
    """Every local module reachable from entry, entry included."""
    seen, stack = set(), [entry.replace(".py", "")]
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        stack.extend(_local_imports(PY / f"{mod}.py") - seen)
    return sorted(seen)


def _is_tmp(node: ast.AST, src: str) -> bool:
    seg = ast.get_source_segment(src, node) or ""
    return any(h in seg for h in TMP_HINTS)


def scan_module(mod: str) -> Dict[str, List[str]]:
    """Import-time writes, runtime writes and model loads in one module."""
    path = PY / f"{mod}.py"
    out = {"import_writes": [], "runtime_writes": [], "model_loads": []}
    try:
        src = path.read_text()
        tree = ast.parse(src)
    except (SyntaxError, OSError):
        return out

    # Map every node to whether it sits inside a function/class body.
    in_func: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            for sub in ast.walk(node):
                in_func.add(id(sub))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else "")
        seg = (ast.get_source_segment(src, node) or "")[:90].replace("\n", " ")
        line = getattr(node, "lineno", 0)

        is_write = name in WRITE_FUNCS
        if name in QUALIFIED_WRITES:
            is_write = any(r in seg for r in QUALIFIED_RECEIVERS)
        if name == "open":
            mode = ""
            for a in node.args[1:2]:
                if isinstance(a, ast.Constant):
                    mode = str(a.value)
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            is_write = any(c in mode for c in "wax")

        if is_write and not _is_tmp(node, src):
            bucket = "runtime_writes" if id(node) in in_func else "import_writes"
            out[bucket].append(f"{mod}.py:{line}  {seg}")

        if any(h in seg for h in MODEL_HINTS):
            out["model_loads"].append(f"{mod}.py:{line}  {seg}")
    return out


def analyse() -> Dict[str, dict]:
    jobs = _job_to_scripts()
    lam, far = _assignments()
    # Resolve job-calls-job references (gap-heal runs racecard and results
    # in-process, so it inherits every write they can make).
    tree = ast.parse(HANDLER.read_text())
    fn_of_job = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "JOBS" for t in node.targets):
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Name):
                    fn_of_job[v.id] = k.value
    for job, scripts in list(jobs.items()):
        expanded = []
        for s in scripts:
            if s.startswith("@"):
                other = fn_of_job.get(s[1:])
                if other and other in jobs:
                    expanded.extend(x for x in jobs[other] if not x.startswith("@"))
            else:
                expanded.append(s)
        jobs[job] = expanded

    report: Dict[str, dict] = {}
    for job, scripts in sorted(jobs.items()):
        assigned = ("lambda" if job in lam else
                    "fargate" if job in far else "unassigned")
        modules: Set[str] = set()
        for s in scripts:
            modules.update(import_graph(s))
        findings = {"import_writes": [], "runtime_writes": [], "model_loads": []}
        for m in sorted(modules):
            f = scan_module(m)
            for k in findings:
                findings[k].extend(f[k])
        required = ("fargate" if findings["import_writes"]
                    or findings["model_loads"] else "lambda-safe")
        report[job] = {
            "assigned": assigned,
            "required": required,
            "scripts": scripts,
            "modules_in_graph": len(modules),
            "verdict": ("OK" if required == "lambda-safe" or assigned == "fargate"
                        else "MISCLASSIFIED"),
            **findings,
        }
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = analyse()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    bad = 0
    for job, r in report.items():
        if r["assigned"] == "unassigned":
            continue
        flag = "" if r["verdict"] == "OK" else "  <-- MISCLASSIFIED"
        print(f"\n{job}: assigned={r['assigned']} required={r['required']}"
              f" ({r['modules_in_graph']} modules in graph){flag}")
        if r["assigned"] == "lambda":
            for w in r["import_writes"][:6]:
                print(f"    IMPORT-TIME WRITE  {w}")
            for w in r["model_loads"][:3]:
                print(f"    MODEL LOAD         {w}")
            if not r["import_writes"] and not r["model_loads"]:
                n = len(r["runtime_writes"])
                print(f"    no import-time writes, no model loads"
                      f" ({n} runtime write(s) — only fatal if that path runs)")
                for w in r["runtime_writes"][:4]:
                    print(f"      runtime: {w}")
        if r["verdict"] != "OK":
            bad += 1
    print(f"\n{'=' * 60}\nmisclassified: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
