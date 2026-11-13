"""The Drivers page: what causes the system's downtime.

Three pictures of one attribution (`raptor.api.insight.attribution`):

* a single bar splitting all downtime by cause;
* the elements that started the outages, largest first, each bar split by
  cause, with the sampling error of its share;
* the *outage anatomy*: every outage as a path from what started it to what
  kept the system down, drawn as an icicle whose heights are shares of
  downtime.

Painted with QPainter like the other plots: no plotting dependency.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from raptor.api.insight.anatomy import Cell, icicle
from raptor.api.insight.attribution import CAUSE_LABELS, CAUSES
from raptor.api.insight.drivers import caption, top
from raptor.gui.resources.design_tokens import active_colour
from raptor.gui.ui.chart_theme import cause_brush, cause_colour, has_pattern

EMPTY_TEXT = ("Run the model with the Event Log switched on (Run panel ▸ Recording) to see "
              "what causes its downtime: which elements start the outages, and whether "
              "the time goes on repair, waiting for spares, waiting for a crew or "
              "planned maintenance.")
NO_OUTAGE_TEXT = "No system outage occurred in {runs:,} runs, so there is no downtime to explain."
TOP_ELEMENTS = 8
ROW_H = 22
ANATOMY_H = 260
ANATOMY_DEPTH = 4


def _pct(x: float | None, digits: int = 3) -> str:
    return "" if x is None else f"{100 * x:.{digits}g} %"


def _fill(p: QPainter, rect: QRectF, cause: str) -> None:
    p.fillRect(rect, cause_colour(cause))
    if has_pattern(cause) and rect.width() > 3 and rect.height() > 3:
        p.fillRect(rect, cause_brush(cause))


class DriversChart(QWidget):
    """The cause bar, the ranked elements and the outage anatomy."""

    element_clicked = Signal(str, int, str)       # kind, id, name

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._attribution: dict[str, Any] | None = None
        self._rows: list[dict[str, Any]] = []
        self._cells: list[Cell] = []
        self._hit: list[tuple[QRectF, dict[str, Any]]] = []
        self._cell_hit: list[tuple[QRectF, Cell]] = []
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_attribution(self, attribution: dict[str, Any] | None) -> None:
        self._attribution = attribution
        self._rows = top(attribution, TOP_ELEMENTS) if attribution else []
        self._cells = (icicle(attribution.get("sequences") or [], ANATOMY_DEPTH, 0.004)
                       if attribution else [])
        self.setFixedHeight(self.sizeHint().height())
        self.update()

    def rows(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def cells(self) -> list[Cell]:
        return list(self._cells)

    def sizeHint(self):  # noqa: N802 - Qt name
        from PySide6.QtCore import QSize
        legend_rows = 2
        h = 26 + 18 + legend_rows * 18 + 16 + 22 + len(self._rows) * ROW_H + 16
        if self._cells:
            h += 26 + ANATOMY_H + 8
        return QSize(640, h)

    # ---- painting --------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt name
        if not self._attribution:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self._hit, self._cell_hit = [], []
        self._x0, self._w = 8.0, self.width() - 16.0
        colour = active_colour()
        self._text, self._muted = QColor(colour["text"]), QColor(colour["muted"])
        self._border, self._surface = QColor(colour["border"]), QColor(colour["surface"])
        self._fm = QFontMetrics(self.font())
        y = self._paint_causes(p, 6.0)
        y = self._paint_rows(p, y)
        if self._cells:
            self._paint_anatomy(p, y)
        p.end()

    def _heading(self, p: QPainter, y: float, label: str) -> float:
        bold = QFont(self.font())
        bold.setBold(True)
        p.setFont(bold)
        p.setPen(self._text)
        p.drawText(QRectF(self._x0, y, self._w, 18), Qt.AlignmentFlag.AlignLeft, label)
        p.setFont(self.font())
        return y + 22

    def _paint_causes(self, p: QPainter, y: float) -> float:
        """One bar: all downtime split by cause, with its legend."""
        x0, w = self._x0, self._w
        y = self._heading(p, y, "Where the downtime goes")
        causes = (self._attribution or {}).get("causes") or {}
        shown = [c for c in CAUSES if causes.get(c, 0.0) > 0]
        x = x0
        for cause in shown:
            _fill(p, QRectF(x, y, w * causes[cause], 16), cause)
            x += w * causes[cause]
        p.setPen(QPen(self._border))
        p.drawRect(QRectF(x0, y, w, 16))
        y += 22
        col_w = max(170.0, w / 3)
        per_row = max(1, int(w // col_w))
        for i, cause in enumerate(shown):
            lx = x0 + (i % per_row) * col_w
            ly = y + (i // per_row) * 18
            _fill(p, QRectF(lx, ly + 3, 12, 12), cause)
            p.setPen(self._muted)
            p.drawText(QRectF(lx + 17, ly, col_w - 20, 18), Qt.AlignmentFlag.AlignLeft,
                       f"{CAUSE_LABELS[cause]} {_pct(causes[cause], 2)}")
        return y + 18 * (1 + (len(shown) - 1) // per_row if shown else 1) + 14

    def _paint_rows(self, p: QPainter, y: float) -> float:
        """The elements that start the outages, each bar split by cause."""
        x0, w, fm = self._x0, self._w, self._fm
        y = self._heading(p, y, "What starts the outages (share of system downtime)")
        name_w = min(170.0, max((fm.horizontalAdvance(r["name"]) for r in self._rows),
                                default=60) + 10)
        value_w = 110.0
        bar_w = max(40.0, w - name_w - value_w)
        biggest = max((r["dtci"]["mean"] or 0.0 for r in self._rows), default=1.0) or 1.0
        centre = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        for row in self._rows:
            share = row["dtci"]["mean"] or 0.0
            self._hit.append((QRectF(x0, y, w, ROW_H), row))
            p.setPen(self._text)
            p.drawText(QRectF(x0, y, name_w - 6, ROW_H - 4), centre,
                       fm.elidedText(row["name"], Qt.TextElideMode.ElideRight, int(name_w - 6)))
            bx, full = x0 + name_w, bar_w * share / biggest
            for cause in CAUSES:
                part = row["causes"].get(cause, 0.0)
                if part > 0:
                    _fill(p, QRectF(bx, y + 3, full * part, ROW_H - 8), cause)
                    bx += full * part
            sem = row["dtci"]["sem"]
            if sem:
                p.setPen(QPen(self._text, 1))
                lo = int(x0 + name_w + bar_w * max(0.0, share - 2 * sem) / biggest)
                hi = int(x0 + name_w + bar_w * min(biggest, share + 2 * sem) / biggest)
                mid = int(y + ROW_H / 2 - 1)
                p.drawLine(lo, mid, hi, mid)
                p.drawLine(lo, mid - 3, lo, mid + 3)
                p.drawLine(hi, mid - 3, hi, mid + 3)
            p.setPen(self._text)
            p.drawText(QRectF(x0 + name_w + bar_w + 6, y, value_w - 6, ROW_H - 4), centre,
                       _pct(share) + (f"  ± {_pct(sem, 2)}" if sem else ""))
            y += ROW_H
        return y + 16

    def _paint_anatomy(self, p: QPainter, y: float) -> None:
        """Every outage as a path: started by, then, then."""
        x0, w, fm = self._x0, self._w, self._fm
        y = self._heading(
            p, y, "What the outages look like (started by → then → then; height = share of downtime)")
        col = w / (max(c.depth for c in self._cells) + 1)
        centre = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        for cell in self._cells:
            rect = QRectF(x0 + cell.depth * col, y + cell.top * ANATOMY_H,
                          col - 3, max(1.0, cell.height * ANATOMY_H - 1))
            _fill(p, rect, cell.cause)
            self._cell_hit.append((rect, cell))
            if rect.height() < 15:
                continue
            label = f"{cell.element} · {CAUSE_LABELS[cell.cause].lower()} · {_pct(cell.height, 2)}"
            plate = QRectF(rect.x() + 3, rect.y() + 1,
                           min(rect.width() - 6, fm.horizontalAdvance(label) + 8), 15)
            p.fillRect(plate, self._surface)
            p.setPen(self._text)
            p.drawText(plate.adjusted(4, 0, 0, 0), centre,
                       fm.elidedText(label, Qt.TextElideMode.ElideRight, int(plate.width() - 6)))

    # ---- interaction -----------------------------------------------------
    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt name
        pos = event.position()
        for rect, row in self._hit:
            if rect.contains(pos):
                parts = ", ".join(f"{CAUSE_LABELS[c].lower()} {_pct(v, 2)}"
                                  for c, v in sorted(row["causes"].items(), key=lambda kv: -kv[1]))
                self.setToolTip(
                    f"{row['name']}\nShare of system downtime: {_pct(row['dtci']['mean'])}\n"
                    f"Share of outages started: {_pct(row['deci']['mean'])}\n"
                    f"Share of failures that failed the system: {_pct(row['fci']['mean'])}\n"
                    f"Its outages: {parts}\nClick to select it on the diagram")
                return
        for rect, cell in self._cell_hit:
            if rect.contains(pos):
                self.setToolTip(
                    f"{cell.element}: {CAUSE_LABELS[cell.cause].lower()}\n"
                    f"{_pct(cell.height)} of downtime passes through this step "
                    f"({cell.outages:,} outages)")
                return
        self.setToolTip("")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt name
        pos = event.position()
        for rect, row in self._hit:
            if rect.contains(pos):
                self.element_clicked.emit(row["kind"], int(row["id"]), row["name"])
                return


class DriversPage(QWidget):
    """The Results dock's Drivers tab."""

    element_clicked = Signal(str, int, str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.caption = QLabel(EMPTY_TEXT)
        self.caption.setWordWrap(True)
        self.caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.caption)
        self.chart = DriversChart()
        self.chart.element_clicked.connect(self.element_clicked)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.chart)
        lay.addWidget(scroll, 1)
        self.footer = QLabel("")
        self.footer.setObjectName("SectionNote")
        lay.addWidget(self.footer)
        self.set_attribution(None)

    def set_attribution(self, attribution: dict[str, Any] | None) -> None:
        self._attribution = attribution
        has_outages = bool(attribution and top(attribution, 1))
        if attribution is None:
            self.caption.setText(EMPTY_TEXT)
        elif not has_outages:
            self.caption.setText(NO_OUTAGE_TEXT.format(runs=attribution["runs"]))
        else:
            self.caption.setText(caption(attribution))
        self.chart.set_attribution(attribution if has_outages else None)
        self.chart.setVisible(has_outages)
        if has_outages and attribution is not None:
            units = attribution.get("units") or ""
            system = attribution["system"]
            self.footer.setText(
                f"{attribution['runs']:,} runs · system down {system['red_time']['mean']:,.4g} "
                f"{units} a run in {system['outages']['mean']:,.3g} outages · error bars are "
                f"± 2 standard errors".replace("  ", " "))
        else:
            self.footer.setText("")

    def attribution(self) -> dict[str, Any] | None:
        return self._attribution
