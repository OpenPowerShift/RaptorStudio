"""Help > Keyboard Shortcuts: `docs/KEYBOARD_SHORTCUTS.md` rendered in a
window of the app's own, in the current theme.

Qt renders the Markdown itself (GitHub tables included); this adds what
its renderer leaves plain -- ruled tables with a header row -- and the
theme's colours. A plain QTextBrowser, not the WebEngine help viewer: the
page is one short document, and it opens instantly.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QTextCursor, QTextLength
from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout

from raptor.gui.resources.design_tokens import active_colour


def _rule_tables(browser: QTextBrowser, c: dict) -> None:
    doc = browser.document()
    seen = set()
    block = doc.begin()
    while block.isValid():
        table = QTextCursor(block).currentTable()
        if table is not None and id(table) not in seen:
            seen.add(id(table))
            fmt = table.format()
            fmt.setBorder(1)
            fmt.setBorderBrush(QBrush(QColor(c["border"])))
            fmt.setBorderCollapse(True)
            fmt.setCellPadding(5)
            fmt.setCellSpacing(0)
            fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
            if table.columns() == 2:      # keys | action, lined up page-wide
                fmt.setColumnWidthConstraints([
                    QTextLength(QTextLength.Type.PercentageLength, 28),
                    QTextLength(QTextLength.Type.PercentageLength, 72)])
            table.setFormat(fmt)
            for col in range(table.columns()):
                cell = table.cellAt(0, col)
                cf = cell.format()
                cf.setBackground(QBrush(QColor(c["group_header"])))
                cell.setFormat(cf)
        block = block.next()


def _blank_line_before_tables(markdown: str) -> str:
    """A table straight after a paragraph line is read as more paragraph
    (the annotation z-order table came out as one run of "| a | b |")."""
    out: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("|") and out and out[-1].strip() \
                and not out[-1].startswith("|"):
            out.append("")
        out.append(line)
    return "\n".join(out)


def show_shortcuts(parent, markdown: str, existing=None) -> QDialog:
    """Show the list (reusing `existing` when it is still open)."""
    if existing is not None:
        try:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing
        except RuntimeError:
            pass                                  # deleted with its parent
    c = active_colour()
    dlg = QDialog(parent)
    dlg.setWindowTitle("Keyboard Shortcuts")
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(0, 0, 0, 0)
    browser = QTextBrowser()
    browser.setOpenExternalLinks(True)
    browser.setStyleSheet(
        f"QTextBrowser {{ background: {c['surface']}; color: {c['text']};"
        f" border: none; padding: 12px 18px; }}")
    browser.document().setDefaultStyleSheet(
        f"h1, h2 {{ color: {c['text']}; }} "
        f"td, th {{ color: {c['text']}; }} code {{ color: {c['accent']}; }}")
    browser.setMarkdown(_blank_line_before_tables(markdown))
    _rule_tables(browser, c)
    lay.addWidget(browser)
    dlg.resize(640, 720)
    dlg.show()
    return dlg
