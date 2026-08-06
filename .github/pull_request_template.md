<!-- Delete any line that does not apply. Leaving an unticked box that DOES
     apply is the useful signal; ticking one you did not do is the only way
     this template makes things worse. -->

## What changes, and what breaks if it is wrong

## Evidence

- [ ] `python -m pytest server/python -q` — count:
- [ ] Any new assertion was checked by reverting the defect it names and
      watching it fail. A test that passes against the broken code is not
      evidence, it is decoration.
- [ ] Anything whose correctness lives in SQL was run against the real schema.
      A unit test with a faked cursor says nothing about a query.

## Preconditions this change assumes

Merging deploys. If this needs something to exist FIRST — an uploaded
artifact, an applied migration, a rebuilt view — say so here and confirm it
has happened, or the first person to notice is the 04:00 alarm.

- [ ] No precondition, or: _______________________ (done at: ____)

Known ones, delete if untouched:
- Panel staging (`_stage_panel`, `PanelUnavailable`) → `infra/09c_upload_panel.sh`
  must have run. Enforced by the `panel-staged-check` workflow.
- `stride_norm_track` DDL → needs a migration applied; the code fix alone
  never reaches production.
- A schedule re-time → `JOB_TIMEOUTS` gaps move with it, and the old schedule
  name must be retired or the job fires twice.
