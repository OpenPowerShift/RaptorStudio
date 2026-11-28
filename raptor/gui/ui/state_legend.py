"""The canvas's state-colour legend: a compact strip along the bottom edge
of the canvas viewport while a Debug session or an animated run colours the
blocks by state.

The colours are NOT defined here. They are read from
`LiveOverlayMixin._LIVE_STATE_COLOURS` (itself derived from
`APPENDIX_D_STATE_COLOURS`), the table the live colouring paints from, so
the legend cannot drift from the canvas. Only the display order and the
short translatable labels live here; a state in the table that is missing
from `ORDER` is still listed (at the end) under its table key.

The widget is a child of the view's viewport, not a scene item: it does
not pan or zoom, and diagram/PDF/SVG export (which render the scene) never
include it.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from raptor.gui import i18n
from raptor.gui.resources.design_tokens import COLOUR
from raptor.gui.ui.diagram_live import LiveOverlayMixin

#: The order the states are commonly understood in: up states first, then
#: the down states. Keys are `_LIVE_STATE_COLOURS` keys.
ORDER = ("Running", "Standby", "Idle", "Repairing", "RepHold",
         "PM", "PMhold", "Done")

_MARGIN = 6      # gap between the legend and the viewport edge
_PAD = 6         # padding inside the strip
_SWATCH = 10
_GAP = 4         # swatch to label
_SPACING = 10    # entry to entry


def state_labels() -> dict[str, str]:
    """Short, translated label for each live block state."""
    return {
        "Running": i18n.tr("Running"),
        "Standby": i18n.tr("Standby"),
        "Idle": i18n.tr("Idle"),
        "Repairing": i18n.tr("Failed, in repair"),
        "RepHold": i18n.tr("Repair hold"),
        "PM": i18n.tr("Maintenance"),
        "PMhold": i18n.tr("Maintenance hold"),
        "Done": i18n.tr("Done"),
    }


def legend_entries() -> list[tuple[str, str, str]]:
    """(state key, label, colour) for every state in the live colour table."""
    table = LiveOverlayMixin._LIVE_STATE_COLOURS
    labels = state_labels()
    keys = [k for k in ORDER if k in table]
    keys += [k for k in table if k not in ORDER]
    return [(k, labels.get(k, k), table[k]) for k in keys]


class StateLegend(QWidget):
    """Single-row (wrapping only if the canvas is narrow) colour key."""

    def __init__(self, view):
        super().__init__(view.viewport())
        self._view = view
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setObjectName("stateLegend")
        self.entries: list[tuple[str, str, str]] = []
        self.refresh()
        self.hide()

    # -- content ---------------------------------------------------------
    def refresh(self) -> None:
        """Re-read the colour table and labels (language may have changed)."""
        self.entries = legend_entries()
        self.reposition()

    def _rows(self, avail: int) -> tuple[list[list[int]], list[int], int]:
        fm = QFontMetrics(self.font())
        widths = [_SWATCH + _GAP + fm.horizontalAdvance(lbl)
                  for _k, lbl, _c in self.entries]
        rows: list[list[int]] = [[]]
        used = 0
        for i, w in enumerate(widths):
            extra = w if not rows[-1] else _SPACING + w
            if rows[-1] and used + extra > avail:
                rows.append([])
                used = 0
                extra = w
            rows[-1].append(i)
            used += extra
        return rows, widths, fm.height()

    def reposition(self) -> None:
        """Bottom-right of the viewport, sized to its entries."""
        vp = self._view.viewport().rect()
        avail = max(40, vp.width() - 2 * _MARGIN - 2 * _PAD)
        rows, widths, fh = self._rows(avail)
        row_h = max(fh, _SWATCH) + 2
        inner_w = max((sum(widths[i] for i in r) + _SPACING * (len(r) - 1)
                       for r in rows), default=0)
        w = min(vp.width() - 2 * _MARGIN, inner_w + 2 * _PAD)
        h = row_h * len(rows) + 2 * _PAD - 2
        self.setGeometry(vp.right() - _MARGIN - w + 1,
                         vp.bottom() - _MARGIN - h + 1, w, h)
        self.update()

    # -- painting --------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(COLOUR["surface"])
        bg.setAlpha(215)
        p.setBrush(bg)
        p.setPen(QColor(COLOUR["border"]))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 4, 4)
        avail = max(40, self._view.viewport().width() - 2 * _MARGIN - 2 * _PAD)
        rows, widths, fh = self._rows(avail)
        row_h = max(fh, _SWATCH) + 2
        y = _PAD - 1
        text = QColor(COLOUR["text"])
        for r in rows:
            x = _PAD
            for i in r:
                _k, label, colour = self.entries[i]
                p.setPen(QColor(COLOUR["border"]))
                p.setBrush(QColor(colour))
                p.drawRoundedRect(QRect(x, y + (row_h - _SWATCH) // 2,
                                        _SWATCH, _SWATCH), 2, 2)
                p.setPen(text)
                p.drawText(QRect(x + _SWATCH + _GAP, y,
                                 widths[i] - _SWATCH - _GAP + 2, row_h),
                           Qt.AlignmentFlag.AlignVCenter
                           | Qt.AlignmentFlag.AlignLeft, label)
                x += widths[i] + _SPACING
            y += row_h
        p.end()
