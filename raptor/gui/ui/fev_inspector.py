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
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from raptor.core.engine.fmeca import fmeca_lists
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

    def __init__(self, parent: QWidget | None = None, *,
                 summary_provider: Callable[[], str] | None = None) -> None:
        super().__init__(parent)
        self._summary_provider = summary_provider
        self._rows: list[tuple[str, str, str, str]] = []
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

        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(list(_HEADERS))
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setVisible(False)
        header_view = self._table.horizontalHeader()
        header_view.setStretchLastSection(True)
        # Interactive columns with a sensible start width, NOT
        # ResizeToContents: the latter remeasures every cell after every
        # insertion, turning a 200-row capacity list into a 1.6 s table
        # rebuild on every click.
        header_view.setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive)
        header_view.setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(
            2, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(0, 104)
        self._table.setColumnWidth(2, 104)
        self._table.itemSelectionChanged.connect(self._sync_copy_buttons)
        outer.addWidget(self._table, 1)

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
        outer.addLayout(buttons)

        self._sync_copy_buttons()

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
        self._populate(self._rows)

    def update_session(self, session: Any, *,
                       show_criticality: bool = False,
                       capacity: dict[str, Any] | None = None) -> None:
        """Rebuild the table from a `FevSession`'s current state."""
        self._session = session
        lists = fmeca_lists(session)
        rows: list[tuple[str, str, str, str]] = []

        # -- system ---------------------------------------------------
        colour = session.system_color
        word = _SYSTEM_WORD.get(colour, colour.value)
        rows.append(_section("SYSTEM"))
        rows.append(("System", "Status", word, _SYSTEM_HELP.get(colour, "")))
        if session.phased:
            phase = session.model.phases[session.phase_number - 1]
            rows.append(("System", "Phase",
                         f"{session.phase_number} of {len(session.model.phases)}",
                         phase.phase_name))
        if show_criticality:
            rows.append(("System", "Criticality",
                         _criticality_text(session, colour),
                         "Only shown when the developer option is enabled"))

        # -- failures -------------------------------------------------
        rows.append(_section("FAILURES"))
        failure_rows: list[tuple[str, str, str, str]] = []
        for key, category, state, detail in _FAILURE_LISTS:
            for name in lists.get(key, []):
                failure_rows.append(
                    ("Failures", name, state, f"{detail} — {category}"))
        rows.extend(failure_rows or [
            ("Failures", "None", "", "No failed, induced, idle, PM, cut or "
                                      "linked elements")])

        # -- effects --------------------------------------------------
        rows.append(_section("EFFECTS"))
        effect_rows: list[tuple[str, str, str, str]] = []
        for key, category, state, detail in _EFFECT_LISTS:
            for name in lists.get(key, []):
                effect_rows.append(
                    ("Effects", name, state, f"{detail} — {category}"))
        rows.extend(effect_rows or [
            ("Effects", "None", "", "No down or degraded nodes/subsystems")])

        # -- capacity -------------------------------------------------
        rows.append(_section("CAPACITY"))
        if capacity is not None:
            percent = capacity.get("delivered_fraction")
            rows.append(("Capacity", "Delivered flow",
                         str(capacity.get("delivered", "—")),
                         f"of {capacity.get('flow_generated', '—')}"
                         + (f" ({percent:.1%})" if percent is not None else "")))
            rows.append(("Capacity", "Lost flow",
                         str(capacity.get("lost", "—")), ""))
            rows.append(("Capacity", "System capacity",
                         str(capacity.get("system_capacity", "—")), ""))
            for node in capacity.get("nodes", ()):
                rows.append(("Capacity", str(node.get("name", "")),
                             str(node.get("flow", "")),
                             f"capacity {node.get('capacity', '')}"))
        else:
            rows.append(("Capacity", "Not analysed", "", "Capacity analysis is off"))

        self._headline.setText(self._headline_text(session, lists))
        self._set_badge(colour, word)
        self._populate(rows)

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

    def _populate(self, rows: list[tuple[str, str, str, str]]) -> None:
        """Replace the table in one pass (a rejected click redraws it too)."""
        self._rows = rows
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
                for column, text in enumerate((category, element, state, detail)):
                    item = QTableWidgetItem(text)
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
                    table.setSpan(row, 0, 1, len(_HEADERS))
        finally:
            table.setUpdatesEnabled(True)
        self._sync_copy_buttons()

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
