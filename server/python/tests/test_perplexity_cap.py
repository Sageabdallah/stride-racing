"""Perplexity gets a spend ceiling, like the other two APIs already had.

Tavily was capped at 50 and Claude at 200. Perplexity was capped at nothing:
what stood in for a cap was a soft warning at 200 calls that printed a line
and carried on, so on a 55-race Saturday (220 queries, 4 per race) it printed
that line about eighty times and stopped nothing.

Two properties are pinned here, and they pull in opposite directions:

  * the cap actually stops calls, before they are made
  * reaching it does NOT raise. SearchUnavailable at that point would discard
    every mention found for the earlier races and take the day to NO_BET,
    which is a worse outcome than a partial consensus and is not something a
    spend guard should ever cause

Zero network: requests is stubbed.
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("requests", types.ModuleType("requests"))

import consensus_agent as ca  # noqa: E402


class FakeResponse:
    status_code = 200
    text = "{}"

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": "tips"}}],
                "citations": ["https://example.com"]}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    monkeypatch.delenv(ca.SEARCH_OPTIONAL_ENV, raising=False)
    monkeypatch.delenv("STRIDE_PERPLEXITY_CAP", raising=False)
    monkeypatch.setattr(ca.time, "sleep", lambda *_: None)
    monkeypatch.setattr(ca, "_save_usage", lambda *a, **k: None)


def _arm(monkeypatch, calls):
    def post(*a, **k):
        calls.append(k.get("json", {}).get("model"))
        return FakeResponse()
    monkeypatch.setattr(ca, "_requests", types.SimpleNamespace(post=post))


def _run(usage):
    return ca.search_race_tips_perplexity_multi(
        "Randwick", 1, "Race 1", 1200, "BM78", [{"horse": "A"}], ["A"],
        "2026-09-16", "Wednesday 16 September 2026", usage)


# ------------------------------------------------------------- it really stops

def test_a_race_under_the_cap_runs_every_query(monkeypatch):
    calls = []
    _arm(monkeypatch, calls)

    _run({})
    assert len(calls) == 4, "four labels: newspaper, portal, data, social"


def test_no_query_is_made_once_the_cap_is_spent(monkeypatch, capsys):
    """The teeth. A tally already at the cap must buy nothing further, and the
    check sits BEFORE the increment so the cap bounds calls made."""
    calls = []
    _arm(monkeypatch, calls)

    _run({"perplexity_newspaper": ca.DEFAULT_PERPLEXITY_CAP})
    assert calls == []
    assert "CAP REACHED" in capsys.readouterr().err


def test_the_cap_stops_a_race_part_way_through(monkeypatch):
    """One query of headroom buys exactly one query, not the whole race."""
    calls = []
    _arm(monkeypatch, calls)

    _run({"perplexity_newspaper": ca.DEFAULT_PERPLEXITY_CAP - 1})
    assert len(calls) == 1


def test_the_tally_sums_every_label(monkeypatch):
    """Spend is spread across four keys. Reading one of them would let a run
    spend four times the cap."""
    quarter = ca.DEFAULT_PERPLEXITY_CAP // 4
    usage = {f"perplexity_{lbl}": quarter
             for lbl in ("newspaper", "portal", "data", "social")}
    assert ca._pplx_calls(usage) == quarter * 4

    calls = []
    _arm(monkeypatch, calls)
    _run(dict(usage))
    # 4*quarter is at or one under the cap, so at most one query gets through.
    assert len(calls) <= 1


def test_refused_queries_still_count_against_the_cap(monkeypatch):
    """A request the provider rejected is still a request that was made.
    Counting only the ones that came back with content would let a run that
    is failing every query spend without limit -- which is the 2026-09-16
    shape, where the account answered three races and refused the rest.

    429 rather than 401/402/403: those are fatal by design and raise before
    this could be observed. The point here is the tally, not the refusal."""
    usage = {}

    def post(*a, **k):
        return types.SimpleNamespace(
            status_code=429, text="rate limited",
            json=lambda: {})
    monkeypatch.setattr(ca, "_requests", types.SimpleNamespace(post=post))

    summary, _, _ = _run(usage)
    assert summary == "", "no content came back"
    assert ca._pplx_calls(usage) == 4, "but all four were still paid for"


# --------------------------------------------- but reaching it is not a failure

def test_reaching_the_cap_does_not_raise(monkeypatch):
    """SearchUnavailable here would throw away every mention from the earlier
    races and take the day to NO_BET. A spend guard must not cost the card."""
    calls = []
    _arm(monkeypatch, calls)

    summary, citations, counts = _run(
        {"perplexity_newspaper": ca.DEFAULT_PERPLEXITY_CAP})

    assert summary == ""
    assert citations == 0
    assert counts == {}


# ------------------------------------------------------------------- the override

def test_the_env_override_is_honoured(monkeypatch):
    monkeypatch.setenv("STRIDE_PERPLEXITY_CAP", "2")
    calls = []
    _arm(monkeypatch, calls)

    _run({})
    assert len(calls) == 2


@pytest.mark.parametrize("raw", ["nonsense", "0", "-5", "3.5"])
def test_an_unusable_override_falls_back_loudly(monkeypatch, capsys, raw):
    """Obeying a typo'd cap is worse than ignoring it, because it is silent.
    Zero is in this list deliberately: it reads as 'spend nothing' to a person
    and would be a disable switch to the code, so it is refused and the
    message names STRIDE_SEARCH_OPTIONAL, which is the real way to say that."""
    monkeypatch.setenv("STRIDE_PERPLEXITY_CAP", raw)

    assert ca.perplexity_cap() == ca.DEFAULT_PERPLEXITY_CAP
    assert "STRIDE_PERPLEXITY_CAP" in capsys.readouterr().err


def test_the_default_clears_the_largest_card_measured(monkeypatch):
    """55 races x 4 queries = 220, the Saturday of 2026-09-05. A default that
    throttled a normal Saturday would be found the hard way, on a Saturday."""
    assert ca.DEFAULT_PERPLEXITY_CAP >= 55 * 4
