"""artifacts.py, db.py and pf.py without a network."""

from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

import pytest

from chat.artifacts import ArtifactMissing, ArtifactStore, ArtifactUnavailable, tips_path
from chat.db import REDACTED, Database, DatabaseUnavailable, redact, relation_missing
from chat.pf import PuntingForm, PuntingFormOutsideWindow, PuntingFormUnavailable, outside_window
from chat.tests.conftest import FakePFClient, pf_meeting
import pf_client


class FakeS3:
    def __init__(self, objects):
        self.objects = objects
        self.gets = []

    def get_object(self, Bucket, Key):
        self.gets.append((Bucket, Key))
        if Key not in self.objects:
            raise Exception("An error occurred (NoSuchKey) when calling GetObject: Not Found")
        body = SimpleNamespace(read=lambda: self.objects[Key].encode())
        return {"Body": body}


def test_artifacts_local_only_when_no_bucket(tmp_path):
    (tmp_path / "racecards").mkdir()
    (tmp_path / "racecards" / "tips_2026-04-12.json").write_text(json.dumps({"races": []}))
    store = ArtifactStore(bucket=None, local_root=str(tmp_path))
    data, source = store.get_json(tips_path("2026-04-12"))
    assert data == {"races": []} and source == "local:racecards/tips_2026-04-12.json"
    with pytest.raises(ArtifactMissing):
        store.get_json(tips_path("2026-04-13"))
    assert "no artifact bucket" in store.describe()


def test_artifacts_prefer_s3_then_local_and_cache(tmp_path):
    (tmp_path / "racecards").mkdir()
    (tmp_path / "racecards" / "tips_2026-04-12.json").write_text(json.dumps({"stale": True}))
    (tmp_path / "racecards" / "tips_2026-04-11.json").write_text(json.dumps({"local": True}))
    s3 = FakeS3({"artifacts/racecards/tips_2026-04-12.json": json.dumps({"fresh": True})})
    store = ArtifactStore(bucket="b", local_root=str(tmp_path))
    store._client = s3
    data, source = store.get_json(tips_path("2026-04-12"))
    assert data == {"fresh": True} and source == "s3:artifacts/racecards/tips_2026-04-12.json"
    data, source = store.get_json(tips_path("2026-04-11"))
    assert data == {"local": True} and source.startswith("local:")
    store.get_json(tips_path("2026-04-12"))
    assert len(s3.gets) == 2, "the second read of the same key is served from the cache"


def test_artifacts_relay_failure_is_loud(tmp_path):
    class Broken:
        def get_object(self, Bucket, Key):
            raise Exception("AccessDenied: no credential")
    store = ArtifactStore(bucket="b", local_root=str(tmp_path))
    store._client = Broken()
    with pytest.raises(ArtifactUnavailable):
        store.get_json(tips_path("2026-04-12"))


def _driver_that_echoes_the_dsn(monkeypatch):
    """psycopg2 whose connect() fails the way libpq does on a string it cannot
    parse: by quoting the whole connection string back, password included."""
    fake = types.ModuleType("psycopg2")

    def connect(dsn, **kwargs):
        raise ValueError(f'invalid dsn: missing "=" after "{dsn}" in connection info string')

    fake.connect = connect
    fake.extras = types.ModuleType("psycopg2.extras")
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    monkeypatch.setitem(sys.modules, "psycopg2.extras", fake.extras)


def test_connect_failure_never_carries_the_password(monkeypatch):
    """db.py interpolated the driver's error straight into DatabaseUnavailable.
    For a URL libpq cannot parse -- a stray quote around a Windows paste is
    enough -- that error quotes the entire connection string, and the text
    goes into the tool result and the stderr log."""
    _driver_that_echoes_the_dsn(monkeypatch)
    url = '"postgresql://stride_chat_ro:s3cr3t%40pw@ep-x-pooler.neon.tech/neondb"'
    db = Database(url=url)
    with pytest.raises(DatabaseUnavailable) as e:
        db.query("SELECT 1")
    text = str(e.value)
    assert "s3cr3t" not in text, text
    assert "connect failed" in text and "invalid dsn" in text, "the reason must survive"
    # No chained cause either: a traceback prints __cause__ and __context__,
    # and the raw driver error is the one object here that holds the password.
    assert e.value.__cause__ is None
    assert e.value.__suppress_context__


