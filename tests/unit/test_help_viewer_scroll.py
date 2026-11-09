"""Maximising the Help viewer keeps the section being read (#207).

The real viewer runs QtWebEngine: the injected page script pins the TOC as
a fixed sidebar at every width and restores the nearest heading + offset
after a resize. Skipped when WebEngine is not importable.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QEventLoop, QTimer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from raptor.gui.ui import help_viewer

pytestmark = pytest.mark.gui

if not help_viewer.WEBENGINE_OK:         # pragma: no cover - env dependent
    pytest.skip("QtWebEngine is not available", allow_module_level=True)


DOC = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  html { scroll-behavior: smooth; }
  body { margin: 0; font-family: sans-serif; }
  #content { font-size: 20px; line-height: 1.6; }
  @media (min-width: 90rem) { #content { font-size: 12px; } }
  #content h2 { margin: 0; padding-top: 80px; }
  .spacer { height: 3000px; }
</style></head>
<body class="article toc2 toc-left">
<div id="header"><h1>Help viewer scroll test</h1></div>
<div id="content">
  <h2 id="section-one">Section one</h2><div class="spacer"></div>
  <h2 id="section-two">Section two</h2><div class="spacer"></div>
  <h2 id="section-three">Section three</h2><div class="spacer"></div>
</div>
</body></html>
"""


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _js(viewer, script, timeout_ms=5000):
    """Run JavaScript on the viewer's page and return its result."""
    loop = QEventLoop()
    result: list[object] = []

    def finished(value):
        result.append(value)
        loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)
    viewer._view.page().runJavaScript(script, 0, finished)
    loop.exec()
    timer.stop()
    assert result, "JavaScript timed out"
    return result[0]


@pytest.fixture
def viewer(qapp, tmp_path, monkeypatch):
    doc = tmp_path / "viewer.html"
    doc.write_text(DOC, encoding="utf-8")
    monkeypatch.setattr(help_viewer, "_resolve_doc", lambda name: doc)
    # The test document lives outside the bundled docs tree; treat it as
    # ours so the page navigates in-app instead of opening a browser.
    monkeypatch.setattr(help_viewer, "_under_docs_tree", lambda url: True)
    help_viewer._register_ops_scheme()

    win = help_viewer.HelpViewer()
    win.resize(1100, 800)
    win.show()
    spy = QSignalSpy(win._view.loadFinished)
    win.show_doc("viewer.html")
    assert spy.wait(10000), "the help page did not load"
    qapp.processEvents()
    try:
        yield win
    finally:
        win.close()
        win.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def test_the_viewer_uses_the_stable_toc_layout(viewer):
    assert _js(viewer, "document.body.classList.contains('raptor-viewer')")
    # The class is what the stylesheet keys the fixed-TOC rules on.
    css = (help_viewer._UI_DIR.parents[2] / "new_docs" /
           "ops_asciidoctor.css").read_text(encoding="utf-8")
    assert "body.toc2.raptor-viewer #toc" in css


def test_resizing_keeps_the_same_heading_and_offset(viewer, qapp):
    assert _js(viewer, "!!document.getElementById('section-two')")
    # Put section two's heading 40px above the viewport top, so its
    # "nearest visible heading" is section-two with a negative offset.
    assert _js(viewer, """
(function () {
  const h = document.getElementById('section-two');
  const y = h.getBoundingClientRect().top + window.scrollY;
  window.scrollTo(0, y + 40);
  return true;
})();
""")
    QTest.qWait(200)
    qapp.processEvents()
    before = _js(viewer, "document.getElementById('section-two')"
                         ".getBoundingClientRect().top")
    assert -42 <= before <= -38

    viewer.resize(1600, 800)
    QTest.qWait(500)
    qapp.processEvents()

    after = _js(viewer, "document.getElementById('section-two')"
                        ".getBoundingClientRect().top")
    assert abs(after - before) < 6, (before, after)
    assert _js(viewer, """
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
