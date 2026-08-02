"""Gate-3 evidence store: local cache semantics, strict day counting, and
loud-vs-quiet failure asymmetry (put never raises, list raises when the
configured bucket is unreadable)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evidence_store


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    monkeypatch.delenv("STRIDE_EVIDENCE_BUCKET", raising=False)
    monkeypatch.setattr(evidence_store, "local_dir", lambda: tmp_path)
    return tmp_path


class TestLocalMode:
    def test_put_then_fetch_roundtrip(self, local_store):
        out = evidence_store.put_evidence("serve_liveness_shadow_2026-08-02.json",
                                          '[{"track": "T"}]')
        assert out["local"] and out["s3"] is None and out["s3_error"] is None
        assert evidence_store.fetch_evidence(
            "serve_liveness_shadow_2026-08-02.json") == '[{"track": "T"}]'

    def test_fetch_absent_is_none(self, local_store):
        assert evidence_store.fetch_evidence("nope.json") is None

    def test_day_count_is_strict_dates_only(self, local_store):
        for name in ("serve_liveness_shadow_2026-08-02.json",
                     "serve_liveness_shadow_2026-08-03.json",
                     # None of these may inflate the day count:
                     "serve_liveness_shadow_summary.json",
                     "serve_liveness_shadow_2026-08-03.json.bak",
                     "calibrator_shadow_2026-08-02.json",
                     "calibrator_compare_pooled.json",
                     "bsp_raw_2026-08-02.csv"):
            (local_store / name).write_text("{}")
        assert evidence_store.list_evidence_dates("serve_liveness_shadow") == [
            "2026-08-02", "2026-08-03"]
        assert evidence_store.list_evidence_dates("calibrator_shadow") == [
            "2026-08-02"]


class _FakeS3:
    def __init__(self, fail_list=False, fail_put=False):
        self.objects = {}
        self.fail_list = fail_list
        self.fail_put = fail_put

    def put_object(self, Bucket, Key, Body, ContentType=None):
        if self.fail_put:
            raise RuntimeError("s3 down")
        self.objects[Key] = Body

    def get_paginator(self, name):
        fake = self

        class P:
            def paginate(self, Bucket, Prefix):
                if fake.fail_list:
                    raise RuntimeError("s3 down")
                keys = [k for k in fake.objects if k.startswith(Prefix)]
                yield {"Contents": [{"Key": k} for k in keys]}
        return P()


class TestS3Mode:
    def test_put_mirrors_to_s3_and_list_unions(self, tmp_path, monkeypatch):
        fake = _FakeS3()
        monkeypatch.setenv("STRIDE_EVIDENCE_BUCKET", "b")
        monkeypatch.setattr(evidence_store, "local_dir", lambda: tmp_path)
        monkeypatch.setattr(evidence_store, "_s3_client", lambda: fake)
        out = evidence_store.put_evidence("calibrator_shadow_2026-08-04.json", "{}")
        assert out["s3"] == "s3://b/evidence/calibrator_shadow_2026-08-04.json"
        # A date present only in S3 (written by another box) still counts.
        fake.objects["evidence/calibrator_shadow_2026-08-05.json"] = b"{}"
        assert evidence_store.list_evidence_dates("calibrator_shadow") == [
            "2026-08-04", "2026-08-05"]

    def test_put_failure_is_reported_not_raised(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STRIDE_EVIDENCE_BUCKET", "b")
        monkeypatch.setattr(evidence_store, "local_dir", lambda: tmp_path)
        monkeypatch.setattr(evidence_store, "_s3_client",
                            lambda: _FakeS3(fail_put=True))
        out = evidence_store.put_evidence("x_2026-08-04.json", "{}")
        assert out["local"] and "s3 down" in out["s3_error"]

    def test_list_failure_raises_loud(self, tmp_path, monkeypatch):
        # A counter that silently reads zero is the defect this replaces.
        monkeypatch.setenv("STRIDE_EVIDENCE_BUCKET", "b")
        monkeypatch.setattr(evidence_store, "local_dir", lambda: tmp_path)
        monkeypatch.setattr(evidence_store, "_s3_client",
                            lambda: _FakeS3(fail_list=True))
        with pytest.raises(evidence_store.EvidenceStoreError):
            evidence_store.list_evidence_dates("calibrator_shadow")


class _FakeSNS:
    def __init__(self, fail=False):
        self.published = []
        self.fail = fail

    def publish(self, TopicArn, Subject, Message):
        if self.fail:
            raise RuntimeError("sns down")
        self.published.append((TopicArn, Subject, Message))


class TestWriteFailureAlerts:
    def test_s3_put_failure_publishes_to_sns(self, tmp_path, monkeypatch):
        sns = _FakeSNS()
        monkeypatch.setenv("STRIDE_EVIDENCE_BUCKET", "b")
        monkeypatch.setenv("STRIDE_ALERT_TOPIC_ARN", "arn:sns:stride-alerts")
        monkeypatch.setattr(evidence_store, "local_dir", lambda: tmp_path)
        monkeypatch.setattr(evidence_store, "_s3_client",
                            lambda: _FakeS3(fail_put=True))
        monkeypatch.setattr(evidence_store, "_sns_client", lambda: sns)
        out = evidence_store.put_evidence("x_2026-08-04.json", "{}")
        assert out["s3_error"]
        ((arn, subject, message),) = sns.published
        assert arn == "arn:sns:stride-alerts"
        assert "x_2026-08-04.json" in message and "5-day count" in message

    def test_no_topic_configured_means_no_publish_attempt(self, tmp_path,
                                                          monkeypatch):
        monkeypatch.setenv("STRIDE_EVIDENCE_BUCKET", "b")
        monkeypatch.delenv("STRIDE_ALERT_TOPIC_ARN", raising=False)
        monkeypatch.setattr(evidence_store, "local_dir", lambda: tmp_path)
        monkeypatch.setattr(evidence_store, "_s3_client",
                            lambda: _FakeS3(fail_put=True))
        monkeypatch.setattr(evidence_store, "_sns_client",
                            lambda: (_ for _ in ()).throw(AssertionError))
        assert evidence_store.put_evidence("x.json", "{}")["s3_error"]

    def test_alert_failure_never_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STRIDE_EVIDENCE_BUCKET", "b")
        monkeypatch.setenv("STRIDE_ALERT_TOPIC_ARN", "arn:sns:x")
        monkeypatch.setattr(evidence_store, "local_dir", lambda: tmp_path)
        monkeypatch.setattr(evidence_store, "_s3_client",
                            lambda: _FakeS3(fail_put=True))
        monkeypatch.setattr(evidence_store, "_sns_client",
                            lambda: _FakeSNS(fail=True))
        out = evidence_store.put_evidence("x.json", "{}")
        assert out["s3_error"] and out["local"]
