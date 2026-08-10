"""Tests for the non-verbose loud-print rule in sectional_times_collector.

Pins the download-failure logging on the verbose=False (--backfill) path.
Originally only anti-bot diagnoses printed (the DM-H5 sanctioned exception
to the zero-collector-change constraint); #125 widened that to every
diagnosis EXCEPT all-variants-404, because a scheduled run whose non-404
failures are silent cannot be told apart from a quiet day in its log.
Plain 404 on every variant — no meeting/CSV that day — remains the one
quiet case. Zero network, zero DB.
"""

import io
from contextlib import redirect_stdout
from unittest.mock import patch

import sectional_times_collector as stc


def _run(diagnosis, verbose):
    with patch.object(stc, "download_csv", return_value=(None, None, diagnosis)):
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = stc.collect_for_date_track("2026-07-25", "Eagle Farm", "db://unused", verbose=verbose)
        assert result == (0, 0, 0, 0)
        return buf.getvalue()


def test_antibot_challenge_page_prints_when_not_verbose():
    out = _run("Eagle_Farm: anti-bot challenge page", verbose=False)
    assert "anti-bot challenge page" in out
    assert "Eagle Farm" in out


def test_antibot_http403_prints_when_not_verbose():
    out = _run("Eagle_Farm: HTTP 403 anti-bot challenge (Cloudflare)", verbose=False)
    assert "anti-bot challenge" in out


def test_ordinary_missing_csv_stays_quiet_when_not_verbose():
    out = _run("Eagle_Farm: HTTP 404", verbose=False)
    assert out == ""


def test_all_diagnoses_print_when_verbose():
    out = _run("Eagle_Farm: HTTP 404", verbose=True)
    assert "No CSV for Eagle Farm" in out


def test_all_variants_404_stays_quiet_when_not_verbose():
    out = _run("Gold_Coast: HTTP 404; Aquis_Park_Gold_Coast: HTTP 404",
               verbose=False)
    assert out == ""


def test_timeout_prints_when_not_verbose():
    out = _run("Eagle_Farm: TimeoutError", verbose=False)
    assert "TimeoutError" in out


def test_schema_drift_prints_when_not_verbose():
    out = _run("Eagle_Farm: 200 but not a sectional CSV", verbose=False)
    assert "not a sectional CSV" in out


def test_mixed_404_and_other_failure_prints_when_not_verbose():
    out = _run("Gold_Coast: HTTP 404; Aquis_Park_Gold_Coast: HTTP 500",
               verbose=False)
    assert "HTTP 500" in out
