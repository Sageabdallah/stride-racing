/**
 * STRIDE chat eval harness (CB-5).
 *
 * Default (CI) mode is fully offline: it validates the corpus files and runs
 * every assertion against recorded fixtures in evals/chat/fixtures/. It must
 * be impossible for this mode to make a network call — there is no fetch in
 * the CI code path at all.
 *
 * --live mode sends each case to a RUNNING app over HTTP (POST /api/chat).
 * It is operator-only: refuses to start unless EVAL_TARGET_URL is set and
 * EVAL_LIVE_CONFIRM=yes, so an automated run can never trigger it.
 *
 * Usage:
 *   npx tsx scripts/eval_chat.ts               # CI mode
 *   npx tsx scripts/eval_chat.ts --self-test   # schema + engine self-checks
 *   EVAL_TARGET_URL=http://localhost:5000 EVAL_LIVE_CONFIRM=yes \
 *     npx tsx scripts/eval_chat.ts --live      # operator only
 */

import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Expect {
  tools_any_of?: string[];
  tools_none_of?: string[];
  must_contain_any?: string[];
  must_not_contain?: string[];
  must_cite_web?: boolean;
  http_status?: number;
}

interface EvalCase {
  id: string;
  category: string;
  question: string;
  modes?: { brain?: boolean; search?: boolean };
  /** Prior turns sent to the same session before the graded question —
   *  gives follow-up cases real conversation context in live mode. */
  setup?: string[];
  expect: Expect;
}

interface Citation {
  url: string;
  title?: string;
}

/** Shape of a recorded (or live) chat response the engine asserts against.
 *  Mirrors the relevant subset of ChatCompletionResponse. */
interface RecordedResponse {
  case_id: string;
  http_status?: number;
  response: string;
  citations?: Citation[];
  tool_calls?: string[];
}

interface CaseResult {
  id: string;
  outcome: "pass" | "fail" | "skipped";
  failures: string[];
  invertedControl: boolean;
}

// ---------------------------------------------------------------------------
// Corpus loading + schema validation (no external deps)
// ---------------------------------------------------------------------------

const ROOT = path.resolve(__dirname, "..");
const CORPUS_DIR = path.join(ROOT, "evals", "chat");
const FIXTURE_DIR = path.join(CORPUS_DIR, "fixtures");

function schemaErrors(c: unknown, line: number, file: string): string[] {
  const errs: string[] = [];
  const where = `${file}:${line}`;
  if (typeof c !== "object" || c === null) return [`${where}: not an object`];
  const o = c as Record<string, unknown>;
  for (const k of ["id", "category", "question"]) {
    if (typeof o[k] !== "string" || (o[k] as string).length === 0) {
      errs.push(`${where}: missing/empty string field "${k}"`);
    }
  }
  const knownTop = new Set(["id", "category", "question", "modes", "setup", "expect"]);
  for (const k of Object.keys(o)) {
    if (!knownTop.has(k)) errs.push(`${where}: unknown top-level key "${k}"`);
  }
  if (o.setup !== undefined && (!Array.isArray(o.setup) || (o.setup as unknown[]).some((s) => typeof s !== "string"))) {
    errs.push(`${where}: "setup" must be a string array`);
  }
  if (typeof o.expect !== "object" || o.expect === null) {
    errs.push(`${where}: missing "expect" object`);
    return errs;
  }
  const e = o.expect as Record<string, unknown>;
  const known = new Set([
    "tools_any_of",
    "tools_none_of",
    "must_contain_any",
    "must_not_contain",
    "must_cite_web",
    "http_status",
  ]);
  for (const k of Object.keys(e)) {
    if (!known.has(k)) errs.push(`${where}: unknown expect key "${k}"`);
  }
  for (const k of ["tools_any_of", "tools_none_of", "must_contain_any", "must_not_contain"]) {
    if (e[k] !== undefined && (!Array.isArray(e[k]) || (e[k] as unknown[]).some((s) => typeof s !== "string"))) {
      errs.push(`${where}: expect.${k} must be a string array`);
    }
  }
  if (e.must_cite_web !== undefined && typeof e.must_cite_web !== "boolean") {
    errs.push(`${where}: expect.must_cite_web must be boolean`);
  }
  if (e.http_status !== undefined && typeof e.http_status !== "number") {
    errs.push(`${where}: expect.http_status must be number`);
  }
  return errs;
}

