#!/usr/bin/env python3
"""DB-free unit tests for ROI task 04 (as-of-prediction-time odds snapshots).

Covers: row-builder correctness, snapshot_kind validation, seconds_to_jump
math, migration SQL sanity, coverage percentage math, and the graceful
no-DB behaviour of the persist path. No database, no network.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SERVER_PYTHON = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SERVER_PYTHON))

import odds_snapshots as osn
import odds_snapshot_coverage as cov
import capture_late_odds as late


UTC = timezone.utc
JUMP = "2026-03-28T04:15:00.000Z"
BEFORE_JUMP = datetime(2026, 3, 28, 4, 0, tzinfo=UTC)
AFTER_JUMP = datetime(2026, 3, 28, 5, 0, tzinfo=UTC)

RUNNERS = [
    {"horse": "Pop Award", "horse_id": "hrs_aus_1", "scratched": False,
     "odds": [{"bookmaker": "Ladbrokes", "win_odds": "12.00", "place_odds": "2.30"},
              {"bookmaker": "Sportsbet", "win_odds": "11.00", "place_odds": "2.20"}]},
    {"horse": "Second Horse", "horse_id": "hrs_aus_2", "scratched": False,
     "odds": [{"bookmaker": "Ladbrokes", "win_odds": "$3.50"}]},
    {"horse": "Scratched Horse", "scratched": True,
     "odds": [{"bookmaker": "Ladbrokes", "win_odds": "5.00"}]},
    {"horse": "No Quote Horse", "odds": []},
]


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

class TestBuildSnapshotRows:
    def _rows(self, **kw):
        args = dict(race_date="2026-03-28", track="Flemington", race_number=5,
                    runners=RUNNERS, snapshot_kind="tip_time",
                    captured_at="2026-03-28T03:00:00Z", jump_time=JUMP)
        args.update(kw)
        return osn.build_snapshot_rows(**args)

    def test_one_row_per_bookmaker(self):
        rows = self._rows()
        # Pop Award x2 bookmakers + Second Horse x1 = 3
        assert len(rows) == 3
        pop = [r for r in rows if r["horse_name"] == "Pop Award"]
        assert {r["bookmaker"] for r in pop} == {"Ladbrokes", "Sportsbet"}
        assert {r["decimal_odds"] for r in pop} == {12.0, 11.0}

    def test_scratched_and_unquoted_runners_produce_no_rows(self):
        rows = self._rows()
        names = {r["horse_name"] for r in rows}
        assert "Scratched Horse" not in names
        assert "No Quote Horse" not in names

    def test_identity_columns(self):
        rows = self._rows()
        row = rows[0]
        assert row["race_id"] == "2026-03-28|flemington|R5"
        assert row["runner_id"] == "hrs_aus_1"
        assert row["track"] == "flemington"
        assert row["race_number"] == 5
        assert row["horse_name_norm"] == "popaward"
        assert row["snapshot_kind"] == "tip_time"
        assert row["source_api"] == "racecard_legacy"

    def test_meet_id_prefixed_into_race_id(self):
        rows = self._rows(meet_id="mt_abc")
        assert rows[0]["race_id"] == "mt_abc|2026-03-28|flemington|R5"

    def test_runner_id_falls_back_to_normalised_name(self):
        rows = osn.build_snapshot_rows(
            race_date="2026-03-28", track="Flemington", race_number=1,
            runners=[{"horse": "No Id", "odds": [{"bookmaker": "B", "win_odds": "4.0"}]}],
            snapshot_kind="morning")
        assert rows[0]["runner_id"] == "noid"

    def test_captured_at_and_seconds_to_jump(self):
        rows = self._rows()
        row = rows[0]
        assert row["captured_at"] == datetime(2026, 3, 28, 3, 0, tzinfo=UTC)
        # 03:00Z capture vs 04:15Z jump = 75 min pre-jump
        assert row["seconds_to_jump"] == -4500

    def test_all_valid_kinds_accepted(self):
        for kind in ("tip_time", "late_t5", "morning", "baseline"):
            rows = self._rows(snapshot_kind=kind)
            assert all(r["snapshot_kind"] == kind for r in rows)

    def test_invalid_kind_rejected(self):
        with pytest.raises(ValueError):
            self._rows(snapshot_kind="closing_sp")
        with pytest.raises(ValueError):
            osn.validate_snapshot_kind("sp")

    def test_odds_of_one_or_less_excluded(self):
        rows = osn.build_snapshot_rows(
            race_date="2026-03-28", track="T", race_number=1,
            runners=[{"horse": "H", "odds": [{"bookmaker": "B", "win_odds": "1.00"},
                                             {"bookmaker": "C", "win_odds": "0.5"},
                                             {"bookmaker": "D", "win_odds": "junk"},
                                             {"bookmaker": "E", "win_odds": "2.50"}]}],
            snapshot_kind="tip_time")
        assert [(r["bookmaker"], r["decimal_odds"]) for r in rows] == [("E", 2.5)]

    def test_duplicate_bookmaker_deduped_first_wins(self):
        rows = osn.build_snapshot_rows(
            race_date="2026-03-28", track="T", race_number=1,
            runners=[{"horse": "H", "odds": [{"bookmaker": "B", "win_odds": "4.0"},
                                             {"bookmaker": "B", "win_odds": "5.0"}]}],
            snapshot_kind="tip_time")
        assert len(rows) == 1 and rows[0]["decimal_odds"] == 4.0


# ---------------------------------------------------------------------------
# seconds_to_jump / timestamp math
# ---------------------------------------------------------------------------

class TestSecondsToJump:
    def test_t5_is_minus_300(self):
        captured = "2026-03-28T04:10:00Z"
        assert osn.compute_seconds_to_jump(JUMP, captured) == -300

    def test_t15_fallback_records_actual(self):
        captured = "2026-03-28T04:00:00Z"
        assert osn.compute_seconds_to_jump(JUMP, captured) == -900

    def test_post_jump_positive(self):
        captured = "2026-03-28T04:16:30Z"
        assert osn.compute_seconds_to_jump(JUMP, captured) == 90

    def test_missing_jump_returns_none(self):
        assert osn.compute_seconds_to_jump(None, "2026-03-28T04:10:00Z") is None
        assert osn.compute_seconds_to_jump("", "2026-03-28T04:10:00Z") is None
        assert osn.compute_seconds_to_jump(JUMP, None) is None

    def test_naive_timestamps_treated_as_utc(self):
        assert osn.compute_seconds_to_jump("2026-03-28 04:15:00",
                                           "2026-03-28 04:10:00") == -300

    def test_parse_timestamp_z_suffix(self):
        dt = osn.parse_timestamp("2026-03-28T01:15:00.000Z")
        assert dt == datetime(2026, 3, 28, 1, 15, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Odds parsing
# ---------------------------------------------------------------------------

class TestOddsParsing:
    def test_dollar_sign_and_strings(self):
        assert osn.parse_decimal_odds("$12.00") == 12.0
        assert osn.parse_decimal_odds("3.5") == 3.5
        assert osn.parse_decimal_odds(7) == 7.0

    def test_invalid_values(self):
        assert osn.parse_decimal_odds(None) is None
        assert osn.parse_decimal_odds("1.0") is None
        assert osn.parse_decimal_odds("0.99") is None
        assert osn.parse_decimal_odds("SP") is None

    def test_extract_bookmaker_odds_returns_all_books(self):
        quotes = osn.extract_bookmaker_odds(RUNNERS[0])
        assert quotes == [("Ladbrokes", 12.0), ("Sportsbet", 11.0)]

    def test_sp_on_runner_is_not_a_quote(self):
        # SP must never masquerade as a live-captured market quote.
        quotes = osn.extract_bookmaker_odds({"horse": "H", "sp": "6.50", "odds": []})
        assert quotes == []


# ---------------------------------------------------------------------------
# Migration SQL sanity
# ---------------------------------------------------------------------------

class TestMigrationSQL:
    SQL = (PROJECT_ROOT / "migrations" / "runner_odds_snapshots.sql").read_text()

    def test_table_created_idempotently(self):
        assert "CREATE TABLE IF NOT EXISTS runner_odds_snapshots" in self.SQL

    def test_snapshot_kind_check(self):
        for kind in ("tip_time", "late_t5", "morning", "baseline"):
            assert f"'{kind}'" in self.SQL
        assert "CHECK" in self.SQL

    def test_primary_key_contract(self):
        assert ("PRIMARY KEY (race_id, runner_id, snapshot_kind, bookmaker, captured_at)"
                in self.SQL)

    def test_append_only_trigger_guarded(self):
        assert "BEFORE UPDATE ON runner_odds_snapshots" in self.SQL
        assert "insufficient_privilege" in self.SQL  # DO block guard
        assert "DO $$" in self.SQL

    def test_balanced_parens_and_terminator(self):
        assert self.SQL.count("(") == self.SQL.count(")")
        assert self.SQL.rstrip().endswith(";")


# ---------------------------------------------------------------------------
# Insert SQL / persist path (no DB)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rowcount=0, fail=None):
        self.rowcount = rowcount
        self.fail = fail
        self.queries = []

    def executemany(self, sql, payload):
        self.queries.append((sql, payload))
        if self.fail:
            raise self.fail
        self.rowcount = len(payload)

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _sample_rows():
    return osn.build_snapshot_rows(
        race_date="2026-03-28", track="Flemington", race_number=5,
        runners=RUNNERS, snapshot_kind="tip_time", jump_time=JUMP)


class TestPersistRows:
    def test_insert_sql_is_append_only_conflict_safe(self):
        sql = osn.SNAPSHOT_INSERT_SQL
        assert sql.startswith("INSERT INTO runner_odds_snapshots")
        assert "ON CONFLICT (race_id, runner_id, snapshot_kind, bookmaker, captured_at) DO NOTHING" in sql
        for col in osn.SNAPSHOT_COLUMNS:
            assert col in sql

    def test_flag_off_skips_without_touching_db(self, monkeypatch):
        monkeypatch.setenv("STRIDE_ODDS_SNAPSHOT_WRITE", "0")
        rows = _sample_rows()
        result = osn.persist_rows(object(), rows)  # conn must not be used
        assert result == {"written": 0, "skipped": len(rows),
                          "reason": "STRIDE_ODDS_SNAPSHOT_WRITE is off"}

    def test_empty_rows(self, monkeypatch):
        monkeypatch.setenv("STRIDE_ODDS_SNAPSHOT_WRITE", "true")
        assert osn.persist_rows(object(), [])["reason"] == "no rows"

    def test_success_returns_written_count(self, monkeypatch):
        monkeypatch.setenv("STRIDE_ODDS_SNAPSHOT_WRITE", "true")
        rows = _sample_rows()
        conn = _FakeConn(_FakeCursor())
        result = osn.persist_rows(conn, rows)
        assert result["written"] == len(rows)
        assert conn.committed and not conn.rolled_back

    def test_db_failure_is_graceful_never_raises(self, monkeypatch):
        monkeypatch.setenv("STRIDE_ODDS_SNAPSHOT_WRITE", "true")
        rows = _sample_rows()
        conn = _FakeConn(_FakeCursor(fail=RuntimeError("boom")))
        result = osn.persist_rows(conn, rows)
        assert result["written"] == 0
        assert result["skipped"] == len(rows)
        assert "boom" in result["reason"]
        assert conn.rolled_back

    def test_capture_helper_no_db_is_noop(self, monkeypatch):
        monkeypatch.setenv("STRIDE_ODDS_SNAPSHOT_WRITE", "true")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(osn, "_database_url", lambda: "")
        monkeypatch.setattr(osn, "_utcnow", lambda: BEFORE_JUMP)
        result = osn.capture_tip_time_snapshots(
            track="Flemington", race_number=5, date_str="2026-03-28",
            runners=RUNNERS, race={"off_time": JUMP})
        assert result["written"] == 0
        assert result["reason"] == "no DATABASE_URL"

    def test_capture_helper_never_raises_on_garbage(self, monkeypatch):
        monkeypatch.setenv("STRIDE_ODDS_SNAPSHOT_WRITE", "true")
        result = osn.capture_tip_time_snapshots(
            track="Flemington", race_number=5, date_str="not-a-date",
            runners=[{"broken": object()}], race=None)
        assert result["written"] == 0  # graceful, not an exception


# ---------------------------------------------------------------------------
# Pre-jump guard: reruns of a past date must never write tip_time rows
# ---------------------------------------------------------------------------

class TestPreJumpGuard:
    def test_post_jump_rerun_refused_without_touching_capture(self, monkeypatch):
        monkeypatch.setattr(osn, "_utcnow", lambda: AFTER_JUMP)

        def _boom(**_kwargs):
            raise AssertionError("capture_snapshots must not run post-jump")
        monkeypatch.setattr(osn, "capture_snapshots", _boom)
        result = osn.capture_tip_time_snapshots(
            track="Flemington", race_number=5, date_str="2026-03-28",
            runners=RUNNERS, race={"off_time": JUMP})
        assert result == {"written": 0, "skipped": len(RUNNERS),
                          "reason": "post_jump"}

    def test_pre_jump_capture_delegates(self, monkeypatch):
        monkeypatch.setattr(osn, "_utcnow", lambda: BEFORE_JUMP)
        seen = {}

        def _record(**kwargs):
            seen.update(kwargs)
            return {"written": 3, "skipped": 0}
        monkeypatch.setattr(osn, "capture_snapshots", _record)
        result = osn.capture_tip_time_snapshots(
            track="Flemington", race_number=5, date_str="2026-03-28",
            runners=RUNNERS, race={"off_time": JUMP, "meet_id": "m1"})
        assert result["written"] == 3
        assert seen["snapshot_kind"] == "tip_time"
        assert seen["jump_time"] == JUMP

    def test_missing_jump_time_still_captures(self, monkeypatch):
        # No advertised jump → capture proceeds; the training view closes
        # this hole by excluding NULL seconds_to_jump rows.
        monkeypatch.setattr(osn, "_utcnow", lambda: AFTER_JUMP)
        monkeypatch.setattr(
            osn, "capture_snapshots", lambda **kw: {"written": 1, "skipped": 0})
        result = osn.capture_tip_time_snapshots(
            track="Flemington", race_number=5, date_str="2026-03-28",
            runners=RUNNERS, race={})
        assert result["written"] == 1


# ---------------------------------------------------------------------------
# Coverage math
# ---------------------------------------------------------------------------

class TestCoverageMath:
    def test_coverage_pct(self):
        assert cov.coverage_pct(95, 100) == 95.0
        assert cov.coverage_pct(1, 3) == 33.33
        assert cov.coverage_pct(0, 0) == 0.0
        assert cov.coverage_pct(10, 10) == 100.0

    def test_status_threshold(self):
        assert cov.status_for_coverage(100, 95.0) == "GREEN"
        assert cov.status_for_coverage(100, 94.99) == "AMBER"
        assert cov.status_for_coverage(0, 0.0) == "NO_DATA"

    def test_no_db_report_is_graceful(self):
        report = cov.build_report("2026-03-28", conn=None)
        assert report["overall_status"] == "NO_DB"
        assert report["coverage"] is None


# ---------------------------------------------------------------------------
# Late-capture window logic (pure)
# ---------------------------------------------------------------------------

class TestLateCaptureWindow:
    JUMP_DT = datetime(2026, 3, 28, 4, 15, tzinfo=UTC)

    def _schedule(self):
        return [{"track": "Flemington", "meet_id": "m1", "race_number": 5,
                 "jump_time": self.JUMP_DT}]

    def _due(self, now, **kw):
        return late.find_due_races(self._schedule(), now, **kw)

    def test_too_early_not_due(self):
        assert self._due(self.JUMP_DT - timedelta(minutes=7)) == []

    def test_due_inside_window(self):
        assert len(self._due(self.JUMP_DT - timedelta(minutes=5))) == 1
        assert len(self._due(self.JUMP_DT - timedelta(minutes=6))) == 1
        assert len(self._due(self.JUMP_DT - timedelta(seconds=30))) == 1

    def test_late_first_sight_still_captured(self):
        # T-1min: missed T-5, but inside grace — capture with actual offset.
        assert len(self._due(self.JUMP_DT - timedelta(minutes=1))) == 1

    def test_after_grace_gives_up(self):
        assert self._due(self.JUMP_DT + timedelta(minutes=3)) == []

    def test_trial_races_excluded_from_racecard_schedule(self, tmp_path, monkeypatch):
        racecard = [{"course": "Flemington", "meet_id": "m1", "races": [
            {"race_number": 1, "off_time": "2026-03-28T01:00:00Z"},
            {"race_number": 2, "off_time": "2026-03-28T01:30:00Z", "is_trial": True},
        ]}]
        (tmp_path / "racecard_2026-03-28.json").write_text(
            __import__("json").dumps(racecard))
        monkeypatch.setattr(late, "RACECARDS_DIR", tmp_path)
        schedule = late.load_schedule("2026-03-28")
        assert [r["race_number"] for r in schedule] == [1]
        assert schedule[0]["jump_time"] == datetime(2026, 3, 28, 1, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Pipeline / training-view wiring sanity (text-level; modules need a DB driver)
# ---------------------------------------------------------------------------

class TestWiring:
    def test_tips_pipeline_hook_is_fire_and_forget(self):
        src = (SERVER_PYTHON / "run_tips_pipeline.py").read_text()
        assert "capture_tip_time_snapshots" in src
        assert "STRIDE_ODDS_SNAPSHOT_WRITE" in src
        # wrapped in try/except directly at the call site
        idx = src.index("capture_tip_time_snapshots")
        assert "try:" in src[:idx][-500:]
        assert "except Exception" in src[idx:idx + 400]

    def test_training_view_medians_only_prejump_rows(self):
        src = (SERVER_PYTHON / "refresh_training_view_v2.py").read_text()
        cte = src[src.index("odds_snap AS ("):]
        cte = cte[:cte.index("GROUP BY")]
        assert "s.snapshot_kind = 'tip_time'" in cte
        assert "s.seconds_to_jump IS NOT NULL" in cte
        assert "s.seconds_to_jump <= 0" in cte

    def test_training_view_columns_additive(self):
        src = (SERVER_PYTHON / "refresh_training_view_v2.py").read_text()
        assert "tip_time_odds" in src
        assert "odds_source" in src
        assert "seconds_to_jump" in src
        assert "runner_odds_snapshots" in src
        # legacy market_odds / sp_odds mapping untouched
        assert "COALESCE(sel.market_odds, td.market_odds) AS market_odds" in src
        assert "r.sp_odds," in src
        assert "p.market_odds," in src
        # odds_source vocabulary per spec
        for label in ("'snapshot'", "'racecard'", "'sp_fallback'"):
            assert label in src

    def test_training_view_odds_snap_is_tip_time_only(self):
        # G2 regression guard: the odds_snap CTE must feed ONLY tip_time snapshots
        # into training features. If a future edit drops or widens this filter,
        # late_t5 / settled odds would leak into training (breaking the as-of
        # guarantee) — fail loudly here rather than silently corrupt the retrain.
        src = (SERVER_PYTHON / "refresh_training_view_v2.py").read_text()
        start = src.index("odds_snap AS (")
        cte = src[start:src.index("\n    )", start)]
        assert "snapshot_kind = 'tip_time'" in cte, \
            "odds_snap CTE must filter snapshot_kind = 'tip_time' (as-of guarantee)"
        for leak_kind in ("late_t5", "morning", "baseline", "closing_sp"):
            assert leak_kind not in cte, \
                f"odds_snap CTE must not admit {leak_kind} — would leak non-as-of odds into training"

    def test_full_pipeline_wires_capture_and_coverage(self):
        # G1 regression guard: the daily orchestrator must launch the T-5min capture
        # watcher and run the coverage monitor, and the watcher must be fire-and-forget
        # (detached) so it can never delay or fail tipping.
        src = (SERVER_PYTHON / "run_full_pipeline.py").read_text()
        assert "capture_late_odds.py" in src and '"--watch"' in src
        assert "odds_snapshot_coverage.py" in src
        assert "start_new_session=True" in src, "late-odds watcher must be detached (fire-and-forget)"

    def test_capture_late_odds_has_watch_mode(self):
        # G1: the self-scheduling loop that lets a cron-less host actually run the
        # per-race T-5 capture through the day.
        src = (SERVER_PYTHON / "capture_late_odds.py").read_text()
        assert "def watch(" in src and '"--watch"' in src

    def test_tips_pipeline_launches_late_odds_watcher(self):
        # G1 invocation-path regression guard: run_full_pipeline.py is an
        # orchestrator no automated or documented manual path actually uses —
        # the Node scheduler and /stride-full reach run_tips_pipeline directly.
        # A watcher wired only into run_full_pipeline leaves the odds clock
        # dead in production, silently. The launch must live in the tips
        # pipeline itself: detached, flag-gated like the tip_time hook, and
        # unable to fail tipping.
        src = (SERVER_PYTHON / "run_tips_pipeline.py").read_text()
        assert "capture_late_odds.py" in src and '"--watch"' in src
        assert "STRIDE_SKIP_ODDS_WATCH" in src
        idx = src.index("def launch_late_odds_watcher")
        body = src[idx:idx + 2400]
        assert "snapshot_write_enabled" in body
        assert "start_new_session=True" in body, "watcher must be detached (fire-and-forget)"
        assert "except Exception" in body, "a launch failure must never fail tipping"
        # launched only on a stored run, after selections land
        store_idx = src.index("store_final_probs_in_audit(all_race_tips, date_str)")
        assert "launch_late_odds_watcher(date_str)" in src[store_idx:store_idx + 400]

    def test_watch_lock_single_instance_per_date(self, tmp_path, monkeypatch):
        # Two launch paths can both start a watcher (tips pipeline +
        # run_full_pipeline); the second must no-op, and a stale pidfile from
        # a crashed watcher must be reclaimed, or one crash kills every
        # subsequent day's capture.
        import subprocess as sp
        monkeypatch.setattr(late, "_watch_pid_path", lambda d: tmp_path / f"{d}.pid")

        assert late.acquire_watch_lock("2026-07-28") is True
        assert (tmp_path / "2026-07-28.pid").read_text() == str(os.getpid())
        # our own live pid holds the lock — a second acquire must refuse
        assert late.acquire_watch_lock("2026-07-28") is False

        # a dead pid is stale — reclaim
        proc = sp.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        (tmp_path / "2026-07-28.pid").write_text(str(proc.pid))
        assert late.acquire_watch_lock("2026-07-28") is True

        # corrupt pidfile is stale — reclaim
        (tmp_path / "2026-07-28.pid").write_text("not-a-pid")
        assert late.acquire_watch_lock("2026-07-28") is True

        # release removes only our own lock
        late.release_watch_lock("2026-07-28")
        assert not (tmp_path / "2026-07-28.pid").exists()
        (tmp_path / "2026-07-28.pid").write_text("12345")
        late.release_watch_lock("2026-07-28")
        assert (tmp_path / "2026-07-28.pid").exists(), \
            "must never remove another watcher's lock"


class TestDeriveSourceApi:
    def test_enriched_quotes_carry_their_own_source(self):
        runners = [{"horse": "A", "odds": [
            {"bookmaker": "betfair", "win_odds": 4.2,
             "source_api": "betfair_snapshot_db"}]}]
        assert osn.derive_source_api(runners) == "betfair_snapshot_db"

    def test_legacy_quotes_keep_the_historical_tag(self):
        runners = [{"horse": "A", "odds": [
            {"bookmaker": "sportsbet", "win_odds": 4.2}]}]
        assert osn.derive_source_api(runners) == osn.SOURCE_API

    def test_empty_field_keeps_the_default(self):
        assert osn.derive_source_api([]) == osn.SOURCE_API
