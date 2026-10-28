"""The Phases dialog on a phased model built from scratch by `build_from_spec` (`phases` + `phase_states`).

Found 2026-10-01: a node whose phase is Cut holds the file's sentinel -1 in its grid cell, and `Apply` range-checked it
as a stress (>= 0), so the dialog refused to apply ANY model with a phased Cut node, behind a modal warning.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from raptor.api.build import build_from_spec
from raptor.core.fileio.builder import save
from raptor.core.fileio.rbd import render_rbd
from raptor.gui.ui.main_window import MainWindow
from raptor.gui.ui.phasing_dialog import PhasingDialog

PHASES = [{"name": f"P{i}", "duration_dist": 8, "params": (100.0, 5.0, 0.0), "mission": 1} for i in (1, 2, 3)]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    spec = {
        "name": "Scratch",
        "blocks": [{"name": n, "fail_dist": 4, "fail_params": [300.0], "repair_dist": 4, "repair_params": [10.0]}
                   for n in "ABC"],
        "nodes": [{"name": "start", "type": 1, "n": 0, "out": 3}, {"name": "J", "type": 2, "n": 3, "k": 2, "out": 1},
                  {"name": "end", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "start", "to": n} for n in "ABC"] + [{"from": n, "to": "J"} for n in "ABC"]
                 + [{"from": "J", "to": "end"}],
        "phases": PHASES,
        "phase_states": {"A": ["A: 1.000", "A: 2.000", "C: 1.000"], "B": ["L: 1.000", "A: 0.500", "A: 1.000"],
                         "J": ["C", "2", "L"]},
    }
    rbd = build_from_spec(spec)
    rbd.version = "rbd"
    path = tmp_path / "Scratch.rbd"
    save(rbd, str(path))
    w = MainWindow()
    w._open_path(str(path))
    qapp.processEvents()
    yield w
    w.deleteLater()


def test_dialog_shows_built_states(win, qapp):
    dlg = PhasingDialog(win)
    assert dlg._define.rowCount() == 3
    blocks = dlg._tables["block"]
    row = {blocks.item(r, 0).text().split(" ")[0]: [blocks.item(r, c).text() for c in range(1, 5)]
           for r in range(blocks.rowCount())}
    assert row["A"][1:] == ["1.000", "2.000", "Cut"]
    assert row["B"][1] == "Link:1.000"
    nodes = dlg._tables["node"]
    assert [nodes.item(0, c).text() for c in (2, 3, 4)] == ["Cut", "k=2", "Linked"]


def test_apply_with_cut_node_does_not_warn_and_is_lossless(win, qapp, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    before = render_rbd(win._rbd)
    dlg = PhasingDialog(win)
    dlg._apply()
    qapp.processEvents()
    assert not warned, warned
    assert render_rbd(win._rbd) == before
