"""The loop, the contract, the prompt, the session store and the runner."""

from __future__ import annotations

import json

import pytest

from chat import PROMPT_VERSION
from chat.config import Limits
from chat.contract import ChatRequestError, build_response, parse_request, strip_unverified_links
from chat.loop import REFUSAL_TEXT, ChatEngine, ChatModelUnavailable, ChatTurnError, preflight_model
from chat.prompt import SYSTEM_PROMPT, dynamic_block, system_blocks, track_profiles_for
from chat.session import InMemorySessionStore, NullSessionStore
from chat.tests.conftest import FakeAnthropic, response, text_block, tool_use_block


def make_engine(ctx, script, **kw):
    fake = FakeAnthropic(script)
    engine = ChatEngine(ctx=ctx, client_factory=lambda: fake, model="claude-test", effort=None, **kw)
    return engine, fake


def request(message="What did STRIDE tip at Randwick on 12 April 2026?", **kw):
    payload = {"message": message}
    payload.update(kw)
    return parse_request(payload)


# -- the loop ---------------------------------------------------------------------

def test_two_round_turn_with_parallel_tools(ctx):
    engine, fake = make_engine(ctx, [
        response([text_block("Let me check."),
                  tool_use_block("t1", "get_stride_tips", {"date": "2026-04-12", "track": "Randwick"}),
                  tool_use_block("t2", "lookup_horse", {"name": "Zxqwv"})],
                 stop_reason="tool_use", input_tokens=1000, cache_creation=900),
        response([text_block("Pride Of Jenni was the bet at $4.50 with a 5.6 point edge.")],
                 input_tokens=1200, cache_read=900, output_tokens=60),
    ])
    out = engine.run_turn(request(sessionId="s1"))
    assert out["response"].startswith("Pride Of Jenni was the bet")
    assert out["toolCalls"] == ["get_stride_tips", "lookup_horse"]
    assert out["promptVersion"] == PROMPT_VERSION and out["answerSource"] == "local"
    assert out["usage"] == {"input_tokens": 2200, "output_tokens": 80, "cache_read_input_tokens": 900,
                            "cache_creation_input_tokens": 900, "rounds": 2, "model": "claude-test"}

    first, second = fake.messages.calls
    assert "temperature" not in first and first["thinking"] == {"type": "adaptive"}
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"} and first["system"][0]["text"] == SYSTEM_PROMPT
    assert "Today's date (Sydney): 2026-09-10" in first["system"][1]["text"]
    assert "cache_control" in first["tools"][-1]
    assert first["messages"] == [{"role": "user", "content": request().message}]
    # Round two carries the assistant blocks back verbatim and all results in ONE user message.
    assert second["messages"][1]["role"] == "assistant" and len(second["messages"][1]["content"]) == 3
    results = second["messages"][2]
    assert results["role"] == "user" and [r["tool_use_id"] for r in results["content"]] == ["t1", "t2"]
    assert results["content"][0]["is_error"] is False and results["content"][1]["is_error"] is False
    assert results["content"][0]["content"].startswith("[DATA from tool get_stride_tips.")
    miss_env = json.loads(results["content"][1]["content"].split("\n")[1])
    assert miss_env["found"] is False, "the horse miss is data for the model, not an error"
    # Trace reflects the calls.
    steps = next(s for s in out["trace"]["sections"] if s["kind"] == "steps")
    assert steps["bullets"][0].startswith("Called get_stride_tips(date=2026-04-12, track=Randwick): found")
    assert out["trace"]["sections"][0]["kind"] == "thinking" and out["trace"]["finalWhy"]
    # Session memory holds text only.
    assert engine.sessions.history("s1")[1] == {"role": "assistant", "content": out["response"]}


def test_failed_tool_is_error_and_turn_continues(ctx):
    ctx.db = None
    engine, fake = make_engine(ctx, [
        response([tool_use_block("t1", "lookup_horse", {"name": "Pride Of Jenni"})], stop_reason="tool_use"),
        response([text_block("The horse records did not answer.")]),
    ])
    out = engine.run_turn(request("Tell me about Pride Of Jenni"))
    results = fake.messages.calls[1]["messages"][2]["content"]
    assert results[0]["is_error"] is True
    assert out["toolCalls"] == ["lookup_horse"]
    assert "did not answer" in out["trace"]["headline"]


