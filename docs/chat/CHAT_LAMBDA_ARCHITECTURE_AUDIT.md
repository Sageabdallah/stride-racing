# Chat backend on AWS: architecture audit

Date: 2026-09-03. Scope: the proposed "chat Lambda" plan (Secrets Manager,
IAM, Lambda behind API Gateway, no VPC, a two-tool Claude agent) audited
against what STRIDE already runs. Read-only: no AWS state, no database, and
no application code was changed to produce this. Every claim below names the
file it was read from; the `stride-app` references are to that repository at
commit `810e7c1` (2026-08-04).

## 0. Verdict

The AWS mechanics in the plan are mostly right and match the estate: a
dedicated execution role, secrets scoped by ARN, secrets cached across warm
invocations, no VPC attachment, alarms to the existing SNS topic. Keep those.

The plan is wrong about its own starting point. It reads as if no chatbot
exists and proposes a fresh two-tool agent (`query_puntingform`,
`query_stride_db(sql)`). A chatbot exists: about 300 KB of TypeScript in
`stride-app` behind `POST /api/chat`, with a request and response contract
the React client depends on, twenty-one question intents, twenty evidence
sources, a versioned prompt, and an eval suite whose golden cases already
name the tool surface the agent is supposed to have (`get_stride_tips`,
`get_race_card`, `lookup_horse`, `query_results`, `get_performance`). Built
as written, the plan would ship a second, thinner chat next to the first one
and fail its own evals on tool names.

The right framing is a port, not a rewrite: move the chat's data plane to
where STRIDE's data now lives (Neon plus the S3 artifact relay plus Punting
Form), keep the browser contract and the eval suite, and change compute from
"a Lambda behind API Gateway" to "a slim Lambda behind a Function URL". The
reasons are in section 2, the decisions in section 3, and a phased path that
keeps the existing chat working throughout in section 5.

Four things in the plan have to change before any AWS resource is created:

1. The compute target. API Gateway HTTP API has a hard 30-second integration
   timeout. A tool loop that calls Claude, then Punting Form, then Neon, then
   Claude again will exceed it. Use a Lambda Function URL.
2. The tool surface. A free-text SQL tool contradicts the chat's own
   injection suite and hands the model joins the repository already solves
   in `identity_normalization.py` and `result_margins.py`. Use typed tools
   with the names the evals use.
3. The caller. "React frontend to API Gateway to Lambda" bypasses the
   Express server that serves the React client, holds the rate limiter, and
   writes chat feedback. Route the browser through Express and have Express
   call the Function URL with a bearer token.
4. The image. The estate's one container image carries the full ML stack and
   Playwright. It is the wrong artifact for an interactive endpoint. Package
   the chat separately.

## 1. What exists today

### 1.1 The chat in `stride-app`

