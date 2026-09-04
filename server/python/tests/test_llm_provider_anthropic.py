"""LLM_PROVIDER=anthropic: Claude as the insight provider.

The pipeline's LLM layer knew two backends, groq and ollama. Every cloud run
since deployment produced empty insights while exiting 0 (2026-09-02: 90 top
picks, 0 with text, 1.1s of total LLM time), the signature of a provider that
is unreachable from inside the container. The Anthropic key already reaches
every task for the consensus agent, so Claude is the provider that can be
relied on there. These tests pin the call shape the consensus agent learned
the hard way (thinking disabled, no sampling parameters) and the error surface
the callers' fallback paths depend on.
"""

import sys
import types

import pytest

import llm_provider


class _Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _Response:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class _Messages:
    def __init__(self, client):
        self._client = client

    def create(self, **kwargs):
        self._client.calls.append(kwargs)
        if self._client.raise_exc is not None:
            raise self._client.raise_exc
        return self._client.response


class _FakeAnthropic:
    instances = []

    def __init__(self, api_key=None, **kwargs):
        self.api_key = api_key
        self.calls = []
        self.raise_exc = None
        self.response = _Response([_Block("text", "THE FORM: second at Rosehill.\n")])
        self.messages = _Messages(self)
        _FakeAnthropic.instances.append(self)


@pytest.fixture
def sdk(monkeypatch):
    module = types.ModuleType("anthropic")
    module.Anthropic = _FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", module)
    _FakeAnthropic.instances.clear()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(llm_provider, "_provider_instance", None)
    return module


def test_get_provider_accepts_anthropic_and_claude(sdk, monkeypatch):
    for name in ("anthropic", "claude", "Anthropic"):
        monkeypatch.setenv("LLM_PROVIDER", name)
        llm = llm_provider.get_provider(force_new=True)
        assert isinstance(llm, llm_provider.AnthropicProvider), name
    assert _FakeAnthropic.instances[-1].api_key == "test-key"


def test_default_model_is_the_consensus_agents_and_llm_model_overrides_it(sdk, monkeypatch):
    assert llm_provider.AnthropicProvider().model == "claude-sonnet-5"
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    assert llm_provider.AnthropicProvider().model == "claude-opus-5"


def test_a_missing_key_is_a_provider_error_not_a_crash(sdk, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(llm_provider.LLMProviderError, match="ANTHROPIC_API_KEY"):
        llm_provider.AnthropicProvider()


def test_generate_disables_thinking_and_sends_no_sampling_parameters(sdk):
    llm = llm_provider.AnthropicProvider()
    text = llm.generate("Write the form.", system="You are the analyst.",
                        temperature=0.4, max_tokens=1500)

    assert text == "THE FORM: second at Rosehill."
    (call,) = _FakeAnthropic.instances[-1].calls
    assert call["model"] == "claude-sonnet-5"
    assert call["max_tokens"] == 1500
    assert call["thinking"] == {"type": "disabled"}, \
        "thinking must be off or it shares the max_tokens budget with the body"
    assert call["system"] == "You are the analyst."
    assert call["messages"] == [{"role": "user", "content": "Write the form."}]
    for rejected in ("temperature", "top_p", "top_k"):
        assert rejected not in call, f"{rejected} is rejected on claude-sonnet-5 and later"
    assert call["timeout"] > 0


def test_generate_without_a_system_prompt_omits_the_field(sdk):
    llm = llm_provider.AnthropicProvider()
    llm.generate("Reply with the single word READY.", max_tokens=16)
    (call,) = _FakeAnthropic.instances[-1].calls
    assert "system" not in call


def test_generate_joins_text_blocks_and_ignores_the_rest(sdk):
    llm = llm_provider.AnthropicProvider()
    client = _FakeAnthropic.instances[-1]
    client.response = _Response([_Block("thinking", "ignored"),
                                 _Block("text", "THE FORM: "),
                                 _Block("text", "won at Randwick.  ")])
    assert llm.generate("x") == "THE FORM: won at Randwick."


def test_an_empty_body_raises_so_the_caller_falls_back(sdk):
    """GroqProvider gets this free: .strip() on a null body raises and becomes
    an LLMProviderError, which llm_post_scorer catches to substitute the
    shorter ai_analysis. Returning "" here instead reaches the SUCCESS path,
    so nothing is logged and the pick is published blank under a fresh
    ai_insight_generated_at — the exact 2026-09-02 row shape."""
    llm = llm_provider.AnthropicProvider()
    client = _FakeAnthropic.instances[-1]

    for content in ([], [_Block("thinking", "only reasoning")], [_Block("text", "   ")]):
        client.response = _Response(content)
        with pytest.raises(llm_provider.LLMProviderError, match="no text"):
            llm.generate("x")


def test_refusals_and_api_errors_surface_as_provider_errors(sdk):
    llm = llm_provider.AnthropicProvider()
    client = _FakeAnthropic.instances[-1]

    client.response = _Response([], stop_reason="refusal")
    with pytest.raises(llm_provider.LLMProviderError, match="refused"):
        llm.generate("x")

    client.raise_exc = RuntimeError("401 invalid x-api-key")
    with pytest.raises(llm_provider.LLMProviderError, match="401"):
        llm.generate("x")


def test_the_insight_generator_runs_end_to_end_on_the_new_provider(sdk, monkeypatch):
    import llm_post_scorer

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    llm_provider.get_provider(force_new=True)
    text = llm_post_scorer.generate_rich_insight(
        horse={"horse": "Ledger Line", "marketOdds": 6.5, "winPercentage": 24.5,
               "recent_runs": [{"track": "Rosehill", "date": "2026-08-15",
                                "distance_m": 1400, "race_class": "BM88",
                                "position": 2, "margin": 0.8, "sp": 9.0}]},
        track="Randwick", race_number=4, distance="1400m", going="Good 4",
        race_class="BM78",
        all_horses=[{"horse": "Market Leader", "marketOdds": 2.8, "winPercentage": 30.1},
                    {"horse": "Ledger Line", "marketOdds": 6.5, "winPercentage": 24.5}],
    )
    assert text == "THE FORM: second at Rosehill."
    (call,) = _FakeAnthropic.instances[-1].calls
    assert "Rosehill" in call["messages"][0]["content"], \
        "the recent-runs history must reach the prompt"
    assert call["system"] == llm_post_scorer.INSIGHT_SYSTEM_PROMPT


def test_an_unknown_provider_names_every_option(monkeypatch):
    monkeypatch.setattr(llm_provider, "_provider_instance", None)
    monkeypatch.setenv("LLM_PROVIDER", "nonsense")
    with pytest.raises(llm_provider.LLMProviderError, match="'ollama', 'groq' or 'anthropic'"):
        llm_provider.get_provider(force_new=True)
