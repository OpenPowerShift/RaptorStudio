"""Link style belongs to the level it was set on (#154)."""
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


def _pick(win, key):
    combo = win._link_routing_combo
    combo.setCurrentIndex(combo.findData(key))
    QApplication.processEvents()


def _enter(view, name):
    hid = next(i for i, h in view.hiers.items() if h.name == name)
    view.enter_level(hid)
    QApplication.processEvents()


def test_opening_a_hierarchy_keeps_its_links(win):
    view = win._diagram_dock.view
    _enter(view, "Power_sub")
    before = _routes(view)
    view.go_up()
    QApplication.processEvents()
    _pick(win, "orthogonal")
    view.auto_layout()
    QApplication.processEvents()
    home = _routes(view)
    _enter(view, "Power_sub")
    # Auto Layout re-derives the file's scale, so compare the shape (the
    # number of points per link), not pixels.
    assert ({k: len(v) for k, v in _routes(view).items()}
            == {k: len(v) for k, v in before.items()})
    assert win._link_routing_combo.currentData() == "straight"
    view.go_up()
    QApplication.processEvents()
    assert _routes(view) == home
    assert win._link_routing_combo.currentData() == "orthogonal"


def test_style_is_saved_with_the_file(win, tmp_path):
    from raptor.core.fileio.rbds import load_rbds, save_rbds
    view = win._diagram_dock.view
    _pick(win, "angled")
    assert win._dirty
    view.write_back_to_rbd(view._rbd_ref)
    path = tmp_path / "m.rbds"
    save_rbds(view._rbd_ref, str(path))
    assert load_rbds(str(path)).link_styles == {"0": "angled"}
