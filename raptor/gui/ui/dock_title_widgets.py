"""The two custom widgets the dock chrome swaps in for Qt's own title bar.

`_DockTitleBar` is the live title bar (it also carries the drag-to-float gesture);
`_DockStub` is the rotated strip a closed side dock collapses to. Both are plain
widgets that call back into the main window, so they live apart from the mixin that
decides when to use them (`dock_chrome.DockSlotChrome`).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QDockWidget, QWidget

from raptor.gui.resources.design_tokens import active_colour


class _DockTitleBar(QWidget):
    """The custom dock title bar, which also carries the drag gesture.

    `QDockWidget.setTitleBarWidget()` replaces Qt's own title bar
    outright, and with it the built-in drag-to-move/float handling:
    accepting mouse presses here without forwarding them left the
    panel impossible to detach by dragging. Reproduce that one gesture
    (drag = float, then move the floating window; double-click = toggle
    float) and leave the title-bar buttons to their own widgets.
    """

    def __init__(self, dock: QDockWidget, window):
        super().__init__()
        self._dock = dock
        self._window = window
        self._press_global = None
        self._dragging = False
        self._drag_offset = None
        self._awaiting_system_move = False

    def _clear_drag(self) -> None:
        self._press_global = None
        self._dragging = False
        self._drag_offset = None
        self._awaiting_system_move = False

    def _try_system_move(self, event) -> bool:
        """Hand the drag to the compositor if it will take it now; True when it did."""
        if not self._window._start_dock_system_move(self._dock):
            return False
        self._clear_drag()
        event.accept()
        return True

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._dragging = False
            self._drag_offset = None
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_global is None:
            super().mouseMoveEvent(event)
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            # A lost release (window switch, popup, remote desktop) used
            # to leave the title bar stuck in drag state: the next hover
            # without a button moved the window.
            self._clear_drag()
            super().mouseMoveEvent(event)
            return
        pos = event.globalPosition().toPoint()
        if not self._dragging:
            if (pos - self._press_global).manhattanLength() \
                    < QApplication.startDragDistance():
                return
            self._dragging = True
            self._window._begin_dock_titlebar_drag(
                self._dock, self._press_global)
            # Wayland does not let a client position a top-level window
            # with move(); ask the compositor for a system move instead,
            # so the dock can cross screens and be placed freely. A dock
            # floated by this very drag is not configured yet, so the
            # request is retried on the following mouse moves.
            self._awaiting_system_move = self._window._system_move_supported()
            if self._awaiting_system_move and self._try_system_move(event):
                return
            # The dock may have just been re-parented into its own
            # floating window. Anchor further movement to where that
            # window now sits so it does not jump on the next event.
            self._drag_offset = self._press_global - self._dock.pos()
        if self._awaiting_system_move and self._try_system_move(event):
            return
        if self._drag_offset is not None and self._dock.isFloating():
            self._dock.move(pos - self._drag_offset)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._window._finish_dock_titlebar_drag(
                self._dock, event.globalPosition().toPoint())
        self._clear_drag()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._clear_drag()
            self._window._toggle_dock_floating(self._dock)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event) -> None:
        # While the button is held the pointer must be allowed to leave
        # the title bar (that is the drag). Without the button it is a
        # stale press state.
        if not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
            self._clear_drag()
        super().leaveEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.WindowDeactivate:
            self._clear_drag()
        return super().event(event)


class _DockStub(QWidget):
    """Vertical 'closed dock' stub: a ~24px strip in the dock's old slot
    showing the dock's name rotated to read facing the user. Clicking
    it restores the dock."""

    clicked = Signal()

    def __init__(self, text: str, angle: float, parent=None):
        super().__init__(parent)
        self._text = text
        self._angle = angle
        self.setToolTip(f"{text} — click to restore this panel")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()

    def paintEvent(self, event) -> None:
        c = active_colour()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(c["group_header"]))
        p.setPen(QColor(c["text"]))
        f = self.font()
        f.setBold(True)
        f.setPointSize(10)
        p.setFont(f)
        p.translate(self.width() / 2, self.height() / 2)
        p.rotate(self._angle)
        fm = p.fontMetrics()
        p.drawText(QRectF(-fm.horizontalAdvance(self._text) / 2, -fm.height() / 2,
                          fm.horizontalAdvance(self._text), fm.height()),
                   Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()
