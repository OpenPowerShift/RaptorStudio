"""A private per-user folder for the application's own state.

Recovery copies and the --debug log used to live in the shared temporary
directory under fixed names. On a multi-user machine anyone could plant a
file there for the next session to offer as "your recovery copy", or a
symlink for it to write through, and the copies were readable by all. This
folder is in the user's own profile and made private (0700) on creation.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def state_dir(*parts: str, create: bool = True) -> Path:
    """`<user state dir>/raptor-studio/<parts>`, created private unless
    `create` is false."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    folder = base.joinpath("raptor-studio", *parts)
    if not create:
        return folder
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        try:
            os.chmod(folder, 0o700)
        except OSError:
            pass
    return folder
