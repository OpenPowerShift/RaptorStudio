"""Auto Layout's page-flow direction belongs to the level (#154)."""
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


def _enter(view, name):
    hid = next(i for i, h in view.hiers.items() if h.name == name)
    view.enter_level(hid)
    QApplication.processEvents()


def test_layout_direction_stays_on_the_level_it_was_applied_to(win):
    """Auto Layout top-to-bottom at Home must not make a hierarchy attach
    its links to the top and bottom of nodes."""
    from raptor.gui.ui import settings
    from raptor.gui.ui.diagram_items import flow_direction
    view = win._diagram_dock.view
    _enter(view, "Power_sub")
    before = _routes(view)
    assert flow_direction() == "auto"
    view.go_up()
    QApplication.processEvents()
    settings.set_value("layout/direction", "TB")
    try:
        view.auto_layout()
        QApplication.processEvents()
        assert flow_direction() == "TB"
        _enter(view, "Power_sub")
        assert flow_direction() == "auto"
        assert ({k: len(v) for k, v in _routes(view).items()}
                == {k: len(v) for k, v in before.items()})
        view.go_up()
        QApplication.processEvents()
        assert flow_direction() == "TB"
    finally:
        settings.set_value("layout/direction", "LR")


def test_flow_direction_is_saved_with_the_file(win, tmp_path):
    from raptor.core.fileio.rbds import load_rbds, save_rbds
    from raptor.gui.ui import settings
    view = win._diagram_dock.view
    settings.set_value("layout/direction", "TB")
    try:
        view.auto_layout()
    finally:
        settings.set_value("layout/direction", "LR")
    view.write_back_to_rbd(view._rbd_ref)
    path = tmp_path / "m.rbds"
    save_rbds(view._rbd_ref, str(path))
    assert load_rbds(str(path)).flow_directions == {"0": "TB"}
