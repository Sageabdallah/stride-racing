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
- [x] [C] Country-suffix fork repair (the known-bad 2026-07-31 writes —
      backfill + evening proof run; the verification-pass step-6 gap),
      reported 2026-08-01 by `server/python/pf_fork_repair.py` (branch
      `pf/fork-repair`, prod-read approved): 589 distinct `pf%` horse ids
      scanned against a corrected reference bridge over the 25,815 non-`pf%`
      normalised keys (the mapper's original-case projection restricted to
      `race_id NOT LIKE 'pf%'` — never the old `LOWER(horse_name)`
      projection). **0 spurious forks found** (0 anomalies, 589 genuinely
      new ids; by-suffix: (NZ) 0 / (GB) 0 / (IRE) 0 / other 0 / none 0),
      confirmed two independent ways (Python `norm_name` bridge and a
      SQL-side normalisation agree). The fork *conditions* exist — 649
      non-`pf%` rows store suffixed names like `Abrafo (NZ)` — but PF
      runner names carry no country suffix (0 suffixed names among the 589
      ids) and no new-id name resolves to any previously-known horse:
      no suffix-stored horse ran in the 2026-06-30..2026-07-31 PF-written
      window under a fresh id. Mechanical blast radius
      (`information_schema`, cross-checked with `git grep`): 6 tables
      carry horse_id — `race_results_history` (645 `pf%`-id rows; the only
      table holding any) plus `barrier_trial_results`,
      `blackbook_entries`, `franking_scores`, `horse_prep_profiles`,
      `tab_odds_raw` (all zero; those five have no race_id column).
      Current PF rows: 10,353 bridged vs 645 new-id of 10,998
      (94.14% bridged). Full remap list: `pf_fork_remap.json` (empty).
      `--apply` is built (single transaction, `pf_fork_repair_backup`
      pre-image before any UPDATE, updates restricted to `pf%`-race_id
      rows, idempotent, in-run re-detect verification) but was NOT run —
      it awaits the operator's separate prod WRITE approval and must run
      AFTER `pf_track_dedup --apply` (DM-G2 before DM-G1); with zero
      forks it is currently a no-op.
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
- [x] [C] `odds_movement.py` → Betfair prices (2026-08-02, WP-1): serve side
      now priced by `betfair_prices.py` (direct Exchange when credentials
      work, freshest `runner_odds_snapshots` rows otherwise). Also ported:
      `capture_late_odds.py` (schedule from race_schedule, market from
      Betfair) and racecard enrichment (`betfair_enrich_racecard.py`, called
      by `run_tips_pipeline`). Ledger rows carry `price_source`. Note:
      direct Betfair calls cannot run on GitHub-hosted runners; the AU
      runner or the Mac only
- [x] [C] Scheduled workflows (2026-08-01, branch pf/scheduled-ingestion):
      `pf-evening-results.yml` (10:30 UTC = 20:30 AEST intent; today AND
      yesterday — late/abandoned meetings resolve next day) and
      `pf-morning-racecards.yml` (19:30 UTC = 05:30 AEST intent). Both:
      secrets from the repo, concurrency group, 30-min timeout, no
      continue-on-error, per-meeting counts to the step summary. Proof runs:
      evening push run **30647305549** green (2026-07-31 imported 48
      races/495 runners — the DM-K1 flagged gap; 2026-08-01 cards fetched,
      0 resulted yet), dispatch **30647658519** green (dedup held: 48/48
      already existed, 0 inserted), sabotaged-input dispatch **30647949139**
      red on exit 1 as required (input-only sabotage, nothing reverted).
      The evening import also writes the raw archive (archive-first per
      meeting). The morning workflow is **gated on DM-2** (pf/racecards
      unmerged): its entrypoint there is stable (verified @ 0fdf879) and a
      gate step fails loudly until it merges — push run 30647304536 shows
      the gate red by design. **Operator:** the done-criterion below (two
      consecutive green scheduled days) is yours to check after merge.
- **Done when:** two consecutive days ingest hands-free with row counts
  matching the meeting calendar.

