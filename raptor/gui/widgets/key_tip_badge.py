"""The little letter chip that Alt shows over a ribbon tab or button."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLabel, QWidget


class KeyTipBadge(QLabel):
    """A letter drawn over `target`, as a child of `window` so it floats
    above the ribbon without taking part in its layout (the ribbon does not
    re-flow, nothing is truncated). Mouse-transparent."""

    def __init__(self, text: str, target: QWidget, window: QWidget,
                 anchor: QPoint | None = None):
        super().__init__(text, window)
        self.target = target
        self._window = window
        #: Where the badge's centre goes, in the target's coordinates; None =
        #: bottom centre of the whole widget.
        self.anchor = anchor
        self.setObjectName("KeyTipBadge")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "QLabel#KeyTipBadge { background: #1f2933; color: #ffffff;"
            " border: 1px solid #f5c542; border-radius: 3px;"
            " font-weight: 700; font-size: 11px; padding: 0px 4px; }")
        self.adjustSize()
        self.setMinimumWidth(self.height())
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()

    def place(self) -> None:
        """Centre on the anchor (default: hanging off the target's lower
        edge), kept inside the window."""
        win = self._window
        t = self.target
        at = self.anchor or QPoint(t.width() // 2, t.height())
        pt = t.mapTo(win, at)
        x = max(0, min(pt.x() - self.width() // 2, win.width() - self.width()))
        y = max(0, min(pt.y() - self.height() // 2,
                       win.height() - self.height()))
        self.move(x, y)
