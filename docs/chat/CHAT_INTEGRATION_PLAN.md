# STRIDE chatbot integration — plan

Date: 2026-09-10; status updated 2026-09-11 (§13). Phase 0 is built and
phase 1 is built and waiting on the operator's APPLY. Supersedes nothing; it
sits between the audit at
[`CHAT_LAMBDA_ARCHITECTURE_AUDIT.md`](CHAT_LAMBDA_ARCHITECTURE_AUDIT.md)
(2026-09-03) and the product plan at [`../../PLAN.md`](../../PLAN.md)
("Stride — Backend Plan v4"), and reconciles them.

Every claim below names the file it was read from. `stride-racing` is read
at `1c68d9f` (= `origin/main` today). `stride-app` is a separate private
repository, read at `810e7c1`, which is still the head of its default
branch — so the audit's reading of it is current, and I re-verified each
of its load-bearing claims rather than inheriting them.

---

## 0. Verdict

**There is no chatbot to integrate into this repository. There is a
chatbot in another repository whose data plane has gone stale, and two
incompatible plans for what to do about it.**

The reconciliation is that both plans need the same thing first, and
neither can proceed past it without a decision that is not an engineering
decision. Concretely:

1. A chat exists — about 300 KB of TypeScript in `stride-app` behind
   `POST /api/chat` — and it has never had tool use. Its own eval suite
   describes an agent with five named tools that was never built.
2. `PLAN.md` v4 describes a different product: multi-tenant, bring-your-own
   Punting Form key, KMS envelope encryption, OIDC, a block-based answer
   contract, SSE, briefings, watchlists and shares. None of that exists.
   There is no Python HTTP service in this repository at all.
3. The audit and `PLAN.md` v4 agree on exactly one layer: a set of typed
   tool functions over Neon, the S3 artifact relay and Punting Form. They
   disagree on everything above it.
4. That one shared layer is also the only part that can be built today
   without answering the audience question or clearing the Punting Form
   terms-of-service gate that `PLAN.md` §15 itself calls "now the biggest
   gate".

**So: build the tool layer first, as a transport-agnostic library. Put the
compute, auth and tenancy fork behind an explicit decision point.** The
tool layer is a strict subset of both futures, so nothing built in phase 0
is wasted whichever way the fork goes.

---

## 1. What exists today, verified

### 1.1 Three codebases, not one

| | `stride-racing` (this repo, public) | `stride-app` (private) |
|---|---|---|
| Owns | Python pipeline, ML ensemble, AWS estate, Neon writes | React client, Express server, the chat, Drizzle schema |
| Chat code | none | `chatOrchestrator.ts`, `strideChatRetrieval.ts`, `stridePrompts.ts`, `strideChatService.ts` |
| Relationship | writes Neon and S3 | reads the same Neon; reads the Mac's local files |

`.gitignore` in this repository excludes `client/`, `package*.json`,
`tsconfig.json` and `drizzle.config.ts` under the heading "Not part of
this repo (frontend / Node / TS server)". The split is deliberate and
predates this work.

### 1.2 The chat in `stride-app`, as it actually is

Re-verified at `810e7c1`:

| Claim | Evidence | Holds? |
|---|---|---|
| No tool use anywhere | three `anthropic.messages.create` calls (`chatOrchestrator.ts:627`, `:1881`, `:2235`), none passing `tools` | yes |
| Model default is retired | `chatOrchestrator.ts:40` falls back to `claude-sonnet-4-20250514` | yes |
| `temperature` blocks any model upgrade | `chatOrchestrator.ts:630` (`0`), `:1884` (`0.2`), `:2238` (`0.2`) | yes |
| SDK predates adaptive thinking | `package.json:15`, `@anthropic-ai/sdk ^0.37.0` | yes |
| The agent is a spec with tests | `STRIDE_CHAT_AGENT` appears once in the whole repo, in `evals/chat/README.md:29` | yes |
| Eval suite fixes the tool surface | 30 golden cases, 16 injection cases; `get_stride_tips`, `get_race_card`, `lookup_horse`, `query_results`, `get_performance` | yes |
| No CI | no `.github` directory | yes |
| Rate limiting only | `server/index.ts:38` `chatLimiter`, `:46` `globalLimiter`; no auth on any chat route | yes |

