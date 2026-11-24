"""A layout that wraps its widgets onto further rows when they do not fit.

The Charts page's tool row holds up to a dozen buttons; in a narrow dock
(and in a language with longer words) a plain row squeezed each button until
its text read "E...a".  `addStretch` still pushes what follows to the right
edge of the row it lands on, as in a box layout."""
from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QSpacerItem, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    # QLayout interface -------------------------------------------------

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt name
        self._items.append(item)

    def addStretch(self, _factor: int = 1) -> None:  # noqa: N802 - box-layout name
        self._items.append(QSpacerItem(
            0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

    def setSpacing(self, spacing: int) -> None:  # noqa: N802 - Qt name
        self._gap = spacing
        self.invalidate()

    def spacing(self) -> int:
        return self._gap

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt name
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt name
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt name
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt name
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt name
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt name
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt name
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt name
        size = QSize(0, 0)
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # Layout ------------------------------------------------------------

    def _rows(self, avail: int) -> list[list[QLayoutItem]]:
        rows: list[list[QLayoutItem]] = [[]]
        used = 0
        for item in self._items:
            if item.spacerItem() is not None:   # (a spacer reports itself empty)
                rows[-1].append(item)
                continue
            if item.isEmpty():
                continue
            width = item.sizeHint().width()
            need = width + (self._gap if used else 0)
            if used and used + need > avail:
                rows.append([])
                used, need = 0, width
            rows[-1].append(item)
            used += need
        return [row for row in rows if any(i.spacerItem() is None for i in row)]

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        """Lay the rows out in `rect` (or only measure); the height used."""
        m: QMargins = self.contentsMargins()
        inner = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        y = inner.y()
        rows = self._rows(inner.width())
        for row in rows:
            widgets = [i for i in row if i.spacerItem() is None]
            widths = [min(i.sizeHint().width(), inner.width()) for i in widgets]
            height = max((i.sizeHint().height() for i in widgets), default=0)
            slack = max(0, inner.width() - sum(widths)
                        - self._gap * (len(widgets) - 1))
            spacers = sum(1 for i in row if i.spacerItem() is not None)
            x, k = inner.x(), 0
            for item in row:
                if item.spacerItem() is not None:
                    x += slack // spacers
                    continue
                if apply:
                    item.setGeometry(QRect(QPoint(x, y), QSize(widths[k], height)))
                x += widths[k] + self._gap
                k += 1
            y += height + self._gap
        body = (y - self._gap - inner.y()) if rows else 0
        return body + m.top() + m.bottom()
