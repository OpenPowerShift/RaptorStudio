"""Failure Effects inspector — the live FEV state as a structured table.

The Failure Effects View is a state you change by hand: a click fails or
repairs an element, and the model's nodes, subsystems and system colour
follow. The status bar says the headline; this dock is the detail beside
the canvas, available for the whole time the view is open.

Rows are grouped by what happened (Failures, Effects, Capacity), with one
row per affected element. Every row is copyable as TSV for a spreadsheet,
and the complete Raptor 7 status text can be copied from the same panel.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QHeaderView, QLabel,
    QMenu, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from raptor.core.engine.fmeca import fmeca_entries
from raptor.core.engine.system_status import SystemColor
from raptor.gui.resources.design_tokens import SPACING, active_colour

_HEADERS = ("Category", "Element", "State", "Detail")

#: The words a user sees for the system colour. The status bar already
#: uses `SystemColor.value`; this table spells out what the colour means.
_SYSTEM_WORD = {
    SystemColor.GREEN: "Green",
    SystemColor.YELLOW: "Yellow",
    SystemColor.RED: "Red",
}
_SYSTEM_HELP = {
    SystemColor.GREEN: "Every required path is working",
    SystemColor.YELLOW: "The system is degraded: enough is working to stay up",
    SystemColor.RED: "The system is fully down",
}

#: One visible table row plus the exact element it can navigate to.
_Row = tuple[str, str, str, str, tuple[str, int] | None]

#: (list key, category, state, detail) in the order Raptor 7 reports them.
_FAILURE_LISTS = (
    ("failed", "Failed components", "Repairing", "Failed block"),
    ("failed_events", "Failed events", "Failure", "Failed event"),
    ("induced", "Induced failures", "Repairing", "Induced by a dependency"),
    ("idle", "Idle components", "Idle", "Constrained by an upstream dependency"),
    ("pm", "PM components", "PM", "Preventive maintenance"),
    ("cut", "Cut components", "Cut", "Cut in this phase"),
    ("linked", "Linked components", "Linked", "Linked in this phase"),
)
_EFFECT_LISTS = (
    ("down_subsystems", "Down subsystems", "Down", "Subsystem"),
    ("degraded_subsystems", "Degraded subsystems", "Degraded", "Subsystem"),
    ("down_nodes", "Down nodes", "Down", "Node"),
    ("degraded_nodes", "Degraded nodes", "Degraded", "Node"),
)


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _section(label: str) -> tuple[str, str, str, str]:
    """A bold group row in the flat table; the empty element marks it."""
    return (label, "", "", "")


class FevInspectorDock(QWidget):
    """The structured Failure Effects view shown while FEV is enabled."""

    #: A row (or the summary text) was copied. The owning window may use
    #: this for a status-bar message; tests use it as a seam.
    copied = Signal(str)
    #: "Go to element": the owning window navigates its canvas to this exact
    #: element id (names are not unique, so a name lookup is not enough).
    locate_requested = Signal(str, int)

    def __init__(self, parent: QWidget | None = None, *,
                 summary_provider: Callable[[], str] | None = None) -> None:
        super().__init__(parent)
        self._summary_provider = summary_provider
        self._rows: list[tuple[str, str, str, str]] = []
        self._row_locators: list[tuple[str, int] | None] = []
        self._session: Any = None
        self._build()

    # ---- construction ---------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING["sm"], SPACING["sm"],
                                 SPACING["sm"], SPACING["sm"])
        outer.setSpacing(SPACING["sm"])

        header = QHBoxLayout()
        self._system_badge = QLabel("—")
        self._system_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._system_badge.setMinimumWidth(74)
        self._system_badge.setToolTip("Overall system status")
        header.addWidget(self._system_badge)
        self._headline = QLabel("Open Failure Effects View to see the live state.")
        self._headline.setObjectName("SectionNote")
        self._headline.setWordWrap(True)
        header.addWidget(self._headline, 1)
        outer.addLayout(header)

        self._table = self._build_table()
        outer.addWidget(self._table, 1)
        outer.addLayout(self._build_buttons())
        self._sync_copy_buttons()

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(_HEADERS))
        table.setHorizontalHeaderLabels(list(_HEADERS))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.TextElideMode.ElideRight)
        table.verticalHeader().setVisible(False)
        header_view = table.horizontalHeader()
        header_view.setStretchLastSection(True)
        # Interactive columns with a sensible start width, NOT
        # ResizeToContents: the latter remeasures every cell after every
        # insertion, turning a 200-row capacity list into a 1.6 s table
        # rebuild on every click. Category is hidden: the bold section
        # rows already say it (it stays in the copied text). The panel's
        # width goes to Element and Detail, the last stretching to the
        # edge, so nothing scrolls sideways; a cut-off cell has the full
        # text in its tooltip.
        table.setColumnHidden(0, True)
        header_view.setSectionResizeMode(
            1, QHeaderView.ResizeMode.Interactive)
        header_view.setSectionResizeMode(
            2, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(1, 120)
        table.setColumnWidth(2, 84)
        table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.itemSelectionChanged.connect(self._sync_copy_buttons)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(self._show_context_menu)
        return table

    def _build_buttons(self) -> QHBoxLayout:
        buttons = QHBoxLayout()
        self._copy_selected = QPushButton("Copy selected")
        self._copy_selected.setToolTip(
            "Copy the selected row(s) to the clipboard as tab-separated text")
        self._copy_selected.clicked.connect(self.copy_selected)
        self._copy_all = QPushButton("Copy all")
        self._copy_all.setToolTip(
            "Copy the whole Failure Effects table as tab-separated text")
        self._copy_all.clicked.connect(self.copy_all)
        self._copy_summary = QPushButton("Copy summary")
        self._copy_summary.setToolTip(
            "Copy the complete System Status Summary text, as written to the "
            "interactive log")
        self._copy_summary.clicked.connect(self.copy_summary)
        buttons.addWidget(self._copy_selected)
        buttons.addWidget(self._copy_all)
        buttons.addWidget(self._copy_summary)
        buttons.addStretch(1)
        self._copy_note = QLabel("")
        self._copy_note.setObjectName("SectionNote")
        buttons.addWidget(self._copy_note)
        return buttons

    # ---- data -----------------------------------------------------------

    def set_summary_provider(self, provider: Callable[[], str] | None) -> None:
        """Set the callable behind Copy summary (MainWindow's FEV text)."""
        self._summary_provider = provider

    def refresh_theme(self) -> None:
        """Repaint table colours after a light/dark switch."""
        if self._session is None:
            return
        colour = self._session.system_color
        self._set_badge(colour, _SYSTEM_WORD.get(colour, colour.value))
        self._populate(self._rows, self._row_locators)

    def update_session(self, session: Any, *,
                       show_criticality: bool = False,
                       capacity: dict[str, Any] | None = None) -> None:
        """Rebuild the table from a `FevSession`'s current state."""
        self._session = session
        entries = fmeca_entries(session)
        lists = {key: [name for name, _kind, _id in values]
                 for key, values in entries.items()}
        colour = session.system_color
        word = _SYSTEM_WORD.get(colour, colour.value)
        rows = [
            (*_section("SYSTEM"), None),
            *self._system_rows(session, colour, word, show_criticality),
            (*_section("FAILURES"), None),
            *self._failure_rows(entries),
            (*_section("EFFECTS"), None),
            *self._effect_rows(entries),
            (*_section("CAPACITY"), None),
            *self._capacity_rows(capacity),
        ]
        self._headline.setText(self._headline_text(session, lists))
        self._set_badge(colour, word)
        self._populate([row[:4] for row in rows],
                       [row[4] for row in rows])

    @staticmethod
    def _system_rows(session: Any, colour: SystemColor, word: str,
                     show_criticality: bool) -> list[_Row]:
        rows: list[_Row] = [
            ("System", "Status", word, _SYSTEM_HELP.get(colour, ""), None)]
        if session.phased:
            phase = session.model.phases[session.phase_number - 1]
            rows.append(("System", "Phase",
                         f"{session.phase_number} of {len(session.model.phases)}",
                         phase.phase_name, None))
        if show_criticality:
            rows.append(("System", "Criticality",
                         _criticality_text(session, colour),
                         "Only shown when the developer option is enabled",
                         None))
        return rows

    @staticmethod
    def _failure_rows(entries: dict[str, list[tuple[str, str, int]]]) -> list[_Row]:
        rows: list[_Row] = []
        for key, category, state, detail in _FAILURE_LISTS:
            for name, kind, oid in entries.get(key, []):
                # The hidden Category column (in the copied text) carries
                # Raptor 7's list name, the visible Detail what it means.
                rows.append((category, name, state, detail, (kind, oid)))
        return rows or [
            ("Failures", "None", "",
             "No failed, induced, idle, PM, cut or linked elements", None)]

    @staticmethod
    def _effect_rows(entries: dict[str, list[tuple[str, str, int]]]) -> list[_Row]:
        rows: list[_Row] = []
        for key, category, state, detail in _EFFECT_LISTS:
            for name, kind, oid in entries.get(key, []):
                rows.append((category, name, state, detail, (kind, oid)))
        return rows or [
            ("Effects", "None", "", "No down or degraded nodes/subsystems",
             None)]

    @staticmethod
    def _capacity_rows(capacity: dict[str, Any] | None) -> list[_Row]:
        if capacity is None:
            return [("Capacity", "Not analysed", "",
                     "Capacity analysis is off", None)]
        percent = capacity.get("delivered_fraction")
        rows: list[_Row] = [
            ("Capacity", "Delivered flow", str(capacity.get("delivered", "—")),
             f"of {capacity.get('flow_generated', '—')}"
             + (f" ({percent:.1%})" if percent is not None else ""), None),
            ("Capacity", "Lost flow", str(capacity.get("lost", "—")), "", None),
            ("Capacity", "System capacity",
             str(capacity.get("system_capacity", "—")), "", None),
        ]
        for node in capacity.get("nodes", ()):
            nid = node.get("id")
            rows.append(("Capacity", str(node.get("name", "")),
                         str(node.get("flow", "")),
                         f"capacity {node.get('capacity', '')}",
                         ("node", int(nid)) if nid is not None else None))
        return rows

    @staticmethod
    def _headline_text(session: Any, lists: dict[str, list[str]]) -> str:
        colour = _SYSTEM_WORD.get(session.system_color, session.system_color.value)
        parts = [colour]
        if lists.get("failed"):
            parts.append(_plural(len(lists["failed"]), "failed block"))
        if lists.get("induced"):
            parts.append(_plural(len(lists["induced"]), "induced failure"))
        if lists.get("failed_events"):
            parts.append(_plural(len(lists["failed_events"]), "failed event"))
        if lists.get("down_nodes"):
            parts.append(_plural(len(lists["down_nodes"]), "down node"))
        if session.phased:
            parts.append(f"phase {session.phase_number}")
        return " · ".join(parts)

    def _set_badge(self, colour: SystemColor, word: str) -> None:
        theme = active_colour()
        background = {
            SystemColor.GREEN: theme["result_green"],
            SystemColor.YELLOW: theme["result_yellow"],
            SystemColor.RED: theme["result_red"],
        }.get(colour, theme["muted"])
        # A white glyph on these colours is legible in both themes; the
        # result palette is already chosen for contrast with white text.
        self._system_badge.setText(word.upper())
        self._system_badge.setStyleSheet(
            f"QLabel {{ background: {background}; color: white; "
            "font-weight: 700; border-radius: 4px; padding: 3px 8px; }")

    def _populate(self, rows: list[tuple[str, str, str, str]],
                  locators: list[tuple[str, int] | None]) -> None:
        """Replace the table in one pass (a rejected click redraws it too)."""
        self._rows = rows
        self._row_locators = locators
        table = self._table
        table.setUpdatesEnabled(False)
        try:
            table.clearSpans()
            table.setRowCount(len(rows))
            theme = active_colour()
            section_background = QColor(theme["group_header"])
            normal_text = QColor(theme["text"])
            muted_text = QColor(theme["muted"])
            state_colours = {
                "Repairing": QColor(theme["failed"]),
                "Failure": QColor(theme["failed"]),
                "Down": QColor(theme["failed"]),
                "Degraded": QColor(theme["result_yellow"]),
                "PM": QColor(theme["maintenance"]),
                "Idle": QColor(theme["idle"]),
                "Linked": QColor(theme["relationship"]),
                "Cut": QColor(theme["muted"]),
            }
            for row, (category, element, state, detail) in enumerate(rows):
                is_section = not element and not state and not detail
                cells = (category, category if is_section else element,
                         state, detail)
                for column, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    if text and not is_section:
                        item.setToolTip(text)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled
                                  | Qt.ItemFlag.ItemIsSelectable)
                    item.setForeground(state_colours.get(state, normal_text)
                                       if column == 2 else normal_text)
                    if column == 3 and not text:
                        item.setForeground(muted_text)
                    if is_section:
                        item.setBackground(section_background)
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                    table.setItem(row, column, item)
                if is_section:
                    # From Element: Category, where the label also is,
                    # is hidden.
                    table.setSpan(row, 1, 1, len(_HEADERS) - 1)
        finally:
            table.setUpdatesEnabled(True)
        self._sync_copy_buttons()

    # ---- context menu ---------------------------------------------------

    def _show_context_menu(self, pos) -> None:
        """Right-click a row: locate its element, or copy the table."""
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        self._table.selectRow(row)
        locator = (self._row_locators[row]
                   if 0 <= row < len(self._row_locators) else None)
        menu = QMenu(self)
        goto = menu.addAction("Go to element")
        goto.setToolTip("Select this element on the canvas, entering its "
                        "hierarchy level if necessary")
        goto.setEnabled(locator is not None)
        goto.triggered.connect(
            lambda _checked=False, target=locator: self._go_to(target))
        menu.addSeparator()
        menu.addAction("Copy selected", self.copy_selected)
        menu.addAction("Copy all", self.copy_all)
        menu.addAction("Copy summary", self.copy_summary)
        self._exec_menu(menu, self._table.viewport().mapToGlobal(pos))

    def _exec_menu(self, menu: QMenu, global_pos) -> None:
        """A seam: `QMenu.exec` cannot be stubbed, so tests replace this."""
        menu.exec(global_pos)

    def _go_to(self, locator: tuple[str, int] | None) -> None:
        if locator is None:
            return
        self.locate_requested.emit(locator[0], locator[1])

    # ---- clipboard ------------------------------------------------------

    def _sync_copy_buttons(self) -> None:
        has_rows = bool(self._rows)
        selected = bool(self._table.selectionModel()
                        and self._table.selectionModel().selectedRows())
        self._copy_selected.setEnabled(selected)
        self._copy_all.setEnabled(has_rows)
        self._copy_summary.setEnabled(self._summary_provider is not None
                                      and self._session is not None)

    def _copy(self, text: str, note: str) -> None:
        QApplication.clipboard().setText(text)
        self._copy_note.setText(note)
        self.copied.emit(text)

    def copy_all(self) -> None:
        """Copy every table row as TSV, including section headers."""
        self._copy(self._table_tsv(None), "Copied the table")

    def copy_selected(self) -> None:
        """Copy the selected rows as TSV, in table order."""
        selection = self._table.selectionModel()
        if selection is None:
            return
        rows = sorted({index.row() for index in selection.selectedRows()})
        if not rows:
            return
        self._copy(self._table_tsv(rows),
                   f"Copied {_plural(len(rows), 'row')}")

    def copy_summary(self) -> None:
        """Copy the full Raptor 7 System Status Summary text."""
        if self._summary_provider is None:
            return
        text = self._summary_provider()
        self._copy(text, "Copied the summary")

    def _table_tsv(self, rows: list[int] | None) -> str:
        selected = None if rows is None else set(rows)
        out = ["\t".join(_HEADERS)]
        for row, values in enumerate(self._rows):
            if selected is not None and row not in selected:
                continue
            out.append("\t".join(values))
        return "\n".join(out)


def _criticality_text(session: Any, colour: SystemColor) -> str:
    """The same rule as the status bar / FMECA line: a value only matters
    when the system is red."""
    if colour != SystemColor.RED:
        return "0.0"
    from raptor.core.engine.reports import real_to_str
    return real_to_str(session.criticality)
