"""The event log as a Qt model, not a widget per line.

The Results dock's Event Log tab used to build one `QTreeWidgetItem` per
log line, synchronously. A long mission-time run has 100k+ lines: about
4 s frozen to display them, then 0.3-0.4 s on every keystroke in the
filter box (it walked every item and read five columns each).

A model holds the parsed rows as plain tuples and Qt asks it only for the
rows that are on screen, so display cost no longer depends on the log
length. Filtering happens inside the model over a precomputed lower-case
search string per row, so it is one list comprehension rather than a
Python callback per row from a proxy model.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QObject,
                            QPersistentModelIndex, Qt)
from PySide6.QtGui import QColor

# The five fields and their split are owned by `raptor.api.tables` --
# Qt-free, because the export path needs them too and this module is not.
# Importing rather than redefining is what keeps the spreadsheet export
# and the on-screen log from ever disagreeing about a line's shape.
from raptor.api.tables import (EVENT_LOG_HEADERS as HEADERS,
                              parse_event_log_line as parse_log_line)

#: Shown when hovering any column header.
HEADER_TIP = ("Click a Block or Node row to select it on the diagram.\n"
              "Colours: green = working · amber = degraded/standby · "
              "red = failed/down.")

Row = tuple[str, str, str, str, str]

#: A search string cannot contain this, so a needle can never match
#: across two columns.
_SEP = "\x00"


class EventLogModel(QAbstractTableModel):
    """Read-only table of parsed event-log rows with a live text filter."""

    def __init__(self, colour_for_state: Callable[[str], QColor | None],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._colour_for_state = colour_for_state
        self._rows: list[Row] = []
        self._search: list[str] = []
        #: Indices into `_rows` that pass the filter; None = all of them.
        self._visible: list[int] | None = None
        self._needle = ""

    # -- loading ------------------------------------------------------

    def set_lines(self, lines: Iterable[str]) -> None:
        """Replace the contents. The current filter is kept and applied."""
        rows = [parse_log_line(line) for line in lines]
        self.beginResetModel()
        self._rows = rows
        self._search = [_SEP.join(r).lower() for r in rows]
        self._visible = self._compute_visible(self._needle)
        self.endResetModel()

    def clear(self) -> None:
        self.set_lines(())

    def set_filter(self, text: str) -> None:
        """Show only rows where some column contains `text`
        (case-insensitive). Empty text shows everything."""
        # The separator is stripped so a pasted NUL cannot match across
        # two columns.
        needle = text.replace(_SEP, "").strip().lower()
        if needle == self._needle:
            return
        self.beginResetModel()
        self._needle = needle
        self._visible = self._compute_visible(needle)
        self.endResetModel()

    def _compute_visible(self, needle: str) -> list[int] | None:
        if not needle:
            return None
        return [i for i, s in enumerate(self._search) if needle in s]

    # -- reading ------------------------------------------------------

    @property
    def total(self) -> int:
        """All rows, ignoring the filter."""
        return len(self._rows)

    def all_rows(self) -> list[Row]:
        return list(self._rows)

    def visible_rows(self) -> list[Row]:
        """The rows currently shown, in order."""
        if self._visible is None:
            return list(self._rows)
        return [self._rows[i] for i in self._visible]

    def _source_row(self, view_row: int) -> int:
        return view_row if self._visible is None else self._visible[view_row]

    # -- QAbstractTableModel ------------------------------------------

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex
                 = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._rows) if self._visible is None \
            else len(self._visible)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex
                    = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(HEADERS)

    def data(self, index: QModelIndex | QPersistentModelIndex,
             role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < self.rowCount()):
            return None
        row = self._rows[self._source_row(index.row())]
        col = index.column()
        system = row[1].strip().lower() == "system"
        if role == Qt.ItemDataRole.DisplayRole:
            if system:
                # A System row's "element" is its event kind (State_change,
                # Permanently_Down, ...), not an element name. Put it in
                # Detail so filtering/reading the Element column is honest.
                if col == 2:
                    return "—"
                if col == 3:
                    return "—"
                if col == 4:
                    extra = f" {row[4]}" if row[4] else ""
                    return f"{row[2]}: {row[3]}{extra}"
            return row[col]
        if role == Qt.ItemDataRole.ForegroundRole and col in (0, 3):
            return self._colour_for_state(row[3])
        if role == Qt.ItemDataRole.UserRole and col == 0 and not system:
            # (kind, element name): what a click on the row selects.
            return (row[1].lower(), row[2])
        return None

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation != Qt.Orientation.Horizontal \
                or not (0 <= section < len(HEADERS)):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        if role == Qt.ItemDataRole.ToolTipRole:
            return HEADER_TIP
        return None
