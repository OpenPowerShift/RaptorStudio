"""Small helpers shared by the main window and its mixins."""
from __future__ import annotations

import gc
import threading

from PySide6.QtWidgets import QWidget


#: Cyclic-GC suppression shared by the background-run and Terminate
#: finalize workers. A worker thread must not collect Qt wrappers owned by
#: the GUI thread. Disable/enable is reference-counted so one worker's
#: exit cannot re-enable collection while another is still running.
_GC_SUPPRESS_LOCK = threading.Lock()
_GC_SUPPRESS_USERS = 0
_GC_SUPPRESS_RESTORE = False


def _suppress_cyclic_gc() -> None:
    """Disable cyclic GC for a worker; safe when workers overlap."""
    global _GC_SUPPRESS_USERS, _GC_SUPPRESS_RESTORE
    with _GC_SUPPRESS_LOCK:
        if _GC_SUPPRESS_USERS == 0:
            _GC_SUPPRESS_RESTORE = gc.isenabled()
            if _GC_SUPPRESS_RESTORE:
                gc.disable()
        _GC_SUPPRESS_USERS += 1


def _resume_cyclic_gc() -> None:
    """Release one worker's suppression; the last one restores GC."""
    global _GC_SUPPRESS_USERS
    with _GC_SUPPRESS_LOCK:
        if _GC_SUPPRESS_USERS == 0:
            return
        _GC_SUPPRESS_USERS -= 1
        if _GC_SUPPRESS_USERS == 0 and _GC_SUPPRESS_RESTORE:
            gc.enable()


class _NoMinimumWidth(QWidget):
    """A container that asks for its natural width but sets no minimum, so
    what it holds does not stop the window getting narrower."""

    def minimumSizeHint(self):                            # noqa: N802
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint
