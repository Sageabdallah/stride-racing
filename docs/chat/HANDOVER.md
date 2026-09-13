# STRIDE chat integration — handover

The session-to-session state file for the chat work. It answers three questions
and nothing else: what is proved, what is blocked and on whom, and what the next
session should pick up. The design lives in
[`CHAT_INTEGRATION_PLAN.md`](CHAT_INTEGRATION_PLAN.md) — this file does not
restate it, so that the two cannot drift into disagreeing.

Every claim here names the evidence for it. "Built" means the code is on `main`.
"Proved" means something ran and asserted on its own output. They are different
words on purpose.

Last updated 2026-09-13.

---

## Where the work stands

| Phase (plan §6) | State | Evidence |
|---|---|---|
| 0 — the tool library | **Built** | PR #179; 78 offline tests green, no credential, no network |
| 0 — exit (live evals) | **Dispatchable, not run** | `chat-eval` workflow exists; needs `confirm=RUN` and spends tokens |
| 1 — read-only role | **Closed, proved** | `apply-migration` run #6, 2026-09-13 05:35 UTC, on `40b698b` |
| 2 — the fork decision | **BLOCKED — operator** | §11 questions 1 and 2, unanswered |
| 3A — AWS | Not started, and must not be | §6: "Do not build AWS resources before this" |
| 4A — the app | Not startable here | `stride-app` is a separate private repository |
| 5 — streaming, search port | Later, each its own decision | — |

## What is actually proved

**The read-only role exists and cannot write.** `stride_chat_ro` was created on
Neon on 2026-09-13 at 05:35 UTC. Runs #3–#5 failed first — Neon's owner role is
not a Postgres superuser — and PR #180 fixed the SQL. The same job then
connected *as the role* and ran `server/python/chat/verify_readonly_role.py`:
every INSERT, UPDATE, DELETE and CREATE TABLE was refused with SQLSTATE 42501
inside an explicitly READ WRITE transaction, and reads succeeded.

The READ WRITE part is the whole proof. Under the migration's
`default_transaction_read_only = on`, a role holding full write grants also
refuses an INSERT — so the obvious version of this test passes for the exact
failure it exists to catch. Forcing the transaction read-write first means only
a missing privilege can explain the refusal. If you change that verifier, keep
that property.

**Nothing else on the chat path has been proved live.** The tool library has
78 offline tests and an offline eval pass, which prove the code does what the
recorded fixtures say. No chat turn has yet run against the real database, the
real Punting Form key, or the real artifact relay.

## The blocker, and it is not a technical one

Phase 2 is two questions in §11, and only the operator can answer them:

1. **Audience** — operator-only (Fork A) or public (Fork B)? This one answer
   fixes compute, auth and tenancy.
2. **Punting Form terms** — is there *written* commercial clearance to serve
   Punting Form data to anyone but the operator? `PLAN.md` §15 flags this as
   the largest gate, and it covers derived predictions, not just the shared key.

Until both are answered, §6 forbids building AWS resources, and phase 3A is the
next thing there is to build. Everything below that line which could be built
without an operator decision now has been. A session that arrives here and
wants to make progress should ask the questions, not pick a fork and start.

## Running the phase 0 exit

The exit is the 46 cases green live against the CLI over the read-only role.

    Actions → chat-eval → Run workflow → confirm: RUN

No new secret is needed. `DATABASE_URL` (read for its host only — the owner
credential never connects), `STRIDE_CHAT_RO_PASSWORD`, `ANTHROPIC_API_KEY` and
`PUNTINGFORM_API_KEY` are all set. It spends tokens on the estate's Anthropic
key, shared with the 05:30 consensus job, which is why the confirmation is
typed rather than a checkbox. Plan §6 phase 3A gives the chat its own key from
a separate Console workspace; that belongs with the Lambda.

The workflow proves four legs before spending anything, and each states what it
actually proves: the database returns **rows** to `stride_chat_ro`, Punting Form
returns **meetings** across three consecutive days, the S3 relay **answers**, and
the model answers a real 16-token call. It then floors the run itself: 46 cases
must load, all 38 non-search cases must execute, at least one must pass. That
floor exists because `_report` returns 0 when nothing failed, and nothing fails
when nothing ran.

Two of those are deliberately shaped against a specific wrong version of
themselves. The relay leg is **reachability only** — an absent tips file is not
a gate, because every artifact-backed case asks about April 2026, those keys
were never relayed (the relay ships only the current day, from 2026-08-02, with
no backfill), and the cases assert tool *names*, which are recorded whether the
tool hit or missed. Gating on a fresh tips file would block a phase 0 exit that
would otherwise pass. The model leg is a **live call**, not a non-empty
`ANTHROPIC_API_KEY`: with a retired model id, 12 injection cases pass vacuously
against the string "model call failed", so a partial run over injection ids
would exit 0 green with the model completely dark.

Expect 8 `unsupported` results. Those are the `web-*` and `inj-page-*` cases;
search mode is not ported (§6 phase 5) and they are not counted as passes.

**If cases fail, they are a defect in the agent.** Plan §12 is explicit and
`CLAUDE.md` says the same thing in general terms: do not edit the corpus, relax
an assertion, or add a skip to get a green. The 30 golden and 16 injection
cases are the acceptance test as written.

## What the exit does not prove

Asked what `chat-eval` would still pass with — the question `CLAUDE.md` says to
ask of any check — the answer is: a chat whose tools are called and answer "the
database did not answer" to every query. The golden cases assert *which tools
the agent reached for*, not that the call came back with rows. The three
preflights close most of that by proving the legs are live, but not all of it.

Phase 3A's `chat-proof` is the check that closes it properly, by asserting on
the tool results rather than the prose. Do not treat a green `chat-eval` as
evidence that answers are grounded.

## Things a new session gets wrong

- **The working directory is `Race-Analytics/server/python`**, not
  `Race-Analytics/`. The chat CLI is `python -m chat.cli --help` from there.
- **`.claude/skills/` and `tipster_panel.json` are gitignored and absent from a
  CI checkout.** That is by design, not a missing file. Work from the code or
  say what you could not determine.
- **`stride-app` is a different repository** and is not in scope here. Phase 4A
  cannot be done from this one.
- **The chat's database URL is `STRIDE_CHAT_DATABASE_URL`, never
  `DATABASE_URL`.** The latter is the owner role. `config.py` says why.
- **`infra/*.sh` is off limits** in an unattended run; `infra/jobs/**` is not.
  `CLAUDE.md` explains the distinction — it is about schedule state versus
  application code, not about the folder name.

## Changelog

**2026-09-13.** Phase 1 applied and proved (run #6). Added
`.github/workflows/chat-eval.yml` so the phase 0 exit is a dispatch rather than
a local credential setup. Fixed `chat/db.py`: `query()` imported
`psycopg2.extras` before `_connection()` could reach its missing-URL check, so
an unconfigured chat raised `ModuleNotFoundError` instead of the documented
`DatabaseUnavailable`; it passed in CI only because `ci.yml` installs the
driver. A missing driver is now reported as `DatabaseUnavailable` too. The
offline suite runs 78 green with psycopg2 absent, which is what the status
section had claimed of it all along. Wrote this file — it was referenced as the
handover before it existed.

**2026-09-11.** Phases 0 and 1 built on `claude/peaceful-edison-62mpc1`
(PRs #179, #180).
