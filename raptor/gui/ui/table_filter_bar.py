"""A filter row under a table's header (Excel's "auto filter"), for big tables.

``FilterBar`` is attached to a table with ``FilterBar.attach(table)``. It adds
nothing to the layout: one small edit per column sits in an extra strip at
the bottom of the table's own header, so it lines up with the columns,
follows resizing and scrolling, and costs nothing until it is shown. Each
edit takes a small expression (``raptor.gui.table_filter``: text, wildcards,
``>0.9``, ``5..10``, ``blank``...). Typing is debounced, so a filter pass
runs when the typing pauses, not on every keystroke; a pass hides the rows
that do not match with ``setRowHidden`` and copies no data.

Tables of ``MIN_FILTER_ROWS`` rows or fewer get no filter at all. The strip is
hidden until asked for (Ctrl+F on the table, or the header's right-click
menu). While a filter is active a small "Showing N of M - Clear" note sits at
the table's bottom right.

A table whose rows are not read from its items (the Data Tables read the
model objects behind the rows, so hidden rows never need their cell widgets)
passes its own ``matcher``.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QTableWidget,
    QWidget)

from raptor.gui.table_filter import SYNTAX_HELP, CompiledFilter, compile_filter

#: A table with this many rows or fewer is small enough to read: no filter.
MIN_FILTER_ROWS = 10
DEBOUNCE_MS = 150
_EDIT_H = 24

#: (row, [(column, compiled filter)...]) -> does the row stay visible?
Matcher = Callable[[int, "list[tuple[int, CompiledFilter]]"], bool]


def _widget_text(w: QWidget | None) -> str:
    """The text a cell widget shows (a distribution combo, a parameter field)."""
    if w is None:
        return ""
    for getter in ("currentText", "text"):
        fn = getattr(w, getter, None)
        if callable(fn):
            try:
                return str(fn())
            except TypeError:
                continue
    for child in w.findChildren(QLineEdit):
        return child.text()
    return ""


class FilterBar(QObject):
    """The filter strip of one table. See the module docstring."""

    #: (rows still shown, rows in the table), after every filter pass.
    changed = Signal(int, int)

    @classmethod
    def attach(cls, table: QTableWidget, **kw: Any) -> FilterBar:
        """A bar for `table` (kept alive by the table). Safe to call twice."""
        existing = getattr(table, "_filter_bar", None)
        if isinstance(existing, FilterBar):
            return existing
        bar = cls(table, **kw)
        table._filter_bar = bar      # type: ignore[attr-defined]
        return bar

    def __init__(self, table: QTableWidget, *, matcher: Matcher | None = None,
                 raw_role: int | None = None, min_rows: int = MIN_FILTER_ROWS,
                 debounce_ms: int = DEBOUNCE_MS,
                 filter_columns: set[int] | None = None,
                 row_key: Callable[[int], Any] | None = None) -> None:
        super().__init__(table)
        self._table = table
        self._matcher = matcher
        #: Columns that can be filtered (None = all). The others get a
        #: disabled box: their text is not known without their cell widgets.
        self._filter_columns = filter_columns
        #: Rows whose `row_key` is in `keep_visible` stay shown by a pass the
        #: user did not ask for (a refill after an edit), so an edited row
        #: that stops matching does not vanish under the cursor. Typing in a
        #: filter box clears it: the next pass is the user's.
        self._row_key = row_key
        self.keep_visible: set[Any] = set()
        self._raw_role = raw_role
        self._min_rows = min_rows
        self._editors: list[QLineEdit] = []
        self._shown = False
        self._filters: list[tuple[int, CompiledFilter]] = []
        self._base_h = 0
        self._passes = 0           # filter passes run (the tests count them)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self.apply)

        self._refilter = QTimer(self)     # coalesces a repopulation's signals
        self._refilter.setSingleShot(True)
        self._refilter.setInterval(0)
        self._refilter.timeout.connect(self._settle)

        self._badge = QFrame(table)
        self._badge.setObjectName("FilterBadge")
        self._badge.setStyleSheet(
            "#FilterBadge { background: palette(window); border: 1px solid "
            "palette(mid); border-radius: 4px; }")
        lay = QHBoxLayout(self._badge)
        lay.setContentsMargins(6, 2, 4, 2)
        lay.setSpacing(6)
        self._count = QLabel("")
        clear = QPushButton("Clear")
        clear.setFlat(True)
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self.clear)
        lay.addWidget(self._count)
        lay.addWidget(clear)
        self._badge.hide()

        header = table.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu)
        header.sectionResized.connect(self._place)
        header.sectionMoved.connect(self._place)
        header.geometriesChanged.connect(self._place)
        table.horizontalScrollBar().valueChanged.connect(self._place)
        sc = QShortcut(QKeySequence("Ctrl+F"), table)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self.toggle)
        table.installEventFilter(self)
        model = table.model()
        # Rows added/removed or reordered (a sort) leave the hidden flags on
        # the wrong rows: filter again, once, when the dust settles.
        for sig in (model.rowsInserted, model.rowsRemoved,
                    model.layoutChanged, model.modelReset):
            sig.connect(self._model_changed)
        for sig in (model.columnsInserted, model.columnsRemoved):
            sig.connect(self._columns_changed)

    # ----------------------------------------------------------- availability
    def available(self) -> bool:
        """Whether this table is big enough to be worth a filter."""
        return self._table.rowCount() > self._min_rows

    def is_shown(self) -> bool:
        return self._shown

    def is_active(self) -> bool:
        return any(not f.empty and f.valid for _c, f in self._filters)

    def toggle(self) -> None:
        self.set_shown(not self._shown)

    def set_shown(self, shown: bool) -> None:
        if shown and not self.available():
            return
        if shown == self._shown:
            return
        self._shown = shown
        header = self._table.horizontalHeader()
        self._grow_fixed_height(_EDIT_H if shown else -_EDIT_H)
        if shown:
            self._build_editors()
            self._base_h = header.sizeHint().height()
            header.setFixedHeight(self._base_h + _EDIT_H)
            header.setViewportMargins(0, 0, 0, _EDIT_H)
            for e in self._editors:
                e.show()
            self._place()
            if self._editors:
                self._editors[0].setFocus()
        else:
            self._timer.stop()
            for e in self._editors:
                e.blockSignals(True)
                e.clear()
                e.blockSignals(False)
                e.setStyleSheet("")
                e.hide()
            self._filters = []
            self._show_all_rows()
            header.setViewportMargins(0, 0, 0, 0)
            header.setMinimumHeight(0)
            header.setMaximumHeight(16777215)
            header.resize(header.width(), self._base_h)
            header.updateGeometry()
            self._table.updateGeometries()
            self._update_badge(self._table.rowCount(), self._table.rowCount())
        self._table.viewport().update()

    def _grow_fixed_height(self, delta: int) -> None:
        """A table sized to exactly fit its rows (the results tables) needs
        the strip's height added, or the last row is clipped."""
        t = self._table
        if t.maximumHeight() < 16777215:
            t.setMaximumHeight(t.maximumHeight() + delta)
        if t.minimumHeight() > 0:
            t.setMinimumHeight(t.minimumHeight() + delta)

    # --------------------------------------------------------------- editors
    def _build_editors(self) -> None:
        ncols = self._table.columnCount()
        while len(self._editors) < ncols:
            c = len(self._editors)
            e = QLineEdit(self._table.horizontalHeader())
            e.setClearButtonEnabled(True)
            e.setPlaceholderText("filter")
            e.setToolTip(SYNTAX_HELP)
            if self._filter_columns is not None \
                    and c not in self._filter_columns:
                e.setEnabled(False)
                e.setPlaceholderText("")
                e.setToolTip("This column cannot be filtered.")
            e.setAccessibleName(f"Filter column {c + 1}")
            e.textChanged.connect(self._typed)
            self._editors.append(e)
        for c, e in enumerate(self._editors):
            h = self._table.horizontalHeaderItem(c)
            e.setAccessibleName(f"Filter {h.text()}" if h is not None
                                else f"Filter column {c + 1}")

    def _place(self, *_a: object) -> None:
        if not self._shown:
            return
        header = self._table.horizontalHeader()
        y = max(header.height() - _EDIT_H, 0)
        for c, e in enumerate(self._editors):
            if c >= self._table.columnCount() or header.isSectionHidden(c):
                e.hide()
                continue
            x = header.sectionViewportPosition(c)
            w = header.sectionSize(c)
            e.setGeometry(x, y, max(w, 1), _EDIT_H)
            e.setVisible(w > 8)

    # ------------------------------------------------------------- filtering
    def _typed(self, *_a: object) -> None:
        """A keystroke: (re)start the debounce; the pass runs once it pauses."""
        self.keep_visible.clear()
        self._timer.start()

    def _model_changed(self, *_a: object) -> None:
        if self._shown:
            self._refilter.start()        # one pass for a burst of changes

    def _settle(self) -> None:
        """After a refill or sort has finished (not in the middle of one)."""
        if not self._shown:
            return
        if not self.available():
            self.set_shown(False)         # shrank to a table worth no filter
        elif self.is_active():
            self.apply()
        else:
            self._place()

    def _columns_changed(self, *_a: object) -> None:
        """A refill with other columns: one edit per column again."""
        if not self._shown:
            return
        self._build_editors()
        for c, e in enumerate(self._editors):
            e.setVisible(c < self._table.columnCount())
        self._place()

    def _compile(self) -> list[tuple[int, CompiledFilter]]:
        out = []
        for c, e in enumerate(self._editors[:self._table.columnCount()]):
            f = compile_filter(e.text())
            if not e.isEnabled():
                continue
            e.setStyleSheet("" if f.valid else
                            "QLineEdit { border: 1px solid #c0392b; }")
            if not f.valid:
                e.setToolTip(f"{f.message}\n\n{SYNTAX_HELP}")
            else:
                e.setToolTip(SYNTAX_HELP)
            if f.valid and not f.empty:
                out.append((c, f))
        return out

    def apply(self) -> None:
        """Run a filter pass now (the debounce calls this)."""
        self._passes += 1
        self._filters = self._compile()
        table = self._table
        n = table.rowCount()
        table.setUpdatesEnabled(False)
        try:
            shown = 0
            for r in range(n):
                keep = (not self._filters) or self._row_matches(r) \
                    or self._pinned(r)
                if table.isRowHidden(r) == keep:
                    table.setRowHidden(r, not keep)
                shown += keep
        finally:
            table.setUpdatesEnabled(True)
        self._update_badge(shown, n)
        self.changed.emit(shown, n)

    def _pinned(self, row: int) -> bool:
        return bool(self.keep_visible and self._row_key is not None
                    and self._row_key(row) in self.keep_visible)

    def _row_matches(self, row: int) -> bool:
        if self._matcher is not None:
            return self._matcher(row, self._filters)
        return self.item_matcher(row, self._filters)

    def item_matcher(self, row: int,
                     filters: list[tuple[int, CompiledFilter]]) -> bool:
        """The default: read each filtered column's item (or cell widget)."""
        table = self._table
        for c, f in filters:
            item = table.item(row, c)
            if item is not None:
                text = item.text()
                raw = None
                if self._raw_role is not None:
                    v = item.data(self._raw_role)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        raw = float(v)
                if not text and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                    text = ("yes" if item.checkState()
                            == Qt.CheckState.Checked else "no")
            else:
                text, raw = _widget_text(table.cellWidget(row, c)), None
            if not f(text, raw):
                return False
        return True

    def clear(self) -> None:
        for e in self._editors:
            e.blockSignals(True)
            e.clear()
            e.blockSignals(False)
        self._timer.stop()
        self.apply()

    def rows_shown_of_total(self) -> tuple[int, int]:
        """(rows a filter leaves showing, rows in the table)."""
        return len(self.visible_rows()), self._table.rowCount()

    def visible_rows(self) -> list[int]:
        """The rows a filter leaves showing, top to bottom."""
        t = self._table
        return [r for r in range(t.rowCount()) if not t.isRowHidden(r)]

    def _show_all_rows(self) -> None:
        t = self._table
        t.setUpdatesEnabled(False)
        try:
            for r in range(t.rowCount()):
                if t.isRowHidden(r):
                    t.setRowHidden(r, False)
        finally:
            t.setUpdatesEnabled(True)

    # ----------------------------------------------------------------- badge
    def _update_badge(self, shown: int, total: int) -> None:
        if self.is_active():
            self._count.setText(f"Showing {shown:,} of {total:,}")
            self._badge.adjustSize()
            self._badge.show()
            self._badge.raise_()
            self._position_badge()
        else:
            self._badge.hide()

    def _position_badge(self) -> None:
        t = self._table
        bar = t.horizontalScrollBar()
        y_off = bar.height() if bar.isVisible() else 0
        self._badge.move(
            max(t.viewport().width() + t.verticalHeader().width()
                - self._badge.width() - 6, 0),
            max(t.height() - self._badge.height() - y_off - 6, 0))

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:  # noqa: N802
        if obj is self._table and ev.type() == QEvent.Type.Resize \
                and self._badge.isVisible():
            self._position_badge()
        return False

    # ------------------------------------------------------------------ menu
    def _header_menu(self, pos: Any) -> None:
        if not self.available():
            return
        menu = QMenu(self._table)
        act = menu.addAction("Filter rows")
        act.setCheckable(True)
        act.setChecked(self._shown)
        act.setShortcut(QKeySequence("Ctrl+F"))
        act.triggered.connect(lambda _c=False: self.toggle())
        if self.is_active():
            menu.addAction("Clear filters").triggered.connect(
                lambda _c=False: self.clear())
        menu.exec(self._table.horizontalHeader().mapToGlobal(pos))


def attach_if_large(table: QTableWidget, **kw: Any) -> FilterBar | None:
    """A filter bar for `table` when it has more than ``MIN_FILTER_ROWS`` rows,
    else nothing. (The bar re-checks as the table is refilled.)"""
    if table.rowCount() <= kw.get("min_rows", MIN_FILTER_ROWS):
        return None
    return FilterBar.attach(table, **kw)