@pytest.mark.parametrize("form", [
    "s3cr3t%40pw",          # as written in the URL
    "s3cr3t@pw",            # URL-decoded, as a driver might print it
])
def test_redact_masks_every_form_of_the_password(form):
    url = "postgresql://stride_chat_ro:s3cr3t%40pw@host/db"
    assert "s3cr3t" not in redact(f"failed for {form} today", url)
    assert REDACTED in redact(f"failed for {form} today", url)
    # The whole URL is masked as a unit, and text without a URL is untouched.
    assert redact(f"invalid dsn: {url}", url) == "invalid dsn: <STRIDE_CHAT_DATABASE_URL>"
    assert redact("nothing to hide", url) == "nothing to hide"
    assert redact("anything", None) == "anything"


def test_redact_masks_a_password_that_contains_a_raw_at_sign():
    """An operator paste with an unencoded "@" in the password. libpq splits
    the authority at its first "@", so it reads the rest of the password as
    the host and echoes exactly that in "could not translate host name". The
    regex used to stop at the first "@" too, so that tail went out unmasked
    -- and the captured head could be a character or two, masking the wrong
    thing ("p" turned "password authentication" into "***assword")."""
    url = "postgresql://stride_chat_ro:Tr0ub4dor@h0rse@ep-x-pooler.neon.tech/neondb"
    msg = 'could not translate host name "h0rse@ep-x-pooler.neon.tech" to address'
    out = redact(msg, url)
    assert "h0rse" not in out and "Tr0ub4dor" not in out, out
    assert "could not translate host name" in out, "the reason must survive"
    assert "Tr0ub4dor" not in redact("failed for Tr0ub4dor@h0rse today", url)
    # A short head is no longer a secret on its own.
    short = "postgresql://u:p@ss@host/db"
    assert redact("password authentication failed", short) == "password authentication failed"
    assert "ss@host" not in redact('host name "ss@host"', short)


def test_database_repr_does_not_show_the_url():
    """Context.db holds this object; a print(ctx), an f-string or pytest's
    assertion introspection would otherwise print the credential."""
    db = Database(url="postgresql://stride_chat_ro:s3cr3t@host/db")
    assert "s3cr3t" not in repr(db) and "s3cr3t" not in str(db)
    assert "postgresql" not in repr(db)


def test_database_without_url_and_relation_missing():
    db = Database(url=None)
    assert not db.configured
    with pytest.raises(DatabaseUnavailable):
        db.query("SELECT 1")
    err = SimpleNamespace(pgcode="42P01")
    assert relation_missing(err)  # type: ignore[arg-type]
    assert relation_missing(RuntimeError('relation "blackbook_entries" does not exist'))
    assert not relation_missing(RuntimeError("permission denied"))


def test_pf_window_and_errors():
    assert outside_window("2026-08-01", today="2026-09-10")
    assert not outside_window("2026-08-20", today="2026-09-10")
    client = FakePFClient(meetings={"2026-09-10": [pf_meeting(1, "Royal Randwick")]})
    pf = PuntingForm(client=client, today="2026-09-10")
    with pytest.raises(PuntingFormOutsideWindow):
        pf.meetings_for_date("2026-07-01")
    assert client.calls == [], "no call is spent on a date the plan cannot serve"
    with pytest.raises(PuntingFormOutsideWindow):
        pf.meetings_for_date("2026-09-01")  # inside the window but the fake 400s: measured wall
    client.raise_on = pf_client.PFAuthError("HTTP 403")
    with pytest.raises(PuntingFormUnavailable):
        pf.meetings_for_date("2026-09-10")
    client.raise_on = None
    assert pf.find_meeting("2026-09-10", "Randwick")["meetingId"] == 1
    assert pf.find_meeting("2026-09-10", "Caulfield") is None
    # Two calls for that date: the one that raised (never cached) and the one
    # find_meeting made; the second find_meeting was served from the cache.
    assert sum(1 for c in client.calls if c[0] == "meetings_for_date" and c[1] == ("2026-09-10",)) == 2
