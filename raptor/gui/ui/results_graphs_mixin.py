"""Results graphs tab: plots, y-axis control and graph data export.

Expects on `self`: the results tabs and the report data held by `ResultsDock`."""
from __future__ import annotations


from raptor.gui.ui.qtcompat import req


from raptor.gui.ui import msgbox

from typing import TYPE_CHECKING, Any, cast

from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox,
)

from raptor.core.engine import weak_metric
from raptor.gui.ui import filedialogs as fdg
from raptor.gui.resources.design_tokens import SPACING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _Base
else:
    _Base = object


class ResultsGraphsMixin(_Base):
    """Results graphs tab: plots, y-axis control and graph data export."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        _MAX_PARSE_ROWS: Any
        _ao_lines: list[str]
        _ao_plot: Any
        _graph_mode: Any
        _graph_note: Any
        _graph_run: Any
        _graphs_page: Any
        _report_data: Any
        _source_file_name: str
        _status_hint_f: Any
        _weak_anal_type: str
        _y_axis_btn: Any
        y_axis_min_changed: Any

    def _build_graphs_tab(self):
        """Ao availability graphs: Per-Trial, Interval, and Multi-run plots fed by the
        engine's `_ao_sink` rows."""
        from raptor.gui.ui.plots import AoPlotWidget
        from PySide6.QtWidgets import QPushButton
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(SPACING["md"], SPACING["md"],
                             SPACING["md"], SPACING["md"])
        v.setSpacing(SPACING["sm"])
        top = QHBoxLayout()
        top.setSpacing(SPACING["sm"])
        self._graph_mode = QComboBox()
        self._graph_mode.addItems(["Per Trial Ao", "Interval Ao",
                                   "Multi-run Ao", "Capacity (Flow)",
                                   "Multi-run Capacity (Flow)",
                                   "Availability Convergence"])
        self._graph_mode.currentIndexChanged.connect(self._on_graph_mode)
        top.addWidget(QLabel("Plot:"))
        top.addWidget(self._graph_mode)
        self._graph_run = QComboBox()
        self._graph_run.currentIndexChanged.connect(self._on_graph_run)
        top.addWidget(QLabel("Run:"))
        top.addWidget(self._graph_run)
        top.addStretch(1)
        self._y_axis_btn = QPushButton("Y-axis min: Auto")
        self._y_axis_btn.clicked.connect(self._ask_y_axis_min)
        top.addWidget(self._y_axis_btn)
        self._update_y_axis_control()
        save_img_btn = QPushButton("Save Image…")
        save_img_btn.setToolTip(
            "Save the currently displayed plot as a PNG image. "
            "The underlying numbers are available separately via "
            "Export CSV on the Final Results tab.")
        save_img_btn.clicked.connect(self._save_graph_image)
        top.addWidget(save_img_btn)
        # Export the raw plot data: with the Files & Reports plots
        # streamed to disk, this is the "export on demand" path. The
        # default name mirrors Raptor 7's rpt_* file convention.
        export_data_btn = QPushButton("Export Data…")
        export_data_btn.setToolTip(
            "Save the current plot's underlying data rows to a text "
            "file (Availability plot -> rpt_availability_plot_<model>"
            ".txt, Capacity plot -> rpt_capacity_plot_<model>.txt)")
        export_data_btn.clicked.connect(self._export_graph_data)
        top.addWidget(export_data_btn)
        self._graph_note = QLabel("")
        self._graph_note.setObjectName("SectionNote")
        self._graph_note.hide()
        top.addWidget(self._graph_note)
        v.addLayout(top)
        self._ao_plot = AoPlotWidget()
        v.addWidget(self._ao_plot, 1)
        self._graphs_page = page

    def set_y_axis_min(self, value: float) -> None:
        """The model's saved `graphYmin`, as the graphs' lower bound (no signal, no dirty flag)."""
        self._ao_plot.set_y_min(value)
        self._update_y_axis_control()

    def _update_y_axis_control(self) -> None:
        """Keep the Y-min button honest about the current mode and value.

        The control is always visible. In capacity/convergence modes it
        names the reason it does not apply; on availability plots it says
        Auto (0, the model default) or the saved manual floor, so a
        disabled-looking result is never mistaken for a broken button
        (#199).
        """
        if not hasattr(self, "_y_axis_btn"):
            return
        from raptor.gui.ui.plots import AoPlotWidget
        index = (self._graph_mode.currentIndex()
                 if hasattr(self, "_graph_mode") else 0)
        capacity = index in (AoPlotWidget.CAPACITY,
                             AoPlotWidget.MULTI_CAPACITY)
        convergence = index == AoPlotWidget.CONVERGENCE
        y_min = self._ao_plot.y_min if hasattr(self, "_ao_plot") else 0.0
        applies = not (capacity or convergence)
        self._y_axis_btn.setEnabled(applies)
        if capacity:
            self._y_axis_btn.setText("Y-axis min: n/a")
            self._y_axis_btn.setToolTip(
                "Not used by capacity plots: their Y axis is derived from "
                "the flow/capacity data.")
        elif convergence:
            self._y_axis_btn.setText("Y-axis min: n/a")
            self._y_axis_btn.setToolTip(
                "Not used by the convergence plot: its Y scale is fitted "
                "to the cumulative means.")
        else:
            self._y_axis_btn.setText(
                "Y-axis min: Auto" if y_min <= 0.0
                else f"Y-axis min: {y_min:.4f}")
            self._y_axis_btn.setToolTip(
                "Y-axis minimum for availability plots (0 to 0.999999). "
                "0 = Auto: Per-trial/Interval show 0–1, Multi-run Ao fits "
                "the data envelope. A value above 0 is a manual floor and "
                "is used by every availability mode, including Multi-run "
                "Ao. Saved with the model as graphYmin.")

    def _ask_y_axis_min(self) -> None:
        """Raptor 7's "Set Y-Axis" box, constrained to 0 <= min < 1."""
        from PySide6.QtWidgets import QInputDialog
        value, ok = QInputDialog.getDouble(
            self, "Set Y-Axis", "Y-axis minimum (0 = Auto):",
            self._ao_plot.y_min, 0.0, 0.999999, 6)
        if not ok:
            return
        # The dialog enforces the range itself; this guard only protects
        # callers/tests that bypass it. No accepted-then-warned value.
        if not (0.0 <= value < 1.0):
            return
        if value != self._ao_plot.y_min:
            self._ao_plot.set_y_min(value)
            self._update_y_axis_control()
            self.y_axis_min_changed.emit(value)

    @staticmethod
    def _stream_lines(src) -> list[str]:
        """Rows from a Files & Reports sink: a disk-backed ResultStream
        reads lazily from its temp file (never materialised in RAM by
        the engine); a plain list is returned as-is (tests)."""
        if src is None:
            return []
        if hasattr(src, "read_all_lines"):
            return src.read_all_lines()
        return src

    def _export_graph_data(self) -> None:
        mode = self._graph_mode.currentIndex()
        if mode == 3:   # Capacity (Flow)
            src, stem = getattr(self, "_cap_lines", None), "rpt_capacity_plot"
        else:
            src, stem = getattr(self, "_ao_lines", None), "rpt_availability_plot"
        lines = self._stream_lines(src)
        if not lines:
            self._status_hint_f and self._status_hint_f(
                "No plot data collected — tick the plot under Run panel ▸ "
                "Recording and run again")
            return
        base = (self._source_file_name or "model").rsplit(".", 1)[0]
        safe = "".join(c if c.isalnum() or c in "_-" else "_"
                       for c in base)[:60] or "model"
        path, _f = fdg.getSaveFileName(
            self, "Export Plot Data",
            f"{stem}_{safe}.txt", "Text (*.txt);;All (*)")
        if not path:
            return
        try:
            if hasattr(src, "export_to"):
                req(src).export_to(path)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
            self._status_hint_f and self._status_hint_f(f"Saved: {path}")
        except OSError as e:
            msgbox.critical(self, "Export Failed",
                                 f"Could not write {path}:\n{e}")

    def _save_graph_image(self) -> None:
        if self._ao_plot.width() <= 0 or self._ao_plot.height() <= 0:
            return
        pm = self._ao_plot.grab()
        if pm.isNull():
            return
        mode = self._graph_mode.currentText().replace(" ", "_").lower()
        path, _f = fdg.getSaveFileName(
            self, "Save Graph Image", f"{mode}.png", "PNG Images (*.png)")
        if not path:
            return
        if not pm.save(path, "PNG"):
            msgbox.critical(self, "Export Failed",
                                 f"Could not write {path}")
            return

    def set_capacity_available(self, available: bool) -> None:
        """Grey the Capacity (Flow) plot mode out when the model has no
        Capacity Analysis. Re-enabled on model load / analysis toggle,
        and force-enabled by set_results when real capacity data
        arrives."""
        indexes = [self._graph_mode.findText("Capacity (Flow)"),
                   self._graph_mode.findText("Multi-run Capacity (Flow)")]
        for idx in (i for i in indexes if i >= 0):
            cast(QStandardItemModel,
                 self._graph_mode.model()).item(idx).setEnabled(
                bool(available))
            if not available and self._graph_mode.currentIndex() == idx:
                self._graph_mode.setCurrentIndex(0)

    def _on_graph_mode(self, index: int):
        from raptor.gui.ui.plots import AoPlotWidget
        self._ao_plot.set_mode(index)
        # Multi-run Ao and Convergence both aggregate across ALL runs;
        # the rest follow the run selector.
        self._graph_run.setEnabled(
            index not in (AoPlotWidget.MULTI_RUN,
                          AoPlotWidget.MULTI_CAPACITY,
                          AoPlotWidget.CONVERGENCE))
        # The Y-axis-minimum control applies to availability plots only;
        # capacity paints a data-derived axis and convergence fits its own
        # scale. The button always says which state it is in (#199).
        self._update_y_axis_control()

    def _on_graph_run(self, index: int):
        self._ao_plot.set_run(index)

    def _update_graphs(self):
        """Re-parse `_ao_lines`/`_cap_lines` and refresh plot + selector.

        The sinks may be plain lists (tests) or disk-backed ResultStreams
        (Files & Reports opt-in): rows are read lazily from the file and
        the interactive view is capped at `_MAX_PARSE_ROWS` with a
        visible note, so the engine never holds them in RAM."""
        from raptor.gui.ui.plots import parse_ao_runs, parse_cap_runs
        ao_all = self._stream_lines(self._ao_lines)
        capped = len(ao_all) > self._MAX_PARSE_ROWS
        runs = parse_ao_runs(ao_all[:self._MAX_PARSE_ROWS]
                             if capped else ao_all)
        self._ao_plot.set_data(runs)
        cap_all = self._stream_lines(getattr(self, "_cap_lines", None) or [])
        cap_runs = parse_cap_runs(
            cap_all[:self._MAX_PARSE_ROWS] if capped else cap_all)
        self._ao_plot.set_cap_data(cap_runs)
        self._update_convergence_data()
        n = max(len(runs), len(cap_runs))
        self._sync_graph_modes(runs, cap_runs, n)
        self._graph_run.blockSignals(True)
        self._graph_run.clear()
        self._graph_run.addItems([f"Run {i + 1}" for i in range(n)])
        if n:
            self._graph_run.setCurrentIndex(0)
        self._graph_run.blockSignals(False)
        self._graph_run.setEnabled(
            n > 0 and self._graph_mode.currentIndex() not in (2, 4, 5))
        if capped:
            self._graph_note.setText(
                f"Showing the first {self._MAX_PARSE_ROWS:,} rows — use "
                f"Export Data… for the full set ({len(ao_all):,} rows)")
            self._graph_note.show()
        else:
            self._graph_note.hide()

    def _sync_graph_modes(self, runs, cap_runs, n_runs) -> None:
        """Enable only plot modes that can actually draw something.

        A disabled mode carries the exact missing prerequisite in its
        tooltip. Capacity used to be enabled whenever the analysis flag was
        on, even when the user had not ticked Capacity plot recording, so
        the tab offered a guaranteed-empty chart.
        """
        has_ao = bool(runs)
        has_interval = any(
            any(v is not None for v in run.get("iv", []))
            for run in runs)
        has_multi = len(runs) >= 2
        has_cap = bool(cap_runs)
        has_multi_cap = len(cap_runs) >= 2
        has_convergence = len(self._report_data or []) >= 2
        reasons = {
            0: ("No availability data — tick Availability plot under "
                "Run panel ▸ Recording and run again." if not has_ao else ""),
            1: ("No interval samples — tick Availability plot under "
                "Run panel ▸ Recording and run again." if not has_interval else ""),
            2: ("Need at least 2 runs with availability plot data." if not has_multi else ""),
            3: ("No capacity plot data — tick Capacity plot under Run "
                "panel ▸ Recording and run again." if not has_cap else ""),
            4: ("Need at least 2 runs with capacity plot data." if not has_multi_cap else ""),
            5: ("Need at least 2 runs to show convergence." if not has_convergence else ""),
        }
        enabled = [has_ao, has_interval, has_multi, has_cap,
                   has_multi_cap, has_convergence]
        for i, ok in enumerate(enabled):
            item = cast(QStandardItemModel, self._graph_mode.model()).item(i)
            if item is not None:
                item.setEnabled(ok)
                item.setToolTip(reasons.get(i, "") if not ok else "")
        if not enabled[self._graph_mode.currentIndex()]:
            first = next((i for i, ok in enumerate(enabled) if ok), 0)
            self._graph_mode.setCurrentIndex(first)
        self._graph_run.setEnabled(
            n_runs > 0 and self._graph_mode.currentIndex() not in (2, 4, 5))
        # A result set can force a mode change; the Y-min button's state
        # and reason must follow it even when currentIndexChanged did not.
        self._update_y_axis_control()

    def _update_convergence_data(self):
        """Cumulative mean Availability/Dependability vs. run count
        (Graphs tab's "Availability Convergence" plot) -- same metric-
        selection logic as the Overview card's own `use_dependability`
        (the model's configured `nodeAnalType`, falling back to
        Availability when Dependability isn't computed for this
        model), so the two views never disagree about which number
        they're both showing."""
        data = self._report_data or []
        use_dependability = (
            weak_metric.canonical(self._weak_anal_type)
            == weak_metric.DEPENDABILITY
            and any(d.dependability is not None for d in data))
        getter = ((lambda d: d.dependability) if use_dependability
                 else (lambda d: d.availability))
        vals = [getter(d) for d in data if getter(d) is not None]
        points: list[tuple[float, float]] = []
        running_sum = 0.0
        for i, v in enumerate(vals, start=1):
            running_sum += v
            points.append((float(i), running_sum / i))
        self._ao_plot.set_convergence_data(
            points, "Dependability" if use_dependability else "Availability")
