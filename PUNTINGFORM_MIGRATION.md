# PUNTINGFORM_MIGRATION — action plan

**Why this exists:** The Racing API ceased Australian coverage, killing the
racecards, results, historical and odds-movement inflows (six modules). The
replacement stack is **Punting Form Starter** (bought 2026-07-31, key stored as
the `PUNTINGFORM_API_KEY` repository secret) for who/what/when/where + form +
sectionals, and **Betfair** (verified 2026-07-31, see
`scripts/BETFAIR_KEYS_STATUS.md`) for prices.

**The clock:** the Starter API serves roughly the **last 60 days** only. The
gap since The Racing API died must be backfilled before it slides out of that
window — Phase B is time-critical. Every raw payload we pull gets archived, so
a permanent proprietary history accrues from day one.

**Licence note:** Punting Form API data is licensed for personal use only. No
redistribution; revisit before any public/commercial tips output.

Owners: **[C]** = Claude (cloud sessions — no Mac needed), **[U]** = user.

---

## Phase A — connectivity & API mapping  *(DONE 2026-07-31, probe run #1)*

- [x] [U] Buy Starter; add `PUNTINGFORM_API_KEY` repository secret (2026-07-31)
- [x] [C] Probe tooling: `scripts/puntingform_probe.py` + `puntingform-probe`
      workflow — fetches their API reference and exercises candidate endpoints
      with the real key from a GitHub runner
- [x] [C] Probe read; findings recorded below. Meetings call for today
      returned live JSON in CI — **done-criterion met.**

### Confirmed API facts (probe run 30632395111)

- **Primary (JSON):** `https://api.puntingform.com.au/v2/form/meetingslist?meetingDate=YYYY-MM-DD&apiKey=...`
  → 200, `payLoad` array of meetings with track objects (name, trackId,
  location, state, country, surface). ISO dates only (d-M-yyyy → 400).
- **Legacy (caret-CSV):** `https://www.puntingform.com.au/api/formdataservice/ExportMeetings/{d-M-yyyy}?apiKey=...`
  → 200; fields `meetingId^track^railPosition^TABMeeting^meetingDate^
  isBarrierTrial^hasSectionals^trackAbbrev^resulted`; includes NZ + country
  meetings. Same service on `old.puntingform.com.au`.
- **Starter-tier endpoints** (from their published reference): Form (last 10
  runs/horse), Results (+ new distance/class fields), Meetings List,
  **Ratings**, **Speedmaps**, **Strike Rates** (trainer/jockey career +
  last-100 with actual-vs-expected), **Scratchings** (timestamped, with
  deductions), **Conditions** (track grading + weather), Worksheets, Notes,
  Blackbook. Ratings require token-based auth (second auth mode for the
  client); form/results/meetings work with the `apiKey` query param.
- **NOT in Starter:** the bespoke **Sectionals** and **Benchmarks** endpoints
  are Modeller/commercial tier; SouthCoast Export is Professional. See the
  plan adjustment in Phase E.

### Full Starter inventory — every included endpoint and what it is worth here

Source: Punting Form's own API reference as printed into probe run
30632395111 (descriptions condensed from their wording). All rows below are
**included in the $59 Starter tier**; JSON and CSV variants exist for most.

| Endpoint | What it returns | Value to STRIDE |
|---|---|---|
| Meetings List | All meetings for a date (track, state, country, surface, rail position, barrier-trial flag, resulted flag) | **Pipeline core** — replaces the dead racecards discovery; verified live 2026-07-31 |
| Form | Form for a race or whole meeting, up to the **last 10 runs per horse** | **Pipeline core** — the who/what/when/where + form-history replacement; feeds existing form features |
| Results | Results for a race or meeting; recently extended with **distance and class fields** (their changelog) | **Pipeline core** — replaces the dead results importers; feeds `race_results_history` and settlement |
| Scratchings | All upcoming scratchings **with timestamps and deductions** | **Serve-time correctness** — late scratchings currently reach the pipeline unreliably; deduction amounts also matter for price logic |
| Conditions | Track grading + weather for all upcoming meetings | **New feature input** — going/weather at serve time without scraping; the race filters (`STRIDE_RACE_FILTER_HEAVY_GOING`) get a proper source |
| Speedmaps | Punting Form's speed map data per race (default, or user-edited version) | **New feature input, ranked #1 of the new levers** — settling position / pace pressure for the barrier×pace interaction features |
| Ratings | Punting Form's standard ratings (token-based auth) | **New feature input + consensus pillar** — independent rated assessment per runner; also a value screen vs Betfair prices |
| Strike Rates | Trainer or jockey **career and last-100** performance incl. **actual-vs-expected** ratings | **New feature input** — upgrades the raw trainer/jockey win% the model uses today with an expectation-adjusted version |
| Worksheets | Punting Form's worksheet data per race (default or user-edited) | Secondary — consolidated race view; useful for spot-checking, not modelling |
| Notes | User-entered notes at form/horse/race level | Workspace feature — no model value unless notes are kept manually |
| Blackbook | Upcoming runners that are in the user's blackbook | Workspace feature — could later mirror model watchlists into the PF UI |

