# 16 — Claude Code Execution Protocol

## Purpose

This file tells an AI coding agent exactly how to execute the STRIDE implementation plan.

The agent should not treat the numbered markdown files as general advice.

They are an ordered engineering specification.

---

# Agent Behaviour

For every phase:

1. Read:
   - `CLAUDE.md`
   - `01_GLOBAL_RULES.md`
   - the current phase file
   - any phase dependencies referenced by that file

2. Inspect the current repository before editing.

3. Identify:
   - existing modules that can be reused
   - existing tests
   - current AWS entry points
   - current model-loading path
   - current data schemas
   - current configuration system

4. Write a short internal implementation plan before modifying code.

5. Make the smallest coherent code changes that satisfy the phase.

6. Add or update automated tests.

7. Run relevant tests.

8. Run the phase acceptance checks.

9. Record:
   - files changed
   - migrations/config changes
   - tests run
   - acceptance criteria passed/failed
   - unresolved risks

10. Update `17_IMPLEMENTATION_STATUS.md`.

11. Do not begin the next phase while blocking acceptance criteria remain unresolved.

---

# Repository Discovery Rule

Before creating a new file, search for an existing equivalent.

Examples:

```text
existing probability calibration module?
existing S3 artifact loader?
existing backtest engine?
existing market snapshot selector?
existing risk configuration?
existing model manifest?
existing CloudWatch logger?
```

Prefer extending a clean existing implementation over duplicating it.

Do not force the example directory structure if the repository already has a sensible structure.

---

# Change-Scope Rule

Each phase should have a narrow purpose.

Do not mix unrelated refactors into RL/decision work.

Examples:

## Allowed

During Phase 0:
- add timestamp columns
- implement as-of join
- add leakage validation

## Not Allowed

During Phase 0:
- replace FastAPI framework
- change ensemble model architecture
- rewrite all database access
- add PPO

---

# Evidence-First Rule

Do not claim a phase is complete because code exists.

Completion requires evidence.

Examples:

```text
"as-of join implemented"
```

is insufficient.

Required:

```text
test_market_snapshot_after_decision_is_rejected: PASS
test_latest_snapshot_before_decision_selected: PASS
historical_audit_rows_checked: N
timestamp_violations: 0
```

Likewise:

```text
"policy beats Kelly"
```

must include the actual out-of-time comparison and stress tests.

---

# Stop-and-Report Conditions

Stop the phase and document the blocker if:

- required historical timestamps do not exist
- historical odds cannot be mapped to decision time
- existing feature tables contain unknown future leakage
- predictor training cutoffs cannot be reconstructed
- final SP is mixed with executable decision-time prices
- model artifacts are not version identifiable
- production feature generation differs materially from backtest feature generation
- a counterfactual reward cannot be calculated without unsupported assumptions
- a required migration risks corrupting production data
- an acceptance criterion fails repeatedly

Do not hide these problems with imputation or convenience assumptions.

---

# No Silent Assumptions

If the codebase does not contain a required value, do not silently invent it.

Examples:

- decision time
- commission
- slippage
- bankroll
- Kelly fraction
- maximum stake
- liquidity
- race-start source

Create a clearly named configuration placeholder and document that it requires a project decision.

Example:

```yaml
risk:
  kelly_fraction: null  # REQUIRED: set after validation
```

Prefer a failing configuration validation over an arbitrary production default.

---

# Testing Protocol

For each phase run:

1. targeted unit tests
2. relevant integration tests
3. regression tests for existing prediction functionality
4. deterministic replay test if the phase affects historical decisions

If the repository has CI, ensure new tests are compatible with CI.

Do not delete failing existing tests merely to make the branch green.

---

# Schema Migration Protocol

For new database fields/tables:

1. define schema
2. create reversible migration
3. make application code tolerant during rollout where needed
4. backfill separately from request path
5. validate row counts and null rates
6. only then make field mandatory

Do not perform giant historical backfills inside an API deployment.

---

# Artifact Protocol

Every trained artifact must have metadata.

Minimum:

```text
artifact version
training start
training cutoff
feature schema
code commit
data build ID
model type
decision time convention
```

Never rely on file name alone to establish lineage.

---

# AWS Change Protocol

Before adding a new AWS service, answer:

1. Can the current FastAPI service do this safely?
2. Can an existing job runner do this?
3. Can EventBridge/Lambda trigger the existing runner?
4. Is a new service necessary for runtime, duration, scale or isolation?

Only add:
- Batch
- Fargate
- SageMaker training
- Step Functions
- separate inference service

when there is a documented reason.

---

# Policy Promotion Protocol

No candidate policy may move directly from notebook/backtest to active use.

Required state transitions:

```text
DEVELOPMENT
→ OFFLINE_VALIDATED
→ SHADOW
→ SHADOW_VALIDATED
→ CONSTRAINED_ACTIVE
→ ACTIVE
```

Every transition must be represented by configuration and versioned artifacts.

---

# Code Review Questions

Before completing a phase, the agent should answer:

## Correctness
- Is there any possible future leakage?
- Is this using the exact decision-time information set?
- Can results be reproduced?

## Architecture
- Did prediction remain separate from decision optimisation?
- Did learned policy remain separate from hard risk controls?
- Did we reuse existing infrastructure where practical?

## Evaluation
- Is the comparison fair?
- Did we avoid test-set tuning?
- Are longshots disproportionately driving the result?

## AWS
- Can the new component be rolled back?
- Is the loaded artifact version traceable?
- Are failures observable?

## Complexity
- Did we introduce anything more complex than necessary?
- Is there a simpler implementation that achieves the same validated result?

---

# Phase Completion Report Template

At the end of each phase, create or print a report in this structure:

```markdown
# Phase N Completion Report

## Status
PASS / BLOCKED / PARTIAL

## Changes
- ...

## Tests
- test_name: PASS
- ...

## Acceptance Criteria
- [x] ...
- [ ] ...

## Data Validation
- rows:
- date range:
- leakage violations:
- missing values:
- excluded rows:

## Risks / Assumptions
- ...

## AWS Impact
- none / describe

## Rollback
- ...

## Next Phase Ready?
YES / NO

## Blocking Items
- ...
```

---

# Final Rule

The agent's goal is not:

> implement RL at all costs.

The agent's goal is:

> determine whether a learned decision policy provides reproducible, out-of-time, risk-controlled incremental value over STRIDE's simpler calibrated EV/Kelly baselines.

If the answer is no, the technically correct outcome is to keep the simpler system.
