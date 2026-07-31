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

## Phase B — 60-day backfill  *(in progress 2026-07-31)*

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
      old pipeline — the gap report will say).
- [ ] [C] Verification pass (next session): per-day row counts vs meeting
      calendar; NULL rates on distance_m / race_class / race_name; exact
      old-pipeline death date and the definitive gap report
- **Done when:** the verification numbers and gap report are recorded here.

## Phase C — daily ingestion (replace the six dead modules)

- [ ] [C] `download_racecards.py` → PF meetings/fields (keep output shape and
      `TARGET_TRACKS` filtering so downstream code is untouched)
- [ ] [C] `fetch_and_import_date.py` + `auto_results_collector.py` → PF
      results (existing racing.com fallback retained)
- [ ] [C] `download_historical.py` + `backfill_barrier_trials.py` → PF within
      the 60-day window; document the pre-window limitation
- [ ] [C] `odds_movement.py` → Betfair prices (delayed key now; live key when
      activated). Note: Betfair calls cannot run on GitHub-hosted runners —
      Mac/self-hosted runner only
- [ ] [C] Scheduled workflows: morning racecards pull, evening results pull,
      raw archive push; failure = red run (no silent passes)
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
- **Done when:** discrepancy rate is quantified and either negligible or
  explained in this file. — **Met 2026-08-01**, with two named follow-ups
  (F-TRACK-ALIAS, F-SEC-PLACEHOLDER) above.

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
