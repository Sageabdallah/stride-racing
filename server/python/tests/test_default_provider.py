"""An unset LLM_PROVIDER must land on a provider that answers.

Nobody ever chose Groq. `os.environ.get("LLM_PROVIDER", "groq")` chose it, and
GroqProvider's own default model id — llama-3.3-70b-versatile — was retired by
Groq. The result was a 404 on every call, including a one-word ping: 55 races,
165 picks, 0 insights and 2.1 seconds of total LLM time on 2026-09-05, and the
same shape on 2026-09-02. Nothing in the pipeline could tell that apart from
"the LLM is switched off".

The second half of the bug is that the default existed twice. llm_provider
picked the backend; run_tips_pipeline stamped each row's provenance (v83) from
its own separate literal. Two copies of a default can disagree, and a
provenance field that disagrees with the code is worse than no field.
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_provider
import run_tips_pipeline


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(llm_provider, "_provider_instance", None)


@pytest.fixture
def anthropic_sdk(monkeypatch):
    class _FakeAnthropic:
        def __init__(self, api_key=None, **kw):
            self.api_key = api_key
    module = types.ModuleType("anthropic")
    module.Anthropic = _FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return module


def test_an_unset_provider_resolves_to_anthropic(anthropic_sdk):
    """The whole point: unset is the common case, and it must not be Groq."""
    assert llm_provider.DEFAULT_LLM_PROVIDER == "anthropic"
    llm = llm_provider.get_provider(force_new=True)
    assert isinstance(llm, llm_provider.AnthropicProvider)
    assert llm.model == "claude-sonnet-5"


def test_an_explicit_groq_still_wins(monkeypatch):
    """Changing a default must not remove a choice. A deployment that wants
    Groq says so, and still gets it."""
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setitem(sys.modules, "groq",
                        types.SimpleNamespace(Groq=lambda **kw: object()))
    llm = llm_provider.get_provider(force_new=True)
    assert isinstance(llm, llm_provider.GroqProvider)


def test_provenance_and_the_backend_read_the_same_default(anthropic_sdk):
    """v83 records which provider produced the ai_score. It used to carry its
    own copy of the default, so the two could disagree about what ran."""
    assert run_tips_pipeline._llm_provider_name() == llm_provider.DEFAULT_LLM_PROVIDER
    llm = llm_provider.get_provider(force_new=True)
    assert run_tips_pipeline._llm_provider_name() in type(llm).__name__.lower()


def test_provenance_follows_an_explicit_setting_and_is_normalised(monkeypatch):
    """get_provider lowercases before dispatching, so provenance must too, or
    a row reads 'Anthropic' while another reads 'anthropic'."""
    for raw, want in (("groq", "groq"), ("Anthropic", "anthropic"),
                      ("  ollama  ", "ollama"), ("CLAUDE", "claude")):
        monkeypatch.setenv("LLM_PROVIDER", raw)
        assert run_tips_pipeline._llm_provider_name() == want


def test_the_retired_groq_model_id_is_left_alone_and_labelled():
    """Not silently swapped for a guess. The id stays until someone sets a
    live one deliberately; the comment above it records why."""
    assert llm_provider.DEFAULT_GROQ_MODEL == "llama-3.3-70b-versatile"
    src = Path(llm_provider.__file__).read_text()
    head = src[:src.index("DEFAULT_GROQ_MODEL")]
    assert "404" in head and "2026-09-05" in head, \
        "the dead model id needs the finding recorded beside it"


def test_an_unknown_provider_is_still_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gpt5")
    with pytest.raises(llm_provider.LLMProviderError, match="Unknown LLM_PROVIDER"):
        llm_provider.get_provider(force_new=True)
