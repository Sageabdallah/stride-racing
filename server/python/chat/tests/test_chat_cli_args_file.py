"""--args and --args-file on chat.cli's --tool path.

The file option exists because Windows PowerShell cannot reliably pass a
JSON object containing both spaces and embedded double quotes to a native
process: the operator hit `--args is not JSON: Expecting property name
enclosed in double quotes: line 1 column 2 (char 1)` running a lookup for a
horse whose name has spaces in it, after an earlier attempt split the value
into separate argv entries. File contents are not subject to any shell
quoting, so the file path has neither failure.
"""

from __future__ import annotations

import json

import pytest

from chat import cli
from chat.artifacts import ArtifactStore
from chat.tests.conftest import FakeArtifacts, FakeDB
from chat.tools import Context


@pytest.fixture(autouse=True)
def _no_real_backends(monkeypatch, artifacts, pf, db):
    """Every test here runs --tool, which builds a Context from the
    environment; give it the fakes instead of Neon and Punting Form."""
    monkeypatch.setattr(cli, "build_context",
                        lambda *a, **k: Context(artifacts=artifacts, pf=pf, db=db,
                                                today="2026-09-10"))


def test_args_file_accepts_a_value_powershell_cannot_pass_inline(tmp_path, capsys):
    """The exact shape that failed: a string value containing spaces."""
    path = tmp_path / "args.json"
    path.write_text(json.dumps({"name": "Pride Of Jenni"}), encoding="utf-8")
    rc = cli.main(["--tool", "lookup_horse", "--args-file", str(path)])
    out = json.loads(capsys.readouterr().out)
    # No database rows behind the fake, so this is an honest miss, not an
    # argument error: the point is the arguments parsed and reached the tool.
    assert rc == 0
    assert out["ok"] is True and out["found"] is False
    assert "Pride Of Jenni" in out["notes"][0]


def test_inline_args_still_work(capsys):
    rc = cli.main(["--tool", "get_stride_tips", "--args",
                   '{"date":"2026-04-12","track":"Randwick"}'])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["found"] is True
    assert [r["race_number"] for r in out["data"]["races"]] == [5, 6]


def test_the_mangled_powershell_string_is_still_reported_clearly(capsys):
    """What PowerShell actually delivered. It must fail as a readable
    argument error, naming --args, not as a traceback."""
    rc = cli.main(["--tool", "lookup_horse", "--args", '{name":"Pride Of Jenni"}'])
    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("--args is not JSON:")


def test_bad_json_in_a_file_names_the_file_not_the_flag(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    rc = cli.main(["--tool", "lookup_horse", "--args-file", str(path)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--args-file" in err and "is not JSON" in err and str(path) in err


def test_missing_file_is_reported_not_raised(tmp_path, capsys):
    rc = cli.main(["--tool", "lookup_horse", "--args-file", str(tmp_path / "nope.json")])
    err = capsys.readouterr().err
    assert rc == 2 and "could not read --args-file" in err


def test_both_options_at_once_is_refused(tmp_path, capsys):
    path = tmp_path / "args.json"
    path.write_text("{}", encoding="utf-8")
    rc = cli.main(["--tool", "lookup_horse", "--args", '{"name":"X"}',
                   "--args-file", str(path)])
    err = capsys.readouterr().err
    assert rc == 2 and "not both" in err


def test_args_file_defaults_are_unchanged_when_absent(capsys):
    """No --args and no --args-file: the tool still runs with {}."""
    rc = cli.main(["--tool", "get_performance"])
    out = json.loads(capsys.readouterr().out)
    assert rc in (0, 1)
    assert "ok" in out
