"""The web-research leg gets a cheap standalone check, like the panel already had.

Perplexity billing has now taken the consensus pillar down twice: the
2026-09-02..09 outage (issue #176) and again on 2026-09-16, when the account
answered three races and was refused at the fourth. Both were read off an SNS
alert after the card was gone, for a structural reason rather than a careless
one — nothing in the system could ask "does Perplexity answer?" without
running the real card, which scores every race, writes consensus_mentions and
publishes the day's file.

`--search-only` closes that. One query, no racecard, no database, no file.

These pin the two things that make it worth having: the exit codes separate
faults whose repairs differ (7 = key or credit, 1 = anything else), and a 200
is never on its own taken as proof, because a 200 carrying no content is
exactly what a status-only check would still pass with.

Zero network: requests is stubbed.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("requests", types.ModuleType("requests"))

import consensus_agent as ca  # noqa: E402

HANDLER_PATH = (Path(__file__).resolve().parents[3] / "infra" / "jobs" / "handler.py")


class FakeResponse:
    def __init__(self, status, payload=None, text="{}"):
        self.status_code = status
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


OK_PAYLOAD = {"choices": [{"message": {"content": "Racenet previewed it."}}],
              "citations": ["https://racenet.com.au"]}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """A key present by default, the optional flag off, and every write path
    armed to fail. The probe must not reach any of them."""
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    monkeypatch.delenv(ca.SEARCH_OPTIONAL_ENV, raising=False)
    for name in ("_write_output", "_save_usage"):
        monkeypatch.setattr(
            ca, name,
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError(f"{name} must not run in --search-only")))


def _arm(monkeypatch, response=None, raises=None, box=None):
    """Point the probe at a server that answers `response`, or explodes."""
    def post(*a, **k):
        if box is not None:
            box.update(k.get("json", {}))
            box["url"] = a[0] if a else k.get("url")
        if raises is not None:
            raise raises
        return response
    monkeypatch.setattr(ca, "_requests", types.SimpleNamespace(post=post))


def _read(capsys):
    """Markers and stderr from ONE drain — readouterr() empties the buffer,
    so a second call in the same test sees nothing."""
    cap = capsys.readouterr()
    markers = dict(line.split(maxsplit=1) for line in cap.out.splitlines()
                   if line.startswith("SEARCH_"))
    return markers, cap.err


def _markers(capsys):
    return _read(capsys)[0]


# --------------------------------------------- 7 is the leg dark, and only that

def test_an_absent_key_is_exit_7_and_names_secrets_delivery(monkeypatch, capsys):
    """An absent key never makes an HTTP call, so it never sees a 401. It is
    still the commonest fault and it is a DIFFERENT repair from a spent
    balance: the key never reached the task."""
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    _arm(monkeypatch, raises=AssertionError("must not call the provider"))

    assert ca.run_search_only("2026-09-16") == 7
    m, err = _read(capsys)
    assert m == {"SEARCH_KEY_PRESENT": "0", "SEARCH_HTTP": "none",
                 "SEARCH_ANSWERED": "0"}
    assert "secrets-delivery fault" in err


@pytest.mark.parametrize("status", [401, 402, 403])
def test_auth_and_billing_refusals_are_exit_7(monkeypatch, capsys, status):
    """The statuses Perplexity uses for a dead key and a spent balance. Same
    code the real run exits, so one number means one thing."""
    _arm(monkeypatch, FakeResponse(status, text='{"error":"insufficient_quota"}'))

    assert ca.run_search_only("2026-09-16") == 7
    m = _markers(capsys)
    assert m["SEARCH_KEY_PRESENT"] == "1"
    assert m["SEARCH_HTTP"] == str(status)
    assert m["SEARCH_ANSWERED"] == "0"


@pytest.mark.parametrize("status", [400, 429, 500, 503])
def test_every_other_refusal_is_exit_1_not_7(monkeypatch, capsys, status):
    """The split earns its keep here. 429 is a throttle and 400 is usually a
    retired model id; topping up the account fixes neither, so they must not
    arrive wearing the code that says 'go and pay someone'."""
    _arm(monkeypatch, FakeResponse(status))

    assert ca.run_search_only("2026-09-16") == 1
    assert _markers(capsys)["SEARCH_HTTP"] == str(status)


def test_a_transport_failure_is_exit_1_and_reports_no_status(monkeypatch, capsys):
    """Nothing was refused — the call never reached an answer. Reporting a
    status here would invent one."""
    _arm(monkeypatch, raises=OSError("connection reset"))

    assert ca.run_search_only("2026-09-16") == 1
    m = _markers(capsys)
    assert m["SEARCH_HTTP"] == "none"
    assert m["SEARCH_ANSWERED"] == "0"


# ------------------------------------------------- a 200 is not by itself proof

def test_a_healthy_answer_is_exit_0(monkeypatch, capsys):
    _arm(monkeypatch, FakeResponse(200, OK_PAYLOAD))

    assert ca.run_search_only("2026-09-16") == 0
    assert _markers(capsys)["SEARCH_ANSWERED"] == "1"


def test_a_200_carrying_no_content_fails(monkeypatch, capsys):
    """The teeth of this file. An empty 200 reaches the run as an empty
    summary and yields no mentions for the race, which is the outage this
    job exists to see coming — and it is the exact case a check written
    against response.status_code would report as healthy."""
    empty = {"choices": [{"message": {"content": "   "}}]}
    _arm(monkeypatch, FakeResponse(200, empty))

    assert ca.run_search_only("2026-09-16") == 1
    m = _markers(capsys)
    assert m["SEARCH_HTTP"] == "200"
    assert m["SEARCH_ANSWERED"] == "0"


def test_a_200_whose_body_is_the_wrong_shape_fails(monkeypatch, capsys):
    """Indexed the way the research leg indexes it, so a body that would
    break the real run breaks this too instead of passing ahead of it."""
    _arm(monkeypatch, FakeResponse(200, {"unexpected": True}))

    assert ca.run_search_only("2026-09-16") == 1
    assert _markers(capsys)["SEARCH_ANSWERED"] == "0"


# ----------------------------------------------------- the probe reports, never obeys

def test_the_optional_flag_does_not_turn_a_refusal_green(monkeypatch, capsys):
    """STRIDE_SEARCH_OPTIONAL lets the REAL run continue panel-only. A check
    that looked for a dark leg and went green because someone had declared a
    dark leg acceptable would report the absence of an opinion as the absence
    of a fault."""
    monkeypatch.setenv(ca.SEARCH_OPTIONAL_ENV, "true")
    _arm(monkeypatch, FakeResponse(402))

    assert ca.run_search_only("2026-09-16") == 7
    assert _markers(capsys)["SEARCH_ANSWERED"] == "0"


def test_the_probe_sends_the_model_id_production_sends(monkeypatch, capsys):
    """Anti-drift. A probe that proves a different model id from the one the
    research queries send proves nothing about them, and a retired model id
    is one of the faults a 4xx can mean."""
    probe, research = {}, {}

    _arm(monkeypatch, FakeResponse(200, OK_PAYLOAD), box=probe)
    assert ca.run_search_only("2026-09-16") == 0

    # Released for the production call only. That _save_usage fires here and
    # not above is itself the difference being relied on: the research query
    # keeps a tally, the probe keeps nothing.
    monkeypatch.setattr(ca, "_save_usage", lambda *a, **k: None)
    monkeypatch.setattr(ca.time, "sleep", lambda *_: None)
    _arm(monkeypatch, FakeResponse(200, OK_PAYLOAD), box=research)
    ca.search_race_tips_perplexity_multi(
        "Randwick", 1, "Race 1", 1200, "BM78", [{"horse": "A"}], ["A"],
        "2026-09-16", "Wednesday 16 September 2026", {})

    assert probe["model"] == research["model"] == ca.PPLX_MODEL
    assert probe["url"] == research["url"]


def test_the_probe_touches_no_racecard_and_no_database(monkeypatch, capsys):
    """The property that makes it safe beside a live job: it is the one
    consensus entry point that needs neither, so it can be run on a race
    morning without spending or contaminating the card."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        ca, "load_racecard_meetings",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("--search-only must not read a racecard")))
    _arm(monkeypatch, FakeResponse(200, OK_PAYLOAD))

    assert ca.run_search_only("2026-09-16") == 0


