# AGENTS.md — Instructions for the AI coding agent (Kimi Code)

You are implementing a sequenced ROI/strike-rate improvement program for the STRIDE
horse-racing prediction pipeline. This file is your global contract. Read it fully
before touching any task file.

## How this pack works

- `README.md` — index, wave order, dependency graph, progress tracker.
- `00-evidence-base.md` — the audit that justifies every task. Consult it when a
  task file's "why" is unclear. Do not treat it as a task list.
- `NN-*.md` — task files, numbered in recommended execution order, grouped in 4 waves.
  Each contains: Goal → Why (evidence with file:line refs) → Scope → Steps →
  Acceptance criteria → Rollout → Guardrails → Related files.

## Execution rules (non-negotiable)

1. **Wave order.** Do not start a task whose `Depends on:` links are unfinished.
   Within a wave, tasks may be done in parallel branches.
2. **One task = one branch = one PR.** Branch names: `roi/NN-short-slug`.
   Never bundle two task files into one change.
3. **Every behavioural change ships behind an env flag, default OFF**, matching the
   existing convention (`STRIDE_LEDGER_WRITE`, `STRIDE_INTERACTION_PARITY`, …).
   The flag name is specified in each task file.
4. **Measure before you promote.** No flag flips to default-on, and no threshold
   changes, without the acceptance evidence specified in the task file
   (shadow P&L, walk-forward comparison, or CLV window — as stated).
5. **Never backfill SP into training features.** Any historical odds you cannot
   prove were knowable at prediction time must not enter `market_odds` or any
   training column. When in doubt: capture prospectively, wait, then use.
6. **Single settlement contract.** Price-taken is the settlement price; SP is stored
   for CLV only. Do not create a third settlement path. See `01`.
7. **No threshold tuning on the evaluating window.** Bands/edges may only be selected
   on window A and validated once on a disjoint later window B. See `09`.
8. **Do not delete losing strategies from reports.** All variants, CIs, and sample
   sizes are always shown. A CI spanning zero is reported as `NOT_REPORTABLE`,
   never rounded into a win.
9. **The crowd gate can veto or downgrade, never promote** a NO_BET to BET. See `07`.
10. **Respect the repo's standing prohibitions** (`docs/analysis/IMPLEMENTATION_PLAN.md` §5):
    no Kelly activation, no CL blend flip, no NN replacement, no wiring
    `market_efficiency.py`, no loosening of entry filters — unless a task file in
    this pack explicitly supersedes one (only `06` and `13` do, conditionally).

## Engineering hygiene

- Preserve the existing temporal-safety patterns: `DateWindowSplitter` purge gaps,
  as-of joins (`assert_as_of`), race-grouped folds. Any new fold/split code must
  meet the same standard, and task files call this out where relevant.
- Preserve NaN semantics per the contract established in `03` — never blanket
  `fillna(0)` on the feature frame.
- Line references in task files point at the commit of the audit; if code has moved,
  search for the named symbol/function instead of trusting line numbers.
- The published repo excludes the DB, models, and data. Commands in acceptance
  criteria assume your real environment (`.env`, database, model artifacts).
- Update `README.md`'s tracker table (status + date + evidence link) in every PR.

## Definition of done (every task)

- [ ] Code + flag implemented per Steps
- [ ] Tests/verification commands in Acceptance criteria pass
- [ ] Evidence artifact produced (backtest JSON, shadow P&L extract, migration, or
      audit query result) and linked from the PR
- [ ] No standing prohibition violated; rollback path = flag off
- [ ] README tracker updated
