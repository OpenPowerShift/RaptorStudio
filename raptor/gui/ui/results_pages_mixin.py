"""Results pages: capacity, weak links, cost and importance tables, with level filtering.

Expects on `self`: the results tabs and the report data held by `ResultsDock`."""
from __future__ import annotations

from raptor.gui.ui.results_common import (
    IMPORTANCE_HEADER_TIPS,
    RAW_VALUE_ROLE,
    _ReadOnlyTable,
    _SortItem,
    _StatefulTable,
    _TableActionStrip,
    _ViewportContextMenuFilter,
    _cell_parts,
)

import math
import re
import weakref

from raptor.gui.ui.qtcompat import req



from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QSizePolicy, QTableWidget, QApplication,
)

from raptor.core.engine import weak_metric
from raptor.core.engine.reports import flow_loss_across_runs
from raptor.gui.ui import filedialogs as fdg
from raptor.gui.resources.design_tokens import SPACING, active_colour

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _Base
else:
    _Base = object


class ResultsPagesMixin(_Base):
    """Results pages: capacity, weak links, cost and importance tables, with level filtering."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _allowed_names: Any
        _cap_table: Any
        _cost_blocks_label: Any
        _cost_blocks_title: Any
        _cost_tables: Any
        _detail_tables: Any
        _importance_metric: Any
        _selected_block_names_fn: Any
        _weak_anal_type: Any
        _weak_gy: Any
        _weak_rebuilding: Any
        _weak_sel_only: Any
        _weak_yr: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    @staticmethod
    def _capacity_node_ids(caps, sys_nid: int) -> list[int]:
        """Node rows for the Capacity tab, system/Start row first.

        Names are collected across every run: a node present only in a
        later run used to raise KeyError on caps[0] and lose the tab.
        Unknown names fall back to the id so the sort still works.
        """
        node_names: dict[int, str] = {}
        for cap in caps:
            for nid, stats in cap.nodes.items():
                node_names.setdefault(nid, stats.name)
        ids = sorted({nid for cap in caps for nid in cap.nodes},
                     key=lambda nid: node_names.get(nid, str(nid)).lower())
        if sys_nid in ids:
            ids.remove(sys_nid)
            ids.insert(0, sys_nid)
        return ids

    def _build_capacity_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(SPACING["md"], SPACING["md"], SPACING["md"], SPACING["md"])
        v.setSpacing(SPACING["sm"])

        caps = [d.capacity for d in self._report_data if d.capacity is not None]
        if not caps:
            msg = QLabel(
                "No capacity analysis in this model.\n\n"
                "Enable Capacity in the Run panel's Analysis section, "
                "give Start a Flow Generated value, and run again.")
            msg.setObjectName("SectionNote")
            msg.setWordWrap(True)
            v.addWidget(msg)
            v.addStretch(1)
            return page

        def mean(getter):
            vals = [v for v in (getter(c) for c in caps) if v is not None]
            return sum(vals) / len(vals) if vals else None

        # The same figures `simulate` and describe_metrics report
        # (`raptor.api.results.capacity_values`).
        mean_flow = mean(lambda c: c.nodes[c.system_node_id].ave_flow)
        generated = caps[0].flow_generated
        weighted = (f"   ·   Mean flow: {mean_flow:,.2f} "
                    f"(capacity-weighted availability {mean_flow / generated:.2%})"
                    if mean_flow is not None and generated else "")
        head = QLabel(
            f"Flow generated: {generated:,}   ·   "
            # Units produced is a flow quantity, not a duration: appending
            # the time unit made it read "1,513,838.93 hours".
            f"Units produced: {mean(lambda c: c.units_produced):,.2f}{weighted}   ·   "
            f"Flow loss (min · avg · max): "
            + " · ".join(f"{x:,.2f}" for x in flow_loss_across_runs(caps)))
        head.setObjectName("SummarySentence")
        head.setWordWrap(True)
        head.setToolTip(
            "Flow Generated: supply fed into the Start node each trial.\n"
            "Units Produced: total flow that made it through the system.\n"
            "Mean flow: the Start node's time-averaged flow; divided by Flow Generated it is the\n"
            "capacity-weighted availability, the share of the flow delivered (Ao only asks whether\n"
            "any flow got through).\n"
            "Flow loss: Flow Generated less the flow that got through, as the Final Results report prints it\n"
            "(minimum, average and maximum over the runs).")
        v.addWidget(head)


        # Row order per Raptor 7's Capacity tab (Raptor 7 manual Fig. 14.12:
        # the system-level row FIRST, then the capacity nodes). The
        # Start node IS the system row (reports carry its index as
        # `system_node_id`); remaining nodes alphabetical.
        sys_nid = caps[0].system_node_id
        node_ids = self._capacity_node_ids(caps, sys_nid)
        rows = []
        for nid in node_ids:
            # Snapshot the per-run stats once, avoiding late-binding
            # closures over the loop variable.
            per_run = [cap.nodes[nid] for cap in caps if nid in cap.nodes]
            if not per_run:
                continue
            # Raptor prints N/A for the capacity columns of full-flow
            # boundary nodes, but the SystemCapacity row is special: it
            # always carries the Start node's real figures (including
            # capability), even when that node is flagged full-flow.
            is_system = nid == sys_nid
            cap_na = any(s.full_flow for s in per_run) and not is_system

            def avg(attr, runs=per_run):
                vals = [getattr(s, attr) for s in runs]
                return sum(vals) / len(vals) if vals else None

            def avg_nums(vals):
                return sum(vals) / len(vals) if vals else None

            fmt = lambda val, dec=2: (
                "N/A" if val is None else f"{val:,.{dec}f}")
            run_min_flow = [s.min_flow for s in per_run]
            run_max_flow = [s.max_flow for s in per_run]
            run_min_cap = [s.min_cap for s in per_run]
            run_max_cap = [s.max_cap for s in per_run]
            # Numeric cells carry (display, raw) so Copy Table hands the
            # full-precision number, not the rounded grid text. N/A cells
            # have no raw value.
            rows.append([
                "SystemCapacity" if is_system else per_run[0].name,
                (f"{int(min(run_min_flow)):,}", min(run_min_flow)),
                (fmt(avg_nums(run_min_flow), 2), avg_nums(run_min_flow)),
                (fmt(avg("ave_flow"), 4), avg("ave_flow")),
                (fmt(avg_nums(run_max_flow), 2), avg_nums(run_max_flow)),
                (f"{int(max(run_max_flow)):,}", max(run_max_flow)),
                ("N/A", None) if cap_na else
                (fmt(avg("usage"), 4), avg("usage")),
                ("N/A", None) if cap_na else
                (f"{int(min(run_min_cap)):,}", min(run_min_cap)),
                ("N/A", None) if cap_na else
                (fmt(avg_nums(run_min_cap), 2), avg_nums(run_min_cap)),
                ("N/A", None) if cap_na else
                (fmt(avg("ave_cap"), 4), avg("ave_cap")),
                ("N/A", None) if cap_na else
                (fmt(avg_nums(run_max_cap), 2), avg_nums(run_max_cap)),
                ("N/A", None) if cap_na else
                (f"{int(max(run_max_cap)):,}", max(run_max_cap)),
                ("N/A", None) if cap_na else
                (fmt(avg("capability"), 4), avg("capability")),
            ])
        table = self._make_table(self._CAP_HEADERS, rows)
        table.setToolTip(
            "Flow/capacity through each capability node, averaged over the runs.\n"
            "Usage = AveFlow/AveCap · Capability = AveCap/MaxCap.\n"
            "Full-flow boundary nodes show N/A usage/capability.")
        self._cap_table = table
        self._add_table_header(v, "Capacity by node", table)
        v.addWidget(table)
        # Capacity is planned by throughput: the busiest node is the one
        # that matters, so start on average flow descending (#208).
        self._apply_sort(table, "CAPACITY",
                         (3, Qt.SortOrder.DescendingOrder))
        v.addStretch(1)
        return page

    @staticmethod
    def _band(value: float, gy: float, yr: float):
        """(colour-key, band name) per Raptor 7 thresholds (higher=better)."""
        if value >= gy:
            return "result_green", "Green"
        if value >= yr:
            return "result_yellow", "Yellow"
        return "result_red", "Red"

    @staticmethod
    def _stats(vals):
        """(mean, min, max, stdev, sem) — the Weak Link table's columns."""
        n = len(vals)
        mean = sum(vals) / n
        if n < 2:
            return mean, min(vals), max(vals), None, None
        var = sum((v - mean) ** 2 for v in vals) / (n - 1)
        sd = var ** 0.5
        return mean, min(vals), max(vals), sd, sd / n ** 0.5

    @staticmethod
    def _weak_metric_rows(runs, buckets_up, buckets_down, principal=False,
                          missions=None):
        """Per-element metric rows: [(name, [ratio_per_run])].

        Explicit bucket sets, because this is called three ways with three
        DIFFERENT semantics: the block Availability/Dependability split, the node-band
        equivalent, and the per-state "% of time" breakdown, which passes one state against all the others. Only the
        first two correspond to the named analyses, so the analysis name
        cannot be inferred here -- hence the parameters.

        The `up/(up+down)` arithmetic is `core.engine.weak_metric`'s, so
        the ratio is computed in one place even though the bucket CHOICE
        is per-call. `principal` is retained for call compatibility and
        ignored: both branches of the old `if principal:` were
        byte-identical, so it never changed a number.

        `missions` (one `missions_attempted` per run, aligned with `runs`)
        switches the rows to Reliability: `m_completed / missions`, not a
        time ratio, so the bucket sets are ignored.
        """
        up_keys, down_keys = tuple(buckets_up), tuple(buckets_down)
        out = []
        for eid in sorted(runs[0]):
            per_run = [ws[eid] for ws in runs if eid in ws]
            if not per_run:
                continue
            name = per_run[0].get("name", str(eid))
            vals = []
            if missions is not None:
                vals = [weak_metric.reliability(ws[eid], missions[i])
                        for i, ws in enumerate(runs) if eid in ws]
                out.append((name, vals))
                continue
            for x in per_run:
                up = sum(x.get(k, 0.0) for k in up_keys)
                down = sum(x.get(k, 0.0) for k in down_keys)
                denom = up + down
                # Empty denominator is Raptor's 1.0 fallback, not a
                # skipped run. Skipping it made the table mean over the
                # remaining runs (Phasing3 HighGain: 0.700) while the
                # canvas and engine both reported 0.971.
                vals.append(up / denom if denom else 1.0)
            if vals:
                out.append((name, vals))
        return out

    def _goto_selected_row(self, table: QTableWidget, kind: str) -> None:
        row = table.currentRow()
        if row < 0:
            self._status_hint_f and self._status_hint_f(
                "Select a row first")
            return
        item = table.item(row, 0)
        if item is not None:
            # Full name (elision keeps it in UserRole data)
            name = item.data(Qt.ItemDataRole.UserRole) or item.text()
            self.element_clicked.emit(kind, name)

    def _goto_menu_at(self, table: QTableWidget, kind: str, pos):
        """The Weak Link row menu at `pos`, or None on empty space.

        Kept separate from `QMenu.exec` so tests can inspect the action
        without spinning an event loop; the signal handler below is the
        only caller that shows it.
        """
        from PySide6.QtWidgets import QMenu
        index = table.indexAt(pos)
        if not index.isValid() or index.row() < 0:
            return None
        row = index.row()
        # A context action must act on the row the user right-clicked,
        # not on whatever was current before.
        table.setCurrentCell(row, max(index.column(), 0))
        table.selectRow(row)
        menu = QMenu(table)
        action = menu.addAction("Go to selected element")
        action.triggered.connect(
            lambda _checked=False, t=table, k=kind:
            self._goto_selected_row(t, k))
        return menu

    def _show_goto_menu(self, table: QTableWidget, kind: str, pos) -> None:
        menu = self._goto_menu_at(table, kind, pos)
        if menu is not None:
            try:
                menu.exec(table.viewport().mapToGlobal(pos))
            finally:
                menu.deleteLater()

    def attach_goto_menu(self, table: QTableWidget, kind: str) -> None:
        """Give a Weak Link table a right-click "Go to selected element"
        action, sharing the double-click signal path (#211)."""
        if getattr(table, "_goto_menu_filter", None) is not None:
            return
        # Handle the real viewport ContextMenu event: relying only on
        # `customContextMenuRequested` left the menu silent in use.
        table_ref = weakref.ref(table)

        def on_context_menu(pos, tr=table_ref, k=kind) -> None:
            target = tr()
            if target is not None:
                self._show_goto_menu(target, k, pos)

        cast(_StatefulTable, table)._goto_menu_filter = (
            _ViewportContextMenuFilter(table.viewport(), on_context_menu))

    def _build_weak_page(self) -> QWidget:
        """Weak Link Analysis: a colour chip per element,
        the chosen metric (Availability or Dependability — Raptor 7's
        "Analysis Type"), and across-run min/max/stdev/SEM like Fig
        11.6's Output Tables. A model with no Weak Link data still gets a
        tab with an explanatory message rather than nothing."""
        runs = [d.weak_stats for d in self._report_data if d.weak_stats]
        run_missions = [d.missions_attempted for d in self._report_data
                        if d.weak_stats]
        if not runs:
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(SPACING["md"], SPACING["md"],
                                 SPACING["md"], SPACING["md"])
            v.setSpacing(SPACING["sm"])
            msg = QLabel(
                "No Weak Link analysis in this model.\n\n"
                "Weak Link Analysis is turned on in the Run panel's "
                "Analysis section — enable it and rerun to see "
                "per-element availability/dependability here.")
            msg.setObjectName("SectionNote")
            msg.setWordWrap(True)
            v.addWidget(msg)
            v.addStretch(1)
            return page
        from PySide6.QtWidgets import QCheckBox
        c = active_colour()
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(SPACING["md"], SPACING["md"],
                             SPACING["md"], SPACING["md"])
        v.setSpacing(SPACING["sm"])

        head = QLabel(
            f"Weak Link Analysis — {len(runs)} run(s). Colour bands "
            f"and metric come from Results ▸ Bands; hover any cell or "
            f"chip for details.")
        head.setObjectName("SummarySentence")
        head.setWordWrap(True)
        v.addWidget(head)


        sel_chk = QCheckBox("Selected elements only")
        sel_chk.setToolTip("Show only elements whose blocks are "
                           "selected on the canvas")
        sel_chk.setChecked(getattr(self, "_weak_sel_only", False))
        def _on_sel_only(on):
            self._weak_sel_only = on
            self._rebuild_weak_page()
        sel_chk.toggled.connect(_on_sel_only)
        v.addWidget(sel_chk)
        sel_note = QLabel(
            "No canvas selection — select one or more blocks, or clear "
            "“Selected elements only”.")
        sel_note.setObjectName("SectionNote")
        sel_note.setWordWrap(True)
        selected: list[str] = (self._selected_block_names_fn()
                               if self._selected_block_names_fn else [])
        sel_note.setVisible(bool(getattr(self, "_weak_sel_only", False)
                                 and not selected))
        v.addWidget(sel_note)

        kind = weak_metric.canonical(self._weak_anal_type)
        dep = kind == weak_metric.DEPENDABILITY
        rel = kind == weak_metric.RELIABILITY

        def metric_cell_tooltip(name, val, mean_v):
            _, band = self._band(mean_v, self._weak_gy, self._weak_yr)
            return (f"{name}\n{kind} = {val:.6f} "
                    f"→ {band} band\n(Green ≥ {self._weak_gy:.2f}, "
                    f"Yellow ≥ {self._weak_yr:.2f}, Red below)")

        def fill_table(table, rows_data):
            # Raptor 7's Output Tables keep the Min/Max/StdDev/SEM
            # columns even for one run. StdDev/SEM are blank there
            # (undefined with n=1), rather than hiding the columns and
            # changing the table's shape between run counts.
            headers = ["Name", "Colour", kind, "Min", "Max",
                       "StdDev", "SEM"]
            table.setColumnCount(len(headers))
            table.setHorizontalHeaderLabels(headers)
            # This table abbreviates further than STAT_COLUMN_TOOLTIPS's
            # own keys (Min/Max/StdDev vs. Minimum/Maximum/St.Dev).
            _short_stat = {"Min": "Minimum", "Max": "Maximum",
                          "StdDev": "St.Dev", "SEM": "SEM"}
            for col_i, h in enumerate(headers):
                h_item = table.horizontalHeaderItem(col_i)
                if h_item is not None:
                    tip = self._stat_col_tip(_short_stat.get(h, h))
                    if tip:
                        h_item.setToolTip(tip)
                    if h == "Colour":
                        h_item.setToolTip(
                            "Weak Link band: Green ≥ "
                            f"{self._weak_gy:.2f}, Yellow ≥ "
                            f"{self._weak_yr:.2f}, Red below.\n"
                            + "\n".join(self._WEAK_COLOUR_TIPS.values()))
            allowed = (self._selected_block_names_fn()
                       if (getattr(self, "_weak_sel_only", False) and
                           self._selected_block_names_fn) else None)
            r = 0
            for name, vals in rows_data:
                if allowed is not None and name not in allowed:
                    continue
                mean_v, mn, mx, sd, sem = self._stats(vals)
                table.insertRow(r)
                n_it = _SortItem(name)
                table.setItem(r, 0, n_it)
                ck, band = self._band(mean_v, self._weak_gy,
                                      self._weak_yr)
                chip = _SortItem(band)
                chip.setBackground(QColor(c[ck]))
                chip.setToolTip(
                    f"{band} band: "
                    f"{self._WEAK_COLOUR_TIPS[band.casefold()]}\n"
                    f"Thresholds: Green ≥ {self._weak_gy:.2f}, "
                    f"Yellow ≥ {self._weak_yr:.2f}, Red below.")
                chip.setFlags(chip.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(r, 1, chip)
                v_it = _SortItem(f"{mean_v:.9f}")
                # Raw value rides along for full-precision Copy Table:
                # the grid shows 9 decimals, the clipboard gets the exact
                # double.
                v_it.setData(RAW_VALUE_ROLE, mean_v)
                v_it.setToolTip(metric_cell_tooltip(
                    name, mean_v, mean_v))
                table.setItem(r, 2, v_it)
                for col, val in zip((3, 4, 5, 6), (mn, mx, sd, sem)):
                    it_s = _SortItem(
                        "" if val is None else f"{val:.9f}")
                    if val is not None:
                        it_s.setData(RAW_VALUE_ROLE, val)
                    table.setItem(r, col, it_s)
                r += 1

        def size_to_content(table: QTableWidget) -> None:
            # Full height, no cap -- see _make_table's own comment; the
            # page-level scroll area below handles overflow instead of
            # each table's own small internal scrollbar.
            full = (table.horizontalHeader().height()
                    + table.verticalHeader().defaultSectionSize()
                    * table.rowCount() + 4)
            # Both bounds: setting only the maximum left the table at its
            # 192px sizeHint, so it kept an internal scrollbar and never
            # filled the page.
            table.setMinimumHeight(full)
            table.setMaximumHeight(full)

        # Blocks: full bucket split. From
        # `core.engine.weak_metric`, the same owner `main_window`'s canvas
        # overlay now uses -- the two previously disagreed on whether `idle`
        # is in the Dependability denominator, so the same block could be
        # green in this table and red on the canvas. See that module for why
        # `idle` is excluded.
        bup, bdown = weak_metric.buckets(kind)
        block_rows = self._weak_metric_rows(
            runs, bup, bdown, missions=run_missions if rel else None)
        btable = _ReadOnlyTable(0, 3)
        fill_table(btable, block_rows)
        self._apply_sort(btable, "WEAK::BLOCKS",
                         (2, Qt.SortOrder.DescendingOrder))
        size_to_content(btable)
        # Double-click and the row context menu both navigate on the
        # element_clicked signal the Event Log already uses.
        btable.cellDoubleClicked.connect(
            lambda r, _c: self._goto_selected_row(btable, "block"))
        self.attach_goto_menu(btable, "block")
        blabel = QLabel(
            "Blocks — "
            + ("reliability is the fraction of attempted missions the "
               "block was up through" if rel else
               "dependability excludes standby/PM/dependency time "
               "(principal states only)" if dep else
               "availability counts standby (blue) as uptime"))
        blabel.setObjectName("SectionNote")
        v.addWidget(blabel)
        self._add_table_header(v, "Blocks", btable, tip=blabel.text(),
                               show_title=False)
        v.addWidget(btable)

        # Nodes.
        # Raptor's NODE_RESULTS lists regular nodes only (`typeNode=2`
        # and reportNodeAnal); Start/End/In/Out are markers, not weak
        # links, and have no bucket time of their own.
        node_runs = [
            {nid: st for nid, st in d.weak_nodes.items()
             if st.get("type_node") == 2 and st.get("report_anal", True)}
            for d in self._report_data if getattr(d, "weak_nodes", None)]
        node_missions = [d.missions_attempted for d in self._report_data
                         if getattr(d, "weak_nodes", None)]
        if node_runs:
            nup = ("green", "yellow", "blue") if not dep else \
                  ("green", "yellow")
            ndown = ("red", "brown", "orange") if not dep else ("red",)
            node_rows = self._weak_metric_rows(
                node_runs, nup, ndown,
                missions=node_missions if rel else None)
            ntable = _ReadOnlyTable(0, 3)
            fill_table(ntable, node_rows)
            self._apply_sort(ntable, "WEAK::NODES",
                             (2, Qt.SortOrder.DescendingOrder))
            size_to_content(ntable)
            ntable.cellDoubleClicked.connect(
                lambda r, _c: self._goto_selected_row(ntable, "node"))
            self.attach_goto_menu(ntable, "node")
            nlabel = QLabel(
                "Nodes — same bands; reliability is the fraction of "
                "attempted missions the node was up through" if rel else
                "Nodes — same bands; dependability "
                "excludes blue/brown/orange")
            nlabel.setObjectName("SectionNote")
            v.addWidget(nlabel)
            self._add_table_header(v, "Nodes", ntable, tip=nlabel.text(),
                                   show_title=False)
            v.addWidget(ntable)

            # Per-state % breakdown (Raptor 7 "State_Data" table):
            # the raw numbers behind the single rolled-up Availability/
            # Dependability figure above. Reuses `_weak_metric_rows` per
            # state (state / everything-else) so the mean-across-runs math
            # matches the table above; not gated by the Analysis Type
            # toggle, so every state shows.
            pct_label = QLabel(
                "Node state breakdown — % of total time in each state, "
                "averaged across runs.")
            pct_label.setObjectName("SectionNote")
            pct_label.setWordWrap(True)
            v.addWidget(pct_label)
            states = ("green", "yellow", "red", "blue", "brown", "orange")
            per_state = {s: dict(self._weak_metric_rows(
                node_runs, (s,), tuple(o for o in states if o != s)))
                for s in states}
            allowed = (self._selected_block_names_fn()
                      if (getattr(self, "_weak_sel_only", False) and
                          self._selected_block_names_fn) else None)
            names = sorted({name for rows in per_state.values()
                           for name in rows
                           if allowed is None or name in allowed})
            pct_table = _ReadOnlyTable(len(names), 1 + len(states))
            pct_table.setHorizontalHeaderLabels(
                ["Name"] + [f"% {s.capitalize()}" for s in states])
            for ci, s in enumerate(states, start=1):
                h_item = pct_table.horizontalHeaderItem(ci)
                if h_item is not None:
                    h_item.setToolTip(
                        f"% of total node time in the {s.capitalize()} "
                        f"state.\n{self._WEAK_COLOUR_TIPS[s]}")
            for r, name in enumerate(names):
                pct_table.setItem(r, 0, _SortItem(name))
                for ci, s in enumerate(states, start=1):
                    vals = per_state[s].get(name, [0.0])
                    mean_v, *_rest = self._stats(vals)
                    it = _SortItem(f"{mean_v * 100:.2f}")
                    it.setData(RAW_VALUE_ROLE, mean_v * 100)
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    it.setToolTip(
                        f"{self._WEAK_COLOUR_TIPS[s]}\n"
                        f"{mean_v * 100:.2f}% of total time")
                    pct_table.setItem(r, ci, it)
            self._apply_sort(pct_table, "WEAK::STATES",
                             (0, Qt.SortOrder.AscendingOrder))
            size_to_content(pct_table)
            pct_table.cellDoubleClicked.connect(
                lambda r, _c: self._goto_selected_row(pct_table, "node"))
            self.attach_goto_menu(pct_table, "node")
            self._add_table_header(v, "Node state breakdown", pct_table,
                                   tip=pct_label.text(), show_title=False)
            v.addWidget(pct_table)

        v.addStretch(1)
        return self._scroll_wrap(page)

    def set_weak_settings(self, anal_type: str, gy: float, yr: float):
        """MainWindow pushes the model's Raptor 7 settings here.

        The Weak tab is built from these values, so an existing tab must
        be rebuilt when the metric or bands change; leaving it alone made
        the table contradict the canvas and status bar.
        """
        old = (self._weak_anal_type, self._weak_gy, self._weak_yr)
        self._weak_anal_type = anal_type or "Availability"
        self._weak_gy, self._weak_yr = float(gy), float(yr)
        if old == (self._weak_anal_type, self._weak_gy, self._weak_yr):
            return
        tabs = getattr(self, "_details_tabs", None)
        if tabs is None or not self._report_data:
            return
        if any(tabs.tabText(i) == "Weak Link"
               for i in range(tabs.count())):
            self._rebuild_weak_page()

    def set_selected_block_names_fn(self, fn):
        """Register the selection-name provider used by the Weak tab filter."""
        self._selected_block_names_fn = fn

    def refresh_weak_selection(self):
        """Re-apply the Weak tab's selection filter after a canvas click."""
        if (getattr(self, "_weak_sel_only", False)
                and getattr(self, "_report_data", None)):
            self._rebuild_weak_page()

    def _rebuild_weak_page(self):
        """Swap the Weak sub-tab for a fresh one (selection filter /
        analysis-type changes). Re-entrancy guarded so a signal echo
        during the rebuild cannot schedule another rebuild."""
        if getattr(self, "_weak_rebuilding", False):
            return
        self._weak_rebuilding = True
        try:
            self._rebuild_weak_page_inner()
        finally:
            self._weak_rebuilding = False

    def _rebuild_weak_page_inner(self):
        was_current = self._details_tabs.currentIndex()
        weak_index = -1
        for i in range(self._details_tabs.count()):
            if self._details_tabs.tabText(i) == "Weak Link":
                weak_index = i
                w = self._details_tabs.widget(i)
                self._details_tabs.removeTab(i)
                req(w).deleteLater()
                break
        page = self._build_weak_page()
        # Advanced is the final tab. Insert Weak Link before it rather
        # than at the end, or a rebuild silently reorders the sub-tabs.
        advanced = next((i for i in range(self._details_tabs.count())
                         if self._details_tabs.tabText(i) == "Advanced"), -1)
        if advanced >= 0:
            index = self._details_tabs.insertTab(advanced, page, "Weak Link")
        else:
            index = self._details_tabs.addTab(page, "Weak Link")
        # Removing the open tab made Qt select its neighbour (usually
        # Cost). Rebuilding because the user ticked "Selected elements
        # only" must leave them on Weak Link, not jump tabs.
        if weak_index >= 0 and was_current == weak_index:
            self._details_tabs.setCurrentIndex(index)

    def _add_table_header(self, v: QVBoxLayout, title: str,
                          table: QTableWidget,
                          tip: str = "", *, show_title: bool = True) -> QLabel:
        """A table's Copy and CSV icon actions, optionally with a title.

        `show_title=False` is for cards whose tab or existing note already
        names the table (Summary, Logistics, Weak Link rows): the icon
        strip remains but there is no duplicate heading (#205 review).

        The actions read `table` at click time through `table_to_grid`, so
        they follow the rows the user can actually see (filter and sort)
        and keep full precision. No global Copy/Export row exists any
        more; Export all tables lives under File > Export.
        """
        from PySide6.QtWidgets import QPushButton
        from raptor.gui.resources import icons
        from raptor.gui.ui.table_export import table_to_grid

        strip = _TableActionStrip(table)
        hdr_row = QHBoxLayout(strip)
        hdr_row.setContentsMargins(0, 0, 0, 0)
        hdr_row.setSpacing(4)
        label = QLabel(title)
        # Bigger than body notes: the title is the table's visual anchor.
        label.setObjectName("TableTitle")
        if tip:
            label.setToolTip(tip)
        # The title must not be squeezed to zero when a table is narrow:
        # the two fixed action buttons would then sit over it (#225).
        label.setSizePolicy(QSizePolicy.Policy.Preferred,
                            QSizePolicy.Policy.Preferred)
        label.ensurePolished()
        # `sizeHint()` can still be the pre-style placeholder here, so
        # measure the text with the polished font and add a little slack.
        title_width = (label.fontMetrics().horizontalAdvance(title) + 32
                       if show_title else 0)
        label.setMinimumWidth(title_width)
        if show_title:
            # Take the available width, not a trailing stretch: a spacer
            # after the label made the title itself collapse to zero.
            hdr_row.addWidget(label, 1)
        else:
            hdr_row.addStretch(1)
        colour = active_colour()["text"]
        # Action names must not go stale when a hierarchy filter changes a
        # "Blocks (N)" heading; the stable name is what a user hears and
        # what the CSV filename uses.
        table_name = re.sub(r"\s*\(\d+\)", "", title).strip() or "table"
        tip_text = f"\n\n{tip}" if tip else ""

        def icon_button(icon, object_name: str, accessible: str,
                        tooltip: str):
            button = QPushButton()
            button.setObjectName(object_name)
            button.setIcon(icon)
            button.setIconSize(QSize(16, 16))
            button.setFixedSize(22, 22)
            button.setFlat(True)
            button.setText("")
            button.setAccessibleName(accessible)
            button.setToolTip(tooltip + tip_text)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            hdr_row.addWidget(button)
            return button

        cp = icon_button(
            icons.copy_icon(colour), "TableCopyButton",
            f"Copy {table_name} table",
            f"Copy the '{table_name}' table to the clipboard as TSV "
            "(pastes into Excel/Sheets; filtered and sorted rows, full "
            "precision)")
        sv = icon_button(
            icons.csv_icon(colour), "TableCsvButton",
            f"Save {table_name} table as CSV",
            f"Save the '{table_name}' table as a CSV file (filtered and "
            "sorted rows, full precision)")
        # Reserve the title plus both fixed 22px buttons (and the layout
        # spacing). A narrower table would make the buttons overlap the
        # heading; widening it is preferable to hiding the title.
        needed = title_width + 2 * 22 + 3 * hdr_row.spacing()
        table.setMinimumWidth(max(table.minimumWidth(), needed))
        strip.setMinimumWidth(needed)
        v.addWidget(strip)

        def _current_grid():
            # Read the table at click time, not a precomputed list: a
            # hierarchy filter, row filter or sort may have changed since
            # it was built; `table_to_grid` also restores full names that
            # the grid elided.
            return table_to_grid(table, raw=True)

        def _scope_word() -> str:
            iso = (getattr(self, "_manifest", None) or {}).get(
                "isolation") or {}
            name = str(iso.get("hierarchy") or "")
            return f" (isolated hierarchy: {name})" if name else ""

        def _copy_this():
            from PySide6.QtCore import QMimeData
            from raptor.gui.ui.table_export import grid_to_html, grid_to_tsv
            headers, rows = _current_grid()
            mime = QMimeData()
            mime.setText(grid_to_tsv(headers, rows))
            mime.setHtml(grid_to_html(headers, rows))
            QApplication.clipboard().setMimeData(mime)
            self._status_hint_f and self._status_hint_f(
                f"Copied '{table_name}'{_scope_word()}")

        def _save_this():
            from raptor.gui.ui.table_export import grid_to_csv
            safe = "".join(c if c.isalnum() or c in " _-"
                           else "_" for c in table_name).strip()
            safe = safe.replace(" ", "_") or "table"
            iso = (getattr(self, "_manifest", None) or {}).get(
                "isolation") or {}
            suffix = ""
            if iso.get("hierarchy"):
                safe_h = "".join(c if c.isalnum() or c in " _-"
                                 else "_" for c in str(iso["hierarchy"]))
                suffix = "_isolated_" + (safe_h.replace(" ", "_") or "hierarchy")
            path, _f = fdg.getSaveFileName(
                self, f"Export {table_name}",
                safe + suffix + ".csv", "CSV (*.csv)")
            if not path:
                return
            headers, rows = _current_grid()
            with open(path, "w", newline="", encoding="utf-8") as f:
                f.write(grid_to_csv(headers, rows))
            self._status_hint_f and self._status_hint_f(f"Saved: {path}")

        cp.clicked.connect(_copy_this)
        sv.clicked.connect(_save_this)
        return label

    def _build_cost_page(self) -> QWidget:
        """Cost analysis view: per-run `CostRunReport`s
        averaged across runs -- blocks' life-cycle quintuple, spare/
        resource pools, events, hierarchies, and the system totals."""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(SPACING["md"], SPACING["md"],
                             SPACING["md"], SPACING["md"])
        v.setSpacing(SPACING["sm"])

        costs = [d.cost for d in self._report_data if d.cost is not None]
        if not costs:
            msg = QLabel(
                "No cost analysis in this model.\n\n"
                "Cost is simulated when Cost is enabled in the Run panel's "
                "Analysis section (and blocks define costs).")
            msg.setObjectName("SectionNote")
            msg.setWordWrap(True)
            v.addWidget(msg)
            v.addStretch(1)
            self._cost_tables: list[QTableWidget] = []
            return page

        def money(x: float) -> str:
            return f"{x:,.2f}"

        def mean_over(getter):
            vals = [getter(cst) for cst in costs]
            return sum(vals) / len(vals)

        down = mean_over(lambda c: c.system_down_cost)
        lost = mean_over(lambda c: c.system_lost_flow_cost)
        total = mean_over(lambda c: c.grand_total)
        head = QLabel(
            f"System-wide totals — Grand total: {money(total)}   \u00b7   "
            f"System down cost: {money(down)}   \u00b7   "
            f"Lost flow cost: {money(lost)}"
            f"   \u00b7   averaged over {len(costs)} run(s)")
        head.setObjectName("SummarySentence")
        head.setWordWrap(True)
        v.addWidget(head)


        self._cost_tables = []
        self._cost_blocks_label = None
        self._cost_blocks_title = ""

        def _sum_cost_values(columns: list[str], vals: list,
                             *, all_values: bool = False
                             ) -> tuple[float, bool]:
            """Sum a report row's additive cost cells.

            The parsed report keeps values as strings (and sometimes
            ``N/A``); the Total column must use the same full-precision
            number the other cells hand to Copy/CSV, not the rounded
            display text. The Operating table's normal rows carry an
            extra `InHier`/Level cell before the costs; its Totals row
            (when the parser keeps it) does not.
            """
            total = 0.0
            found = False
            pairs = (zip(columns, vals) if all_values else
                     zip(columns[2:], vals)
                     if len(vals) == len(columns) - 2
                     else zip(columns[1:], vals))
            for column, value in pairs:
                if column in ("InHier",):
                    continue
                if isinstance(value, tuple):
                    value = value[1] if len(value) > 1 else value[0]
                text = str(value).strip().replace(",", "")
                if not text or text.casefold() in {"n/a", "na", "—", "–", "-"}:
                    continue
                try:
                    number = float(text)
                except ValueError:
                    continue
                if not math.isfinite(number):
                    continue
                total += number
                found = True
            return total, found

        def _with_cost_total(title: str, columns: list[str],
                             rows: list[list]):
            """Append a Total column to additive report-section tables.

            Blocks/Pools/Hierarchies already carry a Total from their own
            builders; only the Operating and Sparing report-section
            tables need one. Event rows are assembled separately and use
            the engine's own event total. Cost_Summary is already one
            value per row.
            """
            name = title.rsplit(" - ", 1)[-1]
            attr = {"Operating": "operating", "Sparing": "sparing"}.get(name)
            if attr is None or "Total" in columns:
                return columns, rows
            from raptor.core.engine.report_rows import element_of
            out = []
            for label, vals in rows:
                total: float | None = None
                element = element_of(label)
                if element is not None and element[0] == "block":
                    per_run = [cst.blocks[element[1]] for cst in costs
                               if element[1] in cst.blocks]
                    if per_run:
                        total = sum(getattr(row, attr) for row in per_run) \
                            / len(per_run)
                if total is None:
                    label_text = str(label)
                    is_totals = (label_text == "Totals"
                                 or label_text.endswith("_Totals"))
                    summed, found = _sum_cost_values(
                        columns, vals, all_values=is_totals)
                    total = summed if found else None
                if total is not None and not math.isfinite(total):
                    total = None
                out.append((label, [*vals,
                                    str(total) if total is not None else "N/A"]))
            return [*columns, "Total"], out

        def add_table(title: str, headers: list[str], rows: list[list],
                      tip: str = "", sort_key: str = "",
                      default_sort: tuple[int, Qt.SortOrder] | None = None):
            # Cells may be (display, raw) tuples: the grid shows the
            # rounded display text, copy/CSV take the raw value at full
            # precision (same rule as the main Copy Table).
            ui_rows = []
            for row in rows:
                out_row = []
                for cell in row:
                    display, raw, censored = _cell_parts(cell)
                    out_row.append((display, raw, censored)
                                   if raw is not None else display)
                ui_rows.append(out_row)
            table = self._make_table(headers, ui_rows)
            if tip:
                table.setToolTip(tip)
            label = self._add_table_header(v, title, table)
            if title.startswith("Blocks ("):
                self._cost_blocks_label = label
                self._cost_blocks_title = title
            v.addWidget(table)
            self._cost_tables.append(table)
            self._apply_sort(table, sort_key or f"COST::{title}",
                             default_sort)

        # Blocks: life-cycle costs (Raptor 7's Life_cycle_cost table).
        block_ids = sorted({bid for cst in costs for bid in cst.blocks})
        block_rows = []
        for bid in block_ids:
            per_run = [cst.blocks[bid] for cst in costs if bid in cst.blocks]

            def avg(attr, runs=per_run):
                return sum(getattr(r, attr) for r in runs) / len(runs)
            block_rows.append([
                per_run[0].name,
                (money(avg("purchase")), avg("purchase")),
                (money(avg("operating")), avg("operating")),
                (money(avg("sparing")), avg("sparing")),
                (money(avg("disposal")), avg("disposal")),
                (money(avg("total")), avg("total"))])
        add_table(f"Blocks ({len(block_rows)}) -- life-cycle costs",
                  self._COST_HEADERS, block_rows,
                  "Purchase + Operating + Sparing + Disposal = Total",
                  sort_key="COST::BLOCKS",
                  default_sort=(5, Qt.SortOrder.DescendingOrder))

        # Pools: spare pools keep their column names; RESOURCE pools
        # relabel NewSpares->PerTimeCost / EmerShip->PerUseCost
        # (`PoolCostRow`'s own docstring).
        spare_names = sorted({n for cst in costs for n in cst.spare_pools})
        res_names = sorted({n for cst in costs for n in cst.resources})
        pool_rows = []
        for name in sorted(set(spare_names) | set(res_names)):
            rows_ = ([cst.spare_pools[name] for cst in costs
                      if name in cst.spare_pools]
                     + [cst.resources[name] for cst in costs
                        if name in cst.resources])
            is_res = any(r.is_resource for r in rows_)

            def avg(attr, runs=rows_):
                return sum(getattr(r, attr) for r in runs) / len(runs)
            kind = "Resource" if is_res else "Spare"
            pool_rows.append([
                f"{name} [{kind}]",
                (money(avg("initial")), avg("initial")),
                (money(avg("new_spares")), avg("new_spares")),
                (money(avg("emer_ship")), avg("emer_ship")),
                (money(avg("total")), avg("total"))])
        if pool_rows:
            time_unit = f" ({self._units})" if self._units else ""
            add_table(
                "Pools",
                ["Name", "Initial", f"New Spares / Per-Time{time_unit}",
                 f"Emer. Shipping / Per-Use{time_unit}", "Total"],
                pool_rows, sort_key="COST::POOLS",
                default_sort=(4, Qt.SortOrder.DescendingOrder))

        # Events (initial/success/failure charges).
        event_ids = sorted({eid for cst in costs for eid in cst.events})
        if event_ids:
            event_rows = []
            for eid in event_ids:
                per_run = [cst.events[eid] for cst in costs
                           if eid in cst.events]

                def avg(attr, runs=per_run):
                    return sum(getattr(r, attr) for r in runs) / len(runs)
                event = per_run[0]
                event_rows.append([
                    event.name,
                    (money(avg("initial")), avg("initial")),
                    (money(avg("success")), avg("success")),
                    (money(avg("failure")), avg("failure")),
                    (money(avg("total")), avg("total"))])
            add_table("Events", ["Name", "Initial", "Success", "Failure",
                                 "Total"], event_rows, sort_key="COST::EVENTS",
                      default_sort=(4, Qt.SortOrder.DescendingOrder))

        # Hierarchies: child-cost rollups.
        hier_ids = sorted({hid for cst in costs for hid in cst.hiers})
        if hier_ids:
            hier_rows = []
            for hid in hier_ids:
                per_run = [cst.hiers[hid] for cst in costs
                           if hid in cst.hiers]

                def avg(attr, runs=per_run):
                    return sum(getattr(r, attr) for r in runs) / len(runs)
                h = per_run[0]
                hier_rows.append([
                    h.name,
                    (money(avg("purchase")), avg("purchase")),
                    (money(avg("operating")), avg("operating")),
                    (money(avg("sparing")), avg("sparing")),
                    (money(avg("disposal")), avg("disposal")),
                    (money(avg("events")), avg("events")),
                    (money(avg("total")), avg("total"))])
            add_table(
                "Hierarchies",
                ["Name", "Purchase", "Operating", "Sparing", "Disposal",
                 "Events", "Total"],
                hier_rows, sort_key="COST::HIERS",
                default_sort=(6, Qt.SortOrder.DescendingOrder))

        # The report's per-state operating cost and per-block sparing cost
        # (not in the snapshots' life-cycle view above).
        from raptor.api.presentation import table_title
        for title, columns, rows in getattr(self, "_cost_extra", []):
            columns, rows = _with_cost_total(title, columns, rows)
            table = self._section_widget(title, columns, rows)
            self._add_table_header(v, table_title(title), table)
            v.addWidget(table)
            self._cost_tables.append(table)

        v.addStretch(1)
        return page

    @staticmethod
    def _label_header(title: str) -> str:
        return {"SUMMARY": "Measure", "LOGISTICS": "Measure"}.get(title, "Name")

    def set_level_filter(self, allowed_names: set[str] | None):
        """`None` = show everything (home level); a name set hides any
        Sparing/Capacity row whose first-column element is outside it."""
        self._allowed_names = allowed_names
        self._apply_level_filter()

    def _hierarchy_row_visible(self, table: QTableWidget, row: int,
                               allowed: set[str] | None = None) -> bool:
        """Whether `row` belongs to the current hierarchy scope.

        Shared by `_apply_level_filter` and the table's FilterBar: sorting
        moves the items but leaves row-hidden flags on the old indexes, so
        both paths must recompute this rather than trusting the flags.
        """
        if allowed is None:
            allowed = getattr(self, "_allowed_names", None)
        if allowed is None:
            return True
        item = table.item(row, 0)
        shown = item.text().split("(")[0].strip() if item is not None else ""
        # Prefer the item's full name (kept as UserRole data when the
        # shown label was elided); fall back to a prefix match so a name
        # truncated somewhere in the report chain still matches its one
        # allowed owner.
        name = (item.data(Qt.ItemDataRole.UserRole)
                if item is not None else None)
        if not name:
            name = shown
        if not name:
            return True
        if name in allowed:
            return True
        matches = [a for a in allowed if a.startswith(shown)]
        return len(matches) == 1

    def _apply_level_filter(self):
        from shiboken6 import isValid
        if not hasattr(self, "_detail_tables") \
                or not hasattr(self, "_details_tabs"):
            return   # a table built directly by a test, outside a full dock
        allowed = getattr(self, "_allowed_names", None)

        def alive(w):
            return w is not None and isValid(w)

        # Summary/Logistics are system-wide measures (Availability, MTBDE,
        # ...). Raptor 7 gives those two tabs no hierarchy filter at
        # all (`FillResultsTable` only wires HierFilter into Sparing/Cost/
        # Analysis/Capacity); filtering them by element name would hide
        # every measure row (a block is never named "MTBDE").
        _MEASURE_TABLES = ("SUMMARY", "LOGISTICS")
        tables: list[QTableWidget] = [
            t for k, t in self._detail_tables.items()
            if alive(t) and k not in _MEASURE_TABLES]
        cap = getattr(self, "_cap_table", None)
        if cap is not None and alive(cap):
            tables.append(cap)
        tables.extend(t for t in getattr(self, "_cost_tables", [])
                      if alive(t))
        # Weak Link tables live in their own page (not the section
        # registry) — include them so hierarchy filtering covers them
        weak_page = None
        for i in range(self._details_tabs.count()):
            if self._details_tabs.tabText(i) == "Weak Link":
                weak_page = self._details_tabs.widget(i)
                break
        if weak_page is not None:
            from PySide6.QtWidgets import QTableWidget as _QT
            tables.extend(t for t in weak_page.findChildren(_QT)
                          if alive(t))
        # Stale entries whose pages were deleteLater'd with an old
        # populate must not crash the filter.
        self._detail_tables = {
            k: t for k, t in self._detail_tables.items() if alive(t)}
        self._cost_tables = [t for t in
                             getattr(self, "_cost_tables", [])
                             if alive(t)]
        for table in tables:
            for r in range(table.rowCount()):
                table.setRowHidden(
                    r, not self._hierarchy_row_visible(table, r, allowed))
        label = getattr(self, "_cost_blocks_label", None)
        title = getattr(self, "_cost_blocks_title", "")
        cost_tables = getattr(self, "_cost_tables", [])
        if label is not None and alive(label) and cost_tables:
            table = cost_tables[0]
            count = sum(1 for r in range(table.rowCount())
                        if not table.isRowHidden(r))
            suffix = title.split(")", 1)[1] if ")" in title else ""
            label.setText(f"Blocks ({count}){suffix}")

    def _build_importance_page(self) -> QWidget:
        """Importance measures for the blocks, ranked by Birnbaum.

        The numbers are the `raptor.api.importance` result for this run; the
        *definitions* live on the formulas page (Help > Formulas) and are not
        restated here -- that duplication is what this tab replaces.
        """
        from raptor.api.importance import METRICS

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(SPACING["md"], SPACING["md"],
                             SPACING["md"], SPACING["md"])
        v.setSpacing(SPACING["sm"])

        data = self._importance or {}
        metrics = {label: entry for label, entry in
                   (data.get("metrics") or {}).items()}
        if not metrics:
            empty = QLabel(
                "No importance measures for this run.\n\n"
                "Enable Importance measures in the Run panel's Analysis "
                "section and run again. It costs one extra batch per block "
                "(two for Both), so it is off unless asked for.")
            empty.setObjectName("SectionNote")
            empty.setWordWrap(True)
            v.addWidget(empty)
            v.addStretch(1)
            return page

        chosen = self._importance_metric
        if chosen not in metrics:
            chosen = next(iter(metrics))
        entry = metrics[chosen]
        rows = entry.get("rows") or []

        def _num(value, places):
            return "\u2014" if value is None else f"{value:,.{places}f}"

        # One ranking rule for the tab, the report and the overlay. Birnbaum
        # alone is not it: in a series model every block's variant moves the
        # system from ~0 to ~1, so it saturates for all of them and ranks
        # nothing. See `raptor.api.importance.RANKING_ORDER`.
        from raptor.api.importance import RANKING_LABELS, ranked as _ranked
        from raptor.api.importance import ranking_measure

        measure_label = RANKING_LABELS[ranking_measure(rows)]
        top = next(iter(_ranked(rows)), None)
        head = QLabel(
            f"Ranked by {measure_label} importance, measured on "
            f"<b>{chosen}</b>. Baseline {_num(entry.get('baseline'), 4)}. "
            f"{data.get('blocks', len(rows))} blocks, "
            f"{data.get('batches', 0)} batches of {data.get('runs', 0)} runs."
            + (f" Most influential: <b>{top['block']}</b>." if top else ""))
        head.setObjectName("SummarySentence")
        head.setWordWrap(True)
        v.addWidget(head)

        picker = QHBoxLayout()
        picker.addWidget(QLabel("Measured on:"))
        combo = QComboBox()
        for _attr, label in METRICS:
            if label in metrics:
                combo.addItem(label, label)
        combo.setCurrentIndex(combo.findData(chosen))
        combo.currentIndexChanged.connect(
            lambda _i, c=combo: self._set_importance_metric(c.currentData()))
        picker.addWidget(combo)
        picker.addStretch(1)
        v.addLayout(picker)

        # Build rows in the report's ranking order, but let the table's
        # own default (Birnbaum descending, #208) reorder them. Tooltips
        # are attached before sorting, so they travel with their row.
        ordered = _ranked(rows)

        def _metric_cell(value, places):
            shown = _num(value, places)
            return shown if value is None else (shown, value)

        table_rows = [[
            r["block"],
            _metric_cell(r.get("birnbaum"), 4),
            _metric_cell(r.get("raw"), 2),
            _metric_cell(r.get("rrw"), 2),
            _metric_cell(r.get("vesely_fussell"), 3),
        ] for r in ordered]
        table = self._make_table(
            ["Block", "Birnbaum", "RAW", "RRW", "Vesely\u2013Fussell"],
            table_rows, first_col_label="Block")
        # The columns are bare acronyms. A header tooltip is the only place
        # to say what each one answers without adding a paragraph the
        # reader has to skip; the equations stay on Help > Formulas, which
        # is the single source (`docs/formulas.adoc`) -- these are one-line
        # readings, not a second definition.
        for col, tip in enumerate(IMPORTANCE_HEADER_TIPS):
            h_item = table.horizontalHeaderItem(col)
            if h_item is not None:
                h_item.setToolTip(tip)
        for i, r in enumerate(ordered):
            item = table.item(i, 0)
            if item is None:
                continue
            item.setToolTip(
                f"{r['block']}\n\n"
                f"System {chosen.lower()} with this block unable to fail: "
                f"{_num(r.get('perfect'), 4)}\n"
                f"with this block dead: {_num(r.get('failed'), 4)}\n"
                f"Combined standard error of the difference: "
                f"{_num(r.get('sem'), 4)}")
        # The table's own default: Birnbaum descending. The prose above
        # ranks by the tab's chosen measure; clicking Birnbaum shows the
        # sensitivity ordering the column actually contains (#208).
        self._apply_sort(table, "IMPORTANCE",
                         (1, Qt.SortOrder.DescendingOrder))
        self._add_table_header(v, "Blocks", table)
        v.addWidget(table)

        note = QLabel(
            "Estimated by re-simulation: the model is run once with each "
            "block unable to fail and once with it dead. These are Monte "
            "Carlo estimates, not analytical derivatives -- the standard "
            "error says how much of the ranking is sampling noise. "
            "Definitions and equations: Help \u25b8 Formulas.")
        note.setObjectName("SectionNote")
        note.setWordWrap(True)
        v.addWidget(note)
        for text in data.get("notes", []):
            extra = QLabel(text)
            extra.setObjectName("SectionNote")
            extra.setWordWrap(True)
            v.addWidget(extra)
        v.addStretch(1)
        return page

    def _set_importance_metric(self, label: str) -> None:
        """Swap the measured metric without re-simulating."""
        if label == self._importance_metric:
            return
        self._importance_metric = label
        self._populate_details(getattr(self, "_final_lines", []) or [])
        self.importance_metric_changed.emit(label)

    def section_table(self, title: str) -> QTableWidget | None:
        """The table for a Final Results section, by section name.

        Accepts the plain section name even when the report held several
        tables under that one marker: `parse_final_sections` splits those
        and names each `"<SECTION> - <sub-table>"` (COST is seven tables
        plus a summary tail; one shared header row is what put values in
        unlabelled columns F..J). A bare `"COST"` therefore returns the
        first of them."""
        found = self._detail_tables.get(title)
        if found is not None:
            return found
        prefix = f"{title} - "
        return next((t for name, t in self._detail_tables.items()
                     if name.startswith(prefix)), None)
