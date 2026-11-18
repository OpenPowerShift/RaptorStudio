"""Put rich data on the system clipboard without crashing on quit.

`QClipboard.setMimeData` takes ownership of a `QMimeData` built in
Python. Qt deletes the clipboard's data from a C++ exit handler that runs
after the Python interpreter has shut down, and deleting the object's
Python wrapper then segfaults (PySide 6.11: copy a block, quit, "core
dumped"). Data Qt builds itself (`setText`, `setImage`, `setPixmap`) has
no such wrapper and is safe.

So `set_mime_data` remembers the object it published, and
`release_clipboard` (connected to `aboutToQuit`) swaps it, while Python
can still run, for a plain-text copy that Qt builds itself. The text stays
on the clipboard after RAPTOR Studio closes; RAPTOR's own format and the
HTML table copy do not, which only matters for pasting into a later
session.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QGuiApplication
from shiboken6 import Shiboken

#: The C++ address of the `QMimeData` this process last published.
_published: int | None = None
_hooked = False


def _address(obj: Any) -> int | None:
    if obj is None or not Shiboken.isValid(obj):
        return None
    return int(Shiboken.getCppPointer(obj)[0])


def set_mime_data(mime: QMimeData) -> None:
    """`QApplication.clipboard().setMimeData(mime)`, safely released on
    quit (see the module docstring)."""
    global _published, _hooked
    app = QGuiApplication.instance()
    if app is None:
        return
    QGuiApplication.clipboard().setMimeData(mime)
    _published = _address(mime)
    if not _hooked:
        app.aboutToQuit.connect(release_clipboard)
        _hooked = True


def release_clipboard() -> None:
    """Replace data `set_mime_data` published with a Qt-built plain-text
    copy. A no-op when another program (or a later `setText`) has taken
    the clipboard since: Qt deleted our object then, while Python ran."""
    global _published
    app = QGuiApplication.instance()
    if _published is None or app is None:
        return
    clipboard = QGuiApplication.clipboard()
    current = clipboard.mimeData()
    if _address(current) == _published:
        text = current.text() if current.hasText() else ""
        if text:
            clipboard.setText(text)
        else:
            clipboard.clear()
    _published = None
