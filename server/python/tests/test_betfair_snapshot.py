"""Snapshot rows, idempotent writes and the CLI contract for
betfair_odds_snapshot.py. Zero network, zero database: transport and
connection are the fixture fakes.
"""

from datetime import datetime, timezone

import pytest

import betfair_markets
import betfair_odds_snapshot as snap
from providers import betfair_auth
# Bound before any fixture stubs the module attribute, so the marker test
# can exercise the real login path.
from providers.betfair_auth import get_session_token as real_get_session_token
from tests.betfair_fixtures import BOOKS, CATALOGUE, DATE, FakeConn, make_post

CAPTURED_AT = datetime(2026, 8, 1, 2, 30, tzinfo=timezone.utc)


def _mapped():
    conn = FakeConn()
    cur = conn.cursor()
    mapped, _, _ = betfair_markets.map_markets(
        CATALOGUE,
        betfair_markets.load_schedule(cur, DATE),
        betfair_markets.load_horse_bridge(cur))
    return mapped


def _rows():
    return snap.build_snapshot_rows(_mapped(), BOOKS, CAPTURED_AT,
                                    "betfair_delayed", "baseline")


def test_book_produces_exact_rows():
    rows, skipped = _rows()
    assert rows[0] == {
        "race_id": "2026-08-01|royalrandwick|R1",
        "runner_id": "h_fast",
        "snapshot_kind": "baseline",
        "bookmaker": "betfair",
        "captured_at": CAPTURED_AT,
        "decimal_odds": 3.5,
        "source_api": "betfair_delayed",
        # 02:30Z capture vs 03:15Z jump: pre-jump is negative (roi/04 rule).
        "seconds_to_jump": -2700,
        "race_date": "2026-08-01",
        "track": "royalrandwick",
        "race_number": 1,
        "horse_name": "Fast Horse",
        "horse_name_norm": "fasthorse",
        "market_id": "1.111",
        "selection_id": 111,
        "back_size": 120.5,
        "lay_price": 3.7,
        "lay_size": 60.0,
        "last_price_traded": 3.55,
        "total_matched": 1500.5,
    }
    assert [r["runner_id"] for r in rows] == ["h_fast", "h_second"]
    assert all(r["source_api"] == "betfair_delayed" for r in rows)
    # Second String has no lastPriceTraded: back price alone still qualifies.
    assert rows[1]["decimal_odds"] == 6.0
    assert rows[1]["last_price_traded"] is None
    # REMOVED runner + runner with no offers/trades are skipped, not guessed.
    assert {(s["selection_id"], s["reason"].split()[0]) for s in skipped} == {
        (444, "runner"), (555, "no")}


def test_missing_book_market_is_skipped_not_fatal():
    rows, skipped = snap.build_snapshot_rows(
        _mapped(), BOOKS[:1], CAPTURED_AT, "betfair_delayed", "baseline")
    assert [r["runner_id"] for r in rows] == ["h_fast"]
    assert any(s["reason"].startswith("market missing") for s in skipped)


def test_invalid_snapshot_kind_rejected():
    with pytest.raises(ValueError):
        snap.build_snapshot_rows([], [], CAPTURED_AT, "betfair_delayed", "betfair")


def test_persist_is_idempotent_on_rerun():
    rows, _ = _rows()
    conn = FakeConn()
    assert snap.persist_rows(conn, rows) == len(rows)
    assert len(conn.rows) == 2
    # Same capture again: the ON CONFLICT clause matching the roi/04 PK must
    # absorb the duplicates — no violation, no extra rows.
    snap.persist_rows(conn, rows)
    assert len(conn.rows) == 2
    assert conn.rollbacks == 0
    # A later capture is a new row, never an update (append-only table).
    later, _ = snap.build_snapshot_rows(
        _mapped(), BOOKS, datetime(2026, 8, 1, 2, 45, tzinfo=timezone.utc),
        "betfair_delayed", "baseline")
    snap.persist_rows(conn, later)
    assert len(conn.rows) == 4


