"""Prevention for the 2026-09-02..09 outage (issue #176).

The Perplexity account ran out of credits somewhere between 2026-09-02 and
2026-09-05. Two separate defects turned a billing lapse into a week of silence:

  * every query 4xx'd and the run carried on to the end of the card anyway,
    because a non-200 only logged a status and continued. Four doomed calls per
    race, then a generic "zero yield" at the end.
  * the exit contract tested `mentions == 0` exactly. On 2026-09-05 the panel
    scored 2 stray horses out of 779 across 55 races, so the run exited 0 and
    reported success. 2026-09-09 only alarmed because the panel happened to hit
    nothing at all that morning — the same broken state, a different coin flip.

These pin both shut. Zero network: requests is stubbed.
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("requests", types.ModuleType("requests"))

import consensus_agent as ca  # noqa: E402


class FakeResponse:
    def __init__(self, status, text='{"error":{"message":"insufficient credits"}}'):
        self.status_code = status
        self.text = text

    def json(self):
        return {"choices": [{"message": {"content": "tips"}}], "citations": ["u"]}


def _arm(monkeypatch, status):
    """Point the multi-query search at a server that always answers `status`."""
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    monkeypatch.delenv(ca.SEARCH_OPTIONAL_ENV, raising=False)
    fake = types.SimpleNamespace(
        post=lambda *a, **k: FakeResponse(status),
        exceptions=types.SimpleNamespace(Timeout=type("Timeout", (Exception,), {})),
    )
    monkeypatch.setattr(ca, "_requests", fake)
    monkeypatch.setattr(ca.time, "sleep", lambda *_: None)
    monkeypatch.setattr(ca, "_save_usage", lambda *a, **k: None)


def _run_search(usage=None):
    return ca.search_race_tips_perplexity_multi(
        "Randwick", 1, "Race 1", 1200, "BM78", [{"horse": "A"}], ["A"],
        "2026-09-09", "Wednesday 09 September 2026", usage if usage is not None else {},
    )


# ------------------------------------- an unfunded account stops the run now

@pytest.mark.parametrize("status", [401, 402, 403])
def test_auth_or_billing_refusal_aborts_the_run(monkeypatch, status):
    """401 covers "an account which ran out of credits" in Perplexity's own
    docs; 402 is exhausted credits outright. Neither is transient, so retrying
    the other 3 queries and then the other 54 races only burns the card."""
    _arm(monkeypatch, status)
    with pytest.raises(ca.SearchUnavailable) as e:
        _run_search()
    assert str(status) in str(e.value)
    assert "credit" in str(e.value).lower()


def test_it_aborts_on_the_very_first_query(monkeypatch):
    """Not after four. The whole point is to stop before the spend."""
    calls = []
    _arm(monkeypatch, 401)
    real = ca._requests.post
    ca._requests.post = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    with pytest.raises(ca.SearchUnavailable):
        _run_search()
    assert len(calls) == 1


def test_an_absent_key_aborts_too(monkeypatch):
    """The gap the first cut of this guard left open, and the likelier fault.

    An ABSENT key never makes an HTTP call, so it never sees a 401/402/403.
    Guarding only the refusal path left a key missing from the container
    environment burning the whole card exactly as before — 32 races of "web
    research is DISABLED for this race" — which is the opposite of what the
    guard claims to do. A missing key is a secrets-delivery fault and is
    knowable before the first request.
    """
    _arm(monkeypatch, 200)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    with pytest.raises(ca.SearchUnavailable) as e:
        _run_search()
    assert "PERPLEXITY_API_KEY" in str(e.value)
    assert "secrets-delivery" in str(e.value)


def test_an_absent_key_is_survivable_when_declared_optional(monkeypatch):
    _arm(monkeypatch, 200)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.setenv(ca.SEARCH_OPTIONAL_ENV, "true")
    assert _run_search()[0] == ""


def test_an_absent_key_never_reaches_the_network(monkeypatch):
    """It aborts before the request, not after — the whole point of catching
    this case separately from the 4xx one."""
    _arm(monkeypatch, 200)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    calls = []
    ca._requests.post = lambda *a, **k: calls.append(1)
    with pytest.raises(ca.SearchUnavailable):
        _run_search()
    assert calls == []


def test_rate_limiting_is_not_fatal(monkeypatch):
    """429 IS transient. Treating it as fatal would kill a healthy day the
    first time a burst got throttled."""
    _arm(monkeypatch, 429)
    text, citations, counts = _run_search()
    assert text == "" and citations == 0


def test_a_server_error_is_not_fatal(monkeypatch):
    _arm(monkeypatch, 500)
    assert _run_search()[0] == ""


def test_search_optional_runs_panel_only_on_purpose(monkeypatch):
    """Local dev and CI have no Perplexity credit. 'Absent by design' has to be
    sayable or the guard gets deleted the first time it is inconvenient."""
    _arm(monkeypatch, 402)
    monkeypatch.setenv(ca.SEARCH_OPTIONAL_ENV, "true")
    assert _run_search()[0] == ""


def test_the_per_query_catch_all_does_not_swallow_it(monkeypatch):
    """The loop has a bare `except Exception` so one bad query cannot kill a
    race. SearchUnavailable must outrun it — it is not one bad query."""
    _arm(monkeypatch, 401)
    with pytest.raises(ca.SearchUnavailable):
        _run_search()


def test_the_refusal_is_recorded_in_the_usage_tally(monkeypatch):
    _arm(monkeypatch, 402)
    usage = {}
    with pytest.raises(ca.SearchUnavailable):
        _run_search(usage)
    assert usage.get("pplx_err_402") == 1


# --------------------------------- a collapse is no longer a passing grade

def _health(horses, scored, races=55, mentions=None, dry_run=False):
    results = {}
    remaining = scored
    for i in range(races):
        n = horses // races + (1 if i < horses % races else 0)
        race = {}
        for h in range(n):
            hit = remaining > 0
            if hit:
                remaining -= 1
            race[f"h{i}_{h}"] = {"total_mentions": 1 if hit else 0}
        results[f"t_R{i}"] = race
    return ca.build_health("2026-09-05", results, {}, [], True, None,
                           dry_run=dry_run)


def test_the_2026_09_05_collapse_now_fails(monkeypatch):
    """The real numbers: 2 horses carried a mention out of 779, across 55
    races. mentions == 0 was False, so this exited 0 and reported success."""
    monkeypatch.delenv("STRIDE_CONSENSUS_MIN_YIELD", raising=False)
    h = _health(horses=779, scored=2)
    assert h["total_mentions"] == 2
    assert h["zero_yield"] is False, "not zero — that is exactly the problem"
    assert h["low_yield"] is True, "0.26% must not pass"
    assert h["yield_rate"] == pytest.approx(2 / 779, abs=1e-4)


def test_a_healthy_august_day_still_passes(monkeypatch):
    """2026-09-02: 98 of 445 carried mentions. The floor must not fail the
    days the system was working."""
    monkeypatch.delenv("STRIDE_CONSENSUS_MIN_YIELD", raising=False)
    h = _health(horses=445, scored=98, races=30)
    assert h["low_yield"] is False
    assert h["yield_rate"] > 0.2


def test_the_worst_healthy_day_has_headroom(monkeypatch):
    """17.9% was the weakest day in the observed healthy band (2026-08-28).
    The floor sits well under it, on purpose."""
    monkeypatch.delenv("STRIDE_CONSENSUS_MIN_YIELD", raising=False)
    assert _health(horses=273, scored=49, races=17)["low_yield"] is False


def test_zero_and_low_yield_are_mutually_exclusive(monkeypatch):
    """Two distinct failures, two distinct messages. Zero must not also be
    reported as 'below the floor' — the operator repairs them differently."""
    monkeypatch.delenv("STRIDE_CONSENSUS_MIN_YIELD", raising=False)
    h = _health(horses=779, scored=0)
    assert h["zero_yield"] is True
    assert h["low_yield"] is False


def test_a_dry_run_never_trips_the_floor(monkeypatch):
    """A dry run makes no extraction calls, so zero yield is its correct
    result, not a fault — same reasoning the zero_yield flag already uses."""
    monkeypatch.delenv("STRIDE_CONSENSUS_MIN_YIELD", raising=False)
    h = _health(horses=779, scored=2, dry_run=True)
    assert h["low_yield"] is False and h["zero_yield"] is False


def test_the_floor_is_tunable_and_disablable(monkeypatch):
    monkeypatch.setenv("STRIDE_CONSENSUS_MIN_YIELD", "0.5")
    assert ca.min_yield() == 0.5
    assert _health(horses=445, scored=98, races=30)["low_yield"] is True
    monkeypatch.setenv("STRIDE_CONSENSUS_MIN_YIELD", "0")
    assert _health(horses=779, scored=2)["low_yield"] is False, "0 disables"


@pytest.mark.parametrize("bad", ["banana", "50", "-1", "1.5"])
def test_an_unusable_floor_is_ignored_not_obeyed(monkeypatch, bad):
    """A typo'd 50 read as 5000% would fail every day forever."""
    monkeypatch.setenv("STRIDE_CONSENSUS_MIN_YIELD", bad)
    assert ca.min_yield() == ca.DEFAULT_MIN_YIELD


def test_yield_is_recorded_even_when_the_run_passes(monkeypatch):
    """The floor is provisional. It can only be set from data if every run
    banks the ratio, not just the failing ones."""
    monkeypatch.delenv("STRIDE_CONSENSUS_MIN_YIELD", raising=False)
    h = _health(horses=445, scored=98, races=30)
    assert h["low_yield"] is False
    assert "yield_rate" in h and "min_yield" in h


def test_the_breakdown_states_the_ratio_against_the_floor(monkeypatch):
    monkeypatch.delenv("STRIDE_CONSENSUS_MIN_YIELD", raising=False)
    line = ca._zero_yield_breakdown(_health(horses=779, scored=2))
    assert "yield 2/779" in line
    assert "0.26%" in line and "5%" in line
