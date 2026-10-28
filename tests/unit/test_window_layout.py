"""The main window remembers its geometry -- but only in the real app.

It used to open at a hard-coded 1600x1000 every time: bigger than a
1366x768 laptop screen, and it forgot where the user had left it.

Persistence is opt-in (`enable_layout_persistence`, called by
`app.main()`), never automatic in `MainWindow`: a test that builds and
closes a window would otherwise hand its geometry to the next test's.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_settings():
    QSettings().clear()
    yield
    QSettings().clear()


def _window():
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._dirty = False
    return w


def test_a_plain_window_never_persists(qapp):
    from raptor.gui.ui import settings
    w = _window()
    w.resize(700, 520)
    w.close()
    assert settings.get("layout/window_geometry") in (None, b"", "")
    w2 = _window()
    assert (w2.width(), w2.height()) != (700, 520)
    w2.close()


def test_geometry_is_saved_on_close_and_handed_back_on_start(qapp,
                                                              monkeypatch):
    """What is asserted is OUR half of the contract: bytes are written on
    close, and the next window passes exactly those bytes to Qt. Qt's own
    restoreGeometry then clamps to the real screen, which on the 800x600
    headless platform makes pixel sizes meaningless to assert."""
    from PySide6.QtWidgets import QMainWindow
    from raptor.gui.ui import settings

    w = _window()
    w.enable_layout_persistence()
    w.show()
    qapp.processEvents()
    expected = bytes(w.saveGeometry())
    w.close()
    saved = settings.get("layout/window_geometry")
    assert saved and bytes(saved) == expected

    seen = []
    real = QMainWindow.restoreGeometry
    monkeypatch.setattr(
        QMainWindow, "restoreGeometry",
        lambda self, b: (seen.append(bytes(b)), real(self, b))[1])
    w2 = _window()
    w2.enable_layout_persistence()
    assert seen == [expected]
    w2.close()


def test_offscreen_saved_geometry_is_pulled_back_on_screen(qapp):
    w = _window()
    w.enable_layout_persistence()
    w.move(-9000, -9000)
    w.resize(700, 520)
    w._clamp_to_screens()
    union = None
    for sc in QGuiApplication.screens():
        g = sc.availableGeometry()
        union = g if union is None else union.united(g)
    assert union.intersects(w.frameGeometry())
    w.close()


def test_small_supported_screens_get_a_window_that_fits(qapp, monkeypatch):
    """A 1366x768 laptop must not get a 1600x1000 window."""
    from PySide6.QtCore import QRect
    w = _window()

    class FakeScreen:
        def availableGeometry(self):
            return QRect(0, 0, 1366, 728)

    monkeypatch.setattr(QGuiApplication, "primaryScreen",
                        staticmethod(lambda: FakeScreen()))
    size = w._initial_window_size()
    assert (size.width(), size.height()) == (1366, 728)
    w.close()


def test_unsupported_tiny_screens_keep_the_default(qapp, monkeypatch):
    """The headless platform reports 800x600; shrinking to that would
    change every window the test suite builds."""
    from PySide6.QtCore import QRect
    w = _window()

    class FakeScreen:
        def availableGeometry(self):
            return QRect(0, 0, 800, 600)

    monkeypatch.setattr(QGuiApplication, "primaryScreen",
                        staticmethod(lambda: FakeScreen()))
    size = w._initial_window_size()
    assert (size.width(), size.height()) == w._DEFAULT_WINDOW_SIZE
    w.close()
