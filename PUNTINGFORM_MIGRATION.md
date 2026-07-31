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

## Phase A — connectivity & API mapping  *(in progress)*

- [x] [U] Buy Starter; add `PUNTINGFORM_API_KEY` repository secret (2026-07-31)
- [x] [C] Probe tooling: `scripts/puntingform_probe.py` + `puntingform-probe`
      workflow — fetches their API reference and exercises candidate endpoints
      with the real key from a GitHub runner
- [ ] [C] Read probe output; record the confirmed base URL, auth style, and the
      exact endpoints for meetings, fields/form, results, sectionals, ratings,
      scratchings in this file
- **Done when:** a meetings call for today's date returns real data in CI.

## Phase B — 60-day backfill  *(time-critical — first coding session after A)*

- [ ] [C] `server/python/pf_client.py`: thin client (auth, retry, rate-limit
      courtesy, raw-payload archiving hook)
- [ ] [C] Backfill runner: walk back 60 days; import results into
      `race_results_history` (same shape as the old importer, tagged
      `source='puntingform'`); stage sectionals into `sectional_times`
- [ ] [C] Raw archive: every response persisted (date-keyed) before parsing —
      the start of the owned archive
- [ ] [C] Gap report: which dates/meetings the old pipeline missed since the
      Racing API died, and which of those PF filled
- **Done when:** `race_results_history` has no missing metro race days in the
  last 60 and the gap report is committed.

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

- [ ] [C] National sectional coverage feeding the existing sectional features
      (widens where current signals fire — biggest expected lift)
- [ ] [C] Speed-map / settling-position features into the pace interactions
- [ ] [C] PF ratings + rated prices as a consensus pillar input and a
      value-screen vs Betfair prices
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
