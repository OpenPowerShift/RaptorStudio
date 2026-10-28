"""Characterisation of `DiagramView.import_rbd_as_hierarchy`.

The golden records every `AddCmd` the method pushes (kind and rows) and the
returned id, for several host/imported model pairs at the top level and
inside a hierarchy, plus the error for an import without exactly one
Start/End. Captured from the ORIGINAL implementation before the id
remapping moved into the Qt-free `raptor.gui.hierarchy_plan`. Regenerate on
purpose with `python tests/unit/test_import_hierarchy_characterization.py
--write`.
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

GOLDEN = REPO / "tests" / "unit" / "data" / "import_hierarchy_golden.json"
GOLD = REPO / "tests" / "golden"
HOSTS = ["TwoBlockRedundancy", "ComplexModel", "EventDepProbe"]
IMPORTS = ["TwoBlockRedundancy", "HierarchyExample", "EventDepProbe",
           "Phasing3", "ComplexModel"]


def observe() -> dict:
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QUndoCommand
    from PySide6.QtWidgets import QApplication
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui import diagram
    QApplication.instance() or QApplication([])
    captured: list = []

    class RecCmd(QUndoCommand):
        def __init__(self, view, kind, rows, *a, **k):
            super().__init__("rec")
            captured.append([kind, plain(rows)])

    real = diagram.AddCmd
    diagram.AddCmd = RecCmd
    out: dict = {}
    try:
        for host in HOSTS:
            for imp in IMPORTS:
                for where in ("home", "level"):
                    v = diagram.DiagramView()
                    v.load_rbd(load_rbd(str(GOLD / host / "input.rbd")))
                    if where == "level":
                        if not v.hiers:
                            continue
                        v.enter_level(sorted(v.hiers)[0])
                    v.mapToScene = lambda *_a: QPointF(400.0, 300.0)
                    captured.clear()
                    key = f"{host}<-{imp}:{where}"
                    try:
                        hid = v.import_rbd_as_hierarchy(
                            str(GOLD / imp / "input.rbd"), name="Imp One")
                        out[key] = {"hid": hid, "cmds": list(captured)}
                    except ValueError as e:
                        out[key] = {"error": str(e).replace(str(REPO), "")}
        # Malformed imports (patched in memory; no fixture is altered).
        import raptor.core.fileio.rbd as rbd_mod
        real_load = rbd_mod.load_rbd
        for label, tweak in (("no_end", "drop_end"), ("two_starts", "dup")):
            def patched(path, _t=tweak):
                r = real_load(path)
                rows = r.sections["NODE_DATA"].rows
                if _t == "drop_end":
                    r.sections["NODE_DATA"].rows = [
                        x for x in rows if x.get("Type") not in ("3", "3.0")
                        and int(float(x.get("Type", 0))) != 3]
                else:
                    st = next(x for x in rows
                              if int(float(x.get("Type", 0))) == 1)
                    rows.append({**st, "ID": "9999"})
                return r
            rbd_mod.load_rbd = patched
            v = diagram.DiagramView()
            v.load_rbd(real_load(str(GOLD / "TwoBlockRedundancy/input.rbd")))
            v.mapToScene = lambda *_a: QPointF(400.0, 300.0)
            captured.clear()
            try:
                v.import_rbd_as_hierarchy(
                    str(GOLD / "EventDepProbe" / "input.rbd"), name="x")
                out[f"error:{label}"] = {"hid": "no error?"}
            except ValueError as e:
                out[f"error:{label}"] = {
                    "error": str(e).replace(str(REPO), ""),
                    "pushed": list(captured)}
            finally:
                rbd_mod.load_rbd = real_load
    finally:
        diagram.AddCmd = real
    return out


@pytest.mark.gui
def test_import_unchanged(observed):
    assert_matches_golden(observed, GOLDEN)


@pytest.mark.gui
def test_golden_covers_success_and_error(observed):
    assert any("hid" in e for e in observed.values())
    assert any("error" in e for e in observed.values())
    kinds = {c[0] for e in observed.values() for c in e.get("cmds", [])}
    assert {"hier", "node", "block", "link"} <= kinds


if __name__ == "__main__":
    regenerate(GOLDEN, observe)
