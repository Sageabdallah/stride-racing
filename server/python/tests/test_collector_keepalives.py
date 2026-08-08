"""Every collector's DB connect must carry the shared TCP keepalives.

A Neon socket dropped while a collector held it idle either raised
InterfaceError (Fargate, 2026-08-08 04:40) or hung forever in libpq
poll() (local repro, same day). KEEPALIVE_KWARGS turns the hang variant
into an error within ~60s. Each script keeps its own DATABASE_URL
resolution and error contract, so the params are shared as a dict rather
than by routing everyone through get_connection().
"""

import sys
from pathlib import Path

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intelligence_common import KEEPALIVE_KWARGS


class FakeConn:
    autocommit = False

    def cursor(self):
        class C:
            def execute(self, sql):
                pass

            def close(self):
                pass

        return C()


@pytest.fixture
def capture_connect(monkeypatch):
    calls = []

    def fake_connect(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeConn()

    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unit-test/db")
    return calls


def _assert_keepalived(calls):
    assert calls, "connect was never called"
    for _, kwargs in calls:
        for key, value in KEEPALIVE_KWARGS.items():
            assert kwargs.get(key) == value, f"missing {key} in {kwargs}"


def test_keepalive_kwargs_shape():
    assert KEEPALIVE_KWARGS["keepalives"] == 1
    assert set(KEEPALIVE_KWARGS) == {
        "keepalives", "keepalives_idle", "keepalives_interval", "keepalives_count",
    }


def test_intelligence_common_get_connection(capture_connect):
    import intelligence_common
    intelligence_common.get_connection()
    _assert_keepalived(capture_connect)


def test_auto_results_collector(capture_connect):
    import auto_results_collector
    auto_results_collector.get_db_connection()
    _assert_keepalived(capture_connect)


def test_stride_results_collector(capture_connect):
    import stride_results_collector
    stride_results_collector._get_connection()
    _assert_keepalived(capture_connect)


def test_betfair_odds_snapshot(capture_connect):
    import betfair_odds_snapshot
    assert betfair_odds_snapshot._connect() is not None
    _assert_keepalived(capture_connect)


def test_odds_snapshots(capture_connect, monkeypatch):
    import odds_snapshots
    monkeypatch.setattr(odds_snapshots, "_database_url",
                        lambda: "postgresql://unit-test/db")
    assert odds_snapshots._connect() is not None
    _assert_keepalived(capture_connect)
    # the helper's existing handshake timeout must survive the change
    assert capture_connect[0][1].get("connect_timeout") == 3
