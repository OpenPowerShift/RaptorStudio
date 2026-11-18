"""Fixes from the first round of new-user testing (large models, CLI, MCP).

Each test names the finding it pins. The speed fixes (the rule gate's
scoped checks, the `.rbds` render memo) are pinned by behaviour: they must
give the same answers as the whole-model pass they replace.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --- core: the scoped rule checks agree with the whole-model check ---------

def _series(n: int):
    from raptor.api.build import build_from_spec, series
    return build_from_spec(series(n))


def test_check_link_ends_finds_what_check_finds_at_the_ends():
    from raptor.core import input_rules
    from raptor.core.fileio.rbdlib import make_link_row
    rbd = _series(3)
    links = rbd.sections["LINK_DATA"]
    block1 = next(r for r in rbd.sections["BLOCK_DATA"].rows
                  if r.get("blockName") == "Block1")
    block3 = next(r for r in rbd.sections["BLOCK_DATA"].rows
                  if r.get("blockName") == "Block3")
    before = {v.key for v in input_rules.check(rbd)}
    # A second link out of Block1: the block may only connect to one object.
    new = make_link_row(9999, "RBDBlock", block1["ID"], "RBDBlock", block3["ID"])
    links.rows.append(new)
    full = {v.key for v in input_rules.check(rbd)} - before
    scoped = {v.key for v in input_rules.check_link_ends(rbd, [new])}
    assert full, "the second link out of a block should be a finding"
    assert full <= scoped


def test_check_new_rows_matches_check_for_a_new_block():
    from raptor.core import input_rules
    from raptor.core.fileio.rbdlib import make_block_row
    rbd = _series(2)
    row = make_block_row(500, "A name far longer than twenty", 0, 0)
    rbd.sections["BLOCK_DATA"].rows.append(row)
    full = {v.key for v in input_rules.check(rbd)}
    scoped = {v.key for v in input_rules.check_new_rows(rbd, "BLOCK_DATA", [row])}
    assert scoped and scoped <= full


def test_rbds_render_memo_reflects_edits():
    from raptor.core.fileio.rbds import render_rbds
    rbd = _series(3)
    first = render_rbds(rbd)
    assert render_rbds(rbd) == first
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if r.get("blockName") == "Block2")
    row["blockName"] = "Renamed"
    assert "Renamed" in render_rbds(rbd)


# --- api/build -------------------------------------------------------------

def _nested_spec(depth: int, *, hier_links: bool = False) -> dict:
    """Start -> b1 -> [H1: b2 -> [H2: b3 ...]] -> End; no `level` given.
    With `hier_links`, links outside a hierarchy name it ("H1"), not its
    In/Out markers."""
    blocks, nodes, hiers, links = [], [
        {"name": "Start", "type": 1, "n": 0, "out": 1},
        {"name": "End", "type": 3, "n": 1, "out": 0}], [], []

    def level(lv: int, parent: int) -> tuple[str, str]:
        name = f"b{lv}"
        blocks.append({"name": name, "parent": parent,
                       "fail_dist": 4, "fail_params": [1000, 0]})
        if lv == depth:
            return name, name
        h = 100 + lv
        nodes.extend([{"id": 900 + 2 * lv, "name": f"In{lv}", "type": 4, "parent": h},
                      {"id": 901 + 2 * lv, "name": f"Out{lv}", "type": 5, "parent": h}])
        hiers.append({"id": h, "name": f"H{lv}", "in_id": 900 + 2 * lv,
                      "out_id": 901 + 2 * lv, "parent": parent})
        first, last = level(lv + 1, h)
        links.extend([{"from": f"In{lv}", "to": first}, {"from": last, "to": f"Out{lv}"}])
        links.append({"from": name, "to": f"H{lv}" if hier_links else f"In{lv}"})
        return name, f"H{lv}" if hier_links else f"Out{lv}"

    first, last = level(1, 0)
    links.extend([{"from": "Start", "to": first}, {"from": last, "to": "End"}])
    return {"blocks": blocks, "nodes": nodes, "hierarchies": hiers, "links": links}


def test_spec_hierarchy_level_comes_from_the_parent_chain():
    from raptor.api.build import build_from_spec
    rbd = build_from_spec(_nested_spec(4), auto_layout=False)
    levels = {r["hierarchyName"]: r["level"]
              for r in rbd.sections["HIERARCHY_DATA"].rows if r["ID"] != "0"}
    assert levels == {"H1": "1", "H2": "2", "H3": "3"}


def test_spec_link_can_name_a_hierarchy():
    from raptor.api.build import build_from_spec
    rbd = build_from_spec(_nested_spec(2, hier_links=True), auto_layout=False)
    ends = {(r["FromType"], r["ToType"]) for r in rbd.sections["LINK_DATA"].rows}
    assert ("Block", "Hier") in ends and ("Hier", "Node") in ends


@pytest.mark.parametrize("n,k", [(0, 1), (-2, 1), (1001, 1), (3, 0), (3, 4)])
def test_degenerate_template_sizes_are_refused(n, k):
    from raptor.api.build import BuildError, template_spec
    template = "k_of_n" if k != 1 or n == 3 else "series"
    with pytest.raises(BuildError, match="template"):
        template_spec(template, n=n, k=k)


def test_unknown_sparing_type_name_is_refused():
    from raptor.api.build import BuildError, build_from_spec
    with pytest.raises(BuildError, match="sparing type"):
        build_from_spec({"blocks": [{"name": "A", "SpType": "bogus"}]})


# --- api/workspace and validate -------------------------------------------

def test_rbd_save_names_elements_off_raptor7s_canvas():
    from raptor.api.validate import layout_warning, outside_raptor_canvas
    rbd = _series(2)
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows if r["ID"] != "0")
    row["xPos"] = "400.000000"
    assert outside_raptor_canvas(rbd) == 1
    # Our own canvas has no edge: only a .rbd save mentions it.
    assert "150 x 100" not in (layout_warning(rbd) or "")
    assert "150 x 100" in layout_warning(rbd, for_rbd=True)


def test_lost_in_rbd_names_the_run_settings():
    from raptor.core import run_experiment
    exp = run_experiment.normalise({})
    assert run_experiment.lost_in_rbd(exp) == []
    exp["streams"] = "separate"
    lost = run_experiment.lost_in_rbd(exp)
    assert len(lost) == 1 and "separate" in lost[0].lower()


def test_levels_below_counts_hierarchies_already_inside():
    from raptor.api.workspace import Workspace
    from raptor.api import build
    from raptor.mcp.tools_model import _depth_fields
    import tempfile
    ws = Workspace(tempfile.mkdtemp())
    mid = ws.create_model("nest", template="blank")["model_id"]
    outer = build.add_hierarchy(ws, mid, "Outer")
    inner = build.add_hierarchy(ws, mid, "Inner", parent=int(outer["ID"]))
    build.add_hierarchy(ws, mid, "Core", parent=int(inner["ID"]))
    fields = _depth_fields(ws, mid, outer["ID"])
    assert fields == {"depth": 1, "levels_below": 5}


# --- cli -------------------------------------------------------------------

@pytest.mark.parametrize("text", ["0", "-3", "x"])
def test_jobs_must_be_positive(text):
    from raptor.cli.common import positive_int
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int(text)
    assert positive_int("4") == 4


def test_set_output_must_be_a_model_file(tmp_path):
    from raptor.api.build import build_from_spec, series
    from raptor.core.fileio.rbds import save_rbds
    model = tmp_path / "m.rbds"
    save_rbds(build_from_spec(series(2)), str(model))
    env = dict(os.environ, HOME=str(tmp_path), XDG_STATE_HOME=str(tmp_path))
    out = subprocess.run(
        [sys.executable, "-m", "raptor.cli.main", "set", str(model),
         "system.num_runs=5", "-o", str(tmp_path / "out.txt")],
        capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 2, out.stderr
    assert "out.txt" in out.stderr and "raptor-tmp" not in out.stderr


# --- gui -------------------------------------------------------------------

@pytest.fixture()
def view():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from raptor.core.edit.new_model import build_new_model
    from raptor.gui.ui.diagram import DiagramView
    view = DiagramView()
    view.load_rbd(build_new_model())
    return view


def test_gui_refused_add_returns_zero(view, monkeypatch):
    monkeypatch.setattr("raptor.gui.ui.msgbox.warning", lambda *a, **k: None)
    assert view.add_block("x" * 40, 0, 0) == 0
    assert view.add_block("Fine", 0, 0) in view.blocks


def test_gui_second_link_out_of_a_block_is_refused(view, monkeypatch):
    refused: list = []
    monkeypatch.setattr("raptor.gui.ui.msgbox.warning",
                        lambda *a, **k: refused.append(a))
    a = view.add_block("A1", 0, 0)
    b = view.add_block("B1", 50, 0)
    c = view.add_block("C1", 50, 50)
    view.connect_elements("Block", a, "Block", b)
    assert not refused
    n_links = len(view._rbd_ref.sections["LINK_DATA"].rows)
    view.connect_elements("Block", a, "Block", c)
    assert refused, "a second link out of a block must be refused"
    assert len(view._rbd_ref.sections["LINK_DATA"].rows) == n_links


def test_a_negative_mean_is_refused_when_building_but_never_fails_runs():
    from raptor.api.build import BuildError, build_from_spec, series
    spec = series(1)
    spec["blocks"][0]["fail_params"] = [-5, 0, 0]
    with pytest.raises(BuildError, match="Mean"):
        build_from_spec(spec)
    spec["blocks"][0]["fail_params"] = [1e12, 0, 0]   # "never fails"
    build_from_spec(spec)
