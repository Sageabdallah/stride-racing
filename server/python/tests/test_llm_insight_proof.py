"""llm_insight_proof.py: the two-pick check that insight text is really produced.

The pipeline cannot tell "insights off" from "insights broken": both leave
ai_insight "" with a fresh timestamp and exit 0. The proof script runs the
pipeline's own generate_rich_insight on two fixtures against whatever provider
the environment names, prints the text, and exits 1 when it is empty. These
tests drive it with stub providers so each verdict is pinned without a network.
"""

import json

import pytest

import llm_provider
import llm_insight_proof as proof


def _scored_json(names):
    return json.dumps({
        "horses": [{"horse": n, "ai_score": 70, "analysis": "a", "key_edge": "e",
                    "risk_factors": ["r"], "vs_field": "v"} for n in names],
        "tip_type": "win", "winner": names[0],
        "selection_ranking": list(names), "ranking_reasoning": "because",
    })


class _StubLLM(llm_provider.LLMProvider):
    """Prose for insight calls, JSON for the scoring call.

    The two stages ask for different things, and conflating them is what let a
    green insight proof stand in for "the LLM works" while score_race_horses
    returned nothing for all 114 picks on 2026-09-05.
    """

    def __init__(self, text, json_names=None):
        super().__init__("stub-model")
        self.text = text
        self.json_names = json_names
        self.calls = []

    def _wants_json(self, prompt, system):
        blob = (prompt or "") + " " + (system or "")
        return "JSON" in blob or "json" in blob

    def generate(self, prompt, system=None, temperature=0.3, max_tokens=1024):
        self.calls.append((prompt, system, max_tokens))
        if self._wants_json(prompt, system):
            if self.json_names is None:
                return self.text          # simulates a model that ignores the schema
            return _scored_json(self.json_names)
        return self.text


def _run(monkeypatch, capsys, provider):
    monkeypatch.setattr(llm_provider, "_provider_instance", provider)
    code = proof.main([])
    return code, capsys.readouterr().out


def test_pass_when_both_picks_come_back_with_text(monkeypatch, capsys):
    names = [h["horse"] for h in proof.build_field()[:6]]
    stub = _StubLLM("THE FORM: Second at Rosehill in a BM88 on 15 August, beaten 0.8L.",
                    json_names=names)
    code, out = _run(monkeypatch, capsys, stub)

    assert "LLM_PROOF scored=6/6" in out, "the JSON stage must be proved too"
    assert code == 0
    assert "LLM_PROOF provider=_StubLLM model=stub-model" in out
    assert "LLM_PROOF ping=" in out
    assert out.count("LLM_PROOF pick=") == 2
    assert "LLM_PROOF pick=Ledger_Line chars=" in out
    assert "LLM_PROOF pick=Blank_Docket chars=" in out
    assert "LLM_PROOF result=PASS" in out
    assert "Second at Rosehill" in out, "the insight text itself must be in the log"
    # The picks went through the pipeline's own prompt, not a stand-in.
    import llm_post_scorer
    insight_calls = [c for c in stub.calls if c[1] == llm_post_scorer.INSIGHT_SYSTEM_PROMPT]
    assert len(insight_calls) == 2
    assert all("THE FORM" in system for _, system, _ in insight_calls)
    assert all(max_tokens == 1500 for _, _, max_tokens in insight_calls)
    assert any("Rosehill" in prompt for prompt, _, _ in insight_calls), \
        "the first fixture's recent runs must reach the prompt"


def test_working_insights_do_not_excuse_a_dead_scoring_stage(monkeypatch, capsys):
    """The 2026-09-05 shape exactly: 114/114 insights, 0/114 ai_score.

    json_names=None makes the stub return prose to the JSON request, which is
    what a model does when it ignores the schema or gets truncated mid-object.
    generate_json then returns {} and score_race_horses silently hands back
    unscored horses — so the proof, not the pipeline, has to notice.
    """
    stub = _StubLLM("THE FORM: a real-looking insight.", json_names=None)
    code, out = _run(monkeypatch, capsys, stub)

    assert code == 1
    assert "LLM_PROOF scored=0/6" in out
    assert "score_race_horses returns no ai_score" in out
    assert "30% AI blend" in out, "the message must say what is actually lost"
    # And it must have probed BOTH budgets so truncation is distinguishable
    # from a format problem rather than guessed at.
    assert "probe budget=2500" in out and "probe budget=8000" in out


def test_the_scoring_probe_reports_size_and_parse_outcome(monkeypatch, capsys):
    stub = _StubLLM("not json at all", json_names=None)
    _run(monkeypatch, capsys, stub)
    out = capsys.readouterr().out if False else None
    # captured inside _run; re-run to inspect deterministically
    stub2 = _StubLLM("not json at all", json_names=None)
    code, out = _run(monkeypatch, capsys, stub2)
    for line in out.splitlines():
        if line.startswith("LLM_PROOF probe budget="):
            assert "chars=" in line and "parsed=" in line and "horses=" in line, line


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