def test_persist_rolls_back_loudly_on_failure():
    rows, _ = _rows()

    class BrokenConn(FakeConn):
        def commit(self):
            raise RuntimeError("connection lost")

    conn = BrokenConn()
    with pytest.raises(RuntimeError):
        snap.persist_rows(conn, rows)
    assert conn.rollbacks == 1
    assert conn.rows == []


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BETFAIR_APP_KEY", "test-app-key")
    monkeypatch.setenv("BETFAIR_USERNAME", "user@example.com")
    monkeypatch.setenv("BETFAIR_PASSWORD", "hunter2")
    monkeypatch.delenv("BETFAIR_SESSION_TOKEN", raising=False)
    monkeypatch.setattr(betfair_auth, "_TOKEN_CACHE", tmp_path / "session.json")
    monkeypatch.setattr(betfair_auth, "_LOGIN_BLOCK_MARKER", tmp_path / "blocked.json")
    monkeypatch.setattr(betfair_auth, "get_session_token", lambda **kw: "TOK")
    monkeypatch.setattr(snap, "_utcnow", lambda: CAPTURED_AT)
    return tmp_path


def test_cli_dry_run_writes_nothing(cli_env, capsys):
    post = make_post()
    conn = FakeConn()
    rc = snap.main(["--date", DATE], post=post, connect=lambda: conn)
    assert rc == 0
    assert conn.insert_statements() == [], "dry run must not execute any INSERT"
    assert conn.rows == []
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "UNMAPPED MARKET 1.999" in out
    assert "Mystery Guest" in out
    assert out.count("WOULD WRITE") == 2


def test_cli_commit_writes_and_reruns_dedup(cli_env):
    conn = FakeConn()
    rc = snap.main(["--date", DATE, "--commit"], post=make_post(), connect=lambda: conn)
    assert rc == 0
    assert len(conn.rows) == 2
    assert conn.commits >= 1
    # Identical capture instant (frozen _utcnow) run again: dedup, no growth.
    rc = snap.main(["--date", DATE, "--commit"], post=make_post(), connect=lambda: conn)
    assert rc == 0
    assert len(conn.rows) == 2


def test_cli_fails_when_mapped_markets_commit_no_rows(cli_env, capsys):
    """The silent no-op this job actually shipped: every scheduled run since
    day zero exited 0 having committed nothing, so gate 1's clock never
    started and nothing said so."""
    conn = FakeConn()
    # Markets map, but the write lands nothing — the shape of a persist that
    # dedups everything away or an INSERT that matches no target.
    monkeypatch_persist = lambda c, rows: 0
    orig = snap.persist_rows
    snap.persist_rows = monkeypatch_persist
    try:
        rc = snap.main(["--date", DATE, "--commit"],
                       post=make_post(), connect=lambda: conn)
    finally:
        snap.persist_rows = orig
    assert rc == 5, "mapped markets that commit zero rows must not exit 0"
    assert "POST-CONDITION FAILED" in capsys.readouterr().out


def test_cli_quiet_day_commits_nothing_and_still_passes(cli_env, capsys):
    """A quiet day maps no markets and writes no rows, and that is correct.
    The post-condition must not fire here — roughly 43 percent of days carry
    no target-track racing, and a check that goes red on those trains the
    operator to stop reading it."""
    conn = FakeConn()
    # Empty catalogue: the provider was healthy, nothing of ours was racing.
    quiet_post = make_post(catalogue=[])
    rc = snap.main(["--date", DATE, "--commit"],
                   post=quiet_post, connect=lambda: conn)
    assert rc == 0, "a quiet day must stay green"
    assert "POST-CONDITION FAILED" not in capsys.readouterr().out


def test_cli_refuses_when_login_marker_present(cli_env, monkeypatch):
    # Real auth path (no stub) with a lockout marker on disk: the CLI must
    # exit 3 before anything touches the wire.
    monkeypatch.setattr(betfair_auth, "get_session_token", real_get_session_token)
    betfair_auth._write_login_block(
        "ACCOUNT_PENDING_PASSWORD_CHANGE",
        betfair_auth.TERMINAL_LOGIN_STATUSES["ACCOUNT_PENDING_PASSWORD_CHANGE"])
    monkeypatch.setattr(betfair_auth, "requests", None)  # any HTTP would crash

    post = make_post()
    conn = FakeConn()
    rc = snap.main(["--date", DATE], post=post, connect=lambda: conn)
    assert rc == 3
    assert post.calls == []
    assert conn.executed == []


def test_cli_missing_config_exits_2(cli_env, monkeypatch):
    monkeypatch.setenv("BETFAIR_APP_KEY", "")
    rc = snap.main(["--date", DATE], post=make_post(), connect=FakeConn)
    assert rc == 2