| Piece | Where | State |
|---|---|---|
| UI | `client/src/pages/ChatInterface.tsx`, `components/chat/ChatTracePanel.tsx` | Posts JSON to `/api/chat` and renders `ChatCompletionResponse`. Not streaming. The visible "thinking stream" is client-side canned text from `client/src/lib/chatModes.ts` (`getDeepThoughtLoadingStream`, `getSearchThoughtStream`). |
| Server | `server/index.ts`, `server/routes.ts` | Express on the operator's Mac. `express-rate-limit`: 20 requests per minute on the chat routes, 120 on `/api`. Sentry. No authentication on any chat route (a `users` table and passport are present but unused by routes). |
| Orchestrator | `server/chatOrchestrator.ts` | Modes `default`, `brain`, `search`. Conceptual answers without an LLM (`strideConceptualChat.ts`), local retrieval, optional Perplexity search, Claude synthesis through JSON contracts at three call sites (lines 627, 1881, 2235). No tool use anywhere. |
| Retrieval | `server/strideChatRetrieval.ts` | 21 intents, 20 evidence sources. Neon through drizzle typed queries and parameterised `db.execute(sql...)`. Local JSON from `racecards/` and `server/python/intelligence/` on the Mac's filesystem (lines 253 and 254). |
| Prompts | `server/stridePrompts.ts` | `STRIDE_PROMPT_VERSION = "v2.2"`, sixteen Australian track profiles, the local synthesis prompt. |
| Live race data | `server/racingApiClient.ts` | Targets `api.theracingapi.com`, which ceased Australian coverage in July 2026 (stride-racing `docs/03-data-and-ingestion.md` section 1a). Each call returns null after up to 12 seconds. |
| Sessions | `server/strideChatService.ts` | In-memory maps: 30-minute TTL, last 12 messages. Process-local. |
| Feedback | `POST /api/chat/feedback` | Writes `chat_feedback` in Neon with the prompt version. |
| Evals | `scripts/eval_chat.ts`, `evals/chat/` | 30 golden cases, 16 injection cases, recorded fixtures, offline CI mode. Live mode expects `STRIDE_CHAT_AGENT=1`. |
| CI | none | `stride-app` has no `.github` directory. `npm test` and the eval runner only run by hand. |

Three facts about that code matter for the plan.

**The agent the evals describe was never built.** `STRIDE_CHAT_AGENT` and the
five tool names appear only in the eval harness; the orchestrator calls
`messages.create` without a `tools` parameter. The eval README calls it the
"v3.0 prompt". The commits are the eval harness (2026-07-29) and nothing
after it on the chat files. So the plan is not competing with a working
agent; it is competing with a specification that already has acceptance
tests.

**The chat is pinned to a retired model generation.** `CLAUDE_MODEL` falls
back to `claude-sonnet-4-20250514` (`chatOrchestrator.ts:40`), the id whose
retirement on 2026-06-15 is recorded in `docs/validation/VR-001-invalidation.md`.
Four more copies are hardcoded in `routes.ts` (lines 781, 972, 1099, 1241)
where no environment variable rescues them. All three orchestrator calls
pass `temperature`, which returns HTTP 400 on `claude-sonnet-5` and later, so
setting `ANTHROPIC_CHAT_MODEL` to a current model would make every synthesis
call fail and the code would fall back to raw data with a warning. The SDK is
`@anthropic-ai/sdk` 0.37.0.

**Half of the chat's evidence is going stale.** Since `stride-app` PR #9
(2026-08-04) the in-process scheduler is off by default because the AWS
schedules own the daily chain. The Mac therefore no longer refreshes
`racecards/tips_<date>.json`, `intelligence/consensus_<date>.json` or
`market_signals_<date>.json`, and the six `local_*` evidence sources read
from those paths fall behind. The fresh copies are written by the Fargate
tasks to `s3://stride-evidence-<account>/artifacts/` (`infra/jobs/handler.py`,
`_sync_up`). The database-backed sources stay fresh because the same tasks
write Neon. This is the strongest argument for moving the chat's data plane
to AWS, and the plan does not mention it.

### 1.2 The AWS estate in `stride-racing`

