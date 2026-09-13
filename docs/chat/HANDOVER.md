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
| 0 — exit (live evals) | **Run; 3 of 38 failed; fixed, re-run pending merge** | run #1 (`34750824003`, 35/38, on `0967164`); diagnosis run #2; fix on `claude/zen-mayer-md6v9k`; run #3 result below |
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

**The chat has now run live, once, and it did not fabricate.** `chat-eval`
run #1 (2026-09-13 10:02 UTC, on `0967164`) executed all 38 non-search cases
against the real database as `stride_chat_ro`, the real Punting Form key and
the real relay: 35 passed, 3 failed, 8 unsupported, all 13 executed injection
cases green. A diagnostic run (#2, three cases, responses captured) showed all
three failures were honest answers that missed the assertion, not invented
ones; the section below has each root cause. The fix is on
`claude/zen-mayer-md6v9k` and the offline suite is 89 green.

**What run #1 taught about the data.** The corpus's April dates do not all
line up with the calendar: 12 April 2026 is a Sunday and Randwick raced on the
11th; 6 April is Easter Monday and STRIDE has no selections between 28 March
and 8 April. The blackbook holds 102 entries, all made in August 2026 from
July and August form, so "blackbooked in March 2026" is honestly empty. None
of that is a corpus defect; it is what an honest agent has to say plainly.

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

## Run #1's three failures, and where each actually was

Read the turn logs before believing a headline. Run #1's report said
`chain-04` and `follow-02` called no expected tool; mapping the 41 `chat_turn`
lines to cases showed they failed in different places.

- **`chain-04` — a tool-surface gap, not a prompt lapse.** The model did call
  a tool: `get_stride_tips` over 1–31 March, because nothing listed the
  blackbook by period and that was the nearest thing. It got a 24,000-character
  truncated dump of selections, said so, and asked the user for horse names.
  `lookup_horse` now takes a `blackbooked_from`/`blackbooked_to` window and
  returns each entry with the horse's runs and wins since its source race.
- **`follow-02` — an uninformative miss upstream, not session memory.** Both
  setup turns missed: no Randwick selections on Sunday 12 April, a bare
  "couldn't find", and so no horse ever entered the conversation for "how has
  that horse gone since?" to look up. The selections-table miss now names the
  tracks that did carry tips, the leading selections elsewhere and the nearest
  dates at the track asked for (Royal Randwick, 11 April, 10 selections), and
  the prompt says a "since" question is a fresh lookup.
- **`miss-02` — wording, not fabrication.** "STRIDE has no tips on record for
  Timbuktu" is honest and contains none of `couldn't find`, `could not find`,
  `don't have`, `do not have`, `no record`. Prompt v3.1 pins the words of a
  miss. The case is not mis-specified: a fixed vocabulary for a miss is a
  product property, and the other three miss cases already met it.

Left for its own change: a range query over a busy month returns the first
400 selection rows and reports only the dates those rows fall on, so "March"
came back as "6 and 7 March". The chain-04 response surfaced it; the fix is
per-date capping with a real count, not a bigger limit.

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

**2026-09-13, later.** `chat-eval` run #1 executed live: 35/38, 3 failed, no
fabrication. Diagnosed each (section above) and fixed them in the tool layer
and prompt v3.1, corpus untouched: `lookup_horse` blackbook window,
`get_stride_tips` misses that say what did exist, and the miss vocabulary
pinned. Offline suite 78 → 89. The full 46-case re-run on the branch is run #3
(`34752688083`); its result is recorded here once it completes.

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