Two things the audit gets slightly wrong, both of which change scope —
see §8.

### 1.3 The pipeline side, as it actually is

- **No Python HTTP service.** `fastapi`, `uvicorn` and `flask` appear
  nowhere in `server/python/`, `requirements.txt` or `pyproject.toml`.
  `mc_api.py` is a stdin/stdout JSON wrapper called by Express through
  `child_process`, not a server.
- **The artifact relay is the fresh copy of everything the chat reads
  locally.** `infra/jobs/handler.py` defines `ARTIFACTS_PREFIX = "artifacts"`
  and relays through `s3://$STRIDE_EVIDENCE_BUCKET/artifacts/<repo-relative
  path>/<file>`:

  | Artifact | S3 key |
  |---|---|
  | tips | `artifacts/racecards/tips_<date>.json` |
  | racecards | `artifacts/server/python/racecards/racecard_<date>.json` |
  | consensus, market signals | `artifacts/server/python/intelligence/consensus_<date>.json`, `market_signals_<date>.json` |

- **The joins the model must not write itself already exist**:
  `identity_normalization.py` (`normalize_runner_key`, `normalize_track_key`,
  `canonical_race_key`), `result_margins.py` (`beaten_margin` — the only
  correct reader of the two margin conventions in
  `race_results_history.margin_lengths`, per `docs/03-data-and-ingestion.md`).
- **Punting Form access is already wrapped.** `pf_client.py` exposes
  `meetings_for_date`, `results_for_meeting`, `meeting_detail`,
  `scratchings`, `conditions`, `speedmaps_for_meeting`,
  `ratings_for_meeting`, `strike_rates`, and raises `PFAuthError` on
  401/403 rather than returning empty.
- **The subscription serves about 31 days.** `pf_window.py` measures the
  wall at runtime. Access lapsed on 2026-09-03 and was renewed the same
  day; `puntingform-probe` now checks it daily at 03:15 AEST.
- **`ANTHROPIC_CHAT_MODEL` is already provisioned and read by nothing.**
  It is in the `KEYS` list at `infra/01_secrets.sh:20` and passed from
  `deploy-infra.yml:60`. No code in either repository reads it.

### 1.4 The two plans

| | `PLAN.md` v4 | The audit |
|---|---|---|
| Audience | many users, authenticated | the operator only |
| Punting Form key | each user's own, plus a shared pool key | the estate's one key |
| Compute | shared FastAPI service | slim Python 3.12 zip Lambda + Function URL |
| Auth | OIDC/JWT, KMS envelope encryption per user | bearer token from Express |
| Answer contract | typed blocks + provenance + follow-ups | existing `ChatCompletionResponse` |
| Transport | SSE with resumable replay | whole response at end of turn |
| Blocked by | Punting Form commercial clearance (§15 risk 1) | nothing |
| Exists today | nothing | nothing |

These are not two designs for one system. They are two products. v4 is a
public product; the audit describes the operator's own tool moved onto
supported infrastructure.

---

## 2. The decision

**Build the shared layer now. Fork later, explicitly.**

The shared layer is the typed tool functions plus the loop and prompt that
drive them. It is identical under both futures because it is defined by
the data, not by the audience: `get_stride_tips` reads the same tips
artifact whether one person or a thousand asked for it.

Everything above that layer forks on two questions the operator owns:

- **Audience.** Operator-only, or public? This decides compute, auth and
  tenancy in one answer.
- **Punting Form terms.** `PLAN.md` §15 risk 1 already states that serving
  many users through one Stride-owned key "is very likely redistribution".
  The audit adds that the Starter plan is licensed for personal use
  (`PUNTINGFORM_MIGRATION.md`). Until that is cleared in writing, Mode B of
  v4 cannot ship, and neither can any public form of the chat that serves
  Punting Form data.

