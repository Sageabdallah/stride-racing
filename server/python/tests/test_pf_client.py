"""pf_client.get must name the provider's reason and retry only what can change.

On 2026-09-03 the Punting Form subscription lapsed. The client swallowed the
403 body, retried three times, and reported "HTTP Error 403: Forbidden"; the
actual reason ("You do not have access to this API.") only surfaced from a
separate probe hours later. These tests pin the behaviour that would have
made that a two-minute diagnosis: the provider's error text is carried in
the exception, 401/403 raise PFAuthError without a retry, the wall's HTTP
400 passes through unchanged (pf_window matches on it), and only 429, 5xx
and network faults are retried.
"""

import io
import json
import urllib.error

import pytest

import pf_client
import pf_window


def _http_error(code, body=None, reason="Server Error"):
    raw = json.dumps(body).encode() if body is not None else b"<html>nope</html>"
    return urllib.error.HTTPError(
        "https://api.puntingform.com.au/v2/form/meetingslist", code, reason,
        hdrs=None, fp=io.BytesIO(raw))


class _Resp:
    def __init__(self, body):
        self._raw = json.dumps(body).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


OK = {"statusCode": 200, "status": 0, "error": None, "errors": None,
      "payLoad": [{"meetingId": 1}]}
ACCESS_DENIED = {"statusCode": 403, "status": 4,
                 "error": "You do not have access to this API.",
                 "errors": None, "payLoad": None}
WALL = {"statusCode": 400, "status": 1,
        "error": "Data cannot be accessed before the time 03-08-2026",
        "errors": None, "payLoad": []}


@pytest.fixture(autouse=True)
def _key_and_no_sleep(monkeypatch):
    monkeypatch.setenv("PUNTINGFORM_API_KEY", "test-key")
    monkeypatch.setattr(pf_client.time, "sleep", lambda *_: None)


def _install(monkeypatch, outcomes):
    """Serve `outcomes` in order (the last one repeats); an exception
    instance is raised, anything else is the JSON body of a 200."""
    calls = []

    def fake_urlopen(req, timeout=60):
        calls.append(req.full_url)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return _Resp(outcome)

    monkeypatch.setattr(pf_client.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_returns_payload_and_sends_the_key(monkeypatch):
    calls = _install(monkeypatch, [OK])
    assert pf_client.get("/form/meetingslist", {"meetingDate": "2026-09-03"}) == [{"meetingId": 1}]
    assert len(calls) == 1
    assert "apiKey=test-key" in calls[0]
    assert "meetingDate=2026-09-03" in calls[0]


def test_envelope_error_on_a_200_is_raised_once(monkeypatch):
    calls = _install(monkeypatch, [{"statusCode": 500, "error": "boom", "payLoad": None}])
    with pytest.raises(pf_client.PFError, match="boom"):
        pf_client.get("/form/results", {"meetingId": 1})
    assert len(calls) == 1


@pytest.mark.parametrize("code", [401, 403])
def test_rejected_key_is_an_auth_error_with_the_provider_text_and_no_retry(monkeypatch, code):
    calls = _install(monkeypatch, [_http_error(code, ACCESS_DENIED, "Forbidden")])
    with pytest.raises(pf_client.PFAuthError) as info:
        pf_client.get("/form/meetingslist", {"meetingDate": "2026-09-03"})
    message = str(info.value)
    assert "You do not have access to this API." in message
    assert f"HTTP {code}" in message
    assert "subscription" in message
    assert isinstance(info.value, pf_client.PFError)
    assert len(calls) == 1, "an auth failure must not be retried"


def test_wall_400_passes_the_provider_text_through_and_is_not_retried(monkeypatch):
    calls = _install(monkeypatch, [_http_error(400, WALL, "Bad Request")])
    with pytest.raises(pf_client.PFError) as info:
        pf_client.get("/form/meetingslist", {"meetingDate": "2026-01-01"})
    assert "400" in str(info.value)
    assert "cannot be accessed before" in str(info.value)
    assert not isinstance(info.value, pf_client.PFAuthError)
    assert len(calls) == 1


def test_wall_probe_still_reads_a_400_as_before_the_wall(monkeypatch):
    _install(monkeypatch, [_http_error(400, WALL, "Bad Request")])
    assert pf_window.probe_date("2026-01-01") is False


def test_wall_probe_does_not_mistake_a_lapsed_key_for_the_wall(monkeypatch):
    _install(monkeypatch, [_http_error(403, ACCESS_DENIED, "Forbidden")])
    with pytest.raises(pf_client.PFAuthError):
        pf_window.probe_date("2026-09-03")


def test_404_is_raised_once(monkeypatch):
    calls = _install(monkeypatch, [_http_error(404, None, "Not Found")])
    with pytest.raises(pf_client.PFError, match="HTTP 404"):
        pf_client.get("/form/nothing")
    assert len(calls) == 1


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_transient_statuses_are_retried_then_succeed(monkeypatch, code):
    calls = _install(monkeypatch, [_http_error(code, None), OK])
    assert pf_client.get("/form/meetingslist", {"meetingDate": "2026-09-03"}) == [{"meetingId": 1}]
    assert len(calls) == 2


def test_network_fault_exhausts_the_retry_budget(monkeypatch):
    calls = _install(monkeypatch, [urllib.error.URLError("connection reset")])
    with pytest.raises(pf_client.PFError, match="failed after 3 attempts"):
        pf_client.get("/form/meetingslist", {"meetingDate": "2026-09-03"})
    assert len(calls) == 3


def test_persistent_5xx_reports_the_status_after_the_budget(monkeypatch):
    calls = _install(monkeypatch, [_http_error(503, None, "Service Unavailable")])
    with pytest.raises(pf_client.PFError) as info:
        pf_client.get("/form/meetingslist", {"meetingDate": "2026-09-03"})
    assert "failed after 3 attempts" in str(info.value)
    assert "HTTP 503: Service Unavailable" in str(info.value)
    assert len(calls) == 3


def test_missing_key_is_named_before_any_request(monkeypatch):
    monkeypatch.delenv("PUNTINGFORM_API_KEY")
    calls = _install(monkeypatch, [OK])
    with pytest.raises(pf_client.PFError, match="PUNTINGFORM_API_KEY is not set"):
        pf_client.get("/form/meetingslist", {"meetingDate": "2026-09-03"})
    assert calls == []
