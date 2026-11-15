"""The results' charts over time: recorded plot rows into the Charts tab.

The availability and capacity plots the engine records (Raptor 7's AoPlot
and capacity display) used to have their own Graphs tab with hand-painted
plots.  They are now charts like every other (`raptor.api.insight.timecharts`)
in the one Charts tab; this mixin feeds that tab the parsed rows, keeps the
model's saved axis floor, and saves the raw rows on request.

Expects on `self`: the results tabs and the report data held by `ResultsDock`."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

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

        def _refresh_drivers(self) -> None: ...

    def _build_graphs_tab(self) -> None:
        """The Charts tab: every chart, over time included, in one view."""
        from raptor.gui.ui.charts_page import ChartsPage
        page = ChartsPage()
        page.y_floor_changed.connect(self.y_axis_min_changed.emit)
        page.raw_export_requested.connect(self._export_graph_data)
        page.drivers_requested.connect(self._refresh_drivers)
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

    def set_capacity_available(self, available: bool) -> None:
        """Kept for the model-load and analysis-toggle callers: the Charts
        tab now decides from the recorded rows themselves, and says what is
        missing when a capacity chart has none."""
        self._capacity_available = bool(available)

    def _update_graphs(self) -> None:
        """Re-parse `_ao_lines`/`_cap_lines` into the charts over time.

        The sinks may be plain lists (tests) or disk-backed ResultStreams
        (Files & Reports opt-in): rows are read lazily from the file and
        the interactive view is capped at `_MAX_PARSE_ROWS` with a visible
        note, so the engine never holds them in RAM."""
        from raptor.api.series import parse_ao_runs, parse_capacity_runs
        ao_all = self._stream_lines(self._ao_lines)
        cap_all = self._stream_lines(getattr(self, "_cap_lines", None) or [])
        limit = self._MAX_PARSE_ROWS
        capped = max(len(ao_all), len(cap_all)) > limit
        ao_runs = parse_ao_runs("\n".join(ao_all[:limit]))
        cap_runs = parse_capacity_runs("\n".join(cap_all[:limit]))
        self._charts_page.set_series(ao_runs, cap_runs)
        self._charts_page.set_note(
            f"The charts over time show the first {limit:,} recorded rows; "
            f"Export ▸ Save the recorded plot rows has all "
            f"{max(len(ao_all), len(cap_all)):,}." if capped else "")
