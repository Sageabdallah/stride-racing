# 14 — Testing and Observability

Cross-cutting requirements for every phase. This repo's failure history is
specific: **silent no-ops and green ticks standing in for looking**. Every
rule here exists to make that class impossible for the decision layer.

---

# Test Taxonomy (per phase)

1. **Unit** — pure logic (reward math, key normalisation, config
   validation). Live in `server/python/tests/`, run by CI
   (`pytest server/python`).
2. **Property** — invariants that must hold on arbitrary inputs (stakes
   under caps, probabilities in [0,1] and race-summed, PASS-in-PASS-out).
3. **Contract** — schema pins that fail CI on drift
   (`training_view_contract` is the template; Phase 0's decision-time
   contract joins it).
4. **Deterministic replay** — same commit + same data build ⇒ identical
   artifacts, hash-compared. No wall-clock, no unseeded randomness
   (the MC seed derivation from race identity, PR #121, is the precedent).
5. **Regression** — existing prediction-pipeline tests must pass untouched
   in every decision-layer PR. Never delete a failing existing test to go
   green (16_…PROTOCOL §Testing).

# CI Rules

- Every phase PR carries its tests in the same PR.
- CI runs the full `server/python` suite — decision-layer tests are not a
  separate optional job.
- A phase's acceptance-criteria script should be runnable as one command
  and cited (with output) in the completion report.

---

# Observability Rules

## Content over exit codes

A job that exits 0 proves nothing. Every scheduled decision-layer job
declares a **content postcondition** (rows written for the day, artifact
field present) checked in-process where possible — the `morning_odds`
shape assertion is the pattern.

## The three failure classes (each needs an owner)

| Class | Detection |
|---|---|
| crashed | ECS failure watch (exists — log tail auto-filed as an issue) |
| ran-but-empty | content postcondition + outcome watchers (rows=N pattern) |
| never-ran | missing-run watch — every new scheduled job **must register** its expected trace when it is created, in the same PR |

## Artifact-field convention

Per-race audit data rides inside the day JSON (as `book_coherence` and
`prediction_stages` already do) — greppable evidence colocated with the
output it explains. Shadow decisions follow the same convention.

## Logging cautions (learned the hard way)

- Fargate/ECS container logs flush **once at exit** — ask ECS, not
  CloudWatch, whether a job is running; never conclude "no logs = not
  running".
- CloudWatch metrics: low-cardinality dimensions only (day-level counts,
  P/L). Never per-race or per-horse dimensions.
- stderr diagnosis lines must print on the scheduled (non-verbose) path
  for every failure except documented-benign cases — the QLD sectional
  collector's all-404 rule (#125/#128) is the template.

# The Standing Question

Every phase report answers: *"if this component silently did nothing
tomorrow, what would notice, and how fast?"* If the answer is "nobody",
the phase is not done.