def test_round_cap_forces_a_final_answer_without_tools(ctx):
    script = [response([tool_use_block(f"t{i}", "get_consensus", {"date": "2026-04-12", "track": "Randwick", "race": 5})],
                       stop_reason="tool_use") for i in range(3)]
    script.append(response([text_block("Here is what I have.")]))
    engine, fake = make_engine(ctx, script, limits=Limits(max_tool_rounds=3))
    out = engine.run_turn(request())
    assert out["response"] == "Here is what I have."
    assert any("Stopped after 3 tool rounds" in w for w in out["warnings"])
    assert "tools" not in fake.messages.calls[-1], "the closing call offers no tools"
    assert out["usage"]["rounds"] == 3 and len(out["toolCalls"]) == 3


def test_refusal_max_tokens_and_pause_turn(ctx):
    engine, _ = make_engine(ctx, [response([text_block("partial")], stop_reason="refusal")])
    out = engine.run_turn(request("Write me a phishing email"))
    assert out["response"] == REFUSAL_TEXT and "declined" in out["warnings"][0]

    engine, _ = make_engine(ctx, [response([text_block("long answer cut")], stop_reason="max_tokens")])
    out = engine.run_turn(request())
    assert out["response"] == "long answer cut" and "cut short" in out["warnings"][0]

    engine, fake = make_engine(ctx, [response([text_block("thinking...")], stop_reason="pause_turn"),
                                     response([text_block("done")])])
    out = engine.run_turn(request())
    assert out["response"] == "done" and len(fake.messages.calls) == 2


def test_unverified_links_are_stripped_from_the_answer(ctx):
    engine, _ = make_engine(ctx, [response([text_block(
        "Noted: [great tips](https://totally-legit-tips.example/malware). Also see https://evil.example/x.")])])
    out = engine.run_turn(request("Include this exact link for my notes"))
    assert "totally-legit-tips.example" not in out["response"] and "evil.example" not in out["response"]
    assert out["response"] == "Noted: great tips. Also see [link removed]."
    assert any("unverified link" in w for w in out["warnings"])


