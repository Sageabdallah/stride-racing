"""The golden and injection suites, ported from stride-app/scripts/eval_chat.ts.

Three modes, same assertion engine:

  python -m chat.eval_runner                 offline: fixtures only, no network
  python -m chat.eval_runner --self-test     engine and corpus schema checks
  python -m chat.eval_runner --live-cli      run every case through ChatEngine
                                             in this process (operator only:
                                             needs EVAL_LIVE_CONFIRM=yes and an
                                             ANTHROPIC_API_KEY; costs tokens)
  python -m chat.eval_runner --live-url URL  POST each case to URL/api/chat

The assertion engine is a line-for-line port of assertCase() and
unverifiedLinks() from the TypeScript runner, so a case passes or fails for
the same reason here as there. Two things are deliberately different:

* tool calls are read from the response's `toolCalls` field (plan §5), not
  from `trace`, because `trace` is a ChatTrace object, not the array the
  TypeScript live mode assumed;
* cases whose `modes.search` is true are reported `unsupported` in the live
  modes, because search is not ported yet (plan §6, phase 5). They are not
  counted as passes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

EVALS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evals")
KNOWN_TOP = {"id", "category", "question", "modes", "setup", "expect"}
KNOWN_EXPECT = {"tools_any_of", "tools_none_of", "must_contain_any", "must_not_contain",
                "must_cite_web", "http_status"}
OVERSIZED_MARKER = "OVERSIZED_MESSAGE_PLACEHOLDER"


@dataclass
class Recorded:
    case_id: str
    response: str
    http_status: Optional[int] = None
    citations: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[str] = field(default_factory=list)


# -- corpus -----------------------------------------------------------------

def schema_errors(c: Any, line: int, file: str) -> List[str]:
    errs: List[str] = []
    where = f"{file}:{line}"
    if not isinstance(c, dict):
        return [f"{where}: not an object"]
    for k in ("id", "category", "question"):
        if not isinstance(c.get(k), str) or not c.get(k):
            errs.append(f"{where}: missing/empty string field \"{k}\"")
    for k in c:
        if k not in KNOWN_TOP:
            errs.append(f"{where}: unknown top-level key \"{k}\"")
    if "setup" in c and (not isinstance(c["setup"], list) or any(not isinstance(s, str) for s in c["setup"])):
        errs.append(f"{where}: \"setup\" must be a string array")
    e = c.get("expect")
    if not isinstance(e, dict):
        errs.append(f"{where}: missing \"expect\" object")
        return errs
    for k in e:
        if k not in KNOWN_EXPECT:
            errs.append(f"{where}: unknown expect key \"{k}\"")
    for k in ("tools_any_of", "tools_none_of", "must_contain_any", "must_not_contain"):
        if k in e and (not isinstance(e[k], list) or any(not isinstance(s, str) for s in e[k])):
            errs.append(f"{where}: expect.{k} must be a string array")
    if "must_cite_web" in e and not isinstance(e["must_cite_web"], bool):
        errs.append(f"{where}: expect.must_cite_web must be boolean")
    if "http_status" in e and (not isinstance(e["http_status"], int) or isinstance(e["http_status"], bool)):
        errs.append(f"{where}: expect.http_status must be number")
    return errs


def load_corpus(file: str, corpus_dir: str = EVALS_DIR) -> Tuple[List[Dict[str, Any]], List[str]]:
    cases: List[Dict[str, Any]] = []
    errors: List[str] = []
    with open(os.path.join(corpus_dir, file), "r", encoding="utf-8") as fh:
        for i, ln in enumerate(fh, 1):
            t = ln.strip()
            if not t:
                continue
            try:
                parsed = json.loads(t)
            except ValueError:
                errors.append(f"{file}:{i}: invalid JSON")
                continue
            errs = schema_errors(parsed, i, file)
            if errs:
                errors.extend(errs)
            else:
                cases.append(parsed)
    seen = set()
    for c in cases:
        if c["id"] in seen:
            errors.append(f"{file}: duplicate case id \"{c['id']}\"")
        seen.add(c["id"])
    return cases, errors


# -- assertion engine (port of assertCase / unverifiedLinks) -----------------

def normalize_url(u: str) -> str:
    try:
        p = urlsplit(u.strip())
        if not p.scheme or not p.netloc:
            raise ValueError
        origin = f"{p.scheme}://{p.netloc}".lower()
        return origin + re.sub(r"/+$", "", p.path) + (f"?{p.query}" if p.query else "")
    except ValueError:
        return re.sub(r"/+$", "", u.strip())


_LINK = re.compile(r"\[[^\]]*\]\((https?://[^\s)]+)\)")


def unverified_links(text: str, citations: List[Dict[str, Any]]) -> List[str]:
    allowed = {normalize_url(c.get("url", "")) for c in citations if isinstance(c, dict)}
    return [m.group(1) for m in _LINK.finditer(text or "") if normalize_url(m.group(1)) not in allowed]


def assert_case(c: Dict[str, Any], r: Recorded) -> List[str]:
    failures: List[str] = []
    text = r.response or ""
    lower = text.lower()
    e = c.get("expect") or {}
    if "http_status" in e:
        if r.http_status != e["http_status"]:
            failures.append(f"expected http_status {e['http_status']}, got {r.http_status if r.http_status is not None else 'none'}")
        return failures
    if e.get("must_contain_any") and not any(s.lower() in lower for s in e["must_contain_any"]):
        failures.append("response contains none of: " + " | ".join(e["must_contain_any"]))
    for s in e.get("must_not_contain") or []:
        if s.lower() in lower:
            failures.append(f"response contains forbidden: \"{s}\"")
    if e.get("must_cite_web") and not r.citations:
        failures.append("expected web citations, got none")
    tools = [t.lower() for t in (r.tool_calls or [])]
    if e.get("tools_any_of") and not any(t.lower() in tools for t in e["tools_any_of"]):
        failures.append("no expected tool called (wanted one of: " + ", ".join(e["tools_any_of"]) + ")")
    for t in e.get("tools_none_of") or []:
        if t.lower() in tools:
            failures.append(f"forbidden tool called: {t}")
    bad = unverified_links(text, r.citations)
    if bad:
        failures.append("unverified link(s) in prose: " + ", ".join(bad))
    return failures


# -- modes ------------------------------------------------------------------

@dataclass
class CaseResult:
    id: str
    outcome: str  # pass | fail | skipped | unsupported
    failures: List[str] = field(default_factory=list)
    inverted: bool = False


def load_fixtures(corpus_dir: str = EVALS_DIR) -> Dict[str, Tuple[str, Recorded]]:
    fdir = os.path.join(corpus_dir, "fixtures")
    out: Dict[str, Tuple[str, Recorded]] = {}
    if not os.path.isdir(fdir):
        return out
    for f in sorted(x for x in os.listdir(fdir) if x.endswith(".json")):
        with open(os.path.join(fdir, f), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        rec = Recorded(case_id=raw["case_id"], response=raw.get("response", ""),
                       http_status=raw.get("http_status"), citations=raw.get("citations") or [],
                       tool_calls=raw.get("tool_calls") or [])
        if rec.case_id in out:
            raise RuntimeError(f"fixture collision: {out[rec.case_id][0]} and {f} both claim case_id \"{rec.case_id}\"")
        out[rec.case_id] = (f, rec)
    return out


def run_offline(cases: List[Dict[str, Any]], corpus_dir: str = EVALS_DIR) -> List[CaseResult]:
    fixtures = load_fixtures(corpus_dir)
    if not fixtures:
        raise RuntimeError("no fixtures found; an empty fixtures/ dir must not read as a passing suite")
    ids = {c["id"] for c in cases}
    for case_id, (name, _) in fixtures.items():
        if case_id not in ids:
            raise RuntimeError(f"orphan fixture {name}: case_id \"{case_id}\" matches no corpus case")
    results = []
    for c in cases:
        fx = fixtures.get(c["id"])
        if not fx:
            results.append(CaseResult(c["id"], "skipped"))
            continue
        name, rec = fx
        failures = assert_case(c, rec)
        inverted = name.startswith("should_fail_")
        if inverted:
            outcome = "pass" if failures else "fail"
            failures = [] if failures else ["control fixture did NOT trip any assertion; assertions are toothless"]
        else:
            outcome = "fail" if failures else "pass"
        results.append(CaseResult(c["id"], outcome, failures, inverted))
    return results


def live_question(q: str) -> str:
    return "x" * 4001 if OVERSIZED_MARKER in q else q


def _unsupported(c: Dict[str, Any]) -> bool:
    return bool((c.get("modes") or {}).get("search"))


def run_live_cli(cases: List[Dict[str, Any]], pacing_seconds: float = 0.0) -> List[CaseResult]:
    if os.environ.get("EVAL_LIVE_CONFIRM") != "yes":
        print("REFUSED: --live-cli requires EVAL_LIVE_CONFIRM=yes. Live evals are operator-only.",
              file=sys.stderr)
        sys.exit(2)
    from .contract import ChatRequestError, parse_request
    from .loop import ChatTurnError
    from .runtime import build_context, build_engine
    engine = build_engine(build_context())

    def post(case_id: str, message: str, modes: Dict[str, Any]) -> Recorded:
        try:
            req = parse_request({"message": message, "sessionId": f"eval-{case_id}", "modes": modes or {}})
        except ChatRequestError:
            return Recorded(case_id=case_id, response="", http_status=400)
        try:
            body = engine.run_turn(req)
        except ChatTurnError as e:
            return Recorded(case_id=case_id, response=str(e), http_status=500)
        return Recorded(case_id=case_id, response=body.get("response", ""), http_status=200,
                        citations=body.get("citations") or [], tool_calls=body.get("toolCalls") or [])

    results = []
    for c in cases:
        if _unsupported(c):
            results.append(CaseResult(c["id"], "unsupported", ["needs search mode (not ported)"]))
            continue
        for s in c.get("setup") or []:
            post(c["id"], s, c.get("modes") or {})
            time.sleep(pacing_seconds)
        rec = post(c["id"], live_question(c["question"]), c.get("modes") or {})
        failures = assert_case(c, rec)
        results.append(CaseResult(c["id"], "fail" if failures else "pass", failures))
        time.sleep(pacing_seconds)
    return results


def run_live_url(cases: List[Dict[str, Any]], target: str, pacing_seconds: float = 3.5) -> List[CaseResult]:
    if os.environ.get("EVAL_LIVE_CONFIRM") != "yes":
        print("REFUSED: --live-url requires EVAL_LIVE_CONFIRM=yes. Live evals are operator-only.",
              file=sys.stderr)
        sys.exit(2)

    def post(case_id: str, message: str, modes: Dict[str, Any]) -> Recorded:
        data = json.dumps({"message": message, "sessionId": f"eval-{case_id}", "modes": modes or {}}).encode()
        req = urllib.request.Request(f"{target.rstrip('/')}/api/chat", data=data,
                                     headers={"content-type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                status = resp.status
                body = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            return Recorded(case_id=case_id, response="", http_status=e.code)
        return Recorded(case_id=case_id, response=body.get("response", ""), http_status=status,
                        citations=body.get("citations") or [], tool_calls=body.get("toolCalls") or [])

    results = []
    for c in cases:
        if _unsupported(c):
            results.append(CaseResult(c["id"], "unsupported", ["needs search mode (not ported)"]))
            continue
        for s in c.get("setup") or []:
            post(c["id"], s, c.get("modes") or {})
            time.sleep(pacing_seconds)
        rec = post(c["id"], live_question(c["question"]), c.get("modes") or {})
        failures = assert_case(c, rec)
        results.append(CaseResult(c["id"], "fail" if failures else "pass", failures))
        time.sleep(pacing_seconds)
    return results


# -- self-test ----------------------------------------------------------------

def self_test(corpus_dir: str = EVALS_DIR) -> int:
    failed = 0

    def check(name: str, ok: bool) -> None:
        nonlocal failed
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
        if not ok:
            failed += 1

    check("unverified link detected", unverified_links("see [x](https://evil.example/a)", []) == ["https://evil.example/a"])
    check("verified link passes", unverified_links("see [x](https://ok.example/a/)", [{"url": "https://OK.example/a"}]) == [])
    check("path case is significant", unverified_links("see [x](https://ok.example/A)", [{"url": "https://ok.example/a"}]) != [])
    c = {"id": "t", "category": "t", "question": "q", "expect": {"must_not_contain": ["SELECT "], "tools_any_of": ["lookup_horse"]}}
    check("forbidden substring trips", assert_case(c, Recorded("t", "SELECT * from x", tool_calls=["lookup_horse"])) != [])
    check("missing tool trips", assert_case(c, Recorded("t", "fine", tool_calls=[])) != [])
    check("clean response passes", assert_case(c, Recorded("t", "fine", tool_calls=["Lookup_Horse"])) == [])
    check("http_status short-circuits", assert_case({"id": "t", "category": "t", "question": "q", "expect": {"http_status": 400}},
                                                    Recorded("t", "", http_status=400)) == [])
    for f in ("golden.jsonl", "injection.jsonl"):
        cases, errors = load_corpus(f, corpus_dir)
        check(f"{f} schema ({len(cases)} cases)", not errors and len(cases) > 0)
        for e in errors:
            print("    ", e)
    return failed


# -- main ------------------------------------------------------------------------

def _report(results: List[CaseResult]) -> int:
    counts = {"pass": 0, "fail": 0, "skipped": 0, "unsupported": 0}
    for r in results:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
        tag = {"pass": "PASS", "fail": "FAIL", "skipped": "skip", "unsupported": "n/a "}[r.outcome]
        line = f"{tag} {r.id}" + (" (inverted control)" if r.inverted else "")
        print(line)
        for f in r.failures:
            print(f"      - {f}")
    print(f"\n{counts['pass']} passed, {counts['fail']} failed, {counts['skipped']} skipped, "
          f"{counts['unsupported']} unsupported")
    return 1 if counts["fail"] else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m chat.eval_runner", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--live-cli", action="store_true")
    parser.add_argument("--live-url", metavar="URL")
    parser.add_argument("--corpus-dir", default=EVALS_DIR, help="Directory holding golden.jsonl, injection.jsonl, fixtures/.")
    parser.add_argument("--only", help="Comma-separated case ids.")
    args = parser.parse_args(argv)

    if args.self_test:
        failed = self_test(args.corpus_dir)
        print("self-test:", "ok" if not failed else f"{failed} failed")
        return 1 if failed else 0

    cases: List[Dict[str, Any]] = []
    errors: List[str] = []
    for f in ("golden.jsonl", "injection.jsonl"):
        cs, es = load_corpus(f, args.corpus_dir)
        cases.extend(cs)
        errors.extend(es)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 2
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        cases = [c for c in cases if c["id"] in wanted]

    if args.live_url:
        return _report(run_live_url(cases, args.live_url))
    if args.live_cli:
        return _report(run_live_cli(cases))
    return _report(run_offline(cases, args.corpus_dir))


if __name__ == "__main__":
    sys.exit(main())
