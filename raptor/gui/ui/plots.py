"""Ao availability plots: Per-Trial, Interval, and Multi-run availability
graphs painted with QPainter -- no plotting dependency.

Data comes from the engine's `_ao_sink` rows (Raptor 7's AoPlot
data, emitted by the orchestrator's per-run interval process):

    SimulationTime        Ao        Green     Yellow    Red       Interval  <- header, once
    t  ao_cum  g_cum  y_cum  r_cum  interval_ao                          <- per sample (6 cols)
    t  ao_cum  g_cum  y_cum  r_cum                                     <- run-end row (5 cols)
    "" / "***...End_of_Run_N***"                                       <- run separators

Per the manual: the Per-Trial plot shows cumulative green/yellow/red
percentages versus sim time, where "the line separating the red region
from the yellow and green regions represents the system availability";
the Interval plot shows the availability observed during the PREVIOUS
sample interval; the Multi-run plot shows min/max/average availability
across runs at each sample time ("as these lines converge ... steady-
state").

Parsing those rows, and the clamped interpolation the multi-run envelope
needs, live in `raptor.api.series` -- the Qt-free owner, shared with the
API/MCP timeseries tool.  This module adapts its run dicts to the
`t/ao/g/y/r/iv` shape the painting code unpacks, and does its own
decimation for painting (`_decimate_for_painting`, which documents why it
is deliberately not the API's LTTB).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from raptor.api.series import (
    _interp, parse_ao_runs as _parse_ao_runs, parse_capacity_runs)
from raptor.gui.resources.design_tokens import ThemeManager

#: Sample keys of one parsed run, as the painting code unpacks them:
#: ``{t, ao, g, y, r, iv}`` for Ao, ``{t, flow, cap}`` for capacity.
#: `raptor.api.series` owns the parsing and the interpolation (it is the
#: Qt-free copy the MCP `get_timeseries` tool uses, and having two
#: parsers for one sink format is how the GUI's copy ended up with its
#: own run-boundary rule and its own missing empty-series guard); the
#: adapters below only re-label its dicts into this shape.
Run = dict


def parse_ao_runs(lines: list[str]) -> list[Run]:
    """Ao-sink text -> [{t, ao, g, y, r, iv}, ...], one dict per run.

    Adapter over `api.series.parse_ao_runs`: the run-boundary rule, the
    `*`-skip, and the row parse all live there.

    Rows with 6 numeric tokens are mid-run interval samples; 5-token rows
    are the run-end row (no Interval column). `iv` entries are None for
    those, and a row too short to carry Green/Yellow/Red carries None for
    them and is dropped here -- the widget paints bands, so it needs the
    full column set to draw anything."""
    runs: list[Run] = []
    for run in _parse_ao_runs("\n".join(lines)):
        cur: Run = {"t": [], "ao": [], "g": [], "y": [], "r": [], "iv": []}
        for t, ao, g, y, r, iv in zip(run["t"], run["values"],
                                       run["green"], run["yellow"],
                                       run["red"], run["interval"]):
            if g is None or y is None or r is None:
                continue          # not a full sink row; nothing to paint
            cur["t"].append(t)
            cur["ao"].append(ao)
            cur["g"].append(g)
            cur["y"].append(y)
            cur["r"].append(r)
            cur["iv"].append(iv)
        if cur["t"]:
            runs.append(cur)
    return runs


def parse_cap_runs(lines: list[str]) -> list[Run]:
    """Capacity-sink rows -> [{t, flow, cap}, ...] per run.

    Adapter over `api.series.parse_capacity_runs`: the row format is
    `time  flow  capacity`, whitespace-split, deduplicated on flow
    change, with runs separated by the same `***End_of_Run_N***` marker
    convention as the Ao sink. Only the capacity column is renamed."""
    return [{"t": run["t"], "flow": run["flow"], "cap": run["capacity"]}
            for run in parse_capacity_runs("\n".join(lines))]


#: A screen cannot usefully show more distinct x pixels than this. A
#: long or many-run simulation can produce tens of thousands of
#: Ao-interval rows, so painting every raw sample makes the plot tab
#: slow; painting is decimated to this many points.
_MAX_PLOT_POINTS = 2000


def _decimate_for_painting(*series: list,
                           max_points: int = _MAX_PLOT_POINTS) -> list[list]:
    """Evenly downsample aligned same-length series to at most
    `max_points` for PAINTING ONLY -- always keeps the first and last
    sample so the line's true endpoints/extent never move. Scrub read-
    outs (`_active_series`) intentionally read the FULL-resolution data
    directly, not this decimated view, so hovering still reports exact
    values; only the drawn line/band loses points a screen couldn't
    render distinctly anyway.

    Deliberately NOT `api.series.decimate`, which is the LTTB
    shape-preserving one used for served/exported series: an exported
    plot and this on-screen plot are therefore not the same curve, and
    that is accepted rather than accidental. Two reasons to keep them
    apart -- this widget only ever draws the decimated points, so the
    cheapest possible uniform striding with pinned endpoints is the whole
    job, whereas the API's series is data handed to a caller who will
    compare numbers against it and needs its spikes kept. Recorded here
    because an exported series plot and the Graphs tab disagreeing about
    which samples survive is otherwise very hard to explain."""
    n = len(series[0]) if series else 0
    if n <= max_points or n == 0:
        return list(series)
    step = n / max_points
    idx = sorted({0, n - 1, *(int(i * step) for i in range(max_points))})
    return [[s[i] for i in idx] for s in series]


def multi_run_envelope(runs: list[Run], points: int = 200):
    """Common-grid min/avg/max of each run's cumulative-Ao trace ->
    (times, mins, avgs, maxs). Grid spans 0..min(last_t) so every run
    covers it fully (Raptor 7 samples every run on the same cadence;
    only the run-end row can differ)."""
    if not runs:
        return [], [], [], []
    horizon = min(r["t"][-1] for r in runs)
    step = (runs[0]["t"][1] - runs[0]["t"][0]) if len(runs[0]["t"]) > 1 else horizon
    if step <= 0:
        step = horizon / points
    n = max(2, min(points, int(horizon / step) + 1))
    times = [horizon * i / (n - 1) for i in range(n)]
    series = [
        [_interp(r["t"], r["ao"], x) for x in times]
        for r in runs
    ]
    mins, avgs, maxs = [], [], []
    for col in zip(*series):
        vals = list(col)
        mins.append(min(vals))
        maxs.append(max(vals))
        avgs.append(sum(vals) / len(vals))
    return times, mins, avgs, maxs


class AoPlotWidget(QWidget):
    """Paints one of six plots: the three Raptor 7 availability graphs,
    Raptor 7's capacity plot (flow vs capacity over time, with the run's
    averages), a multi-run capacity band (min/avg/max flow and capacity),
    and a Convergence plot -- cumulative mean Availability/Dependability
    against the number of runs included so far, showing whether an
    estimate has stabilised or needs more runs. This differs from
    MULTI_RUN, which converges over simulation time within a fixed run
    count rather than over run count itself."""

    PER_TRIAL, INTERVAL, MULTI_RUN, CAPACITY, MULTI_CAPACITY, CONVERGENCE = range(6)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = self.PER_TRIAL
        self._runs: list[Run] = []
        self._run_index = 0
        self._cap_runs: list[Run] = []
        self._convergence_pts: list[tuple[float, float]] = []
        self._convergence_metric_name = "Availability"
        #: The Ao graph's Y-axis minimum (`yMin`, saved as `graphYmin`; Raptor 7's
        #:"Set Y-Axis" accepts 0 up to 0.999999; the maximum is always 1).
        self._y_min = 0.0
        self._scrub_x: float | None = None   # cursor time (data coords)
        self._scrub_px: int | None = None    # last processed cursor pixel
        self._env_cache = None               # memoised multi_run_envelope
        self._cap_env_cache = None           # memoised capacity envelope
        # A floor of 220px here propagated all the way out: the Graphs tab
        # is one page of the Results dock's outer QTabWidget, a stacked
        # layout takes the max over its pages, and a hard minimum on the
        # tallest page is a hard minimum on the dock -- so after a run the
        # Results strip could not be dragged below 220px + toolbar. The
        # widget is a custom paint that rescales its axes to whatever it is
        # given, so it has no minimum worth enforcing; keep only a token
        # one so it cannot collapse to nothing.
        self.setMinimumHeight(48)
        self.setMouseTracking(True)

    # ------------------------------------------------------------------
    def set_data(self, runs: list[Run]):
        self._runs = runs
        self._env_cache = None   # runs replaced: envelope must recompute
        self._run_index = min(self._run_index, max(len(runs) - 1, 0))
        self.update()

    def _envelope(self):
        """Memoised `multi_run_envelope(self._runs)`; the envelope is
        otherwise recomputed on every paint and every mouse-move."""
        if self._env_cache is None:
            self._env_cache = multi_run_envelope(self._runs)
        return self._env_cache

    def set_cap_data(self, runs: list[Run]):
        self._cap_runs = runs
        self._cap_env_cache = None
        self.update()

    def _cap_envelope(self):
        """Common-grid min/avg/max for flow and capacity across all runs.

        Capacity sinks are deduplicated on flow change, so runs have
        different time grids. Interpolate every run onto the longest
        run's grid, then take the band at each time.
        """
        if self._cap_env_cache is not None:
            return self._cap_env_cache
        runs = [r for r in self._cap_runs if r.get("t")]
        if not runs:
            self._cap_env_cache = ([], [], [], [], [], [], [])
            return self._cap_env_cache
        base = max(runs, key=lambda r: len(r["t"]))
        times = list(base["t"])
        fmin, favg, fmax = [], [], []
        cmin, cavg, cmax = [], [], []
        for t in times:
            fs, cs = [], []
            for r in runs:
                fs.append(_interp(r["t"], r["flow"], t))
                cs.append(_interp(r["t"], r["cap"], t))
            fmin.append(min(fs)); fmax.append(max(fs))
            cmin.append(min(cs)); cmax.append(max(cs))
            favg.append(sum(fs) / len(fs))
            cavg.append(sum(cs) / len(cs))
        self._cap_env_cache = (times, fmin, favg, fmax,
                               cmin, cavg, cmax)
        return self._cap_env_cache

    def set_convergence_data(self, pts: list[tuple[float, float]],
                             metric_name: str = "Availability"):
        self._convergence_pts = pts
        self._convergence_metric_name = metric_name
        self.update()

    def set_mode(self, mode: int):
        self._mode = mode
        self.update()

    def set_run(self, index: int):
        self._run_index = index
        self.update()

    @property
    def y_min(self) -> float:
        return self._y_min

    def set_y_min(self, value: float) -> None:
        self._y_min = min(max(float(value), 0.0), 0.999999)
        self.update()

    @property
    def run_count(self) -> int:
        return len(self._runs)

    # ------------------------------------------------------------------
    def paintEvent(self, event):
        c = ThemeManager.instance().colour
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(c["surface"]))
        margin_l, margin_r, margin_t, margin_b = 52, 14, 14, 34
        area = QRectF(self.rect()).adjusted(margin_l, margin_t,
                                            -margin_r, -margin_b)
        if area.width() < 40 or area.height() < 40:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Y scale: availability plots are 0..1; the capacity plot scales
        # to the data's max (flow/cap are integer unit counts).
        cap_mode = self._mode in (self.CAPACITY, self.MULTI_CAPACITY)
        y_max = 1.0
        if self._mode == self.CAPACITY and self._cap_runs:
            run = self._cap_runs[min(self._run_index,
                                     len(self._cap_runs) - 1)]
            y_max = max(max(run["flow"]), max(run["cap"]), 1.0)
        elif self._mode == self.MULTI_CAPACITY and self._cap_runs:
            env = self._cap_envelope()
            y_max = max(max(env[3] or [1]), max(env[6] or [1]), 1.0)
        # Multi-run availability lives in a narrow band near 1.0. A fixed
        # 0..1 axis made every run look like a flat line; use the actual
        # envelope plus a little padding.
        self._y_lo, self._y_hi = self._y_bounds()
        # Axes + gridlines.
        steps = 4
        p.setPen(QPen(QColor(c["border"]), 1))
        for i in range(steps + 1):
            y = area.bottom() - area.height() * i / steps
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        axis_pen = QPen(QColor(c["border"]), 1)
        p.setPen(axis_pen)
        p.drawRect(area)
        f = self.font()
        f.setPointSizeF(8)
        p.setFont(f)
        p.setPen(QColor(c["muted"]))
        span = max(1e-12, self._y_hi - self._y_lo)
        dec = (4 if span < 0.02 else 3 if span < 0.2 else 2)
        for i in range(steps + 1):
            y = area.bottom() - area.height() * i / steps
            label = (f"{y_max * i / steps:g}" if cap_mode
                     else f"{self._y_lo + span * i / 4:.{dec}f}")
            p.drawText(QRectF(0, y - 7, margin_l - 6, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

        runs = self._runs
        if self._mode == self.CAPACITY:
            if not self._cap_runs:
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                           "No capacity plot data — tick Capacity plot "
                           "under Run panel ▸ Recording and run again.")
                return
            run = self._cap_runs[min(self._run_index,
                                     len(self._cap_runs) - 1)]
            y_max = max(max(run["flow"]), max(run["cap"]), 1.0)
            t_end = run["t"][-1] if run["t"] else 1.0
            x_mapper = self._x_mapper(area, t_end)
            dec_t, dec_flow, dec_cap = _decimate_for_painting(
                run["t"], run["flow"], run["cap"])

            def line(vals, colour, width, style):
                pen = QPen(QColor(c[colour]), width, style)
                p.setPen(pen)
                prev = None
                for t, v in zip(dec_t, vals):
                    q = QPointF(x_mapper(t),
                                area.bottom() - area.height()
                                * (v / y_max))
                    if prev is not None:
                        p.drawLine(prev, q)
                    prev = q
            # Actual flow (solid accent) vs achievable capacity
            # (dashed muted) — the gap is lost flow.
            line(dec_cap, "muted", 1.4, Qt.PenStyle.DashLine)
            line(dec_flow, "accent", 2.0, Qt.PenStyle.SolidLine)
            # Run averages: horizontal references so the reader can see
            # how far the run sat from its own mean, not just the trace.
            avg_flow = (sum(run["flow"]) / len(run["flow"])
                        if run["flow"] else 0.0)
            avg_cap = (sum(run["cap"]) / len(run["cap"])
                       if run["cap"] else 0.0)
            for value, colour, style in (
                    (avg_flow, "accent", Qt.PenStyle.DotLine),
                    (avg_cap, "muted", Qt.PenStyle.DotLine)):
                y = area.bottom() - area.height() * (value / y_max)
                p.setPen(QPen(QColor(c[colour]), 1, style))
                p.drawLine(QPointF(area.left(), y),
                           QPointF(area.right(), y))
            legend = ("— flow   ‑ ‑ capacity"
                      f"   ·   avg flow {avg_flow:g}, "
                      f"avg cap {avg_cap:g}"
                      f"   ·   run {self._run_index + 1}"
                      f" of {len(self._cap_runs)}")
            self._legend(p, area, c, legend)
            self._draw_x_ticks(p, area, run["t"][-1], c)
            self._draw_scrub(p, area, c)
            return
        if self._mode == self.MULTI_CAPACITY:
            if not self._cap_runs:
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                           "No capacity plot data — tick Capacity plot "
                           "under Run panel ▸ Recording and run again.")
                return
            (times, fmin, favg, fmax,
             cmin, cavg, cmax) = self._cap_envelope()
            if not times:
                return
            x_mapper = self._x_mapper(area, times[-1])

            def series_line(vals, colour, width, style):
                pen = QPen(QColor(c[colour]), width, style)
                p.setPen(pen)
                prev = None
                for t, v in zip(times, vals):
                    q = QPointF(x_mapper(t),
                                area.bottom() - area.height()
                                * (v / y_max))
                    if prev is not None:
                        p.drawLine(prev, q)
                    prev = q
            # Flow family in accent, capacity family in muted: average
            # solid, min/max thin dashed.
            series_line(fmin, "accent", 1.0, Qt.PenStyle.DashLine)
            series_line(fmax, "accent", 1.0, Qt.PenStyle.DashLine)
            series_line(favg, "accent", 2.2, Qt.PenStyle.SolidLine)
            series_line(cmin, "muted", 1.0, Qt.PenStyle.DashLine)
            series_line(cmax, "muted", 1.0, Qt.PenStyle.DashLine)
            series_line(cavg, "muted", 2.2, Qt.PenStyle.SolidLine)
            self._legend(
                p, area, c,
                "flow min/avg/max (accent) · capacity min/avg/max "
                f"(muted) · {len(self._cap_runs)} runs")
            self._draw_x_ticks(p, area, times[-1], c)
            self._draw_scrub(p, area, c)
            return
        if self._mode == self.CONVERGENCE:
            points = self._convergence_pts
            if len(points) < 2:
                p.drawText(
                    self.rect(), Qt.AlignmentFlag.AlignCenter,
                    "Need at least 2 runs with availability data to show "
                    "convergence — run the model again.")
                return
            self._paint_line(p, area, c, points, QColor(c["accent"]),
                             integer_x=True)
            p.setPen(QColor(c["text"]))
            p.drawText(
                QRectF(area.left(), 0, area.width(), margin_t),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                f"{self._convergence_metric_name} Convergence — "
                f"{len(points)} runs, cumulative mean after {int(points[-1][0])}"
                f" (last: {points[-1][1]:.4f})")
            p.setPen(QColor(c["muted"]))
            p.drawText(QRectF(area.left() + 2, area.top() + 2,
                              area.width(), 14), Qt.AlignmentFlag.AlignLeft,
                       "x = number of runs included so far")
            return
        if not runs:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "No availability plot data — tick Availability plot "
                       "under Run panel ▸ Recording and run again.")
            return

        if self._mode == self.PER_TRIAL:
            run = runs[min(self._run_index, len(runs) - 1)]
            self._paint_stacked(p, area, c, run)
            title = (f"Per Trial Ao — run {self._run_index + 1}"
                     f" of {len(runs)}")
        elif self._mode == self.INTERVAL:
            run = runs[min(self._run_index, len(runs) - 1)]
            self._paint_line(p, area, c,
                             [(t, v) for t, v in zip(run["t"], run["iv"])
                              if v is not None],
                             QColor(c["accent"]))
            title = f"Interval Ao — run {self._run_index + 1} of {len(runs)}"
        else:
            self._paint_multi(p, area, c)
            title = f"Multi-run Ao — {len(runs)} runs"
        p.setPen(QColor(c["text"]))
        p.drawText(QRectF(area.left(), 0, area.width(), margin_t),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, title)
        p.end()

    # ------------------------------------------------------------------
    @staticmethod
    def _x_mapper(area: QRectF, t_max: float):
        t_max = t_max if t_max > 0 else 1.0

        def map(t: float) -> float:
            return area.left() + area.width() * (t / t_max)
        return map

    def _y_bounds(self) -> tuple[float, float]:
        """Visible Y range: auto for Multi-run, manual for the rest."""
        lo0 = max(0.0, float(self._y_min))
        hi0 = 1.0
        if self._mode != self.MULTI_RUN or not self._runs:
            return lo0, hi0
        _times, mins, _avgs, maxs = self._envelope()
        vals = [v for v in (*mins, *maxs) if v is not None]
        if not vals:
            return lo0, hi0
        lo, hi = min(vals), max(vals)
        if hi <= lo:
            return max(0.0, lo - 0.01), min(1.0, hi + 0.01)
        pad = (hi - lo) * 0.08
        return max(lo0, lo - pad), min(hi0, hi + pad)

    def _y_map(self, area: QRectF, v: float) -> float:
        v = max(0.0, min(v, 1.0))
        lo = getattr(self, "_y_lo", self._y_min)
        hi = getattr(self, "_y_hi", 1.0)
        span = hi - lo
        if span <= 0:
            span = 1.0
        frac = (v - lo) / span
        return area.bottom() - area.height() * max(0.0, min(frac, 1.0))

    def _paint_stacked(self, p: QPainter, area: QRectF, c: dict, run: Run):
        """Cumulative G/Y/R percentage bands; the yellow/red boundary is
        system availability vs time."""
        t_end = run["t"][-1]
        ts, gs, ys_, _rs = _decimate_for_painting(
            run["t"], run["g"], run["y"], run["r"])
        x_mapper = self._x_mapper(area, t_end)

        def band(lower: list[float], upper: list[float], colour: str):
            polygon = QPolygonF()
            for t, _lo_v, hi_v in zip(ts, lower, upper):
                polygon.append(QPointF(x_mapper(t), self._y_map(area, hi_v)))
            for t, lo_v in reversed(list(zip(ts, lower))):
                polygon.append(QPointF(x_mapper(t), self._y_map(area, lo_v)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c[colour]))
            p.drawPolygon(polygon)

        zeros = [0.0] * len(ts)
        tops_gy = [g + y for g, y in zip(gs, ys_)]
        band(zeros, gs, "result_green")       # green: fully working
        band(gs, tops_gy, "result_yellow")    # yellow: degraded
        band(tops_gy, [1.0] * len(ts), "result_red")  # red: down
        # Availability boundary line (yellow/red separator).
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor(c["text"]), 1.6)
        p.setPen(pen)
        for i in range(1, len(ts)):
            p.drawLine(QPointF(x_mapper(ts[i - 1]), self._y_map(area, tops_gy[i - 1])),
                       QPointF(x_mapper(ts[i]), self._y_map(area, tops_gy[i])))
        self._draw_x_ticks(p, area, ts[-1], c)
        self._draw_scrub(p, area, c)

    def _paint_line(self, p: QPainter, area: QRectF, c: dict, points, colour,
                    *, integer_x: bool = False):
        if len(points) < 2:
            return
        t_end = points[-1][0]
        ts, vs = _decimate_for_painting([t for t, _ in points],
                                        [v for _, v in points])
        x_mapper = self._x_mapper(area, t_end)
        pen = QPen(colour, 1.8)
        p.setPen(pen)
        prev = None
        for t, v in zip(ts, vs):
            q = QPointF(x_mapper(t), self._y_map(area, v))
            if prev is not None:
                p.drawLine(prev, q)
            prev = q
        self._draw_x_ticks(p, area, t_end, c, integer=integer_x)
        self._draw_scrub(p, area, c)

    def _paint_multi(self, p: QPainter, area: QRectF, c: dict):
        times, mins, avgs, maxs = self._envelope()
        if not times:
            return
        x_mapper = self._x_mapper(area, times[-1])
        for series, colour, width in ((mins, "result_green", 1),
                                      (maxs, "result_red", 1),
                                      (avgs, "accent", 2)):
            pen = QPen(QColor(c[colour]), width)
            p.setPen(pen)
            prev = None
            for t, v in zip(times, series):
                q = QPointF(x_mapper(t), self._y_map(area, v))
                if prev is not None:
                    p.drawLine(prev, q)
                prev = q
        legend = "min / avg / max availability across runs"
        p.setPen(QColor(c["muted"]))
        p.drawText(QRectF(area.left(), area.top() + 2, area.width(), 14),
                   Qt.AlignmentFlag.AlignRight, legend)
        self._draw_x_ticks(p, area, times[-1], c)
        self._draw_scrub(p, area, c)

    @staticmethod
    def _legend(p: QPainter, area: QRectF, c: dict, text: str,
                right: bool = False) -> None:
        """Draw a short legend on an opaque strip so traces behind it do
        not run through the words (capacity lines often sit at the top)."""
        fm = p.fontMetrics()
        w = min(area.width(), fm.horizontalAdvance(text) + 10)
        x = area.right() - w if right else area.left()
        rect = QRectF(x, area.top() + 2, w, fm.height() + 2)
        p.fillRect(rect, QColor(c["surface"]))
        p.setPen(QColor(c["muted"]))
        p.drawText(rect.adjusted(3, 0, -3, 0),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   text)

    def _draw_x_ticks(self, p: QPainter, area: QRectF, t_max: float, c: dict,
                      *, integer: bool = False):
        # ~9 ticks: enough numbers to read values off without crowding.
        n = 8
        p.setPen(QColor(c["muted"]))
        for i in range(n + 1):
            t = t_max * i / n
            label = f"{round(t)}" if integer else f"{t:g}"
            p.drawText(
                QRectF(area.left() + area.width() * i / n - 34,
                       area.bottom() + 4, 68, 14),
                Qt.AlignmentFlag.AlignHCenter, label)

    # ------------------------------------------------------------------
    # Scrubbing: crosshair + value readout at the cursor's time.
    # ------------------------------------------------------------------

    def _active_series(self):
        """(times, values_by_label, y_max) for the CURRENT mode/run."""
        if self._mode == self.CAPACITY:
            runs = self._cap_runs
            if not runs:
                return None
            run = runs[min(self._run_index, len(runs) - 1)]
            ymax = max(max(run["flow"]), max(run["cap"]), 1.0)
            return (run["t"],
                    [("flow", run["flow"]), ("cap", run["cap"])], ymax)
        if self._mode == self.MULTI_CAPACITY:
            if not self._cap_runs:
                return None
            (times, fmin, favg, fmax,
             cmin, cavg, cmax) = self._cap_envelope()
            if not times:
                return None
            ymax = max(max(fmax or [1]), max(cmax or [1]), 1.0)
            return (times, [("flow min", fmin), ("flow avg", favg),
                            ("flow max", fmax), ("cap min", cmin),
                            ("cap avg", cavg), ("cap max", cmax)], ymax)
        runs = self._runs
        if not runs:
            return None
        run = runs[min(self._run_index, len(runs) - 1)]
        if self._mode == self.PER_TRIAL:
            return (run["t"],
                    [("Ao", run["ao"]), ("G", run["g"]), ("Y", run["y"]),
                     ("R", run["r"])], 1.0)
        if self._mode == self.INTERVAL:
            pairs = [(t, v) for t, v in zip(run["t"], run["iv"])
                     if v is not None]
            if not pairs:
                return None
            return ([p[0] for p in pairs],
                    [("Interval Ao", [p[1] for p in pairs])], 1.0)
        times, mins, avgs, maxs = self._envelope()
        return (times, [("min", mins), ("avg", avgs), ("max", maxs)], 1.0)

    def mouseMoveEvent(self, event):
        pos = event.position()
        margin_l, margin_r = 52, 14
        plot_w = self.width() - margin_l - margin_r
        if plot_w <= 0:
            return
        # Throttle to whole-pixel changes: repainting on every mouse-move
        # pixel would make the plot lag.
        px = int(pos.x())
        if px == self._scrub_px:
            return
        self._scrub_px = px
        frac = (px - margin_l) / plot_w
        if 0.0 <= frac <= 1.0:
            self._scrub_x = frac
        else:
            self._scrub_x = None
        self.update()

    def leaveEvent(self, event):
        self._scrub_x = None
        self._scrub_px = None
        self.update()
        super().leaveEvent(event)

    def _draw_scrub(self, p: QPainter, area: QRectF, c: dict):
        """Crosshair + readout at the cursor's time."""
        active = self._active_series()
        if active is None or self._scrub_x is None:
            return
        times, series, y_max = active
        if not times:
            return
        t_max = times[-1]
        t = t_max * self._scrub_x
        x_mapper = self._x_mapper(area, t_max)
        p.setPen(QPen(QColor(c["accent"]), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(x_mapper(t), area.top()),
                   QPointF(x_mapper(t), area.bottom()))
        # Readout: nearest sample values at/just-before t.
        import bisect
        i = max(0, min(bisect.bisect_right(times, t) - 1, len(times) - 1))
        _y_map = (lambda v: area.bottom() - area.height()
                 * (v / y_max)) if self._mode in (
                     self.CAPACITY, self.MULTI_CAPACITY) \
            else (lambda v: self._y_map(area, v))
        lines = [f"t = {t:g}"]
        for label, vals in series:
            lines.append(f"{label} = {vals[i]:.4f}")
        f = self.font()
        f.setPointSizeF(8)
        p.setFont(f)
        fm = p.fontMetrics()
        w = max(fm.horizontalAdvance(l) for l in lines) + 12
        h = fm.height() * len(lines) + 8
        box_x = min(x_mapper(t) + 8, area.right() - w)
        box = QRectF(box_x, area.top() + 2, w, h)
        p.setBrush(QColor(c["surface"]))
        p.setPen(QPen(QColor(c["border"]), 1))
        p.drawRect(box)
        p.setPen(QColor(c["text"]))
        for li, line in enumerate(lines):
            p.drawText(QRectF(box.x() + 6,
                              box.y() + 4 + li * fm.height(),
                              w - 8, fm.height()),
                       Qt.AlignmentFlag.AlignLeft, line)
