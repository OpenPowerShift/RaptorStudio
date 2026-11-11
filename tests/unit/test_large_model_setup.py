"""Work a large model used to pay for more than once: the file re-read and
re-checked on every edit, validation run twice per command, and a row scan
that never took its fast path."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api import build
from raptor.api.workspace import Workspace
from raptor.core import input_rules
from raptor.core.engine import spf_fev

REPO = Path(__file__).resolve().parent.parent.parent
MODEL = REPO / "new_docs" / "models" / "HospitalPower.rbds"


@pytest.fixture
def counted(tmp_path, monkeypatch):
    """A workspace, and how many times it has parsed a model file."""
    calls: list[str] = []
    real = Workspace._load_any

    def load(path: str):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(Workspace, "_load_any", staticmethod(load))
    return Workspace(tmp_path), calls


def test_opening_then_editing_parses_the_file_once(counted):
    ws, calls = counted
    mid = ws.open_model(MODEL)["model_id"]
    assert len(calls) == 1
    ws.get(mid)                                   # already in the cache
    build.configure_system(ws, mid, num_runs=7)   # an edit, saved
    build.configure_system(ws, mid, num_runs=8)   # and another
    assert len(calls) == 1
    assert ws.get(mid).path.is_file()


def test_first_save_keeps_the_opened_file_as_the_undo_point(counted):
    ws, _calls = counted
    mid = ws.open_model(MODEL)["model_id"]
    before = ws.get(mid).rbd.sections["SYSTEM_DATA"].rows[0].get("NumRuns")
    build.configure_system(ws, mid, num_runs=7)
    ws.undo(mid)
    assert ws.get(mid).rbd.sections["SYSTEM_DATA"].rows[0].get("NumRuns") == before


def test_an_edit_that_breaks_a_rule_is_still_refused(counted):
    ws, _calls = counted
    mid = ws.open_model(MODEL)["model_id"]
    build.configure_system(ws, mid, num_runs=7)       # baseline remembered
    handle = ws.get(mid)
    block = next(r for r in handle.rbd.sections["BLOCK_DATA"].rows
                 if str(r.get("ID")) != "0")
    good = block["blockName"]
    block["blockName"] = "bad\x07name"
    with pytest.raises(input_rules.InputRuleViolation):
        ws.save(mid)
    # The model in memory is put back as it was saved.
    names = [r["blockName"] for r in ws.get(mid).rbd.sections["BLOCK_DATA"].rows]
    assert good in names and "bad\x07name" not in names
    # A problem the model already had does not block the next edit.
    build.configure_system(ws, mid, num_runs=9)


def test_a_clean_row_is_not_scanned_cell_by_cell(monkeypatch):
    from raptor.core.fileio.rbds import load_rbds
    rbd = load_rbds(str(MODEL))
    calls = []
    real = input_rules.control_problem
    monkeypatch.setattr(input_rules, "control_problem",
                        lambda text: calls.append(text) or real(text))
    out: list = []
    input_rules._check_control_chars(rbd, out)
    assert out == [] and calls == []


@pytest.mark.parametrize("bad", ["bell\x07", "zero​width", "lone\ud800"])
def test_a_control_character_is_still_found(bad):
    from raptor.core.fileio.rbds import load_rbds
    rbd = load_rbds(str(MODEL))
    rbd.sections["BLOCK_DATA"].rows[-1]["blockName"] = bad
    out: list = []
    input_rules._check_control_chars(rbd, out)
    assert len(out) == 1 and "blockName" in out[0].message


def test_validation_runs_once_for_a_command(tmp_path, monkeypatch):
    from raptor.cli import common
    ws = Workspace(tmp_path)
    mid = ws.open_model(MODEL)["model_id"]
    calls = []
    real = common.issues_for
    monkeypatch.setattr(common, "issues_for",
                        lambda w, m: calls.append(m) or real(w, m))
    common.require_runnable(ws, mid)
    assert len(calls) == 1


def test_single_point_failures_use_more_cores_only_on_a_large_model(monkeypatch):
    monkeypatch.setattr(spf_fev._os, "cpu_count", lambda: 8)
    assert spf_fev.auto_workers(17) == 1
    assert spf_fev.auto_workers(5000) == 4
    monkeypatch.setattr(spf_fev._os, "cpu_count", lambda: 64)
    assert spf_fev.auto_workers(5000) == 8
    monkeypatch.setattr(spf_fev._os, "cpu_count", lambda: 1)
    assert spf_fev.auto_workers(5000) == 2
