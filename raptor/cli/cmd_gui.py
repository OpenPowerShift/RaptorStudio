"""`raptor gui` (also what a bare `raptor` does)."""
from __future__ import annotations

import os
import sys
from typing import Any

from raptor.cli import exitcodes as X


def add_parser(sub: Any) -> None:
    p = sub.add_parser(
        "gui", help="open the desktop application (the default)",
        description="Open the desktop application, optionally with MODEL. "
                    "This is also what 'raptor' with no command does.")
    p.add_argument("model", nargs="?", metavar="MODEL",
                   help=".rbd or .rbds model to open")
    p.add_argument("--new", action="store_true",
                   help="start with a new blank model")
    p.add_argument("--theme", choices=("light", "dark"),
                   help="set the theme (saved)")
    p.set_defaults(func=gui_command)


def _have_display() -> bool:
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY")
                    or os.environ.get("WAYLAND_DISPLAY")
                    or os.environ.get("QT_QPA_PLATFORM"))
    return True


def gui_command(args: Any) -> int:
    if not _have_display():
        print("raptor: no display available, so the desktop application "
              "cannot open.\nUse 'raptor run MODEL' to simulate from the "
              "command line, or 'raptor --help'.", file=sys.stderr)
        return X.USAGE
    if args.model and not os.path.isfile(args.model):
        print(f"raptor: no such model file: {args.model}", file=sys.stderr)
        return X.USAGE
    from raptor.gui.app import main as gui_main
    argv = ["raptor"]
    if args.model:
        argv.append(args.model)
    if args.new:
        argv.append("--new")
    if args.theme:
        argv += ["--theme", args.theme]
    if os.environ.get("RAPTOR_DEBUG"):
        argv.append("--debug")
    return int(gui_main(argv) or 0)
