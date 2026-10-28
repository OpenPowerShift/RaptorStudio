"""The canvas and the model rows never disagree.

The GUI keeps typed objects for the canvas and the file rows that every
reader uses (validation, the Issues list, a run, Save). Whenever an edit
changed only the objects, something downstream read the old value: edits
lost on a hierarchy change, a stale name in Issues, a stale node count in
an exported .rbd. The invariant: after any edit -- and after its Undo and
Redo -- writing the canvas back to the rows changes nothing.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODELS = ["new_docs/models/HospitalPower.rbds",
          "tests/golden/EventDepProbe/input.rbd",
          "tests/golden/Phasing1/input.rbd"]


def _rows(rbd):
    return {n: [{k: v for k, v in r.items() if not k.startswith("_")}
                for r in s.rows] for n, s in rbd.sections.items()}


def _drift(w):
    v = w._diagram_dock.view
    v.flush_pending_moves()
    before = _rows(w._rbd)
    v.write_back_to_rbd(w._rbd)
    after = _rows(w._rbd)
    out = []
    for name, rows in before.items():
        for x, y in zip(rows, after.get(name, [])):
            d = {k: (x.get(k), y.get(k)) for k in set(x) | set(y)
                 if x.get(k) != y.get(k)}
            if d:
                out.append((name, x.get("ID"), d))
    return out


def _ops(v, w):
    b = next(iter(v.blocks))
    n = next((i for i, x in v.nodes.items()
              if getattr(x, "type_node", 2) == 2), next(iter(v.nodes)))
    link = w._rbd.section("LINK_DATA").rows[0]
    return {
        "rename block": lambda: v.rename_block(b, "Renamed_B"),
        "rename node": lambda: v.rename_node(n, "Renamed_N"),
        "failure params": lambda: v.set_block_fail_params(b, [123.0, 0, 0]),
        "repair params": lambda: v.set_block_repair_params(b, [7.0, 1.0, 0]),
        "pm": lambda: (v.set_block_pm_enabled(b, True),
                       v.set_block_pm_freq(b, 333.0),
                       v.set_block_pm_option(b, "refresh", False)),
        "dependency": lambda: (v.set_block_dep_master(b, -2, "RBDBlock"),
                               v.set_block_dep_fields(
                                   b, [10.0, 70.0, 10.0, 10.0, 50.0])),
        "resources and spares": lambda: (
            v.set_block_resource_counts(b, 2, 2),
            v.set_block_sparing_type(b, "CUSTOM"),
            v.set_block_new_spares(b, 3, 99.0),
            v.set_block_init_stock(b, 5)),
        "advanced": lambda: (v.set_block_standby_stress(b, 0.5),
                             v.set_block_gd_type(b, 2),
                             v.set_block_gd_rate_limit(b, 0.2, 3.0)),
        "node fields": lambda: (v.set_node_good_paths(n, 1),
                                v.set_node_full_flow(n, False),
                                v.set_node_cold_standby(n, True),
                                v.set_node_dep(n, -2, "RBDBlock")),
        "distribution": lambda: v.set_block_dist(b, "F", 15),
        "logistics and pm law": lambda: (
            v.set_block_ldt_dist(b, "pre", 4),
            v.set_block_ldt_params(b, "pre", [5.0, 0, 0]),
            v.set_block_pm_dist(b, 4), v.set_block_pm_params(b, [3.0, 0, 0])),
        "stream and costs": lambda: (
            v.set_block_stream(b, "F", 17),
            v.set_block_costs(b, [float(i + 2) for i in range(14)])),
        "add block and node": lambda: (v.add_block("Added", 50.0, 50.0),
                                       v.add_node("AddedN", 55.0, 55.0)),
        "connect": lambda: v.connect_elements("Node", n, "Block", b),
        "link flow": lambda: v.set_link_flow_field(
            int(link["ID"]), "nomFlow", link["nomFlow"], "5"),
        "events": lambda: [(v.rename_event(e, "RenEv"),
                            v.set_event_field(e, "param", 0.75))
                           for e in list(v.events)[:1]],
        "rename hierarchy": lambda: [v.rename_hier(h, "RenH")
                                     for h in list(v.hiers)[:1]],
        "copy and paste": lambda: (v._scene.clearSelection(),
                                   v._block_items[b].setSelected(True),
                                   v.copy_selection(), v.paste()),
        "align": lambda: ([it.setSelected(True) for it in
                           list(v._block_items.values())[:2]],
                          v.align_selected("left")),
        "drag": lambda: v._block_items[b].setPos(
            v._block_items[b].pos() + QPointF(40.0, 25.0)),
        "auto layout": lambda: v.auto_layout(),
        "delete": lambda: (v._scene.clearSelection(),
                           v._block_items[b].setSelected(True),
                           v.delete_selected()),
    }


def _window(model):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow()
    rbd = load_rbds(model) if model.endswith(".rbds") else load_rbd(model)
    w._load_into_app(rbd, model)
    return w


OPS = ["rename block", "rename node", "failure params", "repair params", "pm",
       "dependency", "resources and spares", "advanced", "node fields",
       "distribution", "logistics and pm law", "stream and costs",
       "add block and node", "connect", "link flow", "events",
       "rename hierarchy", "copy and paste", "align", "drag", "auto layout",
       "delete"]


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("model", MODELS)
def test_the_rows_already_hold_every_edit(model, op, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes))
    # Raptor 7's input rules may refuse a value for this model's element;
    # a refused edit changes nothing, which is fine here.
    import raptor.gui.ui.msgbox as mb
    monkeypatch.setattr(mb, "warning", lambda *a, **k: 0)
    w = _window(model)
    v = w._diagram_dock.view
    _ops(v, w)[op]()
    QApplication.processEvents()
    assert _drift(w) == [], f"{op}: the rows lag the canvas"
    v._stack.undo()
    QApplication.processEvents()
    assert _drift(w) == [], f"{op} (undo)"
    v._stack.redo()
    QApplication.processEvents()
    assert _drift(w) == [], f"{op} (redo)"
    w._dirty = False
    w.close()
