"""Characterisation of `DiagramView._apply_rename` and `_apply_dist`.

Golden captured from the ORIGINAL implementation before both moved into the
Qt-free `ModelEditor`; the move must not change any of it. Regenerate on
purpose with `python tests/unit/test_rename_dist_characterization.py --write`.
"""
from __future__ import annotations

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

GOLDEN = REPO / "tests" / "unit" / "data" / "rename_dist_golden.json"
MODELS = {"ComplexModel": REPO / "tests/golden/ComplexModel/input.rbd",
          "EventDepProbe": REPO / "tests/golden/EventDepProbe/input.rbd"}
SECTION = {"block": "BLOCK_DATA", "node": "NODE_DATA", "event": "EVENT_DATA",
           "hier": "HIERARCHY_DATA"}
ITEMS = {"block": "_block_items", "node": "_node_items",
         "event": "_event_items", "hier": "_hier_items"}
OBJS = {"block": "blocks", "node": "nodes", "event": "events", "hier": "hiers"}


def _plain(v):
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return str(v)


def _row(v, kind, oid):
    for r in v._rbd_ref.sections[SECTION[kind]].rows:
        try:
            if int(float(r.get("ID", "-1"))) == oid:
                return r
        except (TypeError, ValueError):
            continue
    return {}


def _diff(b, a):
    return {k: a[k] for k in a if b.get(k) != a[k]}


def _label(item):
    for attr in ("_name_label", "_label"):
        x = getattr(item, attr, None)
        if x is not None:
            return _plain(x.toPlainText() if hasattr(x, "toPlainText") else x)
    return None


def _load(name):
    from PySide6.QtWidgets import QApplication
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win._load_into_app(load_rbd(str(MODELS[name])), str(MODELS[name]))
    app.processEvents()
    return win, win._diagram_dock.view


def observe() -> dict:
    out: dict = {}
    for model, kinds in (("ComplexModel", ("block", "node", "hier")),
                         ("EventDepProbe", ("event",))):
        win, v = _load(model)
        for kind in kinds:
            oid = sorted(getattr(v, OBJS[kind]))[0]
            obj = getattr(v, OBJS[kind])[oid]
            old = getattr(obj, "node_name" if kind == "node" else "name")
            # A COMMENTS row keyed by the old name, to see it follow.
            com = v._rbd_ref.sections["COMMENTS"]
            com.rows.append({"Type": kind, "Name": old, "ID": "0"})
            b_row = dict(_row(v, kind, oid))
            v._apply_rename(kind, oid, "Renamed_X")
            a_row = dict(_row(v, kind, oid))
            out[f"rename:{model}:{kind}"] = {
                "obj_name": _plain(getattr(
                    obj, "node_name" if kind == "node" else "name")),
                "row": _diff(b_row, a_row),
                "comment": [dict(r) for r in com.rows
                            if r.get("Type") == kind],
                "item": _label(getattr(v, ITEMS[kind])[oid]),
            }
            # Missing element and same-name rename are both harmless.
            v._apply_rename(kind, 999999, "Nope")
        # unknown kind is ignored
        v._apply_rename("bogus", 1, "Nope")
        if model == "ComplexModel":
            bid = sorted(v.blocks)[0]
            blk = v.blocks[bid]
            for which, dn in (("F", (3, 2)), ("R", (5, 4))):
                b_row = dict(_row(v, "block", bid))
                b_attrs = {k: _plain(x) for k, x in vars(blk).items()}
                v._apply_dist(bid, which, dn)
                a_attrs = {k: _plain(x) for k, x in vars(blk).items()}
                out[f"dist:{which}"] = {
                    "attrs": _diff(b_attrs, a_attrs),
                    "row": _diff(b_row, dict(_row(v, "block", bid)))}
            v._apply_dist(999999, "F", (1, 1))
            saved = v._rbd_ref
            v._rbd_ref = None
            b_attrs = {k: _plain(x) for k, x in vars(blk).items()}
            v._apply_dist(bid, "F", (2, 3))
            out["dist:no_file"] = _diff(
                b_attrs, {k: _plain(x) for k, x in vars(blk).items()})
            v._rbd_ref = saved
        win._dirty = False
        win.close()
    return out


@pytest.mark.gui
def test_rename_and_dist_unchanged(observed):
    golden = json.loads(GOLDEN.read_text())
    assert set(observed) == set(golden)
    wrong = {k: (golden[k], observed[k]) for k in golden
             if golden[k] != observed[k]}
    assert not wrong, "\n".join(f"{k}\n was {g}\n now {o}"
                                for k, (g, o) in wrong.items())


@pytest.mark.gui
def test_every_case_actually_changes_something(observed):
    for k, e in observed.items():
        assert e, k
        if k.startswith("rename:"):
            assert e["obj_name"] == "Renamed_X", k
            assert e["item"] == "Renamed_X", k


if __name__ == "__main__":
    regenerate(GOLDEN, observe)
