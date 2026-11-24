"""The page a Charts-list group heading opens: one card per chart in it.

Each card is a small live picture of the chart (drawn from this run, by the
same painter as the full chart), its title and one line saying what it
answers.  Click it, or press Enter or Space, to open the chart.  A chart the
run cannot draw is a greyed card that says why; it opens the same
explanation the full view gives.

The cards reflow to the width (one column in a narrow dock).  The pictures
are drawn lazily, one card per event-loop turn, so the page appears at once
even when a chart is slow to draw; a picture already drawn for the same
result is reused (the page owns that cache).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from raptor.gui import i18n
from raptor.gui.resources.design_tokens import RADIUS, SPACING, active_colour

#: the thumbnail's shape (height over width)
ASPECT = 0.625
#: the narrowest a card gets, in line heights (so it follows the UI scale)
MIN_CARD_LINES = 17
#: the most columns, so a very wide window does not stretch cards thin
MAX_COLUMNS = 4


@dataclass
class CardData:
    key: str
    title: str          # already translated
    description: str    # already translated
    missing: str = ""   # the reason it cannot be drawn, translated ("" = fine)


class _Thumb(QWidget):
    """The chart's picture, scaled smoothly to the card's width."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._text = ""
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_pixmap(self, pixmap: QPixmap | None, text: str = "") -> None:
        self._pixmap = pixmap
        self._text = text
        self.update()

    def has_picture(self) -> bool:
        return self._pixmap is not None

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt name
        colour = active_colour()
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing
                               | QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour["canvas"]))
        painter.drawRoundedRect(rect, 4, 4)
        if self._pixmap is not None:
            painter.drawPixmap(self.rect(), self._pixmap)
        elif self._text:
            painter.setPen(QColor(colour["muted"]))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()


