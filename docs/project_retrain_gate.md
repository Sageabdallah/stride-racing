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
