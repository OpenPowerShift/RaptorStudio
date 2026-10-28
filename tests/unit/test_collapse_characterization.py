"""Characterisation of `DiagramView.collapse_selection` and the row builders
it uses (`_build_hierarchy_rows`, `_ensure_hier_section`).

For several selections on several fixtures -- with and without an existing
HIERARCHY_DATA section -- the golden records what `CollapseCmd` receives,
the returned id, and the file's section order afterwards. Captured from the
ORIGINAL implementation before the planning moved into the Qt-free
`raptor.gui.hierarchy_plan` / `raptor.gui.row_ops`. Regenerate on purpose
with `python tests/unit/test_collapse_characterization.py --write`.
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

GOLDEN = REPO / "tests" / "unit" / "data" / "collapse_golden.json"
MODELS = ["TwoBlockRedundancy", "ComplexModel", "SingleHierarchy",
          "HierarchyExample", "EventDepProbe", "Phasing3", "Basic1_Completed"]


def _selections(v):
    blocks = sorted(b for b in v.blocks if b)
    nodes = sorted(n for n, o in v.nodes.items() if o.type_node == 2)
    events = sorted(v.events)
    out = {}
    if blocks:
        out["first_block"] = [("b", blocks[0])]
        out["two_blocks"] = [("b", b) for b in blocks[:2]]
        out["all_blocks"] = [("b", b) for b in blocks]
    if blocks and nodes:
        out["blocks_and_nodes"] = ([("b", b) for b in blocks[:3]]
                                   + [("n", n) for n in nodes[:2]])
    if events:
        out["events"] = [("e", e) for e in events]
    return out


def observe() -> dict:
    from PySide6.QtGui import QUndoCommand
    from PySide6.QtWidgets import QApplication
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui import diagram
    QApplication.instance() or QApplication([])
    captured: list = []

    class RecCmd(QUndoCommand):
        def __init__(self, view, *args, **kw):
            super().__init__("rec")
            captured.append(plain([list(args), kw]))

    real = diagram.CollapseCmd
    diagram.CollapseCmd = RecCmd
    # A selection a hierarchy cannot hold (entered or left by more than one
    # link, Raptor 7's CheckValidLink) is refused with a message box. This
    # runs in a module fixture, before the per-test modal guard exists, so
    # the box is recorded here rather than opened.
    from raptor.gui.ui import msgbox
    refused: list = []
    real_warning = msgbox.warning
    msgbox.warning = lambda *a, **k: refused.append(a[2] if len(a) > 2 else "")
    out: dict = {}
    try:
        for m in MODELS:
            p = REPO / "tests" / "golden" / m / "input.rbd"
            if not p.exists():
                continue
            names = None
            v0 = diagram.DiagramView()
            v0.load_rbd(load_rbd(str(p)))
            names = list(_selections(v0))
            for sel_name in names:
                for cur in ["home"] + (["level"] if v0.hiers else []):
                    v = diagram.DiagramView()
                    v.load_rbd(load_rbd(str(p)))
                    if cur == "level":
                        v.enter_level(sorted(v.hiers)[0])
                    sel = _selections(v).get(sel_name)
                    if not sel:
                        continue
                    v._scene.clearSelection()
                    tabs = {"b": v._block_items, "n": v._node_items,
                            "e": v._event_items}
                    for kind, oid in sel:
                        tabs[kind][oid].setSelected(True)
                    captured.clear()
                    refused.clear()
                    hid = v.collapse_selection()
                    out[f"{m}:{sel_name}:{cur}"] = {
                        "hid": hid, "cmd": list(captured), "refused": bool(refused),
                        "sections": list(v._rbd_ref.sections),
                        "hier_rows_now": len(
                            v._rbd_ref.sections["HIERARCHY_DATA"].rows)
                        if "HIERARCHY_DATA" in v._rbd_ref.sections else None}
    finally:
        diagram.CollapseCmd = real
        msgbox.warning = real_warning
    return out


@pytest.mark.gui
def test_collapse_unchanged(observed):
    assert_matches_golden(observed, GOLDEN)


@pytest.mark.gui
def test_golden_covers_the_branches(observed):
    ops = [op[0] for e in observed.values() for c in e["cmd"]
           for op in c[0][2]]
    assert {"del", "add"} <= set(ops)
    assert len(observed) >= 15
    # Every case either collapsed or was refused with a message, never
    # silently did nothing; and both outcomes are covered.
    assert all((e["hid"] is None) == e["refused"] for e in observed.values())
    assert any(e["refused"] for e in observed.values())
    assert sum(1 for e in observed.values() if e["hid"] is not None) >= 10
    assert any("HIERARCHY_DATA" in e["sections"] for e in observed.values())


if __name__ == "__main__":
    regenerate(GOLDEN, observe)
