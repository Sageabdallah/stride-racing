#!/usr/bin/env python3
"""Regression test for gate_preregistration's marker counter.

The counter must prefix-match "[SAGE-APPROVAL" so it catches the colon form
"[SAGE-APPROVAL: <question, default>]" the protocol pack (PR #18) writes, the
closed form "[SAGE-APPROVAL]", and nothing else — the pack's prose convention
keeps non-marker mentions unbracketed. The pre-fix counter matched only the
closed form and printed GREEN against a doc holding 3 unresolved markers."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import retrain_preflight as rp


MARKER_DOC = """# Pre-registration (fixture)

Status: DRAFT — merges only when zero SAGE-APPROVAL markers remain.

- (c) Minimum sample: [SAGE-APPROVAL: minimum settled-bet count; recommended 200].
- Confirmation: mean CLV > 0 over [SAGE-APPROVAL: N consecutive race days;
  recommended 10].
- A closed-form marker: [SAGE-APPROVAL]
- Prose mention (not a marker): resolving a SAGE-APPROVAL marker before close.
"""

RESOLVED_DOC = """# Pre-registration (fixture)

Status: all markers resolved.

- (c) Minimum sample: 200 settled bets.
- Confirmation: mean CLV > 0 over 10 consecutive race days.
- Prose mention (not a marker): resolving a SAGE-APPROVAL marker before close.
"""


def test_unresolved_markers_counted_in_both_forms(tmp_path):
    doc = tmp_path / "12-preregistration.md"
    doc.write_text(MARKER_DOC, encoding="utf-8")
    rows = rp.gate_preregistration(doc)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "preregistration"
    assert row["status"] == "AMBER"
    # 3 markers (two colon-form + one closed-form); the unbracketed prose
    # mention is NOT counted.
    assert "3" in row["detail"]


def test_resolved_doc_is_green(tmp_path):
    doc = tmp_path / "12-preregistration.md"
    doc.write_text(RESOLVED_DOC, encoding="utf-8")
    rows = rp.gate_preregistration(doc)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "GREEN"
    assert "no unresolved markers" in row["detail"]


def test_missing_doc_is_amber_not_found(tmp_path):
    rows = rp.gate_preregistration(tmp_path / "does-not-exist.md")
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "AMBER"
    assert "not found" in row["detail"]