function loadCorpus(file: string): { cases: EvalCase[]; errors: string[] } {
  const cases: EvalCase[] = [];
  const errors: string[] = [];
  const raw = fs.readFileSync(path.join(CORPUS_DIR, file), "utf8");
  raw.split("\n").forEach((ln, i) => {
    const trimmed = ln.trim();
    if (!trimmed) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      errors.push(`${file}:${i + 1}: invalid JSON`);
      return;
    }
    const errs = schemaErrors(parsed, i + 1, file);
    if (errs.length) errors.push(...errs);
    else cases.push(parsed as EvalCase);
  });
  const seen = new Set<string>();
  for (const c of cases) {
    if (seen.has(c.id)) errors.push(`${file}: duplicate case id "${c.id}"`);
    seen.add(c.id);
  }
  return { cases, errors };
}

// ---------------------------------------------------------------------------
// Citation audit — exported pure function (belt-and-suspenders for CB-3)
// ---------------------------------------------------------------------------

export function normalizeUrl(u: string): string {
  // Host is case-insensitive; the PATH is not — lowercasing it would let a
  // case-variant link pass the allowlist against a differently-cased
  // citation. Normalize only what the URL spec treats as insensitive.
  try {
    const p = new URL(u.trim());
    return p.origin.toLowerCase() + p.pathname.replace(/\/+$/, "") + p.search;
  } catch {
    return u.trim().replace(/\/+$/, "");
  }
}

/** Every markdown link URL in the text must be a member of citations[].url.
 *  Returns the list of unverified URLs (empty = audit passes). */
export function unverifiedLinks(text: string, citations: Citation[]): string[] {
  const allowed = new Set(citations.map((c) => normalizeUrl(c.url)));
  const bad: string[] = [];
  const re = /\[[^\]]*\]\((https?:\/\/[^\s)]+)\)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (!allowed.has(normalizeUrl(m[1]))) bad.push(m[1]);
  }
  return bad;
}

// ---------------------------------------------------------------------------
// Assertion engine
// ---------------------------------------------------------------------------

export function assertCase(c: EvalCase, r: RecordedResponse): string[] {
  const failures: string[] = [];
  const text = r.response ?? "";
  const lower = text.toLowerCase();
  const e = c.expect;

  if (e.http_status !== undefined) {
    if (r.http_status !== e.http_status) {
      failures.push(`expected http_status ${e.http_status}, got ${r.http_status ?? "none"}`);
    }
    // A rejected request has no body worth asserting on.
    return failures;
  }
  if (e.must_contain_any && !e.must_contain_any.some((s) => lower.includes(s.toLowerCase()))) {
    failures.push(`response contains none of: ${e.must_contain_any.join(" | ")}`);
  }
  if (e.must_not_contain) {
    for (const s of e.must_not_contain) {
      if (lower.includes(s.toLowerCase())) failures.push(`response contains forbidden: "${s}"`);
    }
  }
  if (e.must_cite_web && !(r.citations && r.citations.length > 0)) {
    failures.push("expected web citations, got none");
  }
  const tools = (r.tool_calls ?? []).map((t) => t.toLowerCase());
  if (e.tools_any_of && !e.tools_any_of.some((t) => tools.includes(t.toLowerCase()))) {
    failures.push(`no expected tool called (wanted one of: ${e.tools_any_of.join(", ")})`);
  }
  if (e.tools_none_of) {
    for (const t of e.tools_none_of) {
      if (tools.includes(t.toLowerCase())) failures.push(`forbidden tool called: ${t}`);
    }
  }
  const bad = unverifiedLinks(text, r.citations ?? []);
  if (bad.length) failures.push(`unverified link(s) in prose: ${bad.join(", ")}`);
  return failures;
}

// ---------------------------------------------------------------------------
// CI mode — fixtures only, zero network
// ---------------------------------------------------------------------------

function loadFixtures(): Map<string, { fixtureName: string; rec: RecordedResponse }> {
  const map = new Map<string, { fixtureName: string; rec: RecordedResponse }>();
  if (!fs.existsSync(FIXTURE_DIR)) return map;
  for (const f of fs.readdirSync(FIXTURE_DIR).filter((f) => f.endsWith(".json")).sort()) {
    const rec = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, f), "utf8")) as RecordedResponse;
    const prior = map.get(rec.case_id);
    if (prior) {
      throw new Error(`fixture collision: ${prior.fixtureName} and ${f} both claim case_id "${rec.case_id}"`);
    }
    map.set(rec.case_id, { fixtureName: f, rec });
  }
  return map;
}

