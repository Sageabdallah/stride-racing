# STRIDE Merge & Execution Plan

*Issued 2026-07-29. Companion to `PROGRAM_STATUS.md` (PR #20) and the two
prompt packs. This document answers one question precisely: **what merges
now, what waits, and what each wait is actually gated on** — so that nothing
calendar-dependent is merged early and nothing merge-ready is held back by a
gate that doesn't apply to it. It then defines what success looks like
against the architecture and the program objective, and the invariants that
keep the whole path free of data leakage.*

---

## 0. Goal and objective (restated, so every decision below traces to it)

The program objective is a **profitably tipping system whose measured edge is
honest**: daily selections produced by the ensemble model, priced against
tip-time odds, settled net of commission, and eventually retrained on
odds the model could actually have known at tip time. The architecture this
runs on: React frontend + Express server with an **in-process scheduler**
driving the daily pipeline (consensus agent → tips → results), Python
ML/Monte-Carlo scoring, and a remote Neon PostgreSQL database. This Mac is
the sole runner; there is currently no external process manager (OP-1
finding — being fixed at the deploy session).

Everything below is in service of two things:

1. **Restart the clocks safely** — daily tips, odds snapshots, ledger
   settlement — because every downstream gate (shadow weeks, ablation,
   retrain) is measured in *accrued clean race days*.
2. **Guarantee the retrain that follows is leak-free** — the current model's
   headline numbers are contaminated by train-time information that does not
   exist at tip time (§5).

---

## 1. The merge-safety rule (and a correction to the standing position)

**Rule: the calendar gates ACTIVATIONS and EXECUTIONS, never merges.**
Every PR in the merge-now bucket either changes no runtime behavior or
defaults to today's behavior byte-identically (pinned by tests), with the
new behavior behind an env flag that stays OFF.

**Correction.** The board previously said PRs #3, #5, #11 "wait on shadow
evidence to merge." That position was internally inconsistent: the code that
*writes* the shadow evidence (the calibrator comparison JSONs in #3, the
`serve_liveness_shadow_*.json` writer in #11) lives **inside those PRs**,
flag-gated and publishing the legacy result. Held unmerged, the evidence
could never accrue and the gates could never clear. The correct sequence is:

> audit → merge with live-flags OFF and shadow-flags ON → deploy → accrue
> ≥5 clean race days → review against the pre-registered flip criteria
> (PR #18) → flip the live flag.

So #3 and #11 are blocked on nothing at all; the calendar gates their
**flag flips** (§4).

**Second correction — stale PR descriptions.** The GitHub bodies of #3, #4,
and #5 still carry recovery-era text ("WIP", "draft to prevent loss", "not
yet independently verified"). That text predates the completion and
verification work: the pushed branch tips (roi/02 `58057c6`, roi/03
`a0cd755`, roi/05 `316ef1f` — confirmed against origin 2026-07-29) are
exactly the commits the audit ledger records as fully verified on
2026-07-28. All three are merge-ready; #5 additionally needs its draft flag
flipped to "ready for review". Classify PRs by their audited bytes, never by
their description text.

---

## 2. Bucket A — MERGE NOW (no calendar dependency, no other blocker)

All are audited, default-behavior-preserving, and safe to merge today.
Order matters only where stacked.

### stride-app (merge in this order)

| PR | What | Why it's safe now |
|---|---|---|
| #1 | chat/05 eval harness | Offline by construction; live mode double-gated behind env vars. |
| #2 | OPS prompt pack doc | Docs only. |
| #3 | OP-2 python interpreter resolution | Behavior change is interpreter *selection* + one structured failure log line; mocked tests, zero real python. |
| #4 | OP-5 ordered catch-up + consensus gate | Stacked on #3 — **merge after it.** The two pre-existing catch-ups are test-pinned identical; consensus-failure gate proven reachable (26/26 green). |

These two scheduler PRs are not optional polish: **the quality of the entire
accrual window depends on them.** Without #3 the scheduled python tasks run
on a broken interpreter; without #4 a boot after 10:00 either skips the
day's tips or (pre-fix) would have run tips without consensus and filled the
window with NO_BET junk days.

### stride-racing (merge in this order)

| PR | What | Notes |
|---|---|---|
| #1 | roi/01 ledger: CLV + net-of-commission settlement | Starts honest settlement. |
| #2 | roi/04 as-of odds snapshot capture | **Starts the retrain clock** — nothing accrues until this is deployed. |
| #16 | Snapshot-odds training switch + SP ablation harness | Stacked on #2 (retargets to main when #2 merges). `STRIDE_TRAIN_ODDS_SOURCE` defaults `legacy`, byte-identity pinned. The ablation **run** waits on the calendar (§4); the code does not. |
| #6 | Deploy preflight + liveness verifier | Ops tooling. |
| #9 | Tier/crowd-gate P&L attribution | Analysis script; read-only. |
| #10 | ops/deploy-prep: runbook + feature liveness audit | |
| #12 | Provenance sweep docs | Merge after #10 (ordering only). |
| #17 | Retrain promotion preflight gate | Stacked on #10 (retargets when #10 merges). The gate that mechanically enforces "never promote directly." |
| #13 | days_since_run wall-clock fix | **Leak fix** (§5) — must be in before any retrain. |
| #14 | td_* as-of monthly profile buckets | **Leak fix** (§5) — must be in before any retrain. |
| #4 | roi/02 backtest statistics | Verified 2026-07-28 at tip `58057c6` (all 4 acceptance criteria; commission cascade recomputed independently). Body text stale — the code is done. |
| #7 | Ship gate: below-zero-CI → HOLD | Stacked on #4 (roi_stats.py lives there) — merge after it. Stricter-only, differentially proven. |
| #8 | Reportability floor single-sourced | Stacked on #4 — merge after it. Zero behavioral change (200 unchanged). |
| #3 | roi/05 calibrator + renormalisation | Verification FULLY green 2026-07-28 at tip `316ef1f`. Both behavior flags default OFF (byte-parity test-pinned); shadow comparison ships inside it. Body text stale. |
| #5 | roi/03 serve-time probability fixes | Completed (`a0cd755`) + verified 2026-07-28: NaN contract, single builder, 10/10 parity tests; the default-ON parity flag is provably inert. **Flip draft → ready for review**, then merge. Body text stale. |
| #11 | Serve the 15 dead features, flag-gated | Fully audited. Stacked on #5 — merge after it, `STRIDE_SERVE_LIVE_FEATURES` OFF, `_SHADOW` ON. Its shadow writer is the #18 flip evidence source. |
| #19 | 12P-7 betfair coverage audit | Audited incl. the two SQL fixes; the prod **run** needs the standing read approval, the merge doesn't. |
| #20 | PROGRAM_STATUS.md | Docs. |

**Consequence worth stating loudly:** the moment #5 and #11 merge, the hard
gate on **12P-8 (winner-pattern rescue)** clears — that Kimi dispatch
becomes available at the merge session itself, with no calendar wait.

---

## 3. Bucket B — BLOCKED, but NOT by the calendar

These must not merge yet, and waiting on the calendar would not help them.
Each has a named unblock action and owner. (After the stale-description
correction in §1, only two PRs remain here — both waiting on operator
decisions, neither on code or calendar.)

| PR | Blocker | Unblock action | Owner |
|---|---|---|---|
| #15 (prune 45 dead features) | Title's own rule: "approval = merge." | Operator reads the pruning list and approves. | Sage |
| #18 (pre-registration + flip criteria) | 5 unresolved `[SAGE-APPROVAL:]` markers (min settled-bet count, canary N days, pause rule, transition-rate cap, top-3 flip cap). PR #17's `gate_preregistration` counts them mechanically. | Operator resolves each marker with a value. **Do this before the accrual window opens** — pre-registration only protects decisions made before outcomes are visible. | Sage |

---

## 4. Bucket C — CALENDAR-GATED (needs pipeline runs with real data)

**None of these are merges.** They are flag flips, script runs, and
dispatches that consume accrued data. Attempting any of them early produces
exactly the kind of untrustworthy evidence this program exists to eliminate.

| Action | Gated on | Measured by |
|---|---|---|
| Flip `STRIDE_RENORMALISE_FIELD` (from #3) | ≥5 clean race days of shadow calibrator JSONs; renormalised Brier ≤ current; tier-transition matrix review per #18's criteria. Also the 500-row `STRIDE_CAL_MIN_COVERAGE` calibrator coverage gate. | OP-4 check-ins (counts, not impressions). |
| Flip `STRIDE_SERVE_LIVE_FEATURES` (from #11) | ≥5 days of `serve_liveness_shadow_*.json`; no day errored to legacy; delta distribution stable; top-3 flip rate within the #18-registered cap. | OP-4. |
| Run `odds_ablation.py` (from #16) | The accrual window itself — its A/B/C arms need weeks of snapshot-sourced rows to say anything. B-vs-C is the honest SP-contamination estimate. | Registered window dates. |
| task-12 retrain + promotion | 4–6 weeks of snapshot odds; `retrain_preflight.py` (from #17) all green — including ZERO_AT_SERVE = 0, FEATURE_COLUMNS lockstep, ship-gate SHIP verdict on the registered band, resolved pre-registration. | Preflight boards, exit code, no pipes. |

**Moved out of this bucket:** 12P-8 (winner-pattern rescue) is gated on
#5 + #11 being merged — a merge-session gate, not a calendar one. It is
dispatchable to Kimi the same day the merge session completes.

**The single most important discipline:** nothing in this table is
shortened, widened, or re-banded after data exists. That is what PR #18
fixes in writing, which is why its markers must be resolved *first*.

## 4b. The retrain gate, consolidated (audit of 2026-08-01)

A full audit — pushed-byte review of the open PRs, a leak audit covering
the new Punting Form stack, and a read-only measurement of prod against
every gate in §4 — was run on 2026-08-01. This section records where the
data actually stands and the prerequisites that audit ADDED to the
retrain. It supersedes any looser statement elsewhere in this document.

### Measured accrual state (prod, 2026-08-01)

| Gate | Requires | Accrued | What starts the clock |
|---|---|---|---|
| Snapshot odds for the retrain | 4–6 weeks of daily `tip_time` rows | **zero — `runner_odds_snapshots` does not exist** | merging #2 (creates the table) + deploy |
| Shadow flag flips (#3, #11) | ≥5 clean race days of shadow JSONs | **zero** (writers unmerged) | merge + deploy |
| Ledger settlement | daily settled rows | **zero** (`selection_ledger` exists, 0 rows) | merge + deploy + flag |
| Tier promotion | 200+ settled bets per tier | FLAG 17, CONFIRMED 40, LOCK/CROWD_OVERRIDE 0 — frozen since 2026-04-18 | pipeline restart; multi-month at historical rates |
| PF Phase C done-criterion | 2 consecutive green scheduled ingestion days | **zero** — no scheduled ingestion has ever run green | merging #28 + enabling the schedule |

Results history is the one healthy input: 144,511 rows continuous through
2026-07-31, with the Punting Form Phase B backfill (10,998 `pf%` rows,
2026-06-30 → 2026-07-31, raw payloads archived) seamlessly covering the
old provider's 2026-07-13 death. Nothing has written since — selections
have been dead since 2026-04-19, so **no amount of running the current
pipeline accrues retrain data; the clock starts at deploy and only
there.** If the merge sessions and deploy land in the first week of
August, the earliest defensible retrain is early-to-mid September. The PF
wall (~31 days, sliding) keeps each un-ingested day recoverable for about
a month — daily ingestion must restart before ~2026-08-28 to keep
August's early dates safely inside the window.

### Prerequisites the audit added to the retrain (all mechanical, none
calendar-gated)

1. **Track-alias dedup applied first** (PR #32's `--apply`, its own write
   approval): 2,531 duplicated result rows would otherwise double races
   inside the training window.
2. **F-FORM-ASOF resolved CLEAN** (or its remediation landed): the PF
   importers stamp `form_string` from `meeting_detail.last10` at import
   time. If Punting Form regenerates `last10` as-of query time, every
   PF-sourced training row embeds its own result in its own pace
   features, and the 10,998 backfilled rows are tainted for training.
   Verification is dispatched (read-only probe); until its verdict, the
   retrain is blocked regardless of the calendar.
3. **Pre-jump snapshot integrity merged** (PR #35, stacked on #2): the
   snapshot table is append-only and capture fires on every `run_tips`
   invocation, so a routine rerun of a past date wrote post-jump prices
   as `tip_time`; the view now admits only provably pre-jump rows and
   capture refuses post-jump writes. Without it, one rerun inside the
   accrual window silently contaminates `tip_time_odds`.
4. **Preflight gates the as-of profiles** (dispatched): `retrain_v2`
   silently falls back to the leaking legacy `td_*` snapshot when
   `track_distance_profiles_asof.json` is missing; `retrain_preflight.py`
   gains a red gate so that fallback can never reach a promotion.

### Standing rules the audit added (evaluation hygiene)

- `backtest.py walk_forward_backtest` has a ZERO purge gap (vs 14d in the
  production trainer) — its numbers are never quotable as evidence.
- Franking/Elo are as-of-now by design: never replay the mc_api scoring
  path over historical dates; retrospective mc_api numbers are inflated.
- Betfair snapshot capture stays on `baseline`/`morning` kinds until a
  pre-jump market-status guard exists; the PF Phase E accessors
  (strike rates, ratings, speedmaps) have no as-of parameter and are
  serve-time only — never inputs to historical feature building.

**The retrain gate on one line:** #32 applied → F-FORM-ASOF CLEAN (or
remediated) → #13, #14, #35 and the preflight as-of gate merged → deploy
→ 4–6 weeks of green snapshot days → `retrain_preflight.py` green on
both boards → the pre-registered §4 criteria decide, not judgment.

---

## 5. No data leakage — the inventory and the invariants

"Successful" for this program means the retrained model's measured edge
survives contact with reality. Every known way train-time information has
leaked past tip time is enumerated here with its fix and merge vehicle.

### Leak inventory

| # | Leak | Mechanism | Fix | Vehicle |
|---|---|---|---|---|
| L1 | SP-contaminated `market_odds` (21.8% importance — the model's top feature) | Training maps `sp_odds → market_odds`; starting price is unknowable at tip time, so every backtest was optimistic. | Capture as-of tip-time odds; train in `snapshot` mode where `_effective_odds = tip_time_odds`, full stop — poisoned-SP test proves SP cannot reach the feature; ablation arm C controls for row selection. | #2, #16, then the retrain |
| L2 | `td_*` trainer/track aggregates | Aggregates computed over data that includes races after the subject race. | Monthly as-of profile buckets — each race sees only profiles built from strictly earlier months. | #14 |
| L3 | `days_since_run` wall-clock anchor | Anchored to the wall clock at training time, not the subject race date — the same horse gets different values depending on when the retrain ran. | Anchor to the subject race date. | #13 |
| L4 | 25.45% importance zero-at-serve (15 features) | Not future-information leak but train/serve skew: features assigned at train, zero-filled at serve — a quarter of the learned signal fires on fabricated constants. All 15 derivations are traced leak-safe (pre-race inputs only). | Serve-side plumbing, flag-gated; NaN contract so "no history" reaches the trees as NaN, never 0 (0 is a real z-score). | #11 (after #5) |
| L5 | Evaluation-time leakage (the human kind) | Metric/band/threshold chosen after outcomes are visible — the founding +12.3% ROI was best-of-6 on one window, CI [−43.6%, +68.2%]. | Pre-registration of metrics, exactly two bands, new-beats-old rule, append-only exclusion log; ship gate rejects any promotion whose ROI CI sits entirely below zero; preflight enforces all of it mechanically. | #18, #7, #17 |

### Standing invariants (hold at every step, checked, not assumed)

1. **As-of joins everywhere:** any feature computed from history uses
   strictly-before-the-race data (`race_date <` predicates, LATERAL as-of
   views, monthly buckets). Audited per-feature in the provenance sweep
   (#12).
2. **Shadow publishes legacy:** while evidence accrues, live output is
   byte-identical to today — shadow paths only write JSON evidence. No
   silent behavior change can contaminate the window it is being judged in.
3. **Snapshot rows only, loudly:** `snapshot` training mode hard-fails
   (exit 2) on a missing `odds_source` column or zero rows — no silent
   fallback to SP.
4. **The window's own data quality is protected operationally:** the
   consensus-before-tips catch-up gate (stride-app #4) prevents NO_BET junk
   days; the interpreter fix (stride-app #3) prevents silently-dead python
   tasks; both merged before the window opens.
5. **Never promote directly:** the only path to a live model is
   `retrain_preflight.py` green on both boards.
6. **Verification triangle unchanged:** coder handovers are audited on real
   pushed bytes before merge; architect work ships with mechanical
   self-verification; the operator is the sole merge/deploy/prod authority.
   No prod writes by agents, ever; prod reads per-task approved.

---

## 6. Execution order (the whole plan on one page)

1. **Sage:** resolve #18's five `[SAGE-APPROVAL:]` markers; approve #15's
   pruning list; flip #5 from draft to ready for review.
2. **Sage: one merge session** — stride-app #1, #2, #3, #4 (in order);
   stride-racing #1, #2 → #16, #6, #9, #10 → #12, #17, #13, #14,
   #4 → #7 / #8, #3, #5 → #11, #19, #20; then #15 and #18 once step 1 is
   done. Every flag stays at its shipped default (live flags OFF, shadow
   writers ON) — merging changes no runtime behavior.
3. **Sage:** dispatch **12P-8** to Kimi (its #5+#11 gate cleared in step 2);
   architect audits the handover as usual. In parallel, dispatch CB-1 and
   continue the chat pack.
4. **Sage: deploy session** per the OP-1 five-step restart checklist
   (python deps into `.venv`, launchd KeepAlive, prevent sleep, start app,
   confirm catch-up plan output). **This opens the accrual window — steps
   1–2 must be complete first so the window starts with all evidence
   writers in place.**
5. **Architect:** OP-3 verification battery same day; OP-4 check-ins each
   race day thereafter.
6. **Calendar does its work.** Flag flips per §4 as gates clear; ablation
   run when its window closes; task-12 retrain at 4–6 weeks behind a green
   preflight.

### What makes this successful, concretely

- **Clocks:** ~87 selections/day with `tip_time` populated; snapshot rows
  per race morning; ledger rows settling net of commission — visible in the
  OP-3 report and the daily one-glance liveness check.
- **Evidence:** shadow JSONs appear every race day and OP-4 counts them
  against the gates; anomalies pause the clock the day they appear.
- **Integrity:** every §5 invariant checkable by a command, and the retrain
  decision reduces to a pre-registered rule evaluated by a script — not a
  judgment call made after seeing the numbers.
- **Honesty about the outcome:** if the leak-free retrain shows the edge was
  smaller than the contaminated backtests claimed, that is the system
  *working* — the objective is a real edge, not a flattering one.
