"""`raptor run -o DIR` writes the summary there; it used to write nothing unless
`-r` named a report (issue #4, item 14)."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.cli.main import main

GOLD = Path(__file__).resolve().parents[1] / "golden"
CAPACITY_MODEL = str(GOLD / "Capacity2" / "input.rbd")
PLAIN_MODEL = str(GOLD / "HierarchyExample" / "input.rbd")


@pytest.mark.parametrize("fmt, ext", [("text", "txt"), ("markdown", "md"),
                                      ("json", "json"), ("csv", "csv")])
def test_run_with_an_output_dir_writes_the_summary(tmp_path, capsys, fmt, ext):
    out = tmp_path / "out"
    code = main(["run", PLAIN_MODEL, "-n", "2", "-o", str(out), "-f", fmt])
    printed = capsys.readouterr().out
    assert code == 0
    written = (out / f"summary_input.{ext}").read_text()
    assert "Availability" in written
    assert written == printed


def test_format_none_writes_no_summary_and_says_so(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["run", PLAIN_MODEL, "-n", "2", "-o", str(out), "-f", "none"]) == 0
    assert "nothing written" in capsys.readouterr().err
    assert not out.exists() or not any(out.iterdir())


def test_run_without_an_output_dir_writes_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["run", PLAIN_MODEL, "-n", "2"]) == 0
    assert list(tmp_path.iterdir()) == []
