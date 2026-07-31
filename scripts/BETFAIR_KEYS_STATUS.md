# Betfair credentials — verification status

**Verified working: 2026-07-31**, via `scripts/betfair_keys_smoke.py` run from an
Australian residential IP (a-Shell on iOS). No secrets appear in this file.

## Result

- Interactive login on `identitysso.betfair.com.au`: **SUCCESS** — username/password
  correct, no pending password change, account not locked.
- Exchange API `listEventTypes` with the stored app key: **HTTP 200**, 23 event
  types, horse racing (event type 7) available.
- App keys registered on the account (`getDeveloperAppKeys`):
  - `Stride-Racing v1.0-DELAY` — delayed-data **true**, active **true** — this is
    the key currently stored as the `BETFAIR_APP_KEY` repository secret.
  - `Stride-Racing v1.0` — delayed-data **false** (the live key), active **false**
    — exists but has never been activated by Betfair.

## Consequences

- Anything using the current key gets **delayed market data** (Betfair delays
  prices on delayed keys and they cannot place bets). Fine for connectivity and
  research; not fine for live-price work (price-at-tip, CLV) or bet placement.
  For those, the live key `Stride-Racing v1.0` must be activated by Betfair —
  for Australian customers that goes through Betfair Australia's API/Automation
  Hub team; internationally it is a paid activation.
- **GitHub-hosted Actions runners can never reach Betfair.** Betfair's edge
  serves an HTML 403 block page to their US datacenter IPs on every endpoint
  (Exchange API and both SSO hosts, plain client and browser TLS fingerprint
  alike — measured 2026-07-31 in runs 5–6 of the `betfair-keys-smoke-test`
  workflow). Any Betfair-touching job needs an allowed-country network: a
  self-hosted runner (e.g. the development Mac) or any AU-located machine.
- Session tokens (`ssoid` cookies) die on logout, password change, and idle
  expiry — a stored one is a convenience, not a durable credential. The
  username/password (or a registered cert) is what survives.

## Re-running the check

- Locally / any AU machine:
  `BETFAIR_APP_KEY=... BETFAIR_USERNAME=... BETFAIR_PASSWORD=... python3 scripts/betfair_keys_smoke.py`
  (or `BETFAIR_SESSION_TOKEN=...` instead of username/password).
- Via GitHub Actions: the `betfair-keys-smoke-test` workflow — but only useful
  once it targets a self-hosted runner in an allowed country, per above.
