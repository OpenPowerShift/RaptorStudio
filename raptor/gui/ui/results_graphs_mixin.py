"""The results' charts over time: recorded plot rows into the Charts tab.

The availability and capacity plots the engine records (Raptor 7's AoPlot
and capacity display) used to have their own Graphs tab with hand-painted
plots.  They are now charts like every other (`raptor.api.insight.timecharts`)
in the one Charts tab; this mixin feeds that tab the parsed rows, keeps the
model's saved axis floor, and saves the raw rows on request.

Expects on `self`: the results tabs and the report data held by `ResultsDock`."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raptor.api import run_needs
from raptor.gui.ui import filedialogs as fdg
from raptor.gui.ui import msgbox
from raptor.gui.ui.qtcompat import req

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _Base
    import PySide6.QtCore
    import raptor.gui.ui.charts_page
else:
    _Base = object


class ResultsGraphsMixin(_Base):
    """Recorded plot rows -> the Charts tab's charts over time."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _MAX_PARSE_ROWS: int
        _ao_lines: list[str]
        _charts_page: raptor.gui.ui.charts_page.ChartsPage
        _source_file_name: str
        _status_hint_f: Any  # really Any | None
        y_axis_min_changed: PySide6.QtCore.SignalInstance
        enable_requested: PySide6.QtCore.SignalInstance

        def _refresh_drivers(self) -> None: ...

    def _build_graphs_tab(self) -> None:
        """The Charts tab: every chart, over time included, in one view."""
        from raptor.gui.ui.charts_page import ChartsPage
        page = ChartsPage()
        page.y_floor_changed.connect(self.y_axis_min_changed.emit)
        page.raw_export_requested.connect(self._export_graph_data)
        page.drivers_requested.connect(self._refresh_drivers)
        page.enable_requested.connect(self.enable_requested)
        self._charts_page = page

    def set_y_axis_min(self, value: float) -> None:
        """The model's saved `graphYmin`, as the availability axis floor
        (no signal, no dirty flag)."""
        self._charts_page.set_y_floor(value)

    @property
    def y_axis_min(self) -> float:
        return self._charts_page.y_floor

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

    def _export_graph_data(self, kind: str = "availability") -> None:
        """Save every recorded row of one plot, in Raptor 7's rpt_* layout."""
        if kind == "capacity":
            src, stem = getattr(self, "_cap_lines", None), "rpt_capacity_plot"
        else:
            src, stem = getattr(self, "_ao_lines", None), "rpt_availability_plot"
        lines = self._stream_lines(src)
        if not lines:
            self._status_hint_f and self._status_hint_f(
                "No plot data collected: " + run_needs.gui_hint(
                    "capacity_plot" if kind == "capacity" else "availability_plot")
                .replace("<b>", "").replace("</b>", ""))
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

    def set_capacity_available(self, available: bool) -> None:
        """Kept for the model-load and analysis-toggle callers: the Charts
        tab now decides from the recorded rows themselves, and says what is
        missing when a capacity chart has none."""
        self._capacity_available = bool(available)

    @staticmethod
    def _parse_plot(src, kind: str, points: int) -> list[dict]:
        """Every run in a plot sink, each reduced for drawing, never just
        the first slice.

        A disk-backed `ResultStream` is read line by line from its file; a
        plain list (tests) is streamed from memory.  `kind` chooses the
        availability or capacity columns."""
        from raptor.api.series import (
            parse_ao_file, parse_ao_lines, parse_capacity_file,
            parse_capacity_lines,
        )
        if src is None:
            return []
        path = getattr(src, "path", None)
        if path and hasattr(src, "read_all_lines"):
            try:
                return (parse_ao_file(path, max_points=points) if kind == "ao"
                        else parse_capacity_file(path, max_points=points))
            except FileNotFoundError:
                return []
        lines = src if isinstance(src, list) else [str(line) for line in src]
        return (parse_ao_lines(lines, max_points=points) if kind == "ao"
                else parse_capacity_lines(lines, max_points=points))

    @staticmethod
    def _parse_cost(src, end_of_run=None) -> list[dict]:
        """The interim cost report's cumulative snapshots, from disk or RAM,
        each run closed with its end-of-run cost totals when `end_of_run`
        (the End of Run report rows) is given -- the same series
        `cost_series.stored_runs` gives the MCP tools and the command line."""
        from raptor.api.insight.cost_series import (
            close_runs, load_runs, parse_cost_lines, parse_final_lines,
        )
        if src is None:
            return []

        def disk(stream) -> str | None:
            path = getattr(stream, "path", None)
            return str(path) if path and hasattr(stream, "read_all_lines") else None

        cost_path, final_path = disk(src), disk(end_of_run)
        if cost_path:
            try:
                if end_of_run is None or final_path:
                    return load_runs(cost_path, final_path)
                runs = load_runs(cost_path)
            except FileNotFoundError:
                return []
        else:
            runs = parse_cost_lines(src if isinstance(src, list) else list(src))
        if not end_of_run or not runs:
            return runs
        rows = (end_of_run.read_all_lines() if hasattr(end_of_run, "read_all_lines")
                else end_of_run)
        return close_runs(runs, parse_final_lines(rows))

    def _update_graphs(self) -> None:
        """Re-parse `_ao_lines`/`_cap_lines` into the charts over time.

        The sinks may be plain lists (tests) or disk-backed ResultStreams
        (Files & Reports opt-in).  Every recorded row is read; each run is
        reduced to `_PLOT_POINTS_PER_RUN` points for drawing, with its
        extreme samples kept, so a thousand-run batch is represented by all
        of its runs rather than by the first few million rows."""
        points = getattr(self, "_PLOT_POINTS_PER_RUN", 240)
        ao_runs = self._parse_plot(self._ao_lines, "ao", points)
        cap_runs = self._parse_plot(getattr(self, "_cap_lines", None), "cap", points)
        cost_runs = self._parse_cost(getattr(self, "_cost_lines", None),
                                     getattr(self, "_eor_lines", None))
        total = max(len(self._ao_lines or []),
                    len(getattr(self, "_cap_lines", None) or []))
        self._charts_page.set_series(ao_runs, cap_runs)
        self._charts_page.set_cost_series(cost_runs)
        count = max(len(ao_runs), len(cap_runs))
        self._charts_page.set_note(
            (f"The charts over time use all {total:,} recorded rows, from "
             f"{count:,} run{'' if count == 1 else 's'}; each run is reduced "
             f"to about {points:,} points for drawing, keeping its highs, lows "
             "and ends. Export ▸ Save the recorded plot rows keeps every row.")
            if total > points * 8 else "", time_charts_only=True)
