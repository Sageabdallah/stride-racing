# ACTION_THIS

Checklist for the laptop (the Mac), in order. Background: Betfair credentials
were fully verified working on 2026-07-31 from an Australian IP — see
`scripts/BETFAIR_KEYS_STATUS.md` for the results and the facts referenced below.

## 1. Update the repo

```bash
git checkout main
git fetch origin
git reset --hard origin/main
```

Use reset, not pull: main's recent history was rewritten on 2026-07-31
(commit metadata cleanup), so a plain pull would tangle the old and new
histories. The Desktop checkout has no local commits to lose, but stash any
uncommitted edits first. Do not commit the untracked `CLAUDE.md` or `claude/`
directory if they are still there.

## 2. Purge the OLD Betfair password everywhere on this machine

The repeated "reset your password" loop is classically caused by something
still retrying the old password and re-locking the account. Check and update:

- [ ] `.env` in this repo (and any other project folders with Betfair entries)
- [ ] any running scripts/watchers that talk to Betfair
- [ ] saved browser logins (Safari/Chrome autofill) and the Betfair app

## 3. Add the Betfair entries to `.env`

`.env` is gitignored — real values never go in git. Copy the three values from
the password manager, matching the names documented in `.env.example`:

```bash
BETFAIR_APP_KEY=...
BETFAIR_USERNAME=...
BETFAIR_PASSWORD=...
```

## 4. Verify from this machine

```bash
set -a; source .env; set +a
python3 scripts/betfair_keys_smoke.py
```

Expected: `VERDICT: KEYS OK`. Anything else prints its meaning in plain words.

## 5. Install a self-hosted GitHub Actions runner

GitHub's own runners can never reach Betfair (their US datacenter IPs are
edge-blocked). A runner on this Mac executes workflows from an allowed
Australian IP while secrets stay in GitHub:

- [ ] GitHub → stride-racing → Settings → Actions → Runners → New self-hosted
      runner → macOS → run the commands GitHub displays (`config.sh` with the
      shown registration token, then `run.sh`; use `./svc.sh install` to keep
      it running as a service)
- [ ] Then ask Claude to switch the `betfair-keys-smoke-test` workflow to
      `runs-on: [self-hosted]` (one-line change) and dispatch a run to confirm
      the GitHub button works end to end

## 5b. Unblock the racing.com data migration (The Racing API is dead)

The Racing API has ceased AU coverage — racecards, results, historical and
odds-movement inflows are down. racing.com GraphQL is the free replacement
(already used for sectionals). A read-only probe (`scripts/racing_com_probe.py`,
`racing-com-probe` workflow) is ready but blocked:

- [ ] Add `RACING_COM_API_KEY` as a GitHub repository Actions secret (it is
      currently only in the Mac's local `.env`, not in GitHub). Then re-run the
      `racing-com-probe` workflow — it maps which states/racecards/results the
      key can serve, which is the input for swapping the six dead Racing API
      modules (`download_racecards.py`, `fetch_and_import_date.py`,
      `auto_results_collector.py`, `download_historical.py`,
      `backfill_barrier_trials.py`, `odds_movement.py`).
- [ ] Alternatively run the probe locally on the Mac:
      `RACING_COM_API_KEY=... python3 scripts/racing_com_probe.py`
- [ ] Decide Punting Form Starter ($59/mo) only after the probe shows the gap.

## 6. Housekeeping (any device, if not already done)

GitHub secrets verified present on 2026-07-31: `BETFAIR_APP_KEY`,
`BETFAIR_USERNAME`, `BETFAIR_PASSWORD` (plus the expired session token below).

- [ ] Rotate the Betfair password on betfair.com.au, then update: password
      manager → GitHub secret `BETFAIR_PASSWORD` → this machine's `.env`
- [ ] Delete the dead `BETFAIR_SESSION_TOKEN` repository secret on GitHub
- [ ] Optional: ask Betfair Australia to activate the live app key
      (`Stride-Racing v1.0`, currently inactive) — the delayed key distorts
      price-at-tip/CLV work. Rerun the smoke test afterwards; it will show
      `active=True` when done.
