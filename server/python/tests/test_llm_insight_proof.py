"""llm_insight_proof.py: the two-pick check that insight text is really produced.

The pipeline cannot tell "insights off" from "insights broken": both leave
ai_insight "" with a fresh timestamp and exit 0. The proof script runs the
pipeline's own generate_rich_insight on two fixtures against whatever provider
the environment names, prints the text, and exits 1 when it is empty. These
tests drive it with stub providers so each verdict is pinned without a network.
"""

import pytest

import llm_provider
import llm_insight_proof as proof


class _StubLLM(llm_provider.LLMProvider):
    def __init__(self, text):
        super().__init__("stub-model")
        self.text = text
        self.calls = []

    def generate(self, prompt, system=None, temperature=0.3, max_tokens=1024):
        self.calls.append((prompt, system, max_tokens))
        return self.text


def _run(monkeypatch, capsys, provider):
    monkeypatch.setattr(llm_provider, "_provider_instance", provider)
    code = proof.main([])
    return code, capsys.readouterr().out


def test_pass_when_both_picks_come_back_with_text(monkeypatch, capsys):
    stub = _StubLLM("THE FORM: Second at Rosehill in a BM88 on 15 August, beaten 0.8L.")
    code, out = _run(monkeypatch, capsys, stub)

    assert code == 0
    assert "LLM_PROOF provider=_StubLLM model=stub-model" in out
    assert "LLM_PROOF ping=" in out
    assert out.count("LLM_PROOF pick=") == 2
    assert "LLM_PROOF pick=Ledger_Line chars=" in out
    assert "LLM_PROOF pick=Blank_Docket chars=" in out
    assert "LLM_PROOF result=PASS" in out
    assert "Second at Rosehill" in out, "the insight text itself must be in the log"
    # The picks went through the pipeline's own prompt, not a stand-in.
    insight_calls = [c for c in stub.calls if c[1]]
    assert len(insight_calls) == 2
    assert all("THE FORM" in system for _, system, _ in insight_calls)
    assert all(max_tokens == 1500 for _, _, max_tokens in insight_calls)
    assert any("Rosehill" in prompt for prompt, _, _ in insight_calls), \
        "the first fixture's recent runs must reach the prompt"


def test_fail_when_the_provider_returns_empty_text(monkeypatch, capsys):
    code, out = _run(monkeypatch, capsys, _StubLLM(""))
    assert code == 1
    assert "LLM_PROOF result=FAIL" in out
    assert "Ledger_Line" in out and "Blank_Docket" in out
    assert "(empty)" in out


def test_fail_when_no_provider_can_be_built(monkeypatch, capsys):
    monkeypatch.setattr(llm_provider, "_provider_instance", None)
    monkeypatch.setenv("LLM_PROVIDER", "nonsense")
    code = proof.main([])
    out = capsys.readouterr().out
    assert code == 1
    assert "LLM_PROOF provider=UNAVAILABLE" in out
    assert "LLM_PROOF result=FAIL" in out


def test_fail_when_the_one_word_call_errors(monkeypatch, capsys):
    class _Dead(_StubLLM):
        def generate(self, *args, **kwargs):
            raise llm_provider.LLMProviderError(
                "Cannot connect to Ollama at http://localhost:11434")

    code, out = _run(monkeypatch, capsys, _Dead(""))
    assert code == 1
    assert "LLM_PROOF ping=ERROR" in out and "Ollama" in out
    assert "LLM_PROOF pick=" not in out, "no point scoring picks on a dead provider"


def test_the_fixtures_show_both_shapes_the_prompt_meets():
    field = proof.build_field()
    assert len(field) >= 4
    assert field[0]["recent_runs"], "first pick carries history to cite"
    assert not field[1]["recent_runs"], "second pick has none, like a first-starter"
    assert all(h["form"] == "" for h in field), \
        "form is empty in every production pick; the fixtures must not pretend otherwise"
    fav = min(field, key=lambda h: h["marketOdds"])
    assert fav["horse"] not in (field[0]["horse"], field[1]["horse"]), \
        "the favourite must be a rival so WHY HIM has something to argue against"
