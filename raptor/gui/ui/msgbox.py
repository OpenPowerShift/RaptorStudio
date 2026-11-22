"""The one place the GUI opens a plain message box (`warning`, `critical`, `information`, `about`).

Every call goes through here for the same reason `dialogs.question_box` exists: a headless test patches ONE name
(`monkeypatch.setattr("raptor.gui.ui.msgbox.critical", ...)`) to answer or record the box, so a failing export or a
refused edit can be tested instead of hanging on a modal. The functions call the `QMessageBox` static methods at
call time, so a test that stubs `QMessageBox.warning` itself still works. `tests/unit/test_msgbox_seam.py` fails
if any other module calls those static methods directly.

The title and body are looked up in the widget catalogue here: the static helpers never expose the QMessageBox
object, so this seam is also the only place a widget-tree pass could run. Buttons come from Qt's own
`qtbase_<locale>.qm`.
"""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from raptor.gui.i18n import tr


def _translated(args, kwargs):
    """Translate the (parent, title, text) prefix the static helpers take."""
    args = list(args)
    if len(args) >= 3:
        args[1] = tr(str(args[1]), "Gui")
        args[2] = tr(str(args[2]), "Gui")
    elif len(args) == 2:
        args[1] = tr(str(args[1]), "Gui")
    for key in ("title", "text"):
        if key in kwargs and isinstance(kwargs[key], str):
            kwargs[key] = tr(kwargs[key], "Gui")
    return tuple(args), kwargs


def warning(*args, **kwargs):
    args, kwargs = _translated(args, kwargs)
    return QMessageBox.warning(*args, **kwargs)


def critical(*args, **kwargs):
    args, kwargs = _translated(args, kwargs)
    return QMessageBox.critical(*args, **kwargs)


def information(*args, **kwargs):
    args, kwargs = _translated(args, kwargs)
    return QMessageBox.information(*args, **kwargs)


def about(*args, **kwargs):
    args, kwargs = _translated(args, kwargs)
    return QMessageBox.about(*args, **kwargs)
