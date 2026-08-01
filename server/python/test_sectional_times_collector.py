"""Tests for the anti-bot loud-print exception in sectional_times_collector.

Pins the DM-H5 sanctioned one-line exception to the zero-collector-change
constraint: an anti-bot (Cloudflare challenge) diagnosis from download_csv
must print even when collect_for_date_track runs with verbose=False (the
--backfill path), while ordinary "no file" diagnoses stay quiet. Zero
network, zero DB.
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
