"""Auto Layout: undo restores the level's page flow; no spacing overlaps;
a partial selection says so."""
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
    settings.set_value("layout/spacing", 1.0)
    settings.set_value("layout/direction", "LR")
    w._dirty = False
    w.close()


def _overlaps(view) -> int:
    rects = [i.sceneBoundingRect() for t in (view._block_items, view._node_items,
                                             view._hier_items) for i in t.values()]
    n = 0
    for a in range(len(rects)):
        for b in range(a + 1, len(rects)):
            x = rects[a].intersected(rects[b])
            n += x.width() > 1 and x.height() > 1
    return n


def _go(view, hid):
    while view._current_parent:
        view.go_up()
    if hid:
        view.enter_level(hid)
    QApplication.processEvents()


def test_undo_and_redo_restore_the_levels_page_flow(win):
    from raptor.gui.ui import settings
    from raptor.gui.ui.diagram_items import flow_direction
    view = win._diagram_dock.view
    hid = next(iter(view.hiers))
    _go(view, hid)
    assert view.level_flow_direction() == "auto"
    settings.set_value("layout/direction", "TB")
    view.auto_layout()
    QApplication.processEvents()
    assert view.level_flow_direction() == "TB"
    view._stack.undo()
    QApplication.processEvents()
    assert view.level_flow_direction() == "auto"
    assert str(hid) not in win._rbd.flow_directions
    assert flow_direction() == "auto"
    view._stack.redo()
    QApplication.processEvents()
    assert view.level_flow_direction() == "TB"
    assert flow_direction() == "TB"


def test_no_spacing_factor_makes_blocks_overlap_on_any_level(win):
    from raptor.gui.ui import settings
    view = win._diagram_dock.view
    levels = [0] + list(view.hiers)

    def sweep(spacing, which):
        settings.set_value("layout/spacing", spacing)
        for hid in which:
            _go(view, hid)
            view.auto_layout()
            QApplication.processEvents()
    sweep(1.0, levels + levels)
    sweep(0.25, [0])
    sweep(0.25, levels[1:])
    for hid in levels:
        _go(view, hid)
        assert _overlaps(view) == 0, hid


def test_a_partial_selection_says_only_it_was_laid_out(win):
    view = win._diagram_dock.view
    seen = []
    view.status_message_sink = seen.append
    items = [i for t in (view._block_items, view._node_items, view._hier_items)
             for i in t.values()][:2]
    for it in items:
        it.setSelected(True)
    view.auto_layout()
    QApplication.processEvents()
    assert any("only the 2 selected" in m for m in seen)
