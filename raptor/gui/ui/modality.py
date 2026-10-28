"""How the application's own dialogs block input.

`QDialog.exec()` makes a dialog application-modal by default: while Preferences (or a pool dialog, the Phases
dialog...) is open nothing else in the application takes a click, including the Help window the user opened to find
out what a setting means. Window-modal blocks only the window the dialog belongs to; the Help viewer is a top-level
window of its own, so it stays usable.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog


def exec_dialog(dlg: QDialog) -> int:
    """Run `dlg` modally for its parent window only (the Help viewer and other top-level windows stay live)."""
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    return dlg.exec()
