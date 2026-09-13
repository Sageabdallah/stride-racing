"""artifacts.py, db.py and pf.py without a network."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from chat.artifacts import ArtifactMissing, ArtifactStore, ArtifactUnavailable, tips_path
from chat.db import Database, DatabaseUnavailable, relation_missing
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
