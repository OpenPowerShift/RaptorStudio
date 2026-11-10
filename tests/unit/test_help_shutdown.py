"""Help must shut down cleanly with QtWebEngine (#75 / #206).

A crash in WebEngine teardown kills the process, so these run in a child
process and assert its exit code plus a sentinel. Two paths matter: the
normal quit/`_quit_all` path, and `app.main`'s finally when `app.exec()`
raises before the event loop ever runs.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from raptor.gui.ui import help_viewer as _help_viewer

if not _help_viewer.WEBENGINE_OK:        # pragma: no cover - env dependent
    pytest.skip("QtWebEngine is not available", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]


_QUIT_SCRIPT = r'''
from PySide6.QtCore import QTimer

from raptor.gui.app import RaptorApplication
from raptor.gui.ui import settings
settings.set_value("startup/show_start_page", False)

app = RaptorApplication(["raptor"])
from raptor.gui.ui.main_window import MainWindow
win = MainWindow()
win.show()
win._show_help_viewer("book.html")


def quit_all():
    win._dirty = False
    win._quit_all()


QTimer.singleShot(2500, quit_all)
rc = app.exec()
print("help-quit-ok", rc)
'''


_ABOUT_TO_QUIT_SCRIPT = r'''
import weakref

from PySide6.QtCore import QTimer

from raptor.gui.app import RaptorApplication
from raptor.gui.ui import help_service, settings
settings.set_value("startup/show_start_page", False)

app = RaptorApplication(["raptor"])
from raptor.gui.ui.main_window import MainWindow
win = MainWindow()
win.show()
viewer = win._show_help_viewer("book.html")
viewer_ref = weakref.ref(viewer)
del viewer
state = {}


def quit_with_help_open():
    service = help_service._SERVICE
    state["open_before_quit"] = bool(service and service.viewer)
    app.quit()


QTimer.singleShot(2500, quit_with_help_open)
rc = app.exec()
service = help_service._SERVICE
assert state.get("open_before_quit"), "Help was not open at app.quit()"
assert service is not None and service.viewer is None, (
    "aboutToQuit did not destroy Help")
assert viewer_ref() is None, "the viewer survived aboutToQuit teardown"
print("help-about-to-quit-ok", rc)
'''


_EXCEPTION_SCRIPT = r'''
import raptor.gui.app as app_mod
from raptor.gui.ui import help_service

real_show = app_mod.MainWindow.show


def show_with_help(self):
    real_show(self)
    self._show_help_viewer("book.html")


app_mod.MainWindow.show = show_with_help


def boom(self):
    raise RuntimeError("exec boom")


app_mod.RaptorApplication.exec = boom

try:
    app_mod.main(["raptor", "--new"], splash_delay_ms=0, splash_fade_ms=0)
except RuntimeError as exc:
    assert "exec boom" in str(exc)
else:
    raise AssertionError("app.main did not propagate the exec failure")

service = help_service._SERVICE
assert service is not None, "Help service was never created"
assert service.viewer is None, "app.main's finally did not close Help"
print("app-exception-shutdown-ok")
'''


def _run(script: str, tmp_path: Path, timeout: int = 90):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONPATH": str(ROOT),
        "XDG_CONFIG_HOME": str(tmp_path),
        "HOME": str(tmp_path),
    }
    return subprocess.run(
        [sys.executable, "-c", script], env=env, cwd=ROOT,
        capture_output=True, text=True, timeout=timeout)


def test_open_help_then_quit_all_exits_cleanly(tmp_path):
    done = _run(_QUIT_SCRIPT, tmp_path)
    assert done.returncode == 0, done.stderr[-5000:]
    assert "help-quit-ok" in done.stdout, done.stdout[-2000:]


def test_about_to_quit_with_help_open_exits_cleanly(tmp_path):
    done = _run(_ABOUT_TO_QUIT_SCRIPT, tmp_path)
    assert done.returncode == 0, done.stderr[-5000:]
    assert "help-about-to-quit-ok" in done.stdout, done.stdout[-2000:]


def test_app_exec_exception_still_closes_help(tmp_path):
    done = _run(_EXCEPTION_SCRIPT, tmp_path)
    assert done.returncode == 0, done.stderr[-5000:]
    assert "app-exception-shutdown-ok" in done.stdout, done.stdout[-2000:]
