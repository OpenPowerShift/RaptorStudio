"""Patch a name the canvas code looks up, wherever that code now lives.

`DiagramView` is assembled from mixins in several modules, and each looks a global
(``AddCmd``, ``plan_collapse``, ...) up in its own module. Setting it on
``raptor.gui.ui.diagram`` alone no longer reaches the code that moved, so tests that
record or break such a call patch it through here.
"""
from __future__ import annotations

import importlib
import pkgutil

import raptor.gui.ui as _ui


def _canvas_modules():
    for info in pkgutil.iter_modules(_ui.__path__):
        if info.name == "diagram" or (info.name.startswith("diagram_") and info.name.endswith("_mixin")):
            yield importlib.import_module(f"raptor.gui.ui.{info.name}")


def swap(name: str, value):
    """Replace ``name`` in every canvas module that has it; return the undo function."""
    saved = [(m, getattr(m, name)) for m in _canvas_modules() if hasattr(m, name)]
    if not saved:
        raise AttributeError(name)
    for m, _ in saved:
        setattr(m, name, value)

    def restore() -> None:
        for m, old in saved:
            setattr(m, name, old)
    return restore
