"""Sweep and Compare: the same model run many ways, at full speed.

Sweep changes one number (a block's failure or repair parameter, a rate, a
node's k-of-n) over a range and runs the model at each value. Compare runs
this model beside other model files. Both are `raptor.api.batch.run_variants`
-- what `raptor sweep` and `raptor compare` call -- run in a worker thread
on a private copy of the model in a throwaway workspace, so the open model
is never touched and the window stays live. They use the cores in the
Simulate tab's Cores box, and never animate.

Both windows are modeless: you can keep editing while one runs, and the run
uses the model as it was when you pressed Run.
"""
from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
    QProgressBar, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from raptor.gui import cores
from raptor.gui.resources.design_tokens import active_colour

#: Metrics offered, in the order the Results dock shows them, with whether
#: more is better (for "best" highlighting and the chart's caption).
METRICS: tuple[tuple[str, str, bool], ...] = (
    ("Availability", "Availability", True),
    ("Reliability", "Reliability", True),
    ("Dependability", "Dependability", True),
    ("MTBDE", "MTBDE", True),
    ("MDT", "MDT", False),
    ("MTBM", "MTBM", True),
    ("MMT", "MMT", False),
    ("System_Failures", "System failures", False),
    ("Red_Percent", "Red time %", False),
)
_LABEL = {k: lbl for k, lbl, _ in METRICS}
_HIGHER_BETTER = {k: hb for k, _, hb in METRICS}
_TABLE_METRICS = ("Availability", "MTBDE", "MDT", "System_Failures")


def fmt(v: float | None) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.6g}"
    if 0.99 <= abs(v) < 1:          # availability-like: don't round to 1
        return f"{v:.7g}"
    return f"{v:.6g}" if abs(v) >= 1e-3 or v == 0 else f"{v:.3e}"


def best_indices(vals: list[float | None], higher: bool) -> set[int]:
    """Where the best value is -- every one, when several tie. Empty when
    fewer than two values, or all equal (nothing to pick out)."""
    ok = [v for v in vals if v is not None]
    if len(ok) < 2 or len(set(ok)) == 1:
        return set()
    best = max(ok) if higher else min(ok)
    return {i for i, v in enumerate(vals) if v == best}


def _mean(entry: dict, metric: str) -> float | None:
    s = (entry.get("summary") or {}).get(metric)
    return None if s is None else s.get("mean")


def _sem(entry: dict, metric: str) -> float | None:
    s = (entry.get("summary") or {}).get(metric)
    return None if s is None else s.get("sem")


def _trials(rbd) -> int:
    try:
        return max(1, int(float(rbd.section("SYSTEM_DATA").rows[0]
                                .get("NumRuns", 1))))
    except (KeyError, IndexError, ValueError, TypeError):
        return 100


# --------------------------------------------------------------------------
# What can be swept


@dataclass(frozen=True)
class SweepTarget:
    """One number in the model: what the user sees, and its path text."""
    label: str           # "Failure: Mean (hours)"
    path: str            # "block:12.fail_params[0]"
    current: float
    integer: bool = False
    lo: float | None = None     # hard limits (k-of-n: 1..n)
    hi: float | None = None
    note: str = ""              # "linked to @GenMTBF"


def _f(text: Any, default: float = 0.0) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _rows(rbd, section: str) -> list[dict]:
    try:
        return [r for r in rbd.section(section).rows
                if str(r.get("ID", "0")) not in ("", "0")]
    except KeyError:
        return []


def _rate_choices(rbd) -> list[tuple[str, list[SweepTarget]]]:
    return [(f"Rate  @{r['name']}", [SweepTarget(
        "Value", f"rate:{r['name']}", _f(r.get("value")),
        note=r.get("description", "") or "")])
        for r in getattr(rbd, "rates", None) or [] if r.get("name")]


def _block_targets(row: dict, formulas: dict) -> list[SweepTarget]:
    from raptor.core.distro_rules import DISTRO_NAMES, DISTRO_PARAM_LABELS
    bid = str(row.get("ID"))
    targets: list[SweepTarget] = []
    for side, dcol, pcol, field in (
            ("Failure", "FDist", "FParam", "fail_params"),
            ("Repair", "RDist", "RParam", "repair_params")):
        code = int(_f(row.get(dcol), 18))
        labels = DISTRO_PARAM_LABELS.get(code, ("", "", ""))
        dname = DISTRO_NAMES.get(code, "")
        for i, lbl in enumerate(labels):
            if not lbl:
                continue
            col = f"{pcol}{i + 1}"
            rate = (formulas.get(f"block:{bid}:{col}") or {}).get("rate")
            note = (f"linked to @{rate} — sweep the rate instead to move "
                    f"every block that uses it" if rate else "")
            targets.append(SweepTarget(
                f"{side}: {lbl} ({dname})", f"block:{bid}.{field}[{i}]",
                _f(row.get(col)), note=note))
    return targets


