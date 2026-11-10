"""Dragging a dock out by its title bar on Wayland.

Asking the compositor for a system move before the freshly floated window is
configured is a fatal protocol error ("xdg_toplevel: surface has not been
configured yet") that kills the whole app, so the request must wait for the
window to be exposed.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDockWidget  # noqa: E402

from raptor.gui.ui.dock_float_mixin import DockFloatMixin  # noqa: E402
from raptor.gui.ui.dock_title_widgets import _DockTitleBar  # noqa: E402


class _Handle:
    def __init__(self, exposed):
        self.exposed, self.moves = exposed, 0

    def isExposed(self):
        return self.exposed

    def startSystemMove(self):
        self.moves += 1
        return True


class _Dock:
    def __init__(self, handle):
        self._h = handle

    def windowHandle(self):
        return self._h


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_system_move_is_not_requested_before_the_window_is_exposed():
    h = _Handle(exposed=False)
    assert DockFloatMixin._start_dock_system_move(None, _Dock(h)) is False
    assert h.moves == 0                       # never asked: asking is the crash
    h.exposed = True
    assert DockFloatMixin._start_dock_system_move(None, _Dock(h)) is True
    assert h.moves == 1


def test_system_move_without_a_window_handle_is_refused():
    assert DockFloatMixin._start_dock_system_move(None, _Dock(None)) is False


class _Window:
    """Just the calls the title bar makes; the handle becomes exposed later."""

    def __init__(self, handle):
        self.handle = handle
        self.begun = 0

    def _begin_dock_titlebar_drag(self, dock, press):
        self.begun += 1

    def _system_move_supported(self):
        return True

    def _start_dock_system_move(self, dock):
        return DockFloatMixin._start_dock_system_move(self, _Dock(self.handle))

    def _finish_dock_titlebar_drag(self, dock, pos):
        pass


class _Event:
    def __init__(self, pos):
        self._p = pos
        self.accepted = False

    def button(self):
        return Qt.MouseButton.LeftButton

    def buttons(self):
        return Qt.MouseButton.LeftButton

    def globalPosition(self):
        from PySide6.QtCore import QPointF
        return QPointF(self._p)

    def accept(self):
        self.accepted = True


def test_title_bar_retries_the_system_move_until_the_window_is_exposed(qapp):
    handle = _Handle(exposed=False)
    win = _Window(handle)
    dock = QDockWidget()
    bar = _DockTitleBar(dock, win)
    bar.mousePressEvent(_Event(QPoint(10, 10)))
    bar.mouseMoveEvent(_Event(QPoint(200, 200)))     # past the drag threshold
    assert win.begun == 1 and handle.moves == 0      # floated, not yet configured
    bar.mouseMoveEvent(_Event(QPoint(210, 210)))
    assert handle.moves == 0                         # still not exposed: still waiting
    handle.exposed = True
    ev = _Event(QPoint(220, 220))
    bar.mouseMoveEvent(ev)
    assert handle.moves == 1 and ev.accepted         # handed to the compositor once
    assert bar._press_global is None                 # drag state released: not asked again
