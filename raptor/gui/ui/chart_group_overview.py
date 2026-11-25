"""The page a Charts-list group heading opens: one card per chart in it.

Each card is a small live picture of the chart (drawn from this run, by the
same painter as the full chart), its title and one line saying what it
answers.  Click it, or press Enter or Space, to open the chart.  A chart the
run cannot draw is a greyed card that says why; it opens the same
explanation the full view gives.

**All charts** shows every group on one page: a heading and a line per
group, its cards under it.

The cards reflow to the width (one column in a narrow dock).  The pictures
are drawn lazily, one card per event-loop turn, so the page appears at once
even when a chart is slow to draw; a picture already drawn for the same
result is reused (the page owns that cache).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication, QEvent, QRectF, Qt, QTimer, Signal
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
#: the narrowest a picture is drawn (logical px); a narrower card scales it
#: down a little, so its labels stay readable
MIN_RENDER = 200
#: pictures are drawn at multiples of this width, so resizing does not redraw
#: them on every pixel
RENDER_STEP = 20
#: the most columns, so a very wide window does not stretch cards thin
MAX_COLUMNS = 4


@dataclass
class CardData:
    key: str
    title: str          # already translated
    description: str    # already translated
    missing: str = ""   # the reason it cannot be drawn, translated ("" = fine)


@dataclass
class Section:
    """One group on the All charts page: its heading, its line and cards."""
    title: str          # already translated
    blurb: str          # already translated
    cards: list[CardData]


class _SectionView:
    """A section's widgets: a heading and line (absent on a one-group page)
    and the grid its cards sit in."""

    def __init__(self, heading: QLabel | None, blurb: QLabel | None,
                 host: QWidget, grid: QGridLayout) -> None:
        self.heading = heading
        self.blurb = blurb
        self.host = host
        self.grid = grid
        self.cards: list[ChartCard] = []


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
        self._body_lay.addStretch(1)
        self.setWidget(body)
        self._body = body
        self._sections: list[_SectionView] = []
        self.cards: list[ChartCard] = []
        self.group = ""
        self._columns = 0
        self._card_width = 0
        self._render_width = MIN_RENDER
        self._cached: Callable[[str, int], QPixmap | None] = lambda _k, _w: None
        self._render: Callable[[str, int], QPixmap | None] = lambda _k, _w: None
        self._queue: list[ChartCard] = []
        self._generation = 0
        self._generation_shown = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._pump)
        # pictures in view are drawn first (All charts is a long page)
        self.verticalScrollBar().valueChanged.connect(self._visible_first)
        self.restyle()

    # -- content ----------------------------------------------------------

    def set_providers(self, cached: Callable[[str, int], QPixmap | None],
                      render: Callable[[str, int], QPixmap | None]) -> None:
        """`cached(key, width)`: a picture already drawn for this result at
        that logical width (its height follows `ASPECT`), or None;
        `render(key, width)`: draw it now (None when there is no picture)."""
        self._cached = cached
        self._render = render

    def show_group(self, group: str, title: str, blurb: str,
                   cards: list[CardData]) -> None:
        """Replace the page's content; the pictures fill in one per turn."""
        self.show_sections(group, title, blurb, [Section("", "", cards)])

    def show_sections(self, group: str, title: str, blurb: str,
                      sections: list[Section]) -> None:
        """One page of several groups, each under its own heading (a
        section with no title has none); the pictures fill in one per
        turn, those in view first."""
        self._generation += 1
        self._generation_shown = False
        self._timer.stop()
        self._queue = []
        self.group = group
        self.heading.setText(title)
        self.blurb.setText(blurb)
        self.blurb.setVisible(bool(blurb))
        self._clear_sections()
        previous: QWidget = self.heading
        at = self._body_lay.indexOf(self.blurb) + 1
        for section in sections:
            at, previous = self._add_section(section, at, previous)
        self.restyle()
        self._columns = 0
        self._reflow()
        self._generation_shown = True
        self._refresh_pictures()

    def _clear_sections(self) -> None:
        for view in self._sections:
            for card in view.cards:
                card.hide()
                card.setParent(None)
                card.deleteLater()
            for widget in (view.heading, view.blurb, view.host):
                if widget is not None:
                    self._body_lay.removeWidget(widget)
                    widget.hide()
                    widget.setParent(None)
                    widget.deleteLater()
        self._sections = []
        self.cards = []

    def _label(self, text: str, name: str, at: int, *,
               grow: float = 0.0) -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setWordWrap(True)
        if grow:
            font = QFont(self.font())
            font.setPointSizeF(font.pointSizeF() + grow)
            font.setBold(True)
            label.setFont(font)
            label.setContentsMargins(0, SPACING["md"], 0, 0)
        self._body_lay.insertWidget(at, label)
        return label

    def _add_section(self, section: Section, at: int,
                     previous: QWidget) -> tuple[int, QWidget]:
        """Lay out one section at `at` in the page; returns the next slot
        and the last widget in the tab order."""
        heading = blurb = None
        if section.title:
            heading = self._label(section.title, "ChartSectionTitle", at, grow=1.5)
            at += 1
            if section.blurb:
                blurb = self._label(section.blurb, "ChartSectionBlurb", at)
                at += 1
        host = QWidget(self._body)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, SPACING["sm"], 0, 0)
        grid.setSpacing(SPACING["md"])
        self._body_lay.insertWidget(at, host)
        view = _SectionView(heading, blurb, host, grid)
        for data in section.cards:
            card = ChartCard(data, host)
            card.activated.connect(self.chart_chosen)
            view.cards.append(card)
            self.cards.append(card)
            self.setTabOrder(previous, card)
            previous = card
        self._sections.append(view)
        return at + 1, previous

    def section_titles(self) -> list[str]:
        """The section headings shown (empty on a one-group page)."""
        return [view.heading.text() for view in self._sections
                if view.heading is not None]

    def _refresh_pictures(self) -> None:
        """Show the pictures held for the current width; queue the rest."""
        self._timer.stop()
        self._queue = []
        for card in self.cards:
            if card.data.missing:
                continue
            picture = self._cached(card.data.key, self._render_width)
            if picture is not None:
                card.thumb.set_pixmap(picture)
            else:
                if not card.thumb.has_picture():
                    card.thumb.set_pixmap(None, "\u2026")
                self._queue.append(card)
        self._visible_first()
        if self._queue:
            self._timer.start()

    def _visible_first(self, _value: int = 0) -> None:
        """Draw the cards in view before those further down."""
        if len(self._queue) < 2:
            return
        top = self.verticalScrollBar().value()
        bottom = top + self.viewport().height()

        def off_screen(card: ChartCard) -> bool:
            y = card.mapTo(self._body, card.rect().topLeft()).y()
            return y + card.height() < top or y > bottom
        self._queue.sort(key=off_screen)       # stable: page order kept

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
        picture = self._render(card.data.key, self._render_width)
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
        for view in self._sections:
            if view.heading is not None:
                view.heading.setStyleSheet(f"color: {colour['text']};")
            if view.blurb is not None:
                view.blurb.setStyleSheet(f"color: {colour['muted']};")
        for card in self.cards:
            card.style_labels()
            card.update()

    # -- layout -----------------------------------------------------------

    def _min_card(self) -> int:
        return self.fontMetrics().height() * MIN_CARD_LINES

    def _reflow(self) -> None:
        width = max(1, self.viewport().width())
        gap = SPACING["md"]
        cols = max(1, min(MAX_COLUMNS, (width + gap) // (self._min_card() + gap)))
        card_w = max(1, (width - gap * (cols - 1)) // cols)
        if (cols, card_w) == (self._columns, self._card_width) and \
                any(view.grid.count() for view in self._sections):
            return
        self._columns, self._card_width = cols, card_w
        inner_w = card_w - 2 * SPACING["sm"]
        width = max(MIN_RENDER, round(inner_w / RENDER_STEP) * RENDER_STEP)
        redraw = width != self._render_width and bool(self.cards)
        self._render_width = width
        inner = card_w - 2 * SPACING["sm"]
        for view in self._sections:
            grid = view.grid
            for card in view.cards:
                grid.removeWidget(card)
            heights = []
            for card in view.cards:
                card.thumb.setFixedHeight(max(1, int(inner * ASPECT)))
                heights.append(card.height_for(card_w))
            for index, card in enumerate(view.cards):
                row = heights[index - index % cols: index - index % cols + cols]
                card.setFixedSize(card_w, max(row))
                grid.addWidget(card, index // cols, index % cols,
                               Qt.AlignmentFlag.AlignTop
                               | Qt.AlignmentFlag.AlignLeft)
                card.show()
            # the columns past the last card take the slack
            for col in range(MAX_COLUMNS + 1):
                grid.setColumnStretch(col, 0)
            grid.setColumnStretch(cols, 1)
        # the cards' new heights raise the page's minimum, but the scroll
        # area kept the old page height and the rows overlapped (a narrow
        # dock): have it size the page again now
        QCoreApplication.postEvent(self, QEvent(QEvent.Type.LayoutRequest))
        if redraw and self._generation_shown:
            self._refresh_pictures()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt name
        super().resizeEvent(event)
        self._reflow()
