"""A response cut off at max_tokens was invisible, and retried identically.

Every provider already detected truncation — Groq on finish_reason "length",
Anthropic on stop_reason "max_tokens" — and spent the finding on a log line
nothing read. generate_json then retried at the same ceiling, which for a
deterministic failure means paying twice to fail the same way, and returned
an empty dict without a word. score_race_horses turned that empty dict into
`return horses`, silently, so a race with no ai_score looked exactly like a
race the LLM had never been asked about.

That is the whole reason 0 of 114 picks carried an ai_score on 2026-09-05:
2500 tokens for six horses of JSON prose is on the boundary, and the boundary
is where it sat every single race.

These tests pin the budget arithmetic, the escalation, and the loudness.
"""

import logging
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_post_scorer
import llm_provider


class _Provider(llm_provider.LLMProvider):
    """A provider whose bodies and truncation flags the test dictates."""

    def __init__(self, bodies, truncated):
        super().__init__("test-model")
        self.bodies = list(bodies)
        self.truncated = list(truncated)
        self.budgets = []

    def generate(self, prompt, system=None, temperature=0.3, max_tokens=1024):
        self.budgets.append(max_tokens)
        self.last_truncated = self.truncated.pop(0)
        return self.bodies.pop(0)


def test_a_truncated_body_is_retried_with_a_bigger_ceiling():
    """The retry that mattered. Same prompt, same ceiling, same cut — the only
    retry that can succeed is one with room to finish."""
    p = _Provider(bodies=['{"horses": [{"ai_sc', '{"horses": [{"ai_score": 80}]}'],
                  truncated=[True, False])
    result = p.generate_json("score this race", max_tokens=2500)
    assert result == {"horses": [{"ai_score": 80}]}
    assert p.budgets == [2500, 5000], "the second attempt must raise the ceiling"


def test_a_parse_failure_that_was_not_truncation_does_not_raise_the_ceiling():
    """Malformed JSON from a model that finished is a different fault, and
    throwing budget at it would hide it rather than fix it."""
    p = _Provider(bodies=["not json at all", "still not json"],
                  truncated=[False, False])
    assert p.generate_json("go", max_tokens=2500) == {}
    assert p.budgets == [2500, 2500]


def test_escalation_stops_at_the_ceiling():
    start = llm_provider.TRUNCATION_RETRY_CEILING
    p = _Provider(bodies=["{cut", "{cut"], truncated=[True, True])
    assert p.generate_json("go", max_tokens=start) == {}
    assert p.budgets == [start, start], "already at the ceiling; nowhere to escalate"


def test_the_escalated_ceiling_is_never_exceeded():
    below = llm_provider.TRUNCATION_RETRY_CEILING - 1
    p = _Provider(bodies=["{cut", "{cut"], truncated=[True, True])
    p.generate_json("go", max_tokens=below)
    assert p.budgets[1] == llm_provider.TRUNCATION_RETRY_CEILING


def test_giving_up_on_a_truncated_body_says_so(caplog):
    """Returning {} quietly is how this survived. The caller's own log line
    then reads identically to 'the LLM was switched off'."""
    p = _Provider(bodies=["{cut", "{cut"], truncated=[True, True])
    with caplog.at_level(logging.WARNING, logger="llm_provider"):
        assert p.generate_json("go", max_tokens=2500) == {}
    assert any("truncated" in r.message or "cut off" in r.message
               for r in caplog.records), caplog.text


# --- the flag each provider must actually set -------------------------------

def test_groq_reports_truncation_from_finish_reason(monkeypatch):
    class _Msg:
        content = "  body  "

    class _Choice:
        finish_reason = "length"
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    llm = llm_provider.GroqProvider.__new__(llm_provider.GroqProvider)
    llm_provider.LLMProvider.__init__(llm, "m")
    llm._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **k: _Resp())))
    monkeypatch.setattr(llm_provider, "GROQ_MIN_DELAY_SECONDS", 0)
    assert llm.generate("go") == "body"
    assert llm.last_truncated is True

    _Choice.finish_reason = "stop"
    llm.generate("go")
    assert llm.last_truncated is False, "the flag must not survive the next call"


def test_anthropic_reports_truncation_from_stop_reason():
    llm = llm_provider.AnthropicProvider.__new__(llm_provider.AnthropicProvider)
    llm_provider.LLMProvider.__init__(llm, "claude-sonnet-5")
    resp = types.SimpleNamespace(
        stop_reason="max_tokens",
        content=[types.SimpleNamespace(type="text", text="THE FORM: second at Rose")])
    llm._client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **k: resp))
    assert llm.generate("go").startswith("THE FORM")
    assert llm.last_truncated is True

    resp.stop_reason = "end_turn"
    llm.generate("go")
    assert llm.last_truncated is False


