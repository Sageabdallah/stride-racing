# Stride — Backend Plan v4

**Vision: the go-to place to ask anything about Australian horse racing.**

Users bring their own PuntingForm key, authenticate, and ask questions in
plain English. Stride answers from three grounded sources — PuntingForm data
(user's key), Stride's own ML models (pre-computed race predictions), and
cited web search — with explicit provenance on every claim.

Priorities: **credential security > UX > robustness**. v3 made UX lead; v4
adds a shared-access key for keyless users and gives the model ensemble a
voice.

---

## 0. Product principles (the "not tacky" contract)

1. **Data-first, always.** Every visual or verbal flourish must be grounded in
   a real tool result. The moment we dress up ungrounded text, we're a gimmick.
2. **Quiet confidence.** Measured Australian racing-analyst voice. No hype, no
   bookmaker slang-for-clout, no emoji fireworks, no confetti. Typography and
   well-set numbers are the aesthetic.
3. **Honesty is a feature.** Saying "my model doesn't cover this meeting" or
   "your plan doesn't include sectionals" plainly builds more trust than a
   smooth non-answer. The honesty gradient (§10) is brand, not fallback.
4. **Probabilities, not tips.** Model output is framed as analysis with a
   responsible-gambling notice — never as "bets" or "tips".
5. **The racing calendar is the home screen.** The product breathes with the
   real racing week. Ambient, current, specific — never a generic empty box.

---

## 1. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| LLM | Claude (Anthropic Messages API) | Tool-use with strict schemas (parseable plans); server-side web search returns source URLs natively |
| Web fallback | Claude web search tool | ChatGPT-style citations without operating a search API |
| Key encryption | AWS KMS envelope encryption behind `KeyProvider` | Per-user encryption context; CloudTrail decrypt audit; local dev provider refuses `ENV=production` |
| Multi-tenancy | Single shared FastAPI, OIDC/JWT | No per-user VMs |
| Model output | Win/place probabilities per runner (XGB+LGBM+CatBoost ensemble) + fair odds | Natural conversational unit; exotics later |
| Prediction production | Batch pipeline, pre-computed for upcoming races | Chat path = DB read; matches "races in the pipeline" |
| Model features | Stride-owned pipeline PuntingForm key | Tier-independent model answers; **ToS gate (§15)** |
| **Key-optional onboarding (v3)** | Product works without a key (model + web tiers); key unlocks personal-data tier | Value-first onboarding; key ask happens at the moment it's useful (§2.1) |
| **Answer contract (v3)** | Block-based structured answers, not markdown strings | Native rendering, consistent quality, frontend stays dumb (§2.2) |
| **Shared access key (v4)** | Keyless users are served PuntingForm data through a Stride-owned pool key — budget-capped, cache-first, disclosed | Value-first without a key wall; security identical to user/pipeline keys (§8). **ToS gate (§15)** |
| **Model voice (v4)** | The ensemble's internal state (member agreement, calibration, data confidence) deterministically drives tone | The voice *is* the honesty — no manufactured personality (§2.9, §7) |

---

## 2. UX surfaces (the v3 meat)

### 2.1 First run: value before the key — three access modes

Asking for an API key is the highest-friction, highest-anxiety moment in the
product — so no user ever hits it as a wall.

- **Mode A — own key.** Full personal-data tier against the user's own quota
  and subscription. The best experience; always offered, never forced.
- **Mode B — Stride shared access (default when no key).** PuntingForm-backed
  answers are served through a **Stride-owned pool key** — budget-capped per
  user and globally, cache-first, abuse-monitored (§8, §9). The user is told
  plainly: *"answered via Stride's data access — add your own PuntingForm key
  for full limits."* When their shared allowance is exhausted, answers
  degrade to model/web tiers with disclosure and a natural upsell moment.
- **Mode C — keyless.** Model + web tiers only (e.g., global pool budget
  exhausted). Still genuinely useful on raceday.

The pool key is infrastructure, never identifiable as an individual's key;
to users it is simply "Stride's data access." Key entry UX (when they choose
it): explain why in one sentence, validate instantly with a friendly probe —
*"Key works. You're on the Pro plan: form data and sectionals included, ~400
requests left today."* Existing hard rules unchanged: step-up auth for key
operations, one-click delete, KMS envelope encryption.

### 2.2 The chat experience: block-based answers

The backend returns **typed blocks**, not a markdown string. The frontend
renders native components; quality is enforced by the contract, not by
prompt luck. Block types:

| Block | Content | Example moment |
|---|---|---|
| `prose` | Grounded natural language | The analytical voice |
| `field_table` | Race field with p_win, fair odds, top factor per runner | "Who wins R5?" — a quiet, well-set table, not a wall of text |
| `form_guide` | Line-per-start career form | "How did she go last prep?" |
| `comparison` | Side-by-side runners or model-vs-market | "Him vs the favourite" |
| `divergence` | Where model probability exceeds market-implied probability | "Where's your model disagree with the market today?" — the killer feature, framed as data observation with RG notice |
| `briefing_card` | Morning briefing payload (§2.6) | App open |
| `capability_prompt` | Contextual key/tier ask | Key-optional flow (§2.1) |
| `notice` | Staleness, tier, partial-result disclosures | "Scored 09:12, before the scratching of X" |
| `sources` | Provenance + citations | Every answer |

Every answer also carries 2–3 **grounded follow-ups** (§2.3) and a
`confidence` grade. Provenance tiers: `puntingform | stride_model | hybrid |
web_fallback | model_unverified | none`.

### 2.3 Follow-ups and conversation memory

Racing conversation is naturally contextual: *"Who wins R5?" → "What about
the second fave?" → "Has it won here before?"*

- **Session entity memory** (Postgres): horses, races, tracks, dates mentioned
  in the session, plus the last resolved `race_id`. Passed to the orchestrator
  each turn so pronouns and ordinal references resolve correctly.
- **Grounded follow-up suggestions**: the orchestrator emits 2–3 next
  questions, each validated against (a) the user's capability profile and
  (b) pipeline coverage — we never suggest a question we can't answer.
  Discoverability without clutter; the cheapest fallback is the one never
  needed.
- **Fuzzy entity resolution**: horse names from memory are often near-misses
  ("Nature Strip" vs "Nature Stripe"); tracks have nicknames ("Headquarters"
  = Flemington). Resolver does alias + fuzzy matching, and uses `clarify`
  with ranked candidates rather than guessing.

### 2.4 Time and racing-calendar awareness

Racing is intensely temporal. Every orchestrator call is temporally grounded:
user timezone, current time, racing calendar state (what's on today, what's
upcoming, what just ran). "Saturday at Flemington" resolves to a concrete
meeting; "last spring" triggers `clarify` on year. Freshness is disclosed
habitually: *"Model scored 09:12; track now Soft 6 after the 11:40 update."*

### 2.5 Model explainability as conversation

"Why do you rate her?" is the product's soul. Answers use precomputed top-k
SHAP contributions in prose ("first-up record, the weight drop, and the
barrier"), and each factor is **drillable**: *"the weight drop"* → follow-up
pulls the actual weight history via the user's key (or a `capability_prompt`
if keyless). Explanation is a conversation that descends into data, not a
static dump.

### 2.6 Morning briefing & watchlists (the retention loop)

- **Watchlists**: follow horses (and tracks). Stored per user.
- **Morning briefing**: per-user, generated when the pipeline finishes
  scoring (or on first open after): today's meetings with model coverage,
  top-rated runner per meeting, any watched horse racing today, overnight
  scratchings affecting scored races, one `divergence` highlight where odds
  are available. One quiet screen. No push noise unless the user opts in.
- **Watch events**: when the pipeline scores a race containing a watched
  horse, a `briefing_card` update is queued. "A horse you follow is racing
  today at Randwick — rated 28%." This is what brings people back, and it's
  entirely data-grounded.

### 2.7 Errors as conversation

Errors arrive inside the chat voice, with recovery actions, and degrade into
alternative value: *"You're rate-limited on PuntingForm for ~20 minutes —
meanwhile my model's already rated today's cards. Want the Randwick
preview?"* The error taxonomy (§11) gains `key_not_connected` (→
`capability_prompt`) and keeps the v2 set. A failure is a routing decision,
not a dead end.

