"""Inspector node tabs: flow, standby, dependency and capacity settings.

Expects on `self`: the Inspector's tab layouts and the selected node."""
from __future__ import annotations

from raptor.gui.ui.inspector_common import (
    _i_raw,
    _row_float,
    _vbox,
    parse_flow_cell,
)

from raptor.gui.ui.qtcompat import req

from PySide6.QtWidgets import QAbstractItemView
from typing import Any, cast
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLayout, QScrollArea, QLabel, QCheckBox, QComboBox, QRadioButton, QButtonGroup, QTableWidget,
    QTableWidgetItem,
)

from raptor.core.fileio.typed import system_config_from_row
# The distribution rules are `raptor.core.distro_rules`'s, not this
# module's (review D-04) -- they are pure model rules and belong in a
# Qt-free home. Imported here because this widget still uses both.
from raptor.core.model import RBDNode

#: Raptor 7's `UnitsCombo` items, in its own order
#: The box was
#: editable, so these are presets rather than a closed list: Raptor 7
#: reads `unitsBox.Text()` and every label is built from it.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _Base
else:
    _Base = object


class InspectorNodeMixin(_Base):
    """Inspector node tabs: flow, standby, dependency and capacity settings."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _current_block_id: Any
        _current_node_id: Any
        _position_labels: Any
        _position_source: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    def _populate_node(self, node: RBDNode):
        """Node properties across General/Capacity/Dependency/Advanced,
        matching the real Node Properties dialog."""
        self._begin_populate()
        self._configure_tabs("node")
        self._header.setText(f"Node: {node.node_name}  (ID {node.id})")
        self._current_node_id = node.id
        self._current_block_id = None
        for i in range(self._tabs.count()):
            self._clear_tab(cast(QScrollArea, self._tabs.widget(i)))

        w = self._scroll_general.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_general)

        type_names = {1: "Start", 2: "Regular", 3: "End", 4: "Hierarchy In", 5: "Hierarchy Out"}
        gb, fl = self._group("Identity", layout)
        node_id = node.id
        self._name_edit(fl, node.node_name, lambda new_name: self.node_renamed.emit(node_id, new_name))
        self._row(fl, "ID", node.id)
        self._row(fl, "Type", type_names.get(node.type_node, f"Unknown ({node.type_node})"))
        self._row(fl, "Parent ID", node.parent_id)
        self._comment_row(fl, "node", node_id, node.node_name)

        gb, fl = self._group("Position", layout)
        self._position_source = node
        self._position_labels = (
            self._row(fl, "X", f"{node.x_position:.3f}"),
            self._row(fl, "Y", f"{node.y_position:.3f}"))

        gb, fl = self._group("Connections", layout)
        self._row(fl, "Outputs", node.connect_out_of_num)

        gb, fl = self._group("K-out-of-N Logic", layout)
        gb.setToolTip(
            "k-out-of-n: this node needs at least k of its n input paths "
            "working for it to count as up (k=1 is a simple OR, k=n is "
            "a simple AND).")
        # Only Regular (type 2) nodes have a real k-out-of-n threshold;
        # Start/End/Hierarchy nodes store a `k=-1` file-format sentinel
        # that `node_from_row` coerces to `good_paths=1` (see that
        # function's docstring). Editing it here for those types would
        # write back a literal 1, destroying the sentinel. Read-only for
        # anything else.
        if node.type_node == 2:
            node_id = node.id
            self._int_row(fl, "Required good (k)", node.good_paths,
                           lambda v: self.node_good_paths_changed.emit(node_id, v))
        else:
            self._row(fl, "Required good (k)", node.good_paths)
        # `n` is its own NODE_DATA column, distinct from `Out` (which
        # RBDNode.connect_out_of_num maps) and can differ from it.
        # RBDNode doesn't carry `n`, so read the raw row for it.
        raw_row = self._raw_node_row(node.id)
        if raw_row is not None and raw_row.get("n", "") != "":
            self._row(fl, "Total paths (n)", _i_raw(raw_row, "n"))
        else:
            self._row(fl, "Total paths (n)", "(not stored)")

        self._populate_node_capacity(node)
        self._populate_node_standby(node)
        self._populate_node_dependency(node)
        self._populate_node_advanced(node)
        self._restore_tab()

    def _link_rows_for_node(self, node_id: int) -> tuple[list[dict], list[dict]]:
        """(incoming, outgoing) raw LINK_DATA rows touching this node --
        Flow In / Flow Out's own table source. Endpoint TYPE/ID columns
        (FromType/FromID, ToType/ToID) are shared with every other link
        use in the file, not capacity-specific."""
        sec = self._rbd.sections.get("LINK_DATA") if self._rbd else None
        if sec is None:
            return [], []
        into, out_of = [], []
        for row in sec.rows:
            try:
                if row.get("ToType") == "Node" and \
                        int(float(row.get("ToID", -1))) == node_id:
                    into.append(row)
                if row.get("FromType") == "Node" and \
                        int(float(row.get("FromID", -1))) == node_id:
                    out_of.append(row)
            except (TypeError, ValueError):
                continue
        return into, out_of

    def _link_endpoint_name(self, kind: str, oid: int) -> str:
        """Display name for a link endpoint (Block/Node/Hier), by id --
        same raw-row lookup `_raw_node_row` uses for one section, done
        for whichever section this endpoint kind actually lives in."""
        section, col = {"Block": ("BLOCK_DATA", "blockName"),
                        "Node": ("NODE_DATA", "Name"),
                        "Hier": ("HIERARCHY_DATA", "hierarchyName")}.get(
                            kind, (None, None))
        if section is None or self._rbd is None:
            return f"{kind} {oid}"
        sec = self._rbd.sections.get(section)
        if sec is None:
            return f"{kind} {oid}"
        for row in sec.rows:
            try:
                if int(float(row.get("ID", -1))) == oid:
                    return row.get(col or "", "") or f"{kind} {oid}"
            except (TypeError, ValueError):
                continue
        return f"{kind} {oid}"

    @staticmethod
    def _flow_cell_text(raw: str, sentinel_label: str, n_in: int = 1) -> str:
        """nomFlow/MaxFlow store a number or one of two sentinels, which
        Raptor 7's Node dialog shows as keywords:
        -1 is "MaxFlow" (nominal) / "FullFlow" (max) -- all of the flow
        generated at Start -- and -2 is "MaxFlow/N" / "FullFlow/N", an even
        share across the node's N incoming paths. -2 showed as a bare "-2",
        which meant nothing to anyone."""
        try:
            v = int(float(raw))
        except (TypeError, ValueError):
            return str(raw)
        if v == -1:
            return sentinel_label
        if v == -2:
            return f"{sentinel_label}/{n_in}"
        return str(v)

    def _flow_table(self, layout, rows: list[dict], *, incoming: bool):
        headers = (["Link From", "Type", "Nominal Flow", "Max Flow"]
                  if incoming else ["Link To", "Type", "Priority"])
        t = QTableWidget(len(rows), len(headers))
        t.setAccessibleName("Incoming link flows" if incoming
                            else "Outgoing link priorities")
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        # Editable value columns (Raptor 7's Node
        # Properties Capacity tab): Nominal Flow + Max Flow on incoming
        # links, Priority (CapPri) on outgoing links -- each commit routed
        # through the view's undoable IdCellCmd. The identity columns
        # stay read-only.
        t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        value_cols = {2, 3} if incoming else {2}
        for r, row in enumerate(rows):
            kind = row.get("FromType" if incoming else "ToType", "")
            oid = row.get("FromID" if incoming else "ToID", "")
            try:
                oid_i = int(float(oid))
            except (TypeError, ValueError):
                oid_i = 0
            name = self._link_endpoint_name(kind, oid_i)
            cells = [name, kind]
            if incoming:
                cells.append(self._flow_cell_text(
                    row.get("nomFlow", ""), "MaxFlow", len(rows)))
                cells.append(self._flow_cell_text(
                    row.get("MaxFlow", ""), "FullFlow", len(rows)))
            else:
                cells.append(str(row.get("CapPri", "")))
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c not in value_cols:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                else:
                    n = len(rows)
                    keyword = "MaxFlow" if c == 2 else "FullFlow"
                    flow_tip = (
                        f"{keyword}: all of the flow generated at Start "
                        f"(Project Settings). {keyword}/{n}: an even share "
                        f"across this node's {n} incoming paths. Or a whole "
                        f"number: a fixed flow.")
                    item.setToolTip(
                        flow_tip if incoming else
                        "Capacity priority (CapPri): order this link is "
                        "served when flows compete; 1 first, 99 last.")
                t.setItem(r, c, item)
        t.cellChanged.connect(self._make_flow_commit(t, rows, incoming))
        t.resizeColumnsToContents()
        # Header names themselves must stay readable: content-hug alone
        # can size a narrow numeric column below its own header text
        # ("Nominal Flow" clipped). Width from max(cell content, header),
        # with a floor.
        header = t.horizontalHeader()
        for c, head in enumerate(headers):
            fm = t.fontMetrics()
            min_w = max(fm.horizontalAdvance(head) + 16,
                        t.columnWidth(c), 56)
            if t.columnWidth(c) < min_w:
                t.setColumnWidth(c, min_w)
        # Last section fills the remaining viewport width; otherwise the
        # strip right of the final column paints the table viewport's
        # default, which is black in the light theme.
        header.setStretchLastSection(True)
        t.setMaximumHeight(max(64, 28 * (len(rows) + 1) + 4))
        # insertWidget (not addWidget) -- the tab layout ends with a
        # trailing stretch item (see _group's own convention); addWidget
        # would land AFTER it, pushing the table below everything else
        # including the "Flow generated" note added later.
        layout.insertWidget(layout.count() - 1, t)

    def _make_flow_commit(self, t: QTableWidget, rows: list[dict],
                          incoming: bool):
        """cellChanged handler for an editable flow-assignment table:
        validates the number, routes ONE undoable IdCellCmd per commit
        (LINK_DATA nomFlow/MaxFlow for incoming, CapPri for outgoing),
        and reverts invalid input. `rows` are the LIVE LINK_DATA row
        dicts captured at populate time."""
        cols = (("nomFlow", "MaxFlow") if incoming else ("CapPri",))

        def commit(r: int, c: int):
            if c < 2 or r < 0 or r >= len(rows):
                return
            item = t.item(r, c)
            if item is None:
                return
            text = item.text().strip()
            row = rows[r]
            try:
                link_id = int(float(row.get("ID", "-1")))
            except (TypeError, ValueError):
                return
            col = cols[c - 2]
            if col == "CapPri":
                try:
                    new_v = int(float(text))
                except ValueError:
                    new_v = 0
                if not 1 <= new_v <= 99:
                    self.validation_failed.emit("a priority from 1 to 99")
                    item.setText(str(row.get(col, "")))
                    return
                if row.get(col, "") != str(new_v) and \
                        self.flow_cell_changed is not None:
                    self.flow_cell_changed.emit(link_id, col,
                                                row.get(col, ""), str(new_v))
                return
            label = "MaxFlow" if col == "nomFlow" else "FullFlow"
            # Each column has its own keyword ("MaxFlow" / "FullFlow"); they
            # share the stored sentinels, but accepting the wrong one made
            # the cell re-render with this column's spelling, which read as
            # "it picked the other one".
            parsed, problem = parse_flow_cell(text, label, len(rows))
            if problem is not None:
                self.validation_failed.emit(problem)
                item.setText(self._flow_cell_text(
                    row.get(col, ""), label, len(rows)))
                return
            new = str(parsed)
            if row.get(col, "") == new:
                return
            if self.flow_cell_changed is not None:
                self.flow_cell_changed.emit(link_id, col,
                                            row.get(col, ""), new)
        return commit

    def _populate_node_capacity(self, node: RBDNode):
        """Capacity tab (slot 1, reused from the block "Failure" slot):
        the two flow-routing checkboxes and the link tables the real Node
        Properties Capacity tab shows once a checkbox is unchecked. The
        identity columns are read-only; the flow/priority value columns
        are editable.
        """
        w = self._scroll_failure.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_failure)
        node_id = node.id
        into, out_of = self._link_rows_for_node(node_id)

        _gb, fl = self._group("Flow In", layout)
        unrestricted = QCheckBox("Unrestricted flow")
        unrestricted.setChecked(bool(node.full_flow))
        unrestricted.toggled.connect(
            lambda on: self.node_full_flow_changed.emit(node_id, on))
        fl.addRow(unrestricted)
        if not node.full_flow:
            self._flow_table(layout, into, incoming=True)

        _gb, fl = self._group("Flow Out", layout)
        any_path = QCheckBox("Use any available path")
        any_path.setChecked(bool(node.any_path))
        any_path.toggled.connect(
            lambda on: self.node_any_path_changed.emit(node_id, on))
        fl.addRow(any_path)
        if not node.any_path:
            self._flow_table(layout, out_of, incoming=False)

        # System-wide setting (Options/System Settings,
        # "Setting the Flow Generation...") -- echoed read-only
        # here, same as Raptor 7's own Node Properties Capacity tab,
        # so a user configuring flow routing can see it without leaving
        # this dialog. Not a per-node value despite living on this tab.
        sec = self._rbd.sections.get("SYSTEM_DATA") if self._rbd else None
        if sec and sec.rows:
            cfg = system_config_from_row(sec.rows[0])
            note = QLabel(f"Flow generated = {cfg.flow_generated}")
            _vbox(layout).insertWidget(req(layout).count() - 1, note)

    def _populate_node_standby(self, node: RBDNode):
        """Standby tab (slot 2, reused from the block "Repair" slot):
        the three checkboxes and k* value from the real Node Properties
        Standby tab. The underlying fields
        (Cold/PR/Autos/k* columns) are read by `typed.py`."""
        w = self._scroll_repair.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_repair)
        node_id = node.id

        gb, fl = self._group("Switchover", layout)
        cold_cb = QCheckBox("Engage standby logic for this node")
        cold_cb.setChecked(bool(node.cold_standby))
        cold_cb.setToolTip(
            "The first checkbox, which engages the remainder of the tab.")
        cold_cb.toggled.connect(
            lambda on: self.node_cold_standby_changed.emit(node_id, on))
        fl.addRow(cold_cb)

        pr_cb = QCheckBox("Return alternates to standby once the "
                          "primary is repaired")
        pr_cb.setChecked(bool(node.priority_return))
        pr_cb.setEnabled(bool(node.cold_standby))
        pr_cb.setToolTip(
            "If unchecked, an alternate that becomes a primary stays "
            "a primary until it itself fails.")
        pr_cb.toggled.connect(
            lambda on: self.node_priority_return_changed.emit(
                node_id, on))
        fl.addRow(pr_cb)

        autos_cb = QCheckBox("Try automatic switchover before manual")
        autos_cb.setChecked(bool(node.check_autos_first))
        autos_cb.setEnabled(bool(node.cold_standby))
        autos_cb.toggled.connect(
            lambda on: self.node_check_autos_first_changed.emit(
                node_id, on))
        fl.addRow(autos_cb)

        gb, fl = self._group("k* Target", layout)
        gb.setToolTip(
            "The number of paths a user would DESIRE operating at any "
            "time (>= k, <= n) -- distinct from k, the minimum "
            "required. Default equals k.")
        # k* is upper-bounded by n: `n` is its own raw NODE_DATA column
        # (see "Total paths (n)" above), not
        # `RBDNode.connect_out_of_num`/"Out", which can differ. Clamp
        # only when the file actually stores one.
        raw_row = self._raw_node_row(node.id)
        n_max = None
        if raw_row is not None and raw_row.get("n", "") != "":
            n_max = _i_raw(raw_row, "n")
        # Lower bound = k (original rule: k* >= k), upper = n when the
        # file stores one.
        min_k = max(0, int(node.good_paths))
        self._int_row(fl, "k*", node.k_star,
                      lambda v: self.node_k_star_changed.emit(node_id, v),
                      minimum=min_k, maximum=n_max)

        # Per-incoming-link switchover table:
        # Priority / Auto Prob / Auto Time / Man Time. The columns exist
        # in LINK_DATA and the engine consumes them.
        gb, fl = self._group("Incoming link switchover", layout)
        gb.setToolTip(
            "Per incoming link: cold-standby switchover priority, "
            "automatic switchover probability and time, and the manual "
            "switchover time.")
        into, _out = self._link_rows_for_node(node.id)
        self._standby_table(layout, into)

    def _standby_table(self, layout, rows: list[dict]):
        headers = ["Link From", "Priority", "Auto Prob", "Auto Time",
                   "Manual Time"]
        t = QTableWidget(len(rows), len(headers))
        t.setAccessibleName("Standby switchover by incoming link")
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for r, row in enumerate(rows):
            try:
                oid_i = int(float(row.get("FromID", "0")))
            except (TypeError, ValueError):
                oid_i = 0
            name = self._link_endpoint_name(row.get("FromType", ""), oid_i)
            cells = [name, str(row.get("ColdPri", "")),
                     f"{_row_float(row.get('AProb', '')):g}",
                     f"{_row_float(row.get('AutoTime', '')):g}",
                     str(row.get("ManTime", "")) or "N/A"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                t.setItem(r, c, item)
        t.cellChanged.connect(self._make_standby_commit(t, rows))
        t.resizeColumnsToContents()
        header = t.horizontalHeader()
        for c, head in enumerate(headers):
            fm = t.fontMetrics()
            min_w = max(fm.horizontalAdvance(head) + 16,
                        t.columnWidth(c), 56)
            if t.columnWidth(c) < min_w:
                t.setColumnWidth(c, min_w)
        header.setStretchLastSection(True)
        t.setMaximumHeight(max(64, 28 * (len(rows) + 1) + 4))
        layout.insertWidget(layout.count() - 1, t)
        if not rows:
            note = QLabel("No incoming links")
            layout.insertWidget(layout.count() - 1, note)

    def _make_standby_commit(self, t: QTableWidget, rows: list[dict]):
        cols = ("ColdPri", "AProb", "AutoTime", "ManTime")

        def _reset(r: int, c: int, col: str):
            row = rows[r]
            it = t.item(r, c)
            if it is None:
                return
            if col == "ManTime":
                it.setText(str(row.get(col, "")) or "N/A")
            elif col in ("AProb", "AutoTime"):
                it.setText(f"{_row_float(row.get(col, '')):g}")
            else:
                it.setText(str(row.get(col, "")))

        def commit(r: int, c: int):
            if c < 1 or r < 0 or r >= len(rows):
                return
            item = t.item(r, c)
            if item is None:
                return
            row = rows[r]
            try:
                link_id = int(float(row.get("ID", "-1")))
            except (TypeError, ValueError):
                return
            col = cols[c - 1]
            old = row.get(col, "")
            text = item.text().strip()
            if col == "ManTime" and text.upper() in ("N/A", "NA", "NONE"):
                new = "N/A"
            elif col == "ColdPri":
                try:
                    v = int(float(text))
                except ValueError:
                    self.validation_failed.emit("not a number")
                    _reset(r, c, col)
                    return
                if not (1 <= v <= 99):
                    self.validation_failed.emit(
                        "Priority must be between 1 and 99")
                    _reset(r, c, col)
                    return
                new = str(v)
            else:
                try:
                    fv = float(text)
                except ValueError:
                    self.validation_failed.emit("not a number")
                    _reset(r, c, col)
                    return
                if col == "AProb" and not (0.0 < fv <= 1.0):
                    self.validation_failed.emit(
                        "Auto switch probability must be greater than 0 "
                        "and at most 1")
                    _reset(r, c, col)
                    return
                if col in ("AutoTime", "ManTime") and \
                        not (0.0 <= fv <= 999999999.0):
                    self.validation_failed.emit(
                        f"{col} must be between 0 and 999,999,999")
                    _reset(r, c, col)
                    return
                new = f"{fv:g}"
            if new == old:
                return
            self.link_standby_cell_changed.emit(link_id, col, old, new)
        return commit

    def _populate_node_dependency(self, node: RBDNode):
        """Dependency tab (slot 5, reused from the block "Deps" slot):
        independent / locally dependent / system dependent / dependent-
        on-a-block, matching the real Node Properties Dependency tab.
        `node.dependency_num`/`dep_type` ARE read by the engine (e.g.
        `raptor.core.engine.dependency.resolve_dependency_node_id`,
        `cold_standby.py`'s own back-flow checks), unlike a hierarchy's
        -- see `show_hier`'s docstring for why that one stays read-only/
        absent."""
        w = self._scroll_deps.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_deps)
        self._dependency_group(layout, node.id, node.dependency_num,
                               node.dep_type)

    def _populate_node_advanced(self, node: RBDNode):
        """Advanced tab (slot 6, reused from the block "Costs" slot):
        phasing, matching the real Node Properties Advanced tab's
        "changes properties during phasing" checkbox, with the per-phase
        K/Cut/Linked editor as the fuller version of that control. Also
        the Raptor 7 dialog's other Advanced checkbox, "Include node analysis
        information..." (`repNA`/`report_node_anal`)."""
        w = self._scroll_costs.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_costs)
        node_id = node.id
        _gb, fl = self._group("Reporting", layout)
        report_cb = QCheckBox(
            "Include node analysis information for this node in the "
            "output tables and reports")
        report_cb.setChecked(bool(node.report_node_anal))
        report_cb.toggled.connect(
            lambda on: self.node_report_anal_changed.emit(node_id, on))
        fl.addRow(report_cb)
        if node.type_node == 2:
            entries = []
            for v in (node.phase or []):
                if v == -1:
                    entries.append(("C", float(v)))
                elif v == 0:
                    entries.append(("L", 0.0))
                else:
                    entries.append(("K", float(v)))
            self._phasing_group(layout, "node", node.id,
                                node.uses_phasing, entries)
        else:
            _gb, fl = self._group("Phasing", layout)
            self._row(fl, "Status",
                      "Not applicable to this node type")

    def _dependency_group(self, layout: QLayout | None, oid: int,
                          dependency_num: int, dep_type: str,
                          emit=None):
        """Shared independent/locally-dependent/system-dependent/
        dependent-on-a-block master-element selector (Node/Block
        Properties Dependency tab -- not Hierarchy, see `show_hier`'s
        docstring for why that one stays read-only/absent). Semantics per
        `raptor.core.engine.dependency.resolve_dependency_node_id` and
        `cold_standby.py`: 0=independent, -1=locally dependent,
        -2=system dependent, >0 with dep_type=="RBDBlock"=dependent on
        that block id.

        `emit`: callable(oid, dependency_num, dep_type), defaults to
        `self.node_dep_changed.emit`. Blocks pass
        `self.block_dep_master_changed.emit`, routing the output to the
        right element kind's own signal instead of always Node's."""
        layout = _vbox(layout)
        emit = emit or self.node_dep_changed.emit
        gb, fl = self._group("Dependency", layout)
        r_indep = QRadioButton("Independent")
        r_local = QRadioButton("Locally dependent")
        r_sys = QRadioButton("System dependent")
        r_on = QRadioButton("Dependent on:")
        # Tooltips for the four dependency choices; wording follows the
        # dependency.py engine semantics cited above, plus the manual's
        # dependency chapter.
        r_indep.setToolTip(
            "Independent: answers to nothing but its own clocks. No "
            "other element's state can idle, hold or trigger it.")
        r_local.setToolTip(
            "Locally dependent: tied to this element's own downstream "
            "node. When that node's path goes down, this element is "
            "pulled into Idle (paused, failure clock stops) and resumes "
            "when the path is up again.")
        r_sys.setToolTip(
            "System dependent: tied to the whole model's up/down "
            "status (across every hierarchy). Down system = Idle here; "
            "up system = resume.")
        r_on.setToolTip(
            "Dependent on one named element (master): when THAT element "
            "goes down this element idles -- not failed, just paused, "
            "its own failure clock stops -- and it picks up where it "
            "left off when the master comes back.")
        group = QButtonGroup(gb)
        for r in (r_indep, r_local, r_sys, r_on):
            group.addButton(r)
            fl.addRow(r)

        combo = QComboBox()
        blocks: list[tuple[int, str]] = []
        sec = self._rbd.sections.get("BLOCK_DATA") if self._rbd else None
        if sec is not None:
            for row in sec.rows:
                try:
                    bid = int(float(row.get("ID", "-1")))
                except (ValueError, KeyError):
                    continue
                name = row.get("blockName", "")
                # Whitespace-delimited rows can carry blank names; an
                # unnamed master is invalid input, so skip those entries.
                if not name or not name.strip():
                    continue
                blocks.append((bid, name))
        blocks.sort()

        def fill_combo() -> None:
            """Populate the master list on first use.

            Building ~3,500 QComboBox items is ~20-70 ms and it happened
            for every node/block selection, even though the combo is
            disabled unless "Dependent on:" is chosen (or the element
            already is). Lazy, so an independent element never pays it.
            """
            if combo.count():
                return
            for bid, name in blocks:
                combo.addItem(f"{name} block - {bid:03d}", bid)

        # A saved dependency needs its current entry present immediately
        # (findData below), so only the common independent case is lazy.
        if dependency_num > 0:
            fill_combo()
        fl.addRow("", combo)

        def populate() -> None:
            if dependency_num == -1:
                r_local.setChecked(True)
            elif dependency_num == -2:
                r_sys.setChecked(True)
            elif dependency_num > 0:
                r_on.setChecked(True)
                ci = combo.findData(dependency_num)
                if ci >= 0:
                    combo.setCurrentIndex(ci)
            else:
                r_indep.setChecked(True)
            combo.setEnabled(r_on.isChecked() and combo.count() > 0)

        def send() -> None:
            if r_on.isChecked():
                fill_combo()
            combo.setEnabled(r_on.isChecked() and combo.count() > 0)
            if r_indep.isChecked():
                emit(oid, 0, "")
            elif r_local.isChecked():
                emit(oid, -1, "")
            elif r_sys.isChecked():
                emit(oid, -2, "")
            elif r_on.isChecked() and combo.count():
                emit(oid, combo.currentData(), "RBDBlock")

        populate()
        for r in (r_indep, r_local, r_sys, r_on):
            r.toggled.connect(lambda checked: send() if checked else None)
        combo.activated.connect(
            lambda _i: send() if r_on.isChecked() else None)
        if not blocks:
            r_on.setEnabled(False)
            r_on.setToolTip("No blocks in this model")
