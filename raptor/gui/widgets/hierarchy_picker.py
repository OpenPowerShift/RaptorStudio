"""A combo box for choosing "the whole model, or one hierarchy".

A model can have hundreds of hierarchies. A plain combo box listed them in
a popup as tall as the screen with no way to search, and "Whole model" was
just its first row. This one:

* puts the whole-model entry first, with a home icon and a divider under it;
* shows a short list that scrolls (`MAX_VISIBLE` rows), whatever the
  platform style would otherwise do;
* can be typed into: the list narrows to the hierarchies whose name
  contains the text.

The combo's data API is unchanged (`currentData`, `findData`,
`currentIndexChanged`), so it replaces a `QComboBox` in place.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QCompleter, QToolButton

#: Rows shown before the list scrolls.
MAX_VISIBLE = 12

#: Forces a list popup that honours `setMaxVisibleItems`. Some platform
#: styles show a combo's popup as a menu the height of the screen.
POPUP_STYLE = "combobox-popup: 0;"


def picker_style(extra: str = "") -> str:
    """The stylesheet a picker needs, plus `extra` QComboBox properties.
    Anything that restyles a picker must go through this, or the short
    list is lost."""
    return f"QComboBox {{ {POPUP_STYLE} {extra} }}"


class HierarchyPicker(QComboBox):
    """See the module docstring."""

    def __init__(self, parent: Any = None, *,
                 home_text: str = "Whole model", home_data: Any = 0) -> None:
        super().__init__(parent)
        self._home_text = home_text
        self._home_data = home_data
        self.setMaxVisibleItems(MAX_VISIBLE)
        self.setStyleSheet(picker_style())
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        completer = self.completer()
        if completer is not None:
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setCompletionMode(
                QCompleter.CompletionMode.PopupCompletion)
            completer.setMaxVisibleItems(MAX_VISIBLE)
        edit = self.lineEdit()
        if edit is not None:
            edit.setPlaceholderText("Type to search…")
            # Text that names no entry is not a choice: put the current
            # entry's name back when the user leaves the box.
            edit.editingFinished.connect(self._restore_text)
        self.set_hierarchies([])
        from raptor.gui.resources.design_tokens import ThemeManager
        ThemeManager.instance().on_changed(self._retheme)

    def _retheme(self, _dark: bool = False) -> None:
        """Redraw the home entry's icon in the new theme's text colour."""
        if self.count():
            self.setItemIcon(0, self._home_icon())

    def set_hierarchies(self, rows: list[tuple[Any, str]]) -> None:
        """Refill: the home entry, a divider, then `rows` as (data, label).
        The current choice is kept when it is still there; signals are the
        caller's to block."""
        keep = self.currentData()
        self.clear()
        self.addItem(self._home_icon(), self._home_text, self._home_data)
        if rows:
            self.insertSeparator(1)
            for data, label in rows:
                self.addItem(label, data)
        index = self.findData(keep) if keep is not None else 0
        self.setCurrentIndex(max(0, index))

    def go_home(self) -> None:
        self.setCurrentIndex(0)

    def is_home(self) -> bool:
        return self.currentIndex() <= 0

    def _restore_text(self) -> None:
        index = self.currentIndex()
        typed = self.currentText().strip().casefold()
        if index >= 0 and typed == self.itemText(index).strip().casefold():
            return
        for i in range(self.count()):
            name = self.itemText(i).strip()
            if name and name.casefold() == typed:      # a divider has no name
                self.setCurrentIndex(i)
                return
        if index >= 0:
            self.setEditText(self.itemText(index))

    @staticmethod
    def _home_icon():
        from raptor.gui.resources import icons
        from raptor.gui.resources.design_tokens import active_colour
        return icons.home_icon(active_colour()["text"])


class HomeButton(QToolButton):
    """A small button that returns a picker to its whole-model entry, to sit
    beside it. Enabled only while a hierarchy is chosen. Its icon is drawn
    in the theme's text colour, so it is redrawn when the theme changes:
    drawn once, it was dark on dark after switching to the dark theme."""

    def __init__(self, picker: HierarchyPicker,
                 tooltip: str = "Back to the whole model") -> None:
        super().__init__()
        self._picker = picker
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setAutoRaise(True)
        self.clicked.connect(picker.go_home)
        picker.currentIndexChanged.connect(self._sync)
        self._retheme()
        self._sync()
        from raptor.gui.resources.design_tokens import ThemeManager
        ThemeManager.instance().on_changed(self._retheme)

    def _sync(self, _index: int = 0) -> None:
        self.setEnabled(self._picker.isEnabled()
                        and not self._picker.is_home())

    def _retheme(self, _dark: bool = False) -> None:
        self.setIcon(HierarchyPicker._home_icon())


def home_button(picker: HierarchyPicker,
                tooltip: str = "Back to the whole model") -> HomeButton:
    return HomeButton(picker, tooltip)
