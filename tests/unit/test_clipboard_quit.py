"""Copying rich data and then quitting must not segfault (clipboard_data).

Qt deletes the clipboard's `QMimeData` from a C++ exit handler after the
interpreter has gone; a Python-built one then crashed the process on quit.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_SCRIPT = textwrap.dedent("""
    import sys
    from PySide6.QtCore import QByteArray, QMimeData, QTimer
    from PySide6.QtWidgets import QApplication
    from raptor.gui.clipboard_data import set_mime_data

    app = QApplication([])
    def copy_then_quit():
        mime = QMimeData()
        mime.setData("application/x-raptor-test", QByteArray(b"payload"))
        mime.setText("two blocks")
        set_mime_data(mime)
        app.quit()
    QTimer.singleShot(0, copy_then_quit)
    app.exec()
    print("text after quit:", app.clipboard().text(), flush=True)
""")


def _run(script: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
           "PYTHONPATH": str(ROOT)}
    return subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


def test_copy_then_quit_exits_cleanly_and_keeps_the_text():
    result = _run(_SCRIPT)
    assert result.returncode == 0, result.stderr[-2000:]
    assert "text after quit: two blocks" in result.stdout


def test_release_leaves_another_programs_clipboard_alone():
    from PySide6.QtCore import QByteArray, QMimeData
    from PySide6.QtWidgets import QApplication

    from raptor.gui import clipboard_data

    app = QApplication.instance() or QApplication([])
    mime = QMimeData()
    mime.setData("application/x-raptor-test", QByteArray(b"x"))
    mime.setText("ours")
    clipboard_data.set_mime_data(mime)
    app.clipboard().setText("someone else's")   # replaces (and deletes) ours
    clipboard_data.release_clipboard()
    assert app.clipboard().text() == "someone else's"


def test_release_swaps_our_data_for_plain_text():
    from PySide6.QtCore import QByteArray, QMimeData
    from PySide6.QtWidgets import QApplication

    from raptor.gui import clipboard_data

    app = QApplication.instance() or QApplication([])
    mime = QMimeData()
    mime.setData("application/x-raptor-test", QByteArray(b"x"))
    mime.setText("ours")
    clipboard_data.set_mime_data(mime)
    clipboard_data.release_clipboard()
    data = app.clipboard().mimeData()
    assert app.clipboard().text() == "ours"
    assert not data.hasFormat("application/x-raptor-test")
