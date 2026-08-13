# 03 — Phase 0: Data Contract and Leakage Audit

## Purpose

Establish, in code, exactly what information existed at decision time for
every historical race, and prove the training/evaluation data respects it.
Nothing downstream is trustworthy until this phase passes.

Offline only. No live-pipeline changes.

---

# Deliverables

1. **Decision-time data contract** — a module (extend
   `training_view_contract.py` or a sibling `decision_time_contract.py`)
   that declares, per feature column of `training_view_v2`:
   - its `as_of` semantics (pre-race stable / decision-time snapshot /
     post-race, i.e. forbidden)
   - its source table and join key
2. **As-of market join** — a documented, tested function that selects, for a
   given race and decision timestamp, the latest `betfair_odds_snapshots`
   row at or before that timestamp (kind `tip_time`, falling back only as
   documented — never silently to SP).
3. **Leakage audit job** — a batch script (`jobs`-style, runnable locally
   and on Fargate) that scans the training view and reports violations.
4. **Dataset validation report** — committed under `docs/decision-learning/`
   with row counts, date ranges, null rates, and the excluded-row register.

---

# Required Checks

- `sp_odds` is never populated in any column declared decision-time.
- `odds_source` distribution is reported; rows whose only price is SP are
  **flagged and excluded** from decision-learning datasets (they remain
  valid for the prediction layer).
- Phantom-price rows (issue #123 cluster, sub-$1.20 unformed-book quotes)
  are excluded by the same predicate production uses — reuse the
  unformed-book guard, do not write a second filter.
- Snapshot timestamps map to a real decision convention
  (`tip_time`; see 02 §Contracts). Any race where the snapshot postdates
  the race start is a violation, not a warning.
- `seconds_to_jump` is consistent with the snapshot timestamp.
- Identity joins use `identity_normalization` keys only; report join-miss
  rates per source.

---

# Acceptance Criteria

- [ ] Contract module merged with the full column classification; unknown
      columns fail CI (schema drift becomes loud).
- [ ] `test_snapshot_after_decision_is_rejected`: PASS
- [ ] `test_latest_snapshot_before_decision_selected`: PASS
- [ ] `test_sp_never_in_decision_features`: PASS
- [ ] Audit run over the full snapshot era (2026-08-02 → run date):
      `timestamp_violations: 0` after documented exclusions.
- [ ] Validation report committed, including the effective usable date
      range and the count of races lost to each exclusion reason.

# Stop Conditions (per 16_…PROTOCOL)

Stop and record BLOCKED if: decision timestamps cannot be reconstructed for
a material share of the snapshot era; or the audit finds leakage in
`training_view_v2` that requires upstream fixes (file the issue, fix in its
own PR, re-run the audit).

# Known Constraints Entering This Phase

- Usable as-of data starts 2026-08-02; the report must state the resulting
  dataset size honestly. Small is acceptable; padded is not.
- 60-day snapshot retention: raw snapshots age out. If the audit needs
  history preserved beyond retention, the decision-learning dataset build
  (not this audit) is where rows are persisted — record this dependency.
