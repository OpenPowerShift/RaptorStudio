"""Characterisation of `DiagramView.write_back_to_rbd`.

The golden records, per model and scenario, a digest of every row of every
section after the write-back plus the cells that changed from the file as
loaded. Captured from the ORIGINAL implementation before the row-writing
moved into the Qt-free `model_edit.write_rows`. Regenerate on purpose with
`python tests/unit/test_write_back_characterization.py --write`.
"""
from __future__ import annotations

import hashlib
import json
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

from tests.unit.golden_support import regenerate

GOLDEN = REPO / "tests" / "unit" / "data" / "write_back_golden.json"
MODELS = ["ComplexModel", "EventDepProbe", "TwoBlockRedundancy", "Phasing3"]


def _dump(rbd) -> dict:
    return {name: [dict(r) for r in sec.rows]
            for name, sec in rbd.sections.items()}


def _digest(d) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()


def _changed(before: dict, after: dict) -> list[str]:
    out = []
    for sec, rows in after.items():
        for i, row in enumerate(rows):
            b = before.get(sec, [])[i] if i < len(before.get(sec, [])) else {}
            for k, v in row.items():
                if b.get(k) != v:
                    out.append(f"{sec}[{i}].{k}={v}")
    return sorted(out)


def _load(model):
    from PySide6.QtWidgets import QApplication
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    p = str(REPO / "tests" / "golden" / model / "input.rbd")
    win = MainWindow()
    win._load_into_app(load_rbd(p), p)
    app.processEvents()
    return win, win._diagram_dock.view


def observe() -> dict:
    out: dict = {}
    for model in MODELS:
        # A: untouched. B: edited node fields, a rename, a moved event/hier.
        for scenario in ("untouched", "edited"):
            win, v = _load(model)
            rbd = v._rbd_ref
            if scenario == "edited":
                for nid in sorted(v.nodes)[:2]:
                    v._apply_value("node k", nid, 2)
                    v._apply_value("node full flow", nid, True)
                    v._apply_value("node dependency", nid, (2, "RBDBlock"))
                    v._apply_value("node report anal", nid, True)
                    v._apply_value("node cold standby", nid, True)
                for bid in sorted(v.blocks)[:2]:
                    if bid:
                        v._apply_rename("block", bid, "Renamed")
                        v._apply_value("failure params", bid, [1.5, 2.5, .5])
                        v._apply_value("block dependency", bid, (3, "RBDBlock"))
                for item in list(v._event_items.values()) + \
                        list(v._hier_items.values()):
                    item.setPos(item.pos().x() + 37.0, item.pos().y() + 11.0)
            before = _dump(rbd)
            v.write_back_to_rbd(rbd)
            after = _dump(rbd)
            v.write_back_to_rbd(rbd)          # idempotent
            out[f"{model}:{scenario}"] = {
                "digest": _digest(after),
                "idempotent": _digest(_dump(rbd)) == _digest(after),
                "changed": _changed(before, after),
                # Edits now write through to the typed rows immediately;
                # `write_back_to_rbd` only reconciles layout. Record that
                # the rename really landed, which the old `changed` list
                # happened to prove before the write-through change.
                "renamed": sum(1 for b in v.blocks.values()
                               if b is not None and b.name == "Renamed"),
            }
            win._dirty = False
            win.close()
    return out


@pytest.mark.gui
def test_write_back_is_unchanged(observed):
    golden = json.loads(GOLDEN.read_text())
    assert set(observed) == set(golden)
    for k in golden:
        assert observed[k]["changed"] == golden[k]["changed"], k
        assert observed[k]["digest"] == golden[k]["digest"], k
        assert observed[k]["idempotent"] == golden[k]["idempotent"], k


@pytest.mark.gui
def test_the_edited_scenarios_really_write_something(observed):
    for m in MODELS:
        assert len(observed[f"{m}:edited"]["changed"]) >= \
            len(observed[f"{m}:untouched"]["changed"]), m
    assert any(observed[k]["renamed"] for k in observed), (
        "the edited scenarios must rename at least one block")
    assert all(e["idempotent"] for e in observed.values())


if __name__ == "__main__":
    regenerate(GOLDEN, observe)
