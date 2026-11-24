"""The Markdown description of one saved version, shown in Version History.

Rendered Markdown by default; **Edit** swaps in a plain-text editor showing
the raw source, **Save** (Ctrl+Enter) stores it and re-renders, **Cancel**
(Esc) throws the edit away. The text is data: the viewer never loads
remote resources and a clicked link only opens in the system browser when it
is a web or mail address.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QKeyEvent
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QStackedWidget,
    QTextBrowser, QVBoxLayout, QWidget,
)

from raptor.core.history import MAX_DESCRIPTION
from raptor.gui import i18n
from raptor.gui.resources.design_tokens import active_colour

#: Link schemes a click may hand to the system; anything else is ignored.
SAFE_SCHEMES = ("http", "https", "mailto")


def open_link(url: QUrl) -> bool:
    """Open a safe link externally; refuse everything else."""
    if url.scheme().lower() not in SAFE_SCHEMES:
        return False
    return QDesktopServices.openUrl(url)


class _OfflineBrowser(QTextBrowser):
    """A Markdown viewer that never fetches anything (images included)."""

    def loadResource(self, type: int, name: QUrl | str) -> Any:  # noqa: A002
        return None


class _DescriptionEdit(QPlainTextEdit):
    """Raw-Markdown editor: Ctrl+Enter saves, Esc cancels (and never
    reaches the dialog, which would close)."""

    save_requested = Signal()
    cancel_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            self.save_requested.emit()
            return
        if key == Qt.Key.Key_Escape:
            event.accept()
            self.cancel_requested.emit()
            return
        super().keyPressEvent(event)


class DescriptionPanel(QWidget):
    """Render/edit one version's description.

    `save(snapshot_id, text) -> bool` persists it (the dialog supplies the
    store call); `saved` is emitted afterwards so the list can refresh.
    """

    saved = Signal(str)

    def __init__(self, save: Callable[[str, str], bool],
                 parent: QObject | None = None):
        super().__init__(parent)  # type: ignore[arg-type]
        self._save = save
        self.snapshot_id: str | None = None
        self.text = ""
        self.editing = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        head = QHBoxLayout()
        title = QLabel("Description")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        head.addWidget(title)
        head.addStretch(1)
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setToolTip(
            "Write or change this version's description (Markdown)")
        self.edit_btn.clicked.connect(self.begin_edit)
        self.save_btn = QPushButton("Save")
        self.save_btn.setToolTip("Save the description (Ctrl+Enter)")
        self.save_btn.clicked.connect(self.save)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setToolTip("Discard changes (Esc)")
        self.cancel_btn.clicked.connect(self.cancel)
        for button in (self.edit_btn, self.save_btn, self.cancel_btn):
            head.addWidget(button)
        lay.addLayout(head)

        self._stack = QStackedWidget()
        self.view = _OfflineBrowser()
        self.view.setObjectName("HistoryDescriptionView")
        self.view.setAccessibleName("Version description")
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.view.anchorClicked.connect(open_link)
        self.view.setMinimumHeight(70)
        self.editor = _DescriptionEdit()
        self.editor.setAccessibleName("Version description (Markdown)")
        self.editor.setPlaceholderText(
            "Markdown supported: **bold**, *italic*, lists, links…")
        self.editor.setMinimumHeight(70)
        self.editor.save_requested.connect(self.save)
        self.editor.cancel_requested.connect(self.cancel)
        self._stack.addWidget(self.view)
        self._stack.addWidget(self.editor)
        lay.addWidget(self._stack, 1)
        self.hint = QLabel(
            "Markdown supported: **bold**, *italic*, lists, links and "
            "headings. Ctrl+Enter saves, Esc cancels.")
        self.hint.setObjectName("SectionNote")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        self.show_version(None, "")

    # -- state ----------------------------------------------------------
    def is_dirty(self) -> bool:
        return self.editing and \
            self.editor.toPlainText().strip() != self.text.strip()

    def show_version(self, snapshot_id: str | None, text: str) -> None:
        """Show another version's description (leaves edit mode)."""
        self.snapshot_id = snapshot_id
        self.text = text or ""
        self.editing = False
        self._refresh()

    def update_text(self, text: str) -> None:
        """The stored description changed elsewhere; keep an open edit."""
        self.text = text or ""
        if not self.editing:
            self._refresh()

    def _refresh(self) -> None:
        available = self.snapshot_id is not None
        self._stack.setCurrentWidget(self.editor if self.editing
                                     else self.view)
        self.edit_btn.setVisible(not self.editing)
        self.edit_btn.setEnabled(available)
        self.save_btn.setVisible(self.editing)
        self.cancel_btn.setVisible(self.editing)
        self.hint.setVisible(self.editing)
        if not self.editing:
            self._render()

    def _render(self) -> None:
        c = active_colour()
        self.view.setStyleSheet(
            f"QTextBrowser {{ background: {c['surface']}; color: {c['text']};"
            f" border: 1px solid {c['border']}; padding: 4px 8px; }}")
        self.view.document().setDefaultStyleSheet(
            f"a {{ color: {c['accent']}; }} "
            f"code, pre {{ color: {c['accent']}; }} "
            f"h1, h2, h3, h4 {{ color: {c['text']}; }}")
        if self.text.strip():
            self.view.setMarkdown(self.text)
        else:
            self.view.setHtml(
                f"<p style='color:{c['muted']}'>"
                + _escape(i18n.tr("No description. Press Edit to add one "
                                  "(Markdown supported)."))
                + "</p>")

    # -- editing --------------------------------------------------------
    def begin_edit(self) -> None:
        if self.snapshot_id is None or self.editing:
            return
        self.editing = True
        self.editor.setPlainText(self.text)
        self._refresh()
        self.editor.setFocus()
        cursor = self.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)

    def save(self) -> bool:
        """Persist the edit; stays in edit mode when the save fails."""
        if not self.editing or self.snapshot_id is None:
            return False
        text = self.editor.toPlainText()[:MAX_DESCRIPTION]
        if not self._save(self.snapshot_id, text):
            return False
        sid = self.snapshot_id
        self.text = text.strip()
        self.editing = False
        self._refresh()
        self.saved.emit(sid)
        return True

    def cancel(self) -> None:
        if not self.editing:
            return
        self.editing = False
        self._refresh()


def _escape(text: str) -> str:
    import html
    return html.escape(text)