def _node_choices(rbd) -> list[tuple[str, list[SweepTarget]]]:
    out = []
    for row in _rows(rbd, "NODE_DATA"):
        n, k = int(_f(row.get("n"), 0)), int(_f(row.get("k"), 0))
        if n >= 2 and k >= 1:
            out.append((f"Node  {row.get('Name', row.get('ID'))}", [SweepTarget(
                f"k — paths needed (of {n})", f"node:{row.get('ID')}.k",
                float(k), integer=True, lo=1, hi=n)]))
    return out


def element_choices(rbd) -> list[tuple[str, list[SweepTarget]]]:
    """[(element label, its sweepable numbers)], rates first, then blocks,
    then k-of-n nodes. An element with nothing to sweep is left out."""
    formulas = getattr(rbd, "formulas", None) or {}
    rows = _rows(rbd, "BLOCK_DATA")
    names = [row.get("blockName", row.get("ID")) for row in rows]
    # Two blocks may share a name (in different hierarchies): add the id.
    blocks = [(f"Block  {n}" + (f"  (id {row.get('ID')})"
                                if names.count(n) > 1 else ""),
               _block_targets(row, formulas))
              for row, n in zip(rows, names)]
    return (_rate_choices(rbd) + [b for b in blocks if b[1]]
            + _node_choices(rbd))


def invalid_value(rbd, target: SweepTarget, values: list[str]) -> str | None:
    """Why a swept value cannot be used, checked BEFORE running, with the
    same rules the Inspector applies (a negative mean, a Triangular minimum
    above its maximum...). None when every value is fine."""
    import re
    from raptor.core.distro_rules import validate_distro_params
    m = re.match(r"block:(\d+)\.(fail|repair)_params\[(\d)\]$", target.path)
    if not m:
        return None
    bid, side, idx = m.group(1), m.group(2), int(m.group(3))
    row = next((r for r in rbd.section("BLOCK_DATA").rows
                if str(r.get("ID")) == bid), None)
    if row is None:
        return None
    pre = "F" if side == "fail" else "R"
    dist = int(_f(row.get(f"{pre}Dist"), 18))
    params = [_f(row.get(f"{pre}Param{i}")) for i in (1, 2, 3)]
    for v in values:
        trial = list(params)
        trial[idx] = float(v)
        why = validate_distro_params(dist, trial, "")
        if why:
            why = why.strip()
            return (f"Can't sweep to {v}: {why[0].lower()}{why[1:]} "
                    f"({target.label}). Change the range.")
    return None


def sweep_values(lo: float, hi: float, points: int, integer: bool) -> list[str]:
    """`points` evenly spaced values from lo to hi, as path value text."""
    from raptor.api.batch import _trim
    if points <= 1 or lo == hi:
        vals = [lo]
    else:
        # Six significant figures: 500..3000 in 8 values is 857.143, not
        # 857.142857142857.
        vals = [float(f"{lo + (hi - lo) * i / (points - 1):.6g}")
                for i in range(points)]
    if integer:
        seen: list[int] = []
        for v in vals:
            if round(v) not in seen:
                seen.append(round(v))
        return [str(v) for v in seen]
    return [_trim(v) for v in vals]


# --------------------------------------------------------------------------
# The worker


class BatchJob:
    """Runs variants one at a time in a thread; the GUI polls `results`.

    Each variant is its own `run_variants` call so finished points can be
    shown while the rest run. Only plain data crosses the thread boundary.
    """

    def __init__(self, models: list[tuple[str, Any, list[str]]], *,
                 num_runs: int | None, workers: int):
        # models: [(label, rbd copy, assignments)]
        self._models = models
        self.num_runs = num_runs
        self.workers = workers
        self.results: list[dict] = []
        self.current = ""
        self.done = False
        self.abort = threading.Event()
        self._tmp = tempfile.TemporaryDirectory(prefix="raptor-batch-")
        self._thread = threading.Thread(target=self._work, daemon=True)

    @property
    def total(self) -> int:
        return len(self._models)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.abort.set()

    def is_running(self) -> bool:
        return self._thread.is_alive()

    def _work(self) -> None:
        from raptor.api import batch
        from raptor.api.workspace import Workspace
        try:
            ws = Workspace(self._tmp.name)
            for label, rbd, assigns in self._models:
                if self.abort.is_set():
                    break
                self.current = label
                try:
                    mid = ws.register_model(rbd, name="batch")["model_id"]
                    (entry,) = batch.run_variants(
                        ws, mid, [{"label": label, "assignments": assigns}],
                        num_runs=self.num_runs, workers=self.workers,
                        timeout_s=24 * 3600.0, abort=self.abort)
                except Exception as exc:                  # noqa: BLE001
                    entry = {"label": label, "assignments": assigns,
                             "error": f"{type(exc).__name__}: {exc}"}
                if self.abort.is_set():
                    # Stopped part-way: a point with no results is not a
                    # failure, just not run; keep one that has some.
                    if "error" in entry:
                        break
                    entry["stopped"] = True
                self.results.append(entry)
        finally:
            self.done = True

    def cleanup(self) -> None:
        if self.is_running():
            self.abort.set()
            self._thread.join(timeout=30)
        try:
            self._tmp.cleanup()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Shared dialog parts


