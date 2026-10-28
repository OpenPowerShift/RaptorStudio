"""Characterisation of `DiagramView.copy_selection` and `paste`.

For several selections (elements, hierarchy ovals with their subtrees,
events) on several fixtures and both levels, the golden records the paste
buffer after copying and every `AddCmd` two consecutive pastes push (the
real command runs, so the second paste sees the ids the first took).
Captured from the ORIGINAL implementation before the row planning moved into
the Qt-free `raptor.gui.clipboard_plan`. Regenerate on purpose with
`python tests/unit/test_clipboard_characterization.py --write`.
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

GOLDEN = REPO / "tests" / "unit" / "data" / "clipboard_golden.json"
MODELS = ["TwoBlockRedundancy", "ComplexModel", "HierarchyExample",
          "EventDepProbe", "Phasing3", "NestedHierarchyCost"]


def _selections(v):
    blocks = sorted(b for b in v.blocks if b)
    nodes = sorted(n for n, o in v.nodes.items() if o.type_node == 2)
    events = sorted(v.events)
    hiers = sorted(h for h in v.hiers
                   if h in v._hier_items)          # visible at this level
    sels = {}
    if blocks:
        sels["first_block"] = [("b", blocks[0])]
        sels["all_blocks"] = [("b", b) for b in blocks]
    if blocks and nodes:
        sels["blocks_nodes"] = ([("b", b) for b in blocks[:3]]
                                + [("n", n) for n in nodes[:2]])
    if events:
        sels["events"] = [("e", e) for e in events]
    for h in hiers[:2]:
        sels[f"hier_{h}"] = [("h", h)]
    if len(hiers) > 1:
        sels["all_hiers"] = [("h", h) for h in hiers]
    if hiers and blocks:
        sels["hier_and_block"] = [("h", hiers[0]), ("b", blocks[0])]
    return sels


def _select(v, sel):
    tabs = {"b": v._block_items, "n": v._node_items, "e": v._event_items,
            "h": v._hier_items}
    v._scene.clearSelection()
    for kind, oid in sel:
        tabs[kind][oid].setSelected(True)


def observe() -> dict:
    from PySide6.QtWidgets import QApplication
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui import diagram
    QApplication.instance() or QApplication([])
    captured: list = []
    RealAdd = diagram.AddCmd

    class RecAdd(RealAdd):
        def __init__(self, view, kind, rows, *a, **k):
            captured.append([kind, plain(rows)])
            super().__init__(view, kind, rows, *a, **k)

    diagram.AddCmd = RecAdd
    out: dict = {}
    try:
        for m in MODELS:
            p = str(REPO / "tests" / "golden" / m / "input.rbd")
            probe = diagram.DiagramView()
            probe.load_rbd(load_rbd(p))
            for where in ("home", "level"):
                if where == "level" and not probe.hiers:
                    continue
                v0 = diagram.DiagramView()
                v0.load_rbd(load_rbd(p))
                if where == "level":
                    v0.enter_level(sorted(v0.hiers)[0])
                for name in _selections(v0):
                    v = diagram.DiagramView()
                    v.load_rbd(load_rbd(p))
                    if where == "level":
                        v.enter_level(sorted(v.hiers)[0])
                    _select(v, _selections(v)[name])
                    n_copied = v.copy_selection()
                    buf = plain(v._paste_buffer)
                    captured.clear()
                    n1 = v.paste(at_model=(120.0, 40.0))
                    first = list(captured)
                    captured.clear()
                    n2 = v.paste(at_model=(120.0, 40.0))
                    out[f"{m}:{name}:{where}"] = {
                        "copied": n_copied, "buffer": buf,
                        "paste1": [n1, first], "paste2": [n2,
                                                          list(captured)]}
        # nothing to paste / no model
        v = diagram.DiagramView()
        out["empty"] = {"copy": v.copy_selection(), "paste": v.paste()}
    finally:
        diagram.AddCmd = RealAdd
    return out


@pytest.mark.gui
def test_clipboard_unchanged(observed):
    assert_matches_golden(observed, GOLDEN)


@pytest.mark.gui
def test_golden_covers_the_branches(observed):
    assert len(observed) >= 25
    kinds = {k for e in observed.values() if "buffer" in e
             for k, _ in e["buffer"]}
    assert {"block", "node", "event", "hier", "link"} <= kinds
    # a second paste takes fresh ids
    for name, e in observed.items():
        if "paste1" in e and e["paste1"][1] and e["paste2"][1]:
            assert e["paste1"][1] != e["paste2"][1], name
            break


if __name__ == "__main__":
    regenerate(GOLDEN, observe)