Recommendation: **Fork A (operator-only) now.** It has no legal gate, it
restores a capability that is currently rotting, and its data plane is a
strict subset of Fork B's. Fork B stays live as a design, and phase 0 is
built so that adopting it later is a new transport over the same tools,
not a rewrite.

---

## 3. Target architecture (Fork A)

```
  browser ──► Express (stride-app, the Mac)
              /api/chat  ·  rate limiter  ·  chat_feedback writes
                 │
                 │  bearer token, same contract
                 ▼
        Lambda Function URL  (stride-racing, python3.12 zip)
                 │
        ┌────────┴─────────────────────────────────┐
        │  chat.loop — Anthropic tool loop          │
        │  chat.prompt — v3.0, ported from v2.2     │
        └────────┬─────────────────────────────────┘
                 │  typed tools only, no free-text SQL
     ┌───────────┼───────────────┬──────────────────┐
     ▼           ▼               ▼                  ▼
  Neon (ro)  S3 artifacts   Punting Form      DynamoDB
  pooled     artifacts/*    via pf_client     sessions + daily cap
```

Fixed points, from the audit and unchanged here:

- **Function URL, not API Gateway.** HTTP API caps integrations at 30
  seconds and cannot be raised; a two-round tool turn exceeds it.
- **A zip on the managed runtime, not the `stride-jobs` image.** That image
  carries the full ML stack and Playwright and pulls pandas at import
  (`intelligence/common.py`). Wrong artifact for an interactive endpoint.
  This is a deliberate deviation from the one-image rule in
  `infra/04_ecr_image.sh`; the AWS Change Protocol asks the reason be
  recorded, and the reason is isolation plus cold-start latency.
- **Not VPC-attached.** Matches every existing Lambda. A NAT Gateway is
  roughly double the account's US$20 tripwire.
- **Express stays in front.** A public Function URL with `AuthType=NONE`
  and no other check lets anyone spend the Anthropic key.
- **Sizing:** 1024 MB, 180 s timeout, reserved concurrency 3. Reserved
  concurrency is also the spend cap.

---

## 4. Tool surface

Named as the evals name them, because the evals are the acceptance test.
Built on the existing helpers so the model never writes a join.

| Tool | Reads | Notes |
|---|---|---|
| `get_stride_tips(date, track?, race?)` | `artifacts/racecards/tips_<date>.json`, falling back to `selections` | The artifact carries the full decision contract (`bet_pick`, `coverage_pick`, `bet_status`, `convergence_tier`, `full_field`); `selections` holds only `should_bet` rows |
| `get_race_card(date, track, race?)` | `artifacts/server/python/racecards/racecard_<date>.json`, else `pf_client.meeting_detail` | Replaces the dead Racing API client |
| `lookup_horse(name, n=10)` | `race_results_history`, `sectional_times`, `franking_scores` via `normalize_runner_key` | Blackbook handled separately — see §8.1 |
| `query_results(date, track?, race?)` | `race_results_history`, `prediction_audit`, `pf_client.results_for_meeting` for unsettled days | Margins only through `result_margins.beaten_margin` |
| `get_performance(window, group_by)` | `selection_ledger`, `stride_tip_results`, `selection_results` | Net of commission, per the ledger |
| `get_consensus(date, track, race)` | `artifacts/.../consensus_<date>.json`, `consensus_scores` | Read only. The consensus agent itself is untouchable under `CLAUDE.md` |
| `get_market_signals(date, track, race)` | `artifacts/.../market_signals_<date>.json`, `betfair_odds_snapshots` | |
| `puntingform(endpoint, ...)` | `pf_client` functions behind an `endpoint` enum | Never raw REST; `pf_client` owns envelope errors, `PFAuthError`, retries and pacing. Add a short in-memory TTL cache |
| `run_readonly_sql(sql)` | optional, **off by default** | Single `SELECT`/`WITH`, wrapped in `SELECT * FROM (...) q LIMIT 200`, allowlisted tables. An operator escape hatch, not a default tool |

**No free-text SQL tool by default.** It contradicts the chat's own
injection suite (`inj-sql-01`, `inj-sql-02` require the model never emit
SQL or table names), and it hands the model joins this repository already
solves. `race_date` is TEXT in `race_results_history` and `sectional_times`,
runner keys carry country suffixes, track aliases are many, and the
winner's margin is stored under two conventions.

