"""Selecting a block builds only the Inspector tab that is shown; the others are built when opened."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QGroupBox

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.inspector import InspectorDock
from raptor.gui.ui.main_window import MainWindow

pytestmark = pytest.mark.gui
GOLD = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _titles(scroll) -> list[str]:
    return [gb.title() for gb in scroll.widget().findChildren(QGroupBox)]


def test_deferred_block_tabs_never_build_into_a_node(qapp, monkeypatch):
    """A block's lazy Failure/Repair builders must be dropped on a node.

    Capacity and Standby reuse the block Failure/Repair tab slots, so a
    stale builder made Capacity show Failure Distribution and Standby
    show Repair Distribution.
    """
    monkeypatch.setattr(InspectorDock, "lazy_tabs", True)
    w = MainWindow()
    rbd = load_rbd(str(GOLD / "TwoBlockRedundancy" / "input.rbd"))
    w._load_into_app(rbd, None)
    view = w._diagram_dock.view
    insp = w._inspector
    bid = next(b for b in view.blocks if b != 0)
    nid = next(n for n in view.nodes if n != 0)
    insp._user_tab_index = 0
    insp.show_block(bid, view.blocks)
    insp.show_node(nid, view.nodes)
    insp._tabs.setCurrentIndex(insp._tabs.indexOf(insp._scroll_failure))
    qapp.processEvents()
    assert "Failure Distribution" not in _titles(insp._scroll_failure)
    assert "Flow In" in _titles(insp._scroll_failure)
    insp._tabs.setCurrentIndex(insp._tabs.indexOf(insp._scroll_repair))
    qapp.processEvents()
    assert "Repair Distribution" not in _titles(insp._scroll_repair)
    assert "Switchover" in _titles(insp._scroll_repair)
    w._dirty = False
    w.close()


def test_only_the_shown_tab_is_built_until_it_is_opened(qapp, monkeypatch):
    monkeypatch.setattr(InspectorDock, "lazy_tabs", True)
    w = MainWindow()
    rbd = load_rbd(str(GOLD / "TwoBlockRedundancy" / "input.rbd"))
    w._load_into_app(rbd, None)
    view = w._diagram_dock.view
    insp = w._inspector
    bid = next(b for b in view.blocks if b != 0)
    insp._user_tab_index = 0
    insp.show_block(bid, view.blocks)
    assert "Identity" in _titles(insp._scroll_general)
    assert _titles(insp._scroll_failure) == []            # not built yet
    insp._tabs.setCurrentIndex(insp._tabs.indexOf(insp._scroll_failure))
    assert "Failure Distribution" in _titles(insp._scroll_failure)
    # a later selection refreshes the tab that is open and leaves the others for when they are opened
    other = next(b for b in view.blocks if b not in (0, bid))
    insp.show_block(other, view.blocks)
    assert "Failure Distribution" in _titles(insp._scroll_failure)
    assert f"(ID {other})" in insp._header.text()
    insp.ensure_all_tabs_built()
    assert "Repair Distribution" in " ".join(_titles(insp._scroll_repair)) or _titles(insp._scroll_repair)
    w._dirty = False
