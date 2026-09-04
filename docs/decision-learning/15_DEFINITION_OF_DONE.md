# 15 — Definition of Done

A phase is DONE when all of the following hold. "Code exists" is none of
them.

---

# Per-Phase Definition of Done

1. Every acceptance criterion in the phase file is checked off **with
   evidence** — test names + PASS, artifact hashes, report links. The
   Evidence-First examples in `16_…PROTOCOL.md` set the bar.
2. The completion report (16_…PROTOCOL template) is committed under
   `docs/decision-learning/reports/`.
3. `17_IMPLEMENTATION_STATUS.md` row updated to COMPLETE with the evidence
   link — in the same PR as the completing change, never retroactively.
4. All REQUIRED config placeholders introduced by the phase are either
   resolved by a recorded project decision or still `null` **and** listed
   in the report's Blocking Items (a silent default is a DoD failure).
5. CI green on the full suite; no existing test modified to pass except
   with documented cause.
6. The phase's PR(s) merged to `main` — one phase per PR; unrelated
   refactors rejected in review (16_…PROTOCOL §Change-Scope).
7. Anything the phase deploys or schedules has its watcher registration
   and rollback lever in place (14_…OBSERVABILITY, 02 §Deploy paths).

# Whole-Project Definition of Done

The project — as distinct from each phase — is DONE when **one** of these
end states is reached and recorded in the register:

## End state A: promoted

Phases 0–9 COMPLETE; the decision layer is ACTIVE under manifest control;
runbook exercised; Phase 10's question answered (either way) with evidence.

## End state B: baseline retained

Phases 0–7 COMPLETE; Phase 7's recommendation was REJECT (baselines win);
the risk engine (Phase 6) is wired into production guardrails where it
adds protection; the register records why the learned policy was not
promoted. **This is a success**, not an abandonment — the point was to
find out (16_…PROTOCOL §Final Rule).

## End state C: blocked-and-parked

A stop condition proved unresolvable (e.g. decision-time data cannot
support the required evidence). The register records the blocker, what
would unblock it, and the date the question should be re-asked. Parked
work leaves no half-wired code in the live pipeline: dormant modules
stay dormant or are removed.

---

# Anti-Definition (never counts as done)

- A register row flipped without an evidence link
  (the EXECUTION_STATUS.md failure mode — see repo history).
- A phase "demonstrated" only on in-sample data or the sealed holdout
  reopened to make a result look better.
- A flag left on after its measurement window with nobody reading the
  measurements.
- A deploy on a Friday night or Saturday.
