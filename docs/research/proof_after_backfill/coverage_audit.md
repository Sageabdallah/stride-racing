# Coverage Audit

- Date range: `2024-01-01` to `2026-03-26`
- Tracks: Randwick, Kensington, Rosehill, Flemington, Caulfield, Moonee Valley, Eagle Farm, Doomben, Morphettville, Ascot WA, Sha Tin, Happy Valley
- Results rows: 26324
- Sectional rows: 12350
- Betfair mapped runners: 265
- Training view rows: 25815

## Capability Notes
- Usable T-10/SP-ish Betfair snapshot coverage query did not return rows in scope; advanced market-window analysis will be skipped or downgraded.
- Historical rail-position columns are not present in the production DB; rail-position analysis is conditional and currently skipped.
- Trainer is not stored in race_results_history or training_view_v2 at 18-month scale; trainer-specific findings are marked unavailable unless a broader source is added.

Coverage details written to `coverage/track_month_coverage.csv`.