**Tool results are untrusted input.** Wrap every result in a delimited
block and say so in the system prompt. Punting Form text and database text
never sit in the instruction position. `inj-data-01` — a horse called
"Ignore Previous Instructions" — is the test that this holds.

**Punting Form's 31-day wall is an answer, not an error.** Asked for
something older, the tool says the subscription does not serve it. That is
the honest-miss behaviour `miss-01` to `miss-04` require.

---

## 5. Contracts

**Keep `ChatCompletionRequest` and `ChatCompletionResponse` from
`stride-app/shared/schema.ts` unchanged.** In: `message`, `sessionId`,
`turnId`, `modes`, `raceContext` (`schema.ts:845-863`). Out: `response`,
`mode`, `answerSource`, `trace`, `citations`, `warnings`, `turnId`,
`promptVersion` (`schema.ts:911`). `ChatInterface.tsx` and
`ChatTracePanel.tsx` then need no change.

The tool loop maps onto the existing trace cleanly: tools called become
`steps`, tool provenance becomes `sources`, and `citations[]` can name
Punting Form endpoints and S3 keys as well as URLs. `inj-link-01` and
`inj-link-02` require that no link appears in prose that is not in
`citations[]`.

**The bridge to v4.** Add `blocks?: AnswerBlock[]` as an *optional*
additive field. The composer that fills it is a second renderer over the
same tool results, not a second agent. Old clients ignore it; a v4 client
reads it and ignores `response`. This is what keeps Fork A from becoming
throwaway work if the operator later chooses Fork B.

**Sessions.** The API is stateless and Lambda containers are disposable, so
the in-memory maps in `strideChatService.ts` cannot move. Store the last 12
messages per `sessionId` in DynamoDB with a 30-minute TTL — the constants
the TypeScript already uses. DynamoDB is already in the estate.

**API usage.** Read the model id from `ANTHROPIC_CHAT_MODEL`, defaulting to
`claude-opus-5`. Preflight it at cold start with a small call the way
`consensus_agent.preflight_extraction_model()` does — a dead model id is
loud, but a rejected parameter after a model change is exactly how the
consensus agent went silent for six weeks. Adaptive thinking on.
`max_tokens` at least 8,000. Do not pass `temperature`. Cache the system
prompt and tool definitions as the stable prefix. Return every
`tool_result` for parallel tool calls in one user message; a failed tool
returns `is_error: true` rather than being dropped. Cap the loop at eight
rounds and handle `stop_reason` of `max_tokens` and `refusal` explicitly.

---

## 6. Phases

Each phase states what proves it, not what it produces. A 200 with prose
is not evidence.

### Phase 0 — the tool library. No AWS, no fork.

Create `server/python/chat/` in this repository:

```
chat/
  tools/          typed tool functions, transport-agnostic
  artifacts.py    S3 reader with local-directory fallback
  loop.py         the Anthropic tool loop
  prompt.py       v2.2 + 16 track profiles ported from stridePrompts.ts, bumped to v3.0
  cli.py          python -m chat.cli, for local runs and the eval harness
  tests/          offline replay of recorded tool results
```

Nothing here imports boto3 at module scope or assumes Lambda. The tool
functions take plain arguments and return plain dicts, so the same
functions are importable by a Lambda handler, a FastAPI app, a CLI, or a
stdio MCP server (§9).

**Exit:** the 30 golden and 16 injection cases green against the CLI, run
locally against a read-only Neon URL, with the operator's approval for
that read. Offline tests green in CI with no credentials.

### Phase 1 — the read-only role and the eval runner.

The role, applied through the `apply-migration` workflow with the
operator's `APPLY`:

```sql
CREATE ROLE stride_chat_ro LOGIN PASSWORD '<from the password manager>';
GRANT pg_read_all_data TO stride_chat_ro;
ALTER ROLE stride_chat_ro SET default_transaction_read_only = on;
ALTER ROLE stride_chat_ro SET statement_timeout = '15s';
ALTER ROLE stride_chat_ro SET idle_in_transaction_session_timeout = '10s';
```

