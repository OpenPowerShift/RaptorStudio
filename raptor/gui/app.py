"""RAPTOR Studio application entry point.

Creates QApplication, sets organisation metadata, constructs and shows the
main window, and enters the Qt event loop.
"""
from __future__ import annotations

from raptor.gui.ui import msgbox
import argparse
import faulthandler
import logging
import os
import signal
import sys
import tempfile
from typing import cast
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from raptor.gui.ui.main_window import MainWindow

#: Fixed path for the --debug log. A fixed location survives a crashed
#: terminal, so the log can still be read afterwards.
DEBUG_LOG_PATH = Path(tempfile.gettempdir()) / "raptor_studio_debug.log"


def _configure_debug_tracing() -> None:
    """Enable verbose load/render tracing to stderr and to a fixed log
    file, plus a native-crash backtrace (`--debug` or `RAPTOR_GUI_DEBUG`).

    A segfault happens inside Qt's C++ code and never raises a Python
    exception, so try/except cannot catch it. `faulthandler` installs a
    signal handler that prints the Python stack active at the crash,
    and the DEBUG-level trace calls along diagram.py's model-loading
    path record which element was being built.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(logging.FileHandler(DEBUG_LOG_PATH, mode="w"))
    except OSError:
        pass   # stderr alone still works
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s.%(msecs)03d %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
    # file=None -> faulthandler's OWN fd, which survives after Python's
    # logging handlers might not get a chance to flush during a hard
    # native crash (a raw OS-level write, not routed through logging).
    fault_fd = open(DEBUG_LOG_PATH, "a")  # noqa: SIM115 -- faulthandler writes to it until exit
    faulthandler.enable(file=fault_fd, all_threads=True)
    log = logging.getLogger("raptor.gui")
    log.debug("debug tracing enabled -- log file: %s", DEBUG_LOG_PATH)


def _install_excepthook() -> None:
    """Surface uncaught exceptions instead of silently crossing the Qt
    slot boundary. Without this a failure in any slot (e.g. a PDF export
    or a deferred callback) printed only to a possibly-invisible stderr
    and PySide6 could abort the process, taking the unsaved model with
    it."""
    original = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        original(exc_type, exc_value, exc_tb)
        try:
            import traceback
            detail = "".join(
                traceback.format_exception(exc_type, exc_value, exc_tb))
            logging.getLogger("raptor.gui").error(
                "uncaught exception:\n%s", detail)
            from PySide6.QtWidgets import QApplication
            if QApplication.instance() is not None:
                msgbox.critical(
                    None, "Unexpected Error",
                    "RAPTOR Studio hit an unexpected error:\n\n"
                    f"{exc_type.__name__}: {exc_value}\n\n"
                    "Your model may not be saved. This has been written "
                    "to the log; consider saving to a new file.")
        except Exception:
            # Never let the handler itself raise -- that would recurse
            # through the excepthook.
            pass

    sys.excepthook = _hook


def _schedule_startup_actions(window, args) -> None:
    """Open the model / new model named on the command line, once the event
    loop is running (so any dialog it raises has a window to belong to)."""
    if args.theme:
        try:
            from raptor.gui.ui import settings as _settings
            _settings.set_value("appearance/dark_mode", args.theme == "dark")
        except Exception:                       # noqa: BLE001
            logging.getLogger("raptor.gui").warning(
                "could not apply --theme %s", args.theme)
    if args.model:
        path = os.path.abspath(args.model)
        QTimer.singleShot(0, lambda: window._open_path(path))
    elif args.new:
        QTimer.singleShot(0, window._new_file)


def main(argv: list[str] | None = None, splash_delay_ms: int | None = None,
        splash_fade_ms: int | None = None) -> int:
    if argv is None:
        argv = sys.argv
    parser = argparse.ArgumentParser(prog="raptor.gui.app", add_help=False)
    parser.add_argument(
        "--debug", action="store_true",
        help="verbose model-load/render tracing + a native-crash "
             f"backtrace on segfault, written to stderr and to "
             f"{DEBUG_LOG_PATH} (also settable via RAPTOR_GUI_DEBUG=1)")
    parser.add_argument(
        "--mcp", action="store_true", default=False,
        help="Start the MCP server instead of the GUI (no Qt required).")
    parser.add_argument(
        "model", nargs="?", default=None,
        help="a .rbd or .rbds model to open at start-up")
    parser.add_argument(
        "--new", action="store_true", default=False,
        help="start with a new blank model")
    parser.add_argument(
        "--theme", choices=("light", "dark"), default=None,
        help="set the theme (saved, like the View menu choice)")
    args, qt_argv = parser.parse_known_args(argv[1:])

    # --mcp: skip all Qt setup and hand off to the MCP server
    if args.mcp:
        from raptor.mcp.server import main as mcp_main
        return mcp_main([a for a in argv[1:] if a != "--mcp"])

    if args.debug or os.environ.get("RAPTOR_GUI_DEBUG"):
        _configure_debug_tracing()

    # QApplication is a process-wide singleton -- reuse one if it
    # already exists (a test harness's own `qapp` fixture, or `main()`
    # somehow called twice) instead of Qt raising on a second
    # construction attempt.
    app = cast(QApplication, QApplication.instance() or QApplication([argv[0], *qt_argv]))
    # Force Fusion: the platform's own style paints native selected-tab
    # corner "arcs" and scroller-arrow bitmaps that ignore the stylesheet
    # and palette. The design is tuned against Fusion, so this keeps all
    # platforms consistent.
    from PySide6.QtWidgets import QStyleFactory
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setOrganizationName("OpenPowerShift")
    app.setApplicationName("RAPTOR Studio")
    _install_excepthook()
    # A native crash (segfault) prints the Python stack of every thread
    # to the terminal, always -- not only under --debug -- so a report of
    # "core dumped" comes with where it happened.
    if not faulthandler.is_enabled():
        faulthandler.enable(all_threads=True)

    # Qt's C++ event loop never lets the Python interpreter run while
    # blocked in `app.exec()`, so Python's usual SIGINT check never
    # happens. A periodic no-op timer returns control to Python often
    # enough for the signal to be delivered, so Ctrl+C closes the app.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sigint_pump = QTimer()
    sigint_pump.timeout.connect(lambda: None)
    sigint_pump.start(200)

    from raptor.gui.ui.splash import app_icon, show_splash, run_splash_then
    app.setWindowIcon(app_icon())
    splash = show_splash()
    if splash is not None:
        app.processEvents()   # paint the splash before MainWindow's
                              # (possibly slow) construction blocks it

    window = MainWindow()
    window.enable_layout_persistence()
    _schedule_startup_actions(window, args)
    def show_window() -> None:
        window.show()
        # Nothing named on the command line: open on the start page (new,
        # recent, examples, help), not an empty canvas.
        if not (args.model or args.new):
            window.show_start_page()

    if splash is not None:
        # Delay the main window's first appearance until the splash has
        # fully faded and closed -- see splash.py's module docstring.
        run_splash_then(splash, show_window, splash_delay_ms,
                        splash_fade_ms)
    else:
        show_window()

    try:
        return app.exec()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
