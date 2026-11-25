"""The Inspector's tag chips: one element's tags, editable in place.

Each tag is a chip (its colour, its name, a remove button); a box after
the chips adds a tag, completing from the model's tags as you type. A new
name makes a new tag. Every change is one `tags_edited(list)` signal with
the element's whole new list, which the main window turns into one
undoable `TagsCommand`.
"""
from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, QStringListModel, Qt, Signal
from PySide6.QtWidgets import (
    QCompleter, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget,
)

from raptor.gui import i18n
from raptor.gui.ui.flow_layout import FlowLayout
from raptor.gui.ui.tags_dialog import swatch


class TagChips(QWidget):
    """Chips for *tags*; `known` feeds the completer, `colours` the swatches."""

    tags_edited = Signal(list)

    def __init__(self, tags: Sequence[str], known: Sequence[str],
                 colours: dict[str, str], parent: QWidget | None = None,
                 *, describe: dict[str, str] | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TagChips")
        self._tags = list(tags)
        self._colours = dict(colours)
        self._describe = dict(describe or {})
        # the app's button style pads a button until an 18 px "x" vanishes
        self.setStyleSheet(
            "#TagChip { border: 1px solid palette(mid); border-radius: 9px; }"
            "#TagChipRemove { padding: 0px; margin: 0px; border: none;"
            " min-width: 0px; min-height: 0px; font-weight: bold; }"
            "#TagChipRemove:hover { background: palette(midlight);"
            " border-radius: 8px; }")
        self._flow = FlowLayout(self, spacing=4)
        for tag in self._tags:
            self._flow.addWidget(self._chip(tag))
        self.add_edit = QLineEdit()
        self.add_edit.setObjectName("TagAdd")
        self.add_edit.setPlaceholderText(i18n.tr("Add tag…"))
        self.add_edit.setAccessibleName(i18n.tr("Add a tag to this element"))
        self.add_edit.setToolTip(i18n.tr(
            "Type a tag and press Enter. An existing tag completes as you "
            "type; a new name makes a new tag."))
        self.add_edit.setMinimumWidth(110)
        self.add_edit.setMaximumWidth(180)
        completer = QCompleter(QStringListModel(
            [t for t in known if t not in self._tags]), self.add_edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.add_edit.setCompleter(completer)
        self.add_edit.returnPressed.connect(self._add_typed)
        completer.activated.connect(self._add)
        self._flow.addWidget(self.add_edit)

    def tags(self) -> list[str]:
        return list(self._tags)

    def _chip(self, tag: str) -> QFrame:
        chip = QFrame()
        chip.setObjectName("TagChip")
        lay = QHBoxLayout(chip)
        lay.setContentsMargins(4, 1, 1, 1)
        lay.setSpacing(3)
        colour = QLabel()
        colour.setPixmap(swatch([self._colours.get(tag, "")], 10).pixmap(
            QSize(10, 10)))
        lay.addWidget(colour)
        name = QLabel(tag)
        name.setProperty("raptor_ignore_i18n", True)   # a user's own word
        if self._describe.get(tag):
            chip.setToolTip(self._describe[tag])
        lay.addWidget(name)
        remove = QPushButton("×")
        remove.setObjectName("TagChipRemove")
        remove.setFlat(True)
        remove.setFixedSize(16, 16)
        remove.setToolTip(i18n.tr("Remove the tag %1 from this element")
                          .replace("%1", tag))
        remove.setAccessibleName(i18n.tr("Remove tag %1").replace("%1", tag))
        remove.clicked.connect(lambda _c=False, t=tag: self._remove(t))
        lay.addWidget(remove)
        return chip

    def _add_typed(self) -> None:
        self._add(self.add_edit.text())

    def _add(self, text: str) -> None:
        from raptor.core.tags import clean_tag
        tag = clean_tag(text)
        self.add_edit.clear()
        if not tag or tag in self._tags:
            return
        self.tags_edited.emit([*self._tags, tag])

    def _remove(self, tag: str) -> None:
        self.tags_edited.emit([t for t in self._tags if t != tag])
