"""Real QtWebEngine checks for the Help viewer layout, run in a subprocess.

A WebEngine crash takes its whole process down (and PySide can abort the
pytest worker at teardown). These assertions therefore live in a child
process, like `test_no_app_event_filters.py`: a clean exit code and a
sentinel on stdout are the assertion.
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


_LAYOUT_SCRIPT = r'''
import json
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QEventLoop, QTimer, QUrl
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from raptor.gui.ui import help_viewer

app = QApplication.instance() or QApplication([])
doc = Path(os.environ["HELP_DOC"])
doc2 = Path(os.environ["HELP_DOC2"])
help_viewer._resolve_doc = (
    lambda name: doc2 if name == "viewer2.html" else doc)
help_viewer._under_docs_tree = lambda url: True
help_viewer._register_ops_scheme()


def js(win, script, timeout_ms=5000):
    loop = QEventLoop()
    result = []

    def done(value):
        result.append(value)
        loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)
    win._view.page().runJavaScript(script, 0, done)
    loop.exec()
    timer.stop()
    assert result, "JavaScript timed out"
    return result[0]


def wait_until(predicate, what, timeout_ms=10000):
    waited = 0
    while waited < timeout_ms:
        if predicate():
            return
        QTest.qWait(20)
        app.processEvents()
        waited += 20
    raise AssertionError(f"timed out waiting for {what}")


def wait_stable(expr, what, timeout_ms=10000):
    """Wait until expr's numeric result has stopped moving (3 samples)."""
    last = None
    stable = 0
    waited = 0
    while waited < timeout_ms:
        value = js(win, expr)
        if last is not None and abs(value - last) <= 0.5:
            stable += 1
            if stable >= 3:
                return value
        else:
            stable = 0
        last = value
        QTest.qWait(20)
        app.processEvents()
        waited += 20
    raise AssertionError(f"timed out waiting for {what}")


def load_doc(name, anchor=None, timeout_ms=10000):
    spy = QSignalSpy(win._view.loadFinished)
    win.show_doc(name, anchor)
    assert spy.wait(timeout_ms), f"{name} did not load"
    app.processEvents()


LAYOUT_JS = r"""
(function () {
  const t = document.getElementById('toc');
  const s = getComputedStyle(t);
  return JSON.stringify({
    position: s.position,
    width: s.width,
    top: s.top,
    marginTop: s.marginTop,
    borderTopWidth: s.borderTopWidth,
    paddingTop: s.paddingTop,
    bodyClass: document.body.className,
    innerWidth: window.innerWidth
  });
})();
"""

win = help_viewer.HelpViewer()
win.resize(1100, 800)
win.show()
load_doc("viewer.html")

layout = json.loads(js(win, LAYOUT_JS))
assert "raptor-viewer" in layout["bodyClass"], layout
assert layout["position"] == "fixed", layout
assert layout["width"] == "240px", layout
assert layout["top"] == "0px", layout
assert layout["marginTop"] == "0px", layout
assert layout["borderTopWidth"] == "0px", layout
assert layout["paddingTop"] == "16px", layout
assert layout["innerWidth"] <= 1120, layout

# Real pending-anchor path: a same-document load is in flight with no
# fragment. A second Help action must queue the anchor and use JS scroll,
# not start a fragment load. Waiting on page().isLoading() (the real API)
# also proves the branch the old QWebEngineView.isLoading() check missed.
load_doc("viewer2.html")
recorded_loads = []
original_load = win._view.load


def record_load(url):
    recorded_loads.append(url.toString())
    original_load(url)


win._view.load = record_load
win._view.load(QUrl.fromLocalFile(str(doc2)))
wait_until(lambda: win._view.page().isLoading(), "the same-doc reload")
wait_until(lambda: win._inflight_url is not None, "the load-started token")
recorded_loads.clear()

spy = QSignalSpy(win._view.loadFinished)
win.show_doc("viewer2.html", "section-two")
assert win._pending_anchor == "section-two", win._pending_anchor
assert recorded_loads == [], recorded_loads
assert spy.wait(10000), "the same-doc reload did not finish"
app.processEvents()
wait_until(
    lambda: abs(js(win, "document.getElementById('section-two')"
                          ".getBoundingClientRect().top")) <= 2,
    "the queued anchor to be applied")
assert recorded_loads == [], recorded_loads
assert js(win, """
(function () {
  const hs = Array.from(document.querySelectorAll('#content h2'));
  let current = hs[0];
  for (const h of hs) {
    if (h.getBoundingClientRect().top <= 8) current = h;
    else break;
  }
  return current.id;
})();
""") == "section-two"
win._view.load = original_load

