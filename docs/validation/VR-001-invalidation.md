# VR-001 — invalidation record

**Status: INVALIDATED. Not a failed rule. Not graveyarded.**

Append-only correction to [`registry.md`](registry.md), per its rule that
corrections are new entries referencing the old and never edits. This file is
self-contained: every claim below is verifiable from the repository without
reference to the discussion that produced it.

---

## The claim

VR-001 was registered against a system whose consensus pillar was already
non-functional at the moment of registration. The window therefore never validly
ran. This is a defect in the *system under test*, not in the registered rule, and
not a FAIL of the rule on data — the rule was never tested.

## The evidence, inline

**1. The registration event.**

```
$ git log -1 --format='%H %cI %s' b26c6b5
b26c6b5933736dd231665c0ebc3aea94437effda 2026-08-02T13:02:08+10:00 \
  WP-8: register the retrain window before any outcome is examined
```

`docs/project_retrain_gate.md` records the same instant as
`2026-08-02T03:02:08Z` and names window B as `2026-08-02 to 2026-09-13`.

**2. The extraction model in the consensus agent was retired 2026-06-15.**

`claude-sonnet-4-20250514` reached end of life on 2026-06-15. Calls to a retired
model id return HTTP 404. Verified against the live API on 2026-08-02:

```
extraction model 'claude-sonnet-4-20250514' is not callable:
NotFoundError: Error code: 404 - {'type': 'error', 'error':
{'type': 'not_found_error', ...}}
```

**3. That id was on `main` continuously, and was never changed.**

```
$ git log --format='%h %cI %s' -S "claude-sonnet-4-20250514" main \
      -- server/python/consensus_agent.py
8f9a1f8 2026-05-19T20:02:31+10:00 Initial release: STRIDE horse racing ML pipeline
```

Exactly one commit ever touched the string: the initial release. It was
introduced 2026-05-19 and never modified. Immediately before the repair it stood
at three call sites on `main`:

```
$ git show main:server/python/consensus_agent.py | grep -n "claude-sonnet-4-20250514"
826:            model="claude-sonnet-4-20250514",
929:            model="claude-sonnet-4-20250514",
1002:            model="claude-sonnet-4-20250514",
```

**4. The failure was silent by construction.**

Every extraction path caught the exception, returned no picks, and continued.
The agent then wrote a complete consensus file of all-zero-mention scores,
printed `[CONSENSUS] Complete`, and exited 0. Nothing downstream could
distinguish that from a genuinely quiet news day.

**5. The consequence for the bet population.**

`crowd_score` is zero for every runner when extraction yields nothing.
`run_tips_pipeline.py` takes only horses with `crowd_score >= 50` as bet
candidates (top 3 per race) and forces every other pick to
`should_bet = False, classification = "REJECTED"`. With `STRIDE_CROWD_GATE_ONLY`
default-on the crowd cannot create a bet, but it does veto. So from 2026-06-15
the crowd gate was vetoing against an all-zero vector on every race.

## The arithmetic

The model retired **2026-06-15**. VR-001 was registered **2026-08-02** — 48 days
later. Window B opened the same day. There is no interval in which the window
was accruing against a working consensus pillar.

## Why invalidated rather than graveyarded

`registry.md`'s graveyard holds rules that were tested and FAILed, kept so they
are never re-tested on overlapping windows. VR-001 was never tested. Filing it in
the graveyard would assert a result that was never obtained, and would bar a
replacement rule that has not been disproven. It is filed here instead.

## Why `docs/project_retrain_gate.md` is deliberately not amended

That document's dates remain true statements about the object it governs. The
five retrain gates are implemented in `server/python/gate_status.py` as
`gate1_snapshot_weeks`, `gate2_gseries`, `gate3_shadow_flips`,
`gate4_calibrator_coverage`, `gate5_preflight`. **None reads VR-001 or
`validate_forward`.** "May you retrain" and "may you quote a validated ROI band"
are separate decisions that happen to share dates; the retrain gate does not
depend on this registry entry, so nothing there requires correction. Its
cross-reference row naming VR-001 now points at an invalidated entry — following
that pointer lands in `registry.md`, which `validate_forward.py` names as the
ledger of record.

## Why the no-peek guard is not weakened

`validate_forward.window_read_allowed(entry, today=None)` derives the first
readable date from `entry["window_b"][1]`; `REGISTRY` is keyed by entry id.
Registering a successor is a data addition. VR-001's entry and its guard are left
in place, unreadable until 2026-09-14 as originally registered, over a window now
recorded as invalid.

## The repair, and what governs from here

Repaired 2026-08-02 on top of `main` at `ab68294`: the extraction model id moved
to `ANTHROPIC_CONSENSUS_MODEL` (default `claude-sonnet-5`), `temperature` was
dropped (it is rejected with a 400 on that model family), thinking was explicitly
disabled, a startup preflight aborts with exit 3 on a dead id or rejected
parameter, a zero-yield run now exits 4 instead of 0, and the DB mirrors retry.

The successor entry is **VR-002**. Its window opens at the **deploy** of that
repair, not at its commit — the bet population changes when the scheduled task
picks the change up, which is a later and different instant.

## The generalisable defect

Pre-registration protected the rule and said nothing about whether the system
under test was working when the rule was registered. Nothing in the protocol
required checking. Two preconditions are proposed for
`09-forward-validation-protocol.md` as a result — a **system-health pre-flight**
before any registration, and a **zero-skill negative control** on any analysis
that conditions on a market baseline. See
[`market-baseline-negative-control.md`](market-baseline-negative-control.md).
