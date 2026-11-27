"""The Colour by Tag legend: which colour is which tag, on this level.

A small panel in the canvas's top-left corner, listing the tags shown on
the current level with their colour and how many elements carry each,
most-used first. With many tags a search box filters the list, which
scrolls past ten rows. Clicking a tag shows it in the Explorer (the same
dim-the-rest view the Explorer's tag box gives).
"""
from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
    QWidget,
)

from raptor.gui import i18n
from raptor.gui.ui.tags_panel import swatch

#: Past this many tags the legend offers a search box.
SEARCH_FROM = 8
ROW_H = 20
MAX_ROWS = 10


class TagLegend(QFrame):
    """Build with the canvas view as parent, then `set_rows`."""

    tag_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TagLegend")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 6)
        lay.setSpacing(3)
        self.title = QLabel(i18n.tr("Tags on this level"))
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        lay.addWidget(self.title)
        self.search = QLineEdit()
        self.search.setPlaceholderText(i18n.tr("Find a tag…"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(
            lambda item: self.tag_clicked.emit(str(item.data(Qt.ItemDataRole.UserRole))))
        lay.addWidget(self.list)
        self.empty = QLabel(i18n.tr("Nothing on this level is tagged."))
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty)
        self.setFixedWidth(200)

    def set_rows(self, rows: Sequence[tuple[str, str, int]]) -> None:
        self.list.clear()
        for tag, colour, count in rows:
            item = QListWidgetItem(swatch([colour]), f"{tag}  ({count})")
            item.setData(Qt.ItemDataRole.UserRole, tag)
            item.setToolTip(i18n.tr("Show the elements tagged %1")
                            .replace("%1", tag))
            self.list.addItem(item)
        self.search.setVisible(len(rows) > SEARCH_FROM)
        self.list.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        row_h = self.list.sizeHintForRow(0) if rows else ROW_H
        self.list.setFixedHeight(
            max(row_h, 1) * max(1, min(len(rows), MAX_ROWS))
            + 2 * self.list.frameWidth() + 2)
        self._filter(self.search.text())
        self.adjustSize()

    def visible_tags(self) -> list[str]:
        return [str(self.list.item(i).data(Qt.ItemDataRole.UserRole))
                for i in range(self.list.count())
                if not self.list.item(i).isHidden()]

    def _filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for i in range(self.list.count()):
            item = self.list.item(i)
            tag = str(item.data(Qt.ItemDataRole.UserRole))
            item.setHidden(bool(needle) and needle not in tag.casefold())