class ChartCard(QFrame):
    """One chart: picture, title, what it answers.  A focusable button."""

    activated = Signal(str)

    def __init__(self, data: CardData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.data = data
        self._hover = False
        self.setObjectName("ChartCard")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        pad = SPACING["sm"]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(pad, pad, pad, pad)
        lay.setSpacing(SPACING["xs"])
        self.thumb = _Thumb()
        lay.addWidget(self.thumb)
        self.title = QLabel(data.title)
        self.title.setObjectName("ChartCardTitle")
        bold = QFont(self.font())
        bold.setBold(True)
        self.title.setFont(bold)
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        self.text = QLabel(data.missing or data.description)
        self.text.setObjectName("ChartCardText")
        self.text.setWordWrap(True)
        lay.addWidget(self.text)
        lay.addStretch(1)
        for label in (self.title, self.text):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            label.setSizePolicy(QSizePolicy.Policy.Preferred,
                                QSizePolicy.Policy.Minimum)
        self.style_labels()
        reason = f"{data.title}. {data.missing or data.description}"
        self.setAccessibleName(data.title)
        self.setAccessibleDescription(data.missing or data.description)
        self.setToolTip(reason if data.missing else data.title)
        if data.missing:
            self.thumb.set_pixmap(None, i18n.tr("Not available"))

    @property
    def available(self) -> bool:
        return not self.data.missing

    def style_labels(self) -> None:
        colour = active_colour()
        text = colour["muted"] if self.data.missing else colour["text"]
        self.title.setStyleSheet(f"color: {text}; background: transparent;")
        self.text.setStyleSheet(
            f"color: {colour['muted']}; background: transparent;"
            + ("font-style: italic;" if self.data.missing else ""))

    def height_for(self, width: int) -> int:
        lay = self.layout()
        assert lay is not None
        return lay.totalHeightForWidth(width)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt name
        colour = active_colour()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        lit = self._hover or self.hasFocus()
        painter.setBrush(QColor(colour["surface"]))
        painter.setPen(QPen(QColor(colour["accent"] if lit else colour["border"]),
                            2 if self.hasFocus() else 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1),
                                RADIUS["card"], RADIUS["card"])
        painter.end()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt name
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt name
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt name
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt name
        self.update()
        super().focusOutEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt name
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.activated.emit(self.data.key)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt name
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit(self.data.key)
            event.accept()
            return
        super().keyPressEvent(event)


class ChartGroupOverview(QScrollArea):
    """The heading, a line about the group, and a grid of `ChartCard`s."""

    #: a card was chosen: the chart's key
    chart_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChartGroupOverview")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAccessibleName("Charts in this group")
        body = QWidget()
        body.setObjectName("ChartGroupOverviewBody")
        self._body_lay = QVBoxLayout(body)
        self._body_lay.setContentsMargins(0, SPACING["xs"], 0, SPACING["sm"])
        self._body_lay.setSpacing(SPACING["xs"])
        self.heading = QLabel("")
        self.heading.setObjectName("ChartGroupTitle")
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() + 4)
        font.setBold(True)
        self.heading.setFont(font)
        self.heading.setWordWrap(True)
        self.blurb = QLabel("")
        self.blurb.setObjectName("ChartGroupBlurb")
        self.blurb.setWordWrap(True)
        self._body_lay.addWidget(self.heading)
        self._body_lay.addWidget(self.blurb)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, SPACING["sm"], 0, 0)
        self._grid.setSpacing(SPACING["md"])
        self._body_lay.addWidget(self._grid_host)
        self._body_lay.addStretch(1)
        self.setWidget(body)
        self.cards: list[ChartCard] = []
        self.group = ""
        self._columns = 0
        self._card_width = 0
        self._cached: Callable[[str], QPixmap | None] = lambda _key: None
        self._render: Callable[[str], QPixmap | None] = lambda _key: None
        self._queue: list[ChartCard] = []
        self._generation = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._pump)
        self.restyle()

    # -- content ----------------------------------------------------------

    def set_providers(self, cached: Callable[[str], QPixmap | None],
                      render: Callable[[str], QPixmap | None]) -> None:
        """`cached(key)`: a picture already drawn for this result, or None;
        `render(key)`: draw it now (None when the chart has no picture)."""
        self._cached = cached
        self._render = render

    def show_group(self, group: str, title: str, blurb: str,
                   cards: list[CardData]) -> None:
        """Replace the page's content; the pictures fill in one per turn."""
        self._generation += 1
        self._timer.stop()
        self._queue = []
        self.group = group
        self.heading.setText(title)
        self.blurb.setText(blurb)
        self.blurb.setVisible(bool(blurb))
        for card in self.cards:
            self._grid.removeWidget(card)
            card.hide()
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        previous: QWidget = self.heading
        for data in cards:
            card = ChartCard(data, self._grid_host)
            card.activated.connect(self.chart_chosen)
            self.cards.append(card)
            self.setTabOrder(previous, card)
            previous = card
            if data.missing:
                continue
            picture = self._cached(data.key)
            if picture is not None:
                card.thumb.set_pixmap(picture)
            else:
                card.thumb.set_pixmap(None, "…")
                self._queue.append(card)
        self._columns = 0
        self._reflow()
        if self._queue:
            self._timer.start()

    def pending(self) -> int:
        """Cards still waiting for their picture."""
        return len(self._queue)

    def flush(self) -> None:
        """Draw every waiting picture now (tests, screenshots)."""
        while self._queue:
            self._pump()
        self._timer.stop()

    def _pump(self) -> None:
        generation = self._generation
        if not self._queue:
            return
        card = self._queue.pop(0)
        picture = self._render(card.data.key)
        if generation != self._generation:
            return
        card.thumb.set_pixmap(picture, "" if picture is not None
                              else i18n.tr("No preview"))
        if self._queue:
            self._timer.start()

    # -- look -------------------------------------------------------------

    def restyle(self) -> None:
        colour = active_colour()
        self.heading.setStyleSheet(f"color: {colour['text']};")
        self.blurb.setStyleSheet(f"color: {colour['muted']};")
        for card in self.cards:
            card.style_labels()
            card.update()

    # -- layout -----------------------------------------------------------

    def _min_card(self) -> int:
        return self.fontMetrics().height() * MIN_CARD_LINES

    def _reflow(self) -> None:
        width = max(1, self.viewport().width())
        gap = self._grid.spacing()
        cols = max(1, min(MAX_COLUMNS, (width + gap) // (self._min_card() + gap)))
        card_w = max(1, (width - gap * (cols - 1)) // cols)
        if (cols, card_w) == (self._columns, self._card_width) and self._grid.count():
            return
        self._columns, self._card_width = cols, card_w
        for card in self.cards:
            self._grid.removeWidget(card)
        inner = card_w - 2 * SPACING["sm"]
        heights = []
        for card in self.cards:
            card.thumb.setFixedHeight(max(1, int(inner * ASPECT)))
            heights.append(card.height_for(card_w))
        for index, card in enumerate(self.cards):
            row = heights[index - index % cols: index - index % cols + cols]
            card.setFixedSize(card_w, max(row))
            self._grid.addWidget(card, index // cols, index % cols,
                                 Qt.AlignmentFlag.AlignTop
                                 | Qt.AlignmentFlag.AlignLeft)
            card.show()
        # the columns past the last card take the slack
        for col in range(cols):
            self._grid.setColumnStretch(col, 0)
        self._grid.setColumnStretch(cols, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt name
        super().resizeEvent(event)
        self._reflow()
