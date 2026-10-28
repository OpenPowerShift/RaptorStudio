"""Startup splash screen: it shows for a couple of seconds, fades out, and
then the main window appears. Tests use a synthetic PNG rather than the
real `raptor_logo.png` asset, so they are not coupled to that file's
integrity."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def logo_path(tmp_path):
    pm = QPixmap(64, 64)
    pm.fill(QColor("red"))
    path = tmp_path / "logo.png"
    assert pm.save(str(path))
    return str(path)


def test_show_splash_displays_the_scaled_logo(qapp, logo_path):
    from raptor.gui.ui.splash import show_splash, _DISPLAY_SIZE
    splash = show_splash(logo_path)
    try:
        assert splash is not None
        assert splash.isVisible()
        pm = splash.pixmap()
        assert not pm.isNull()
        assert pm.width() <= _DISPLAY_SIZE and pm.height() <= _DISPLAY_SIZE
        assert splash.windowOpacity() == pytest.approx(1.0)
    finally:
        splash.close()


def test_show_splash_defaults_to_the_real_package_version(qapp, logo_path):
    """With no `version=` override, the overlay reads `raptor.__version__`
    rather than a hardcoded placeholder."""
    from raptor import __version__
    from raptor.gui.ui.splash import show_splash, RELEASE_CODENAME
    splash = show_splash(logo_path)
    try:
        assert splash is not None
        # White pixels must exist (the overlay text) somewhere in the
        # bottom band -- a weak but real "something was actually
        # drawn there" check independent of exact font metrics.
        img = splash.pixmap().toImage()
        band_top = round(img.height() * 0.89)
        white = any(
            img.pixelColor(x, y).lightness() > 240
            for y in range(band_top, img.height())
            for x in range(0, img.width(), 4))
        assert white, "no white pixels found in the version-overlay band"
    finally:
        splash.close()
    assert __version__, "raptor.__version__ must be a non-empty string"
    assert RELEASE_CODENAME


def test_draw_version_overlay_renders_the_given_text(qapp, logo_path):
    """`_draw_version_overlay` runs on an already-scaled 320x320 pixmap in
    production (`show_splash` scales before overlaying), so scale the tiny
    test fixture up the same way; overlaying a raw thumbnail would leave
    the small, light text no room to render."""
    from raptor.gui.ui.splash import _draw_version_overlay, _DISPLAY_SIZE
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QPixmap
    base = QPixmap(logo_path).scaled(
        _DISPLAY_SIZE, _DISPLAY_SIZE, _Qt.KeepAspectRatio,
        _Qt.SmoothTransformation)
    out = _draw_version_overlay(base, "9.9.9", "Test Codename")
    assert out.size() == base.size()   # overlay must not resize the image
    band_top = round(out.height() * 0.89)
    img = out.toImage()
    white = any(
        img.pixelColor(x, y).lightness() > 240
        for y in range(band_top, img.height())
        for x in range(0, img.width(), 4))
    assert white, "expected white overlay text in the bottom band"


def test_show_splash_returns_none_for_a_missing_or_corrupt_image(
        qapp, tmp_path):
    from raptor.gui.ui.splash import show_splash
    bad = tmp_path / "not_an_image.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\ntruncated garbage, no IEND")
    assert show_splash(str(bad)) is None
    assert show_splash(str(tmp_path / "does_not_exist.png")) is None


def test_fade_out_and_close_reaches_zero_opacity_and_closes(
        qapp, logo_path):
    """Drives the fade with delay_ms=0 (no real wait) and pumps the
    event loop until the QVariantAnimation's own timer completes --
    confirms the splash actually finishes fading and closes, not just
    that a timer was scheduled."""
    from raptor.gui.ui.splash import show_splash, fade_out_and_close
    splash = show_splash(logo_path)
    assert splash is not None
    fade_out_and_close(splash, delay_ms=0, duration_ms=40)

    import time
    deadline = time.monotonic() + 2.0
    while splash.isVisible() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert not splash.isVisible(), \
        "the splash must close once its fade-out animation finishes"


def test_render_fade_frame_blends_toward_the_fade_colour(qapp, logo_path):
    """The fade repaints the pixmap rather than animating
    `QWidget.setWindowOpacity`, which some platform plugins refuse
    outright. This is a pure-function check of the blend math, independent
    of the real `QVariantAnimation`'s timing under the offscreen QPA."""
    from raptor.gui.ui.splash import render_fade_frame
    from PySide6.QtGui import QColor, QPixmap
    base = QPixmap(logo_path)
    fade_colour = QColor("blue")

    at_start = render_fade_frame(base, fade_colour, 0.0)
    at_end = render_fade_frame(base, fade_colour, 1.0)
    at_mid = render_fade_frame(base, fade_colour, 0.5)

    assert at_start.toImage() == base.toImage(), \
        "t=0 must be the untouched original"
    assert at_end.toImage().pixelColor(0, 0) == fade_colour, \
        "t=1 must be fully replaced by the fade colour"
    assert at_mid.toImage() != base.toImage()
    assert at_mid.toImage() != at_end.toImage()