Use Neon's **pooled** host for this role. The batch jobs are single
processes and never approach Neon's connection limit; a Lambda with
several warm containers will. Keep `sslmode=require` and
`channel_binding=require` as `.env.example` has them — `stride-app`'s
`db.ts` sets `rejectUnauthorized: false` and that must not be carried over.

`chat_feedback` writes stay with the Express app and its existing role.

**Exit:** a test proves the role cannot write — an `INSERT` against it
raises — and the eval suites pass through it.

### Phase 2 — the fork decision. **Blocking, operator-owned.**

Answer §11. Do not build AWS resources before this.

### Phase 3A — AWS, additive (operator-only path).

`infra/10_chat_lambda.sh`, idempotent like its siblings, creating: the
execution role, the three `stride/chat/*` secrets, the function, the
Function URL, the log group (created before retention is set) and the
alarms. Wired into `deploy-infra` behind a `deploy_chat` input.

IAM, scoped and nothing more:

| Grant | Resource |
|---|---|
| `secretsmanager:GetSecretValue` | the three `stride/chat/*` ARNs |
| `s3:GetObject` | `arn:aws:s3:::stride-evidence-<account>/artifacts/*` |
| `dynamodb:GetItem`, `PutItem`, `UpdateItem` | the one new table ARN |
| `logs:*` | via `AWSLambdaBasicExecutionRole` |

No access to `stride/prod`, the models bucket, or `stride_run_state`. The
chat is the one component that will face untrusted input; `stride/prod`
carries Betfair credentials and the write database role.

Issue the chat **its own Anthropic key** from a separate Console workspace,
with its own spend limit, revocable without stopping the 05:30 consensus
job. This is the cheapest real isolation available.

Alarms on `Errors`, `Throttles` and `Duration` p95 near the timeout, all to
`stride-alerts`; 60-day log retention. **No dead-letter queue and no async
retries** — Function URL invocations are synchronous and a retry doubles
the spend on a turn the user already saw fail.

**Exit — `chat-proof`, in the `verify-jobs` style.** A canned question that
must produce *at least one database tool call that returned rows* and *one
Punting Form call that returned a non-empty payload*, asserted on the tool
results, not on the prose. Run from `deploy-infra` after the function
updates. Ask of this check what `CLAUDE.md` says to ask: what would it
still pass with? A chat that answers fluently from the prompt alone and
calls nothing must fail it.

### Phase 4A — the app.

`STRIDE_CHAT_BACKEND=lambda` routes `/api/chat` to the Function URL for
default and brain modes; `modes.search = true` stays on the TypeScript path
until search is ported. Feedback stays local. Remove `racingApiClient.ts`.
Fix the model default, the three `temperature` arguments and the SDK
version (§8.2 for what this does *not* fix).

**Exit:** live evals green through the UI.

### Phase 5 — later, each its own decision.

Streaming through the Lambda Web Adapter and server-sent events; the
search-mode port; the `blocks` renderer; retiring the TypeScript
orchestrator; static hosting behind CloudFront.

---

## 7. Guardrails

| Guardrail | Value | Why |
|---|---|---|
| Reserved concurrency | 3 | bounds parallel Anthropic calls and Neon connections; also the spend cap |
| Daily turn cap | DynamoDB counter | the `DAILY_CLAUDE_CAP` pattern from `consensus_agent.py`; past it, answer "budget exhausted" without calling Anthropic |
| Tool loop | 8 rounds | |
| `statement_timeout` | 15 s on the role | |
| Result size cap | per tool | a 130,000-row table must not reach the context |
| Tool output framing | delimited, declared as data | `inj-data-01` |
| Secret refetch | once, on 401 from Anthropic or Punting Form | a rotation should not need a redeploy |

Log `usage` (input, output, cache read and write tokens) per turn as one
structured line. Low-cardinality metrics only: turns, tool errors,
Anthropic 4xx and 5xx. Never a per-race or per-horse dimension.

---

## 8. Corrections to the audit

The audit holds up. Three details change scope.