def test_ollama_reports_truncation_from_done_reason(monkeypatch):
    body = {"response": "cut", "done_reason": "length"}
    fake = types.ModuleType("requests")
    fake.ConnectionError = ConnectionError
    fake.post = lambda *a, **k: types.SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: body)
    monkeypatch.setitem(sys.modules, "requests", fake)

    llm = llm_provider.OllamaProvider()
    assert llm.generate("go") == "cut"
    assert llm.last_truncated is True

    body["done_reason"] = "stop"
    llm.generate("go")
    assert llm.last_truncated is False


# --- the budgets the callers ask for ----------------------------------------

class _Recorder:
    def __init__(self, result=None, text="insight", truncated=False):
        self.result = result if result is not None else {}
        self.text = text
        self.last_truncated = truncated
        self.budgets = []

    def generate_json(self, prompt, system=None, temperature=0.2,
                      max_tokens=1024, retries=1):
        self.budgets.append(max_tokens)
        return self.result

    def generate(self, prompt, system=None, temperature=0.3, max_tokens=1024):
        self.budgets.append(max_tokens)
        return self.text


@pytest.fixture
def rec(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr(llm_post_scorer, "get_provider", lambda *a, **k: r)
    return r


def _field(n):
    return [{"horse": f"H{i}", "selectionScore": 70.0 - i, "odds": 5.0 + i,
             "winPercentage": 0.15} for i in range(n)]


def test_the_scorer_budget_scales_with_the_field_and_clears_the_old_2500(rec):
    """2500 was roughly what six horses of JSON prose need. Sitting on the
    requirement is why it failed every race rather than some of them."""
    llm_post_scorer.score_race_horses(
        horses=_field(6), track="Randwick", race_number=3, race_name="T",
        distance="1200m", going="Good", race_class="BM70", all_horses=_field(6))
    assert rec.budgets == [4400]
    assert rec.budgets[0] > 2500

    rec.budgets.clear()
    llm_post_scorer.score_race_horses(
        horses=_field(3), track="Randwick", race_number=4, race_name="T",
        distance="1200m", going="Good", race_class="BM70", all_horses=_field(3))
    assert rec.budgets == [2600], "a smaller ask gets a smaller ceiling"


def test_a_race_the_scorer_could_not_score_is_logged_not_swallowed(rec, caplog):
    """`return horses` on an empty result is indistinguishable from the LLM
    being switched off — which is how 0 of 114 went unnoticed for a week."""
    with caplog.at_level(logging.WARNING, logger="llm_post_scorer"):
        out = llm_post_scorer.score_race_horses(
            horses=_field(6), track="Randwick", race_number=3, race_name="T",
            distance="1200m", going="Good", race_class="BM70",
            all_horses=_field(6))
    assert not any("ai_score" in h for h in out)
    msg = caplog.text
    assert "Randwick" in msg and "4400" in msg and "no ai_score" in msg


def test_a_parsed_result_with_no_horses_array_is_its_own_message(rec, caplog):
    rec.result = {"tip_type": "win", "winner": "H0"}
    with caplog.at_level(logging.WARNING, logger="llm_post_scorer"):
        llm_post_scorer.score_race_horses(
            horses=_field(6), track="Flemington", race_number=1, race_name="T",
            distance="1200m", going="Good", race_class="BM70",
            all_horses=_field(6))
    assert "no horses array" in caplog.text


def test_brief_assessments_scale_with_the_number_of_horses_asked_about(rec):
    """One call covers every non-tipped runner, so a fixed ceiling shrinks per
    horse as the field grows. It failed 13 of 38 races that way."""
    rec.result = {"assessments": {"H4": "no"}}
    llm_post_scorer.generate_brief_assessments(
        non_tipped_horses=_field(11), tipped_horses=_field(3),
        track="Randwick", distance="1200m", going="Good", race_class="BM70")
    assert rec.budgets == [3350]

    rec.budgets.clear()
    llm_post_scorer.generate_brief_assessments(
        non_tipped_horses=_field(4), tipped_horses=_field(3),
        track="Randwick", distance="1200m", going="Good", race_class="BM70")
    assert rec.budgets == [1600]


def test_an_insight_that_ends_mid_sentence_is_flagged(rec, caplog):
    """8 of 114 insights on 2026-09-05 stopped mid-word and were published
    that way. The text is still worth returning; the silence was not."""
    rec.last_truncated = True
    rec.text = "THE FORM: second at Rosehill in May, then a close third at Ran"
    with caplog.at_level(logging.WARNING, logger="llm_post_scorer"):
        out = llm_post_scorer.generate_rich_insight(
            horse={"horse": "Fastnet Rock"}, track="Randwick", race_number=7,
            distance="1200m", going="Good", race_class="BM70")
    assert out.endswith("Ran"), "a truncated insight still beats none"
    assert "Fastnet Rock" in caplog.text and "mid-sentence" in caplog.text


def test_a_complete_insight_is_not_flagged(rec, caplog):
    rec.last_truncated = False
    with caplog.at_level(logging.WARNING, logger="llm_post_scorer"):
        llm_post_scorer.generate_rich_insight(
            horse={"horse": "Fastnet Rock"}, track="Randwick", race_number=7,
            distance="1200m", going="Good", race_class="BM70")
    assert "mid-sentence" not in caplog.text
