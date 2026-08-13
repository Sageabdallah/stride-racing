# 02 — Architecture and Contracts

This file corrects and pins the architecture the rest of the plan runs against.
Where `CLAUDE.md`'s stack summary and this file disagree, **this file wins** —
it was written against the actual repository and infrastructure.

---

# Actual Stack (corrections to the guide)

| Guide claims | Reality |
|---|---|
| FastAPI for serving | Express/TypeScript server (untracked, local) + Python **batch jobs**. There is no Python API service. |
| EventBridge/Lambda orchestration | **GitHub Actions schedules** dispatch work; heavy jobs run as **ECS Fargate tasks** (stride-jobs image, `:latest`); Lambda exists only for small pinned-digest jobs. |

Everything else in the guide's list is correct: CatBoost / LightGBM / XGBoost,
ensemble layer, Neon Postgres, S3 artifacts.

## Deploy paths (load-bearing for every phase)

1. **Actions path** — workflows check out `main` and run immediately after a
   merge. Anything imported by a workflow job goes live on merge.
2. **Fargate path** — the stride-jobs image is rebuilt and shipped only by a
   manual `deploy-infra` dispatch. Merges alone never change Fargate behaviour.
3. Flags ride: GitHub secret → `01_secrets.sh` KEYS → `stride/prod` JSON →
   job handler `setdefault` into every job env. Never write flags directly to
   task definitions; the next deploy wipes them.
4. `infra/` scripts have **full-replacement semantics**. A partial edit
   silently reverts live state. Do not modify them casually.

Operational rule: **no merges or deploys Friday night or Saturday.** A weekday
card proves every change before it faces a Saturday card.

---

# Data Stores

| Store | Contents | Notes |
|---|---|---|
| `race_results_history` (Neon) | one row per runner per race; `race_date` is **TEXT**, `created_at` timestamp | append-only; per-runner dedup in daily importer |
| `betfair_odds_snapshots` (Neon) | decision-time odds snapshots, kinds: `tip_time`, `late_t5`, `morning`, `baseline` | **day zero 2026-08-02**; 60-day retention; known phantom sub-$1.20 rows (issue #123) — always exclude via the unformed-book fence |
| `sectional_times` (Neon) | sectionals from racing.com / NSW / QLD | `race_date` TEXT with occasional malformed values — compare as text, never cast |
| `training_view_v2` (materialized view) | training rows incl. `market_odds`, `tip_time_odds`, `odds_source`, `seconds_to_jump` | schema pinned by `training_view_contract.py` |
| S3 | day artifacts (tips JSON), model artifacts, panel staging | artifact fields are the audit channel |

## Known data limitations (do not rediscover these)

- `total_matched` is 0.00 in **all** Betfair depth rows — there is **no
  usable liquidity data**. Any liquidity guard is a config placeholder, not a
  data-driven check.
- Displayed Betfair prices from unformed books are not executable
  (global rule: never assume displayed = executable). The unformed-book
  guard (`STRIDE_UNFORMED_BOOK_REJECT`) and coherence verdicts
  (`STRIDE_BOOK_COHERENCE`) are the fences.
- The deployed model was trained on ~2× the track universe it serves
  (49.9% of rows in-universe). Deferred by decision; do not "fix" it inside
  a decision-learning phase.
- Snapshot odds history only begins 2026-08-02. Every evaluation phase must
  report its effective date range and refuse to silently pad with SP.

---

# Existing Primitives (reuse; do not duplicate)

Shipped by Phase −1 (PR #131) and earlier, all under `server/python/`:

- `identity_normalization.py` — canonical runner/track/race keys + SQL DDL.
  All new joins use these; never write a new normalizer.
- `prediction_stages.py` — typed per-runner audit trail
  (base model probs → ensemble → MC → adjustments → final_decision).
- `decision_contract.py` — `reconcile_crowd_bet` / `demote_active_bet`;
  the single place a bet is refused. The decision layer plugs in here.
- `staking_controls.py` — drawdown breaker + exposure caps (deterministic,
  already ordered before exports/DB writes).
- `release_manifest.py` — atomic release-bundle contract, **dormant**
  (`STRIDE_RELEASE_MANIFEST_KEY` unset). Phase 8 activates it; it already
  reserves a `decision_model` artifact slot.
- `training_view_contract.py` — fail-fast schema validation for retraining.
- `nan_contract.py` / `winner_pattern_features.py` — NaN-preservation rules;
  `prior_pb_close_underreaction` is deliberately dormant (SP leakage).
- Calibration: OOF isotonic + `double_calibrator` in `ml_model.py`.
- MC engine: deterministic seed from race identity (`_race_seed`, PR #121).
  Six verified defects are deferred pending measurement (issue #124) — the
  decision layer consumes MC output as-is and must not quietly compensate.

---

# Contracts Pinned Here

1. **Decision time** = the `tip_time` snapshot timestamp for the race. If a
   phase needs a different convention it must be a documented config change,
   not an inline choice.
2. **Race identity** = `canonical_race_key(date, track, race_number)`;
   stored snapshot IDs keep their legacy `source_race_key` shape.
3. **Runner identity** = `normalize_runner_key` (country suffix stripped).
4. **One active bet per race** in V1; stake sizing is deterministic and
   owned by the risk engine, never the policy.
5. **Commission / slippage / bankroll / Kelly fraction** have no repo values
   today. They are `null` config placeholders requiring a project decision
   (see `16_…PROTOCOL.md` — No Silent Assumptions).
6. New scheduled jobs must register with the watcher layer (missing-run
   watch + ECS failure watch) and emit **content-level** postconditions —
   an exit code is not evidence (see `14_TESTING_AND_OBSERVABILITY.md`).
