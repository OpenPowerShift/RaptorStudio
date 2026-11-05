"""New and Open: a build tool never carries over to another model, and
opening a second window says it is busy at once."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from raptor.core.fileio.rbd import load_rbd  # noqa: E402
from raptor.gui.ui import settings  # noqa: E402
from raptor.gui.ui.main_window import MainWindow  # noqa: E402

pytestmark = pytest.mark.gui
GOLD = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "golden")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), None)
    yield w
    settings.remove("files/open_in")
    for other in qapp.topLevelWidgets():
        if isinstance(other, MainWindow):
            other._dirty = False
            other.close()


def test_new_in_the_same_window_disarms_a_build_tool(qapp, win):
    """Add Node armed, then New: the blank model showed the node cursor
    and the first click placed a node."""
    settings.set_value("files/open_in", "this")
    win._add_node_tool.trigger()
    assert win._diagram_dock.view.tool_mode == "add_node"
    win._dirty = False
    win._new_file()
    v = win._diagram_dock.view
    assert v.tool_mode == "select"
    assert not win._add_node_tool.isChecked()
    assert not v.viewport().testAttribute(
        __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.WidgetAttribute.WA_SetCursor)


def test_a_new_window_says_it_is_busy_and_restores_the_cursor(
        qapp, win, monkeypatch):
    settings.set_value("files/open_in", "new")
    win._current_file = "model.rbd"
    said, cursors = [], []
    monkeypatch.setattr(win._statusbar, "showMessage",
                        lambda text, *a: said.append(text))
    real_init = MainWindow.__init__

    def spy(self, *a, **k):
        cursors.append(QApplication.overrideCursor() is not None)
        real_init(self, *a, **k)
    monkeypatch.setattr(MainWindow, "__init__", spy)
    new = win._spawn_window()
    assert new is not win
    assert said and said[0].startswith("Opening a new window")
    assert cursors == [True]                  # busy while it was built
    assert QApplication.overrideCursor() is None
