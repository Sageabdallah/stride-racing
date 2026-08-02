# Agent 2 Findings

## Notes
- Usable T-10/SP-ish Betfair snapshot coverage query did not return rows in scope; advanced market-window analysis will be skipped or downgraded.
- Historical rail-position columns are not present in the production DB; rail-position analysis is conditional and currently skipped.
- Trainer is not stored in race_results_history or training_view_v2 at 18-month scale; trainer-specific findings are marked unavailable unless a broader source is added.
- Trainer-level first-up, trainer-jockey synergy, apprentice-claim, and stable-intent findings are limited because trainer and claim fields are not present in race_results_history/training_view_v2 at 18-month scale.
- training_view_v2 model-probability coverage is sparse in this scope (5/26324 rows), so 'missed winner' and EV-based findings are diagnostic only until the view is backfilled.
- training_view_v2 expected_value coverage is sparse in this scope (5/26324 rows), so model-underweight tagging is incomplete.
- No track-month cohort passed the 85% Betfair mapping gate. Market findings are downgraded and mapping should be expanded via server/python/build_betfair_mapping.py before production use.
