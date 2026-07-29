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

So #3 and #11 are blocked on **audit/completion**, not the calendar; the
calendar gates their **flag flips** (§4).

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
| #19 | 12P-7 betfair coverage audit | Audited incl. the two SQL fixes; the prod **run** needs the standing read approval, the merge doesn't. |
| #20 | PROGRAM_STATUS.md | Docs. |

---

## 3. Bucket B — BLOCKED, but NOT by the calendar

These must not merge yet, and waiting on the calendar would not help them.
Each has a named unblock action and owner.

| PR | Blocker | Unblock action | Owner |
|---|---|---|---|
| #3 (roi/05 calibrator + renormalisation) | Its own description: "not yet independently verified against the spec checklist." House rule: nothing merges unaudited. | Architect audit against `docs/roi-roadmap/05` — **can run today**; on PASS it merges with `STRIDE_RENORMALISE_FIELD` OFF, shadow comparison ON. | Architect |
| #4 (roi/02 backtest statistics) | Self-described WIP, "NOT complete or reviewed" (recovered from an interrupted run). | Kimi completion dispatch against `docs/roi-roadmap/02`, then architect audit. | Kimi → Architect |
| #7 (ship gate: reject below-zero-CI promotion) | Stacked on #4 ("merge after or with it"). The fix itself is audited and stricter-only. | Merges immediately after #4. | — |
| #8 (reportability floor from roi_stats) | Depends on #4's `roi_stats.py`. | Merges after #4. | — |
| #5 (roi/03 serve-time probability fixes) | Draft, self-described "NOT complete or reviewed." | Kimi completion dispatch against `docs/roi-roadmap/03`, then architect audit. This is the base 12P-8 rebases onto. | Kimi → Architect |
| #11 (serve the 15 dead features, flag-gated) | Fully audited and complete, but stacked on #5. | Merges immediately after #5, `STRIDE_SERVE_LIVE_FEATURES` OFF, `_SHADOW` ON. | — |
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
| 12P-8 winner-pattern rescue (Kimi) | #5 + #11 merged to main (code gate downstream of Bucket B — listed here because it cannot be scheduled independently). | Merge state. |

**The single most important discipline:** nothing in this table is
shortened, widened, or re-banded after data exists. That is what PR #18
fixes in writing, which is why its markers must be resolved *first*.

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
   pruning list.
2. **Sage:** merge Bucket A — stride-app #1, #2, #3, #4 (in order);
   stride-racing #1, #2 → #16, #6, #9, #10 → #12, #17, #13, #14, #19, #20;
   then #15 and #18 once step 1 is done.
3. **Architect:** audit #3 (roi/05) against the spec checklist → merge on
   PASS, flags OFF / shadow ON.
4. **Kimi:** complete #4 (roi/02) → architect audit → merge #4, #7, #8.
   Then complete #5 (roi/03) → architect audit → merge #5, then #11
   (flags OFF / shadow ON).
5. **Sage: deploy session** per the OP-1 five-step restart checklist
   (python deps into `.venv`, launchd KeepAlive, prevent sleep, start app,
   confirm catch-up plan output). **This opens the accrual window — steps
   1–4 should be complete first so the window starts with all evidence
   writers in place.**
6. **Architect:** OP-3 verification battery same day; OP-4 check-ins each
   race day thereafter.
7. **Calendar does its work.** Flag flips per §4 as gates clear; ablation
   run and 12P-8 dispatch when their gates clear; task-12 retrain at 4–6
   weeks behind a green preflight.

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