class _BatchDialog(QDialog):
    """Run / Stop, a progress line, CSV export, and the poll timer."""

    TITLE = ""

    def __init__(self, win):
        super().__init__(win)
        self._win = win
        self._job: BatchJob | None = None
        self._model_key: tuple = ()     # set by the window that opens it
        self.setWindowTitle(self.TITLE)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._poll)

        self._trials = QSpinBox()
        self._trials.setRange(1, 10_000_000)
        self._trials.setGroupSeparatorShown(True)
        self._trials.setValue(_trials(win._rbd))
        self._trials.setToolTip("Trials (replications) at every point")
        self._trials.valueChanged.connect(self._update_plan)

        self._plan = QLabel()
        self._plan.setWordWrap(True)
        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)
        self._run_btn = QPushButton("Run")
        self._run_btn.setDefault(True)
        self._run_btn.clicked.connect(self._start)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        self._export_btn = QPushButton("Export CSV…")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        self._buttons = QHBoxLayout()
        self._buttons.addWidget(self._export_btn)
        self._buttons.addStretch(1)
        self._buttons.addWidget(self._run_btn)
        self._buttons.addWidget(self._stop_btn)
        self._buttons.addWidget(close)

    # -- subclass hooks ---------------------------------------------------
    def _variants(self) -> list[tuple[str, Any, list[str]]] | str:
        """[(label, rbd copy, assignments)], or a reason it can't run."""
        raise NotImplementedError

    def _show_results(self) -> None:
        raise NotImplementedError

    def _csv(self) -> tuple[list[str], list[list[str]]]:
        raise NotImplementedError

    # -- plumbing -----------------------------------------------------------
    def _plan_text(self, points: int) -> str:
        n = cores.chosen()
        return (f"{points} run{'s' if points != 1 else ''} × "
                f"{self._trials.value():,} trials, on {n} of "
                f"{cores.available()} core{'s' if cores.available() != 1 else ''}"
                f" (Simulate ▸ Run ▸ Cores). Runs on a copy: the open "
                f"model is not changed.")

    def _update_plan(self) -> None:
        pass

    def _start(self) -> None:
        if self._job is not None and self._job.is_running():
            return
        if self._win._rbd is None:
            self._plan.setText("Open a model first.")
            return
        variants = self._variants()
        if isinstance(variants, str):
            self._plan.setText(variants)
            return
        if self._job is not None:
            self._job.cleanup()
        self._job = BatchJob(variants, num_runs=self._trials.value(),
                             workers=cores.chosen())
        self._progress.setRange(0, self._job.total)
        self._progress.setValue(0)
        self._progress.setFormat(f"0 of {self._job.total}")
        self._progress.setVisible(True)
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._export_btn.setEnabled(False)
        self._set_inputs_enabled(False)
        self._shown = 0
        self._show_results()
        self._job.start()
        self._timer.start()

    def _set_inputs_enabled(self, on: bool) -> None:
        pass

    def _stop(self) -> None:
        if self._job is not None:
            self._job.stop()
            self._stop_btn.setEnabled(False)
            self._progress.setFormat("Stopping after the current run…")

    def _poll(self) -> None:
        job = self._job
        if job is None:
            self._timer.stop()
            return
        n = len(job.results)
        if n != getattr(self, "_shown", 0):
            self._shown = n
            self._show_results()
        if not job.done:
            self._progress.setValue(n)
            if not job.abort.is_set():
                self._progress.setFormat(
                    f"{n} of {job.total} — running {job.current}")
            return
        self._timer.stop()
        self._show_results()
        self._progress.setValue(n)
        stopped = job.abort.is_set()
        errors = sum(1 for r in job.results if r.get("error"))
        msg = (f"Stopped: {n} of {job.total} done" if stopped
               else f"Done: {n} run{'s' if n != 1 else ''}")
        if errors:
            msg += f", {errors} failed (hover the row for why)"
        self._progress.setFormat(msg)
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._export_btn.setEnabled(bool(job.results))
        self._set_inputs_enabled(True)

    def _export(self) -> None:
        from raptor.gui.ui.filedialogs import getSaveFileName
        from raptor.gui.ui.table_export import grid_to_csv
        start = os.path.dirname(self._win._current_file or "") or os.getcwd()
        path, _ = getSaveFileName(self, f"Export {self.TITLE}",
                                  os.path.join(start, f"{self.TITLE.lower()}.csv"),
                                  "CSV files (*.csv)")
        if not path:
            return
        headers, rows = self._csv()
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(grid_to_csv(headers, rows))
        self._progress.setFormat(f"Saved {os.path.basename(path)}")

    def closeEvent(self, event):                          # noqa: N802
        if self._job is not None and self._job.is_running():
            self._job.stop()
        self._timer.stop()
        super().closeEvent(event)

    def done(self, r):                                    # noqa: D401
        if self._job is not None:
            self._job.cleanup()
            # Finish the bookkeeping the poll timer would have: closed while
            # running, the window came back with Run off and Stop on for
            # good. The points that finished stay, as after Stop.
            self._poll()
        super().done(r)


