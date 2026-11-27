"""The Inspector's tag chips: one element's tags, editable in place.

Each tag is a chip (its colour, its name, a remove button); a box after
the chips adds a tag, completing from the model's tags as you type. A new
name makes a new tag. Every change is one `tags_edited(list)` signal with
the element's whole new list, which the main window turns into one
undoable `TagsCommand`.
"""
from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, QPointF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction, QColor, QIcon, QKeyEvent, QPainter, QPixmap, QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QApplication, QCompleter, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QWidget,
)

from raptor.gui import i18n
from raptor.gui.ui.flow_layout import FlowLayout

_DOT = 10   # the colour dot's diameter, in device-independent pixels


def _arrow_icon(colour: str) -> QIcon:
    from raptor.gui.resources.icons import chevron_down_icon
    return chevron_down_icon(colour)


def _dot_icon(colour: str) -> QIcon:
    """A round dot in *colour*; an untagged-colour tag gets a hollow ring."""
    ratio = 2.0
    side = round(_DOT * ratio)
    pix = QPixmap(side, side)
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    fill = QColor(colour) if colour else QColor()
    if fill.isValid():
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
    else:
        p.setPen(QApplication.palette().mid().color())
        p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(_DOT / 2, _DOT / 2), _DOT / 2 - 0.75, _DOT / 2 - 0.75)
    p.end()
    return QIcon(pix)


class TagChips(QWidget):
    """Chips for *tags*; `known` feeds the picker, `colours` the swatches.

    The add box lists the model's other tags when it is clicked, when Down
    is pressed in it, or from its drop-down arrow; typing narrows the list.
    Plain keyboard focus opens nothing, so Tab through the Inspector stays
    quiet."""

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
            "#TagChip { border: 1px solid palette(mid); border-radius: 10px; }"
            "#TagChipRemove { padding: 0px; margin: 0px; border: none;"
            " background: transparent; min-width: 0px; min-height: 0px;"
            " }"
            "#TagChipRemove:hover { background: palette(midlight);"
            " border-radius: 7px; }")
        self._flow = FlowLayout(self, spacing=4)
        self.add_edit = QLineEdit()
        self.add_edit.setObjectName("TagAdd")
        self.add_edit.setPlaceholderText(i18n.tr("Add tag…"))
        self.add_edit.setAccessibleName(i18n.tr("Add a tag to this element"))
        self.add_edit.setMinimumWidth(110)
        self.add_edit.setMaximumWidth(180)
        self._height = self.add_edit.sizeHint().height()
        for tag in self._tags:
            self._flow.addWidget(self._chip(tag))
        others = [t for t in dict.fromkeys(known) if t not in self._tags]
        self._others = others
        self.add_edit.setToolTip(i18n.tr(
            "Type a tag and press Enter. An existing tag completes as you "
            "type; a new name makes a new tag."))
        self._model = QStandardItemModel(self.add_edit)
        for tag in others:
            item = QStandardItem(_dot_icon(self._colours.get(tag, "")), tag)
            if self._describe.get(tag):
                item.setToolTip(self._describe[tag])
            self._model.appendRow(item)
        self._completer = QCompleter(self._model, self.add_edit)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.add_edit.setCompleter(self._completer)
        popup = self._completer.popup()
        if popup is not None:
            popup.setIconSize(QSize(_DOT, _DOT))
        self.add_edit.returnPressed.connect(self._add_typed)
        self._completer.activated.connect(self._add)
        self.arrow_action: QAction | None = None
        if others:
            from raptor.gui.resources.design_tokens import active_colour
            self.arrow_action = QAction(
                _arrow_icon(active_colour()["text"]),
                i18n.tr("Show the model's tags"), self.add_edit)
            self.arrow_action.setToolTip(i18n.tr("Show the model's tags"))
            self.arrow_action.setIconText(i18n.tr("Show the model's tags"))
            self.add_edit.addAction(
                self.arrow_action, QLineEdit.ActionPosition.TrailingPosition)
            self.arrow_action.triggered.connect(self.show_tags)
            self.add_edit.installEventFilter(self)
        self._flow.addWidget(self.add_edit)

    def show_tags(self) -> None:
        """Open the popup: every tag not on this element, narrowed by what
        is typed."""
        if not self._others:
            return
        self.add_edit.setFocus()
        self._completer.setCompletionPrefix(self.add_edit.text())
        self._completer.complete()

    def _popup_visible(self) -> bool:
        popup = self._completer.popup()
        return popup is not None and popup.isVisible()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.add_edit and self._others:
            kind = event.type()
            if (kind == QEvent.Type.MouseButtonPress
                    and not self.add_edit.text()
                    and self.add_edit.isEnabled()):
                self.show_tags()
            elif (kind == QEvent.Type.KeyPress
                  and isinstance(event, QKeyEvent)
                  and event.key() == Qt.Key.Key_Down
                  and not self._popup_visible()):
                self.show_tags()
                return True
        return super().eventFilter(obj, event)

    def tags(self) -> list[str]:
        return list(self._tags)

    def _chip(self, tag: str) -> QFrame:
        chip = QFrame()
        chip.setObjectName("TagChip")
        lay = QHBoxLayout(chip)
        chip.setFixedHeight(self._height)
        lay.setContentsMargins(7, 0, 4, 0)
        lay.setSpacing(4)
        colour = QLabel()
        colour.setPixmap(_dot_icon(self._colours.get(tag, "")).pixmap(
            QSize(_DOT, _DOT)))
        lay.addWidget(colour)
        name = QLabel(tag)
        name.setProperty("raptor_ignore_i18n", True)   # a user's own word
        if self._describe.get(tag):
            chip.setToolTip(self._describe[tag])
        lay.addWidget(name)
        # an icon, not a "×" glyph: the glyph sat above the text's middle
        from raptor.gui.resources.design_tokens import active_colour
        from raptor.gui.resources.icons import close_x_icon
        remove = QPushButton()
        remove.setIcon(close_x_icon(active_colour()["text"]))
        remove.setIconSize(QSize(10, 10))
        remove.setObjectName("TagChipRemove")
        remove.setFlat(True)
        remove.setFixedSize(14, 14)
        remove.setToolTip(i18n.tr("Remove the tag %1 from this element")
                          .replace("%1", tag))
        remove.setAccessibleName(i18n.tr("Remove tag %1").replace("%1", tag))
        remove.clicked.connect(lambda _c=False, t=tag: self._remove(t))
        lay.addWidget(remove, 0, Qt.AlignmentFlag.AlignVCenter)
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
