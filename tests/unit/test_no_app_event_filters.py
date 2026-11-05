"""No Python event filter on the QApplication (#142).

An application-wide filter receives every event in the process, including
those of QtWebEngine's internal Quick items, and PySide 6.11 segfaults in
`getWrapperForQObject` resolving a wrapper for them. It showed as a crash
on opening Block Defaults, but any app-wide Python filter plus a web view
(Help) can do it."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[2]

#: `<anything>.instance().installEventFilter(` or `app.installEventFilter(`.
APP_FILTER = re.compile(
    r"(instance\(\)|\bapp|\bqapp)\.installEventFilter\(")


def test_no_source_installs_a_filter_on_the_application():
    hits = [f"{p.relative_to(ROOT)}:{n}"
            for p in (ROOT / "raptor").rglob("*.py")
            for n, line in enumerate(p.read_text().splitlines(), 1)
            if APP_FILTER.search(line)]
    assert hits == []


SCRIPT = r"""
import faulthandler, time
faulthandler.enable()
from raptor.gui.app import RaptorApplication
app = RaptorApplication(["raptor"])
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from raptor.gui.ui import settings
settings.set_value("startup/show_start_page", False)
from raptor.gui.ui.main_window import MainWindow
win = MainWindow()
win.show()
view = QWebEngineView()
view.setHtml("<p>" + "help " * 400 + "</p>")
view.resize(400, 400)
view.show()
win._show_key_tips()           # the one filter that came and went
end = time.monotonic() + 4
while time.monotonic() < end:
    app.processEvents()
win._dismiss_key_tips()
win._dirty = False
win.close()
view.close()
print("survived")
"""


@pytest.mark.gui
def test_the_app_survives_a_web_view_alongside_the_window(tmp_path):
    """Before the fix this segfaulted within the first second of loading
    the web view, in the same PySide frames as the reported crash."""
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
           "PYTHONPATH": str(ROOT),
           "XDG_CONFIG_HOME": str(tmp_path), "HOME": str(tmp_path)}
    done = subprocess.run([sys.executable, "-c", SCRIPT], env=env,
                          cwd=ROOT, capture_output=True, text=True,
                          timeout=20)
    assert done.returncode == 0, done.stderr[-3000:]
    assert "survived" in done.stdout
