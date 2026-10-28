"""Characterisation of `DiagramView._apply_value`: for every edit key, exactly
which attributes of the live model object and which columns of its file row
change, which node-edit sets record it, and which canvas refreshes follow.

`_apply_value` is being moved out of the 6,900-line `DiagramView` into a
Qt-free `ModelEditor` (raptor/gui/model_edit.py). The golden file was
captured from the ORIGINAL implementation before that move, so this test is
the definition of "behaviour unchanged": it passes on the old code and on
the new. If an arm's behaviour is changed on purpose, regenerate with

    python tests/unit/test_apply_value_characterization.py --write

and review the golden diff in the commit.
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

GOLDEN = REPO / "tests" / "unit" / "data" / "apply_value_golden.json"
FIXTURE = REPO / "tests" / "golden" / "EventDepProbe" / "input.rbd"

# (kind, key, value). Values are chosen to differ from any default so a
# no-op arm cannot pass by accident.
ARMS: list[tuple[str, str, object]] = [
    ("block", "failure params", [1.5, 2.5, 0.5]),
    ("block", "repair params", [3.5, 4.5, 0.25]),
    ("block", "PM frequency", 123.0),
    ("block", "PM stagger", 4.5),
    ("block", "dependency fields", (10.0, 20.0, 30.0, 40.0, 50.0)),
    ("block", "costs", None),           # filled in below (one per field)
    ("block", "always add done cost", True),
    ("block", "pre LDT params", [1.0, 2.0, 3.0]),
    ("block", "post LDT params", [4.0, 5.0, 6.0]),
    ("block", "PM distribution", 3),
    ("block", "PM params", [7.0, 8.0, 9.0]),
    ("block", "stream edit", ("FR", 210)),
    ("block", "stream edit", ("F", 211)),
    ("block", "stream edit", ("R", 212)),
    ("block", "sparing type", "__other_sparing__"),
    ("block", "pm trigger", (True, "TrigA")),
    ("block", "pm trigger", (False, "TrigA")),
    ("block", "pre LDT dist", 4),
    ("block", "post LDT dist", 5),
    ("block", "pool name", "PoolX"),
    ("block", "pool name", ""),
    ("block", "initial stock", 7),
    ("block", "block uses pm", True),
    ("block", "block pm spare_needed", "__flip__:pm_spare_needed"),
    ("block", "block pm refresh", "__flip__:pm_refresh"),
    ("block", "block pm mis_defer", "__flip__:pm_mis_defer"),
    ("block", "block pm fail_reset", "__flip__:pm_fail_reset"),
    ("block", "block pm req_defer", "__flip__:pm_req_defer"),
    ("block", "block dependency", (3, "RBDBlock")),
    ("block", "block dep default idle", True),
    ("block", "block resource pool", ("ResA", 2)),
    ("block", "block resource counts", (2, 1)),
    ("block", "block new spares", (3, 12.0)),
    ("block", "block standby stress", 1.5),
    ("block", "block gd type", 2),
    ("block", "block gd rate limit", (0.1, 5.0)),
    ("node", "node k", 2),
    ("node", "node full flow", True),
    ("node", "node any path", "__flip__:any_path"),
    ("node", "node dependency", (2, "RBDBlock")),
    ("node", "node report anal", True),
    ("node", "node cold standby", True),
    ("node", "node priority return", True),
    ("node", "node check autos first", True),
    ("node", "node k star", 3),
    ("event", "event costs", (1.0, 2.0, 3.0)),
    ("event", "event dist", 2),
    ("event", "event param", 0.25),
    ("event", "event stream", 208),
]

EDITED_SETS = ("_edited_good_paths", "_edited_flow_flags", "_edited_node_deps",
               "_edited_report_anal", "_edited_standby")


def _plain(v):
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return str(v)


def _attrs(obj) -> dict:
    return {k: _plain(v) for k, v in vars(obj).items()}


def _diff(before: dict, after: dict) -> dict:
    return {k: after[k] for k in after if before.get(k) != after[k]}


def _row(view, kind, oid):
    sec = {"block": "BLOCK_DATA", "node": "NODE_DATA",
           "event": "EVENT_DATA"}[kind]
    for r in view._rbd_ref.sections[sec].rows:
        try:
            if int(float(r.get("ID", "-1"))) == oid:
                return r
        except (TypeError, ValueError):
            continue
    return {}


def observe() -> dict:
    """Run every arm on a fresh model and record what it changed."""
    from PySide6.QtWidgets import QApplication
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win._load_into_app(load_rbd(str(FIXTURE)), str(FIXTURE))
    app.processEvents()
    v = win._diagram_dock.view
    ids = {"block": next(b for b in sorted(v.blocks) if b != 0),
           "node": next(iter(sorted(v.nodes))),
           "event": next(iter(sorted(v.events)))}
    objs = {"block": v.blocks, "node": v.nodes, "event": v.events}
    out: dict = {}
    for kind, key, value in ARMS:
        if key == "costs":
            value = [float(i + 1) * 1.5 for i in range(len(v.BLOCK_COST_FIELDS))]
        oid = ids[kind]
        obj = objs[kind][oid]
        # Some samples must differ from the fixture's current value, or the
        # arm looks like a no-op when it merely re-sets what was there.
        if isinstance(value, str) and value.startswith("__flip__:"):
            value = not getattr(obj, value.split(":", 1)[1])
        elif value == "__other_sparing__":
            value = "NONE" if obj.sparing_type.name != "NONE" else "INFINITE"
        b_attrs, b_row = _attrs(obj), dict(_row(v, kind, oid))
        b_sets = {n: oid in getattr(v, n) for n in EDITED_SETS}
        b_sub = _plain(v._block_items[oid]._sub.toPlainText()) \
            if kind == "block" else None
        v._apply_value(key, oid, value)
        a_attrs, a_row = _attrs(obj), dict(_row(v, kind, oid))
        a_sets = {n: oid in getattr(v, n) for n in EDITED_SETS}
        a_sub = _plain(v._block_items[oid]._sub.toPlainText()) \
            if kind == "block" else None
        entry = {
            "attrs": _diff(b_attrs, a_attrs),
            "row": _diff(b_row, a_row),
            "edited_sets": [n for n in EDITED_SETS
                            if a_sets[n] and not b_sets[n]],
            "sublabel_changed": a_sub != b_sub,
        }
        if kind in ("block", "node"):
            items = v._block_items if kind == "block" else v._node_items
            entry["has_dep_badge"] = items[oid]._dep_badge is not None
        out[f"{kind}:{key}:{_plain(value)!r}"[:120]] = entry
    win._dirty = False
    win.close()
    return out


@pytest.mark.gui
def test_every_arm_still_does_exactly_what_it_did(observed):
    golden = json.loads(GOLDEN.read_text())
    assert set(observed) == set(golden), (
        f"arms added/removed: {sorted(set(observed) ^ set(golden))}")
    wrong = {k: (golden[k], observed[k]) for k in golden
             if golden[k] != observed[k]}
    assert not wrong, "\n".join(
        f"{k}\n   was {g}\n   now {o}" for k, (g, o) in list(wrong.items())[:6])


@pytest.mark.gui
def test_no_arm_is_a_silent_no_op(observed):
    """A key that changes nothing is a dead edit -- the failure mode the
    AST guard in test_mutation_names.py exists to prevent."""
    dead = [k for k, e in observed.items()
            if not e["attrs"] and not e["row"] and not e["edited_sets"]]
    assert not dead, dead


if __name__ == "__main__":
    regenerate(GOLDEN, observe)
