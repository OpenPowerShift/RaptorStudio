"""The Live Log page: the event log of a stepped simulation, run by run.

A stepped session spans every configured run. The page follows the run on
screen (new rows land as each step happens, the rows of the latest step are
highlighted and scrolled into view) and keeps the earlier runs reachable
through a run selector, so a finished run can be read again while the next
one is stepped.

Only the rows on screen live in the tree: the session keeps every run's
lines, the tree shows at most `ROW_CAP` of the chosen run (the newest), and
says so when it is cutting.
"""
from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from raptor.api.tables import parse_event_log_line
from raptor.gui.resources.design_tokens import active_colour

#: Most rows the tree holds for one run (the newest are kept).
ROW_CAP = 5000
#: Opacity of the "this step" tint over the row (0-255), so the State
#: colouring and the alternating rows underneath stay readable.
_HIGHLIGHT_ALPHA = 80
#: Stronger tint for the one row Focus Event selected, so the current
#: event is obvious even when the tree does not hold keyboard focus.
_FOCUS_ALPHA = 170


class LiveLogPanel(QWidget):
    """Run selector, status line and the event-log tree."""

    #: The run on screen was changed by the user (0-based index).
    run_selected = Signal(int)

    #: True while Focus Event is on: new rows are followed to the bottom.
    #: Off, the user's scroll position is left alone.
    _follow_latest = True
    #: The row Focus Event selected for the latest step.
    _focus_row: QTreeWidgetItem | None = None

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.caption = QLabel("Live event log — stepping / animated run")
        self.caption.setObjectName("SectionNote")
        self.caption.setWordWrap(True)
        lay.addWidget(self.caption)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        self.prev_button = QToolButton()
        self.prev_button.setText("◀")
        self.prev_button.setAccessibleName("Previous run")
        self.prev_button.setToolTip("Show the previous run's event log")
        self.next_button = QToolButton()
        self.next_button.setText("▶")
        self.next_button.setAccessibleName("Next run")
        self.next_button.setToolTip("Show the next run's event log")
        self.run_combo = QComboBox()
        self.run_combo.setAccessibleName("Run shown in the live log")
        self.run_combo.setToolTip(
            "Which run's events are listed. The run being stepped is the "
            "last one; earlier runs stay here to read.")
        self.run_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        bar.addWidget(self.prev_button)
        bar.addWidget(self.run_combo, 1)
        bar.addWidget(self.next_button)
        lay.addLayout(bar)

        self.status = QLabel("")
        self.status.setObjectName("SectionNote")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Live log position")
        lay.addWidget(self.status)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Time", "Type", "Element", "State", "Detail"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setAccessibleName("Live event log")
        lay.addWidget(self.tree, 1)

        # Whole-run state, all reset by `clear()`.
        self._runs: list[Sequence[str]] = []     # finished runs' lines
        self._current: Sequence[str] = []        # the run being stepped
        self._total = 0
        self._shown = 0                          # run index on screen
        self._fed = 0                            # lines of `_shown` in tree
        self._highlighted: list[QTreeWidgetItem] = []
        self._cut = 0                            # rows dropped from the top
        self._t = 0.0
        self._building = False

        self.run_combo.currentIndexChanged.connect(self._on_combo)
        self.prev_button.clicked.connect(lambda: self._step_run(-1))
        self.next_button.clicked.connect(lambda: self._step_run(+1))
        self._sync_buttons()

    # -- public ---------------------------------------------------------
    @property
    def current_index(self) -> int:
        """0-based index of the run being stepped (the newest)."""
        return len(self._runs)

    @property
    def shown_index(self) -> int:
        return self._shown

    @property
    def following(self) -> bool:
        """True when the run on screen is the one being stepped."""
        return self._shown == self.current_index

    def set_follow_latest(self, follow: bool) -> None:
        """Follow newly appended rows to the bottom, or leave the view
        where the user put it (Focus Event off)."""
        self._follow_latest = bool(follow)
        if not self._follow_latest and self._focus_row is not None:
            # Focus Event off: drop the stronger current-row tint, keeping
            # the ordinary latest-step highlight.
            self._focus_row = None
            self._apply_highlight(self._highlighted)

    def focus_event(self, index: int) -> bool:
        """Select and scroll to the current run's `index`th log line.

        If the user is reading an earlier run, switch back to the run
        being stepped first: the event just described happened there.
        Returns whether a row was found and selected."""
        if index < 0 or index >= len(self._current):
            return False
        if not self.following:
            self._show_run(self.current_index)
            self._update_status()
            self._sync_buttons()
        row = index - self._cut
        if row < 0 or row >= self.tree.topLevelItemCount():
            return False
        item = self.tree.topLevelItem(row)
        if item is None:
            return False
        # This is a programmatic focus, not a click: select the row
        # without re-running the click handler (which would locate the
        # element on the canvas again / navigate into its hierarchy).
        blocker = QSignalBlocker(self.tree)
        try:
            self.tree.setCurrentItem(item)
        finally:
            blocker.unblock()
        self.tree.scrollToItem(
            item, QAbstractItemView.ScrollHint.PositionAtCenter)
        self._paint_focus_row(item)
        return True

    def clear(self) -> None:
        self._runs = []
        self._current = []
        self._total = 0
        self._shown = 0
        self._fed = 0
        self._cut = 0
        self._highlighted = []
        self._focus_row = None
        blocker = QSignalBlocker(self.tree)
        try:
            self.tree.clear()
        finally:
            blocker.unblock()
        self._building = True
        self.run_combo.clear()
        self._building = False
        self.status.setText("")
        self._sync_buttons()

    def update_session(self, finished: Sequence[Sequence[str]],
                       current: Sequence[str], total_runs: int,
                       time: float) -> None:
        """Called after every step: `finished` are the runs already done,
        `current` is the live line list of the run being stepped."""
        had_runs = len(self._runs)
        was_on_current = self.following or not self.run_combo.count()
        self._runs = list(finished)
        self._current = current
        self._total = max(int(total_runs), len(self._runs) + 1)
        self._t = float(time)
        if len(self._runs) != had_runs or not self.run_combo.count():
            self._rebuild_combo()
        if self._shown == self.current_index:
            # Same run as before: append new rows (without scrolling when
            # Focus Event is off). A run restarted under the same index is
            # rebuilt by `_append_new_lines`.
            self._set_combo_index(self._shown)
            self._append_new_lines()
        elif was_on_current:
            # A new run started while its predecessor was on screen. Move
            # on to it only when following is wanted. With Focus Event off
            # the user keeps reading the finished run they chose -- rebuild
            # it so its completed log (not the partial one) stays on screen.
            self._show_run(self.current_index if self._follow_latest
                           else self._shown)
        self._update_status()
        self._sync_buttons()

    def refresh_colours(self) -> None:
        """Re-tint the highlighted rows after a theme change."""
        self._apply_highlight(self._highlighted)

    # -- run selection --------------------------------------------------
    def _rebuild_combo(self) -> None:
        self._building = True
        self.run_combo.clear()
        n = self.current_index + 1
        for i in range(n):
            label = f"Run {i + 1} of {self._total}"
            if i < n - 1:
                label += " (finished)"
            else:
                label += " (stepping)"
            self.run_combo.addItem(label, i)
        self._building = False
        self._set_combo_index(self._shown)

    def _set_combo_index(self, i: int) -> None:
        self._building = True
        self.run_combo.setCurrentIndex(min(i, self.run_combo.count() - 1))
        self._building = False

    def _on_combo(self, idx: int) -> None:
        if self._building or idx < 0:
            return
        self._show_run(idx)
        self._update_status()
        self._sync_buttons()
        self.run_selected.emit(idx)

    def _step_run(self, delta: int) -> None:
        target = self.run_combo.currentIndex() + delta
        if 0 <= target < self.run_combo.count():
            self.run_combo.setCurrentIndex(target)

    def _sync_buttons(self) -> None:
        i = self.run_combo.currentIndex()
        n = self.run_combo.count()
        self.prev_button.setEnabled(i > 0)
        self.next_button.setEnabled(0 <= i < n - 1)

    # -- rows -----------------------------------------------------------
    def _lines_of(self, run: int) -> Sequence[str]:
        if run < len(self._runs):
            return self._runs[run]
        return self._current

    def _show_run(self, run: int) -> None:
        """Rebuild the tree from the chosen run (the newest ROW_CAP rows)."""
        self._shown = run
        self._set_combo_index(run)
        self.tree.setUpdatesEnabled(False)
        try:
            blocker = QSignalBlocker(self.tree)
            try:
                self.tree.clear()
            finally:
                blocker.unblock()
            self._highlighted = []
            self._focus_row = None
            lines = self._lines_of(run)
            start = max(0, len(lines) - ROW_CAP)
            self._cut = start
            self._fed = start
            self._add_rows(lines[start:])
            self._fed = len(lines)
            if self.following:
                self._apply_highlight(self._last_step_rows(len(lines)))
        finally:
            self.tree.setUpdatesEnabled(True)
        if self.following:
            if self._follow_latest:
                self.tree.scrollToBottom()
        else:
            self.tree.scrollToTop()

    def _append_new_lines(self) -> None:
        lines = self._current
        if len(lines) < self._fed:           # the run restarted under us
            self._show_run(self.current_index)
            return
        new = lines[self._fed:]
        if not new:
            return
        self._add_rows(new)
        self._fed = len(lines)
        # Keep the tree bounded: the session still holds every line.
        extra = self.tree.topLevelItemCount() - ROW_CAP
        if extra > 0:
            for _ in range(extra):
                self.tree.takeTopLevelItem(0)
            self._cut += extra
        self._apply_highlight(self._last_step_rows(len(lines), len(new)))
        if self._follow_latest:
            self.tree.scrollToBottom()

    def _rows(self) -> list[QTreeWidgetItem]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it is not None:
                out.append(it)
        return out

    def _last_step_rows(self, total: int, n_new: int = 0) -> list[QTreeWidgetItem]:
        """The rows the latest step added (or, on a rebuild, the rows of the
        newest instant)."""
        rows = self._rows()
        if not rows:
            return []
        if n_new:
            return rows[-min(n_new, len(rows)):]
        # Rebuild of the run being stepped: the rows stamped with the
        # newest time.
        stamp = rows[-1].text(0)
        newest = []
        for it in reversed(rows):
            if it.text(0) != stamp:
                break
            newest.append(it)
        return newest[::-1]

    def _add_rows(self, lines: Sequence[str]) -> None:
        from raptor.gui.ui.results import ResultsDock
        c = active_colour()
        for line in lines:
            item = QTreeWidgetItem(self.tree, list(parse_event_log_line(line)))
            colour = ResultsDock._state_colour(item.text(3), c)
            if colour is not None:
                item.setForeground(3, colour)
                # The element name gets the same tint so a state change is
                # visible when the columns are narrow.
                item.setForeground(2, colour)

    def _paint_focus_row(self, item: QTreeWidgetItem) -> None:
        """Give the row Focus Event selected a stronger tint than the rest
        of the step's rows; the tree does not have to take keyboard focus
        (Step/Alt shortcuts keep working)."""
        old = self._focus_row
        if old is not None and old is not item and old in self._highlighted:
            regular = QColor(active_colour()["accent"])
            regular.setAlpha(_HIGHLIGHT_ALPHA)
            brush = QBrush(regular)
            for col in range(self.tree.columnCount()):
                old.setBackground(col, brush)
        self._focus_row = item
        tint = QColor(active_colour()["accent"])
        tint.setAlpha(_FOCUS_ALPHA)
        brush = QBrush(tint)
        for col in range(self.tree.columnCount()):
            item.setBackground(col, brush)
            f = QFont(item.font(col))
            f.setBold(True)
            item.setFont(col, f)

    def _apply_highlight(self, items: Sequence[QTreeWidgetItem]) -> None:
        """Tint the latest step's rows (and bold them); clear the old ones."""
        for it in self._highlighted:
            try:
                for col in range(self.tree.columnCount()):
                    it.setData(col, Qt.ItemDataRole.BackgroundRole, None)
                    f = it.font(col)
                    f.setBold(False)
                    it.setFont(col, f)
            except RuntimeError:            # row already trimmed away
                continue
        tint = QColor(active_colour()["accent"])
        tint.setAlpha(_HIGHLIGHT_ALPHA)
        brush = QBrush(tint)
        for it in items:
            for col in range(self.tree.columnCount()):
                it.setBackground(col, brush)
                f = QFont(it.font(col))
                f.setBold(True)
                it.setFont(col, f)
        self._highlighted = list(items)
        if self._focus_row is not None and self._focus_row not in items:
            self._focus_row = None
        if self._focus_row is not None:
            self._paint_focus_row(self._focus_row)

    def _update_status(self) -> None:
        total_lines = len(self._lines_of(self._shown))
        parts = [f"Run {self._shown + 1} of {self._total}"]
        if self.following:
            parts.append(f"t = {self._t:.3f}")
        else:
            parts.append("finished")
        parts.append(f"{total_lines:,} events")
        text = " · ".join(parts)
        if self._cut:
            text += (f" — showing the newest {total_lines - self._cut:,}; "
                     "the full log is in the Results Event Log when the runs end")
        self.status.setText(text)