load_doc("viewer.html")

# Make the scroll deterministic; the shipped stylesheet asks for smooth.
js(win, "document.documentElement.style.scrollBehavior = 'auto'; true")
assert js(win, "!!document.getElementById('section-two')")
js(win, """
(function () {
  const h = document.getElementById('section-two');
  const y = h.getBoundingClientRect().top + window.scrollY;
  window.scrollTo(0, y + 40);
  return true;
})();
""")
wait_until(
    lambda: -42 <= js(win, "document.getElementById('section-two')"
                            ".getBoundingClientRect().top") <= -38,
    "the initial scroll position")
wait_until(lambda: js(win, "window.__raptorReading.id") == "section-two",
           "the reading-position capture")
wait_until(lambda: abs(js(win, "window.__raptorReading.offset") + 40) <= 2,
           "the captured heading offset")
before = js(win, "document.getElementById('section-two')"
                 ".getBoundingClientRect().top")
assert -42 <= before <= -38, before

js(win, "window.__raptorRestoreCount = 0; true")
win.resize(1600, 800)
wait_until(lambda: js(win, "window.innerWidth") >= 1500, "the wider viewport")
wait_until(lambda: js(win, "window.__raptorRestoreCount || 0") >= 1,
           "the resize restore sentinel")
after = wait_stable(
    "document.getElementById('section-two').getBoundingClientRect().top",
    "the restored heading position")
assert abs(after - before) < 6, (before, after)

layout2 = json.loads(js(win, LAYOUT_JS))
assert layout2["position"] == "fixed", layout2
assert layout2["width"] == "240px", layout2
assert layout2["top"] == "0px", layout2
assert layout2["marginTop"] == "0px", layout2
assert layout2["borderTopWidth"] == "0px", layout2

nearest = js(win, """
(function () {
  const hs = Array.from(document.querySelectorAll('#content h2'));
  let current = hs[0];
  for (const h of hs) {
    if (h.getBoundingClientRect().top <= 8) current = h;
    else break;
  }
  return current.id;
})();
""")
assert nearest == "section-two", nearest

# The process-wide scheme handler must not store a viewer callback.
assert help_viewer._OPS_HANDLER is not None
assert not hasattr(help_viewer._OPS_HANDLER, "_on_open")
handler = help_viewer._OPS_HANDLER

win.shutdown()
QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
app.processEvents()

# Re-creating a viewer after the hard teardown must not touch the old page
# or replace the stable handler.
win2 = help_viewer.HelpViewer()
assert help_viewer._OPS_HANDLER is handler
win2.shutdown()
QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
app.processEvents()
print("help-layout-ok", before, after)
'''


def _run(script: str, tmp_path: Path, timeout: int = 90, **extra_env):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONPATH": str(ROOT),
        "XDG_CONFIG_HOME": str(tmp_path),
        "HOME": str(tmp_path),
        **extra_env,
    }
    return subprocess.run(
        [sys.executable, "-c", script], env=env, cwd=ROOT,
        capture_output=True, text=True, timeout=timeout)


def test_the_stable_toc_and_resize_restore_survive_in_a_subprocess(tmp_path):
    css = (ROOT / "new_docs" / "ops_asciidoctor.css").as_uri()
    doc = tmp_path / "viewer.html"
    doc.write_text(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="{css}">
<style>
  #content h2 {{ margin: 0; padding-top: 80px; }}
  .spacer {{ height: 3000px; }}
</style></head>
<body class="article toc2 toc-left">
<div id="header"><h1>Help viewer scroll test</h1>
<div id="toc" class="toc2"><div id="toctitle">Contents</div>
<ul><li><a href="#section-one">One</a></li>
<li><a href="#section-two">Two</a></li></ul></div></div>
<div id="content">
  <h2 id="section-one">Section one</h2><div class="spacer"></div>
  <h2 id="section-two">Section two</h2><div class="spacer"></div>
  <h2 id="section-three">Section three</h2><div class="spacer"></div>
</div>
</body></html>""", encoding="utf-8")

    doc2 = tmp_path / "viewer2.html"
    doc2.write_text(doc.read_text(encoding="utf-8"), encoding="utf-8")

    done = _run(_LAYOUT_SCRIPT, tmp_path, HELP_DOC=str(doc),
                HELP_DOC2=str(doc2))
    assert done.returncode == 0, done.stderr[-5000:]
    assert "help-layout-ok" in done.stdout, done.stdout[-2000:]