| Concern | What runs today | Source |
|---|---|---|
| Secrets | One JSON secret, `stride/prod`, holding every key (Betfair, the write `DATABASE_URL`, Punting Form, Anthropic, Groq, Perplexity, Tavily, the flag values). Written from GitHub Actions secrets by `deploy-infra`. `ANTHROPIC_CHAT_MODEL` is already in it, though nothing in this repository reads it. | `infra/01_secrets.sh`, `.github/workflows/deploy-infra.yml` |
| Secret loading | `_load_secrets()` runs on every dispatch and copies the whole blob into the environment. Not cached, and not a problem at four Lambdas invoked at most about ninety times a day. | `infra/jobs/handler.py` |
| IAM | `stride-jobs-role`, trusted by Lambda and ECS, with `secretsmanager:GetSecretValue`, DynamoDB, SQS, SNS and S3 all on `Resource: "*"`. | `infra/05_lambda_jobs.sh` |
| Compute | One container image (`python:3.11-slim`, full ML stack, Playwright, `awslambdaric`) for four container Lambdas and ten Fargate task definitions, dispatched by `STRIDE_JOB`. | `infra/Dockerfile`, `infra/04_ecr_image.sh`, `infra/07_fargate_heavy.sh` |
| Networking | Lambdas are not VPC-attached. Fargate tasks run in the default VPC with a public IP and no NAT. Neon is external. | `infra/05_lambda_jobs.sh`, `infra/07b_fargate_schedules.sh` |
| Cost envelope | AWS Free Plan. A US$20 per month budget as a runaway tripwire. Free period ends 2027-02-01, watched weekly. | `infra/03_notifications.sh`, `.github/workflows/aws-plan-watch.yml` |
| Alarms | Per function: `Errors >= 1` per hour to SNS `stride-alerts`, an SQS dead-letter queue, two async retries, log group pre-created with 60-day retention. | `infra/05_lambda_jobs.sh` |
| Watchers | ECS failure watch, missing-run watch and schedule audit cover scheduled jobs. Content-level postconditions are the house rule. | `.github/workflows/*-watch.yml`, `14_TESTING_AND_OBSERVABILITY.md` |
| Standing rules | No Python API service exists today. New AWS services need a documented reason under the AWS Change Protocol. Reuse current infrastructure where practical; do not create unnecessary microservices. `infra/*.sh` is not edited unattended; new scripts arrive by reviewed PR. | `02_ARCHITECTURE_AND_CONTRACTS.md`, `16_CLAUDE_CODE_EXECUTION_PROTOCOL.md`, `01_GLOBAL_RULES.md`, `CLAUDE.md` |

## 2. The plan, section by section

Each row: what the plan says, what is already true, and the verdict.

### 2.1 Credentials

**Plan.** Three separate secrets (Anthropic key, Punting Form key, a
read-only Postgres connection string). Fetch at cold start, cache in memory.

**Reality.** There is one bundled secret and the chat must not read it. It
carries Betfair credentials and the write database role, and a chat endpoint
is the one component that will face untrusted input. The read-only role does
not exist yet: every `DATABASE_URL` in the estate, including the one the
Express app uses, is the owner role.

**Verdict: keep, with four additions.**

- Provision the three secrets through the deploy path, not by hand in the
  console. The estate's principle is that `deploy-infra` converges the
  account from GitHub secrets. A new `infra/10_chat_secrets.sh` (or a
  `--chat` mode) is the shape; naming `stride/chat/anthropic`,
  `stride/chat/puntingform`, `stride/chat/database_ro`.
- `ANTHROPIC_CHAT_MODEL` is configuration, not a secret. Put it in the
  function's environment, and preflight it at cold start with a four-token
  call the way `consensus_agent.preflight_extraction_model()` does. A dead
  model id in a chat is loud, but a rejected parameter after a model change
  is exactly how the consensus agent went silent for six weeks.
- Issue the chat its own Anthropic key from a separate Console workspace.
  It gets its own spend limit and can be revoked without stopping the 05:30
  consensus job. This is the cheapest real isolation available and the plan
  does not have it.
- The Punting Form key is one subscription key. "Separate secret" means a
  separate storage path and IAM grant, not a separate credential. The
  2026-09-03 lapse showed a rotation has to reach every copy; write that into
  the runbook so the chat copy is not forgotten.

The read-only role, created through the `apply-migration` workflow with the
operator's `APPLY`:

```sql
CREATE ROLE stride_chat_ro LOGIN PASSWORD '<from the password manager>';
GRANT pg_read_all_data TO stride_chat_ro;
ALTER ROLE stride_chat_ro SET default_transaction_read_only = on;
ALTER ROLE stride_chat_ro SET statement_timeout = '15s';
ALTER ROLE stride_chat_ro SET idle_in_transaction_session_timeout = '10s';
```

