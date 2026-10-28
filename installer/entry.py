"""PyInstaller entry point for the RAPTOR Studio installers.

Frozen-app glue only: multiprocessing/WebEngine need freeze_support on
Windows, and the bundled GTK runtime (Windows-only, for WeasyPrint's
PDF export) must be on the DLL search path BEFORE weasyprint is
imported -- the app imports it lazily, so registering the directory
here is sufficient.
"""
import multiprocessing
import os
import sys


def _prep_frozen_env() -> None:
    if not getattr(sys, "frozen", False):
        return
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return
    if sys.platform == "win32":
        gtk = os.path.join(meipass, "_gtk")
        if os.path.isdir(gtk):
            os.environ["PATH"] = gtk + os.pathsep + os.environ.get("PATH", "")
            try:
                os.add_dll_directory(gtk)
            except (AttributeError, OSError):
                pass


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _prep_frozen_env()
    from raptor.gui.app import main
    raise SystemExit(main())
