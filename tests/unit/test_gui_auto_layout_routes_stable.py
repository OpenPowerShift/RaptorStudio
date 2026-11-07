"""Auto Layout's routes are final: a later re-route changes nothing (#154)."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "new_docs/models/DC2.rbds"


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._open_path(MODEL)
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _routes(view):
    from raptor.gui.ui.diagram_items import _LinkItem
    out = {}
    for it in view.scene().items():
        if isinstance(it, _LinkItem):
            p = it.path()
            out[it._link_num] = tuple(
                (round(p.elementAt(i).x, 2), round(p.elementAt(i).y, 2))
                for i in range(p.elementCount()))
    return out


@pytest.mark.parametrize("level", ["Power_sub", "Cooling_sub", "IT_sub"])
def test_reroute_after_auto_layout_changes_nothing(win, level):
    view = win._diagram_dock.view
    hid = next(i for i, h in view.hiers.items() if h.name == level)
    view.enter_level(hid)
    QApplication.processEvents()
    view.auto_layout()
    QApplication.processEvents()
    before = _routes(view)
    view.reroute_links()
    assert _routes(view) == before
