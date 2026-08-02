# Task 10 step 4: exchange-mid feasibility spike

Verdict: GO, and it is already implemented in substance.

The DM-1 Betfair adapter (betfair_odds_snapshot.py) has been storing best
back price and size, best lay price and size, last traded price, and total
matched volume per runner per capture into runner_odds_snapshots since
2026-08-02 (migration runner_odds_snapshots_betfair.sql). Exchange mid at
any captured timepoint is therefore (back + lay) / 2 over existing columns;
no new columns are needed. T-5 coverage comes from the late_t5 rows the
capture_late_odds watcher writes (ported to Betfair in WP-1).

Coverage evidence, first live day (2026-08-02): 47 AU WIN markets listed,
8 of 8 scheduled races mapped, 52 of 53 active runners priced (one name
missing from the horse bridge, reported not guessed).

Rate limits: the delayed app key served four full capture runs in one day
without throttling; per-market book pulls are one JSON-RPC call per ~40
markets. The live key (inactive, needs Betfair AU activation) removes the
price delay but is not required for mid computation.

Follow-up when task 14 wants WOM/liquidity features: expose a
exchange_mid(runner_row) helper over the existing columns; nothing to
migrate.