def model_labels(paths: list) -> list[str]:
    """Short names for the models to compare: the file name, with its
    folder added where two files share a name. An entry may also be a
    model open in another window (`open_models.OpenModel`), named
    "<name> (open)"."""
    names = [os.path.basename(p) if isinstance(p, str) else None
             for p in paths]
    out = []
    for p, n in zip(paths, names):
        if n is None:
            out.append(f"{p.label} (open)")
            continue
        if names.count(n) > 1:
            parent = os.path.basename(os.path.dirname(os.path.abspath(p)))
            n = f"{parent}/{n}"
        out.append(n)
    return out


def _model_copy(win):
    return win._model_for_engine(copy=True)


# --------------------------------------------------------------------------
# Sweep


class SweepChart(QWidget):
    """One metric against the swept value: a line, points, and a 95% band
    (mean ± 1.96 standard errors), with the model's current value marked."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 220)
        self.points: list[tuple[float, float, float]] = []   # x, mean, sem
        self.x_label = ""
        self.y_label = ""
        self.current_x: float | None = None

    def set_data(self, points, x_label, y_label, current_x):
        self.points = sorted(points)
        self.x_label, self.y_label, self.current_x = x_label, y_label, current_x
        self.update()

    _LEFT, _RIGHT, _TOP, _BOTTOM = 92, 36, 14, 44

    def _bands(self):
        lows = [m - 1.96 * (s or 0) for _, m, s in self.points]
        highs = [m + 1.96 * (s or 0) for _, m, s in self.points]
        return lows, highs

    def _ranges(self, lows, highs):
        """(x0, x1, y0, y1, pad): the data extent, never zero-width, with
        a margin above and below."""
        xs = [x for x, _, _ in self.points]
        if self.current_x is not None:
            xs.append(self.current_x)
        x0, x1 = min(xs), max(xs)
        if x1 == x0:
            x0, x1 = x0 - 1, x1 + 1
        y0, y1 = min(lows), max(highs)
        if y1 == y0:
            grow = abs(y0) * 0.01 or 1.0
            y0, y1 = y0 - grow, y1 + grow
        pad = (y1 - y0) * 0.08
        return x0, x1, y0 - pad, y1 + pad, pad

    def paintEvent(self, _ev):                            # noqa: N802
        c = active_colour()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(c["surface"]))
        plot = QRectF(self._LEFT, self._TOP,
                      max(10, self.width() - self._LEFT - self._RIGHT),
                      max(10, self.height() - self._TOP - self._BOTTOM))
        p.setPen(QPen(QColor(c["border"]), 1))
        p.drawRect(plot)
        p.setPen(QColor(c["muted"]))
        if not self.points:
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter,
                       "Results appear here as each run finishes")
            p.end()
            return
        lows, highs = self._bands()
        x0, x1, y0, y1, pad = self._ranges(lows, highs)

        def X(v):  # noqa: N802
            return plot.left() + (v - x0) / (x1 - x0) * plot.width()

        def Y(v):  # noqa: N802
            return plot.bottom() - (v - y0) / (y1 - y0) * plot.height()

        self._draw_axes(p, c, plot, X, Y, (y0 + pad, (y0 + y1) / 2, y1 - pad))
        self._draw_series(p, c, plot, X, Y, lows, highs)
        p.end()

    def _draw_axes(self, p, c, plot, X, Y, y_ticks):      # noqa: N803
        for v in y_ticks:
            y = Y(v)
            p.setPen(QPen(QColor(c["border"]), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(c["muted"]))
            p.drawText(QRectF(26, y - 8, self._LEFT - 32, 16),
                       Qt.AlignmentFlag.AlignRight
                       | Qt.AlignmentFlag.AlignVCenter, fmt(v))
        xs = [x for x, _, _ in self.points]
        for v in (min(xs), max(xs)):
            p.drawText(QRectF(X(v) - 50, plot.bottom() + 2, 100, 16),
                       Qt.AlignmentFlag.AlignHCenter, fmt(v))
        p.drawText(QRectF(plot.left(), plot.bottom() + 20, plot.width(), 18),
                   Qt.AlignmentFlag.AlignHCenter, self.x_label)
        p.save()
        p.translate(12, plot.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-plot.height() / 2, -8, plot.height(), 16),
                   Qt.AlignmentFlag.AlignHCenter, self.y_label)
        p.restore()
        if self.current_x is not None:
            x = X(self.current_x)
            p.setPen(QPen(QColor(c["muted"]), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            right = x + 40 > plot.right()     # no room: label on the left
            p.drawText(QRectF(x - 44 if right else x + 4, plot.top() + 2,
                              40, 16),
                       Qt.AlignmentFlag.AlignRight if right
                       else Qt.AlignmentFlag.AlignLeft, "now")

    def _draw_series(self, p, c, plot, X, Y, lows, highs):  # noqa: N803
        accent = QColor(c["accent"])
        xs = [x for x, _, _ in self.points]
        if len(self.points) > 1:
            band = QPainterPath()
            band.moveTo(X(xs[0]), Y(highs[0]))
            for x, h in zip(xs[1:], highs[1:]):
                band.lineTo(X(x), Y(h))
            for x, lo in zip(reversed(xs), reversed(lows)):
                band.lineTo(X(x), Y(lo))
            band.closeSubpath()
            fill = QColor(accent)
            fill.setAlpha(46)
            p.fillPath(band, fill)
            line = QPainterPath()
            line.moveTo(X(xs[0]), Y(self.points[0][1]))
            for x, m, _ in self.points[1:]:
                line.lineTo(X(x), Y(m))
            p.setPen(QPen(accent, 2))
            p.drawPath(line)
        p.setPen(QPen(accent, 2))
        p.setBrush(QColor(c["surface"]))
        for x, m, _ in self.points:
            p.drawEllipse(QPointF(X(x), Y(m)), 3.5, 3.5)


class _NumberBox(QDoubleSpinBox):
    """A spin box that shows 2,500 rather than 2,500.000000."""

    def textFromValue(self, v: float) -> str:             # noqa: N802
        text = f"{v:,.{self.decimals()}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text


class SweepDialog(_BatchDialog):
    TITLE = "Sweep"

    def __init__(self, win):
        super().__init__(win)
        self._choices = element_choices(win._rbd) if win._rbd else []
        self._element = QComboBox()
        self._element.setMinimumContentsLength(26)
        self._field = QComboBox()
        self._field.setMinimumContentsLength(26)
        for label, _ in self._choices:
            self._element.addItem(label)
        self._now = QLabel()
        self._now.setWordWrap(True)
        self._from = _NumberBox()
        self._to = _NumberBox()
        for sp in (self._from, self._to):
            sp.setDecimals(6)
            sp.setRange(-1e12, 1e12)
            sp.setGroupSeparatorShown(True)
            sp.valueChanged.connect(self._update_plan)
        self._points = QSpinBox()
        self._points.setRange(2, 101)
        self._points.setValue(5)
        self._points.valueChanged.connect(self._update_plan)
        self._metric = QComboBox()
        for key, label, _ in METRICS:
            self._metric.addItem(label, key)
        self._metric.currentIndexChanged.connect(self._show_results)
        self._layout(self._build_form(), self._build_results())
        self._element.currentIndexChanged.connect(self._fill_fields)
        self._field.currentIndexChanged.connect(self._fill_range)
        if not self._choices:
            self._now.setText("This model has no rates, block parameters or "
                              "k-of-n nodes to sweep.")
            self._run_btn.setEnabled(False)
        else:
            self._fill_fields()

    def _build_form(self) -> QFormLayout:
        form = QFormLayout()
        form.addRow("Change:", self._element)
        form.addRow("Number:", self._field)
        form.addRow("", self._now)
        rng = QHBoxLayout()
        rng.addWidget(self._from, 1)
        rng.addWidget(QLabel("to"))
        rng.addWidget(self._to, 1)
        rng.addWidget(QLabel("in"))
        rng.addWidget(self._points)
        rng.addWidget(QLabel("values"))
        form.addRow("From:", rng)
        form.addRow("Trials each:", self._trials)
        return form

    def _build_results(self) -> QSplitter:
        self._table = QTableWidget(0, 1 + len(_TABLE_METRICS))
        self._table.setHorizontalHeaderLabels(
            ["Value"] + [_LABEL[m] for m in _TABLE_METRICS])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._chart = SweepChart()
        chart_box = QWidget()
        cb = QVBoxLayout(chart_box)
        cb.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(QLabel("Plot:"))
        top.addWidget(self._metric)
        top.addStretch(1)
        cb.addLayout(top)
        cb.addWidget(self._chart, 1)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._table)
        split.addWidget(chart_box)
        split.setSizes([430, 390])
        return split

    def _layout(self, form, split) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        intro = QLabel("Run the model at a range of values for one number, "
                       "and see how the results move.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(self._plan)
        lay.addWidget(split, 1)
        lay.addWidget(self._progress)
        lay.addLayout(self._buttons)
        self.resize(820, 600)

    # -- inputs -----------------------------------------------------------
    def _target(self) -> SweepTarget | None:
        i, j = self._element.currentIndex(), self._field.currentIndex()
        if i < 0 or j < 0 or i >= len(self._choices):
            return None
        targets = self._choices[i][1]
        return targets[j] if j < len(targets) else None

    def select(self, element_label_prefix: str, field_prefix: str = "") -> bool:
        """Pick an element (and field) by label; for tests and callers that
        open the sweep on something already chosen."""
        for i, (label, targets) in enumerate(self._choices):
            if label.split("  ", 1)[-1] == element_label_prefix or \
                    label.startswith(element_label_prefix):
                self._element.setCurrentIndex(i)
                for j, t in enumerate(targets):
                    if t.label.startswith(field_prefix):
                        self._field.setCurrentIndex(j)
                        return True
        return False

    def _fill_fields(self) -> None:
        self._field.blockSignals(True)
        self._field.clear()
        i = self._element.currentIndex()
        if 0 <= i < len(self._choices):
            for t in self._choices[i][1]:
                self._field.addItem(t.label)
        self._field.blockSignals(False)
        self._fill_range()

    def _fill_range(self) -> None:
        t = self._target()
        if t is None:
            return
        dec = 0 if t.integer else 6
        for sp in (self._from, self._to):
            sp.blockSignals(True)
            sp.setDecimals(dec)
            sp.setRange(t.lo if t.lo is not None else -1e12,
                        t.hi if t.hi is not None else 1e12)
        if t.integer:
            lo = t.lo if t.lo is not None else max(0.0, t.current - 2)
            hi = t.hi if t.hi is not None else t.current + 2
            self._from.setValue(lo)
            self._to.setValue(hi)
            self._points.setValue(int(hi - lo) + 1)
        else:
            cur = t.current
            self._from.setValue(cur * 0.5 if cur else 0.0)
            self._to.setValue(cur * 1.5 if cur else 1.0)
            self._points.setValue(5)
        for sp in (self._from, self._to):
            sp.blockSignals(False)
        now = f"Now {fmt(t.current)}."
        if t.note:
            now += f"  {t.note[0].upper()}{t.note[1:]}."
        self._now.setText(now)
        self._update_plan()

    def _values(self) -> list[str]:
        t = self._target()
        if t is None:
            return []
        return sweep_values(self._from.value(), self._to.value(),
                            self._points.value(), t.integer)

    def _update_plan(self) -> None:
        vals = self._values()
        if vals:
            self._plan.setText(self._plan_text(len(vals)))

    def _set_inputs_enabled(self, on: bool) -> None:
        for w in (self._element, self._field, self._from, self._to,
                  self._points, self._trials):
            w.setEnabled(on)

    def _variants(self):
        t = self._target()
        vals = self._values()
        if t is None or not vals:
            return "Choose a number to sweep."
        bad = invalid_value(self._win._rbd, t, vals)
        if bad:
            return bad
        self._swept = (t, vals)
        out = []
        for v in vals:
            out.append((v, _model_copy(self._win), [f"{t.path}={v}"]))
        return out

    # -- results ----------------------------------------------------------
    def _show_results(self) -> None:
        job = self._job
        rows = job.results if job is not None else []
        self._table.setRowCount(len(rows))
        best = self._best_rows(rows)
        for r, entry in enumerate(rows):
            items = [entry["label"]] + [
                ("error" if entry.get("error") else fmt(_mean(entry, m)))
                for m in _TABLE_METRICS]
            for c, text in enumerate(items):
                it = QTableWidgetItem(text)
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
                if entry.get("error"):
                    it.setToolTip(entry["error"])
                elif entry.get("stopped"):
                    it.setToolTip("Stopped part-way: fewer trials than asked")
                if c > 0 and r in best.get(_TABLE_METRICS[c - 1], ()):
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self._table.setItem(r, c, it)
        metric = self._metric.currentData() or "Availability"
        points = []
        for entry in rows:
            m = _mean(entry, metric)
            if m is None:
                continue
            try:
                points.append((float(entry["label"]), m, _sem(entry, metric) or 0))
            except (TypeError, ValueError):
                continue
        t = getattr(self, "_swept", (self._target(), None))[0]
        self._chart.set_data(points, self._axis_label(t),
                             _LABEL.get(metric, metric),
                             t.current if t else None)

    def _axis_label(self, t: SweepTarget | None) -> str:
        if t is None:
            return ""
        for label, targets in self._choices:
            if t in targets:
                name = label.split("  ", 1)[-1]
                return name if t.label == "Value" else f"{name} — {t.label}"
        return t.label

    @staticmethod
    def _best_rows(rows) -> dict[str, set[int]]:
        return {m: best_indices([None if e.get("error") else _mean(e, m)
                                 for e in rows], _HIGHER_BETTER[m])
                for m in _TABLE_METRICS}

    def _csv(self):
        t = getattr(self, "_swept", (self._target(), None))[0]
        job = self._job
        headers = [t.path if t else "value"] + [k for k, _, _ in METRICS]
        rows = []
        for e in (job.results if job else []):
            rows.append([e["label"]] + [
                "" if _mean(e, k) is None else repr(_mean(e, k))
                for k, _, _ in METRICS])
        return headers, rows


# --------------------------------------------------------------------------
# Compare


class CompareDialog(_BatchDialog):
    TITLE = "Compare"

    def __init__(self, win):
        super().__init__(win)
        #: The other models: file paths, or models open in other windows
        #: (`open_models.OpenModel`, run as they are now).
        self._files: list = []
        self._list = QListWidget()
        self._list.setMaximumHeight(120)
        here = os.path.basename(win._current_file or "") or "Untitled"
        first = QListWidgetItem(f"This model — {here} (as it is now, "
                                f"including unsaved edits)")
        first.setFlags(first.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self._list.addItem(first)
        add = self._add = QPushButton("Add models")
        # A menu: a file, or a model open in another window. Built when
        # it opens, so it lists the windows open then.
        from PySide6.QtWidgets import QMenu
        self._add_menu = QMenu(add)
        self._add_menu.aboutToShow.connect(self._fill_add_menu)
        add.setMenu(self._add_menu)
        self._remove = QPushButton("Remove")
        self._remove.clicked.connect(self._remove_selected)
        self._remove.setEnabled(False)
        # Results differ: what is different in the models themselves?
        self._whats_diff = QPushButton("What's different?")
        self._whats_diff.setToolTip(
            "Compare the selected model's parameters and wiring with this "
            "one (Model > Differences)")
        self._whats_diff.setEnabled(False)
        self._whats_diff.clicked.connect(self._open_differences)
        self._list.itemSelectionChanged.connect(self._on_selection)
        side = QVBoxLayout()
        side.addWidget(add)
        side.addWidget(self._remove)
        side.addWidget(self._whats_diff)
        side.addStretch(1)
        models = QHBoxLayout()
        models.addWidget(self._list, 1)
        models.addLayout(side)

        form = QFormLayout()
        form.addRow("Trials each:", self._trials)

        self._table = QTableWidget(0, 1)
        self._table.verticalHeader().setVisible(True)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # Columns as wide as their names, scrolling sideways when there are
        # many: stretched to fit, nine models read "ter_Fixed.", "rone1.rbd".
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)

        lay = QVBoxLayout(self)
        intro = QLabel("Run this model beside other versions of it — a "
                       "design option, last year's model — with the same "
                       "number of trials, and see which does best. The best "
                       "value in each row is in bold.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        lay.addLayout(models)
        lay.addLayout(form)
        lay.addWidget(self._plan)
        lay.addWidget(self._table, 1)
        lay.addWidget(self._progress)
        lay.addLayout(self._buttons)
        self.resize(760, 560)
        self._update_plan()

    def add_paths(self, paths: list[str]) -> None:
        here = os.path.abspath(self._win._current_file or "")
        refused = ""
        for p in paths:
            if p and os.path.abspath(p) == here:
                # Already the first column ("This model", with any unsaved
                # edits); its saved copy would only repeat it.
                refused = (f"{os.path.basename(p)} is this model, already "
                           f"in the comparison.")
                continue
            if p and p not in self._files:
                self._files.append(p)
                it = QListWidgetItem(os.path.basename(p))
                it.setToolTip(p)
                self._list.addItem(it)
        self._relabel()
        if refused:
            self._plan.setText(refused + " " + self._plan.text())

    def _fill_add_menu(self) -> None:
        from raptor.gui import open_models
        menu = self._add_menu
        menu.clear()
        menu.addAction("From a file…", self._add_files)
        others = open_models.others(self._win)
        if others:
            menu.addSeparator()
            for om in others:
                menu.addAction(om.description,
                               lambda m=om: self.add_open_models([m]))
            if len(others) > 1:
                menu.addAction("All open models",
                               lambda: self.add_open_models(others))

    def add_open_models(self, models: list) -> None:
        """Add models open in other windows, as they are now. One whose
        file is already in the list replaces nothing and is skipped."""
        for om in models:
            if any(e is om or (not isinstance(e, str) and e.window is om.window)
                   or (isinstance(e, str) and om.path and
                       os.path.abspath(e) == os.path.abspath(om.path))
                   for e in self._files):
                continue
            self._files.append(om)
            it = QListWidgetItem("")
            it.setToolTip(om.path or om.label)
            self._list.addItem(it)
        self._relabel()

    def _relabel(self) -> None:
        for i, label in enumerate(model_labels(self._files), start=1):
            self._list.item(i).setText(label)
        self._update_plan()

    def _add_files(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from raptor.gui.ui.filedialogs import theme_file_dialog
        start = os.path.dirname(self._win._current_file or "") or os.getcwd()
        dlg = QFileDialog(self, "Add models to compare", start,
                          "Raptor models (*.rbd *.rbds)")
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        theme_file_dialog(dlg)
        dlg.setFileMode(QFileDialog.FileMode.ExistingFiles)
        if dlg.exec():
            self.add_paths(dlg.selectedFiles())

    def _on_selection(self) -> None:
        picked = bool(self._list.selectedItems())
        self._remove.setEnabled(picked and self._add.isEnabled())
        self._whats_diff.setEnabled(picked)

    def _open_differences(self) -> None:
        rows = [self._list.row(it) for it in self._list.selectedItems()]
        if rows and rows[0] > 0:
            entry = self._files[rows[0] - 1]
            self._win._open_diff_dialog(entry if isinstance(entry, str)
                                        else None, open_model=None
                                        if isinstance(entry, str) else entry)

    def _remove_selected(self) -> None:
        for it in self._list.selectedItems():
            row = self._list.row(it)
            if row > 0:
                self._list.takeItem(row)
                del self._files[row - 1]
        self._update_plan()

    def _update_plan(self) -> None:
        n = 1 + len(self._files)
        if n < 2:
            self._plan.setText("Add at least one more model to compare "
                               "with this one.")
            return
        self._plan.setText(self._plan_text(n))

    def _set_inputs_enabled(self, on: bool) -> None:
        for w in (self._list, self._trials, self._add):
            w.setEnabled(on)
        self._remove.setEnabled(on and bool(self._list.selectedItems()))

    def _variants(self):
        if not self._files:
            return "Add at least one more model to compare with this one."
        from raptor.api.workspace import Workspace
        out = [("This model", _model_copy(self._win), [])]
        for p, label in zip(self._files, model_labels(self._files)):
            if not isinstance(p, str):           # open in another window
                try:
                    out.append((label, p.model(), []))
                except RuntimeError:
                    return (f"{p.label} was closed. Remove it from the list "
                            f"to compare the others.")
                continue
            try:
                rbd = Workspace._load_any(p)
            except Exception as exc:                      # noqa: BLE001
                return (f"Couldn't open {os.path.basename(p)}: it is not a "
                        f"model this program can read ({type(exc).__name__}). "
                        f"Remove it from the list to compare the others.")
            out.append((label, rbd, []))
        return out

    def _show_results(self) -> None:
        job = self._job
        entries = job.results if job is not None else []
        labels = ["This model"] + model_labels(self._files)
        self._table.setColumnCount(len(labels))
        self._table.setHorizontalHeaderLabels(labels)
        self._table.setRowCount(len(METRICS))
        self._table.setVerticalHeaderLabels([lbl for _, lbl, _ in METRICS])
        for r, (key, _, higher) in enumerate(METRICS):
            vals = [None if e.get("error") else _mean(e, key)
                    for e in entries]
            best = best_indices(vals, higher)
            base = vals[0] if vals else None
            for c in range(len(labels)):
                if c >= len(entries):
                    it = QTableWidgetItem("…" if job and not job.done else "")
                elif entries[c].get("error"):
                    it = QTableWidgetItem("error")
                    it.setToolTip(entries[c]["error"])
                else:
                    v = vals[c]
                    it = QTableWidgetItem(fmt(v))
                    if c > 0 and v is not None and base:
                        it.setToolTip(f"{(v - base) / abs(base):+.2%} "
                                      f"against this model")
                    if c in best:
                        f = it.font()
                        f.setBold(True)
                        it.setFont(f)
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(r, c, it)

    def _csv(self):
        job = self._job
        entries = job.results if job else []
        headers = ["metric"] + [e["label"] for e in entries]
        rows = [[key] + ["" if _mean(e, key) is None else repr(_mean(e, key))
                         for e in entries] for key, _, _ in METRICS]
        return headers, rows