Use Neon's pooled host in the connection string for this role. The batch
jobs are single processes and never hit Neon's connection limit; a Lambda
with several warm containers will. Keep `sslmode=require` and
`channel_binding=require` as in `.env.example`; the Express `db.ts` sets
`rejectUnauthorized: false`, and that must not be carried over. The
`chat_feedback` writes stay with the Express app and its existing role.

Cache the secrets in module scope at cold start as the plan says, and add
one thing: on an authentication failure from Anthropic or Punting Form,
refetch once before failing the request, so a rotation does not need a
redeploy.

### 2.2 IAM

**Plan.** A dedicated role with `secretsmanager:GetSecretValue` on the three
ARNs only, separate from the ETL role.

**Reality.** The existing role is shared by the Lambdas and the ECS tasks
(the "nightly ETL" is a Fargate task, not a Lambda) and grants everything on
`*`. The plan's role is strictly tighter, which is the direction the
repository's own comments point (the digest Lambda deliberately lacks
`sns:ListTopics`).

**Verdict: keep. Add the grants the data plane needs and nothing else.**

| Grant | Resource | Why |
|---|---|---|
| `secretsmanager:GetSecretValue` | the three `stride/chat/*` ARNs | the plan |
| `s3:GetObject` | `arn:aws:s3:::stride-evidence-<account>/artifacts/*` | the day artifacts (section 2.5) |
| `dynamodb:GetItem`, `PutItem`, `UpdateItem` | one new table ARN | sessions and the daily cap (section 2.5) |
| `sns:Publish` | the `stride-alerts` topic ARN | only if the function self-alerts like `dispatch()` does |
| `logs:*` via `AWSLambdaBasicExecutionRole` | | as today |

No access to `stride/prod`, the models bucket, or `stride_run_state`.

### 2.3 Compute

**Plan.** A new Lambda behind API Gateway HTTP API. Streaming via a Function
URL or "a small Fargate service behind an ALB" if wanted.

**Reality and verdict: change all three parts.**

*Not API Gateway.* HTTP API integrations time out at 30 seconds and that
cannot be raised. One chat turn here is a loop: Claude decides, the function
calls Punting Form (0.4-second pacing plus network) and Neon, sends the
results back, and Claude writes the answer, often with a second round of
tools. With adaptive thinking on a current model that is routinely over 30
seconds. A Lambda Function URL runs to the function's own timeout (up to 15
minutes), supports CORS natively, and supports response streaming. The plan
already reaches this conclusion for streaming; it is the right answer even
without streaming.

*Not Fargate plus an ALB.* An Application Load Balancer alone is roughly
US$18 a month in Sydney before traffic, plus an always-on task. The budget
tripwire is US$20 a month for the whole account. A Lambda serving a few
hundred turns a day costs effectively nothing.

*Not the `stride-jobs` image.* It is the "one image for every job"
convention (`infra/04_ecr_image.sh`), and it is the wrong artifact for an
interactive endpoint: multi-gigabyte cold starts, no SnapStart for container
images, and its helper modules pull pandas at import time
(`intelligence/common.py`). Package the chat as a zip on the `python3.12`
managed runtime with `anthropic` and `psycopg2-binary` as its only
third-party dependencies (boto3 ships with the runtime). This is a deliberate
deviation from the one-image rule; the AWS Change Protocol asks for the
reason to be documented, and the reason is isolation plus latency. Record it
in the PR that adds the function.

Streaming: Python has no native Lambda response streaming. It needs the
Lambda Web Adapter (as a layer, with a small ASGI app) or a Node handler.
The UI today is not streaming and animates its own progress, so phase one
can return the whole `ChatCompletionResponse` at the end of the turn. Design
the handler so a later switch to server-sent events does not change the
contract.