function runCi(cases: EvalCase[]): CaseResult[] {
  const fixtures = loadFixtures();
  if (fixtures.size === 0) {
    throw new Error("no fixtures found — an empty fixtures/ dir must not read as a passing suite");
  }
  const ids = new Set(cases.map((c) => c.id));
  for (const [caseId, { fixtureName }] of fixtures) {
    if (!ids.has(caseId)) {
      throw new Error(`orphan fixture ${fixtureName}: case_id "${caseId}" matches no corpus case (renamed id?)`);
    }
  }
  return cases.map((c) => {
    const fx = fixtures.get(c.id);
    if (!fx) return { id: c.id, outcome: "skipped" as const, failures: [], invertedControl: false };
    const failures = assertCase(c, fx.rec);
    const inverted = fx.fixtureName.startsWith("should_fail_");
    // Control fixtures PROVE the assertions bite: they pass the suite by failing it.
    const outcome = inverted ? (failures.length ? "pass" : "fail") : failures.length ? "fail" : "pass";
    return {
      id: c.id,
      outcome,
      failures: inverted && !failures.length ? ["control fixture did NOT trip any assertion — assertions are toothless"] : failures,
      invertedControl: inverted,
    };
  });
}

// ---------------------------------------------------------------------------
// Live mode — operator only
// ---------------------------------------------------------------------------

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Stay under the app's own 20 req/min per-IP limiter (CB-1) with headroom. */
const LIVE_PACING_MS = 3500;

function liveQuestion(q: string): string {
  // The oversized-abuse case ships as a placeholder so the corpus stays
  // readable; the live runner expands it to just over the 4,000-char cap.
  return q.includes("OVERSIZED_MESSAGE_PLACEHOLDER") ? "x".repeat(4001) : q;
}

