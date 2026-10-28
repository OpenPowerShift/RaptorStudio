"""The one place the GUI opens a plain message box (`warning`, `critical`, `information`, `about`).

Every call goes through here for the same reason `dialogs.question_box` exists: a headless test patches ONE name
(`monkeypatch.setattr("raptor.gui.ui.msgbox.critical", ...)`) to answer or record the box, so a failing export or a
refused edit can be tested instead of hanging on a modal. The functions call the `QMessageBox` static methods at
call time, so a test that stubs `QMessageBox.warning` itself still works. `tests/unit/test_msgbox_seam.py` fails
if any other module calls those static methods directly.
"""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox


def warning(*args, **kwargs):
    return QMessageBox.warning(*args, **kwargs)


def critical(*args, **kwargs):
    return QMessageBox.critical(*args, **kwargs)


def information(*args, **kwargs):
    return QMessageBox.information(*args, **kwargs)


def about(*args, **kwargs):
    return QMessageBox.about(*args, **kwargs)
