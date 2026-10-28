"""Phases dialog -- the modern replacement for the Raptor 7's
Options ▸ Phases `PhaseBoxObj` ('s
Define + Assign-Block/Node/Event/Hierarchy tab set).

Capability parity with the Raptor 7 dialog, in one modern surface:

- **Define** (left panel): the phase list -- name, part-of-mission
  flag, duration distribution + parameters. Add/Insert/Delete are
  undoable through DiagramView's existing keyed-row commands
  (`add_keyed_row`/`set_keyed_cell`/`remove_keyed_row`).
- **Assign** (right tabs, one per element kind): a grid with one row
  per element and one column per phase. A block/event cell is a mode
  + value pair (Active/Link/Cut + stress multiplier; events also
  Fire/Prior), a node cell is Cut/Link/required-k, a hierarchy cell is
  Active/Link/Cut. Mode glyphs are cut=scissors, link=chain,
  active=play-in-circle.
- **Fill Row / Fill Column** stamp the selected cell's mode+value
  across its whole row (that element, every phase) or whole column
  (that phase, every element).
- **Apply**: every changed element lands in ONE undoable macro via
  `DiagramView.set_element_phasing` (one call per element, the same
  choke point the Inspector's Phasing group uses), so Cancel is a
  plain macro-abort and the canvas hatching/status stay in sync.
"""
from __future__ import annotations
from raptor.gui.ui.modality import exec_dialog

from raptor.gui.ui.qtcompat import req

from raptor.gui.ui import msgbox

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QSpinBox, QDoubleSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from raptor.gui.resources import icons
from raptor.gui.resources.design_tokens import active_colour, SPACING
from raptor.core.fileio.typed import row_int

# Cell roles: mode letter lives in Qt.UserRole, value in UserRole+1.
_MODE_ROLE = Qt.ItemDataRole.UserRole
_VALUE_ROLE = Qt.ItemDataRole.UserRole + 1

_BLOCK_MODES = (("A", "Active"), ("L", "Link"), ("C", "Cut"))
_NODE_MODES = (("K", "k paths"), ("C", "Cut"), ("L", "Linked"))
_EVENT_MODES = (("A", "Probability"), ("F", "Fire"), ("P", "Prior"),
                ("L", "Link"), ("C", "Cut"))

_MODE_ICONS = {
    "A": icons.phase_active_icon,
    "K": icons.phase_active_icon,
    "L": icons.phase_link_icon,
    "C": icons.phase_cut_icon,
    "F": icons.phase_active_icon,
    "P": icons.phase_active_icon,
}


def _current_state(view, kind: str):
    """(ids, names, phased_flags, [(mode, value)] per element, from the
    LIVE file rows. Mirrors what `set_element_phasing` writes back."""
    rbd = view._rbd_ref
    if rbd is None:
        return [], [], [], []
    sec_name = {"block": "BLOCK_DATA", "node": "NODE_DATA",
                "event": "EVENT_DATA", "hier": "HIERARCHY_DATA"}[kind]
    sec = rbd.sections.get(sec_name)
    if sec is None:
        return [], [], [], []
    name_col = {"BLOCK_DATA": "blockName", "NODE_DATA": "Name",
                "EVENT_DATA": "eventName",
                "HIERARCHY_DATA": "hierarchyName"}[sec_name]
    flag_col = {"block": "Phasing", "node": "Phases", "event": "Phasing",
                "hier": "Phs"}[kind]

    def s(x):
        """DELEGATED to `core.fileio.typed.row_int` (review E-12).

        The 0 sentinel is DELIBERATE and load-bearing: ID 0 is the file's
        injected template row, which the caller below skips with
        `if oid == 0`. An unreadable ID therefore being read as 0 makes it
        skip cleanly instead of offering a phase slot for a row that does
        not exist. Changing this to -1 (what the other copies used) would
        put a bogus phase entry on the template row.
        """
        return row_int(x, 0)

    ids, names, flags, entries = [], [], [], []
    for r in sec.rows:
        oid = s(r.get("ID"))
        if oid == 0:
            continue
        name = r.get(name_col, "")
        if kind == "node" and s(r.get("Type")) in (1, 3, 4, 5):
            # Start/End/In/Out markers are never phase-assigned.
            continue
        ids.append(oid)
        names.append(f"{name} ({oid:03d})")
        flags.append(str(r.get(flag_col, "0")) not in ("0", "", "0.000000"))
        per = []
        if kind == "hier":
            # A hierarchy has no phase columns of its own: its per-phase Active / Linked / Cut values are its Out
            # node's tokens (SetPhases on `outNode`).
            out = next((x for x in view._rbd_ref.sections["NODE_DATA"].rows
                        if s(x.get("ID")) == s(r.get("out"))), None)
            if out is not None:
                for i in range(1, 100):
                    key = f"P{i:03d}"
                    if key not in out:
                        break
                    tok = out[key].strip()
                    per.append(("C", 0.0) if tok == "C" else ("L", 0.0) if tok == "L" else ("A", 1.0))
        else:
            for i in range(1, 100):
                key = f"P{i:03d}" if kind == "node" else f"P {i:03d}"
                if key not in r:
                    break
                tok = r[key]
                code, _, val = tok.partition(":")
                code = code.strip()
                if kind == "node":
                    if code == "C":
                        per.append(("C", -1.0))
                    elif code == "L":
                        per.append(("L", 0.0))
                    else:
                        try:
                            per.append(("K", float(code)))
                        except ValueError:
                            per.append(("K", 1.0))
                else:
                    val = val.strip()
                    if code in ("C", "F") and not val:
                        per.append((code, 0.0))
                    elif code in ("A", "L", "P", "F", "C") and val:
                        try:
                            per.append((code, float(val)))
                        except ValueError:
                            per.append(("A", 1.0))
                    else:
                        try:
                            per.append(("A", float(tok)))
                        except ValueError:
                            per.append(("A", 1.0))
        if not per:
            per.append(("A", 1.0))
        entries.append(per)
    return ids, names, flags, entries


