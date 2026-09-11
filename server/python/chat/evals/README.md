# Chat evals (vendored)

`golden.jsonl` (30 cases), `injection.jsonl` (16 cases) and `fixtures/` are
copied verbatim from `stride-app` at commit `810e7c1` (`evals/chat/`). They
are the acceptance test the chat plan names (docs/chat/CHAT_INTEGRATION_PLAN.md
§6, Phase 0 exit) and they are not edited here: a case that fails is a
defect in the agent, not in the case. To re-sync, copy the three things
again and update the commit hash in this sentence.

`fixtures/should_fail_*.json` are inverted controls: deliberately bad
responses that pass the suite only by tripping an assertion. They prove the
assertions bite.

Run with `python -m chat.eval_runner` (offline, fixtures only, zero network by
construction) or `python -m chat.eval_runner --live-cli` (operator only; costs
real tokens; needs `EVAL_LIVE_CONFIRM=yes`). See `chat/eval_runner.py`.

The `web-*` and `inj-page-*` cases need search mode, which this backend does
not have (plan §6, phase 5); the runner reports them as `unsupported`, not
as passes.