Practical notes for the client build (Phase B): the v2 JSON service takes
ISO dates and the `apiKey` query parameter; Ratings is documented as
token-based auth (second auth mode to implement); JSON `payLoad` envelope
carries `statusCode`/`error` fields to check on every call; the legacy
caret-delimited CSV service remains available as a fallback shape.

## Phase B — 60-day backfill  *(DONE 2026-08-01, pf-verify run 30642646209)*

- [x] [C] `server/python/pf_client.py`: thin client for the confirmed v2
      endpoints (envelope error-checking, retries, 0.4s pacing)
- [x] [C] `server/python/pf_backfill_results.py`: mirrors the retired
      importer's exact contract (21 columns, append-only, date/track/race
      dedup, trials + unplaced excluded) with the **horse-ID bridge**
      (match by normalised name; only unknown horses get `pf<runnerId>` ids;
      PF rows are identifiable by `race_id LIKE 'pf%'`)
- [x] [C] Raw archive: results payloads persisted to `pf_raw_payloads`
      (jsonb, created on first commit run) before parsing
- [x] [C] 3-day dry run (pf-backfill run #1): 12 meetings → 943 rows, **87%
      of runners bridged to existing horse_ids** (124 new), zero errors,
      and 0 existing DB rows in the window — confirming the pipeline had
      been dead since the Racing API cutoff
- [x] [C] Full `commit=true` run (pf-backfill run #2, 2026-07-31, 21 min):
      **10,503 rows inserted, 4,400 already present (dedup held), 578 new
      horse ids (~95% of inserted runners bridged to existing horses).**
      Raw results payloads for every resulted meeting stored in
      `pf_raw_payloads`.
- **Measured API wall:** meetingslist returns HTTP 400 for dates before
      **2026-06-08** — the "~60 days" window is ~53 days in practice, and it
      slides daily. 2026-06-01..07 are beyond the subscription's reach
      (recoverable only via the $1,100 archive, or already covered by the
      old pipeline — the gap report will say). **(Superseded by the
      verification below: the wall re-measured far tighter — ~31 days.)**
- [x] [C] Verification pass (pf-verify run 30642646209, 2026-08-01):
      `server/python/pf_verify_backfill.py` + the re-runnable read-only
      `pf-verify` workflow. Recorded numbers:
  - **Coverage:** 55 days checked (2026-06-08..08-01). DB ≥ PF expected on
        every resulted day inside the window — **zero missing meetings or
        races** vs the archived payloads. One flagged day: **2026-07-31**
        (6 resulted meetings, 48 races / 495 runners, 0 DB rows — daily
        ingestion isn't built yet; recoverable, owned by DM-K2/K4).
        2026-07-09 imported 0 rows because the old pipeline already held all
        348 — dedup matched exactly, a quiet parity signal.
  - **NULL rates** (10,503 `pf%` rows): **0.0% NULL** on distance_m,
        race_class, class_level, going, sp_odds, margin_lengths, race_name,
        opponents_json.
  - **Old-pipeline death date: 2026-07-13** (last non-`pf%` row). PF rows
        span 2026-06-30..07-30, overlapping 06-30..07-13 — coverage is
        continuous; the feared June/July gap does not exist.
  - **Lost dates: NONE.** The only zero-row dates are 2026-07-31 and
        2026-08-01, both at/after the runtime wall and recoverable by daily
        ingestion. Nothing requires the $1,100 archive.
  - **Bridge audit:** 9,925/10,503 rows bridged to existing horse_ids
        (94.5%), 578 new-id rows; 7,546 distinct horses bridged, 536 new
        `pf%` ids. 20-sample eyeball: PF name == that horse's prior
        old-pipeline name on all 20.
  - **Wall correction:** runtime binary search (8 calls) measured the
        meetingslist wall at **2026-07-01 on 2026-08-01**, and backfill run
        #2 fetched nothing before 2026-06-30 (2026-07-31 23:36 AEST) — the
        practical window is **~31 days, not ~53**; the 2026-06-08 probe
        figure did not hold. A missed day becomes unrecoverable after ~31
        days, which makes daily ingestion (Phase C) the critical path.
- **Done when:** the verification numbers and gap report are recorded here. **(DONE 2026-08-01.)**

## Phase C — daily ingestion (replace the six dead modules)

- [ ] [C] `download_racecards.py` → PF meetings/fields (keep output shape and
      `TARGET_TRACKS` filtering so downstream code is untouched)
- [ ] [C] `fetch_and_import_date.py` + `auto_results_collector.py` → PF
      results (existing racing.com fallback retained)
- [x] [C] `download_historical.py` + `backfill_barrier_trials.py` → PF within
      the window (2026-08-01, branch pf/historical-trials): bulk results go
      through `pf_results_mapper` into `race_results_history` (target-track
      filter kept); trials come ONLY from `isBarrierTrial` meetings and keep
      the exact `trials_<date>.json` contract `import_barrier_trials_to_db.py`
      loads into `barrier_trial_results` (19 columns, unchanged — verified by
      feeding PF output through the real importer in tests). The
      results/trials partition is asserted on every fetched date.
      **Pre-window limitation:** the Starter wall measured **~31 days** on
      2026-08-01 (meetingslist 400s before 2026-07-01 — tighter than the
      ~53 first assumed; pf-verify run 30642646209). Both scripts probe the
      wall at runtime before fetching, print each skipped date with the
      reason, and exit `3` (partial-due-to-wall) vs `1` (fetch failure) vs
      `0` — pre-window dates are unrecoverable without the $1,100 archive.
- [ ] [C] `odds_movement.py` → Betfair prices (delayed key now; live key when
      activated). Note: Betfair calls cannot run on GitHub-hosted runners —
      Mac/self-hosted runner only
- [ ] [C] Scheduled workflows: morning racecards pull, evening results pull,
      raw archive push; failure = red run (no silent passes)
- **Done when:** two consecutive days ingest hands-free with row counts
  matching the meeting calendar.

## Phase D — trust checks before the model touches anything

- [ ] [C] Parity: PF results vs existing DB on overlapping days (winners,
      margins, field sizes, track-name normalisation, horse-name matching)
- [ ] [C] Sectionals sanity: PF vs racing.com/RQ values where both exist
- [ ] [C] Coverage report: PF meeting coverage vs the meeting calendar by
      state (the 92%-of-TAB claim, verified on our own data)
- **Done when:** discrepancy rate is quantified and either negligible or
  explained in this file.

## Phase E — feature uplift (flags + walk-forward evidence only)

Ranked by expected value; each behind its own flag, promoted only on
walk-forward + promotion-gate evidence (`retrain_preflight.py`):

- **Plan adjustment (Phase A finding):** PF sectionals need the Modeller
  tier, not Starter — so the free racing.com/RQ sectional collectors STAY in
  service, and the Starter-tier levers below move up the ranking. PF
  sectionals become a paid upgrade decision only if the model's sectional
  features prove valuable enough to fund Modeller.
- [ ] [C] **Speedmaps** (Starter ✓) — settling-position/pace-pressure
      features into the existing pace interactions
- [ ] [C] **PF Ratings** (Starter ✓) — consensus-pillar input and value
      screen vs Betfair prices
- [ ] [C] **Strike Rates** (Starter ✓) — trainer/jockey actual-vs-expected as
      features (upgrade on raw win% the model uses today)
- [ ] [C] **Conditions** (Starter ✓) — track grading + weather as serve-time
      features; **Scratchings** into the serve pipeline for late changes
- [ ] [C] Retrain + walk-forward A/B: current features vs +PF features
- **Done when:** the A/B shows the PF feature set wins (or the losers are
  documented and dropped).

## Phase F — measurement loop (where ROI becomes real)

- [ ] [C] Shadow mode: ledger writes on, Betfair price captured at tip time,
      CLV settled vs close/BSP for 2–4 weeks
- [ ] [U] Betfair live key activation (Automation Hub form — submitted?)
- [ ] [U] Mac session: self-hosted runner (unlocks Betfair automation), local
      `.env` update, purge old password (see `ACTION_THIS.md`)
- **Decision gate:** positive CLV → tune staking/selection and consider live
  betting; flat/negative CLV → iterate Phase E, spend nothing further.

---

## Risks & standing rules

- **60-day window:** if backfill slips past it, missing dates are gone unless
  the $1,100 archive is ever bought. Phase B runs first.
- **Unknown rate limits:** client backs off politely; backfill runs in date
  batches; probe findings recorded here.
- **Train/serve consistency:** PF-derived features only exist go-forward;
  training windows and availability flags must respect that (walk-forward
  harness handles it — no silent feature backfill into history).
- **ROI vs strike rate:** selection filters that raise strike rate usually
  lower ROI (`selection_policy.py` header). CLV is the compass; strike rate is
  a constraint, not the target.
- **No flag flips without evidence:** every model-facing change passes the
  walk-forward + promotion gate discipline already in the repo.
