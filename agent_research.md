# ORCHESTRATOR PROMPT — Research-Driven ROI & Hit-Rate Improvement

You are the lead orchestrator. You will coordinate multiple sub-agents across
four phases. Do NOT skip phases — each phase feeds the next. Use parallel
sub-agents wherever work is independent. Write all deliverables to the
`/docs/analysis/` folder (create it if missing).

---

## PHASE 1 — CODEBASE RECON (run agents in parallel)

Spawn these agents. Their job is ONLY to understand, never to modify code.

**Agent 1 — Architecture Mapper**
- Map the full directory structure and identify the tech stack, entry points,
  core modules, data flow, and external integrations (APIs, databases, feeds).
- Identify the "selection pipeline": how raw data → analysis → a selection
  is produced, and where staking/sizing/execution happens.

**Agent 2 — Documentation Reader**
- Find and read EVERY markdown file in the repo (README, CLAUDE.md, /docs,
  changelogs, design notes, TODO files, comments-as-docs).
- Summarize: stated project goals, design decisions, known limitations,
  naming conventions, architectural patterns in use, and any explicit
  "do not change" warnings left by the author.
- Treat these .md files as the source of truth for INTENT — flag anywhere the
  code appears to have drifted from the docs.

**Agent 3 — Selection Logic Tracer**
- Trace exactly how a selection is scored/ranked/chosen today: models,
  heuristics, thresholds, filters, odds/probability conversion, edge
  calculation, staking rules.
- Identify where ROI and hit rate are (or should be) measured: backtests,
  tracking logs, results tables.

**Phase 1 deliverable:** `/docs/analysis/SYSTEM_MAP.md` containing:
- One-paragraph summary of what this system does and its domain
- The selection pipeline as a step-by-step diagram (text)
- Current conventions: file layout, naming, patterns, config approach
- Explicit constraints: anything the docs say must not be broken
- Glossary of domain terms used in the codebase

⚠️ GATE: Do not proceed until SYSTEM_MAP.md is complete. All later agents
MUST read SYSTEM_MAP.md before starting.

---

## PHASE 2 — ACADEMIC RESEARCH (run agents in parallel, after Phase 1)

Spawn research agents, each given SYSTEM_MAP.md and one topic below (adapt
topic wording to the domain discovered in Phase 1 — e.g. sports betting,
trading, prediction markets, or whatever the system actually selects):

**Agent R1 — Prediction Methodology:** academic literature on forecasting/
modelling approaches proven to beat baseline methods in this domain
(Elo/Glicko variants, gradient boosting, Bayesian hierarchical models,
Poisson/Dixon-Coles-type models, ensemble methods — whichever apply).

**Agent R2 — Probability Calibration:** research on turning model outputs
into accurate probabilities (Platt scaling, isotonic regression, Brier score
optimization, calibration testing) — hit rate improvements usually live here.

**Agent R3 — Edge & Market Efficiency:** literature on identifying mispricing,
closing-line value, odds-movement signals, sample-size significance of edge,
and why most apparent edges are noise.

**Agent R4 — Staking & Bankroll:** Kelly criterion and fractional Kelly,
drawdown control, variance reduction — the ROI side of the equation.

**Agent R5 — Feature Engineering & Data Quality:** what academic/professional
literature says about the highest-signal features in this domain, plus data
leakage, survivorship bias, and backtest-overfitting pitfalls.

**Rules for research agents:**
- Prioritize peer-reviewed papers, published working papers, and well-cited
  practitioner research. Note citation count/recency where possible.
- Every claim must have a source (title, authors, year).
- For each finding, state explicitly: "This matters because our system
  currently does X (per SYSTEM_MAP.md), and the research suggests Y."
- Reject findings that require data or infrastructure the system doesn't have
  unless you flag them as "aspirational."

**Phase 2 deliverable:** `/docs/analysis/ACADEMIC_FINDINGS.md`
One section per research agent, each finding formatted as:
[Finding] → [Evidence/source] → [What our system does today] → [The gap]

---

## PHASE 3 — ROI & HIT-RATE IMPROVEMENT REPORT

Spawn one synthesis agent. Give it SYSTEM_MAP.md and ACADEMIC_FINDINGS.md.

Produce `/docs/analysis/IMPROVEMENT_REPORT.md`:
1. **Ranked list of improvements**, scored on: expected impact on ROI,
   expected impact on hit rate, implementation effort, and risk of
   destabilizing the existing system. Separate "improves ROI" from
   "improves hit rate" — they are NOT the same lever, and the report must
   say which lever each item pulls.
2. **Quick wins** (implementable in days, low risk) vs **structural changes**
   (weeks, higher risk) vs **aspirational** (needs new data/infra).
3. For each item: the academic backing, the exact file(s)/module(s) in our
   codebase it would touch (per SYSTEM_MAP.md), and how to measure whether
   it worked (metric + backtest/A-B approach).

---

## PHASE 4 — TRANSLATE RESEARCH INTO BUILDABLE SPEC

Spawn one agent to produce `/docs/analysis/IMPLEMENTATION_PLAN.md`.

Convert the Phase 3 report into concrete, codable work items. Each item must
be a self-contained ticket with:
- **What to build** — precise enough to code from without re-reading the papers
- **Where it lives** — exact file paths/modules, following the existing
  structure from SYSTEM_MAP.md (new modules go where similar modules already
  live; extend existing files rather than creating parallel ones)
- **Interface contract** — function/class signatures that match existing
  naming conventions and types already used in the codebase
- **Pseudocode or algorithm sketch** — the math from the research translated
  into steps
- **Acceptance criteria** — including backtest or unit-test expectations
- **Rollback plan** — how to disable it

## ANTI-CONFLICT GUARDRAILS (apply to every ticket, non-negotiable)

1. **Additive, not destructive.** No ticket may remove or rewrite existing
   working logic. New capabilities slot in alongside current ones.
2. **Feature-flag everything.** Each change sits behind a config toggle
   (using the existing config system) so it can run A/B against the current
   behavior. Default = off until validated.
3. **One source of truth.** If the system already has a probability
   conversion, odds handler, staking module, or config loader — the ticket
   MUST extend it, never duplicate it. Call out the existing module by name.
4. **Schema safety.** No changes to existing database schemas, log formats,
   or API contracts. New data gets new tables/fields, additive only, with
   migration notes if needed.
5. **Convention lock.** Naming, folder placement, error handling, logging,
   and testing patterns must match what's documented in SYSTEM_MAP.md —
   even if the research suggests a "better" style. Consistency &gt; elegance.
6. **No pipeline reordering without evidence.** The existing selection
   pipeline's step order stays intact unless a ticket explicitly justifies
   the change with expected-impact numbers and a rollback path.
7. **Conflict check.** Every ticket must end with a "Conflicts checked"
   section listing any existing module, flag, or behavior it could interfere
   with — or explicitly stating "none."

---

## FINAL STEP

Summarize in chat: the top 3 improvements, which lever each pulls (ROI vs
hit rate), and the recommended build order. Do NOT write production code in
this run — the output is the four documents in /docs/analysis/ plus the
summary. Wait for my approval before implementing any ticket.
