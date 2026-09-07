# Retrain gate: registered window (WP-8)

Registered: 2026-08-02T03:02:08Z (UTC). Repo head at the moment of registration:
3324c25. The commit that adds this file is the registration event;
its hash and committer timestamp prove the ordering. No outcome or
backtest data was examined before this file was committed (work-order
ground rule 6), and none may be used to adjust these dates afterwards.

## The one thing the retrain fixes

Training on odds the model could actually see at tip time. That requires
four to six weeks of tip_time snapshot rows. Row #1 was written
2026-08-02. Nothing substitutes for elapsed time.

## Registered window dates

| Milestone | Date |
|---|---|
| Day zero (first tip_time row) | 2026-08-02 |
| Earliest retrain window opens (4 weeks) | 2026-08-30 |
| Recommended retrain window opens (6 weeks) | 2026-09-13 |
| Validation window B (registry VR-001) | 2026-08-02 to 2026-09-13 |

These dates are fixed at registration. They are not shortened, widened,
or re-banded after data exists.

## The five gates (all must pass; gate_status.py prints them live)

1. Four to six weeks of tip_time snapshot rows (day count since day zero).
2. G2 then G1 prod applies confirmed. Applied and verified 2026-08-02
   (WP-5): zero alias-doubled groups, zero country-suffix forks; the
   verification queries are re-run live by gate_status.py.
3. Two shadow-flag flips (STRIDE_RENORMALISE_FIELD,
   STRIDE_SERVE_LIVE_FEATURES), each with at least 5 clean race days of
   shadow evidence and the pre-registered flip criteria met.
4. Calibrator coverage: 500 audit rows with final_win_prob since day zero.
5. retrain_preflight.py fully green.

## Standing prohibitions

No training job runs before the window opens and every gate passes. The
promotion path is retrain_preflight.py plus a staged artifact
(racing_ensemble_v3.pkl beside v2, one week parallel scoring); the gate
never promotes itself.

## Clarification 2026-09-05 (dates unchanged; gate mechanics only)

Two of the five gates did not measure what they claimed, found while auditing
the retrain plan against the code:

- **Gate 3** was `ok = all(flipped)` in `gate_status.py`: the shadow-day
  counts were printed but never enforced, so two environment variables set
  on day one would have passed with zero evidence. It now requires, for each
  stream, at least 5 evidence days in the durable store **and** a PASS
  review record from `shadow_flip_review.py --emit-evidence` (the registered
  criteria of `shadow-flip-criteria.md`, computed) **and** the flag on. The
  human flip remains the approval act; the record is what makes it a flip on
  the registered criteria.
- **Gate 5** ran `retrain_preflight.py` with no candidate, which its
  required `--staging` argument rejects (exit 2) every day, and then looked
  for a `VERDICT` line the script never prints. It could not pass. It now
  runs `retrain_preflight.py --inputs-only`: the staging-independent gates
  (serve liveness of the declared columns, source lockstep, parity suites,
  as-of td profiles, pre-registration). Candidate preflight (`--staging`)
  runs on the artifact once it exists — a gate on whether training may start
  cannot depend on the artifact training would produce.

The registered dates above are untouched.

## Clarification 2026-09-06 (dates unchanged; gate mechanics only)

Auditing the 2026-09-05 repair against the code found that both repaired
gates could still pass, or be bypassed, on something other than the thing
they name:

- **Gate 3** read whichever review record sorted last and trusted its
  verdict alone: not which flag it was about, not how many clean days it
  was computed on (the store's filename count stood in, and that count
  includes empty and unreadable files), and not whether evidence had
  arrived since. A PASS emitted once stayed authoritative forever. It now
  requires, per stream, a record about that flag, computed by
  `shadow_flip_review.py` on at least 5 **clean** days, and no older than
  the newest evidence file for its stream — a day written after the review
  may be the dirty day that restarts the count, so it invalidates the PASS
  until the review is re-run. The reviewer, for its part, now exits
  non-zero when `--emit-evidence` wrote no durable record, and reads the
  per-race transition detail the day files carry, so the single-race
  sign-off rule is computed on the evidence rather than passed over it.
- **Gate 5** is enforced twice: by `gate_status.py` for the daily readout,
  and by the `retrain-model` workflow before a `v3-candidate` run. The
  workflow's refusal captured the exit status of `tee`, not of the
  preflight (GitHub's default shell has no `pipefail`), so it could never
  fire; and the preflight's own exit code is 1 on RED only, so an unsigned
  `[SAGE-APPROVAL]` marker (AMBER) would not have refused either. The
  workflow now hands its JSON board to `gate_status.py --preflight-board`,
  the same every-row-GREEN rule the gate applies, and a test executes the
  step under the shell GitHub uses. The Fargate image also lacked
  `pytest`, which the parity gate runs in-process, so the scheduled
  preflight could only report an unreadable board; `requirements.txt`
  lists it now and a missing runner is an explicit AMBER row.

The registered dates above are untouched.
