"""Exercise the production digest body and its error path without AWS/DB."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def digest(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace())
    path = Path(__file__).resolve().parents[3] / "infra/jobs/handler.py"
    spec = importlib.util.spec_from_file_location("kelly_digest_handler", path)
    handler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handler)
    published = []
    monkeypatch.setattr(handler, "boto3", SimpleNamespace(
        client=lambda *a, **kw: SimpleNamespace(publish=lambda **kw: published.append(kw))))
    monkeypatch.setattr(handler, "_state", lambda: SimpleNamespace(scan=lambda: {"Items": []}))
    monkeypatch.setattr(handler, "_run", lambda *a: SimpleNamespace(stdout="capture checked"))
    monkeypatch.setenv("STRIDE_ALERT_TOPIC_ARN", "test-topic")
    monkeypatch.setenv("STRIDE_COMMISSION_RATE", "0.08")
    return handler, published


def test_digest_prints_and_publishes_actual_readiness(digest, monkeypatch, capsys):
    handler, published = digest
    def query(sql, params, *, fetch_all=False):
        assert "FROM selection_ledger WHERE settled = TRUE" in sql
        assert params == () and fetch_all is True
        return [("2026-09-01", True, False, 100.0, 3.0, False, -2.0)]
    monkeypatch.setattr(handler, "_db_query", query)
    handler.job_weekly_digest()
    # A successful SNS call alone would pass if Kelly was omitted entirely.
    # Assert the body contents and the printed body's identity instead.
    body = published[0]["Message"]
    assert "ready: False; settled bets: 1/400" in body
    assert "mean CLV (%): -2.0; positive: False; CLV coverage: 1/1" in body
    assert capsys.readouterr().out.strip() == body


@pytest.mark.parametrize("failure", ["empty", "missing table", "DATABASE_URL unset"])
def test_digest_cannot_succeed_without_ledger_evidence(digest, monkeypatch, failure):
    handler, published = digest
    def query(*a, **kw):
        if failure == "empty":
            return []
        raise RuntimeError(failure)
    monkeypatch.setattr(handler, "_db_query", query)
    with pytest.raises((ValueError, RuntimeError)):
        handler.job_weekly_digest()
    assert published == []


def test_database_reader_fetches_all_and_closes_connection(digest, monkeypatch):
    handler, _ = digest
    from unittest.mock import MagicMock
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [(1,), (2,)]
    driver = SimpleNamespace(connect=lambda url: conn)
    monkeypatch.setitem(sys.modules, "psycopg2", driver)
    monkeypatch.setenv("DATABASE_URL", "test-only")
    assert handler._db_query("SELECT settled", (), fetch_all=True) == [(1,), (2,)]
    cursor.execute.assert_called_once_with("SELECT settled", ())
    conn.close.assert_called_once()
