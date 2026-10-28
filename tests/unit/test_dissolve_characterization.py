"""Characterisation of `DiagramView._dissolve_hierarchies`' planning.

The golden records what the method hands `DissolveCmd` (rows to remove,
parent and link rewrites, boundary rewiring) plus the status message, for
every hierarchy of every golden fixture that has one, captured from the
ORIGINAL inline implementation before the planning moved into the Qt-free
`raptor.gui.hierarchy_plan`. Regenerate on purpose with
`python tests/unit/test_dissolve_characterization.py --write`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Direct execution (`python tests/unit/<this file> --write`) puts the
# script's own directory on sys.path, not the repository root; add the
# root so the shared `tests.unit.golden_support` import resolves both
# under pytest and as a script.
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.unit.golden_support import (
    assert_matches_golden, plain, regenerate)

GOLDEN = REPO / "tests" / "unit" / "data" / "dissolve_golden.json"


def observe() -> dict:
    from PySide6.QtWidgets import QApplication
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui import diagram
    QApplication.instance() or QApplication([])
    captured: list = []

    class Rec:
        def __init__(self, view, *args):
            captured.append(plain(list(args)))
        # QUndoStack.push needs a real command; the recorder is a no-op one
    from PySide6.QtGui import QUndoCommand

    class RecCmd(QUndoCommand):
        def __init__(self, view, *args):
            super().__init__("rec")
            captured.append(plain(list(args)))

    real = diagram.DissolveCmd
    diagram.DissolveCmd = RecCmd
    out: dict = {}
    try:
        for p in sorted((REPO / "tests" / "golden").glob("*/input.rbd")):
            rbd = load_rbd(str(p))
            v = diagram.DiagramView()
            v.load_rbd(rbd)
            hids = sorted(v.hiers)
            if not hids:
                continue
            cases = [[h] for h in hids]
            if len(hids) > 1:
                cases.append(hids)
            for case in cases:
                # fresh view per case: the recorder does not apply the edit
                captured.clear()
                msgs: list = []
                v.status_message_sink = msgs.append
                v._dissolve_hierarchies(list(case))
                out[f"{p.parent.name}:{case}"] = {
                    "args": list(captured), "status": list(msgs)}
    finally:
        diagram.DissolveCmd = real
    return out


@pytest.mark.gui
def test_dissolve_plan_unchanged(observed):
    # Fixtures added since the golden was captured are not in it; every one
    # that WAS recorded must still be there and unchanged.
    assert_matches_golden(observed, GOLDEN, subset=True)


@pytest.mark.gui
def test_the_golden_exercises_the_interesting_paths(observed):
    args = [a for e in observed.values() for a in e["args"]]
    assert len(observed) >= 10
    # (hier_row, markers, removed_links, parent_rewrites, link_pid_rewrites,
    #  rewired_links)
    assert any(a[2] for a in args), "no removed links anywhere"
    assert any(a[3] for a in args), "no parent_rewrites anywhere"
    assert any(a[4] for a in args), "no link_pid_rewrites anywhere"
    assert any(a[5] for a in args), "no rewired boundary links anywhere"


if __name__ == "__main__":
    regenerate(GOLDEN, observe)