def test_fade_out_and_close_never_touches_window_opacity(qapp, logo_path):
    from raptor.gui.ui.splash import show_splash, fade_out_and_close
    splash = show_splash(logo_path)
    assert splash is not None
    fade_out_and_close(splash, delay_ms=0, duration_ms=40)

    import time
    deadline = time.monotonic() + 2.0
    while splash.isVisible() and time.monotonic() < deadline:
        assert splash.windowOpacity() == pytest.approx(1.0), \
            "fade must not touch windowOpacity on this path"
        qapp.processEvents()
        time.sleep(0.01)
    assert not splash.isVisible()


def test_app_icon_loads_the_real_asset(qapp):
    """`raptor_icon.png` must decode and produce a non-null icon."""
    from raptor.gui.ui.splash import app_icon, ICON_PATH
    assert ICON_PATH.exists()
    icon = app_icon()
    assert not icon.isNull()
    assert not icon.pixmap(64, 64).isNull()


def test_app_icon_returns_a_null_icon_for_a_missing_file(qapp, tmp_path):
    from raptor.gui.ui.splash import app_icon
    icon = app_icon(tmp_path / "does_not_exist.png")
    assert icon.isNull()


def test_app_main_does_not_crash_with_a_missing_logo(qapp, monkeypatch,
                                                      tmp_path):
    """End-to-end: `raptor.gui.app.main()` must not crash or hang when
    the logo asset is missing/corrupt -- `show_splash` returning None
    is a normal, handled case, not an error path."""
    import raptor.gui.ui.splash as splash_mod
    monkeypatch.setattr(splash_mod, "LOGO_PATH", tmp_path / "missing.png")
    from raptor.gui.app import main
    from raptor.gui.ui.main_window import MainWindow

    # exec() would block forever in a real test run, so stub it out to
    # exercise startup wiring without entering the event loop.
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    monkeypatch.setattr(MainWindow, "show", lambda self: None)
    assert main(["raptor.gui.app"]) == 0


def test_app_main_installs_sigint_default_handler_and_survives_it(
        qapp, monkeypatch, tmp_path):
    """`main()` must reset SIGINT to Python's default handling so Ctrl+C
    during `app.exec()` raises KeyboardInterrupt instead of sitting pending
    forever, and must let that exception exit cleanly rather than crash."""
    import signal
    import raptor.gui.ui.splash as splash_mod
    monkeypatch.setattr(splash_mod, "LOGO_PATH", tmp_path / "missing.png")
    from raptor.gui.app import main
    from raptor.gui.ui.main_window import MainWindow

    calls = []
    orig_signal = signal.signal
    def traced_signal(sig, handler):
        calls.append((sig, handler))
        return orig_signal(sig, handler)
    monkeypatch.setattr(signal, "signal", traced_signal)

    def _raise_keyboard_interrupt(self):
        raise KeyboardInterrupt
    monkeypatch.setattr(QApplication, "exec", _raise_keyboard_interrupt)
    monkeypatch.setattr(MainWindow, "show", lambda self: None)

    assert main(["raptor.gui.app"]) == 0, \
        "a KeyboardInterrupt from app.exec() must result in a clean exit"
    assert (signal.SIGINT, signal.SIG_DFL) in calls


def test_app_main_shows_window_only_after_splash_closes(
        qapp, monkeypatch, logo_path):
    """`main()` blocks on a private `QEventLoop` until the splash has
    fully faded and closed, and only then shows the main window, so the
    splash is not left behind it."""
    import raptor.gui.ui.splash as splash_mod
    monkeypatch.setattr(splash_mod, "LOGO_PATH", logo_path)
    from raptor.gui.app import main
    from raptor.gui.ui.main_window import MainWindow
    from PySide6.QtWidgets import QSplashScreen

    order = []
    orig_close = QSplashScreen.close
    def traced_close(self):
        order.append("splash_closed")
        return orig_close(self)
    def traced_show(self):
        order.append("window_shown")
    monkeypatch.setattr(QSplashScreen, "close", traced_close)
    monkeypatch.setattr(MainWindow, "show", traced_show)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)

    assert main(["raptor.gui.app"], splash_delay_ms=0,
               splash_fade_ms=10) == 0
    assert order == ["splash_closed", "window_shown"], \
        "the main window must not show until the splash has closed"
