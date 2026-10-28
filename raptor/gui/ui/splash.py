"""Startup splash screen and the application/window icon.

`raptor_logo.png` and `raptor_icon.png` live alongside this module. The
splash is shown as a `QSplashScreen` immediately on launch, stays up
while `MainWindow` constructs, then dissolves and closes to reveal the
main window underneath rather than a blank frame.

The fade repaints the pixmap itself, blending it toward its own corner
colour frame by frame, rather than using `QWidget.setWindowOpacity`:
some platform plugins refuse window-level opacity outright. Pixmap
blending is pure Qt software compositing and works regardless of the
window manager; the logo is a fully opaque square, so there is no
transparency to preserve.

Startup ordering: the main window is not shown until the splash has
fully faded and closed (`run_splash_then`, used by `app.main()`). Some
window managers do not honour "stays on top" against a window that maps
after it, so never showing a second top-level window while the splash
is still up avoids the reordering entirely.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import (Qt, QEventLoop, QTimer, QVariantAnimation,
                            QEasingCurve)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QSplashScreen

#: Native asset is 1254x1254 (a source-quality square logo, not pre-
#: sized for display) -- scaled down here to a normal splash footprint.
_DISPLAY_SIZE = 320
_RESOURCES = Path(__file__).resolve().parent.parent / "resources"
LOGO_PATH = _RESOURCES / "raptor_logo.png"
ICON_PATH = _RESOURCES / "raptor_icon.png"

#: This release's codename. Bump alongside `raptor.__version__` on
#: future releases.
RELEASE_CODENAME = "Living in the Experiment"

#: How long the splash sits before fading, and how long the fade takes.
#: Module-level (not just default args) so `app.main()` can read the
#: CURRENT value at call time -- tests monkeypatch these to keep the
#: startup sequence fast without changing `main()`'s own signature.
DEFAULT_DELAY_MS = 2000
DEFAULT_FADE_MS = 350


def app_icon(icon_path: Path | str | None = None) -> QIcon:
    """The window/taskbar icon. `QIcon` handles a missing or unreadable
    file the same way `show_splash` does for the logo: silently empty
    rather than raising, so a bad asset never blocks startup."""
    return QIcon(str(icon_path or ICON_PATH))


def _draw_version_overlay(pm: QPixmap, version: str, codename: str) -> QPixmap:
    """Bake "vX.Y.Z — Codename" into the pixmap in white, centred
    horizontally, in the band between the logo's bottom margin and the
    image's true bottom edge. It is drawn into the pixmap itself rather
    than a separate overlay widget, so it fades out together with the
    logo -- the same reason the fade animates the pixmap."""
    out = QPixmap(pm)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setPen(QColor("white"))
    font = QFont("sans-serif", max(7, round(out.height() * 0.026)))
    p.setFont(font)
    text = f"v{version} — {codename}"
    band_h = round(out.height() * 0.11)
    p.drawText(0, out.height() - band_h, out.width(), band_h,
              Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, text)
    p.end()
    return out


def show_splash(logo_path: Path | str | None = None,
                version: str | None = None,
                codename: str = RELEASE_CODENAME) -> QSplashScreen | None:
    """Show the splash immediately, returns the screen (or None if the
    logo asset is missing/unreadable -- a missing splash image is not
    worth blocking startup over)."""
    pm = QPixmap(str(logo_path or LOGO_PATH))
    if pm.isNull():
        return None
    pm = pm.scaled(_DISPLAY_SIZE, _DISPLAY_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                   Qt.TransformationMode.SmoothTransformation)
    if version is None:
        from raptor import __version__ as version
    pm = _draw_version_overlay(pm, str(version), codename or "")
    splash = QSplashScreen(pm, Qt.WindowType.WindowStaysOnTopHint)
    splash.show()
    return splash


def _corner_colour(pm: QPixmap) -> QColor:
    image = pm.toImage()
    return QColor(image.pixelColor(0, 0)) if not image.isNull() else QColor("black")


def render_fade_frame(base: QPixmap, fade_colour: QColor, t: float) -> QPixmap:
    """One frame of the dissolve, `t` in [0, 1] (0 = the original
    image, 1 = fully replaced by `fade_colour`), via
    `QPainter.setOpacity`."""
    frame = QPixmap(base)
    p = QPainter(frame)
    p.setOpacity(t)
    p.fillRect(frame.rect(), fade_colour)
    p.end()
    return frame


def fade_out_and_close(splash: QSplashScreen, delay_ms: int = 2000,
                       duration_ms: int = 350, on_finished=None) -> None:
    """After `delay_ms` showing the logo as-is, dissolve it into its
    own corner colour over `duration_ms` and close. The delay is a
    parameter so tests can drive it at delay_ms=0 without a real wait.
    `on_finished`, if given, runs right after the splash closes;
    `app.main()` uses it to delay showing the main window until the
    splash is fully gone (see `run_splash_then`)."""
    base = splash.pixmap()
    fade_colour = _corner_colour(base)

    def start_fade():
        animation = QVariantAnimation(splash)
        animation.setDuration(duration_ms)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.InCubic)
        animation.valueChanged.connect(
            lambda t: splash.setPixmap(render_fade_frame(base, fade_colour, t)))
        def _done():
            splash.close()
            if on_finished is not None:
                on_finished()
        animation.finished.connect(_done)
        # Keep `anim` alive through both C++ ownership and an explicit
        # Python reference, as a guard against it being GC'd mid-run.
        cast(Any, splash)._fade_anim = animation
        animation.start()
    QTimer.singleShot(delay_ms, start_fade)


def run_splash_then(splash: QSplashScreen, on_done, delay_ms: int | None = None,
                    duration_ms: int | None = None) -> None:
    """Blocks (via a private `QEventLoop`, NOT `app.exec()` -- so this
    behaves identically whether called from `main()` or from a test
    with `QApplication.exec` stubbed out) until the splash has shown
    for `delay_ms` and faded out, THEN calls `on_done()` (`MainWindow.
    show`, from `app.main()`). See this module's own docstring for why
    the main window's first appearance is ordered strictly after the
    splash disappears, not the other way around."""
    if delay_ms is None:
        delay_ms = DEFAULT_DELAY_MS
    if duration_ms is None:
        duration_ms = DEFAULT_FADE_MS
    loop = QEventLoop()

    def _finished():
        on_done()
        loop.quit()
    fade_out_and_close(splash, delay_ms=delay_ms, duration_ms=duration_ms,
                       on_finished=_finished)
    loop.exec()
