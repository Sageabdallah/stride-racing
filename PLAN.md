# Stride — Backend Plan v5.2 (v4, audited three times)

**Vision: the go-to place to ask anything about Australian horse racing.**

Users bring their own PuntingForm key, authenticate, and ask questions in
plain English. Stride answers from three grounded sources — PuntingForm data
(user's key), Stride's own ML models (pre-computed race predictions), and
cited web search — with explicit provenance on every claim.

Priorities: **credential security > UX > robustness**, with **data
licence as a hard gate on audience** rather than a priority. v5 ranked
licence above credential security; the second audit reversed that (§22
finding 4). They are not comparable quantities: the licence is a binary
question about who may be served, answered once in writing; credential
security is a continuous obligation that applies at every audience size,
including an audience of one. Ordering them invites the reading that an open
licence question could justify deferring a security control. It cannot.

**Spend posture — read before costing anything.** This account runs on the
AWS Free Plan, where credits cover usage and access simply ends when they
run out. `aws-plan-watch.yml` states the consequence in its header: *"the
risk is therefore not a bill. It is an OUTAGE"*, and the out-of-pocket
ceiling is US$0. Shipping this chat ends that posture. Anthropic bills in
real money with no free-plan cutoff, at US$180 to US$3,600 a month across
the volumes in §10.2, and **no watcher in this repository can see it** — the
US$20 tripwire and the plan watch both read AWS spend only. The chat's own
spend watch (§15.4) is therefore not an ops nicety; it is the replacement
for a safety net that stops covering the largest cost line the day this
launches.

**What v5 is.** v4 audited on 2026-09-09 against the two repositories it has
to run in — `stride-racing` (the pipeline and the AWS estate) and `stride-app`
(the web app, its chat, its eval suite and four unmerged `chat/*` branches) —
and against the 2026-09-03 audit of the earlier "chat Lambda" plan
(`docs/chat/CHAT_LAMBDA_ARCHITECTURE_AUDIT.md`). Every locked decision was
checked. Where v4 assumed something that does not exist, v5 names what does
exist and re-bases the decision on it. Where v4 was missing a control, v5
adds it. Nothing here is implemented; §15 is how it will be tested, §16 is
what must be true before it can function, §19 is the change log against v4,
§20 is the list of decisions only the operator can make, §21 is rollback.

**What v5.1 is.** v5 audited again on 2026-09-09, adversarially and against
itself. Twelve findings: eleven corrections — claims that were wrong,
over-scoped, or asserted without checking — and one claim that survived the
challenge and is now recorded with the evidence that settles it. §22 logs
each; the sections below carry the corrections. An independent adversarial pass then found
twenty more, several of them serious; §23 logs those and the sections carry
those corrections too. The largest self-audit finding is scope: v5's critical path put a
Cognito user pool and a KMS key service in front of a first version that has
exactly one user, who already has a working password gate. The first useful
slice is roughly a third of what v5 implied (§17).

**Read this first if you read nothing else.** v4 references "v2 §6", "v2
§7", "v2 §13" and "v3" as if they stand. They are not in either repository
(`git log --all -- '*plan*'` finds only this file). v5 inlines what it needs
from them so the plan is self-contained.

---

## 0. Product principles (the "not tacky" contract)

1. **Data-first, always.** Every visual or verbal flourish must be grounded in
   a real tool result. The moment we dress up ungrounded text, we're a gimmick.
2. **Quiet confidence.** Measured Australian racing-analyst voice. No hype, no
   bookmaker slang-for-clout, no emoji fireworks, no confetti. Typography and
   well-set numbers are the aesthetic.
3. **Honesty is a feature.** Saying "my model doesn't cover this meeting" or
   "your plan doesn't include sectionals" plainly builds more trust than a
   smooth non-answer. The honesty gradient (§11) is brand, not fallback.
4. **Probabilities, not tips.** Model output is framed as analysis with a
   responsible-gambling notice — never as "bets" or "tips". *(v5 note: the
   pipeline's own product surface is a BET/NO_BET contract and a `selections`
   table the existing app displays as tips. This principle is a positioning
   decision for the chat surface; it does not rename the pipeline's output,
   and the chat must not pretend the bet contract does not exist when asked.
   See §20 Q1.)*
5. **The racing calendar is the home screen.** The product breathes with the
   real racing week. Ambient, current, specific — never a generic empty box.
6. **(v5) Spend is a security property.** Every model call sits behind
   authentication and a budget. An unauthenticated chat is an open invoice
   (§9, §10).
7. **(v5) Reuse before build.** The estate rule is to reuse current
   infrastructure and document a reason for any new AWS service
   (`docs/decision-learning/01_GLOBAL_RULES.md` rule 28;
   `16_CLAUDE_CODE_EXECUTION_PROTOCOL.md` "AWS Change Protocol"). Most of v4's
   new services had no reason that survives §1; v5 removes them.

---

## 1. Starting point — what already exists (v5, all new)

v4 reads as if the backend is a blank page. It is not. Everything below was
read from the repositories on 2026-09-09; paths are given so the reader can
check.

| Concern | What exists today | Where | What it means for this plan |
|---|---|---|---|
| Web app | Express + React (Vite). Served by Express on the operator's own machine (`PORT` 5000). **No per-user auth**: a `users` table exists, no route uses it, no `passport.*` call anywhere. Rate limits per IP: 20/min on chat routes, 120/min on `/api`. Sentry. | `stride-app` `server/index.ts`, `server/routes.ts`, `shared/schema.ts` | A multi-tenant product cannot run on a laptop with no login. Hosting and identity are prerequisites, not details (§16). |
| Chat, on `main` | TypeScript orchestrator (~300 KB): 21 intents, 20 evidence sources, Claude synthesis, Perplexity search, JSON contracts. Pinned to the **retired** `claude-sonnet-4-20250514`, passes `temperature` (rejected on current models). Sessions in-memory, 30-min TTL, last 12 messages. Feedback to `chat_feedback`. | `server/chatOrchestrator.ts:40`, `server/strideChatService.ts:57` | The browser contract (`ChatCompletionRequest`/`Response`, trace sections, `citations[]`) is what the client renders today. v4's block contract is a client change too (§13, §16). |
| Chat, unmerged branches `chat/01-hygiene` … `chat/04-streaming` (2026-07-29 → 08-02) | An agent loop with **five typed tools, `strict: true`** (`lookup_horse`, `get_race_card`, `get_stride_tips`, `query_results`, `get_performance`); `web_search_20260209` + `web_fetch_20260209`; a citation allowlist shared by server and client; 8-iteration cap; SSE route `POST /api/chat/stream`; per-IP + per-session limiter; an `STRIDE_CHAT_AGENT` flag gate; a `stride_chat_ro` read-only Neon role migration with `statement_timeout = '5s'`; a shared app-password gate for deployed instances; model default `claude-opus-5` via `ANTHROPIC_CHAT_MODEL` (`claudeConfig.ts` on the aws branch). | `server/chatAgent.ts`, `server/chatStream.ts`, `server/chatRateLimit.ts`, `migrations/app/chat_readonly_role.sql`, `server/authGate.ts` (branch `claude/frontend-public-repo-aws-pqjkty`) | v4's "orchestrator + toolbelt + bounded loop + SSE" exists as a prototype in TypeScript. v5 keeps its tool names, its citation invariant and its eval contract, whatever language the production loop ends up in (§7). |
| Eval suite | 30 golden cases (db_tips, horse_lookup, performance, chained, web, honest_miss, followup) + 16 injection cases (prompt_exfil, sql_probe, jailbreak, data_as_instruction, adversarial_page, link_injection, schema_probe, off_domain). Offline by construction; live mode is operator-only and refuses without two env vars. | `stride-app/evals/chat/`, `scripts/eval_chat.ts` | The **questions** are the acceptance test v4 §14 asks for, and they carry over. The **harness** does not, and v5 said "already written" without qualifying that (§23 finding 16): `eval_chat.ts` asserts substrings over a flat `response: string`, replays TypeScript-agent fixtures, and drives live mode by POSTing to `/api/chat`. This plan's contract is `blocks[]`, and §20 Q2 contemplates a Python service the harness cannot invoke. §15.6 additionally wants scoring on block choice, follow-up quality, tone, band conformance and RG framing, none of which the engine has. Treat the harness, the response contract and the assertion vocabulary as new work on the release-gate critical path (§15). |
| Prior audit (2026-09-03) | Decisions: port, don't rewrite; a slim Python 3.12 **zip Lambda behind a Function URL** (not API Gateway, not Fargate+ALB, not VPC-attached, not the jobs image); Neon read-only **pooled** role; typed tools, no SQL tool by default; `stride/chat/*` secrets + a **separate Anthropic key**; Express proxies to the Function URL with a bearer; DynamoDB sessions; reserved concurrency 3; daily cap; 8-round cap; a `chat-proof` content smoke. | `docs/chat/CHAT_LAMBDA_ARCHITECTURE_AUDIT.md` §3 | v5 adopts all of it. v4 contradicted four of these (a shared FastAPI service, Redis, an always-on API, no spend cap) without saying why. |
| AWS estate | One container image for 4 Lambdas + 10 Fargate tasks; EventBridge Scheduler (Sydney tz); DynamoDB `stride_run_state`; S3 `stride-models-<acct>` (private) and `stride-evidence-<acct>/artifacts/` (the day-artifact relay); Secrets Manager `stride/prod` (**one blob holding Betfair credentials and the write `DATABASE_URL`**); SNS `stride-alerts`; a **US$20/month cost tripwire** on the AWS Free Plan (ends 2027-02-01); deploys from GitHub Actions through an OIDC role that carries `AdministratorAccess`. **No KMS key, no Redis, no Cognito, no API service.** | `infra/*.sh`, `infra/jobs/handler.py`, `.github/workflows/deploy-infra.yml`, `infra/09_bootstrap_oidc.sh:52` | New resources can be created by `deploy-infra` (the role allows it). Anything always-on (ALB, NAT, ElastiCache, a Fargate service) breaks the tripwire on its own (§10). |
| Pipeline outputs | `tips_<date>.json` per race: `top_picks`, `raw_model_leader`, `bet_pick`, `coverage_pick`, `full_field`; per runner: `win_pct` (**market-anchored** published probability), `raw_model_pct` (pre-anchor model opinion), `place_pct`, `fair_odds` (**= 100 / de-vigged market probability — the market's fair price, not the model's**), `edge_pct`, `key_factors`, `ai_insight` (LLM prose), and `prediction_stages` with `base_xgb`, `base_lightgbm`, `base_catboost`, `ensemble`, `mc_raw`, `mc_recalibrated`, …, `final_decision`. `prediction_audit` holds every scored runner (MC-stage win/place prob, `market_odds`, `edge`, `final_win_prob`). `selections` holds bet-worthy picks only. | `examples/sample_race.json`, `server/python/run_tips_pipeline.py:985` (`fairOdds = 100/true_market`), `:2361`, `:3304`, `server/python/prediction_stages.py`, `migrations/final_prob_audit.sql` | Per-member probabilities v4 wants to *add* are already produced, **per runner**: `mc_api` attaches the base/ensemble/MC stages inside its per-runner loop (`mc_api.py:7243-7265`, called at `:7862`) and `run_tips_pipeline` adds the wrapper stages for every horse (`:1077-1080`) before `finalise_stages` writes them onto each `full_field` entry (`:3310`). `examples/sample_race.json` predates the field and does not show it (§22 finding 8) (§8). The names v4 uses (`predictions`, `model_registry`) do not exist; the semantics of `fair_odds` differ from v4's example (§8, §11). |
| Coverage and timing | Only `TARGET_TRACKS` are scored (env-overridable list; ~one day in three is quiet). The morning chain was **re-timed on 2026-08-06** so a Saturday card can finish before its own races: 04:00 racecard, 04:15 baseline-night, 04:20 intelligence, 05:30 consensus, 07:30 morning-odds, **08:05 tips**. At ~90 races the tips job still costs **3.4–6.2 h** and lands after midday whatever time it starts; the handler sizes its timeout at 8.5 h. On a quiet day the tips job returns early and `tips_<date>.json` is **never written**. | `infra/07b_fargate_schedules.sh:73-77` (retires the old names) and `:101-121` (the live chain and its measured costs); `infra/jobs/handler.py:953-959` (quiet day); `server/python/target_tracks.py`. **Not `infra/README.md`, which still lists the retired times** | "My model doesn't cover this meeting" is the common case, not the edge case. On the biggest racing day predictions land in the early afternoon, and on a third of days there is no artifact at all (§3.6, §8). |
| PuntingForm | Starter tier. Auth is the **`apiKey` query parameter** on every call. Rate limits **unpublished** (client paces 0.4 s). Data reachable **~31 days** back (sliding). Sectionals need the Modeller tier. Ratings use the same `apiKey`. No endpoint reports plan, quota or remaining calls. Licence note recorded by the operator: *"personal use only. No redistribution; revisit before any public/commercial tips output."* **One physical key**: it is the pipeline's key. Cards carry **no odds**; prices are injected from Betfair. | `server/python/pf_client.py`, `PUNTINGFORM_MIGRATION.md:15`, `:412`, `server/python/betfair_enrich_racecard.py` | v4's onboarding copy ("Pro plan… ~400 requests left today") cannot be produced (§3.1). The "three-key separation" is two keys until a second subscription exists (§9). Any user other than the operator needs the licence conversation first (§16). |
| Prices | Betfair Exchange, **delayed** app key (the live key was never activated); captures only from an AU IP (GitHub-hosted runners are geo-blocked); snapshot history begins 2026-08-02; phantom sub-$1.20 rows from unformed books must be fenced. | `scripts/BETFAIR_KEYS_STATUS.md`, `docs/decision-learning/02_ARCHITECTURE_AND_CONTRACTS.md` (data limitations) | `divergence` is real but its price side is delayed data with a licence of its own (§8, §16). |
| Claude API (verified 2026-09-09 against the bundled Anthropic `claude-api` reference shipped with the tooling — **not** this repository's gitignored `.claude/skills/`, which holds STRIDE's own pipeline reference and is a different thing; its docs cache is dated 2026-06-24, so treat these as current-as-of that date rather than live) | `web_search_20260209` server-side with citations (`web_search_result_location`); `strict: true` tools; `output_config.format` structured JSON — no recursion, no numeric `minimum`/`maximum`, `additionalProperties: false` required, **incompatible with citations (400)**; `claude-opus-5` US$5/US$25 per MTok, `claude-sonnet-5` US$2/US$10; adaptive thinking on by default on Opus 5; `temperature` rejected; no prefill; prompt caching is a prefix match; Batch API at 50%; web search US$10 per 1,000 searches. | skill `shared/tool-use-concepts.md`, `shared/managed-agents-core.md:196` | v4's two LLM claims hold (strict schemas, native citations). Structured output and citations do not mix in one call, which fixes the composer's shape (§7). |

---

## 2. Locked decisions (v5)

| Decision | v4 | v5 | Why (evidence in §1 / §19) |
|---|---|---|---|
| LLM | Claude Messages API | **Keep.** `claude-opus-5` by default, read from an `ANTHROPIC_CHAT_MODEL` **Lambda environment variable** set by `10_chat_stack.sh`. The same name exists in `stride/prod` (`infra/01_secrets.sh:20`), and the chat cannot read that secret (§9.3, invariant 14) — v5 cited it as the source and contradicted itself (§23 finding 4). The prior audit settles it: "`ANTHROPIC_CHAT_MODEL` is configuration, not a secret". Preflighted at cold start with a four-token call the way `consensus_agent.preflight_extraction_model()` does; abort loudly on a dead id or a rejected parameter. `claude-sonnet-5` is allowed for the composer only after the evals say quality holds. No `temperature`, no prefill, adaptive thinking. | Both v4 claims verified. The preflight exists because a retired model id once silenced the consensus agent for six weeks. |
| Web fallback | Claude web search tool | **Keep**, as `web_search_20260209` plus `web_fetch_20260209` for cited pages; `max_uses` per turn; an `allowed_domains`/`blocked_domains` policy owned by the operator; searches counted against a budget (US$10 per 1,000). | Verified; the unmerged `chat/03-web-citations` branch already wires it with a citation allowlist. |
| Compute | "Single shared FastAPI" | **One Python Lambda `stride-chat` behind a Function URL in `RESPONSE_STREAM` mode**, packaged as a zip on the managed `python3.12` runtime with the Lambda Web Adapter layer (`AWS_LWA_INVOKE_MODE=response_stream`). FastAPI is fine *as the in-process ASGI framework*; it is not a service. Reserved concurrency is the spend and connection cap **and the concurrent-user ceiling**: §13 needs `GET /share/{id}` and `/healthz` unauthenticated, so the Function URL is `AuthType=NONE` and open to the internet, and an unauthenticated flood consumes every slot and every cold start before invariant 9's JWT check runs (§23 finding 12). Put the API behind the same CloudFront distribution §20 Q3 already proposes for the client, with a rate-limiting behaviour, or accept that the concurrency number is also an availability decision. Not API Gateway, not Fargate + ALB, not VPC-attached, not the `stride-jobs` image. | Prior audit §2.3–2.4; the US$20 tripwire; LWA README verified 2026-09-09 (FastAPI streaming examples, `AWS_LWA_INVOKE_MODE`). API Gateway's 29-s ceiling can now be raised by quota request on regional REST APIs (2024 change; **unverified from this session**, egress blocked) — the Function URL still wins on cost and hops. |
| Key encryption | AWS KMS envelope behind `KeyProvider` | **Keep**, made concrete in §9: one customer-managed key, `GenerateDataKey` per stored key, per-user encryption context, decrypt only inside the executor, plaintext never cached beyond the request, local provider refuses `ENV=production`. | KMS does not exist in the estate yet; `deploy-infra` can create it. |
| Identity | "OIDC/JWT" (unspecified) | **Amazon Cognito user pool** (hosted UI, OIDC, JWTs verified in the Lambda against the pool's JWKS), MFA optional, step-up = `auth_time` within 5 minutes or an MFA challenge. Any OIDC provider would do; Cognito is in-account, deploys from the same scripts, and has a free tier (**pricing unverified here**; verify before committing). | Nothing authenticates the app today (§1). "Step-up auth" needs a mechanism, not a phrase. |
| Session memory, SSE replay | Postgres + Redis | **DynamoDB with TTL** for sessions, entity memory, replay buffer and budget counters. No Redis. | DynamoDB is in the estate; ElastiCache's smallest node alone (~US$12/month, **unverified**) is more than half the tripwire. |
| Model output | Win/place probabilities + fair odds | **Keep, with defined semantics** (§8): `win_pct` (published, market-anchored), `raw_model_pct` (model opinion, pre-anchor), `place_pct`, `market_fair_odds` (today's `fair_odds`), `model_fair_odds` (= 100 / `raw_model_pct`), member stages from `prediction_stages`. | `fair_odds` in the artifact is the market's de-vigged price. v4's example ("31% (fair $3.20)") quoted the model's. Both are useful; they must be named. |
| Prediction production | Batch, pre-computed | **Keep.** Add a **chat read model** `chat_predictions` materialised from `tips_<date>.json` when the tips job lands, plus a `scoring_in_progress` state for the hours before it does. | The `predictions` table v4 reads from does not exist; the JSON and `prediction_audit` do. |
| Model features / pipeline key | Stride-owned PuntingForm key; ToS gate | **Keep.** | Unchanged. |
| Key-optional onboarding | Modes A, B, C | **Modes A and C ship. Mode B (pool key) is deferred** until (1) written PF clearance and (2) a second PF subscription exists so pool traffic cannot take the pipeline down with it. | One physical key today; licence note says personal use only (§1, §9, §16). |
| Answer contract | Block-based | **Keep**, built deterministically. Code projects every block but `prose` from the tool results; the orchestrator's own text is the prose. A second tool-free composer call using `output_config.format` stays available behind a flag for narrative-heavy answers. | Structured output and citations cannot share one call (400) — but that argues for assembling blocks in code, not for paying for a second model call (§22 finding 2). |
| Model voice | Ensemble internal state drives tone | **Keep in spirit, corrected in mechanism** (§8): agreement is measured across MC, ML and market stages; per-member percentages are quoted only from calibrated outputs; tone bands are derived from the served distribution, not guessed. | `base_*` stage values are raw class-weight-inflated booster scores unless `STRIDE_ML_APPLY_ISOTONIC` is on ("a true 10% runner reads ~50%", `ml_model.py`). |
| Frontend (new) | (not in v4) | The block renderer, SSE client, key-entry UI and Cognito login live in `stride-app`. Hosting moves off the operator's machine: static client behind CloudFront with the Function URL as the API, *or* Express kept as a thin proxy on a small always-on host. **Operator decision** (§20 Q3). | A "backend plan" whose contract the client cannot render is not a plan for a product. |
| Licence (new) | ToS as an open risk | **A written PF answer is a launch gate for any user other than the operator**, and it determines which blocks may contain PF-derived data, whether a per-user cache is allowed, and whether Mode B can exist at all. Betfair's data terms are checked before prices appear on a public surface. | `PUNTINGFORM_MIGRATION.md:15`; prior audit §2.5. |

---

## 3. UX surfaces (v4 text kept; v5 corrections marked)

### 3.1 First run: value before the key — access modes

Asking for an API key is the highest-friction, highest-anxiety moment in the
product — so no user ever hits it as a wall.

- **Mode A — own key.** Full personal-data tier against the user's own quota
  and subscription. The best experience; always offered, never forced.
- **Mode C — keyless.** Model + web tiers only. Still genuinely useful on
  raceday. **(v5) This is the default for keyless users at launch.**
- **Mode B — Stride shared access. (v5) Deferred**, not deleted. It returns
  when the two conditions in §2 are met. Until then the copy for a keyless
  user is *"add your PuntingForm key for form data and sectionals"*, with no
  mention of a shared pool.

**(v5) Key entry UX, corrected to what PuntingForm can actually tell us.**
The friendly probe in v4 — *"You're on the Pro plan… ~400 requests left
today"* — cannot be built: there is no plan, quota or remaining-calls
endpoint in the documented API (`pf_client.py`, `PUNTINGFORM_MIGRATION.md`
Phase A inventory), "Pro" is not a tier (Starter, Modeller, Professional
are), and rate limits are unpublished. What the probe *can* say, honestly:

- *"Key works"* — `meetingslist` for today returned 200 with a payload.
- *"Your tier includes X"* — inferred by probing one tier-gated endpoint
  (sectionals → 403 on Starter) and reported as an inference.
- *"Data reaches back about a month"* — the ~31-day wall, measured at
  runtime the way `pf_window.py` does, never assumed.
- *"PuntingForm doesn't tell us how many calls you have left; if you hit a
  limit we'll say so and fall back to my model."*

Existing hard rules unchanged: step-up auth for key operations, one-click
delete, KMS envelope encryption, the key is never sent back to the browser
(status shows the last four characters only).

### 3.2 The chat experience: block-based answers

The backend returns **typed blocks**, not a markdown string. The frontend
renders native components; quality is enforced by the contract, not by
prompt luck. Block types:

| Block | Content | Example moment |
|---|---|---|
| `prose` | Grounded natural language | The analytical voice |
| `field_table` | Race field with `win_pct`, `raw_model_pct`, `model_fair_odds`, `market_fair_odds`, price (source + `captured_at`), top factor per runner | "Who wins R5?" — a quiet, well-set table, not a wall of text |
| `form_guide` | Line-per-start career form | "How did she go last prep?" |
| `comparison` | Side-by-side runners or model-vs-market | "Him vs the favourite" |
| `divergence` | Where `raw_model_pct` exceeds the de-vigged market probability | "Where's your model disagree with the market today?" — framed as data observation with RG notice |
| `briefing_card` | Briefing payload (§3.6) | App open |
| `capability_prompt` | Contextual key/tier ask | Key-optional flow (§3.1) |
| `notice` | Staleness, tier, coverage, `scoring_in_progress`, partial-result disclosures | "Scored 09:12, before the scratching of X" |
| `generated_note` **(v5)** | The pipeline's own LLM prose (`ai_insight`, `brief_assessment`), labelled as generated | Never a source; never quoted as data |
| `sources` | Provenance + citations | Every answer |

Every answer also carries 2–3 **grounded follow-ups** (§3.3) and a
`confidence` grade. Provenance tiers: `puntingform | stride_model | hybrid |
web_fallback | model_unverified | none`.

**(v5)** Every numeric token in a `prose` block must trace to a tool result
or a block cell (§15 anti-hallucination lint). `ai_insight` text in the day
JSON is *itself* LLM output (Groq or Claude, `llm_post_scorer.py`) and
contains numbers; it is presented as `generated_note`, never as a fact
source, and its numbers are not "tool results" for the lint.

### 3.3 Follow-ups and conversation memory

Racing conversation is naturally contextual: *"Who wins R5?" → "What about
the second fave?" → "Has it won here before?"*

- **Session entity memory** (**v5: DynamoDB**, keyed by session, TTL 30 min,
  alongside the last 12 messages — the constants the existing chat uses):
  horses, races, tracks, dates mentioned in the session, plus the last
  resolved `race_id`. Passed to the orchestrator each turn so pronouns and
  ordinal references resolve correctly.
- **Grounded follow-up suggestions**: the orchestrator emits 2–3 next
  questions, each validated against (a) the user's capability profile and
  (b) pipeline coverage — we never suggest a question we can't answer.
- **Fuzzy entity resolution** (**v5: reuse, don't rewrite**):
  `identity_normalization.normalize_runner_key` / `normalize_track_key`
  carry the approved track aliases (Randwick/Royal Randwick, Kensington,
  Rosehill, Ascot WA, Sandown circuits kept distinct);
  `horse_names.horse_name_match` does the > 0.85 sequence match;
  `target_tracks` documents why loose contains-matching beat canonical keys
  on 95 stored cards. The estate rule is *"never write a new normalizer"*
  (`02_ARCHITECTURE_AND_CONTRACTS.md`). `chat_entity_aliases` is seeded from
  these plus distinct names in `race_results_history` and PF payloads, and
  the resolver uses `clarify` with ranked candidates rather than guessing.

### 3.4 Time and racing-calendar awareness

Racing is intensely temporal. Every orchestrator call is temporally grounded:
user timezone, current time, racing calendar state (what's on today, what's
upcoming, what just ran). "Saturday at Flemington" resolves to a concrete
meeting; "last spring" triggers `clarify` on year. Freshness is disclosed
habitually: *"Model scored 09:12; track now Soft 6 after the 11:40 update."*

**(v5)** Calendar sources, in order: `race_schedule` (seeded from the day's
racecard by `seed_race_schedule.py`), the racecard JSON in the S3 relay, and
PF `meetingslist` for meetings outside `TARGET_TRACKS`. Track condition
comes from PF `/Updates/Conditions`; scratchings from `/Updates/Scratchings`
(timestamped, with deductions) — both Starter-tier, both already wrapped in
`pf_client.py`.

**(v5, prompt caching)** The per-turn context bundle is volatile by
construction (time, calendar, entities). It must go **after** the cache
breakpoint — as the first user content of the turn, or as a
mid-conversation `role: "system"` message, which Opus 5 supports without a
beta — never inside the cached system prompt, or every turn pays full price
for the prefix.

### 3.5 Model explainability as conversation

"Why do you rate her?" is the product's soul. **(v5) The explanation surface
is what the pipeline already computes**, not SHAP:

- `key_factors` (DB-enriched strings: C&D record, sectional z-scores,
  franking, first-up peak…), the intelligence adjustments (franking, prep
  cycle, barrier edge, sectional trend, trainer synergy — `docs/07`), the
  Monte Carlo factor inputs, and `RacingMLModel.explain_prediction` (top-5
  by global importance × feature value — a heuristic, labelled as such).
- Per-runner SHAP is **not produced at serve**; `shap` is used only at
  training for global importance (`train_ml_enhanced.py:1193`). It is
  feasible later (tree explainers are cheap) but it is a pipeline change
  under the staged-artifact rules, and it would explain only the ML arm,
  which carries 20–40 % of the pre-anchor probability (`docs/09` §1). Say
  so in the voice rather than pretend.
- Each factor stays **drillable**: *"the weight drop"* → a follow-up pulls
  weight history via the user's key (or a `capability_prompt` if keyless).

### 3.6 Briefing & watchlists (the retention loop)

- **Watchlists**: follow horses (and tracks). Stored per user.
- **Briefing**: per user, generated when the tips job lands (or on first open
  after): today's meetings with model coverage, top-rated runner per
  meeting, any watched horse racing today, overnight scratchings affecting
  scored races, one `divergence` highlight where odds are available. One
  quiet screen. No push noise unless the user opts in.
- **(v5) "Morning" is a promise the pipeline cannot always keep.** Scoring
  starts **08:05** Sydney and on a ~90-race Saturday costs 3.4 to 6.2 hours,
  landing in the early afternoon (§1). The briefing therefore has an explicit
  `scoring_in_progress` state — *"Randwick and Caulfield scored; Doomben
  still running"* — and a `quiet_day` state for the third of days with no
  target-track racing at all. It re-renders when the final file lands.
- **(v5.2) Publishing meetings as they finish is closer than v5 said.**
  `run_tips_pipeline.py` already takes positional track filters and
  auto-merges a filtered run into the canonical `tips_<date>.json`
  (`merge_tips_into_canonical`, called at `:3740`), and
  `infra/07b_fargate_schedules.sh:117-123` names per-meeting publication as
  the remaining lever and calls it "a handler change with its own rehearsal,
  not a schedule change" — and `infra/jobs/**` is editable under the normal
  rules. v5 described this as a pipeline change requiring the full model
  review path; that overstated it (§23 finding 17). It is still not the
  chat's decision to make, but it is a cheaper option than v5 implied, and
  it would remove most of the `scoring_in_progress` machinery.
- **Watch events**: fire from the same completion signal (§5). *"A horse you
  follow is racing today at Randwick — rated 28 %."*
- **(v5) Generation path**: briefings are pure model + cached public data,
  zero PF calls, and are a natural fit for the **Message Batches API** (50 %
  cost, results keyed by `custom_id`), because they are produced for every
  opted-in user at once after scoring, not interactively.

### 3.7 Errors as conversation

Errors arrive inside the chat voice, with recovery actions, and degrade into
alternative value: *"You're rate-limited on PuntingForm for ~20 minutes —
meanwhile my model's already rated today's cards. Want the Randwick
preview?"* The error taxonomy (§12) gains `key_not_connected` (→
`capability_prompt`) and, in v5, `budget_exhausted`, `auth_required`,
`step_up_required`, `scoring_in_progress` and `licence_restricted`. A
failure is a routing decision, not a dead end.

### 3.8 Speed and streaming

- `GET /bootstrap` on app open: today's meetings, model coverage flags,
  briefing status, key status. **(v5)** Assembled from DynamoDB and S3, not
  the LLM; the shared part (meetings, coverage) is cached per minute; the
  per-user part (key status, briefing status) is never in a shared cache.
- SSE status lines are **meaningful and specific** ("checking the Golden
  Eagle field", "consulting Stride model 2026-09-08"), never "loading…".
  They come from tool events, not from thinking output.
- SSE resume: events carry sequence ids; a dropped connection replays from
  last-seen id. **(v5)** Replay buffer is a DynamoDB item per job (last K
  events, short TTL); the resume endpoint is authenticated and
  owner-checked (§9). Streaming is Lambda response streaming through the
  Function URL (`RESPONSE_STREAM`, up to the function timeout — **limits
  unverified here**, see §19).

### 3.9 The model's voice (giving the ensemble a brain)

Design question: *if the engine could answer, how would it sound?* Like what
it is — a measurement instrument. Not a tipster. Three principles, corrected
in v5 so the voice reflects what the pipeline actually computes:

1. **Agreement is confidence — measured at the right stages.** The published
   number is a blend: Monte Carlo (`mc_raw` → `mc_recalibrated`) and the ML
   ensemble (`ensemble`) combined into `wrapper_model_blend`, then anchored to
   the de-vigged market with a price-dependent weight (`docs/09` §1). So the
   honest "agreement" story has three voices, not three boosters: *does the
   simulation agree with the trees, and does the model agree with the
   market?* All of these stage values already ride in `prediction_stages`.
   **Per-member booster percentages are quoted only when they are calibrated
   probabilities** — i.e. once `STRIDE_ML_APPLY_ISOTONIC` is promoted and the
   artifact carries all three calibrators. Until then the raw `base_*`
   values are class-weight-inflated scores (a true 10 % runner can read
   ~50 %), and quoting them as "CatBoost has him at 33 %" would be a false
   statement. Before promotion, member disagreement is expressed as **rank
   agreement** ("the three trees rank him 1st, 1st and 3rd") or as a spread
   band, never as percentages. Disagreement is surfaced, never averaged away
   silently.
2. **Calibration is tone — bands derived, not guessed.** v4's thresholds
   (≥ 0.40 "strongly fancied", 0.25–0.40 "rates on top", …) do not match the
   served distribution: the README calibration table shows 8 runners out of
   3,396 above 0.30, because `win_pct` is market-anchored. Bands are
   **fitted once from `prediction_audit.final_win_prob` quantiles** (per
   field-size bucket), named for the probability they apply to (`win_pct`,
   not `raw_model_pct`), versioned, and enforced by the block assembler and
   the CI lint (§15). The LLM may not upgrade or soften a band.
3. **Humility is character.** The models only see their features. A
   pipeline-computed **data-confidence flag** (§8) powers honest hedging:
   *"first-up, no public trials in my data — treat this 22 % cautiously."*
   Asked beyond the features, the voice says *"that's outside what I can
   measure"* and routes to form data or web.

Persona texture: a measured Australian racing analyst — fluent in the
vernacular (get-out stakes, swoopers, blackbookers, midweeks, the carnivals)
without performing it; dry rather than cute; explains jargon gently for
newcomers; never tipping language ("moral", "good thing", "can't lose").
First person as Stride; internals quoted as "my model" / "my simulation".
Numbers always from tool results — inventing a figure is a release-blocking
eval failure (§15).

Worked examples of the voice (v5, using stages that exist):

- *Agreement:* "I rate King's Legacy clearly on top — 38 % published, and
  the simulation and the trees land within two points of each other. The
  market has him at 31 %, so that's a genuine disagreement with the price."
- *Disagreement:* "Headline 27 %, but my simulation and my trees are split —
  the simulation likes him more than the trees do, and the trees don't
  agree among themselves on rank. Read that as a genuine each-way profile,
  not a confident top pick."
- *Humility:* "She's first-up with no trials in my features — my 22 % is
  thin on data, so treat it cautiously. Want me to check the web for trial
  reports?"

### 3.10 Shareable answers

`POST /share` snapshots an answer (blocks + provenance) to an immutable,
public, read-only card — the organic growth loop. Shares **strip all
user-specific material**: no key status, no tier info, no quota, no session
data. Model provenance (release id, scored_at) stays — it's the
credibility.

**(v5) Shares are model-derived content only.** No `form_guide`, no PF
field data, no `generated_note`, and no Betfair price unless the Betfair
terms are confirmed to allow it: a public card that redistributes licensed
data is redistribution regardless of whose key fetched it. The sanitizer is
an **allowlist of block types and fields**, not a denylist; share ids are
≥ 128-bit random; `GET /share/{id}` is rate-limited, `noindex`, and the
anti-extraction caps (§9.5) apply.

---

## 4. Invariants

1. **Two-key separation.** User key never in any LLM prompt/response/log/
   trace/error. Claude sees schema + question only.
2. **Three-key separation — (v5) honestly counted.** Today there are two
   kinds of key: user keys, and *the* Stride key, which is the pipeline's
   Starter subscription. The Stride key is managed in `stride/prod`, is never
   read by the chat, and is never a pool key. A third kind (a pool key)
   exists only when a second subscription does.
3. **SSRF containment.** The executor reaches `api.puntingform.com.au` only
   (`pf_client.BASE` is a constant, not a parameter) plus Anthropic. The
   Lambda never fetches a user-supplied URL; `web_fetch` runs on Anthropic's
   side.
4. **Quota protection.** Internal budgets kill malformed loops before they
   touch user quota. Model lookups cost zero user quota.
5. **Honest provenance.** Every claim sourced: PuntingForm, Stride model
   (release id + timestamp), cited web, or explicit "unverified". Never
   blended.
6. **Probabilities, not tips.** RG notice on all model answers.
7. **No dead ends.** Every error, gap, or limitation routes to the best
   remaining tier with disclosure.
8. **Pool key = service key (deferred).** When Mode B exists, the pool key
   receives identical KMS, canary and in-memory treatment as user keys, plus
   stricter budgets, and is never identifiable as an individual's key.
9. **(v5) Auth before spend.** No Anthropic call, no PuntingForm call and no
   SSE stream without a verified JWT. `healthz`/`readyz` and `GET /share/{id}`
   are the only unauthenticated routes, and neither can trigger spend.
10. **(v5) Subject-scoped resources.** Every key, watchlist, briefing,
    session, job id and replay buffer is stored under the authenticated
    subject and read back only by it. Ids are opaque and unguessable.
    Cross-user access is a 404, never a 403 that confirms existence.
11. **(v5) The key never travels through a log.** PuntingForm auth is a query
    parameter, so a logged URL *is* a logged key. No component logs a
    request URL. Every log sink (CloudWatch, Sentry, test capture) runs a
    redaction filter for `apiKey=` and for the stored last-four pattern, and
    a canary key must never appear in any sink (§15).
12. **(v5) Tool output is data.** PuntingForm fields, web pages, database
    text and the pipeline's own generated prose arrive delimited and marked
    untrusted, never in the instruction position. No free-text SQL tool. No
    link in an answer that is not in `citations[]`.
13. **(v5) Licensed data stays with its licensee.** Data fetched with a
    user's key is cached only for that key and served only to that user.
    No PF-derived block enters a share card or another user's briefing.
    Pipeline-derived PF facts (fields, scratchings) are shown to other users
    only within whatever the written PF answer allows.
14. **(v5) The chat cannot reach the pipeline's secrets or write its
    tables.** It reads `stride/chat/*` only, connects to Neon as a read-only
    role on the pooled host, and keeps all chat-owned state in its own
    DynamoDB tables. A compromised chat cannot corrupt a prediction, a
    ledger row or a schedule.
15. **(v5) Numbers come from tool results.** Generated text (`ai_insight`)
    is labelled generated and is not a number source.
16. **(v5) Content over exit codes.** Every scheduled chat job (read-model
    materialiser, briefing generator) declares a content postcondition and
    ships **its own never-ran watcher** in the PR that creates it. A job that
    exits 0 having written nothing is the bug (CLAUDE.md, "the silent no-op
    class"). v5 said "registers with `missing-run-watch`"; that watcher tests
    four ECS-shaped signals — a RUNNING task in an ECS family, a log stream
    under `/ecs/$FAMILY`, a STOPPED task, and a `stride_run_state` row —
    against a hardcoded table of due times. A zip Lambda has no family, logs
    to `/aws/lambda/*`, has no fixed due time (the tips file lands anywhere
    from mid-morning to late afternoon, or never), and §9.3 deliberately
    denies the chat role `stride_run_state`, which is the one signal it could
    otherwise have written. Either extend that watcher with a Lambda-shaped
    signal in the same PR, or give the chat its own. Do not claim reuse it
    cannot deliver (§23 finding 7).

---

## 5. Architecture (v5 — drawn on the estate that exists)

```
 OFFLINE — the existing AWS chain, unchanged (Sydney time)
 ┌───────────────────────────────────────────────────────────────────────────────────┐
 │ 04:00 racecard-collect (Fargate, pipeline PF key) ──┐                              │
 │ 04:15 baseline-night · 04:20 intelligence-build     │  S3 stride-evidence/artifacts/ │
 │ 05:30 consensus-agent (panel + Perplexity + Claude) ├─► racecard_<d>.json,          │
 │ 07:30 morning-odds (Betfair delayed, AU IP)         │  consensus_<d>.json,          │
 │ 08:05 tips-pipeline (3.4-6.2h at ~90 races) ────────┘  market_signals_<d>.json,     │
 │        └─ tips_<d>.json, written atomically; absent entirely on a quiet day         │
 │        └─ Neon: prediction_audit.final_win_prob, selections, race_schedule …        │
 └───────────────────────────────────────────────────────────────────────────────────┘
                     │ (polled, not evented — §22 finding 1; and the key is matched
                     │  EXACTLY, never by glob — §23 finding 1)
                     ▼
 NEW, OFFLINE ── chat-materialise (zip Lambda, EventBridge Scheduler, ~10-min cadence
                 through the racing day): reads run-state + the one relayed file whose
                 key matches ^artifacts/racecards/tips_\d{4}-\d{2}-\d{2}\.json$
                 ─► DynamoDB stride_chat_predictions
                 (postcondition: rows == runners in the file; registers with missing-run watch)
                 └─► scoring_done ─► briefing generator (Batch API) ─► stride_chat_briefings,
                                                                          stride_chat_watch_events

 ONLINE
 Browser ──► static client (CloudFront/S3)  ─or─  Express (thin proxy, bearer to the URL)
    │  Cognito hosted UI ──► JWT (OIDC)
    ▼
 POST /chat (SSE) ──► Lambda stride-chat  [FastAPI under Lambda Web Adapter, Function URL RESPONSE_STREAM,
                      python3.12 zip, reserved concurrency N, role: stride-chat-role]
                      ├─ auth: JWT vs Cognito JWKS; step-up for /keys
                      ├─ budgets: DynamoDB atomic counters (user/day, global/day, searches)
                      ├─ session memory + entity memory + replay buffer: DynamoDB (TTL)
                      ├─ orchestrator turn (Claude, tools, citations)
                      │    ├─ get_stride_tips / get_race_card / lookup_horse / query_results / get_performance
                      │    │      └─ DynamoDB read model + Neon (stride_chat_ro, pooled) + S3 artifacts
                      │    ├─ puntingform(endpoint enum) ─► executor: KMS decrypt in-process, allowlisted host,
                      │    │      0.4 s pacing, per-key TTL cache, PFAuthError → key_invalid
                      │    ├─ web_search / web_fetch (Anthropic server-side, citations)
                      │    └─ clarify (ends the turn with ranked candidates)
                      ├─ block assembly in code (prose from the turn; tables/guides/divergence
                      │    projected from tool results) — optional composer call behind a flag
                      ├─ lints: numbers ⊆ tool results; band consistency; citations allowlist
                      └─ blocks + provenance ─► SSE events with seq ids ─► client
 Key Service (module inside the same Lambda; the only importer of KMS):
   user keys (KMS envelope, per-user encryption context) · validation probe · tier inference · audit trail
 Secrets: stride/chat/anthropic · stride/chat/database_ro · stride/chat/proxy_bearer   — never stride/prod
```

What is deliberately *not* in the picture: API Gateway, an ALB, a NAT
gateway, Redis, a VPC attachment, a second container image, a Postgres
write role for the chat, and the `stride-jobs` image (multi-gigabyte, pulls
pandas at import; wrong artifact for an interactive endpoint — prior audit
§2.3).

---

## 6. Backend changes driven by UX (traceability)

| UX surface | Backend change (v5) |
|---|---|
| Value-first onboarding (§3.1) | Intent router works keyless; tools that need a key emit `capability_prompt` instead of failing; key probe reports only what PF can tell us |
| Block answers (§3.2) | Deterministic block assembly in code from tool results, with the turn's own text as `prose`; per-block schema validation (client-side range checks); per-block provenance mapping; `generated_note` for pipeline prose; an optional composer call behind a flag |
| Follow-ups (§3.3) | Session + entity memory in DynamoDB; follow-up generator constrained by capability profile × pipeline coverage |
| Entity resolution (§3.3) | `stride_chat_entity_aliases` seeded from `identity_normalization`, `horse_names`, `race_results_history`, PF names; clarify candidates |
| Time awareness (§3.4) | Calendar service over `race_schedule` + S3 racecard + PF `meetingslist`/`Conditions`/`Scratchings`; user timezone in prefs; grounding injected **after the cache breakpoint** |
| Drillable explanations (§3.5) | Read model carries `key_factors`, intelligence adjustments, `explain_prediction` output and stage values; factor → data pointers chained through the executor when keyed |
| Briefing & watchlists (§3.6) | Materialiser + briefing generator (Batch API) triggered by the tips JSON landing; `scoring_in_progress` state; DynamoDB tables |
| Errors as value (§3.7) | Error taxonomy returns `fallback_offer` payloads, not just codes |
| Speed (§3.8) | `GET /bootstrap` aggregate from DynamoDB/S3; SSE sequence ids + DynamoDB replay buffer; Lambda response streaming |
| Share (§3.10) | `POST /share` → allowlist sanitizer (model-derived blocks only) → immutable DynamoDB item; public read endpoint, rate-limited, `noindex` |
| Shared access (§3.1, deferred) | Key-selection layer own key → keyless tiers today; pool key layer added only with a second subscription |
| Model voice (§3.9) | Stage values from `prediction_stages` + `data_confidence` + bands fitted on observed frequency; tone mapping in the block assembler + band-consistency lint |

---

## 7. Orchestrator (v5)

Unchanged core: toolbelt, five intents, bounded loop, race resolution. The
prototype in `stride-app` `chat/02-agent-loop` … `chat/04-streaming` is the
reference behaviour; v5 fixes its shape where the API requires it.

**One turn = one orchestrator call chain, and by default no second model
call.**

- **Orchestrator call.** System prompt and tool list are the stable, cached
  prefix (`cache_control` on the last system block; tools before system in
  render order). Tools, all custom ones `strict: true` with
  `additionalProperties: false`:
  - `get_stride_tips(date, track?, race?)`, `get_race_card(date, track,
    race)`, `lookup_horse(name, n)`, `query_results(filters)`,
    `get_performance(window, group_by)` — the names the eval suite already
    asserts; backed by the DynamoDB read model, the read-only Neon role and
    the S3 artifacts; zero user quota.
  - `puntingform(endpoint, …)` with an `endpoint` enum over the `pf_client`
    accessors (`meetings_for_date`, `meeting_detail`, `results_for_meeting`,
    `scratchings`, `conditions`, `speedmaps_for_meeting`,
    `ratings_for_meeting`, `strike_rates`). Never raw REST. Keyless →
    returns a `key_not_connected` result the planner turns into a
    `capability_prompt`.

    **`pf_client` cannot be used as it stands, and v5 said it could** (§23
    finding 10). Three defects, all in the same file. `_api_key()` reads the
    process-global `PUNTINGFORM_API_KEY` (`pf_client.py:58`), so a per-user
    key means mutating the process environment mid-request inside a
    concurrent ASGI app: it races between interleaved users, leaves plaintext
    in the environment, and breaks both §9.2's "nothing caches the plaintext
    across requests" and §9.1's stated proof that no chat code path reads
    that variable. `get()` sleeps 0.4 s after every call and 2 s upward on
    retry (`:84`), which paces the **process**, not the key — so §10.3's
    "0.4 s pacing per key" does not exist today, and under the Lambda Web
    Adapter it stalls every co-resident request. And the ~31-day wall raises
    a generic `PFError`, so §12's `window_exceeded` has nothing to key on.
    The fix is one pipeline PR, byte-identical by default: an explicit
    `api_key` parameter, an async transport with per-key pacing, and a
    `PFWindowError` subclass. It is a prerequisite for any keyed tool
    (§16B).
  - `web_search` (`web_search_20260209`) and `web_fetch`
    (`web_fetch_20260209`), server-side; `max_uses` set per turn.
  - `clarify(candidates[])` — a client-side tool that ends the turn.
  - No `run_readonly_sql`. (An operator-only escape hatch may exist off by
    default, as the prior audit allowed; it is not a product tool.)
- **Loop rules.** At most 8 rounds. Parallel `tool_use` blocks are executed
  concurrently and *all* `tool_result`s return in one user message; a failed
  tool returns `is_error: true` rather than being dropped. `stop_reason`
  handling: `max_tokens` → retry once with a larger cap; `refusal` → the
  fixed refusal answer, no retry; `pause_turn` → continue. **`max_tokens` is
  64,000 with streaming** — a backstop, not a tuning knob. The model never
  sees it, and hitting it truncates mid-thought and costs a retry: Anthropic's
  published coding runs had a 16,384 cap end 15 % of Claude Opus 5's attempts,
  none of them solved, so the capped runs spent less per attempt and bought
  proportionally fewer completions. The prototype's 4,000 is far too low and
  v5's 16,000 was still a lowball. To shorten what the user reads, specify the
  output shape in the prompt; to shorten reasoning, use `effort` (§10.5).
  Treat `stop_reason: "max_tokens"` as a failed turn, not something to retry
  at the same cap. Adaptive thinking on; no `temperature`; no prefill.
- **Volatile context** (`now_in_user_tz`, `calendar_state`,
  `session_entities`, `capability_profile`, `pipeline_coverage`) goes after
  the cache breakpoint (§3.4). The per-turn usage line records
  `cache_read_input_tokens`; a zero across repeated turns is a bug, not a
  cost.
- **Block assembly — deterministic first, a second model call only if
  measurement demands it.** v5 mandated a second Claude call (a "composer")
  because `output_config.format` and citations cannot appear in one request.
  That is true, and it was the wrong conclusion: it buys structured output at
  the price of re-sending every tool result to a second model, which is the
  single largest avoidable cost in this design (§22 finding 2, §10.2). Every
  block except `prose` is a deterministic projection of a tool result — a
  field table is rows, a form guide is starts, a divergence block is
  arithmetic — and v4 itself said "deterministic table rendering; LLM only
  writes prose around grounded numbers". So the **default is one model call**:
  the orchestrator's final text becomes the `prose` block, code builds every
  other block from the tool results it already holds, and the citation list
  comes from the response's citation blocks. A composer call remains
  available behind a flag for the cases where narrative ordering across
  several tool results genuinely needs a model; ship the deterministic path
  first, measure quality against the evals, and turn the composer on only if
  the numbers say it earns US$0.09 a turn. If it is on: no tools,
  `output_config.format` for the block schema (no recursion, no numeric
  min/max — validated client-side, as the SDK does), one retry on schema
  failure, then a `prose` + `notice` fallback, never a raw dump.
- **Lints before streaming.** (1) Every numeric token in `prose` matches a
  number in a tool result or a block cell (rounding tolerance). (2) Band
  consistency: the adjectives used for a runner match the fitted band for
  its `win_pct`. (3) Every link is in `citations[]` (the existing
  `chatCitations` normalisation, shared with the client). A lint failure
  downgrades the answer (drop the sentence, add a `notice`), and is counted.
- **Tool results are untrusted.** Each result is wrapped in a delimited
  block tagged with its source and trust level; the system prompt says so
  (the prototype prompt already carries the rule — keep its wording, bump
  the version).
- **Session memory**: DynamoDB, session id → last 12 turns + entity memory,
  30-minute TTL (the existing constants).
- **Keyless planning** and the **follow-up generator** as v4.

---

## 8. Model layer (v5)

### 8.1 What the pipeline already produces, per runner

| Field (read model name) | Source today | Semantics |
|---|---|---|
| `win_pct` | `run_tips_pipeline.calibrate_and_score` → `tips_<d>.json`, `prediction_audit.final_win_prob` | Published probability, **market-anchored** (model weight 0.30–0.80 by price) |
| `raw_model_pct` | `rawModelProb` → JSON only | The model's own opinion: isotonic MC blended with the ML ensemble, before the anchor |
| `place_pct` | MC place probability → JSON; `prediction_audit.predicted_place_prob` (MC stage) | |
| `market_fair_odds` | `fairOdds = 100 / true_market` (`run_tips_pipeline.py:985`) | The de-vigged **market** price |
| `model_fair_odds` | derived: `100 / raw_model_pct` | The model's price; **not stored today** — derived in the read model and named so it cannot be confused with the above |
| `edge_pct` | `win_pct − true_market` | Edge on the calibrated basis |
| `price`, `price_source`, `captured_at` | Betfair via `betfair_enrich_racecard.py`; `runner_odds_snapshots` | Delayed data; provenance rides with the number |
| stage values | `prediction_stages`: `base_xgb`, `base_lightgbm`, `base_catboost`, `ensemble`, `mc_raw`, `mc_recalibrated`, `sectional_blend`, `ml_adjustment`, `combined_adjustment`, `wrapper_pre_calibration`, `wrapper_model_blend`, `market_context_probability`, `selection_score`, `final_decision` | `base_*` are **raw booster scores unless `STRIDE_ML_APPLY_ISOTONIC` is on** |
| `key_factors`, `ai_insight`, `confidence`, `staking`, convergence fields | JSON | `ai_insight` is generated prose |
| release context | `output["release"]` = `{release_id, manifest_sha256, source_commit, image_digest}` written by `run_tips_pipeline.py:3695` from `release_manifest.release_context_from_env`; `source_commit` is `STRIDE_IMAGE_SHA`, stamped into the container | The `model_version` v4 wanted. `release_id` reads `UNRESOLVED` until the release-manifest slot is activated (decision-learning Phase 8), so the read model versions on `source_commit` + `scored_at` until then, exposed as `model_release_id` |

### 8.2 What is missing and how it is added

- **`data_confidence`** (new artifact field, pipeline PR): the share of
  `FEATURE_COLUMNS` the served row actually carried, computed where the row
  is built (`serve_features.py` / `ml_model.prepare_features`), counting
  NaN-by-contract (`nan_contract.PHASE2_FEATURES`) and absent inputs as
  missing, and **excluding the by-design constants** (the 15
  `ZERO_AT_SERVE` features are constants until `STRIDE_SERVE_LIVE_FEATURES`
  flips — a runner must not read "complete" because of them). Because that
  exclusion set changes when the flag flips, the field carries a
  `data_confidence_basis` version alongside it; without one, values either
  side of the flip are silently incomparable. Plus three named flags:
  `first_up`, `no_trials`, `no_sectionals`. Shipped behind a default-off
  flag, byte-identical when off, with its own test, in its own PR — the house
  rule of one change at a time.

  **This is the last thing built, not a prerequisite** (§22 finding 6). It is
  the only change in this entire plan that touches the live scoring hot path:
  `serve_features.py` and `ml_model.prepare_features` run inside the 10:00
  job, and a flag cannot protect against an import-time error — the
  Dockerfile's own comment records a missing module turning that job into 31
  per-race failures and a card with zero selections. Until it exists, the
  humility voice runs on what the artifact already carries per runner:
  `is_first_up`, `days_since_run`, absent sectionals, and the `NaN` contract's
  own missingness. That is most of the signal for none of the risk.
- **Calibrated member probabilities**: not a chat change. They arrive when
  `STRIDE_ML_APPLY_ISOTONIC` is promoted after its shadow week and the
  artifact carries all three calibrators. Until then §3.9's rank-agreement
  wording applies.
- **Tone bands**: fitted on **observed outcome frequency**, not on quantiles
  as v5 said (§22 finding 5). A quantile describes where a number sits in the
  distribution; a band claims what it means, and "strongly fancied" has to
  cash out as "wins about this often". `prediction_audit` carries both sides
  — `final_win_prob` written per runner by
  `run_tips_pipeline.store_final_probs_in_audit`, and `won` / `actual_position`
  / `starting_price` written by `auto_results_collector` on settlement — so
  the fit is a grouped strike rate against predicted probability, per
  field-size bucket, which is a calibration curve and reads as one. Two
  guards, both borrowed from the repo's own practice: the write is a
  **non-fatal UPDATE** that only lands where an MC-stage audit row already
  exists, so coverage is measured before anything is fitted rather than
  assumed; and the fit refuses below a minimum row count, exactly as
  `STRIDE_CAL_MIN_COVERAGE` (500) gates the recorded-source calibrator.
  Stored as a versioned JSON that the block assembler and the lint both read;
  a prod read, so operator approval per the house rule.
- **Per-runner SHAP**: deferred (§3.5).

### 8.3 The read model

`stride_chat_predictions` (DynamoDB): partition key `race_date#track_key`,
sort key `race_number#runner_key`; item = the runner fields above + stages
+ `data_confidence` + `model_release_id` + `scored_at` + `scratched` +
`tracks_merged_so_far`; a GSI on `runner_key` for `lookup_horse`. Written by
`chat-materialise`, which **polls** rather than subscribing: an EventBridge
Scheduler rule on the estate's existing pattern, firing every ten minutes
through the racing day, that compares the relayed tips file and the
`stride_run_state` row against what it has already materialised. It is
idempotent on re-read, and its postcondition is
`items_written == runners_in_file`.

**It matches the object key exactly, against
`^artifacts/racecards/tips_\d{4}-\d{2}-\d{2}\.json$`, and exits on anything
else** (§23 finding 1). The relay uploads by glob —
`_sync_up("racecards", "tips_*.json")` at `infra/jobs/handler.py:266` — and
three other files share that prefix and suffix: `tips_<date>_candidate.json`
and `tips_<date>_cloudproof.json` from proof runs (`handler.py:1169`), and
`tips_<date>.pre_merge_backup_<ts>.json` written beside the canonical file on
every track-filtered run (`run_tips_pipeline.py:3737`). A candidate file is
scored with a **non-production ensemble** under `STRIDE_ENSEMBLE_ARTIFACT`
and can be run for an arbitrary date. Publishing one to users, stamped with a
release id, would be the worst failure this product can have, and
`handler.py:1165` already says in prose why the suffix exists: "so it can
never be mistaken for the real `tips_<date>.json`". A prefix-and-suffix
filter, on S3 events or on a glob, cannot make that distinction.

**Quiet days** are a state, not a stall. On a day with no target-track racing
the tips job returns early and never writes the file (`handler.py:953-959`,
about one day in three), so a `scoring_in_progress` flag that clears only on
the file's arrival would latch forever and its watcher would fire an issue
every quiet day. The relay already uploads `quiet_<date>.json`
(`handler.py:507`): the materialiser reads it and sets `quiet_day`, which the
chat says plainly (§23 finding 6). It does **not** use
an S3 event notification: `PutBucketNotificationConfiguration` is a
full-replacement API on a bucket the retrain-gate evidence store shares, and
full-replacement calls on shared live state are the hazard class `CLAUDE.md`
names for `update-schedule`. The bucket carries no notification
configuration today (`infra/02b_evidence_bucket.sh` sets versioning and
public-access-block only), so the first such call would work and the second
consumer's would silently erase the first. Polling costs a few cents a month
and touches nothing the pipeline owns. `scoring_in_progress` is
true for a date between the racecard landing and the tips JSON landing.

Why DynamoDB and not a `predictions` table in Neon: the chat then needs no
Postgres write role at all (§4 inv. 14), the query pattern is by
race/date/horse, and the table is in the estate. If the operator prefers
SQL for chat-owned state, the alternative is a dedicated `stride_chat`
schema with its own role; the decision is §20 Q4.

### 8.4 Divergence

`divergence = raw_model_pct − true_market_pct`, shown only where a real
price exists (`has_real_market_odds`), with `price_source` and
`captured_at`, the unformed-book fence applied, and the RG notice. It is a
data observation, not a recommendation.

### 8.5 Rules for touching the pipeline

Every pipeline addition above follows the pipeline's own rules, not this
plan's: a staged artifact never promoted directly, one change at a time,
flags delivered through the secrets path, and **never** a change to the
consensus agent, convergence tiers or franking thresholds (CLAUDE.md).

---

## 9. Security design (v5 — full, replaces v4's deltas)

### 9.1 Threat model

| Asset | Threat | Control | Proof (test in §15) |
|---|---|---|---|
| User PF key at rest | Table dump, backup leak | KMS envelope (§9.2); ciphertext + wrapped data key in DynamoDB; plaintext never persisted | Item inspection shows no plaintext; canary never in any sink |
| User PF key in flight inside Stride | It is a **query parameter**: any URL log, proxy log, Sentry breadcrumb or exception string with the URL leaks it | One function builds the URL; no component logs URLs; redaction filter on every handler; Sentry `beforeSend` scrub; a CloudWatch metric filter on `apiKey=` that alarms if it ever matches | Canary-key turn: pattern absent from logs, responses, traces |
| User PF key in LLM context | Prompt or tool argument carries it | Tool schemas have no key field; executor resolves the key from the subject, never from the model | Test asserts every request body sent to Anthropic is free of the canary |
| Anthropic spend | Unauthenticated or scripted use | JWT required before any model call; per-user and global daily caps; reserved concurrency; a **separate Anthropic key in its own Console workspace with a spend limit**; alarms at 80 % | Cap trip returns `budget_exhausted` with zero model calls (counted by the test double) |
| PuntingForm quota / ban | Loops, abuse | 8-round cap; per-turn PF call cap; 0.4 s pacing; per-key TTL cache; own-key only (no shared key to ban) | Loop test: N rounds ⇒ ≤ M PF calls |
| Prompt injection | PF text fields, web pages, DB prose, `ai_insight` | Delimited, trust-tagged tool results; no SQL tool; strict schemas; links only from `citations[]`; refusal on off-domain | The 16-case injection suite + "horse named *Ignore Previous Instructions*" in CI |
| Cross-user access (IDOR) | Guessable ids, missing ownership checks | Every item keyed by subject; opaque ids; SSE resume owner-checked | User A → user B's key/watchlist/job/briefing ⇒ 404 |
| SSRF | Model or user steers the executor | Constant host; no URL parameter anywhere; `web_fetch` is Anthropic-side | Executor refuses any other host in a unit test |
| Share leakage | User data or licensed data in a public card | Allowlist sanitizer: model-derived block types and fields only; ≥ 128-bit ids; rate limit; `noindex` | Property test: arbitrary answer ⇒ share ⊆ allowlist; PF block ⇒ refused |
| Licensed data across tenants | Shared cache serves A's PF data to B | Cache keyed by key id (hash of the key record id, not the key); no cross-user PF cache | B's request after A's identical request still calls upstream |
| Pipeline secrets and data | Chat compromise reaches Betfair creds, the write DB role, models | Chat role reads `stride/chat/*` only; read-only Neon role on the pooled host with `default_transaction_read_only`, `statement_timeout`; no access to `stride/prod`, the models bucket, `stride_run_state` | IAM: `GetSecretValue` on `stride/prod` ⇒ AccessDenied; `INSERT` as the RO role ⇒ error |
| Transport | Downgraded TLS to Neon | `sslmode=require&channel_binding=require` (as `.env.example`); never `rejectUnauthorized: false` (the Express `db.ts` does this today and must not be copied) | Config test |
| Public repository | Real keys in fixtures, prompts with secrets | Canary-shaped test keys; secrets grep in CI (the OPS pack rule); `.env` and `.claude/` ignored | CI step |
| Key operations | Session theft ⇒ key replaced or deleted | Step-up: `auth_time` ≤ 5 min or MFA challenge on `POST/DELETE /keys` | Stale token ⇒ `step_up_required` |
| Availability coupling | Chat load exhausts the pipeline's PF key | Chat never uses the Stride key (Mode B deferred) | Static: no code path reads `PUNTINGFORM_API_KEY` in the chat |
| Personal data | Emails, keys, chat history | Minimal PII (Cognito `sub`, email); one-click delete removes keys, sessions, watchlists, briefings and writes an audit record; retention stated in the privacy policy | Delete ⇒ zero items for the subject |
| Gambling context | Product reads as tipping/inducement | RG notice on model surfaces; BetStop/self-exclusion link; no inducement language; the voice rules | Tone evals; legal review (§16, **unverified law**) |

### 9.2 Key Service (the only module that talks to KMS)

- One customer-managed KMS key, alias `alias/stride-chat-keys`, annual
  rotation on. Key policy grants `kms:GenerateDataKey` and `kms:Decrypt` to
  `stride-chat-role` only, with a condition on
  `kms:EncryptionContext:purpose = "puntingform"`.
- **Store**: `GenerateDataKey(AES_256, EncryptionContext={user_id, purpose})`
  → encrypt the API key with the plaintext data key (AES-GCM) → persist
  `{ciphertext, wrapped_data_key, kms_key_id, created_at, last4,
  validated_at, tier_inferred, window_days}`; discard the plaintext data key.
- **Use**: `Decrypt(wrapped_data_key, EncryptionContext)` → unwrap → decrypt
  → the executor uses the key for this request only; nothing caches the
  plaintext across requests. CloudTrail therefore records one `Decrypt` per
  keyed request with the user's context — the audit trail v4 asked for.
- **Delete**: remove the item, write an audit record; CloudTrail shows no
  later `Decrypt` for that context.
- **Local dev provider**: AES-GCM with a key from the environment; refuses
  to construct when `ENV=production`; same interface, same tests.
- **Service key**: the pipeline's PF key stays in `stride/prod` and is never
  referenced by chat code. Mode B's pool key, when it exists, is a second
  subscription stored under `{purpose: "shared_pool"}` with the same
  mechanics and stricter budgets.
- Cost: approximately US$1/month per key plus US$0.03 per 10,000 requests
  with a monthly free allowance (**pricing unverified from this session**).

### 9.3 Secrets and IAM for the chat

`stride-chat-role`: `secretsmanager:GetSecretValue` on the three
`stride/chat/*` ARNs; `kms:*` as above; `dynamodb:GetItem/PutItem/
UpdateItem/Query/DeleteItem` on the `stride_chat_*` table ARNs;
`s3:GetObject` on `stride-evidence-<acct>/artifacts/*`; `sns:Publish` on
`stride-alerts`; `logs:*` through the basic execution policy. Nothing on
`stride/prod`, the models bucket, `stride_run_state`, ECS, or IAM. The
Anthropic key for the chat is a **different key** from the pipeline's, in
its own Console workspace with a spend limit, so it can be revoked without
stopping the 05:30 consensus job.

### 9.4 Logging

One structured line per turn: subject hash, session id, turn id, model,
input/output/cache tokens, tool names and durations, PF calls made,
searches made, budget counters after, lint outcomes, `request-id`. Never
the prompt, never a tool payload, never a URL. DEBUG-level payload capture
exists only in local development and is scrubbed by the same filter.
CloudWatch retention 60 days like the rest of the estate; low-cardinality
metrics only (turns, tool errors, Anthropic 4xx/5xx, budget trips) — never
per-race or per-horse dimensions (`14_TESTING_AND_OBSERVABILITY.md`).

### 9.5 What v4 had that stands

Share privacy (now an allowlist), background jobs never decrypting user
keys (briefings are model + cached public data only), anti-extraction caps
on chat, shares and briefing payloads, the canary suite extended to the
sanitizer paths.

---

## 10. Budgets (v5)

### 10.1 LLM spend (new — v4 had no LLM budget at all)

| Control | Launch value (config, not code) | Enforcement |
|---|---|---|
| Per-user turns per day | 40 | DynamoDB atomic counter `user#<sub>#<date>` |
| Global turns per day | 500 | counter `global#<date>`; alarm at 80 % |
| Rounds per turn | 8 | loop cap |
| Web searches per turn / per day | 3 / 300 | `max_uses`; counter |
| Concurrency | reserved concurrency 3 at launch, raised deliberately, never implicitly | bounds parallel Anthropic calls and Neon connections at once. **Three is a spend cap, not a capacity plan**: a streaming turn holds an execution for tens of seconds, and §3.6 deliberately creates a simultaneous open by notifying every opted-in user the moment scoring lands. At an audience of one that is irrelevant; before Phase 4 it must be re-set against the measured turn duration from §15.3, or the product queues at exactly the moment it promises freshness (§22 finding 3) |
| Console spend limit | set on the chat's own key | hard stop independent of our code |

Past a cap the answer is the `budget_exhausted` error with a real
alternative ("come back tomorrow" is not one; "here is today's coverage
from the read model, no model call needed" is), and **no model call is
made**.

### 10.2 Cost model (why the caps exist)

Per-turn worked example on `claude-opus-5`, from the prior audit §7 (a
two-round tool turn, 15 k cached prefix, 5 k fresh input, 3.5 k output and
thinking): about **US$0.12**, plus US$0.01 per web search.

v5 quoted that figure while also mandating a second model call, which the
figure does not include (§22 finding 2). A composer re-ingesting ~8 k tokens
of tool results and writing ~2 k adds about **US$0.09** — a 75 % increase, to
roughly US$0.21 a turn. Deterministic block assembly (§7) avoids it.

| Turns/day | One call (deterministic blocks) | Two calls (composer on) |
|---:|---:|---:|
| 50 | ~US$180/mo | ~US$315/mo |
| 200 | ~US$720/mo | ~US$1,260/mo |
| 1,000 | ~US$3,600/mo | ~US$6,300/mo |

These are estimates from a worked example, not measurements; Tier 1 (§15.2)
records the real number before any cap is set. They are also **pre-optimisation**
figures: §10.5 ranks the levers that act on them, the largest of which is worth
a measured 16x on the dominant input component and costs nothing in quality.

The AWS side needs more care than v5's "a few dollars" (§23 finding 20).
`infra/03_notifications.sh` records steady state at about US$0.03 a day, so
roughly US$0.90 a month, and sets the tripwire at US$20 as a runaway
detector calibrated to that. This plan adds a customer-managed KMS key (§9.2
itself puts it near US$1 a month), a dozen DynamoDB tables, CloudFront and S3
hosting, Cognito at Phase 4, and another log group at 60-day retention.
Plausibly five to ten times current steady state — still far under US$20, but
enough to make the FORECASTED-over-100 % notification fire routinely and
desensitise the operator's only AWS runaway detector. **Set a target monthly
AWS figure for the chat before Phase 2, and decide in §20 whether the
tripwire moves with it.** A tripwire that cries wolf is worse than none,
because the estate has exactly one and the daily pipeline depends on it.

And the tripwire never sees the column that matters at all: see the spend
posture note at the top. A Sonnet 5 composer roughly halves the composer's share, if the
composer is on at all; measure before switching.

### 10.3 PuntingForm

- Own-key traffic: 0.4 s pacing **per key**, which requires the `pf_client`
  change in §7 — today's pacing is per process; per-turn cap on PF calls; a
  per-key in-memory TTL cache (scratchings 60 s, conditions 5 min, meeting
  detail and results 10 min); the answer tells the user how many calls the
  turn made (we can count our own calls even though PF reports no quota).
- Pool key (deferred): global token bucket sized from **measured** limits
  (a probe, recorded in `PUNTINGFORM_MIGRATION.md` like the wall
  measurements), hard daily ceiling with alerting, small per-user daily cap,
  cache-first, auto-cutover to keyless tiers.

### 10.4 Other

Briefings: cached per user per day, zero PF calls, generated through the
Batch API. `GET /bootstrap`: shared part cacheable per minute; per-user part
never in a shared cache. SSE replay: last K events per job, short TTL.

### 10.5 Cost levers, ranked — free wins before tradeoffs

Ranked by savings ceiling, **not** application order. Everything here is a
code-read estimate: no chat code exists yet, so there are no `usage` logs and
no measured baseline. The ceilings are therefore relative buckets, and the
Tier 1 run (§15.2) is what turns them into numbers.

| # | Lever | Type | Ceiling | Basis |
|---|---|---|---|---|
| 1 | Project tool results to the fields the answer needs | free win | **largest** | Measured on `examples/sample_race.json` |
| 2 | Answer templated questions from the read model with no model call | free win | large | Design |
| 3 | Prompt caching, two breakpoints (§14) | free win | large | Prefix measured at 1.5–6 k tokens |
| 4 | Deterministic block assembly instead of a composer call | free win | medium | §22 finding 2, already applied |
| 5 | Batch API for briefings | free win | medium, on that class only | Guide: 50 % on every token |
| 6 | `max_tokens` as a backstop at 64,000 | free win | small, and it is a *saving* not a cost | §7 |
| 7 | Effort sweep | **tradeoff — needs an eval** | large | Published curves |
| 8 | Task budgets | **tradeoff — needs an eval** | medium | Published: ~18 % for ~2.7 points |
| 9 | Model choice | **tradeoff — needs an eval** | large | Last, deliberately |

**1. Tool-result projection is the biggest lever, and it is free.** One
9-runner race in the day artifact is 48,305 bytes, roughly 12,000 tokens.
**71 % of that is prose the pipeline's own LLM wrote** (`ai_insight` and
`brief_assessment`), and the file carries 16 runner objects for a 9-horse
field because the same runner repeats across `top_picks`, `raw_model_leader`,
`bet_pick`, `coverage_pick`, `primary_pick` and `full_field`. Handing that to
Claude raw would spend 12,000 input tokens to convey about 760 tokens of
racing. Projected to one row per runner with the fields an answer actually
uses, the same race is 3,046 bytes — a **16x reduction on the dominant input
component**, before any other lever runs. Rules that follow from it:

- Tools return projections, never raw artifact objects. `ai_insight` reaches
  the model only when the user asked for the pipeline's own commentary, and
  then for one runner, not a field.
- Every list tool takes `limit` and `fields`. Narrow accessors over data
  dumps.
- Stage values (§8.1) are a dozen floats per runner; include them only for
  the runners under discussion, not the field.
- Cap every tool result and say so in the result when it truncates.

**2. Not every question needs a model.** "What is racing today", "what did
you rate her in race 5", "how did last week's tips go" are read-model lookups
with a fixed answer shape. A deterministic classifier that recognises the top
templated questions and renders blocks directly costs zero tokens and returns
in milliseconds. Anything it does not recognise falls through to the
orchestrator unchanged. This is the same principle as §10.1's
`budget_exhausted` fallback, promoted from a degraded mode to the fast path,
and it is measured as **cost per completed task** rather than per call.

**3. Caching** is §14. Note the honest ceiling: this workload's input is
mostly per-request payload, which can never cache. The published 2.5x to 3.7x
agent-loop figures come from workloads with large stable prefixes. Here the
win is concentrated in the loop's repeated tool results, which is why §14 puts
a breakpoint after them.

**7–9. The tradeoffs stay proposals until an eval exists.** There is no
harness that scores the block contract today (§23 finding 16), so a saving
cannot be told apart from a regression. Once there is one, sweep in this
order, one change at a time, cells byte-identical except the variable:
effort first, then task budgets, then model. Two published expectations worth
carrying into that sweep: on research and knowledge workloads the effort
curve is nearly flat, with `medium` matching the default's accuracy at 70–85 %
of its cost, and a chat workload is closer to that shape than to coding; and
before building any cheap-model cascade, price the strong model at lower
effort on the same tasks, because a cascade also forfeits cache reuse across
its models.

**Levers deliberately skipped, so they are not re-litigated:** context editing
and compaction (the loop is capped at 8 rounds and sessions hold 12 messages,
so neither trigger is reached, and the guide records context editing costing
more than it saved); tool search with `defer_loading` (schemas measure about
800 tokens, far under the ~10 k where the search step pays); programmatic tool
calling (incompatible with `strict: true`, which is load-bearing here); the
advisor and orchestrator patterns (no bulk fan-out — a chat turn is one
dependent chain); and fast mode (a premium, the opposite direction).

**What would turn these estimates into measurements**, in order of how cheap
they are: the Console usage figures for the Anthropic key the consensus agent
already spends on, which is a real baseline for a real workload in this
account; an Admin API key, which makes the usage and cost reports readable
directly and costs no tokens; and, once any chat code runs, the per-turn
`usage` line §9.4 already specifies, which carries all four token counts.

---

## 11. Provenance & honesty gradient

Degradation ladder, each step disclosed: **stride_model → puntingform →
web_fallback → model_unverified → honest "couldn't find it."** Mode A users
reach tier 2 with their own key; keyless users start at tier 1/3. Hard
rules stand: no verifiable link from memory; citations never fabricated;
fallback opt-out per query.

Answer envelope v5 (abridged; the two fair prices are named, the release id
and the price provenance travel with the numbers):

```json
{
  "blocks": [
    { "type": "prose", "text": "My model rates Her Majesty 31% before the market anchor (a fair $3.20 on that view); the de-vigged market has her at 24% ($4.10). First-up record, the weight drop and the barrier are doing the work[M1]." },
    { "type": "field_table", "race_id": "2026-09-12#randwick#5",
      "rows": [ { "runner": "Her Majesty", "win_pct": 28.4, "raw_model_pct": 31.0,
                  "model_fair_odds": 3.23, "market_fair_odds": 4.10,
                  "price": 3.90, "price_source": "betfair_delayed", "captured_at": "2026-09-12T00:45:10Z",
                  "top_factor": "first_up_record", "data_confidence": 0.71 } ],
      "note": "Release 2026-09-08-a, scored 09:12 AEST", "stale": false },
    { "type": "sources", "items": [
        { "id": "M1", "type": "stride_model", "model_release_id": "2026-09-08-a", "scored_at": "..." } ] }
  ],
  "follow_ups": ["Has she won at 1600m?", "Where does the model disagree with the market today?"],
  "provenance": { "tier": "stride_model", "fallback_reason": "none" },
  "responsible_gambling_notice": true,
  "confidence": "high"
}
```

---

## 12. Error taxonomy (extended)

| code | action |
|---|---|
| `auth_required` **(v5)** | `sign_in` |
| `step_up_required` **(v5)** | `reauthenticate` (key operations only) |
| `budget_exhausted` **(v5)** | `fallback_offer` from the read model, no model call; `retry_after` = **the counter's own rollover**, not "midnight in the user's timezone" as v5 said. The counter is keyed `scope#date`; if that date is UTC and the user is in Sydney, local midnight leaves them blocked another ten hours (§23 finding 19). Either key the counter on the user's local date or return the key's rollover instant |
| `key_not_connected` | `connect_key` (renders `capability_prompt`) |
| `key_invalid` (401/403, `PFAuthError`) | `reenter_key`, with PuntingForm's own reason text (the client already carries it) |
| `rate_limited` (429) | `wait_retry_after` + `fallback_offer` |
| `tier_restricted` (403 on a tier-gated endpoint) | `show_upgrade_note` + fallback answer |
| `window_exceeded` **(v5)** (the ~31-day wall, HTTP 400) | answer from `race_results_history` where it reaches; disclose |
| `scoring_in_progress` **(v5)** | show what has landed, say what has not, `retry_after` |
| `licence_restricted` **(v5)** | feature withheld pending clearance; say so plainly |
| `plan_failed` | `rephrase` |
| `ambiguous_query` / `race_not_found` | `clarify` with options |
| `no_prediction` / `prediction_stale` | answer via other tiers, disclosed |
| `upstream_error` | `retry` |
| `no_data_no_fallback` | `enable_fallback` |

---

## 13. API surface & data model (v5)

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /bootstrap` | JWT | Meetings + model coverage + `scoring_in_progress` + briefing status + access mode (`own_key` / `keyless`) in one call |
| `POST /keys`, `GET /keys/status`, `DELETE /keys` | JWT + step-up | Store / probe / delete; status returns last four characters, tier inference, validated_at — never the key |
| `POST /chat` (SSE), `GET /chat/{job_id}/events?after={seq}` | JWT, owner-checked | Turn + replay |
| `GET /suggestions` | JWT | Capability × coverage aware |
| `GET /races/today` | JWT | Meetings/races with coverage flags |
| `GET /briefing/today` | JWT | Personal briefing |
| `POST /watchlist`, `DELETE /watchlist/{id}`, `GET /watchlist` | JWT | Follow horses/tracks |
| `POST /share` | JWT | Sanitized immutable card |
| `GET /share/{id}` | none, rate-limited, `noindex` | Public read |
| `GET /healthz`, `/readyz` | none | Ops; `readyz` includes the cold-start model preflight result |

**Chat-owned state — DynamoDB, on demand, TTL where noted**

| Table | Key | Notes |
|---|---|---|
| `stride_chat_users` | `sub` | prefs: timezone, favourite tracks, briefing opt-in |
| `stride_chat_keys` | `sub` | KMS envelope fields (§9.2) |
| `stride_chat_key_audit` | `sub`, `ts` | store / probe / delete / decrypt-failure events |
| `stride_chat_sessions` | `session_id` | last 12 turns + entity memory; owner `sub`; `expires_at` enforced **on read** |
| `stride_chat_events` | `job_id`, `seq` | SSE replay buffer; owner `sub`; `expires_at` enforced on read |
| `stride_chat_budgets` | `scope#date` | atomic counters |
| `stride_chat_predictions` | `race_date#track_key`, `race_number#runner_key`; GSI `runner_key` | the read model (§8.3) |
| `stride_chat_entity_aliases` | `entity_type#alias` | canonical id |
| `stride_chat_watchlists` | `sub`, `entity_type#entity_id` | |
| `stride_chat_briefings` | `sub`, `date` | payload + generated_at |
| `stride_chat_watch_events` | `sub`, `ts` | race/runner/kind/payload/read_at |
| `stride_chat_shares` | `share_id` | sanitized blocks + model provenance only |
| `stride_chat_query_log` | `sub`, `ts` (TTL 90 d) | the per-turn usage line, for the operator |

**DynamoDB TTL is cleanup, not expiry** (§23 finding 19). Deletion is
best-effort and routinely lags by hours, so an item stays readable past its
timestamp. Every table above that v5 described as "TTL 30 min" or "TTL 1 h"
stores an explicit `expires_at` that the code checks on read; the TTL
attribute is set as well, purely so storage does not grow.

**Pipeline data — Neon, read-only** through `stride_chat_ro` on the
**pooled** host (`-pooler`, PgBouncer transaction mode: no session `SET`,
no `LISTEN`, protocol-level prepared statements only — verified 2026-09-09
from Neon's docs source). Grants as in
`migrations/app/chat_readonly_role.sql` on `stride-app` branch `chat/01-hygiene` (`selections`,
`race_results_history`, `sectional_times`, `franking_scores`,
`blackbook_entries`, `stride_tip_results`, `race_schedule`,
`prediction_audit`, `selection_results*`, `convergence_output`, …) plus
`runner_odds_snapshots`, `betfair_odds_snapshots` and `consensus_scores`,
with `default_transaction_read_only = on`, a `statement_timeout` (the branch
file says 5 s, the prior audit 15 s — pick one and prove it against
`get_performance`, the heaviest query) and
`idle_in_transaction_session_timeout = '10s'`. Applied once through
`apply-migration` with the typed `APPLY`.

**Not created**: the v4 Postgres tables `users`, `api_keys`, `service_keys`,
`audit_log`, `query_log`, `cache`, `model_registry`, `races`, `predictions`,
`results_settlements`. Identity lives in Cognito, chat state in DynamoDB,
predictions in the read model, settlement in the pipeline's own
`selection_ledger` / `prediction_audit`.

---

## 14. Caching (v5.2)

- **Prompt cache — two breakpoints, and it stays on permanently.**
  1. *Explicit, on the static prefix*: tool schemas then the system prompt,
     rendered in that order and byte-frozen. Measured from the prototype this
     is about 1,500 tokens today and 4,000 to 6,000 once v5's track profiles,
     band vocabulary and voice rules are added — comfortably over Claude Opus
     5's 512-token minimum. Because every user shares one system prompt and
     one tool list, and caches are per workspace, **any user's turn keeps the
     prefix warm for everyone**. That is the one place this workload's shape
     helps rather than hurts.
  2. *Rolling, after the tool results of each round*: the loop resends the
     whole growing conversation every round, so with an 8-round cap the first
     round's tool results can be billed eight times. A breakpoint after them
     reprices the repeats at 0.1x. This is worth more here than the static
     prefix, because the prefix is small and the tool results are not.
  Volatile per-turn context (time, calendar state, session entities) goes
  after the last breakpoint, never inside the cached prefix.
- **TTL: start on the 5-minute default, and pre-warm rather than pay for an
  hour.** A read refreshes the entry at no cost, and the lifetime runs from
  the *start* of the writing request, so generation time counts against it.
  Traffic here is bursty: clustered on race mornings, near-silent overnight.
  Inside a burst, turns start well under five minutes apart and the default
  TTL is strictly cheaper. Across the gap to the next burst, a 1-hour TTL
  would double every write to cover a window that is usually hours wide
  anyway. If measurement shows the gaps sitting in the five-to-sixty-minute
  band, switch to the 1-hour TTL for that window only. **Verify from
  `usage.cache_read_input_tokens`, never from code review**: on a warm loop
  reads should dominate regular input tokens and `cache_creation_input_tokens`
  should be about one round's worth. Zero reads across repeated turns means a
  breakpoint has something dynamic above it.
- Read model: hot per container in module scope, refreshed on a version
  stamp the materialiser writes.
- PuntingForm: per-key in-memory TTL cache only (§10.3). **No shared PF
  cache across users** — the shared cache v4 §13 allowed is redistribution
  and cross-tenant leakage until the licence says otherwise.
- Bootstrap: shared part per minute; per-user part uncached.
- Divergence data: TTL tied to the Betfair capture cadence; disclosed when
  stale.
- Answers: never cached.

---

## 15. Testing & verification — end to end

### 15.0 The direct answer to "do we just send it to the cloud and test it there?"

No. The cloud is where we prove the *deployment* (IAM, secrets, KMS,
streaming, cold start, cost), not where we find out whether the logic
works. Four tiers, each with an exit gate; nothing moves up a tier without
its gate. The repository's own house rule applies at every tier: **verify
content, not proxies** — a 200 with prose proves the endpoint answered, not
that the answer was right (CLAUDE.md; `14_TESTING_AND_OBSERVABILITY.md`).

### 15.1 Tier 0 — offline and hermetic (runs in CI on every push)

Lives in `server/python/chat/tests/`, collected by the existing
`python -m pytest server/python` step in `ci.yml`, and must stay hermetic
the way that suite is: no `DATABASE_URL`, no keys, no network — a property
that suite's own comment records as verified, and that this work must not
degrade. The TypeScript side (client renderer, any Express proxy) runs under
the existing `vitest` and the offline `eval_chat.ts` mode.

**`ci.yml` needs changes in the same PR, and v5 did not say so** (§23
finding 15). It installs `numpy scipy pandas scikit-learn lightgbm pytest
requests psycopg2-binary` — no `boto3`, no `moto` for the fake KMS, no
`anthropic`, no `fastapi`. Collection fails at import without them. It also
pins Python 3.11 and runs `compileall` over the whole repository, while §2
targets the managed 3.12 runtime: either the chat stays 3.11-compatible or
CI gains a second matrix entry. Decide it in the PR that adds the first
test, not in the one that goes red.

| Family | What it proves | What it would *still pass with* (the proxy check) — and the extra assertion that closes it |
|---|---|---|
| Block contract | Every assembled answer validates against the schema; ranges checked client-side | a valid but empty answer → assert required blocks per intent |
| Anti-hallucination lint | Every numeric token in prose is in a tool result or block cell | prose with no numbers → golden cases require numbers where the intent needs them |
| Band determinism | probability ⇒ one band; adjectives ⊆ band vocabulary | an answer that never uses adjectives → tone evals |
| Key Service | envelope round-trip with a fake KMS (`moto`); context mismatch fails; local provider refuses `ENV=production` | — |
| Redaction | a canary key placed in a URL, an exception, a tool error and a Sentry event never reaches any captured sink | a sink nobody captures → the test enumerates handlers |
| Executor | host allowlist; pacing; `PFAuthError` → `key_invalid`; wall 400 → `window_exceeded`; recorded fixtures from `providers/fixtures/pf_*.json` | — |
| Budgets | counters, caps, `budget_exhausted` with zero model calls (the Anthropic client is a test double that counts) | — |
| Injection | the 16 existing cases + PF text-field cases (runner named "Ignore previous instructions", a `gearChanges` string with instructions) + a web page with instructions | fixtures the model never sees → the cases assert on tool framing *and* output |
| Authorization | cross-subject reads are 404; SSE resume owner-checked | — |
| Share sanitizer | property test: arbitrary answers ⇒ shares ⊆ allowlist; any PF-derived block ⇒ refused | — |
| SSE framing | sequence ids monotonic; resume from `after` replays without duplication; heartbeat | — |
| Entity resolver | alias hits, > 0.85 fuzzy hits, ambiguity ⇒ `clarify` | — |
| Read model | materialiser on a **current** tips-shaped fixture ⇒ items == runners; re-run idempotent; `model_fair_odds` derived correctly; every runner carries `prediction_stages` | `examples/sample_race.json` — it is from 2026-04-18 and predates `prediction_stages`, so a test built on it passes while proving nothing about the stage plumbing the voice depends on (§22 finding 8). Capture a fresh fixture from a `tips-proof` run |
| Pipeline additions | `data_confidence` byte-identical off; correct on a row with known missingness | — |

### 15.2 Tier 1 — local, live, operator only

- `python -m chat.cli` against: the read-only Neon role (a prod **read**,
  which needs the operator's explicit approval each time per the house
  rule), the operator's own PF key, the chat's own Anthropic key with a low
  spend limit, the local `KeyProvider`, DynamoDB Local (or `moto`) for
  state.
- The golden and injection suites in live mode (`EVAL_LIVE_CONFIRM=yes`,
  the existing double opt-in), with a cost cap on the run.
- Measure and record per-turn tokens, cache hit rate, PF calls, latency.
- **Gate**: injection 16/16; golden tool-choice ≥ 95 %; zero fabricated
  numbers; zero unverified links; band lint failures < 2 %; median cost per
  turn recorded.

### 15.3 Tier 2 — cloud staging (same account, an alias, not a second stack)

- `infra/10_chat_stack.sh` (new script, by reviewed PR — `infra/*.sh` is
  never edited unattended) creates, idempotently: the KMS key and policy;
  the Cognito user pool, domain and two app clients; `stride-chat-role`;
  the three `stride/chat/*` secrets from GitHub secrets (the same
  `--from-env` shape as `01_secrets.sh`); the `stride-chat` function with
  the Lambda Web Adapter layer, `AWS_LWA_INVOKE_MODE=response_stream`, two
  aliases (`staging`, `prod`) each with its own Function URL; the
  `chat-materialise` function and its EventBridge Scheduler rule (no S3
  notification — §22 finding 1); the DynamoDB tables;
  log groups with 60-day retention; alarms on `Errors`, `Throttles` and
  `Duration` p95 to `stride-alerts`. It runs from **its own
  `deploy-chat.yml`**, assuming the same OIDC role — **not** as a step inside
  `deploy-infra` (§23 finding 3). `deploy-infra.yml` is a single linear job:
  a `deploy_chat` input would gate only the new step while every existing one
  still ran, so each chat iteration would re-run `01_secrets.sh --from-env`
  (which `put-secret-value`s the whole `stride/prod` blob and drops any key
  absent from the process environment — the erasure that script's own comment
  documents), `06_schedules.sh` and `07b_fargate_schedules.sh` (whose
  `update-schedule` is full-replacement and which `CLAUDE.md` puts off
  limits), a container image rebuild, and a live ECS smoke task. "Nothing
  existing changes" is only true if the chat deploy does not go through that
  workflow.
- `chat-proof` — its **own** workflow, in the spirit of `verify-jobs` but not
  inside it. That dispatcher branches on `LAMBDA_JOBS` (container Lambdas
  invoked by name) and otherwise runs an ECS task definition; a zip Lambda
  behind a Function URL is neither, and needs an authenticated HTTP call
  that workflow has no shape for (§22 finding 10). Against the **staging**
  alias:
  1. an authenticated canned turn ⇒ a `get_stride_tips` tool call that
     returned ≥ 1 runner *and* a block set that validates (content, not a
     200);
  2. a **canary-key** turn (a fake key with a recognisable prefix) ⇒ the
     `key_invalid` path *and* the canary absent from CloudWatch, the
     response, and the SSE stream. **There is deliberately no proof that
     spends a real PuntingForm key** (§23 finding 2): there is one physical
     key and it is the pipeline's, its rate limits are unpublished, and a
     ban takes down the 04:00 racecard collect, which `missing-run-watch`
     calls chain-fatal. A CI job that exercises the executor against the
     live provider would violate this plan's own invariant 2 and the control
     §9.1 states as "the chat never uses the Stride key". The canary proves
     the executor path; the operator proves the live path by hand, once,
     from Tier 1;
  3. a budget trip against **staging's own counters** ⇒ `budget_exhausted`
     with zero Anthropic requests (the usage line count). Counters and every
     `stride_chat_*` item carry a `STRIDE_CHAT_ENV` key segment, so tripping
     a cap on staging cannot exhaust production's global cap for the day —
     v5 shared one counter set between the two aliases (§23 finding 14);
  5. an SSE resume mid-stream ⇒ no duplicate events;
  6. `readyz` reports the model preflight passed.
- A load probe (`k6`) recording p95 latency, throttles, pooled Neon
  connections and spend, **sized to the cap it is meant to trip** — v5 asked
  for 20 sessions of 5 turns, which is 100 turns and cannot reach a 500/day
  global cap (§23 finding 14). Run it against a staging cap set deliberately
  low. Reserved concurrency and the daily cap must both be seen to trip, and
  the measured turn duration from this probe is what §10.1's concurrency
  figure is then set from.
- **Gate**: all six proofs green with their evidence pasted into the PR;
  cost per turn within 25 % of the Tier 1 figure.

### 15.4 Tier 3 — production

- Promote the `prod` alias; the client flips `STRIDE_CHAT_BACKEND` (or the
  Express proxy target) to the prod URL.
- Watchers, following the estate's pattern: a `chat-watch` workflow that
  reads the query log daily (turns, error rate, lint failures, budget trips)
  and opens an issue on anomalies; the materialiser registered with
  `missing-run-watch`; alarms as above.
- **A spend watch, weekly, filing a GitHub issue** — the same shape and the
  same reasoning as `aws-plan-watch.yml`, which files an issue rather than
  trusting an SNS subscription nobody has confirmed. It reads the chat's own
  per-turn usage lines (input, output, cache-read tokens, searches), prices
  them, and reports the week's total, the trend, and the projected month
  against the Console spend limit. This is the only thing in the repository
  that can see Anthropic spend: the US$20 AWS tripwire and `aws-plan-watch`
  read AWS bills only (§22 finding 12). Without it, the first signal of a
  runaway is the credit-card statement.
- **The standing question** (`14_TESTING_AND_OBSERVABILITY.md`): *if this
  component silently did nothing tomorrow, what would notice, and how
  fast?*

| Component | If it silently did nothing | What notices | How fast |
|---|---|---|---|
| chat-materialise | no read model for today | its **own** watcher (§23 finding 7): `scoring_in_progress` still set after the tips job's own timeout has expired — 08:05 plus the handler's 8.5 h bound, so ~16:35, not the 15:00 v5 guessed from a retired 10:00 start — with quiet days excluded by the `quiet_day` state | same day |
| briefing generator | no briefings | chat-watch: opted-in users with no briefing once the day's file has landed and been materialised, keyed off that event rather than a clock | same day |
| Key Service | decrypts fail | `key_invalid` spike in the query log → chat-watch; `Errors` alarm | minutes / same day |
| budgets | counters not written | chat-watch asserts counters == turns | same day |
| web search | dark | `web_fallback` tier count zero on a day with web-intent turns → chat-watch | same day |

### 15.5 Security tests (mapped to §9.1)

Each row of the threat table names its proof; the proofs are Tier 0 where
they can be and Tier 2 where they need AWS (IAM denial on `stride/prod`,
KMS context enforcement, the CloudWatch metric filter firing on a planted
`apiKey=`). A `security-review` pass on the chat directory before the first
Tier 2 deploy; dependency pins for `anthropic`, `psycopg`, `boto3`.

### 15.6 The eval programme

The 46 existing cases are the seed. Grow to ~300 across personas (novice,
form student, price hunter, keyless, own-key Starter, own-key Modeller),
intents (the five tools × coverage states), and days (quiet day, scoring
in progress, midweek, Saturday carnival). Score intent, tool choice, block
choice, follow-up quality, tone, band conformance, RG framing. The
existing rule stands: every prompt change bumps the prompt version and
re-runs live before deploy. The eval is the release gate, not a
nice-to-have.

### 15.7 What is *not* tested in the cloud first

Prompts, routing, the sanitizer, the lints, the resolver, the budgets'
arithmetic — all Tier 0/1. If a Tier 2 proof is the first place a logic
defect shows, that is a Tier 0 gap to close, not a reason to debug in AWS.

---

## 16. Prerequisites — what must be true before this can function

"Proof" is what closes the item; a tick without the proof is the proxy
failure this repository keeps a register of.

v5 listed fifteen prerequisites in one flat list and made a Cognito user pool
block "everything online". That was wrong (§22 finding 7). The first useful
version of this product has **one user, who is the operator**, and for a
single-user deployment the identity problem is already solved: `authGate.ts`
on `stride-app` branch `claude/frontend-public-repo-aws-pqjkty` is a
shared-password gate with a constant-time compare, written and unmerged.
Likewise a per-user KMS envelope protects one user's key from nobody. Both
belong to the audience-widening gate, alongside the licence.

So the prerequisites split in two, and only table A is on the critical path.

### 16A. For the first useful version (operator-only)

| # | Prerequisite | Owner | Proof |
|---|---|---|---|
| A1 | **Decide the prototype's fate** (§20 Q2): port the `chat/*` TypeScript branches to Python, or finish them in TypeScript behind Express | Operator | Decision recorded here |
| A2 | **Read-only Neon role** `stride_chat_ro` on the pooled host. The DDL is copied into `stride-racing/migrations/chat_readonly_role.sql` creating the role **without a password**, and the password is set out of band with `ALTER ROLE … PASSWORD` from the operator's shell. Connection string into `stride/chat/database_ro` | Operator (typed confirmation, then a shell step) | Through the **pooled** host: a `SELECT` succeeds and an `INSERT` fails as that role. Role-level `ALTER ROLE … SET` under PgBouncer transaction pooling is assumed, not verified (§22 finding 9) — this test is what establishes it. **Not** the path v5 named (§23 finding 9): `apply-migration.yml` `cat`s the whole migration into the step log of a public repository, and the source file on the `stride-app` branch opens `CREATE ROLE … PASSWORD 'CHANGE_ME'`; that workflow also rejects any path containing a slash and reads only this repository's `migrations/` |
| A3 | **Secrets**: `stride/chat/anthropic` (a **new** Anthropic key in its own Console workspace with a spend limit), `stride/chat/database_ro`, `stride/chat/puntingform` (the operator's own key, for a single-user deployment), `stride/chat/gate_password` | Operator + deploy | The chat role reads all four; `stride/prod` ⇒ AccessDenied |
| A4 | **Chat service code** `server/python/chat/` — tools, budgets, orchestrator, deterministic block assembly, schema, SSE, CLI — with Tier 0 tests | PR(s) | CI green; the Tier 1 gate in §15.2 met, with a measured cost per turn |
| A5 | **Read model materialiser** (polled, §8.3) with its content postcondition, registered with `missing-run-watch` | PR | Tier 0 test + Tier 2 proof |
| A6 | **`infra/10_chat_stack.sh`** reviewed and merged; `deploy_chat` input on `deploy-infra`, default off; the `chat-proof` workflow (§15.3) | PR | Deploy run id; proof run id |
| A7 | **Client hosting decision** (§20 Q3) and the client work it implies: block renderer, SSE client with resume, RG notice placement | Operator + PR(s) | Live evals through the UI |
| A8 | **Chat spend watch** — the weekly workflow that reads the chat's own token totals and files an issue, because no existing watcher can see Anthropic spend (see the spend posture note at the top) | PR | A run that reports a real number |
| A9 | **Tone bands fitted** on observed outcome frequency (§8.2). Not a release-time item: the band lint is a Tier 0 test that runs on every push and the block assembler reads the band file (§23 finding 13) | Authoring + a prod read, approved | The fitted file in the repo, with its coverage count and the minimum-row gate satisfied |
| A10 | **`pf_client` made per-key and non-blocking** (§7): an explicit `api_key` parameter, async transport with per-key pacing, `PFWindowError`. Byte-identical by default. Needed as soon as any tool uses a key, including the operator's own | Pipeline PR | Existing `tests/test_pf_client.py` still green; a new test that two keys pace independently |

### 16B. Before any second user (the audience gate)

| # | Prerequisite | Owner | Proof | Gates |
|---|---|---|---|---|
| B1 | **Written PuntingForm answer** on: (a) Stride proxying a user's *own* key on the user's behalf; (b) caching that user's data server-side; (c) showing pipeline-derived predictions and field facts to other users; (d) a shared pool key | Operator | The reply filed with the private records; §20 Q5 resolved with a date | Every user other than the operator (Phase 4), Mode B (Phase 5), PF blocks in shares |
| B2 | **Betfair data terms** for displaying delayed prices to other users or publicly | Operator | Same | Prices on shares and on other users' field tables |
| B3 | **Legal review** — this becomes a consumer-facing service that supplies betting-related information in Australia. The specific questions for a lawyer, which this audit cannot answer: whether it is a betting-information or tipping service requiring licensing in any state; what the gambling advertising and inducement rules require of the copy; RG notice placement and the BetStop link; terms, privacy policy and the deletion path | Operator + counsel | Written advice; documents linked from the repo | Any second user |
| B4 | **Identity**: Cognito user pool, hosted-UI domain, email sender, MFA policy, app clients — replacing the shared-password gate | Operator sitting + `10_chat_stack.sh` | Login round trip on staging; a JWT verified by the Lambda | Any second user |
| B5 | **KMS key**, policy, and the per-user Key Service (§9.2) with step-up on key operations | `10_chat_stack.sh` + PR | `GenerateDataKey` with context succeeds from the chat role; `Decrypt` without context fails; the canary tests in §15 | Storing anyone else's PuntingForm key |
| B6 | **Eval corpus** to ≥ 300, with a harness that scores `blocks[]` — the existing runner asserts substrings over a flat string and cannot (§23 finding 16); tone and band evals | Authoring | Corpus in the repo; the gate numbers in §15.2 | Release to anyone |
| B7 | **Pipeline additions** — `data_confidence` (built **last**, §8.2), per-track merge progress, `STRIDE_ML_APPLY_ISOTONIC` promotion (owned by the model programme, on its own evidence) | Pipeline PRs | Tests in `server/python/tests`; shadow-week evidence for the flag | Fuller humility voice; member percentages |

---

## 17. Delivery phases (mapped to the estate)

**Phase 0 — paper (this document).** Decisions in §20 taken; the licence
conversation opened; the prototype's fate decided. *Exit:* §20 answered.

**Phase 1 — local, with two production touches.** v5 titled this "local, no
AWS" while requiring the tools to run over the read-only Neon role, which is
a production DDL change (§23 finding 13). It is honest to name the two:
creating that role, and reading production data with the operator's
approval. Everything else is local. Build `server/python/chat/`: the tools
over the read-only role and the S3 artifacts, with a local-directory
fallback for development the way the tips job reads its own files; budgets;
the single-call orchestrator with deterministic block assembly; the block
schema; the CLI; Tier 0 tests wired into `ci.yml` with the dependency and
runtime changes §15.1 names. No Key Service beyond reading the operator's own
key from the secret, no Cognito, no KMS.

The **tone bands** are fitted here, not at release: §15.1 runs the
band-consistency lint as a Tier 0 test on every push, and the block assembler
reads the band file, so nothing downstream works without it. v5 filed them
under "blocks: release", which is a phase too late (§23 finding 13).

*Exit:* Tier 0 green in CI; the Tier 1 gate met with a **measured** cost per
turn — which is also the number every cap in §10.1 gets set from.

**Phase 2 — cloud staging, additive.** The read-only role through
`apply-migration`; `10_chat_stack.sh` (role, secrets, the two functions and
their aliases, DynamoDB tables, log groups, alarms — no KMS, no Cognito
yet); the polled materialiser and its postcondition; `chat-proof`; the load
probe; the spend watch. Nothing existing changes. *Exit:* Tier 2 gate met,
evidence in the PR.

**Phase 3 — operator-only production.** Block renderer, SSE client, the
shared-password gate, the hosting decision executed, the `prod` alias
promoted, watchers registered. The audience is the operator alone, which is
what the current PF licence covers and what the password gate is adequate
for. *Exit:* live evals green through the UI; a week of chat-watch and
spend-watch with no silent-no-op findings and no cost surprise.

**Phase 3.5 — the pipeline additions**, once the chat is real and its needs
are known rather than guessed: `data_confidence` (last, §8.2), per-track
merge progress, a current fixture. Each its own PR, flag-gated,
byte-identical off, and deployed on a weekday — never a Friday or Saturday
(`02_ARCHITECTURE_AND_CONTRACTS.md`). *Exit:* a scored card with the flag on
and the flag off, byte-identical where it should be.

**Phase 4 — audience widening.** Only after the whole of §16B. Cognito
replaces the password gate; the Key Service and KMS arrive with the first
user who is not the operator; Mode A; shares (model-derived only);
briefings and watchlists; the Batch-API briefing job. *Exit:* the licence
answer's conditions are implemented as tests, not as intentions.

**Phase 5 — Mode B.** Only with a second PF subscription and written
clearance. Pool budgets, abuse detection, cache-first policy, canary shape
in CI. *Exit:* the pool-key security tests in §15 green.

Each phase's PR carries its tests, per the estate rule; the acceptance
script for each phase is runnable as one command and cited with output in
the PR.

---

## 18. Open risks (updated)

1. **Licence** — the gate on every audience wider than the operator (§16).
   The pool key is the sharpest case, but BYOK proxying and per-user
   caching are also questions to ask in writing, not to assume.
2. **Anthropic spend** — the largest cost line by an order of magnitude at
   any real volume (§10.2); mitigated by auth, caps, concurrency and a
   separate key with a Console limit.
3. **Coverage and timing** — target tracks only; a third of days are quiet
   and produce no artifact at all; scoring starts 08:05 and on a ~90-race
   Saturday lands in the early afternoon. The product must be honest about
   all three (§3.6, §8). Publishing meetings as they complete is a handler
   change the pipeline owners can make more cheaply than v5 assumed, but it
   is still their decision, not the chat's.
4. **Raw member scores** — until the isotonic path is promoted, per-booster
   percentages cannot be quoted (§3.9).
5. **One PuntingForm key** — a chat that used it would couple the product's
   availability to the pipeline's; Mode B is deferred for that reason as
   much as for the licence.
6. **Prices** — delayed key, AU-IP capture, unformed-book phantoms; the
   live key needs Betfair Australia's activation; Betfair's terms are
   unverified for redistribution.
7. **Entity resolution quality** — horse-name fuzziness is the top clarify
   trigger; measure and iterate; reuse the existing normalisers.
8. **Routing quality is the product** — the eval is the release gate.
9. **Claude availability** — single point of failure for routing and
   composition; the read model still answers coverage questions without a
   model call (`budget_exhausted` shape), which is also the outage shape.
10. **Model drift** — settlement loop + staged retrain, owned by the
    pipeline programme.
11. **Model extraction** — caps + monitoring mitigate, not eliminate.
12. **Free Plan end 2027-02-01** — the AWS cost picture changes then; v5's
    "no always-on services" keeps the exposure small.
13. **Two implementations drifting** — the TypeScript prototype branches
    and a Python service would diverge; §20 Q2 exists so only one is
    finished.
14. **Unverified external figures** — the AWS and Cognito prices, Lambda
    streaming limits and the API Gateway timeout change were not
    verifiable from this session (egress blocked); none of them changes a
    decision, but they must be checked before the infra script is written.

---

## 19. Audit log — what changed from v4 and the evidence

| # | v4 said | Finding | Evidence | v5 change |
|---|---|---|---|---|
| 1 | "v2 §6 / §7 / §13 stand" | v2 and v3 are not in either repository | `git log --all -- '*plan*'` | §1 and §4 inline what is needed; v5 is self-contained |
| 2 | Single shared FastAPI service, OIDC/JWT | No API service, no IdP, no auth exists; the prior audit chose a zip Lambda behind a Function URL; always-on compute breaks the US$20 tripwire | `02_ARCHITECTURE_AND_CONTRACTS.md:13`, `docs/chat/…AUDIT.md §2.3`, `infra/03_notifications.sh` | Lambda + Function URL + Lambda Web Adapter (FastAPI in-process is fine); Cognito named as the IdP |
| 3 | Redis for SSE replay | No Redis; ElastiCache alone is more than half the tripwire | estate scripts | DynamoDB with TTL |
| 4 | KMS envelope | No KMS key exists; the deploy role can create one | `infra/09_bootstrap_oidc.sh:52` | Kept; concrete design in §9.2; created by `10_chat_stack.sh` |
| 5 | Tables `users, api_keys, service_keys, audit_log, query_log, cache, model_registry, races, predictions, results_settlements` | None exist; the real stores are `prediction_audit`, `selections`, `race_schedule`, `race_results_history`, `runner_odds_snapshots`, `selection_ledger`, the day JSONs in S3 | `docs/03-data-and-ingestion.md §6`, `migrations/` | Chat-owned state in DynamoDB; pipeline data via a read-only role; a read model materialised from the day JSON |
| 6 | "Key works. You're on the Pro plan… ~400 requests left today" | No plan/quota endpoint; tiers are Starter/Modeller/Professional; limits unpublished | `pf_client.py`, `PUNTINGFORM_MIGRATION.md` Phase A, `:412` | Probe copy rewritten to what PF can tell us (§3.1) |
| 7 | Three-key separation; pool traffic "never competes with own-key traffic" | One physical PF key, the pipeline's; pool traffic would compete with the 04:00 racecard job and share its ban risk | `.env.example`, `PUNTINGFORM_MIGRATION.md:15` | Mode B deferred until a second subscription and written clearance; invariant 2 re-counted |
| 8 | Shared PuntingForm cache "subject to ToS" | Serving A's licensed data to B from a shared cache is redistribution and cross-tenant leakage | licence note | Per-key cache only (§14); invariant 13 |
| 9 | Share cards carry field tables | PF-derived and Betfair-derived data on a public card is redistribution regardless of whose key fetched it | licence note; `betfair_enrich_racecard.py` | Shares are model-derived only; allowlist sanitizer (§3.10) |
| 10 | No LLM spend budget | v4 §9 budgets cover PF, briefings and SSE only; a public chat is an open invoice | v4 §9 | §10.1 caps, reserved concurrency, separate key with Console limit; invariant 9 |
| 11 | "Never in any log" | PF auth is a query parameter, so URL logging is key logging; Sentry is in the app | `pf_client.py:76`, `stride-app/server/index.ts` | Invariant 11; redaction filters; CloudWatch metric filter; canary proof |
| 12 | Prompt injection not addressed | Tool text, web pages and the pipeline's own prose are untrusted; the eval suite already has 16 injection cases | `stride-app/evals/chat/injection.jsonl`, agent prompt on `chat/04-streaming` | Invariant 12; §9.1 row; CI gate |
| 13 | Authorization unspecified | Multi-tenant resources need subject scoping; SSE resume needs an owner check | — | Invariant 10; tests |
| 14 | `member_probs` "stored by the pipeline (v4 adds)" | Already produced per runner in `prediction_stages`; but raw unless `STRIDE_ML_APPLY_ISOTONIC` is on | `prediction_stages.py`, `run_tips_pipeline.py:2367,3310,3562`, `ml_model.py` comment | No new pipeline field; voice uses stage agreement and rank agreement until promotion (§3.9, §8) |
| 15 | "precomputed top-k SHAP contributions" | No serve-time SHAP; `explain_prediction` is importance × value; SHAP only at training | `ml_model.py:806`, `train_ml_enhanced.py:1193` | Explanations from `key_factors`, intelligence adjustments, MC factors; SHAP deferred (§3.5) |
| 16 | Tone bands ≥ 0.40 / 0.25 / 0.15 / 0.08 | Published probabilities are market-anchored; 8 of 3,396 runners above 0.30 in the last window | `README.md` calibration table, `docs/09 §1` | Bands fitted from `prediction_audit` quantiles, versioned (§3.9) |
| 17 | "31% (fair $3.20)" | `fair_odds` in the artifact is `100/true_market`, the market's price | `run_tips_pipeline.py:985` | `model_fair_odds` vs `market_fair_odds` named (§8.1, §11) |
| 18 | "Morning briefing … generated when the pipeline finishes scoring" | Tips do not start in the morning as v4 assumed. v5 said 10:00 and **that was also wrong** — it came from `infra/README.md`, which is stale. The chain was re-timed on 2026-08-06 and tips start 08:05, running 3.4–6.2 h at ~90 races (§23 finding 5) | `infra/07b_fargate_schedules.sh:73-77`, `:101-121` | `scoring_in_progress` and `quiet_day` states (§3.6, §8.3) |
| 19 | Entity aliases "seeded from pipeline + PuntingForm" | Normalisers with approved aliases and fuzzy matching already exist; the estate forbids new normalisers | `identity_normalization.py`, `horse_names.py`, `02_ARCHITECTURE… "never write a new normalizer"` | Reuse (§3.3) |
| 20 | "context bundle per turn" | Placed in the system prompt it would bust the prompt cache every turn | `claude-api` skill, prompt caching rules | Volatile bundle after the breakpoint (§3.4, §7) |
| 21 | Composer outputs typed blocks | `output_config.format` is incompatible with citations (400) | skill `shared/tool-use-concepts.md:510` | v5 answered with a two-call turn; **superseded by §22 finding 2** — blocks are assembled in code, the composer is optional (§7) |
| 22 | Strict tool schemas; Claude web search with source URLs | Both verified; the prototype already uses `strict: true` and `web_search_20260209` | skill; `chat/04-streaming` `server/chatAgent.ts:261` | Kept; tool names aligned with the eval suite |
| 23 | Divergence "where market odds are available" | Cards carry no PF odds; prices are Betfair delayed, AU-IP capture, history from 2026-08-02, phantom rows to fence | `betfair_enrich_racecard.py`, `BETFAIR_KEYS_STATUS.md`, `02_ARCHITECTURE…` limitations | §8.4 definition with provenance; Betfair terms as a prerequisite |
| 24 | Data-confidence flag "pipeline-computed" | Not computed today; the serve row contains by-design constants that must not count as present | `serve_features.py`, `nan_contract.py`, `feature_liveness_audit.py` | §8.2 specification, flag-gated pipeline PR |
| 25 | `GET /bootstrap` "fully cacheable except key status" | briefing status is per-user too | — | Shared vs per-user split (§3.8) |
| 26 | Testing section is a checklist | No tiers, no gates, no answer to "where do we test" | — | §15 end to end; §16 prerequisites; §17 phases |
| 27 | Unstated | The prototype branches in `stride-app` implement most of the loop already | `git ls-remote` on `stride-app`: `chat/01-hygiene` … `chat/04-streaming` | §1; §20 Q2 |

**Verification status of external facts used above.** Verified from source
in this session: the Claude API features and prices (skill cache dated
2026-06-24), the Lambda Web Adapter's `AWS_LWA_INVOKE_MODE=response_stream`
and FastAPI streaming support (README), Neon pooling behaviour (docs
source). **Not verifiable** (egress to `aws.amazon.com`,
`docs.aws.amazon.com`, `docs.anthropic.com`, `neon.com` and
`puntingform.com.au` is blocked from this environment): KMS and Cognito
prices, ElastiCache/ALB/NAT prices, Lambda streaming payload and duration
limits, the 2024 API Gateway timeout change, PuntingForm's published terms.
Each is marked where used; none changes a decision.

---

## 20. Questions for the operator (decisions only you can make)

1. **Positioning.** The chat frames model output as probabilities, not tips,
   while the pipeline's product is a BET/NO_BET contract the app displays as
   tips. Should the chat surface the bet contract when asked ("what are you
   backing?") with the same RG framing, or decline to? v5 assumes *surface
   it, framed as the pipeline's decision, with the notice*.
2. **The prototype's fate.** Port the `chat/*` branches' behaviour to
   Python behind the Function URL (the prior audit's direction, one
   language for tools that already exist in Python: `pf_client`,
   `identity_normalization`, `result_margins`), or finish them in
   TypeScript behind Express. v5 assumes *Python*, keeping the tool names
   and the eval suite.
3. **Client hosting.** Static client on S3 + CloudFront with the Function
   URL as the API (cheapest, no always-on host), or Express kept as a thin
   proxy on a small always-on host (keeps `chat_feedback` and the rate
   limiter where they are; costs an always-on line). v5 assumes *static +
   CloudFront*.
4. **Chat-owned state store.** DynamoDB (v5's recommendation: no Postgres
   write role for an internet-facing service) or a dedicated Postgres
   schema with its own role.
5. **PuntingForm.** Will you open the written licence conversation now, and
   with which of the four questions in §16B item B1? Until answered, the
   product's audience is you.
6. **Betfair.** Delayed prices on other users' screens, and the live-key
   activation through Betfair Australia's Automation Hub — pursue now or
   after Phase 3?
7. **Model for the composer.** Opus 5 everywhere at launch (v5's
   assumption), with a measured trial of Sonnet 5 for the composer once the
   evals exist.
8. **Launch caps.** 40 turns per user per day and 500 global were v5's
   numbers, written before any measurement. Set them from the Tier 1 figure
   instead, and set the Console spend limit on the chat's key to match.
9. **The AWS tripwire.** The chat plausibly lifts AWS steady state five to
   ten times, from about US$0.90 a month. Does the US$20 tripwire move with
   it, and to what? Leaving it makes the forecast alarm routine, and a
   tripwire that cries wolf is worse than none when the estate has one and
   the daily pipeline depends on it (§10.2, §23 finding 20).
10. **Per-meeting publication.** `run_tips_pipeline` already merges
    track-filtered runs into the canonical file, and `07b` calls the
    remaining work a handler change with a rehearsal. Do you want it? It
    would let the chat answer about Randwick at 10:00 instead of waiting for
    Doomben, and would remove most of the `scoring_in_progress` design
    (§3.6, §23 finding 17).


---

## 21. Rollback (v5.1 — new; v5 had none)

`CLAUDE.md` keeps a Rollback Pattern for the model, the pipeline code and
the tips file. v5 shipped a whole new subsystem without one (§22 finding
11). Every layer here needs an answer to "it is 10:40 on a Saturday and this
is wrong — how do I stop it", and the answer must not be "redeploy".

| Layer | Rollback | Time to safe |
|---|---|---|
| Chat function | Repoint the `prod` Lambda alias at the previous version. Aliases exist for exactly this; versions are immutable | seconds, no build |
| Chat, entirely | Set the `deploy_chat` input off and repoint the client at the legacy path, or set reserved concurrency to 0 — the function stops answering and spends nothing, while the pipeline is untouched | seconds |
| Runaway spend | Revoke the chat's Anthropic key in its own Console workspace. It is a different key from the pipeline's precisely so this does not stop the 05:30 consensus job (§9.3) | seconds |
| Read model | Materialiser is idempotent and the table is derived: delete the date's items and let the next poll rebuild. Nothing downstream of the pipeline reads it | one poll cycle |
| Block schema / prompt | Prompt version is stamped on every answer; revert the version and redeploy the function. The evals are the gate that should have caught it | one deploy |
| Neon read-only role | `REVOKE`/`ALTER ROLE … NOLOGIN` through `apply-migration`. The chat loses database tools and degrades to model + web, which is a designed tier, not an outage | one migration |
| Pipeline additions (§8.2) | Flag off through the secrets path plus a `deploy-infra --skip_image` run — the estate's own flag-flip procedure. If the defect is at import time, a flag cannot save it: revert the commit and rebuild the image, which is why this lands last and on a weekday | minutes (flag) to one image build (revert) |
| Chat state (sessions, budgets, keys) | DynamoDB tables are chat-owned; dropping them loses conversation history and forces re-entry of a key, and breaks nothing in the pipeline | immediate |

The property that makes all of this cheap is the one §4 invariant 14 buys:
the chat reads pipeline data and writes only its own. There is no rollback
path that has to reason about pipeline state, because the chat can never
have changed any.

---

## 22. Second audit — what changed from v5, and the evidence

v5 was audited again on 2026-09-09, adversarially and against itself. The
findings below are defects in v5, not in v4 (those are in §19). Each names
what v5 claimed, what is actually true, and where the correction landed.

| # | v5 claimed | Finding | Evidence | Correction |
|---|---|---|---|---|
| 1 | The materialiser fires on an **S3 ObjectCreated** event from `stride-evidence-<acct>/artifacts/` (§5, §8.3) | `PutBucketNotificationConfiguration` is a **full-replacement** API, and the bucket is shared with the retrain-gate evidence store. That is the same hazard class `CLAUDE.md` names for `update-schedule`: the first call works, the second consumer's silently erases the first. The bucket has no notification config today, so v5 would have been the one to introduce the trap | `infra/02b_evidence_bucket.sh` (versioning + public-access-block only; no notification anywhere in `infra/` or `.github/workflows/`); `CLAUDE.md` "Never touch `infra/*.sh`" | Polled materialiser on EventBridge Scheduler; no S3 notification (§5, §8.3, §15.3) |
| 2 | Two Claude calls per turn (orchestrator + composer), costed at **US$0.12/turn** | The US$0.12 figure is the prior audit's number for **one** two-round tool turn. A composer re-ingesting ~8 k of tool results and writing ~2 k adds ~US$0.09 — a 75 % increase the cost table did not carry. And the composer's justification was thin: it exists only because `output_config.format` cannot coexist with citations, yet every block but `prose` is a deterministic projection of a tool result — which v4 itself said | `docs/chat/CHAT_LAMBDA_ARCHITECTURE_AUDIT.md` §7; skill `shared/tool-use-concepts.md:510`; v4 §6 "deterministic table rendering" | Deterministic block assembly is the default, one model call; composer behind a flag, switched on only if the evals justify it. Cost table shows both (§7, §10.2) |
| 3 | Reserved concurrency **3** at launch | Stated as a spend cap in §10.1 while §3.6 deliberately creates a simultaneous open — every opted-in user notified the moment scoring lands. A streaming turn holds an execution for tens of seconds. The two sections were never reconciled | v5 §10.1 vs §3.6 | Concurrency is a spend cap, explicitly re-set against measured turn duration before Phase 4 (§10.1) |
| 4 | Priorities: **licence compliance > credential security** > UX > robustness | Not comparable quantities. The licence is a binary question about audience, answered once in writing; credential security is continuous and applies at an audience of one. Ranking them invites deferring a security control pending a licence answer | — | Reverted to credential security first; licence restated as a hard audience gate (header, §16B) |
| 5 | Tone bands fitted from `final_win_prob` **quantiles** | A quantile says where a number sits, not what it means; "strongly fancied" has to cash out as an observed win rate. `prediction_audit` carries both sides, so the honest fit is a calibration curve. v5 also assumed coverage rather than measuring it, and ignored the repo's own minimum-sample discipline | `run_tips_pipeline.store_final_probs_in_audit` (non-fatal UPDATE, `:2232-2263`); `auto_results_collector.py:202-213` (`won`, `actual_position`, `starting_price`); `STRIDE_CAL_MIN_COVERAGE` = 500 | Bands fitted on observed outcome frequency per field-size bucket, with a coverage measurement and a minimum-row gate (§8.2) |
| 6 | `data_confidence` is **prerequisite #9** | It is the only item in the whole plan that touches the live scoring hot path (`serve_features.py`, `ml_model.prepare_features`, inside the 10:00 job), where a flag cannot protect against an import-time error — the failure the Dockerfile comment records as 31 per-race failures and a card with zero selections. It also changes meaning when `STRIDE_SERVE_LIVE_FEATURES` flips, with nothing to say which basis a stored value used | `infra/Dockerfile` (the `racing_system_v8.3_mc.py` comment); `serve_features.py`; `nan_contract.py` | Moved to Phase 3.5, after the chat is real; `data_confidence_basis` version added; the humility voice ships on fields the artifact already carries (§8.2, §17) |
| 7 | Cognito blocks "**everything online**"; KMS blocks key storage in Phase 2 | The first useful version has one user, who is the operator. A shared-password gate with a constant-time compare is already written and unmerged, and a per-user KMS envelope protects one user's key from nobody. v5 put two AWS services, a hosted-UI domain, an email sender, an MFA policy and a whole Key Service in front of a single-user product | `stride-app` branch `claude/frontend-public-repo-aws-pqjkty`, `server/authGate.ts` | §16 split into 16A (first version, operator-only) and 16B (the audience gate); Cognito and KMS moved to Phase 4 (§16, §17) |
| 8 | Stage values per runner, tested against `examples/sample_race.json` | The claim is **correct** — `mc_api` attaches base/ensemble/MC stages inside its per-runner loop and `run_tips_pipeline` adds the wrapper stages for every horse — but the named fixture is from 2026-04-18 and predates the field, so a test built on it would pass while proving nothing | `mc_api.py:7243-7265`, called `:7862`; `run_tips_pipeline.py:1077-1080`, `:3310`; `examples/sample_race.json` has no `prediction_stages` key | Provenance recorded in §1; Tier 0 requires a current fixture from a `tips-proof` run (§15.1) |
| 9 | The read-only role's `default_transaction_read_only` and `statement_timeout` hold on the pooled host | Role-level `ALTER ROLE … SET` under PgBouncer transaction pooling is *probably* fine, but v5 stated it as fact — and it is the only thing standing between an internet-facing service and a write | Neon docs (transaction mode, no session `SET`, no `LISTEN`), verified 2026-09-09 | Stated as an assumption; the positive test (an `INSERT` that fails **through the pooled host**) is what establishes it (§16A A2) |
| 10 | `chat-proof` runs "in the `verify-jobs` style" | That dispatcher branches on `LAMBDA_JOBS` (container Lambdas by name) and otherwise runs an ECS task definition. A zip Lambda behind a Function URL is neither, and the proof needs an authenticated HTTP call the workflow has no shape for | `.github/workflows/verify-jobs.yml:140-200` | `chat-proof` is its own workflow, in the spirit rather than inside it (§15.3) |
| 11 | No rollback plan at all | `CLAUDE.md` keeps one for the model, the pipeline and the tips file. v5 added a whole subsystem without answering "how do I stop it" | `CLAUDE.md` "Rollback Pattern" | New §21, layer by layer |
| 12 | AWS cost framed against the US$20 tripwire | The tripwire and `aws-plan-watch` read **AWS** spend. This account is on the Free Plan, where the out-of-pocket ceiling is US$0 and the stated risk is an outage, not a bill. Anthropic bills real money with no such cutoff, and nothing in the repository can see it | `.github/workflows/aws-plan-watch.yml` header: *"the risk is therefore not a bill. It is an OUTAGE"*; `infra/03_notifications.sh` | Spend posture stated at the top; a chat spend watch added as a first-class prerequisite (§16A A8) |

**What the second audit did not find.** No security control in §9 was found
to be wrong, and no invariant in §4 was found to be unenforceable. The
threat model, the tool surface, the licence position and the test tiers
survive unchanged. The defects above are, with the exception of finding 1,
errors of scope, arithmetic and over-claiming rather than of design.

---

## 23. Third audit — an independent adversarial pass over v5.1

§22 was the author re-reading their own work. This section is a separate
reviewer given one instruction: find what is wrong with v5, assume the author
was competent but overconfident. It found twenty. The ones marked **verified
here** were re-checked against the code before being acted on; the rest are
recorded as reported.

| # | v5 claimed | Finding | Evidence | Correction |
|---|---|---|---|---|
| 1 | The materialiser reads `tips_<date>.json` from the artifacts prefix | **The worst finding in either audit.** S3 filters and globs match prefix and suffix only, and three other files share both: `tips_<date>_candidate.json` and `tips_<date>_cloudproof.json` from proof runs, and `tips_<date>.pre_merge_backup_<ts>.json` from every track-filtered run. A candidate file is scored with a **non-production ensemble** for an arbitrary date. v5 would have published it to users stamped with a release id — and `handler.py:1165` already says in prose why the suffix exists: "so it can never be mistaken for the real `tips_<date>.json`" (**verified**) | `infra/jobs/handler.py:266`, `:1169`; `server/python/run_tips_pipeline.py:3737` | Exact-regex key match, exit on anything else (§5, §8.3) |
| 2 | `chat-proof` includes "an own-key turn with the operator's key ⇒ a non-empty PF payload" | That is a CI job spending the pipeline's only PuntingForm key against a provider whose rate limits are unpublished. A ban takes down the 04:00 racecard collect. It violates this plan's own invariant 2 and the §9.1 control that says the chat never uses the Stride key, whose stated proof is that no chat code path reads it | v5 §15.3 vs §4 invariant 2, §9.1; `pf_client.py:68` | Proof deleted; the canary-key proof covers the executor (§15.3) |
| 3 | `10_chat_stack.sh` wired into `deploy-infra` behind a `deploy_chat` input, "nothing existing changes" | `deploy-infra.yml` is one linear job. Gating the new step leaves every old one running: the whole-blob `stride/prod` rewrite, both full-replacement schedule scripts, an image rebuild and a live ECS smoke, on every chat iteration (**verified**) | `.github/workflows/deploy-infra.yml`; `infra/01_secrets.sh:28-31` | Its own `deploy-chat.yml` on the same OIDC role (§15.3) |
| 4 | Model id read from `ANTHROPIC_CHAT_MODEL` "already in `stride/prod`" | The chat is denied `stride/prod` by §9.3 and invariant 14. Both halves are true of the secret and cannot both be true of the chat, and the prior audit already ruled it "configuration, not a secret" | v5 §2 vs §9.3; `infra/01_secrets.sh:20`; `docs/chat/…AUDIT.md:135` | A Lambda environment variable (§2, §16A A3) |
| 5 | The morning chain is 05:30 / 06:00 / 07:00 / 08:00 / **10:00 tips** | Every time is stale. The chain was re-timed on 2026-08-06 to 04:00 / 04:15 / 04:20 / 05:30 / 07:30 / **08:05**, and the script retires the old schedule names by hand. v5 cited `infra/README.md`, which was not updated — the same failure v5 charged v4 with: trusting a document over the code. The error propagated into the `scoring_in_progress` premise and the watcher thresholds (**verified**) | `infra/07b_fargate_schedules.sh:73-77`, `:101-121` | Corrected in §1, §3.6, §5, §15.4, §18, §19 |
| 6 | `scoring_in_progress` is true between the racecard landing and the tips JSON landing | On a quiet day the tips job returns early and the file is never written, so the flag latches forever and its watcher files a false issue on about one day in three — while v5 praises `missing-run-watch` for being quiet-day-proof (**verified**) | `infra/jobs/handler.py:953-959`, `:507` | `quiet_day` state read from `quiet_<date>.json` (§8.3) |
| 7 | The materialiser "registers with `missing-run-watch`" | That watcher tests ECS-shaped signals against a hardcoded due-time table. A zip Lambda has no family, logs elsewhere, has no fixed due time, and §9.3 denies it the one signal it could have written | `.github/workflows/missing-run-watch.yml`; v5 §9.3 | Its own watcher, or extend that one in the same PR (§4 inv. 16, §15.4) |
| 8 | Per-turn cost about US$0.12 | Same finding as §22 #2, reached independently, with the additional point that §10.2 had no row for the 500/day launch cap and never priced the 300 searches/day | `docs/chat/…AUDIT.md:498-512` | Already corrected in §10.2 |
| 9 | The read-only role is applied "through `apply-migration` with the typed `APPLY`" | `apply-migration.yml` prints the whole migration into the step log of a **public** repository, and the source file opens `CREATE ROLE … PASSWORD 'CHANGE_ME'`. The workflow also rejects any path with a slash and reads only this repository's `migrations/`, so the cited file is not even reachable (**verified**) | `.github/workflows/apply-migration.yml:57,64,66`; `stride-app` `migrations/app/chat_readonly_role.sql` | Role created password-less by migration; password set out of band (§16A A2) |
| 10 | The `puntingform` tool wraps the existing `pf_client` accessors | `_api_key()` reads the process-global environment variable, so per-user keys mean mutating the environment inside a concurrent app: a race between users, plaintext left in the process, and the failure of §9.1's own stated proof. `get()` sleeps 0.4 s per call process-wide, so §10.3's "per key" pacing does not exist. The 31-day wall raises a generic error, so `window_exceeded` has nothing to key on | `server/python/pf_client.py:58`, `:84` | A byte-identical-by-default pipeline PR: `api_key` parameter, async transport, `PFWindowError` (§7, §10.3, §16A A10) |
| 11 | A composer call rewrites the prose while citations are "framed as data" | Citations attach to spans of the **orchestrator's** text. A separate call that rewrites the prose loses the span mapping, so §11's inline `[M1]` markers and §3.2's "provenance on every claim" have no mechanism. The §7 lint only checks links appear in `citations[]`, which by the plan's own standard passes with every citation on the wrong sentence | v5 §7 vs §3.2, §11 | Largely dissolved by §22 #2: blocks are assembled in code and the orchestrator's own text is the prose, so the spans survive. If the optional composer is ever enabled it must carry an immutable claim-id map, and the lint must resolve every marker |
| 12 | Reserved concurrency 3 is a spend control | §13 requires unauthenticated routes, so the Function URL is open and an unauthenticated flood consumes every slot before the JWT check runs. It is an availability decision too | v5 §13 vs §10.1 | Stated as both; CloudFront with a rate-limiting behaviour proposed (§2) |
| 13 | Phase 1 is "local, no AWS"; Cognito is ranked third by blocking; bands block release | Three ordering contradictions. Phase 1 requires a production DDL change. Cognito's proof needs the script that creates Cognito. The band lint is a Tier 0 test running from Phase 1, so bands cannot block release | v5 §16, §17 vs §15.1 | Phase 1 renamed and its two production touches named; bands moved to §16A A9; Cognito already moved by §22 #7 |
| 14 | Staging and prod are two aliases over one set of tables | The budget counter is keyed `scope#date`, so tripping the cap on staging exhausts production's global cap for the day. The load probe's 100 turns also cannot trip a 500/day cap the gate says must be seen to trip | v5 §15.3 vs §10.1, §13 | `STRIDE_CHAT_ENV` key segment; probe sized to a deliberately low staging cap (§15.3) |
| 15 | Chat tests are "collected by the existing `pytest server/python` step" | That step installs neither `boto3`, `moto`, `anthropic` nor `fastapi`, so collection fails at import; it pins Python 3.11 while §2 targets 3.12, and `compileall` runs over the whole repository (**verified**) | `.github/workflows/ci.yml` | Dependencies and the runtime split named in §15.1, to be decided in the PR that adds the first test |
| 16 | The eval suite "is the acceptance test v4 asks for, already written" | The questions carry over; the harness does not. It asserts substrings over a flat string, replays TypeScript fixtures, and posts to `/api/chat`. This plan's contract is `blocks[]`, and §15.6 wants scoring the engine cannot do | `stride-app` `scripts/eval_chat.ts`, `evals/chat/*.jsonl` | Claim qualified in §1; the harness named as new work on the release-gate path |
| 17 | Per-meeting publication is "a pipeline change through the model review path" | It is closer than that. `run_tips_pipeline` already merges track-filtered runs into the canonical file, and `07b` names it the remaining lever and calls it "a handler change with its own rehearsal, not a schedule change" — and `infra/jobs/**` is editable under the normal rules (**verified**) | `server/python/run_tips_pipeline.py:3740`, `:3820-3822`; `infra/07b_fargate_schedules.sh:117-123` | §3.6 corrected; put to the operator as §20 Q10 |
| 18 | Over-scope | Reached independently and agreeing with §22 #7, with the additional point that a new pipeline flag also requires edits to `01_secrets.sh` (off limits unattended), `deploy-infra.yml` and `test_flag_plumbing.py`, none of which v5 mentioned — and that item 9 contained an open investigation ("check what the track-filtered merge writes today") masquerading as a prerequisite | `infra/01_secrets.sh`; `server/python/tests/test_flag_plumbing.py` | Already restructured by §22 #7; the flag-plumbing cost belongs in §16B B7 |
| 19 | `retry_after` = midnight in the user's timezone; DynamoDB TTL as 30-minute expiry | The counter is keyed on a date that may be UTC, leaving a Sydney user blocked ten hours past the time they were told. And TTL deletion is best-effort and lags by hours, so "expired" items stay readable | v5 §12, §13 | Rollover returned from the counter; `expires_at` enforced on read (§12, §13) |
| 20 | "The AWS side is a few dollars… the only lines that could break the tripwire are always-on services" | True as arithmetic, wrong as risk. Steady state is about US$0.90 a month and the US$20 tripwire is calibrated to it; adding a KMS key, a dozen tables, CloudFront and Cognito plausibly multiplies that five to ten times, which makes the forecast alarm routine and desensitises the estate's only AWS runaway detector | `infra/03_notifications.sh:7-9` | A target AWS figure before Phase 2, and §20 Q9 asks whether the tripwire moves |

**What the independent pass confirmed as sound.** The structured-output and
citation incompatibility; the `fair_odds` correction; the market-anchored
calibration correction and the band consequence; the raw `base_*`
rank-agreement workaround; the per-key-only PuntingForm cache; the reuse of
`identity_normalization`, `horse_names`, `target_tracks` and
`prediction_stages`, all confirmed standard-library-only and so genuinely fit
for a slim zip package; and all eight `pf_client` accessor names in §7. It
singled out §15.1's "what would this check still pass with" column as the
best thing in the document and said it should be the template for the rest.

**The pattern across all three audits.** Every serious finding is the same
shape: a claim about the estate taken from a document rather than from the
code that runs. v4 took it from plans that no longer existed; v5 took the
schedule from `infra/README.md` and the migration path from a branch file it
did not open. The repository's own rule covers this exactly — verify content,
not proxies — and a plan is a proxy. **Before implementing any section of
this document, re-read the code it names.**
