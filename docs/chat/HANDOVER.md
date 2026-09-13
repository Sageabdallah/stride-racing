# Chat integration — session handover

Written 2026-09-13, at the end of the session that built the chat tool
layer and created the database role. Read this plus
[`CHAT_INTEGRATION_PLAN.md`](CHAT_INTEGRATION_PLAN.md) §13 to pick the work
up cold. This file records the live operational state, which the plan does
not: what has actually been run against real systems, and what is next.

Delete or rewrite this file once phase 0's live exit is met — it is a
snapshot, not a standing document, and a stale handover is worse than none.

## Where the work is

The plan (§2) chose **Fork A, operator-only**: no multi-tenant surface, no
Punting Form licence gate. Steps 1 and 2 of the plan's execution order are
built. Step 3 is in progress and is where the next session starts.

| Step | State |
|---|---|
| 1. Tool library (`server/python/chat/`) | Built, merged (#179), 78 offline tests |
| 2. Read-only role in Neon | **Created and proven live** (see below) |
| 3. Prove the data plane, no token spend | **In progress — blocked on operator env setup** |
| 4. Live eval suites | Not started; needs step 3 and a separate Anthropic key |
| 5-9. Lambda, DynamoDB, infra script, `chat-proof` smoke | Not started |
| 10-12. Express proxy in `stride-app` | Not started; different repository |

## What is true in the live account right now

**The role exists and cannot write.** `apply-migration` run
[34740673203](https://github.com/Sageabdallah/stride-racing/actions/runs/34740673203)
created `stride_chat_ro` and its final step printed `VERDICT: PASS: the
role cannot write`. Every INSERT, UPDATE, DELETE and CREATE TABLE was
refused with SQLSTATE 42501 inside an explicitly READ WRITE transaction, so
the refusals are the missing grant and not the session's read-only default.
That output is the evidence; do not re-run the migration to "check" — read
the run.

**Nothing else has touched AWS.** No Lambda, no secrets under
`stride/chat/*`, no Function URL. `server/python/chat/` still has no caller.

**The GitHub secret `STRIDE_CHAT_RO_PASSWORD` is set** and is what the
migration substitutes for the token in the SQL file.

## Open pull requests, both small

Both are fixes for defects found while running step 3 for real. Both are
green offline and independent of each other; they touch different regions
of `cli.py` and merge cleanly in either order.

- **[#181](https://github.com/Sageabdallah/stride-racing/pull/181)** —
  `verify_readonly_role.py` never loaded `.env`, so a
  `STRIDE_CHAT_DATABASE_URL` set there was invisible to it while `chat.cli`
  read it fine. Moves the loader into `config.load_dotenv_once()` that both
  call.
- **[#182](https://github.com/Sageabdallah/stride-racing/pull/182)** — adds
  `--args-file` to `chat.cli`. See "PowerShell" below; this is not a
  nicety, the inline `--args` path is unusable on the operator's machine
  for any tool call whose arguments contain a space.

## What the operator does next (step 3)

Nothing here costs an Anthropic token. The point is to prove the tools
return real rows before spending anything on a model.

1. Merge #181 and #182, then `git pull origin main` in the local clone.
2. Set `STRIDE_CHAT_DATABASE_URL` to the **pooled** Neon host (the hostname
   contains `-pooler`), user `stride_chat_ro`, that role's password, and
   `?sslmode=require&channel_binding=require`. Pooled matters: a Lambda
   with several warm containers will exhaust the direct host's connection
   limit.
3. `python -m chat.verify_readonly_role` — expect the same PASS verdict the
   workflow produced, this time from the operator's machine.
4. Run three tools and read the envelopes:

       python -m chat.cli --tool get_stride_tips  --args-file tips.json
       python -m chat.cli --tool lookup_horse     --args-file horse.json
       python -m chat.cli --tool get_performance  --args-file perf.json

   Wanted: `"ok": true` with rows under `data`. An `"ok": true,
   "found": false` is an honest miss and says why — acceptable, but read
   the reason. An `"ok": false` means a backend did not answer and names
   which; that is the thing to fix.

**Step 3's exit is content, not exit codes.** A tool that returns
`found: false` for every date proves nothing about the data plane. At
least one call must come back with real rows.

## Then step 4, which does cost money

Roughly 46 turns, about US$6-10 on `claude-opus-5`.

1. Issue the chat **its own** Anthropic key from a separate Console
   workspace with its own spend limit. The plan's reasoning (§3): it can be
   revoked without stopping the 05:30 consensus job.
2. `ANTHROPIC_API_KEY` and `ANTHROPIC_CHAT_MODEL=claude-opus-5` in `.env`.
3. `python -m chat.cli --preflight` first. One tiny call; exit 3 means the
   model id is dead or a parameter was rejected.
4. `EVAL_LIVE_CONFIRM=yes python -m chat.eval_runner --live-cli`

Seven search-mode cases report `unsupported` by design — search is not
ported (plan §6, phase 5). They are not passes and must not be counted as
such. Any other FAIL is a defect in the agent to fix; the cases do not
change. Both suites green here is phase 0's exit.

## Things that cost this session time

**The operator is on Windows PowerShell.** Three separate failures came
from this, none of them obvious in advance:

- `python` and `python3` are not on PATH; the launcher is `py -3`.
- Inline JSON in `--args` cannot be made to work. `\"` escaping makes
  Windows re-split the value on spaces; plain `"` inside single quotes gets
  mangled rebuilding the native command line. Values with no spaces come
  through intact, which makes it look intermittent. #182 is the fix; use
  `--args-file` on Windows, always.
- Commands are copy-pasted verbatim, placeholders included. Three
  connection attempts went to hosts literally named `<your real pooled
  host>` and `ep-yourhost-pooler.yourregion.aws.neon.tech`. **Do not write
  example values that could be mistaken for real ones.** Have the operator
  edit the string Neon itself prints, in a text editor, rather than
  assembling one from a template.

**`gh workflow run` dispatches against the remote default branch**, not the
local checkout. Two runs failed with "migrations/chat_readonly_role.sql
does not exist" purely because the pull request had not merged yet. Either
merge first, or pass `--ref <branch>`.

**Neon's owner role is not a PostgreSQL superuser.** The first apply failed
on `ALTER ROLE ... NOSUPERUSER` — Postgres refuses to let a non-superuser
touch the SUPERUSER attribute of any role, even to restate the default that
`CREATE ROLE` already set. Same restriction covers REPLICATION and
BYPASSRLS. Fixed in #180; the role's actual privileges are unchanged
because a bare `CREATE ROLE` already defaults every one of them off.

## Credential hygiene, unresolved

Two secrets were pasted into the session transcript:

- The **`stride_chat_ro` password**, in a full terminal-history paste. It
  is live. The role is read-only, but `pg_read_all_data` means it can read
  every table in the database. Rotating it is cheap and is the safe call:
  generate a new value, update the `STRIDE_CHAT_RO_PASSWORD` GitHub secret,
  re-run the `apply-migration` dispatch (the SQL file is written to be
  idempotent for exactly this), then update the local
  `STRIDE_CHAT_DATABASE_URL`.
- A **`neondb_owner` connection string**. The operator says the password in
  it was fabricated. If that is wrong, it is the full read-write owner
  credential and rotating it is urgent — and `DATABASE_URL` must be updated
  in the GitHub secret and everywhere else it is stored, or the 04:00 chain
  breaks.

Neither has been rotated. Confirm with the operator rather than assuming.

## Corrections to the plan found while building

Recorded in `CHAT_INTEGRATION_PLAN.md` §13, repeated here because they
change what a reader of §4 and §5 would otherwise expect:

- `lookup_horse` reads `blackbook_entries` and `blackbook_entry_runs`,
  whose schema `stride-app` owns. Columns are pinned in
  `chat/tools/horse.py` and covered by a test; the tables being absent is
  reported inside the result rather than failing the lookup.
- The eval harness's live mode reads tool names from a `trace` array the
  response contract does not have. The response therefore carries an
  additive `toolCalls` field, and the Python runner reads that. **Phase 4A's
  Express proxy must forward it** or the live evals go blind on tool
  assertions.
