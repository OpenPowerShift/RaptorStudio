"""Rebuilding a panel must not flash windows at the corner of the screen.

Toggling an analysis (Weak Link, Cost, Capacity) refreshes the Inspector's
System Config tab, which used to unparent six still-shown group boxes with
`setParent(None)`. Left parentless and not explicitly hidden, Qt mapped them
as top-level windows at (0, 0): the "pile of transient dialogs" that flashed
up on the Run tab. Nothing headless ever saw it because no `exec()` was
involved, so this watches for any top-level widget being SHOWN instead.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QMainWindow

MODEL = "tests/golden/TwoBlockRedundancy/input.rbd"

pytestmark = pytest.mark.gui


class _Watcher(QObject):
    """Records every top-level widget that is shown other than the
    application's own main window."""

    def __init__(self):
        super().__init__()
        self.shown: list[str] = []

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and hasattr(obj, "isWindow") \
                and obj.isWindow() and not isinstance(obj, QMainWindow):
            title = obj.title() if hasattr(obj, "title") else ""
            self.shown.append(f"{type(obj).__name__}({title!r})")
        return False


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._load_into_app(load_rbd(MODEL), MODEL)
    qapp.processEvents()
    yield w
    w._dirty = False
    w.close()


@pytest.fixture
def watcher(qapp):
    f = _Watcher()
    qapp.installEventFilter(f)
    yield f
    qapp.removeEventFilter(f)


@pytest.mark.parametrize("via", ["ribbon", "run_panel"])
@pytest.mark.parametrize("flag,key", [("weakAnalysis", "weak"),
                                      ("costAnalysis", "cost"),
                                      ("capacityAnalysis", "capacity")])
def test_toggling_an_analysis_shows_no_stray_window(win, watcher, qapp,
                                                    flag, key, via):
    control = (win._analysis_actions[flag] if via == "ribbon"
               else win._run_panel._analysis_checks[key])
    for _ in range(2):                       # on, then off
        watcher.shown.clear()
        control.trigger() if via == "ribbon" else control.toggle()
        qapp.processEvents()
        assert watcher.shown == []


def test_the_system_config_tab_can_be_rebuilt_repeatedly(win, watcher, qapp):
    for _ in range(3):
        win._refresh_inspector()
        qapp.processEvents()
    assert watcher.shown == []


@pytest.mark.parametrize("model", ["EventDepProbe", "HierarchyExample",
                                   "TwoBlockRedundancy"])
def test_selecting_each_kind_of_element_shows_no_stray_window(
        qapp, watcher, model):
    """Every selection rebuilds the Inspector's tabs through the same
    clear-and-rebuild path."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    path = f"tests/golden/{model}/input.rbd"
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._load_into_app(load_rbd(path), path)
    qapp.processEvents()
    v = w._diagram_dock.view
    watcher.shown.clear()
    for items in (v._block_items, v._node_items, v._event_items,
                  v._hier_items):
        for it in list(items.values()):
            v._scene.clearSelection()
            it.setSelected(True)
            qapp.processEvents()
    v._scene.clearSelection()
    qapp.processEvents()
    w._dirty = False
    w.close()
    assert watcher.shown == []