### 8.1 `blackbook_entries` exists, but this repository does not own it

The audit lists `blackbook_entries` as a source for `lookup_horse`. It is
not a `stride-racing` table: it is declared in
`stride-app/shared/schema.ts:678` (with `blackbook_entry_runs` at `:725`
and `blackbook_alerts` at `:759`) and created by raw DDL at runtime in
`stride-app/server/blackbook.ts`. What this repository has is
`blackbook_candidates.py`, which writes
`racecards/blackbook_candidates_<date>.json` — a per-day file, not a
history.

Two golden cases depend on this: `horse-03` ("Is Amelia's Jewel in our
blackbook? Why?") and `chain-04` ("Of the horses we blackbooked in March
2026, which have raced since?"). The second needs history.

So `lookup_horse` reads a table whose schema is owned by the other
repository's Drizzle model and mutated by its runtime DDL. That is a
cross-repo coupling the audit does not name, and it is the one place where
the Python chat is exposed to a schema change made in TypeScript. Either
pin the columns it reads and test them, or move blackbook ownership — a
decision, not a detail.

### 8.2 The four hardcoded model ids are not on the chat path

The audit lists `routes.ts:781`, `:972`, `:1099` and `:1241` under the
chat's retired-model problem. They are real and they are dead, but they sit
in four *other* endpoints:

| Line | Endpoint |
|---|---|
| 781 | `/api/ask-stride` (`routes.ts:747`) |
| 972 | `/api/stride-analyst` (`:875`) |
| 1099 | `/api/stride-analyst/generate-insights` (`:1014`) |
| 1241 | `/api/generate-all-insights` (`:1190`) |

Porting the chat does not fix them. They are a separate, smaller piece of
work in `stride-app`, and they should be tracked separately rather than
absorbed into the chat port's definition of done. `server/runnerAnalysis.ts`
carries three more `temperature` arguments (`:1776`, `:2323`, `:2427`) with
the same upgrade-blocking property.

### 8.3 There are seven chat routes, not one

`/api/chat` (`routes.ts:550`) plus `/api/chat/reason` (`:418`),
`/respond` (`:456`), `/with-race` (`:505`), `/complete` (`:610`),
`/clear` (`:629`) and `/feedback` (`:635`). "Express proxies `/api/chat`"
understates phase 4A. Each route needs a decision: proxy, retire, or leave
on the TypeScript path. `/clear` and `/feedback` clearly stay local.

---

## 9. On MCP

Keep the tool functions transport-agnostic under `server/python/chat/tools/`
and a stdio MCP server exposing the same functions to Claude Code or Claude
Desktop is nearly free — a useful local operator surface.

For the production chat it is the wrong shape: a remote MCP server is
another hosted, authenticated service, and the Messages API MCP connector
needs a publicly reachable URL. In-process tools in one Lambda are simpler,
cheaper and testable offline.

---

## 10. Cost

The Anthropic side dominates and is the reason the separate key and the
daily cap matter more than any AWS line item.

One worked two-round turn on `claude-opus-5` (US$5/US$25 per million), with
a 15,000-token cached prefix, 5,000 fresh input tokens and 3,500 output
plus thinking tokens:

| Component | Tokens | Cost |
|---|---:|---:|
| cached prefix read | 15,000 | $0.008 |
| fresh input | 5,000 | $0.025 |
| output and thinking | 3,500 | $0.088 |
| **total** | | **~$0.12** |

At 50 turns a day that is roughly US$180 a month. The AWS side at that
volume is under US$2 a month: three secrets at US$0.40 each, and negligible
Lambda, DynamoDB and log charges. The account's budget tripwire is US$20 a
month and the Free Plan period ends 2027-02-01.

---

## 11. Decisions the operator owns

Phase 2 cannot start without 1 and 2.

1. **Audience.** Operator-only (Fork A, no legal gate, buildable now), or
   public (Fork B, blocked on 2)? This one answer fixes compute, auth and
   tenancy.
2. **Punting Form terms.** Is there written commercial clearance for
   serving Punting Form data to anyone but the operator? `PLAN.md` §15
   already flags this as the biggest gate, and it covers the pipeline key
   and derived predictions too, not just the shared pool key.
3. **Model.** `claude-opus-5` for the chat (recommended, measured per turn),
   or `claude-sonnet-5` as the consensus agent uses.
4. **Streaming.** Now or later? Recommendation: later — the UI already
   animates its own progress from `client/src/lib/chatModes.ts`.
5. **Search mode.** Port Perplexity search and citation verification to
   Python, or keep it on the TypeScript path indefinitely?
6. **Blackbook ownership** (§8.1). Pin and test the columns, or move the
   table to this repository?

---

## 12. What this plan does not do

- It does not touch the consensus agent, convergence tier logic or franking
  thresholds. The chat reads their outputs and never writes them.
- It does not edit `infra/*.sh`. `10_chat_lambda.sh` is a new file;
  `06_schedules.sh` and `07b_fargate_schedules.sh` are not opened.
- It does not retrain or promote anything.
- It does not build `PLAN.md` v4's multi-tenant surface — no OIDC, no KMS
  key service, no briefings, watchlists or shares. Those stay a design
  until questions 1 and 2 are answered.
- It does not weaken any eval to pass. The 30 golden and 16 injection cases
  are the acceptance test as written; a case that fails is a defect in the
  agent, not in the case.

---

## 13. Status, 2026-09-11

Steps 1 and 2 of the execution order (plan §6, phases 0 and 1) are built on
branch `claude/peaceful-edison-62mpc1`. Nothing has touched AWS, Neon or
`stride-app`.

**Phase 0, built.** `server/python/chat/` holds the tool library: the eight
typed tools of §4 plus the off-by-default SQL escape hatch, the read-only
database layer, the artifact reader (S3 relay first, local checkout second),
the Punting Form facade with the 31-day wall, the tool loop, prompt v3.0,
the request and response contract, session memory, a CLI, and the eval
runner with the 46 cases and 10 fixtures vendored from `stride-app` at
`810e7c1`. 78 offline tests run under `python -m pytest server/python`
with no credential, no network and none of `anthropic`, `boto3` or
`pandas` imported at module scope. The offline eval suite reproduces the
TypeScript runner's result: 10 pass (3 inverted controls), 36 live-only.

**Phase 0 exit, not yet met.** The exit is the golden and injection suites
green *live* against the CLI over a read-only Neon URL. That needs the role
from phase 1 applied, `STRIDE_CHAT_DATABASE_URL`, an `ANTHROPIC_API_KEY`
and the operator's `EVAL_LIVE_CONFIRM=yes`; it costs tokens and is the
operator's call. Run: `python -m chat.eval_runner --live-cli`. The `web-*`
and `inj-page-*` cases report `unsupported` until search is ported (§6,
phase 5); they are not counted as passes.

**Phase 1, built, waiting on APPLY.** `migrations/chat_readonly_role.sql`
creates `stride_chat_ro` with `pg_read_all_data` and nothing else. The
password never enters the repository: the file carries a token that
`apply-migration.yml` replaces from a new repository secret,
`STRIDE_CHAT_RO_PASSWORD`, at execution time. The same workflow run then
connects as the new role and runs
`server/python/chat/verify_readonly_role.py`, which fails the job unless
every INSERT, UPDATE, DELETE and CREATE TABLE is refused for lack of
privilege inside an explicitly READ WRITE transaction, so the role's
read-only session default cannot pass for the grant. To apply: set the
secret (20+ random characters), dispatch `apply-migration` with
`migration=chat_readonly_role.sql` and `confirm=APPLY`, and read the proof
step's output.

**Corrections to this plan found while building.** `lookup_horse` reads
`blackbook_entries` and `blackbook_entry_runs` with the columns pinned in
`chat/tools/horse.py` and covered by a test; when the tables are absent
the tool says so rather than failing the lookup (§8.1, first option, taken
for now). The eval harness's live mode reads tool names from a `trace`
array that the response contract does not have; the response therefore
carries an additive `toolCalls` field (§5) and the Python runner reads
that. Phase 4A's Express proxy must forward it.