async function runLive(cases: EvalCase[]): Promise<CaseResult[]> {
  const target = process.env.EVAL_TARGET_URL;
  if (!target || process.env.EVAL_LIVE_CONFIRM !== "yes") {
    console.error(
      "REFUSED: --live requires EVAL_TARGET_URL and EVAL_LIVE_CONFIRM=yes.\n" +
        "Live evals are operator-only; automated runs must use the default (CI) mode."
    );
    process.exit(2);
  }

  async function post(caseId: string, message: string, modes: EvalCase["modes"]): Promise<Response> {
    let res = await fetch(`${target}/api/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message, sessionId: `eval-${caseId}`, modes: modes ?? {} }),
    });
    if (res.status === 429) {
      const retryAfter = Number(res.headers.get("retry-after")) || 65;
      console.log(`  (rate limited; waiting ${retryAfter}s and retrying once)`);
      await sleep(retryAfter * 1000);
      res = await fetch(`${target}/api/chat`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message, sessionId: `eval-${caseId}`, modes: modes ?? {} }),
      });
    }
    return res;
  }

  const results: CaseResult[] = [];
  for (const c of cases) {
    // Setup turns give follow-up cases real conversation context in the
    // same session; their responses are not graded.
    for (const s of c.setup ?? []) {
      await post(c.id, s, c.modes);
      await sleep(LIVE_PACING_MS);
    }
    const res = await post(c.id, liveQuestion(c.question), c.modes);
    let rec: RecordedResponse;
    if (res.ok) {
      const body = (await res.json()) as {
        response?: string;
        citations?: Citation[];
        trace?: Array<{ label?: string; tool?: string }>;
      };
      rec = {
        case_id: c.id,
        http_status: res.status,
        response: body.response ?? "",
        citations: body.citations ?? [],
        tool_calls: (body.trace ?? []).map((t) => t.tool ?? t.label ?? "").filter(Boolean),
      };
    } else {
      rec = { case_id: c.id, http_status: res.status, response: "" };
    }
    const failures = assertCase(c, rec);
    results.push({ id: c.id, outcome: failures.length ? "fail" : "pass", failures, invertedControl: false });
    await sleep(LIVE_PACING_MS);
  }
  return results;
}

// ---------------------------------------------------------------------------
// Self-test — engine sanity on built-in mini-fixtures
// ---------------------------------------------------------------------------

function selfTest(): number {
  let failed = 0;
  const check = (name: string, ok: boolean) => {
    console.log(`${ok ? "ok" : "FAIL"} - ${name}`);
    if (!ok) failed++;
  };

  check("contain_any passes on match", assertCase(
    { id: "t", category: "t", question: "q", expect: { must_contain_any: ["couldn't find"] } },
    { case_id: "t", response: "I couldn't find that race." }
  ).length === 0);

  check("must_not_contain trips", assertCase(
    { id: "t", category: "t", question: "q", expect: { must_not_contain: ["SELECT "] } },
    { case_id: "t", response: "Sure: SELECT * FROM selections;" }
  ).length === 1);

  check("unverified link trips", unverifiedLinks(
    "See [tips](https://evil.example/bet)", [{ url: "https://racing.com/news" }]
  ).length === 1);

  check("verified link passes (trailing slash + host case normalized)", unverifiedLinks(
    "See [news](https://Racing.com/news/)", [{ url: "https://racing.com/news" }]
  ).length === 0);

  check("path case mismatch trips (paths are case-sensitive)", unverifiedLinks(
    "See [x](https://ok.com/Path)", [{ url: "https://ok.com/path" }]
  ).length === 1);

  check("schema rejects unknown top-level key", schemaErrors(
    { id: "x", category: "c", question: "q", expect: {}, bogus_top: 1 }, 1, "t"
  ).length === 1);

  check("schema accepts setup array, rejects non-array", (() => {
    const ok = schemaErrors({ id: "x", category: "c", question: "q", setup: ["a"], expect: {} }, 1, "t").length === 0;
    const bad = schemaErrors({ id: "x", category: "c", question: "q", setup: "a", expect: {} }, 1, "t").length === 1;
    return ok && bad;
  })());

  check("must_cite_web trips on empty citations", assertCase(
    { id: "t", category: "t", question: "q", expect: { must_cite_web: true } },
    { case_id: "t", response: "answer", citations: [] }
  ).length === 1);

  check("tools_any_of trips when tool absent", assertCase(
    { id: "t", category: "t", question: "q", expect: { tools_any_of: ["lookup_horse"] } },
    { case_id: "t", response: "answer", tool_calls: ["get_race_card"] }
  ).length === 1);

  check("http_status assertion", assertCase(
    { id: "t", category: "t", question: "q", expect: { http_status: 400 } },
    { case_id: "t", http_status: 400, response: "" }
  ).length === 0);

  check("schema rejects unknown expect key", schemaErrors(
    { id: "x", category: "c", question: "q", expect: { bogus: 1 } }, 1, "t"
  ).length === 1);

  for (const f of ["golden.jsonl", "injection.jsonl"]) {
    const { errors } = loadCorpus(f);
    check(`${f} schema-valid`, errors.length === 0);
    for (const e of errors) console.log(`    ${e}`);
  }
  return failed;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  const args = process.argv.slice(2);
  if (args.includes("--self-test")) {
    const failed = selfTest();
    console.log(failed === 0 ? "\nSELF-TEST: all green" : `\nSELF-TEST: ${failed} failure(s)`);
    process.exit(failed === 0 ? 0 : 1);
  }

  const golden = loadCorpus("golden.jsonl");
  const injection = loadCorpus("injection.jsonl");
  const schemaProblems = [...golden.errors, ...injection.errors];
  if (schemaProblems.length) {
    console.error("CORPUS SCHEMA ERRORS:");
    schemaProblems.forEach((e) => console.error("  " + e));
    process.exit(1);
  }
  const cases = [...golden.cases, ...injection.cases];

  const results = args.includes("--live") ? await runLive(cases) : runCi(cases);

  const pass = results.filter((r) => r.outcome === "pass");
  const fail = results.filter((r) => r.outcome === "fail");
  const skipped = results.filter((r) => r.outcome === "skipped");
  for (const r of fail) {
    console.log(`FAIL ${r.id}${r.invertedControl ? " [control]" : ""}`);
    r.failures.forEach((f) => console.log(`     - ${f}`));
  }
  console.log(
    `\n${args.includes("--live") ? "LIVE" : "CI"} RESULT: ` +
      `${pass.length} pass (${pass.filter((r) => r.invertedControl).length} inverted controls), ` +
      `${fail.length} fail, ${skipped.length} skipped (no fixture), ${cases.length} total cases`
  );
  process.exit(fail.length ? 1 : 0);
}

main();