## Phase D — trust checks before the model touches anything

Measured 2026-08-01 by `server/python/pf_trust_checks.py` (read-only;
re-runnable; PF side read from the `pf_raw_payloads` archive, nothing
refetched). Overlap window: **14 days, 2026-06-30..2026-07-13** (old
pipeline's last day .. PF coverage start), 739 races compared across
meetings matched by runner-name overlap (track-name strings differ — see
alias finding below).

- [x] [C] Parity: PF results vs existing DB on overlapping days:
  - **Winner agreement 100%** (704/704 races with a position-1 on both
    sides; 35 unknown = abandoned/void races with no placings either side).
  - **Margins: 99.93% exact** (6,088/6,092 shared placed runners). The 4
    non-exact are explained: 3 are non-finisher sentinels (PF stores 99.9
    or 0.0 where the old pipeline stored the raw beaten distance, e.g.
    112.57L/206.7L) and 1 is a 0.01L rounding difference. Negligible
    (threshold: <0.5% non-exact).
  - **Winner-margin semantic (705 races, 100%):** the old pipeline stored
    NULL for the winner's margin; PF stores the winning margin. Not a
    discrepancy — consumers must know the convention changed.
  - Other null-vs-value margin pairings: 20/6,112 (0.3%) — scratching
    timing differences. Negligible.
  - **Field sizes:** 707/739 races exact. Of the 32 non-zero deltas, all
    32 are abandoned/void race shells (PF has the race with zero placings;
    the old pipeline kept NULL-position runner rows at the full field
    size, e.g. Moe 2026-07-03 abandoned after R4). Explained.
  - **Track-name normalisation collisions (literal `lower(track)`): 0.**
  - **NON-NEGLIGIBLE — alias duplicates (22 pairs):** the same meeting is
    stored twice under different track names (PF `Ballarat Synthetic` vs
    old `Sportsbet-Ballarat Synthetic`, `Belmont Park` vs `Belmont`,
    `Fannie Bay` vs `Darwin`, `Randwick` vs `Royal Randwick`, etc.) — the
    `(race_date, lower(track), race_number)` dedup key is blind to sponsor
    prefixes and aliases, so ~40 doubled race-days sit in
    `race_results_history` with different race_ids. Follow-up:
    **F-TRACK-ALIAS** — add a canonical track-name map to
    `pf_results_mapper` (normalise old sponsor-prefixed names to PF names
    before dedup) and one-time-delete the doubled `pf%` rows whose races
    already exist under the old alias.
  - **Horse-name bridge: 0 same-name misses** on duplicate-stored races
    (the bridge resolved PF runners to the existing `hrs_aus_*` ids even
    for doubled races); 2 position-level name disagreements out of 6,817
    shared positions (late-scratching reshuffles). Negligible.
- [x] [C] Sectionals sanity: 9 overlap days (2026-07-01..07-11,
      `nsw_pidata` source), 80 sectional races. **61 fully aligned; 19
      with runner-set differences; 0 sectional races missing from PF.**
      Differences: 17 races where PF names starters the sectional feed
      lacks (sectional data gap for those runners — an existing collector
      condition, not PF-caused), 1 race with a real sectionals-only runner
      (Muswellbrook 2026-07-07 R3 `russianwords` — PF has no record of the
      runner), and 1 race (Gosford 2026-07-09 R4) where the collector
      stored `Unknown <id>` placeholder names. Follow-up: **F-SEC-PLACEHOLDER** — fix `Unknown` placeholder
      name resolution in the NSW sectional collector.
- [x] [C] Coverage report: PF resulted meetings 2026-06-30..08-01 (260
      meetings/33 days) vs old-pipeline historical average
      meetings-per-state-per-weekday (786 meetings, 2026-04-01..07-13).
      **Overall 103.9% — the 92%-of-TAB claim holds on our data** (PF
      meets or exceeds what the old pipeline actually collected).
      Per-state weekly ratios: NSW 95.4%, VIC 103.6%, QLD 102.4%,
      SA 125.8%, WA 109.9%, TAS 93.8%, NT 146.9%, ACT 75.0%. Caveats: PF
      window is only ~5 weeks (weekday cells average 4–5 samples), the
      historical baseline is winter-only, and 8 country one-off tracks
      rest on manual state assignment (noted in the script).
- [x] [C] **F-SEC-PLACEHOLDER** (sectional collector `Unknown_<id>` runner
      names): root cause fixed in `nsw_sectional_collector.py` — when a .tol
      file lacks the horse-metadata record the write path now resolves the
      runner against `race_results_history` (same race + finishing position,
      falling back to barrier; `pf_results_mapper.norm_name` claimed-name
      exclusion) or skips the runner with one warning per race; no new
      placeholder rows can be written. Prod report (read-only, 2026-08-01):
      **20 placeholder rows, all `source='nsw_pidata'` — 2026-01: 8
      (Canterbury Park 2026-01-01 R7), 2026-06: 7 (Hawkesbury 2026-06-04 R2),
      2026-07: 5 (Gosford 2026-07-09 R4); 20/20 join-resolvable.** The gated
      backfill-fix (`server/python/fix_sectional_placeholder_names.py
      --apply`: backup table `sectional_times_placeholder_backup_20260801`,
      idempotent updates of the resolvable rows only) is built and tested
      but **not yet run — it awaits the operator's explicit prod WRITE
      approval** (two-gate rule).
- [x] **[C] F-FORM-ASOF (2026-08-01, probe branch `probe/pf-last10-asof`): is
      `meeting_detail.last10` frozen as-of the race card or regenerated as-of
      query time? — VERDICT: CLEAN (frozen as-of the card).** `form_string`
      populated from `last10` at import time does NOT embed the subject
      race's own result; the PF-3 leak hypothesis is rejected and the
      task-12 retrain is unblocked on this front.
  - **Method:** three meetings inside the API wall with full recorded
    finishes, across three states — Randwick 2026-07-11 (NSW,
    meetingId 241257, 98 runners), Belmont Park 2026-07-11 (WA,
    meetingId 241265, 107 runners), Murray Bridge GH 2026-07-07 (SA,
    meetingId 241096, 87 runners). Fresh `meeting_detail` fetched
    2026-08-01 (workflow run 30687071784, artifact `pf-last10-asof-3`);
    per runner: fresh `last10` head vs finish AT the meeting vs finish of
    the PRIOR run (both from `race_results_history`). 292 runners
    classified; raw payloads + full evidence table committed under
    `scripts/fixtures/pf_last10_asof/`.
  - **Decoding (established on this data):** the RIGHTMOST character is
    the most recent run — decided 94.7% vs 54.7% (leftmost) on
    discriminating runners; strings are right-aligned, shorter strings =
    fewer runs on record; `x` = spell (no letter codes f/p observed in
    292 strings); `0` = a finish of 10th or worse (12 runners had both
    finishes ≥10 with head `0`).
  - **Measurement A:** 238 discriminating runners (meet ≠ prior after
    0-encoding): **230 CLEAN (96.6%)**, 2 ADVERSE (0.8%), 6 neither —
    ≥90% agreement threshold met. ALL 8 outliers have prior DB rows
    5–6 months stale (2026-01/2026-02), i.e. DB coverage gaps (intervening
    runs not in `race_results_history`), not PF regeneration. Sequence
    corroboration: per-runner digit alignment of fresh `last10`
    (rightmost-recent) against the DB finish sequence — **0 of 249**
    runners align better with the subject race included than without
    (93 align frozen-at-card exactly, 158 imperfect on DB gaps).
  - **Measurement B: NOT-APPLICABLE.** `pf_raw_payloads` holds
    `kind='results'` payloads only (one per meeting, matched for all
    three meetingIds); results payloads carry NO `last10` field
    (verified on the archived bytes) — `last10` appears only in
    `meeting_detail` payloads, which the 2026-07-31 archive run did not
    store. No archive-drift comparison is possible; the verdict rests on
    Measurement A.
- **Done when:** discrepancy rate is quantified and either negligible or
  explained in this file. — **Met 2026-08-01**, with two named follow-ups
  (F-TRACK-ALIAS, F-SEC-PLACEHOLDER) above.

### F-TRACK-ALIAS — repair status (DM-G2, 2026-08-01)

K5 (on `pf/trust-checks`) measured the doubles; G2 shipped the fix:

- **Future-ingest hole CLOSED:** `canonical_track_key` is single-sourced
  in `pf_results_mapper` (consolidated from `auto_results_collector.py`,
  which imports it back; explicit K5-evidenced alias entries only, each
  citing its measured pair) and the dedup key
  (`load_existing_keys`/`row_race_key`) now canonicalises. The stored
  `track` column keeps the source spelling — only the key canonicalises.
- **Existing doubles REPORTED** (`pf_track_dedup.py`, read-only run
  2026-08-01): **257 doubled race-days (34 doubled meeting-days — matches
  K5's ~40), 257 duplicate row-sets, 2,531 rows to delete.** Every doomed
  set is a `pf%` race_id set; every survivor is the old-pipeline
  (`met_aus_*`) set; the 10-double eyeball shows identical runner names on
  both sides. Note: K5's `Ballarat` vs `Sportsbet-Ballarat Synthetic`
  2026-07-12 pair was a matching artifact — that day's old rows are the
  SYNTHETIC meeting (field sizes identical race-by-race to PF's synthetic
  card), so `ballarat` and `ballaratsynthetic` stay distinct keys and the
  report correctly pairs the old rows with the synthetic PF rows.
- **`--apply` PENDING the operator's separate explicit prod WRITE
  approval** (two-gate rule; never run in CI). Single transaction; every
  deleted row pre-imaged to `pf_track_dedup_backup` (row as jsonb,
  deleted_at) before any delete; idempotent (second apply = zero deletes).
  Operator command:
  `DATABASE_URL=... python3 server/python/pf_track_dedup.py --apply`
- [ ] [C] `--apply` executed by operator; re-run report shows zero
      doubles; this box ticked with the run date.

## Phase E — feature uplift (flags + walk-forward evidence only)

Ranked by expected value; each behind its own flag, promoted only on
walk-forward + promotion-gate evidence (`retrain_preflight.py`):

- **Plan adjustment (Phase A finding):** PF sectionals need the Modeller
  tier, not Starter — so the free racing.com/RQ sectional collectors STAY in
  service, and the Starter-tier levers below move up the ranking. PF
  sectionals become a paid upgrade decision only if the model's sectional
  features prove valuable enough to fund Modeller.
- **Plan correction (2026-08-01, from the published reference pages):** the
  "token based authentication" on Ratings is the SAME `apiKey` query
  parameter as every other endpoint — no second auth mode exists. All five
  endpoint contracts are pinned and wrapped as typed `pf_client` accessors
  with docstring contracts: `/Updates/Scratchings?jurisdiction=` (timeStamp +
  deduction per item), `/Updates/Conditions?jurisdiction=`,
  `/User/Speedmaps?meetingId=&raceNo=` (pfScore, neuralPrice, speed, settle,
  mapA2E, jockeyA2E), `/Ratings/MeetingRatings?meetingId=`,
  `/form/strikerate?entityType=&jurisdiction=`.
- [ ] [C] **Speedmaps** (Starter ✓) — settling-position/pace-pressure
      features into the existing pace interactions
- [ ] [C] **PF Ratings** (Starter ✓) — consensus-pillar input and value
      screen vs Betfair prices
- [ ] [C] **Strike Rates** (Starter ✓) — trainer/jockey actual-vs-expected as
      features (upgrade on raw win% the model uses today)
- [ ] [C] **Conditions** (Starter ✓) — track grading + weather as serve-time
      features
- [x] [C] **Scratchings into the serve pipeline** (2026-08-01): the racecard
      provider folds `/Updates/Scratchings` into every card — late-scratched
      runners flagged (never deleted) with the deduction carried in the new
      additive `scratch_deduction` runner field; a scratchings-feed failure
      costs late scratchings only, never the card (declared-runner flags
      from meeting_detail still apply). Golden-pinned.
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