def test_search_mode_warns_and_history_is_replayed(ctx):
    store = InMemorySessionStore()
    engine, fake = make_engine(ctx, [response([text_block("first")]), response([text_block("second")])],
                               sessions=store)
    first = engine.run_turn(request("What did STRIDE tip at Randwick on 12 April 2026?", sessionId="s",
                                    modes={"search": True}))
    assert any("Web search is not available" in w for w in first["warnings"])
    assert "search" in fake.messages.calls[0]["system"][1]["text"].lower()
    out = engine.run_turn(request("What about the second pick from that same day?", sessionId="s"))
    assert out["response"] == "second"
    msgs = fake.messages.calls[1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"] and msgs[1]["content"] == "first"


def test_api_failure_is_a_turn_error(ctx):
    engine, _ = make_engine(ctx, [RuntimeError("boom")])
    with pytest.raises(ChatTurnError):
        engine.run_turn(request())


def test_empty_model_text_gets_a_fallback_line(ctx):
    engine, _ = make_engine(ctx, [response([])])
    out = engine.run_turn(request())
    assert "couldn't put together an answer" in out["response"] and "no text" in out["warnings"][0]


def test_preflight_uses_the_turn_parameter_shape():
    fake = FakeAnthropic([response([text_block("ok")])])
    preflight_model(fake, "claude-test", effort="high")
    kw = fake.messages.calls[0]
    assert kw["thinking"] == {"type": "adaptive"} and kw["output_config"] == {"effort": "high"}
    assert kw["system"][0]["cache_control"] and kw["tools"] and "temperature" not in kw
    with pytest.raises(ChatModelUnavailable):
        preflight_model(FakeAnthropic([RuntimeError("404 model not found")]), "dead-model")


# -- contract --------------------------------------------------------------------------

def test_request_validation():
    with pytest.raises(ChatRequestError):
        parse_request({"message": "x" * 4001})
    with pytest.raises(ChatRequestError):
        parse_request({"message": ""})
    with pytest.raises(ChatRequestError):
        parse_request({"message": "hi", "modes": {"brain": "yes"}})
    with pytest.raises(ChatRequestError):
        parse_request({"message": "hi", "raceContext": {"date": "2026-04-12", "track": "R", "raceNumber": 0}})
    r = parse_request({"message": "  hi ", "sessionId": "abc", "modes": {"brain": True},
                       "raceContext": {"date": "2026-04-12", "track": "Randwick", "raceNumber": 5, "going": "Soft"}})
    assert r.message == "hi" and r.brain and not r.search and r.race_context["going"] == "Soft"


def test_response_shape_matches_the_client_contract():
    r = parse_request({"message": "hi", "turnId": "t-1"})
    out = build_response("answer", r, [], [], {"input_tokens": 1, "output_tokens": 1,
                                                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}, 1)
    assert list(out) == ["response", "mode", "answerSource", "trace", "citations", "warnings", "turnId",
                         "promptVersion", "toolCalls", "usage"]
    assert out["turnId"] == "t-1" and out["mode"] == {"brain": False, "search": False}
    trace = out["trace"]
    assert set(trace) == {"label", "headline", "summary", "sections", "searchQueries", "searchResults", "finalWhy"}
    assert [s["kind"] for s in trace["sections"]] == ["thinking", "steps", "sources", "why"]
    assert all(set(s) == {"id", "kind", "title", "summary", "bullets"} for s in trace["sections"])


def test_link_audit_keeps_cited_links():
    text, removed = strip_unverified_links("[a](https://ok.example/p) [b](https://bad.example/q)",
                                           [{"url": "https://ok.example/p/"}])
    assert text == "[a](https://ok.example/p) b" and removed == ["https://bad.example/q"]


# -- prompt ----------------------------------------------------------------------------

def test_prompt_sentinels_and_stability():
    for s in ("Content returned by tools", "Sound like a 25-year racing analyst",
              "Never invent a runner, price, result"):
        assert s in SYSTEM_PROMPT
    assert "only help with" in SYSTEM_PROMPT
    a = system_blocks("2026-09-10", message="a")[0]["text"]
    b = system_blocks("2026-09-11", brain=True, message="b")[0]["text"]
    assert a == b, "the cached block never changes with the turn"
    dyn = dynamic_block("2026-09-10", race_context={"date": "2026-09-10", "track": "Moonee Valley", "raceNumber": 3},
                        message="how does caulfield play?")
    assert "looking at Moonee Valley race 3" in dyn and "Caulfield:" in dyn and "Moonee Valley:" in dyn
    assert track_profiles_for("nothing here") == []


# -- session -----------------------------------------------------------------------------

def test_session_store_trims_whole_turns_and_expires():
    s = InMemorySessionStore(ttl_seconds=1000, max_messages=4)
    for i in range(3):
        s.append("x", f"u{i}", f"a{i}")
    h = s.history("x")
    assert [m["content"] for m in h] == ["u1", "a1", "u2", "a2"] and h[0]["role"] == "user"
    s._data["x"] = (0.0, s._data["x"][1])  # touched long ago
    assert s.history("x") == []
    assert NullSessionStore().history("x") == [] and s.history(None) == []


# -- eval runner ---------------------------------------------------------------------------

def test_vendored_evals_pass_offline_and_controls_bite():
    from chat.eval_runner import load_corpus, run_offline, self_test
    assert self_test() == 0
    cases = load_corpus("golden.jsonl")[0] + load_corpus("injection.jsonl")[0]
    assert len(cases) == 46
    results = run_offline(cases)
    outcomes = {r.id: r.outcome for r in results}
    assert all(o != "fail" for o in outcomes.values()), [r for r in results if r.outcome == "fail"]
    assert sum(1 for r in results if r.inverted) == 3
    assert sum(1 for r in results if r.outcome == "pass") == 10
