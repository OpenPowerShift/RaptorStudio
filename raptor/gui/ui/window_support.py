"""Small helpers shared by the main window and its mixins."""
from __future__ import annotations

import gc
import threading

from PySide6.QtCore import QObject
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


class _OverlayKeeper(QObject):
    """Keeps the File view on top while it is open. Docks raise themselves
    in many places (a run finishing raises Results), and a raised dock sat
    over the File view; this puts the view back above any dock or the
    canvas that comes forward or appears."""

    def __init__(self, window):
        super().__init__(window)
        self._win = window

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.Type.ZOrderChange, QEvent.Type.Show):
            bs = getattr(self._win, "_backstage", None)
            if bs is not None and bs.isVisible() and obj is not bs:
                bs.raise_()
        return False


def _live_state_signature(blocks, cap_data, model, system_state=None) -> tuple:
    """Fingerprint of everything the live canvas paints.

    The animated run must repaint when something actually changed, but a
    3,470-block model spends its first ~6,900 scheduler steps at t=0
    (block kickoff) with an identical picture. Repainting those frames
    looked like a frozen run and cost a full frame each. Compare this
    first; skip only the canvas repaint (status text and event-loop
    pumping still run every tick).

    Only painted state goes in: block op/active status and phasing,
    node/hierarchy/event status, and the live flow/capacity dicts.
    Link states are derived from block/node changes in the same event,
    so they do not need their own 5,184-entry scan.
    """
    if isinstance(blocks, dict):
        blocks = blocks.values()
    sig: list = [getattr(system_state, "color", None),
                 getattr(system_state, "current_phase", None)]
    for b in blocks:
        run_state = getattr(b, "run_state", None)
        sig.append((getattr(b, "op_status", None),
                    getattr(b, "active_status", None),
                    getattr(run_state, "fev_induced_by_dependency", False),
                    (getattr(run_state, "phase_stress", None)
                     if getattr(b, "uses_phasing", False) else None)))
    if model is not None:
        if isinstance(model, dict):
            # Plain per-status maps from the background worker.
            for key in ("nodes", "hiers", "events"):
                sig.append(tuple(sorted((model.get(key) or {}).items())))
        else:
            sig.append(tuple(n.status for n in model.nodes_by_id.values()))
            sig.append(tuple(
                (h.status, h.dsi_state, h.num_currently_failed,
                 h.dsi_yellows)
                for h in model.hiers_by_id.values()))
            sig.append(tuple((e.op_status, e.active_status)
                             for e in model.events_by_id.values()))
    if cap_data is not None:
        sig.append(tuple(cap_data[0].items()))
        sig.append(tuple(cap_data[1].items()))
    return tuple(sig)
