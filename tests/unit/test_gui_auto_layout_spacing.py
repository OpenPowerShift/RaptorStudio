"""The Auto Layout spacing factor sets the drawn gap on every level (#155)."""
from __future__ import annotations

import itertools

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
    w._dirty = False
    w.close()


def _gaps(view) -> list[float]:
    """Edge-to-edge gap between each pair of adjacent columns."""
    cols: dict[int, list] = {}
    for table in (view._block_items, view._node_items, view._hier_items):
        for it in table.values():
            r = it.sceneBoundingRect()
            cols.setdefault(round(r.center().x() / 20), []).append(r)
    keys = sorted(cols)
    return [min(r.left() for r in cols[b]) - max(r.right() for r in cols[a])
            for a, b in itertools.pairwise(keys)]


def _layout_every_level(win, spacing: float) -> dict[str, float]:
    from raptor.gui.ui import settings
    settings.set_value("layout/spacing", spacing)
    view = win._diagram_dock.view
    levels = [("Home", 0)] + [(h.name, i) for i, h in view.hiers.items()]
    for _ in range(2):
        for _name, hid in levels:
            while view._current_parent:
                view.go_up()
            if hid:
                view.enter_level(hid)
            QApplication.processEvents()
            view.auto_layout()
            QApplication.processEvents()
    out = {}
    for name, hid in levels:
        while view._current_parent:
            view.go_up()
        if hid:
            view.enter_level(hid)
        QApplication.processEvents()
        out[name] = sum(_gaps(view)) / len(_gaps(view))
    return out


def test_gap_grows_with_the_spacing_factor_on_every_level(win):
    small = _layout_every_level(win, 0.5)
    nominal = _layout_every_level(win, 1.0)
    large = _layout_every_level(win, 2.0)
    for level in nominal:
        assert small[level] < nominal[level] < large[level], level