### 2.8 Speed and streaming

- `GET /bootstrap` on app open: today's meetings, model coverage flags,
  briefing status, key status. First meaningful paint without a round-trip
  storm; race resolution becomes effectively instant.
- SSE status lines are **meaningful and specific** ("checking the Golden
  Eagle field", "consulting Stride model v1.4.0"), never "loading…".
- SSE resume: events carry sequence ids; a dropped connection replays from
  last-seen id. Chat that survives a tunnel on the way to the track.

### 2.9 The model's voice (giving the ensemble a brain)

Design question: *if XGBoost, LightGBM and CatBoost could answer, how would
they sound?* Like what they are — measurement instruments. Not tipsters, not
hype men. Three principles make the voice emerge from real model internals
rather than manufactured quirk:

1. **Agreement is confidence.** Every prediction stores per-member
   probabilities. When the three models agree, the tone is assured: *"all
   three models land within two points of each other."* When they diverge,
   the answer says so and says why: *"headline 27%, but my models are split —
   CatBoost has him at 33% on class, XGBoost at 21%, mostly the wide gate."*
   Disagreement is surfaced, never averaged away silently — the average is
   shown, the split is told. This is the most honest voice possible: it is
   literally the ensemble's internal state rendered as language.
2. **Calibration is tone.** Language bands are a deterministic function of
   calibrated probability: ≥0.40 "strongly fancied by the model"; 0.25–0.40
   "rates on top"; 0.15–0.25 "a genuine each-way profile"; 0.08–0.15 "a
   roughie with one factor in its favour"; <0.08 "the model can't find much."
   The LLM may not upgrade or soften a band — tone is a function of the
   number, enforced by the composer and linted in CI (§14).
3. **Humility is character.** The models only see their features: they don't
   watch replays, don't hear stable mail, don't know intent. Asked beyond the
   features, the voice says *"that's outside what I can measure"* and routes
   to form data or web. A pipeline-computed **data-confidence flag**
   (feature completeness) powers honest hedging: *"first-up, no public trials
   in my data — treat this 22% cautiously."*

Persona texture: a measured Australian racing analyst — fluent in the
vernacular (get-out stakes, swoopers, blackbookers, midweeks, the carnivals)
without performing it; dry rather than cute; explains jargon gently for
newcomers; never tipping language ("moral", "good thing", "can't lose").
First person as Stride; internals quoted as "my models." Numbers always from
tool results — inventing a figure is a release-blocking eval failure (§14).

Worked examples of the voice:

- *Agreement:* "I rate King's Legacy clearly on top — 38%, and all three
  models land within two points. It's the weight drop and the barrier doing
  the work."
- *Disagreement:* "Headline 27%, but my models are split — CatBoost has him
  at 33% on class, XGBoost at 21%, mostly the wide gate. Read that as a
  genuine each-way profile, not a confident top pick."
- *Humility:* "She's first-up with no trials in my features — my 22% is thin
  on data, so treat it cautiously. Want me to check the web for trial
  reports?"

### 2.10 Shareable answers

`POST /share` snapshots an answer (blocks + provenance) to an immutable,
public, read-only card — the organic growth loop ("look what Stride rates in
the Cup"). Shares **strip all user-specific material**: no key status, no
tier info, no quota, no session data. Model provenance (version, scored_at)
stays — it's the credibility. This is a marketing surface, so the
anti-extraction caps (§7.5) apply to shared field tables too.

---

## 3. Invariants

1. **Two-key separation.** User key never in any LLM prompt/response/log/
   trace/error. Claude sees schema + question only.
2. **Three-key separation.** Pipeline key managed identically (KMS envelope,
   service-scoped context, canary-tested); never enters the chat path.
3. **SSRF containment.** Executor reaches allowlisted PuntingForm hosts only.
4. **Quota protection.** Internal budgets kill malformed loops before they
   touch user quota. Model lookups cost zero user quota.
5. **Honest provenance.** Every claim sourced: PuntingForm, Stride model
   (version + timestamp), cited web, or explicit "unverified". Never blended.
6. **Probabilities, not tips.** RG notice on all model answers.
7. **(v3) No dead ends.** Every error, gap, or limitation routes to the best
   remaining tier with disclosure.
8. **(v4) Pool key = service key.** The shared access key receives identical
   KMS, canary, and in-memory treatment as user and pipeline keys, plus
   stricter budgets. It is never identifiable as an individual's key.

---

## 4. Architecture

```
                 OFFLINE (pipeline)                       PER-USER JOBS
 ┌────────────────────────────────────────┐   ┌──────────────────────────┐
 │ Scoring pipeline (pipeline key,        │   │ Briefing generator        │
 │ executor-grade client, KMS in-memory): │──▶│ (on scoring completion /  │
 │ features → XGB/LGBM/CatBoost ensemble  │   │  first open after)        │
 │ → p_win/p_place/fair odds → top-k SHAP │   │ watchlist matcher         │
 └───────────────┬────────────────────────┘   │ settlement & calibration  │
                 ▼                             └───────────┬──────────────┘
   predictions / races / model_registry / settlements      │
                                                           ▼
                                            briefings / watch_events

                 ONLINE (chat path)
 POST /chat (SSE) ──► ┌──────────────────────────────────────────────┐
                      │ Orchestrator (Claude, keyless)                │
                      │  • temporal grounding (calendar, timezone)    │
                      │  • session entity memory (horses/races/tracks)│
                      │  • intent router + race/entity resolver       │
                      │  • bounded agent loop (max N steps)           │
                      │  • answer composer → block contract           │
                      │  • grounded follow-up generator               │
                      └──┬─────────┬───────────────┬─────────────────┘
               toolbelt: │         │               │
                  query_model  query_puntingform  web_fallback / clarify
                         │         │               │
                         ▼         ▼               ▼
                   Model Svc   Validator+       Claude + web
                   (DB read)   Executor         search (citations)
                               (user key,
                                KMS, budgets)
                         └────┬───┴───────────────┘
                              ▼
                  blocks + provenance → SSE (resumable) → client

 Key Service (separate module): user keys + pipeline key, KMS, validation,
 tier probing, audit. Nothing else imports it.
```

---

## 5. Backend changes driven by UX (traceability)

| UX surface | Backend change |
|---|---|
| Value-first onboarding (§2.1) | Intent router works keyless; tools that need a key emit `capability_prompt` instead of failing |
| Block answers (§2.2) | **Answer contract rewrite**: composer outputs typed blocks; per-block schema validation; per-block provenance mapping |
| Follow-ups (§2.3) | `session_entities` table + resolver input; follow-up generator constrained by capability profile × pipeline coverage |
| Entity resolution (§2.3) | Alias/fuzzy index over horses/trainers/jockeys/tracks (seeded from pipeline + PuntingForm); clarify candidates |
| Time awareness (§2.4) | Calendar service (meetings, race status); user timezone in profile; grounding injected into every orchestrator call |
| Drillable explanations (§2.5) | Model Service exposes factor→data pointers; orchestrator chains them through the executor when keyed |
| Briefing & watchlists (§2.6) | Briefing generator job; `watchlists`, `briefings`, `watch_events` tables; pipeline-completion hook |
| Errors as value (§2.7) | Error taxonomy returns `fallback_offer` payloads, not just codes |
| Speed (§2.8) | `GET /bootstrap` aggregate; SSE sequence ids + replay buffer (Redis) |
| Share (§2.10) | `POST /share` → sanitized immutable snapshot; public read endpoint; share-scoped anti-extraction caps |
| Shared access (§2.1) | Key-selection layer in the executor: own key → pool key → keyless tiers; pool budgets, cache-first policy, abuse detection |
| Model voice (§2.9) | Per-member probabilities + disagreement + data-confidence stored by the pipeline; composer tone-band mapping + band-consistency lint |

---

## 6. Orchestrator (updated)

Unchanged core (§4 of v2): toolbelt, five intents, bounded loop, race
resolution. v3 additions:

- **Context bundle per turn**: `{question, now_in_user_tz, calendar_state,
  session_entities, capability_profile, pipeline_coverage}`. Cheap to
  assemble; transformative for correctness.
- **Keyless planning**: planner knows which tools need a key; when unkeyed,
  it plans around them and emits `capability_prompt` at the natural moment.
- **Composer as separate stage**: tool results → typed blocks (schema-
  validated) → prose glue with per-claim provenance. Deterministic table
  rendering; LLM only writes prose around grounded numbers.
- **Follow-up generator**: 2–3 suggestions, filtered against capability ×
  coverage, deduped against conversation history.

---

## 7. Model layer (unchanged core + drill-down)

As v2: batch pipeline, ensemble, registry, staleness flags, settlement loop.
v3 adds:

- **Factor pointers**: each top-k factor stores how to fetch its evidence
  (e.g., `weight_drop` → horse weight history endpoint + params), powering
  §2.5 drill-downs.
- **Divergence support**: where market odds are available (pipeline pull or
  user's tier), store market-implied probabilities alongside model
  probabilities; the `divergence` block compares them with the RG notice.
- Briefing-completion hook publishes `scoring_done(meeting_date)` events.

v4 adds (the voice, §2.9):

- **Per-member probabilities** (`member_probs`: xgb / lgbm / catboost) and a
  **disagreement score** (spread across members) stored per runner.
- **Data-confidence flag** per runner (feature completeness — e.g., first-up
  with no trials → low).
- The composer maps `(p_win, disagreement, data_confidence, calibration)` to
  a deterministic tone band; prose must stay inside the band (linted, §14).

---

## 8. Security design (deltas only; v2 §6 stands)

- **Share privacy (new)**: share snapshots are built by a dedicated
  sanitizer that allows only answer blocks + model/web provenance; unit
  tests assert no user identifiers, key status, tier, or quota fields can
  appear. Share URLs are unguessable ids.
- **Briefing/watch jobs** run with the user's authority context but never
  decrypt the user's key (briefings use model + cached public data only —
  personal-data pulls happen live in chat, not in background jobs).
- **Shared pool key (v4, §2.1 Mode B).** Stored in `service_keys` with
  encryption context `{service: "shared_pool"}` — identical KMS envelope,
  in-memory-only decrypt inside the executor, canary-shaped in CI. Key
  selection order per request: user's own key → pool key (budget permitting)
  → keyless tiers. Pool traffic runs in its own token buckets so it never
  competes with own-key traffic; abuse detection (scraping patterns,
  scripted enumeration) throttles or suspends shared access per user. The
  pool key's identity is never exposed — responses and provenance say
  "Stride's data access." Hard global daily ceiling with alerting; on
  exhaustion, degrade to model/web tiers with disclosure.
- Anti-extraction caps now cover chat, shares, and briefing payloads.
- Canary suite extended to share sanitizer paths.

## 9. Budgets (deltas; v2 §7 stands)

- Briefing generation: cached per user per day; zero PuntingForm calls.
- `GET /bootstrap`: fully cacheable except key status; single-digit ms target.
- SSE replay buffer: bounded (last K events per job, short TTL).
- **Pool key budgets (v4):** global token bucket sized under PuntingForm's
  rate limits; hard global daily ceiling (alert + auto-cutover to keyless
  tiers); small per-user daily cap on pool-backed answers (it's for
  evaluation/light use — heavy users are nudged to Mode A); cache-first
  policy — pool requests must check the shared cache before any upstream
  call, so repeated identical queries within TTL cost zero upstream.

## 10. Provenance & honesty gradient

Degradation ladder, each step disclosed: **stride_model → puntingform →
web_fallback → model_unverified → honest "couldn't find it."** Mode B users
reach tier 2 via the pool key (disclosed as "Stride's data access",
budget-capped); fully keyless users start at tier 1/3. Hard rules stand: no
verifiable link from memory; citations never fabricated; fallback opt-out
per query.

Answer envelope v3 (abridged):

```json
{
  "blocks": [
    { "type": "prose", "text": "Stride's model rates Her Majesty 31% (fair $3.20) — first-up record, the weight drop, and the barrier are doing the work[M1]." },
    { "type": "field_table", "race_id": "au.randwick.2026-09-12.r5",
      "rows": [ { "runner": "Her Majesty", "p_win": 0.31, "fair_odds_win": 3.23, "top_factor": "first_up_record" } ],
      "note": "Model v1.4.0, scored 09:12 AEST", "stale": false },
    { "type": "sources", "items": [
        { "id": "M1", "type": "stride_model", "model_version": "1.4.0", "scored_at": "..." } ] }
  ],
  "follow_ups": ["Has she won at 1600m?", "Where does the model disagree with the market today?"],
  "provenance": { "tier": "stride_model", "fallback_reason": "none" },
  "responsible_gambling_notice": true,
  "confidence": "high"
}
```

## 11. Error taxonomy (extended)

| code | action |
|---|---|
| `key_not_connected` | `connect_key` (renders `capability_prompt`) |
| `key_invalid` (401) | `reenter_key` |
| `rate_limited` (429) | `wait_retry_after` + `fallback_offer` |
| `tier_restricted` (403) | `show_upgrade_note` + fallback answer |
| `plan_failed` | `rephrase` |
| `ambiguous_query` / `race_not_found` | `clarify` with options |
| `no_prediction` / `prediction_stale` | answer via other tiers, disclosed |
| `upstream_error` | `retry` |
| `no_data_no_fallback` | `enable_fallback` |

## 12. API surface & data model (v3)

| Endpoint | Purpose |
|---|---|
| `GET /bootstrap` | Meetings + model coverage + briefing status + access mode (`own_key` / `stride_pool` / `keyless`), one call |
| `POST /keys`, `GET /keys/status`, `DELETE /keys` | As v2 (step-up auth) |
| `POST /chat`, `GET /chat/{job_id}/events?after={seq}` | SSE with replay |
| `GET /suggestions` | Capability × coverage aware |
| `GET /races/today` | Meetings/races with coverage flags |
| `GET /briefing/today` | Personal morning briefing |
| `POST /watchlist`, `DELETE /watchlist/{id}`, `GET /watchlist` | Follow horses/tracks |
| `POST /share`, `GET /share/{id}` | Immutable sanitized answer cards |
| `GET /healthz`, `/readyz` | Ops |

```sql
-- v2 tables stand (users, api_keys, service_keys, audit_log, query_log,
-- cache, model_registry, races, predictions, results_settlements) plus:
user_prefs(user_id, timezone, favourite_tracks jsonb, briefing_opt_in bool)
session_entities(session_id, entities jsonb, updated_at)       -- horses/races/tracks
entity_aliases(entity_type, alias, canonical_id)               -- fuzzy resolution
watchlists(id, user_id, entity_type, entity_id, created_at)
briefings(user_id, date, payload jsonb, generated_at)
watch_events(id, user_id, race_id, runner_id, kind, payload jsonb, created_at, read_at)
shares(id, payload jsonb /* sanitized blocks */, created_at)
shared_usage(user_id, date, pool_calls int)                          -- pool budgets (v4)

-- v4: predictions gains the voice signals (§2.9, §7):
--   member_probs jsonb          -- per-member p_win: {xgb, lgbm, catboost}
--   disagreement_score float    -- spread across ensemble members
--   data_confidence float       -- feature completeness (thin data → low)
```

## 13. Caching (deltas)

- Bootstrap payload: cacheable per minute except key status.
- Briefings: per user per day; invalidated on scoring_done events.
- Divergence data: TTL tied to odds freshness; disclosed when stale.
- Rest as v2 (shared PuntingForm cache subject to ToS; answers never cached).

## 14. Testing & compliance (additions; v2 §13 stands)

- [ ] **Block-contract tests**: every answer validates against the block
      schema; every numeric token in prose traced to a tool result (anti-
      hallucination lint)
- [ ] **Routing + shape evals**: golden set (~300 racing questions across
      personas) scored for intent, block choice, follow-up quality
- [ ] **Tone evals**: persona conformance (measured, no hype, RG framing)
- [ ] **Keyless journey tests**: full first-run with no key; capability
      prompts appear at the right moments; no 401 ever shown raw
- [ ] **Follow-up grounding**: suggestions always answerable given
      capability × coverage
- [ ] **Share sanitizer tests**: no user/tier/quota leakage; unguessable ids
- [ ] **SSE replay tests**: reconnect mid-stream resumes without duplication
- [ ] Briefing correctness: watched horse in scored race ⇒ event emitted;
      no user-key decrypts in background jobs (assert via KMS audit)
- [ ] Pool-key security (v4): canary shape in CI; budget enforcement (global
      ceiling + per-user cap); cache-first assertion (identical query within
      TTL ⇒ zero upstream calls); pool key identity never appears in any
      response, error, or share card
- [ ] Voice tests (v4): tone-band determinism (probability ⇒ fixed band);
      band-consistency lint on prose; disagreement disclosed whenever spread
      exceeds threshold; data-confidence hedging present on thin data
- [ ] ToS review (launch blocker): credential storage, proxying, caching,
      **pipeline key + commercial derived predictions** (§15)
- [ ] RG review: disclosure placement on model/divergence/briefing surfaces

## 15. Open risks (updated)

1. **ToS on the shared pool key (v4) — now the biggest gate.** Serving many
   users' queries through one Stride-owned key is very likely redistribution
   under PuntingForm's terms. Before Mode B launches: obtain written
   commercial clearance (or a partner/multi-tenant arrangement). Until
   cleared, ship Mode B tightly capped or keep keyless = model/web only.
   The same conversation should cover the pipeline key and derived
   predictions.
2. **Odds availability for divergence** — if odds data is tier-gated or
   licensed out, `divergence` degrades to model-only gracefully.
3. **Entity resolution quality** — horse-name fuzziness is the top clarify
   trigger; measure and iterate.
4. **Routing quality is the product** — the golden-set eval is the release
   gate, not a nice-to-have.
5. **Claude availability** — single point of failure for routing/planning/
   fallback/composition; abstraction later if justified.
6. **Model drift** — settlement loop + auto-pin mitigates; needs ownership.
7. **Model extraction** — caps + monitoring mitigate, not eliminate.
