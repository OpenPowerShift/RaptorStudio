"""Long tooltips that wrap instead of stretching across the screen.

Qt word-wraps a tooltip only when its text is rich text; a plain one is
one line as wide as the text, so a 300-character ribbon tooltip runs
right across the screen. `wrapped` turns plain text into an equivalent
rich-text paragraph broken into lines of a readable width.

The widget's own `toolTip()` is left as written (tests, accessibility
and the command reference read it); only what is shown changes. Each
widget opts in, either in its own `event()` (`show_wrapped`) or with
`wrap_tooltips(widget)`. Never install this on the QApplication: an
application-wide Python filter sees QtWebEngine's internal objects and
PySide crashes resolving them (#142).
"""
from __future__ import annotations

import html
import textwrap

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QHelpEvent, Qt
from PySide6.QtWidgets import QToolTip, QWidget

#: Shorter tooltips are left as they are: they already fit on one line.
WRAP_FROM = 80

#: Characters per wrapped line.
LINE = 72


def wrapped(text: str) -> str:
    """`text` as rich text in lines of `LINE` characters, or unchanged when
    it is short or already rich text."""
    if len(text) < WRAP_FROM or Qt.mightBeRichText(text):
        return text
    lines = [line for paragraph in text.split("\n")
             for line in (textwrap.wrap(paragraph, LINE) or [""])]
    body = "<br>".join(html.escape(line, quote=False) for line in lines)
    # The lines are broken here, at a reading width; left to itself Qt
    # wraps rich text in a narrow column that makes a tall, cramped box.
    return f"<p style='white-space:pre'>{body}</p>"


def show_wrapped(widget: QWidget, event: QEvent) -> bool:
    """Show `widget`'s tooltip wrapped, for a ToolTip `event`. True when it
    was shown here, so the caller returns True instead of letting Qt
    show it on one line."""
    if event.type() != QEvent.Type.ToolTip or not isinstance(event, QHelpEvent):
        return False
    tip = widget.toolTip()
    shown = wrapped(tip)
    if not tip or shown == tip:
        return False
    QToolTip.showText(event.globalPos(), shown, widget, widget.rect(),
                      widget.toolTipDuration())
    return True


class _WrapTips(QObject):
    def eventFilter(self, obj, event) -> bool:     # noqa: N802 - Qt's name
        if event.type() == QEvent.Type.ToolTip and isinstance(obj, QWidget):
            return show_wrapped(obj, event)
        return False


_FILTER: _WrapTips | None = None


def wrap_tooltips(*widgets: QWidget) -> None:
    """Show each widget's long tooltip wrapped. One shared filter, on these
    widgets only."""
    global _FILTER
    if _FILTER is None:
        _FILTER = _WrapTips()
    for widget in widgets:
        widget.installEventFilter(_FILTER)