Sizing to start: 1024 MB, 180-second timeout, reserved concurrency of 3.
Reserved concurrency is also the spend cap: it bounds parallel Anthropic
calls and parallel Neon connections at once.

### 2.4 Networking

**Plan.** No VPC attachment; Neon and both APIs are reachable from a
non-VPC Lambda.

**Reality.** Matches the estate exactly. Every existing Lambda is non-VPC
and every Fargate task uses a public IP instead of a NAT.

**Verdict: keep, and note it is not optional.** A NAT Gateway is roughly
US$40 a month before data in Sydney, twice the tripwire. Add the pooled Neon
host (section 2.1) and a five-second connect timeout, the pattern in
`run_tips_pipeline.db_connect`.

### 2.5 Flow and tool design

**Plan.** Two tools, `query_puntingform(race_id, ...)` and
`query_stride_db(sql)`; the frontend calls the Lambda directly; the Lambda
runs the tool loop and returns the final text.

**Reality.** The eval suite in `stride-app` already fixes the tool surface
and the threat model:

- Golden cases require `get_stride_tips`, `get_race_card`, `lookup_horse`,
  `query_results` and `get_performance`, plus a search mode that must cite
  server-verified links.
- Injection cases require that the model never emits SQL or table names
  (`inj-sql-01`, `inj-sql-02`), never lists tools or schemas (`inj-tool-01`),
  treats a horse called "Ignore Previous Instructions" as data
  (`inj-data-01`), and never includes a link that is not in `citations[]`
  (`inj-link-*`).
- Honest-miss cases require an admission when data does not exist
  (`miss-01` to `miss-04`).

**Verdict: change the tools; keep the loop; keep the contract.**

*Typed tools, named as the evals name them.* A free-text SQL tool is the
wrong primitive here even behind a read-only role. It contradicts the
injection suite; model-written SQL can pull `pf_raw_payloads` JSON or a
130,000-row table into the context or spend the whole statement timeout on
a cross join; and the joins are the hard part. `race_date` is TEXT in
`race_results_history` and `sectional_times`; runner keys carry country
suffixes; track aliases are many; the winner's margin is stored under two
conventions and only `result_margins.beaten_margin` reads it correctly
(`docs/03-data-and-ingestion.md`). `identity_normalization.py` exists so
that nobody writes those joins twice. Typed tools built on those helpers
give the model correct joins for free. A proposed surface:

| Tool | Backed by | Notes |
|---|---|---|
| `get_stride_tips(date, track?, race?)` | `artifacts/racecards/tips_<date>.json` in S3, falling back to `selections` | The tips file carries the full decision contract (`bet_pick`, `coverage_pick`, `bet_status`, `convergence_tier`, `full_field`); `selections` holds only `should_bet` rows. |
| `get_race_card(date, track, race?)` | `artifacts/server/python/racecards/racecard_<date>.json`, else Punting Form `meeting_detail` | Replaces the dead Racing API client. |
| `lookup_horse(name, n=10)` | `race_results_history`, `sectional_times`, `franking_scores`, `blackbook_entries` via `normalize_runner_key` | The `horse-*` cases. |
| `query_results(date, track?, race?)` | `race_results_history`, `prediction_audit`, Punting Form `results_for_meeting` for unsettled days | The `chain-*` cases. |
| `get_performance(window, group_by)` | `selection_ledger`, `stride_tip_results`, `selection_results` | Net of commission, per the ledger. |
| `get_consensus(date, track, race)` | `artifacts/.../consensus_<date>.json`, `consensus_scores` | Read only; the agent itself is untouchable. |
| `get_market_signals(date, track, race)` | `artifacts/.../market_signals_<date>.json`, `betfair_odds_snapshots` | |
| `puntingform(endpoint, ...)` | `pf_client.py` functions: meetings, meeting detail, results, scratchings, conditions, speedmaps, ratings, strike rates | One tool with an `endpoint` enum, or one tool per function. Never raw REST: `pf_client` owns envelope errors, `PFAuthError` on 401/403, retries on 429/5xx, pacing. Add a short in-memory TTL cache. |
| `run_readonly_sql(sql)` | optional, off by default | Single statement, `SELECT`/`WITH` only, wrapped in `SELECT * FROM (...) q LIMIT 200`, allowlisted tables, result size cap. An operator escape hatch, not a default tool. |