# ------------------------------------------------------- the handler's backstop

@pytest.fixture(scope="module")
def handler():
    class _StubBoto3:
        def __getattr__(self, name):
            raise AssertionError(f"boto3.{name} must not be called in this test")
    sys.modules.setdefault("boto3", _StubBoto3())
    spec = importlib.util.spec_from_file_location("stride_handler", HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["stride_handler"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_search_proof_is_dispatchable(handler):
    assert handler.JOBS["search-proof"] is handler.job_search_proof


def test_exit_0_without_the_marker_still_fails(handler, monkeypatch):
    """The class, not an instance. An exit code is a claim about a run; the
    marker is the only line here that saw the provider. A future early return
    that exits 0 having called nothing must not read as a green leg."""
    monkeypatch.setattr(handler, "_run_ok", lambda *a, **k: "nothing to see\n")

    with pytest.raises(RuntimeError, match="SEARCH_ANSWERED 1"):
        handler.job_search_proof()


def test_a_green_run_reports_the_status_it_saw(handler, monkeypatch):
    monkeypatch.setattr(handler, "_run_ok",
                        lambda *a, **k: "SEARCH_KEY_PRESENT 1\nSEARCH_HTTP 200\n"
                                        "SEARCH_ANSWERED 1\n")

    got = handler.job_search_proof()
    assert got["search_answered"] is True
    assert got["http_status"] == "200"
