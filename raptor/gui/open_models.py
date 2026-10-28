"""The open windows, one model each.

RAPTOR Studio opens a second model in a second window rather than in tabs:
every dock (Explorer, Inspector, Data Tables, Results, Run) shows one
model, and a window is the unit that already owns all of them, its undo
stack and its run. This registry is what the windows share: which ones are
open, so that Open can bring forward a file that is already open, and the
pickers that choose "another model" (Compare, Differences) can offer the
models open in the other windows, unsaved edits included.

No Qt types are needed here; a window is anything with `_rbd`,
`_current_file`, `_dirty` and `windowTitle()`.
"""
from __future__ import annotations

import os
import weakref
from dataclasses import dataclass
from typing import Any

#: Open windows, oldest first, weakly held: the test suite builds and drops
#: hundreds of windows, and the registry must not keep them alive.
_WINDOWS: list[weakref.ref] = []
#: Windows this module opened (`keep`). Nothing else owns them, so they are
#: held here until closed. The first window is held by `app.main`.
_KEPT: list[Any] = []
_SERIAL = [0]


def register(win: Any) -> int:
    """Add a window; returns its serial number (1 for the first)."""
    if win not in windows():
        _WINDOWS.append(weakref.ref(win))
    _SERIAL[0] += 1
    return _SERIAL[0]


def keep(win: Any) -> None:
    """Hold a window opened for a second model until it is closed."""
    if win not in _KEPT:
        _KEPT.append(win)


def unregister(win: Any) -> None:
    _WINDOWS[:] = [r for r in _WINDOWS if r() is not None and r() is not win]
    if win in _KEPT:
        _KEPT.remove(win)


def windows() -> list[Any]:
    """The open windows, oldest first."""
    from shiboken6 import isValid
    return [w for w in (r() for r in _WINDOWS)
            if w is not None and isValid(w)]


def window_for_file(path: str | None) -> Any | None:
    """The open window holding `path`, if any."""
    if not path:
        return None
    want = os.path.normcase(os.path.abspath(path))
    for w in windows():
        f = getattr(w, "_current_file", None)
        if f and os.path.normcase(os.path.abspath(f)) == want:
            return w
    return None


@dataclass
class OpenModel:
    """A model open in another window, for a picker."""
    label: str              # "Hospital.rbds" or "Untitled (window 2)"
    path: str | None        # its file, None when never saved
    dirty: bool
    window: Any

    def model(self) -> Any:
        """A copy of the model as that window shows it now, unsaved edits
        included."""
        return self.window._model_for_engine(copy=True)

    @property
    def description(self) -> str:
        state = ("open, unsaved" if self.path is None else
                 "open, with unsaved changes" if self.dirty else "open")
        return f"{self.label} — {state}"


def others(win: Any) -> list[OpenModel]:
    """The models open in every window but `win`."""
    out = []
    for i, w in enumerate(windows(), start=1):
        if w is win or getattr(w, "_rbd", None) is None:
            continue
        path = getattr(w, "_current_file", None)
        label = (os.path.basename(path) if path
                 else f"Untitled (window {i})")
        out.append(OpenModel(label, path, bool(getattr(w, "_dirty", False)),
                             w))
    return out