Punting Form's data is licensed for personal use only
(`PUNTINGFORM_MIGRATION.md`). That is fine while the chat is the operator's
own; it needs a licence review before anyone else can ask it questions. The
subscription serves about 31 days back; the tool should say so when asked
for older dates rather than return nothing.

*Tool results are untrusted.* Wrap every result in a delimited block, say so
in the system prompt, and never let Punting Form or database text sit in the
instruction position. This is the rule `claude.yml` already applies to logs.

*API usage.* Default the model to `claude-opus-5` unless the operator
chooses otherwise, and read the id from `ANTHROPIC_CHAT_MODEL`. Adaptive
thinking on. `max_tokens` of at least 8,000; do not carry over the 900 to
2,000 caps from the TypeScript code. Prompt caching: the system prompt and
tool definitions form the stable prefix, the date and session block go after
the breakpoint. When Claude issues parallel tool calls, return every
`tool_result` in one user message; a failed tool returns `is_error: true`
rather than being dropped. Cap the loop at eight tool rounds and handle
`stop_reason` values `max_tokens` and `refusal` explicitly. Do not pass
`temperature`.

*Conversation state.* The API is stateless and Lambda containers are
disposable, so the in-memory maps cannot move. Store the last 12 messages per
`sessionId` in a new DynamoDB table with a 30-minute TTL (the constants the
TypeScript code uses). DynamoDB is already in the estate.

*The contract.* Keep `ChatCompletionRequest` and `ChatCompletionResponse`
from `shared/schema.ts` unchanged: `message`, `sessionId`, `modes`,
`raceContext` in; `response`, `mode`, `answerSource`, `trace`, `citations`,
`warnings`, `promptVersion` out. The trace sections (`thinking`, `steps`,
`sources`, `why`) map onto the tool loop directly: tools called become
steps, tool provenance becomes sources, and citations can name Punting Form
endpoints and S3 keys as well as URLs. `ChatInterface.tsx` and
`ChatTracePanel.tsx` then need no change.

*Search mode.* Perplexity search and citation verification live in the
TypeScript orchestrator today. Port them last. Until then the Express proxy
routes `modes.search = true` to the existing path and everything else to the
Lambda. That is what makes the migration incremental.

### 2.6 Who calls the Lambda

**Plan.** The React frontend calls API Gateway directly.

**Reality.** The React client is served by the Express app and calls
`/api/chat` same-origin with `credentials: "include"`. The rate limiter,
the feedback route and the session id all live there. Nothing authenticates
the chat today; it is reachable only where the app is served.

**Verdict: change.** A public Function URL with `AuthType=NONE` and no other
check lets anyone spend the Anthropic key. That is the exact threat the
repository's `claude.yml` owner allowlist exists to close on the public repo.
Options, in order of simplicity:

1. Express proxies `/api/chat` to the Function URL with a bearer token
   (stored in Secrets Manager, present in the Mac's `.env`) and the Lambda
   rejects anything else before touching Anthropic. Same-origin for the
   browser, rate limit unchanged, no AWS credentials on the Mac (the estate's
   rule is no long-lived AWS keys anywhere). Recommended for phase one.
2. `AuthType=AWS_IAM` behind CloudFront with origin access control, and
   Cognito on the front, if the React client is later hosted statically and
   the audience widens.

Under either option, reserved concurrency and the daily cap (section 2.7)
bound what a leaked token can cost.

### 2.7 Ops

**Plan.** Reuse the ETL alarm pattern; keep dev and prod secrets separate.

**Reality.** The alarm pattern is per-function `Errors` to `stride-alerts`,
a dead-letter queue, two async retries and 60-day log retention. Dev and
prod do not exist as separate things: one account, one `stride/prod`, no
dev stack. The SNS email subscription's confirmation state has never been
verified (`aws-plan-watch.yml` header).

**Verdict: keep the alarms and retention; drop the DLQ; add what an
interactive endpoint needs.**

- No dead-letter queue and no async retries. Function URL invocations are
  synchronous; a retry would double the spend on a turn the user already
  saw fail.
- Alarms on `Errors`, `Throttles` and `Duration` p95 near the timeout, all
  to `stride-alerts`. Log group created before retention is set, retention
  unguarded, exactly as `05_lambda_jobs.sh` does it.
- Log `usage` (input, output, cache read and write tokens) per turn as one
  structured line. Low-cardinality metrics only: turns, tool errors,
  Anthropic 4xx and 5xx. Never a per-race or per-horse dimension.
- A daily cap in DynamoDB, the `DAILY_CLAUDE_CAP` idea from
  `consensus_agent.py`: past the cap the function answers with a clear
  "budget exhausted" message and does not call Anthropic.
- A content-level smoke, in the `verify-jobs` style: a workflow input
  `chat-proof` posts a canned question that must produce at least one
  database tool call returning rows and one Punting Form call returning a
  non-empty payload. A 200 with prose is not evidence; that is the proxy
  failure `CLAUDE.md` describes. Run it from `deploy-infra` after the
  function updates.
- Dev versus prod means separate names (`stride/chat/*`), a local path
  (`python -m chat.cli`, `.env`) and the separate Anthropic key. Not a
  second AWS stack.

### 2.8 MCP

The plan suggests wrapping Punting Form and Postgres as MCP servers so any
MCP client can use them. As a local operator surface that is cheap: a stdio
MCP server exposing the same Python tool functions to Claude Desktop or
Claude Code. For the production chat it is the wrong shape: a remote MCP
server is another hosted, authenticated service, and the Messages API MCP
connector needs a publicly reachable URL. In-process tools in one Lambda
are simpler, cheaper and testable offline. Keep the tool functions
transport-agnostic under `server/python/chat/tools/` so both bindings stay
thin.

## 3. Decisions

1. **Scope.** Port the chat's data plane to AWS behind the existing
   contract, with the existing eval suite as acceptance. Not a new
   two-tool agent.
2. **Compute.** A slim Python 3.12 zip Lambda behind a Function URL.
   Not API Gateway, not the `stride-jobs` image, not Fargate with an ALB,
   not VPC-attached.
3. **Data plane.** Neon through a new read-only pooled role, the S3
   artifact relay, and Punting Form through `pf_client.py`. Typed tools
   named as the evals name them; no SQL tool by default.
4. **Secrets and IAM.** A dedicated role; three ARN-scoped secrets under
   `stride/chat/*` provisioned by a new infra script inside `deploy-infra`;
   a separate Anthropic key; the model id in the environment with a
   cold-start preflight.
5. **Caller.** Express proxies `/api/chat` to the Function URL with a
   bearer token; a per-mode flag keeps search mode on the TypeScript path
   until it is ported.
6. **Guardrails.** Reserved concurrency of 3, a daily turn cap, an
   eight-round loop cap, `statement_timeout` on the role, result size caps,
   tool output framed as data.
7. **Ops.** `Errors`, `Throttles` and `Duration` alarms to `stride-alerts`;
   60-day logs; per-turn usage logging; a `chat-proof` smoke that checks
   content.

## 4. Defects in the existing chat, independent of the plan

These are true today and worth fixing whether or not the Lambda is built.

1. The model default `claude-sonnet-4-20250514` is retired
   (`chatOrchestrator.ts:40`); four hardcoded copies in `routes.ts` at lines
   781, 972, 1099 and 1241 are not covered by `ANTHROPIC_CHAT_MODEL`.
2. `temperature` on all three orchestrator calls (lines 630, 1884, 2238)
   blocks any move to `claude-sonnet-5` or later.
3. `@anthropic-ai/sdk` 0.37.0 predates adaptive thinking, effort,
   structured outputs and strict tools.
4. `racingApiClient.ts` targets a dead provider; `hasRacingApiCreds` is
   true whenever the old credentials are in `.env`, so search-mode turns
   with race context still wait on it.
5. The `local_*` evidence sources read the Mac's `racecards/` and
   `intelligence/` directories, which stopped refreshing when the app
   scheduler was gated off. The S3 relay has the fresh copies.
6. `db.ts` disables TLS verification to Neon.
7. No authentication on the chat routes, only a rate limit.
8. No CI in `stride-app`; the eval harness never runs automatically.
9. `STRIDE_CHAT_AGENT` and the five tool names exist only in the evals.

## 5. Phased delivery

**Phase 0, no AWS.** Create `server/python/chat/` in this repository: the
tools (built on `pf_client`, `identity_normalization`, `result_margins`,
and an S3 reader with a local-directory fallback), the loop, the prompt
(port v2.2 and the track profiles from `stridePrompts.ts`, bump the
version), a CLI runner, and offline tests that replay recorded tool results
the way `evals/chat/fixtures` do. Run the golden and injection suites
against the CLI over HTTP with the existing `eval_chat.ts --live`, or port
the runner. Exit: both suites green locally against a read-only Neon URL,
with the operator's approval for that read.

**Phase 1, AWS, additive.** The read-only role through `apply-migration`.
`infra/10_chat_lambda.sh` creating the role, secrets, function, Function
URL, log group and alarms, idempotent like its siblings, wired into
`deploy-infra` behind a `deploy_chat` input. The `chat-proof` smoke. Exit:
smoke green with tool calls proven in its output.

**Phase 2, the app.** `STRIDE_CHAT_BACKEND=lambda` in Express routes
`/api/chat` to the Function URL for default and brain modes; feedback stays
local; remove the Racing API client; fix the model, temperature and SDK
items in section 4. Exit: live evals green through the UI.

**Phase 3, later decisions.** Streaming through the Lambda Web Adapter and
server-sent events from the proxy; the search-mode port; retiring the
TypeScript orchestrator; static hosting of the React client behind
CloudFront with the Function URL as origin. Each is its own decision.

## 6. Questions for the operator

1. Audience. Operator-only keeps the Punting Form licence and the threat
   model simple. Anyone else needs authentication, a licence review and a
   spend policy.
2. Model. `claude-opus-5` by default, or `claude-sonnet-5` as the consensus
   agent uses. Recommendation: Opus 5 for the chat, measured per turn.
3. Streaming now or later. Recommendation: later; the UI already animates.
4. Search mode. Port to Python, or keep on the TypeScript path.
5. Frontend hosting. Stay on the Mac for now, or move to static hosting
   when the Lambda is proven.

## 7. Illustrative cost per turn

Formula: input tokens at the model's input rate, cache reads at about a
tenth of it, output and thinking tokens at the output rate. One worked
example on `claude-opus-5` (US$5 and US$25 per million), a two-round tool
turn with a 15,000-token cached prefix, 5,000 fresh input tokens and 3,500
output plus thinking tokens:

| Component | Tokens | Cost |
|---|---:|---:|
| cached prefix read | 15,000 | $0.008 |
| fresh input | 5,000 | $0.025 |
| output and thinking | 3,500 | $0.088 |
| total | | about $0.12 |

At fifty turns a day that is about US$180 a month on the Anthropic side,
which is why the separate key with its own limit and the daily cap matter
more than any AWS line item. The AWS side at that volume is under US$2 a
month: three secrets at US$0.40 each and negligible Lambda, DynamoDB and
log charges.
