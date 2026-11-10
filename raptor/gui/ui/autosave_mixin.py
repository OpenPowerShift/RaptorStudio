"""Autosave and crash recovery for the open model.

Expects on `self`: `_rbd`, `_current_file`, `_dirty`."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any


import os
from pathlib import Path

from raptor.core.fileio import rbds as rbds_io




if TYPE_CHECKING:
    # Mixin methods call Qt on `self`; type-check them as a main window.
    from PySide6.QtWidgets import QMainWindow as _Base
    import PySide6.QtCore
    import PySide6.QtWidgets
    import raptor.gui.ui.diagram
    import raptor.gui.ui.inspector
else:
    _Base = object


class AutosaveMixin(_Base):
    """Autosave and crash recovery for the open model."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        _autosave_timer: PySide6.QtCore.QTimer
        _diagram_dock: raptor.gui.ui.diagram.DiagramDock
        _dirty: bool
        _inspector: raptor.gui.ui.inspector.InspectorDock
        def _load_into_app(self, rbd: Any, path: Any) -> Any: ...
        def _open_model_any(self, path: str) -> Any: ...
        def _prefs(self) -> dict: ...
        _rbd: Any  # really raptor.core.fileio.rbd.RbdFile | None
        def _spawn_window(self) -> Any: ...
        _statusbar: PySide6.QtWidgets.QStatusBar

    def _apply_autosave_timer(self):
        prefs = self._prefs()
        if prefs["autosave"]:
            self._autosave_timer.start(prefs["autosave_min"] * 60_000)
        else:
            self._autosave_timer.stop()

    def _recovery_path(self):
        import tempfile
        # `.rbds` (not the old `.rbd`): autosave must never silently drop
        # annotations, formulas/rates or the display-unit preference the
        # legacy format cannot carry. The extension also selects
        # `rbds_io` in `_open_model_any`.
        # One copy per window, or two windows would overwrite each
        # other's, and closing one would delete the other's.
        n = getattr(self, "_window_serial", 1)
        name = ("unsaved_recovery.rbds" if n <= 1
                else f"unsaved_recovery-{n}.rbds")
        return str(Path(tempfile.gettempdir())
                   / "raptor_studio_recovery" / name)

    def _clear_recovery(self):
        """Delete the rolling recovery copy on a clean exit (the original
        deletes its crash file on clean shutdown; a surviving copy is the
        signal that the previous session crashed)."""
        try:
            os.remove(self._recovery_path())
        except OSError:
            pass

    def _check_recovery(self):
        """Offer to open the recovery copies left by a previous session's
        crash. One is written per window for every dirty model (saved or
        not) and deleted when that window closes cleanly, so a surviving
        copy means a crash. The first window asks once for all of them;
        each opens in a window of its own."""
        if getattr(self, "_window_serial", 1) > 1 or \
                getattr(self, "_rbd", None) is not None:
            return
        # A load is in progress (its indicator pumps the event loop, so
        # this 600 ms startup timer can come due mid-load, before the new
        # model is assigned): the user is already opening a model, and
        # asking about crash copies on top of it would be noise.
        if getattr(self, "_loading_model", False):
            return
        folder = Path(self._recovery_path()).parent
        try:
            paths = sorted(str(p) for p in folder.glob("unsaved_recovery*.rbds"))
        except OSError:
            return
        if not paths:
            return
        from raptor.gui.ui.dialogs import question_box
        many = len(paths) > 1
        if not question_box(
                self, "Recovery Copy Found",
                (f"{len(paths)} auto-saved recovery copies from a previous "
                 f"session exist.\n\nOpen them now?\n\n" if many else
                 "An auto-saved recovery copy from a previous session "
                 "exists.\n\nOpen it now?\n\n") + "\n".join(paths),
                yes_text="Open recovery cop" + ("ies" if many else "y"),
                no_text="Not now", default_yes=True):
            return
        for path in paths:
            try:
                rbd = self._open_model_any(path)
            except Exception:
                continue
            target = self if self._rbd is None else self._spawn_window()
            target._load_into_app(rbd, None)
            target._statusbar.showMessage(
                "Recovered unsaved model — Save As to keep it")

    def _autosave_tick(self):
        """Write a separate rolling recovery copy -- only when dirty.
        Deliberately never touches the user's own file, matching the
        Raptor 7, whose autosave went to a dedicated crash file so
        "close without saving" always worked. Routing through the .rbds
        writer and a recovery path avoids overwriting an open .rbds with
        legacy .rbd text, mutating the live model in place, and dropping
        annotations."""
        if self._rbd is None:
            return
        if getattr(self, "_loading_model", False):
            # A load is replacing the model; the old one's recovery copy
            # is not this session's unsaved work.
            return
        if getattr(self, "_run_in_progress", False):
            # Never write back into a model a live engine is reading.
            return
        # A pending Comment draft is not in `_dirty` yet. Flush it BEFORE
        # deciding there is nothing to save, or a clean model with only a
        # just-typed comment would leave no recovery copy at all.
        self._inspector.flush_pending_comment()
        if not self._dirty:
            return
        try:
            # Recovery must include a comment still inside its debounce
            # window, same rule as `_write_model`.
            self._diagram_dock.view.write_back_to_rbd(self._rbd)
            # Mandatory or deleted annotations would survive in the
            # recovery file (same rule as `_write_model`).
            self._diagram_dock.view.write_back_annotations()
            path = self._recovery_path()
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            rbds_io.save_rbds(self._rbd, path)
            self._statusbar.showMessage(
                f"Auto-saved recovery copy — {path} "
                f"(restored automatically after a crash)", 8000)
        except Exception:
            pass  # auto-save must never crash the session