class _StampPage(QWidget):
    """The stamp palette's page: carries the armed mode and value its buttons share."""
    _stamp_state: dict


class PhasingDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self._win = win
        self.setWindowTitle("Phases")
        self.setModal(True)
        self.resize(1000, 620)
        v = win._diagram_dock.view
        self._view = v
        self._phases = self._read_phases()

        root = QVBoxLayout(self)
        self._split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self._split, 1)

        # ---- Define panel (left) --------------------------------
        self._define = QTableWidget(len(self._phases), 4)
        self._define.setHorizontalHeaderLabels(
            ["Phase", "Mission", "Dist", "Params"])
        self._define.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self._define.horizontalHeader().setStretchLastSection(True)
        self._populate_define()
        # Dist/Params editing: double-click opens the reusable standard
        # distribution entry dialog, with the same fields, validation
        # and stats as every other distribution entry point in the app.
        self._define.cellDoubleClicked.connect(
            self._on_define_double_clicked)
        define_side = _panel("Phases (duration & mission)", self._define,
                             [("Insert before selected", self._insert_phase),
                              ("Add at end", self._add_phase),
                              ("Delete", self._delete_phase),
                              ("Clear all", self._clear_phases)])
        self._split.addWidget(define_side)

        # ---- Assign tabs (right) --------------------------------
        self._tabs = QTabWidget()
        self._tables: dict[str, QTableWidget] = {}
        self._kinds = []
        for kind, label in (("block", "Blocks"), ("node", "Nodes"),
                            ("event", "Events"), ("hier", "Hierarchies")):
            ids, names, flags, entries = _current_state(v, kind)
            if not ids:
                continue
            t = self._build_assign_table(kind, ids, names, flags, entries)
            page = self._build_stamp_palette(kind, t)
            self._tables[kind] = t
            self._tabs.addTab(page, label)
            self._kinds.append(kind)
        if not self._kinds:
            self._tabs.addTab(QLabel("No elements to phase."), "Assign")
        # The Fill/Reset buttons act on the table on the right, so they sit under THAT table (not under the phase
        # list on the left, where they looked like they belonged to it).
        assign_side = QWidget()
        assign_col = QVBoxLayout(assign_side)
        assign_col.setContentsMargins(0, 0, 0, 0)
        assign_col.addWidget(self._tabs, 1)
        self._split.addWidget(assign_side)
        self._split.setSizes([360, 640])

        # ---- Fill row/column toolbar -----------------------------
        bar = QHBoxLayout()
        tip = ("Stamp the PALETTE's armed mode+value (or, if none is "
               "armed, the selected cell's own mode+value) across its "
               "whole row (this element, every phase)")
        row_btn = QToolButton()
        row_btn.setText("Fill Row")
        row_btn.setIcon(self._mode_icon(self._selected_mode()))
        row_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        row_btn.setToolTip(tip)
        row_btn.clicked.connect(self._fill_row_with_stamp)
        col_tip = ("Stamp the PALETTE's armed mode+value (or, if none "
                   "is armed, the selected cell's own) down its whole "
                   "column (this phase, every element)")
        col_btn = QToolButton()
        col_btn.setText("Fill Column")
        col_btn.setIcon(self._mode_icon(self._selected_mode()))
        col_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        col_btn.setToolTip(col_tip)
        col_btn.clicked.connect(self._fill_column_with_stamp)
        clear_row = QToolButton()
        clear_row.setText("Reset Row")
        clear_row.setToolTip(
            "Reset the row's phase cells to the default (Active 1.0)")
        clear_row.clicked.connect(lambda: self._fill_row(default=True))
        clear_col = QToolButton()
        clear_col.setText("Reset Column")
        clear_col.clicked.connect(
            lambda: self._fill_column(default=True))
        for b, t in ((row_btn, tip), (col_btn, col_tip)):
            b.setToolTip(t)
        bar.addWidget(row_btn)
        bar.addWidget(clear_row)
        bar.addSpacing(SPACING["lg"])
        bar.addWidget(col_btn)
        bar.addWidget(clear_col)
        bar.addStretch(1)
        assign_col.addLayout(bar)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # --------------------------------------------------------------

    def _read_phases(self) -> list[dict]:
        rbd = self._view._rbd_ref
        sec = rbd.sections.get("PHASES") if rbd else None
        rows = [dict(r) for r in (sec.rows if sec else [])]
        return rows

    def _populate_define(self):
        t = self._define
        t.setRowCount(len(self._phases))
        for r, row in enumerate(self._phases):
            from raptor.core.distro_rules import DISTRO_NAMES
            dist_name = DISTRO_NAMES.get(int(_safe_f(row.get("Dist"))),
                                          f"#{row.get('Dist')}")
            vals = [row.get("PhaseName", ""),
                    "Yes" if str(row.get("Mission", "0")) not in
                    ("0", "", "0.000000") else "No",
                    dist_name,
                    ", ".join(_fmt_param(row.get(f"Param{i}", ""))
                              for i in (1, 2, 3)
                              if _is_set(row.get(f"Param{i}")))]
            for c, text in enumerate(vals):
                item = QTableWidgetItem(text)
                if c in (1, 2, 3):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                t.setItem(r, c, item)
        t.resizeColumnsToContents()

    def _edit_phases(self, text: str, edit) -> bool:
        """Run a phase-list edit (`core.fileio.phase_edit`) as one undo step, after committing the grid's own pending
        cell edits; refresh both panels. A refused edit changes nothing and says why."""
        from raptor.core.fileio.phase_edit import PhaseEditError
        if not self._validate_cells():
            return False
        self._commit_cells()
        try:
            self._view.edit_phases(text, edit)
        except PhaseEditError as exc:
            msgbox.warning(self, "Phases", str(exc))
            return False
        self._phases = self._read_phases()
        self._populate_define()
        self._rebuild_assign()
        return True

    def _new_phase_name(self) -> str:
        used = {p.get("PhaseName") for p in self._phases}
        n = len(self._phases) + 1
        while f"Phase_{n}" in used:
            n += 1
        return f"Phase_{n}"

    def _insert_phase(self):
        """Insert a new phase before the selected one (Raptor 7's Insert Phase). Its cells take Raptor 7's
        defaults: blocks Active 1, events Prior, nodes their own k, hierarchies Active."""
        from raptor.core.fileio.phase_edit import insert_phase
        row = self._define.currentRow()
        pos = row + 1 if row >= 0 else len(self._phases) + 1
        name = self._new_phase_name()
        if self._edit_phases("Insert phase", lambda rbd: insert_phase(rbd, pos, name=name)):
            self._define.selectRow(pos - 1)

    def _add_phase(self):
        from raptor.core.fileio.phase_edit import insert_phase
        pos = len(self._phases) + 1
        name = self._new_phase_name()
        if self._edit_phases("Add phase", lambda rbd: insert_phase(rbd, pos, name=name)):
            self._define.selectRow(pos - 1)

    def _delete_phase(self):
        from raptor.core.fileio.phase_edit import delete_phase
        r = self._define.currentRow()
        if r < 0 or r >= len(self._phases):
            return
        self._edit_phases("Delete phase", lambda rbd: delete_phase(rbd, r + 1))

    def _clear_phases(self):
        from raptor.core.fileio.phase_edit import clear_phases
        if self._phases:
            self._edit_phases("Clear phases", clear_phases)

    def _on_define_double_clicked(self, row: int, col: int):
        """Double-click Dist (2) or Params (3): the reusable standard
        distribution entry dialog; commits via the same keyed-row
        commands the Data Tables grid uses (undoable)."""
        if col < 2 or row < 0 or row >= len(self._phases):
            return
        prow = self._phases[row]
        name = prow.get("PhaseName", "")
        if not name:
            return
        from raptor.gui.ui.distro_editor import edit_distro_dialog
        units_str = ""
        try:
            from raptor.core.fileio.typed import system_config_from_row
            cfg = system_config_from_row(
                self._view._rbd_ref.sections["SYSTEM_DATA"].rows[0])
            units_str = cfg.units
        except Exception:
            units_str = ""
        out = edit_distro_dialog(
            self, int(_safe_f(prow.get("Dist"), 19)),
            [_safe_f(prow.get(f"Param{i}"), 0.0) for i in (1, 2, 3)],
            use="Phase length", units=units_str)
        if out is None:
            return
        distro, params = out
        v = self._view
        v._stack.beginMacro(f"Phase {name} distribution")
        try:
            if str(prow.get("Dist", "")) != str(distro):
                v.set_keyed_cell("PHASES", "PhaseName", name,
                                 "Dist", str(distro))
            for i, pval in enumerate(params, start=1):
                parent_column = f"Param{i}"
                if str(prow.get(parent_column, "")) != f"{pval:.6f}":
                    v.set_keyed_cell("PHASES", "PhaseName", name, parent_column,
                                     f"{pval:.6f}")
        finally:
            v._stack.endMacro()
        self._phases = self._read_phases()
        self._populate_define()

    # ---- Assign tables -------------------------------------------

    def _build_assign_table(self, kind, ids, names, flags, entries):
        phase_count = len(self._phases) or 1
        headers = ["Element", "Phased"] + [
            (p.get("PhaseName") or f"P{i + 1:03d}")
            for i, p in enumerate(self._phases)] or ["P001"]
        t = QTableWidget(len(ids), len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        t.blockSignals(True)   # populate without per-cell parse echoes
        for r, (oid, name) in enumerate(zip(ids, names)):
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setData(Qt.ItemDataRole.UserRole + 2, oid)
            t.setItem(r, 0, item)
            ph = QTableWidgetItem()
            ph.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable)
            ph.setCheckState(Qt.CheckState.Checked if flags[r] else Qt.CheckState.Unchecked)
            t.setItem(r, 1, ph)
            cur = entries[r]
            for pi in range(phase_count):
                mode, val = cur[pi] if pi < len(cur) else ("A", 1.0)
                t.setItem(r, 2 + pi, self._make_cell(kind, mode, val))
        t.resizeColumnsToContents()
        # Phase columns: the header text is the phase NAME -- keep it
        # fully visible, never narrower than the header.
        fm = t.fontMetrics()
        for c in range(len(headers)):
            need = fm.horizontalAdvance(headers[c]) + 18
            if t.columnWidth(c) < need:
                t.setColumnWidth(c, need)
        t.blockSignals(False)
        # Type via right-click menu; stress via validated double-click.
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        t.customContextMenuRequested.connect(
            lambda pos, t=t, kind=kind: self._on_cell_menu(t, kind, pos))
        t.cellDoubleClicked.connect(self._on_cell_value_edit)
        return t

    def _make_cell(self, kind: str, mode: str, value: float) -> QTableWidgetItem:
        # READ-ONLY display: type changes go through the right-click
        # menu, the stress through a separately validated double-click
        # entry -- never free typing.
        item = QTableWidgetItem(self._cell_text(kind, mode, value))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setData(_MODE_ROLE, mode)
        item.setData(_VALUE_ROLE, float(value))
        item.setIcon(self._mode_icon(mode))
        return item

    @staticmethod
    def _cell_text(kind: str, mode: str, value: float) -> str:
        if kind == "node":
            if mode == "C":
                return "Cut"
            if mode == "L":
                return "Linked"
            return f"k={int(value)}"
        if kind == "hier":
            if mode == "C":
                return "Cut"
            if mode == "L":
                return "Link"
            return "Active"
        # blocks / events
        if mode == "C":
            return "Cut"
        if mode == "L":
            return f"Link:{value:.3f}"
        if mode == "F":
            return "Fire"
        if mode == "P":
            return f"Prior:{value:.3f}"
        return f"{value:.3f}"

    @staticmethod
    def _mode_icon(mode: str) -> QIcon:
        return _MODE_ICONS.get(mode, icons.phase_active_icon)(
            active_colour()["text"])

    def _selected_mode(self) -> str:
        t = self._current_assign_table()
        if t is None:
            return "A"
        it = t.currentItem()
        if it is None or t.currentColumn() < 2:
            return "A"
        return it.data(_MODE_ROLE) or "A"

    def _current_assign_table(self):
        w = self._tabs.currentWidget()
        if isinstance(w, QTableWidget):
            return w
        if isinstance(w, QWidget) and w is not None:
            # the stamp-palette page wrapper: find the table among the
            # page's layout children (hint label, toolbar layout, table)
            lay = w.layout()
            if lay is not None:
                for i in range(lay.count()):
                    child = req(lay.itemAt(i)).widget()
                    if isinstance(child, QTableWidget):
                        return child
        return None

    def _current_palette(self):
        w = self._tabs.currentWidget()
        if isinstance(w, QTableWidget):
            return None
        return getattr(w, "_stamp_state", None) if w else None

    def _current_kind(self) -> str:
        i = self._tabs.currentIndex()
        if 0 <= i < len(self._kinds):
            return self._kinds[i]
        return "block"

    def _on_cell_menu(self, t: QTableWidget, kind: str, pos) -> None:
        """Right-click a phase cell: change its type via a menu of the
        modes for this kind, each with its mode icon. The value is
        untouched (entered separately)."""
        cell = t.itemAt(pos)
        if cell is None or t.columnAt(pos.x()) < 2:
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        modes = {"block": (("A", "Active — normal stress"), ("L", "Link — in circuit, no stress"),
                           ("C", "Cut — out of circuit")),
                 "hier": (("A", "Active"), ("L", "Link"), ("C", "Cut")),
                 "node": (("K", "k paths required"), ("L", "Linked"), ("C", "Cut")),
                 "event": (("A", "Probability (fire with p)"), ("F", "Fire — always fires"),
                           ("P", "Prior — fire with p from block"),
                           ("L", "Link"), ("C", "Cut"))}[kind]
        for code, label in modes:
            act = menu.addAction(self._mode_icon(code), label)
            act.setData(code)
        chosen = menu.exec(t.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        new_mode = chosen.data()
        old_val = float(cell.data(_VALUE_ROLE) or 0.0)
        # mode-specific default when the carried value is out of domain
        if not self._value_valid(kind, new_mode, old_val):
            old_val = self._default_value(kind, new_mode)
        t.blockSignals(True)
        try:
            new_cell = self._make_cell(kind, new_mode, old_val)
            t.setItem(t.rowOfItem(cell) if False else cell.row(),
                      cell.column(), new_cell)
        finally:
            t.blockSignals(False)

    def _on_cell_value_edit(self, row: int, col: int):
        """Double-click a phase cell: a separate, validated stress /
        probability / k entry. Invalid input is rejected with a message
        and the cell keeps its value."""
        t = self._current_assign_table()
        if t is None or col < 2 or row < 0:
            return
        kind = self._current_kind()
        item = t.item(row, col)
        if item is None:
            return
        mode = item.data(_MODE_ROLE)
        if mode not in ("A", "P", "K", "L"):
            return   # Cut / Fire carry no value
        cur = float(item.data(_VALUE_ROLE) or 0.0)
        from PySide6.QtWidgets import (QDoubleSpinBox,
                                       QDialog, QDialogButtonBox,
                                       QLabel, QFormLayout)
        dlg = QDialog(self)
        dlg.setWindowTitle("Phase value")
        form = QFormLayout(dlg)
        limits = _value_domain(kind, mode)
        if mode == "K":
            from PySide6.QtWidgets import QSpinBox
            spin: QSpinBox | QDoubleSpinBox = QSpinBox()
            spin.setRange(int(limits[0]), int(limits[1]))
            spin.setValue(int(cur))
            tip = f"Required working paths this phase ({limits[0]}..{limits[1]})"
        else:
            spin = QDoubleSpinBox()
            spin.setRange(float(limits[0]), float(limits[1]))
            spin.setDecimals(4)
            spin.setValue(cur)
            tip = (f"Stress multiplier this phase ({limits[0]}..{limits[1]})"
                   if mode in ("A", "L") else
                   f"Fire probability this phase ({limits[0]}..{limits[1]})")
        form.addRow(QLabel(tip))
        form.addRow("Value:", spin)
        err = QLabel("")
        err.setStyleSheet(f"color: {active_colour()['failed']};")
        form.addRow(err)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if exec_dialog(dlg) != QDialog.DialogCode.Accepted:
            return
        new_v = float(spin.value())
        if not self._value_valid(kind, mode, new_v):
            err.setText(f"Value must be within {limits[0]}..{limits[1]}")
            return
        t.blockSignals(True)
        try:
            t.setItem(row, col, self._make_cell(kind, mode, new_v))
        finally:
            t.blockSignals(False)

    @staticmethod
    def _value_domain(kind: str, mode: str) -> tuple[float, float]:
        """Delegates to the module-level `_value_domain` -- the validated
        domain per kind/mode. Stress multipliers >= 0 (engine:
        CombinedStress = phase*standby/GD, 0 = zero-wait legal); event
        probabilities in 0..1; node k a non-negative int <= 9999."""
        return _value_domain(kind, mode)

    def _value_valid(self, kind: str, mode: str, v: float) -> bool:
        lo, hi = _value_domain(kind, mode)
        return lo <= v <= hi

    @staticmethod
    def _default_value(kind: str, mode: str) -> float:
        if mode == "K":
            return 1.0
        return 1.0 if kind != "event" else 0.5

    def _kind_of_table(self, t) -> str:
        for k, tbl in self._tables.items():
            if tbl is t:
                return k
        return "block"

    def _reset_cell(self, kind: str, oid) -> tuple[str, float]:
        """What Reset puts in a phase cell: what a newly inserted phase holds (`core/fileio/phase_edit.py`) --
        Active 1.0 for a block or hierarchy, Prior 0.0 for an event, and for a node its own k (Linked when it has
        none). Never Cut: Reset used to cut every node."""
        if kind == "event":
            return "P", 0.0
        if kind == "node":
            node = self._view.nodes.get(oid)
            k = int(getattr(node, "good_paths", 0) or 0)
            return ("K", float(k)) if k >= 1 else ("L", 0.0)
        return "A", 1.0

    def _row_oid(self, t: QTableWidget, r: int):
        item = t.item(r, 0)
        return item.data(Qt.ItemDataRole.UserRole + 2) if item is not None else None

    def _fill_row(self, default: bool = False):
        t = self._current_assign_table()
        if t is None:
            return
        kind = self._current_kind()
        r, c = t.currentRow(), t.currentColumn()
        if r < 0 or (c < 2 and not default):
            return
        if default:
            mode, val = self._reset_cell(kind, self._row_oid(t, r))
        else:
            src = t.item(r, c)
            mode, val = src.data(_MODE_ROLE), src.data(_VALUE_ROLE)
        t.blockSignals(True)
        try:
            for ci in range(2, t.columnCount()):
                t.setItem(r, ci, self._make_cell(kind, mode, val))
        finally:
            t.blockSignals(False)

    def _fill_row_with_stamp(self):
        """Fill Row driven by the stamp palette -- stamps the palette's
        selected mode+value across the selected element's row, the Raptor 7
       PhaseBox's Mark Row shape."""
        t = self._current_assign_table()
        if t is None:
            return
        stamp = self._current_palette()
        if stamp is None or stamp["mode"] is None:
            # nothing armed: fall back to the selected cell's own mode
            self._fill_row()
            return
        r = t.currentRow()
        if r < 0:
            return
        kind = self._current_kind()
        t.blockSignals(True)
        try:
            for ci in range(2, t.columnCount()):
                t.setItem(r, ci, self._make_cell(
                    kind, stamp["mode"], stamp["value"]))
        finally:
            t.blockSignals(False)

    def _fill_column_with_stamp(self):
        t = self._current_assign_table()
        if t is None:
            return
        stamp = self._current_palette()
        if stamp is None or stamp["mode"] is None:
            self._fill_column()
            return
        c = t.currentColumn()
        if c < 2:
            return
        kind = self._current_kind()
        t.blockSignals(True)
        try:
            for ri in range(t.rowCount()):
                t.setItem(ri, c, self._make_cell(
                    kind, stamp["mode"], stamp["value"]))
        finally:
            t.blockSignals(False)

    def _fill_column(self, default: bool = False):
        t = self._current_assign_table()
        if t is None:
            return
        kind = self._current_kind()
        r, c = t.currentRow(), t.currentColumn()
        if c < 2 or (r < 0 and not default):
            return
        src = None if default else (t.item(r, c).data(_MODE_ROLE), t.item(r, c).data(_VALUE_ROLE))
        t.blockSignals(True)
        try:
            for ri in range(t.rowCount()):
                # per element for a reset: a node keeps its own k
                if src is None:
                    mode, val = self._reset_cell(kind, self._row_oid(t, ri))
                else:
                    mode, val = src
                t.setItem(ri, c, self._make_cell(kind, mode, val))
        finally:
            t.blockSignals(False)

    # ---- stamp palette -------------------------------------------
    #
    # Pick a mode button (Cut=scissors, Link=chain, Active=play -- plus
    # the row/column Fill buttons), then click cells to paint them.
    # This matches how the Raptor 7 PhaseBox works (a mode combo +
    # value box + "Mark Row/Column" buttons) with buttons instead of a
    # combo, and it composes with the existing editable text cells
    # (direct typing still works; the palette is the fast path).

    def _build_stamp_palette(self, kind: str, t: QTableWidget) -> QWidget:
        page = _StampPage()
        page._stamp_state = {"mode": None, "value": 1.0}
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        bar = QHBoxLayout()
        bar.setSpacing(4)
        hint = ("Pick a mode, then CLICK cells to stamp it — or "
                "RIGHT-CLICK a cell to change its type, and "
                "DOUBLE-CLICK to enter its stress/probability/k (all "
                "values are validated). Then Apply.")
        hint_lbl = QLabel(hint)
        hint_lbl.setWordWrap(True)
        hint_lbl.setStyleSheet(
            f"color: {active_colour()['muted']}; font-style: italic;")
        lay.addWidget(hint_lbl)

        modes = {"block": (("A", "Active"), ("L", "Link"), ("C", "Cut")),
                 "hier": (("A", "Active"), ("L", "Link"), ("C", "Cut")),
                 "node": (("A", "Active"), ("L", "Linked"),
                          ("C", "Cut"), ("K", "k paths")),
                 "event": (("A", "Prob"), ("F", "Fire"), ("P", "Prior"),
                           ("L", "Link"), ("C", "Cut"))}[kind]

        buttons: dict[str, QToolButton] = {}

        def arm(mode: str):
            page._stamp_state["mode"] = (
                mode if page._stamp_state["mode"] != mode else None)
            for m, btn in buttons.items():
                btn.setChecked(m == page._stamp_state["mode"])
            val.setVisible(page._stamp_state["mode"] in ("A", "P", "K"))

        for m, label in modes:
            btn = QToolButton()
            btn.setText(label)
            btn.setIcon(self._mode_icon(m))
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setCheckable(True)
            btn.setToolTip(
                {"C": "Cut: out of the circuit this phase (scissors)",
                 "L": "Link: in the circuit, not under stress this "
                      "phase",
                 "A": "Active: normal operation this phase"
                      + (", with the stress multiplier (or event "
                         "probability) in the value box"
                         if m == "A" else ""),
                 "F": "Fire: event fires this phase",
                 "P": "Prior: fire with the probability in the value "
                      "box",
                 "K": "k paths: the node needs this many working "
                      "inputs this phase"}.get(m, ""))
            btn.clicked.connect(
                lambda _c=False, m=m: (arm(m)))
            buttons[m] = btn
            bar.addWidget(btn)

        # value editor for the stamped mode (stress / probability / k)
        val: QSpinBox | QDoubleSpinBox
        if kind == "node":
            val = QSpinBox()
            val.setRange(0, 9999)
            val.setValue(1)
        else:
            val = QDoubleSpinBox()
            val.setRange(-1e9, 1e9)
            val.setDecimals(3)
            val.setValue(1.0)
        val.setToolTip("Value the palette stamps (stress multiplier, "
                       "probability, or k)")
        val.valueChanged.connect(
            lambda _v: page._stamp_state.__setitem__(
                "value", float(val.value())))
        val.setVisible(False)
        bar.addWidget(val)
        bar.addStretch(1)
        bar_widget = QWidget()
        bar_widget.setLayout(bar)
        lay.addWidget(bar_widget)

        t.cellClicked.connect(self._on_cell_stamp)
        lay.addWidget(t, 1)
        return page

    def _on_cell_stamp(self, r: int, c: int):
        """Clicking a phase cell with a palette mode armed paints that
        cell (the stamp pattern). Without an armed mode the click just
        selects (normal table behaviour)."""
        t = self._current_assign_table()
        stamp = self._current_palette()
        if t is None or stamp is None or c < 2 or r < 0:
            return
        if stamp["mode"] is None:
            return
        kind = self._current_kind()
        t.setItem(r, c, self._make_cell(
            kind, stamp["mode"], stamp["value"]))

    def _rebuild_assign(self):
        """Phase list changed: rebuild the assign tables so their
        columns match (keeps each element's current entries where the
        phase index still exists)."""
        cur = self._tabs.currentIndex()
        while self._tabs.count():
            w = self._tabs.widget(0)
            self._tabs.removeTab(0)
            req(w).deleteLater()
        self._tables.clear()
        self._kinds = []
        for kind, label in (("block", "Blocks"), ("node", "Nodes"),
                            ("event", "Events"), ("hier", "Hierarchies")):
            ids, names, flags, entries = _current_state(self._view, kind)
            if not ids:
                continue
            t = self._build_assign_table(kind, ids, names, flags, entries)
            page = self._build_stamp_palette(kind, t)
            self._tables[kind] = t
            self._tabs.addTab(page, label)
            self._kinds.append(kind)
        if not self._kinds:
            self._tabs.addTab(QLabel("No elements to phase."), "Assign")
        self._tabs.setCurrentIndex(min(cur, max(self._tabs.count() - 1, 0)))

    # ---- Apply ----------------------------------------------------

    def _validate_cells(self) -> bool:
        # Validate every entry first -- a cell outside its kind/mode
        # domain aborts the Apply with the offending element named.
        for kind, t in self._tables.items():
            for r in range(t.rowCount()):
                for c in range(2, t.columnCount()):
                    it = t.item(r, c)
                    mode = req(it).data(_MODE_ROLE)
                    if kind == "node" and mode in ("C", "L"):
                        # Cut/Linked carry no value: the cell holds the
                        # file's sentinel (-1 for Cut), not a k to range-check.
                        continue
                    val = float(req(it).data(_VALUE_ROLE) or 0.0)
                    lo, hi = _value_domain(kind, mode)
                    if not (lo <= val <= hi):
                        msgbox.warning(
                            self, "Invalid phase value",
                            f"{req(t.item(r, 0)).text()} — phase {c - 1}: "
                            f"{'k' if mode == 'K' else 'value'} {val:g} "
                            f"must be within {lo:g}..{hi:g}.\n"
                            "Fix it (double-click the cell) and Apply "
                            "again.")
                        return False
        return True

    def _hier_out_node(self, hier_id: int):
        for r in self._view._rbd_ref.sections["HIERARCHY_DATA"].rows:
            if row_int(r.get("ID"), 0) == hier_id:
                return row_int(r.get("out"), 0)
        return None

    def _commit_cells(self):
        v = self._view
        v._stack.beginMacro("Phases dialog")
        try:
            changed = []
            for kind, t in self._tables.items():
                for r in range(t.rowCount()):
                    oid = req(t.item(r, 0)).data(Qt.ItemDataRole.UserRole + 2)
                    phased = req(t.item(r, 1)).checkState() == Qt.CheckState.Checked
                    entries = []
                    # With no phases the grid shows one placeholder column; it must not become a token.
                    for c in range(2, t.columnCount() if self._phases else 2):
                        it = t.item(r, c)
                        entries.append((req(it).data(_MODE_ROLE),
                                        float(req(it).data(_VALUE_ROLE))))
                    changed.append((kind, oid, phased, entries))
            # set_element_phasing pushes one PhaseCmd per element --
            # inside this macro the whole dialog is ONE undo step.
            for kind, oid, phased, entries in changed:
                if kind == "hier":
                    # The values live on the Out node: C / L / 1 as for any node.
                    out_id = self._hier_out_node(oid)
                    if out_id is not None:
                        node_entries = [(m, -1.0 if m == "C" else 0.0 if m == "L" else 1.0)
                                        for m, _v in entries]
                        v.set_element_phasing("node", out_id, phased, node_entries)
                v.set_element_phasing(kind, oid, phased, entries if kind != "hier" else [])
        finally:
            v._stack.endMacro()

    def _apply(self):
        if not self._validate_cells():
            return
        self._commit_cells()
        self.accept()


def _value_domain(kind: str, mode: str) -> tuple[float, float]:
    """Module-level twin of `PhasingDialog._value_domain` (the static
    method delegates here)."""
    if mode == "K":
        return (0.0, 9999.0)
    if kind == "event" and mode in ("A", "P"):
        return (0.0, 1.0)
    return (0.0, 999999999.0)


def _fmt_param(v: str) -> str:
    try:
        f = float(v)
        return f"{f:g}"
    except (TypeError, ValueError):
        return str(v or "")


def _safe_f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _is_set(v) -> bool:
    try:
        return abs(float(v or 0)) > 1e-12
    except (TypeError, ValueError):
        return bool(v)


def _parse_cell(kind: str, text: str):
    """Cell text -> (mode, value). Accepts the dialog's own display
    strings ('Cut', 'k=3', 'Link:1.000', '0.500', 'Fire', 'Prior:0.5')
    and real-file tokens ('A: 1.500', 'C', 'L')."""
    t = text.strip()
    if kind == "node":
        if t.lower().startswith("cut"):
            return "C", -1.0
        if t.lower().startswith("link"):
            return "L", 0.0
        if t.startswith("k="):
            try:
                return "K", float(int(t[2:]))
            except ValueError:
                return "K", 1.0
        try:
            return "K", float(int(float(t)))
        except ValueError:
            return "K", 1.0
    if kind == "hier":
        if t.lower().startswith("cut"):
            return "C", 0.0
        if t.lower().startswith("link"):
            return "L", 0.0
        return "A", 1.0
    low = t.lower()
    for code in ("prior", "link", "fire", "cut"):
        if low.startswith(code):
            rest = t[len(code):].lstrip(":").strip()
            try:
                return {"prior": "P", "link": "L", "fire": "F",
                        "cut": "C"}[code], float(rest or 0.0)
            except ValueError:
                return {"prior": "P", "link": "L", "fire": "F",
                        "cut": "C"}[code], 0.0
    if ":" in t:
        code, _, val = t.partition(":")
        code = code.strip().upper()
        if code in ("A", "F", "P", "L", "C"):
            try:
                return code, float(val)
            except ValueError:
                return code, 0.0
    try:
        return "A", float(t)
    except ValueError:
        return "A", 1.0


def _panel(title: str, table, buttons: list[tuple[str, object]]):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(QLabel(title))
    for text, fn in buttons:
        b = QPushButton(text)
        b.clicked.connect(fn)
        lay.addWidget(b)
    lay.addWidget(table, 1)
    return w


def open_phasing_dialog(win) -> None:
    dlg = PhasingDialog(win)
    exec_dialog(dlg)
