"""Applying run options must not re-lay the model out (#4 item 5).

`configure_system` saves through the workspace and `save_rbds` runs the
legacy layout gate on every save, so `raptor run -n 1` moved every block of
a crammed, off-grid model (minutes on a large one) and dropped its frozen
view transform, only to apply a run count.
"""
from __future__ import annotations

import os
from argparse import Namespace

from raptor.api import build
from raptor.api.workspace import Workspace
from raptor.cli.runopts import apply_run_options
from raptor.core.fileio.legacy_layout import (
    needs_legacy_normalization, no_layout_on_save,
)
from raptor.core.fileio.rbds import load_rbds, render_rbds

SPEC = {
    "blocks": [{"id": i, "name": f"B{i}", "x": 0.5 * i + 0.17,
                "y": 90 + 0.3 * (i % 3) + 0.07, "fail_dist": 13,
                "fail_params": [100, 0, 0], "repair_dist": 19,
                "repair_params": [5, 0, 0]} for i in range(1, 9)],
    "nodes": [{"id": 1, "type": "start", "name": "S", "x": 0.1, "y": 90.1},
              {"id": 2, "type": "end", "name": "E", "x": 9.3, "y": 90.1}],
    "links": ([{"from": "S", "to": "B1"}]
              + [{"from": f"B{i}", "to": f"B{i + 1}"} for i in range(1, 8)]
              + [{"from": "B8", "to": "E"}]),
}


def _positions(rbd):
    return {r["ID"]: (r["xPos"], r["yPos"])
            for r in rbd.sections["BLOCK_DATA"].rows if r["ID"] != "0"}


def _crammed_workspace(tmp_path):
    rbd = build.build_from_spec(SPEC, auto_layout=False)
    rbd.view_transform = {}
    path = os.path.join(tmp_path, "m.rbds")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_rbds(rbd))
    assert needs_legacy_normalization(load_rbds(path))     # the premise
    ws = Workspace(os.path.join(tmp_path, "ws"))
    return ws, ws.open_model(path)["model_id"]


def test_applying_run_options_leaves_the_positions_alone(tmp_path):
    ws, mid = _crammed_workspace(str(tmp_path))
    before = _positions(ws.get(mid).rbd)
    applied = apply_run_options(ws, mid, Namespace(runs=3))
    assert applied["num_runs"] == 3
    assert _positions(ws.get(mid).rbd) == before


def test_the_run_options_themselves_still_apply(tmp_path):
    ws, mid = _crammed_workspace(str(tmp_path))
    apply_run_options(ws, mid, Namespace(runs=7, length=500.0))
    row = ws.get(mid).rbd.sections["SYSTEM_DATA"].rows[0]
    assert float(row["NumRuns"]) == 7


def test_a_deliberate_save_still_repairs_a_crammed_layout(tmp_path):
    """The gate is for exactly this; only the run options skip it."""
    ws, mid = _crammed_workspace(str(tmp_path))
    before = _positions(ws.get(mid).rbd)
    build.configure_system(ws, mid, num_runs=2)
    assert _positions(ws.get(mid).rbd) != before


def test_the_switch_is_scoped_to_its_block(tmp_path):
    ws, mid = _crammed_workspace(str(tmp_path))
    before = _positions(ws.get(mid).rbd)
    with no_layout_on_save():
        build.configure_system(ws, mid, num_runs=2)
    assert _positions(ws.get(mid).rbd) == before
    build.configure_system(ws, mid, num_runs=3)       # outside: gate is back
    assert _positions(ws.get(mid).rbd) != before
