"""Planning a hierarchy delete from the file rows alone -- no Qt."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.typed import hier_from_row
from raptor.gui.hierarchy_plan import plan_dissolve

GOLD = Path(__file__).resolve().parents[1] / "golden"


def _load(name):
    rbd = load_rbd(str(GOLD / name / "input.rbd"))
    hiers = {}
    for r in rbd.sections["HIERARCHY_DATA"].rows:
        try:
            h = hier_from_row(r)
        except (ValueError, KeyError):
            continue
        if h.id:
            hiers[h.id] = h
    return rbd, hiers


def test_module_needs_no_qt():
    code = ("import sys, raptor.gui.hierarchy_plan;"
            "sys.exit(any(m.startswith('PySide') for m in sys.modules))")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


@pytest.fixture(scope="module")
def single():
    return _load("SingleHierarchy")


def test_the_oval_its_markers_and_the_children_are_all_accounted_for(single):
    rbd, hiers = single
    hid, h = next(iter(hiers.items()))
    plan = plan_dissolve(rbd, h, hid)
    assert plan.hier_row["ID"] == str(hid)
    assert {r["ID"] for r in plan.marker_rows} == {str(h.in_id),
                                                   str(h.out_id)}
    promoted = {(s, o) for s, o, _old, _new in plan.parent_rewrites}
    kids = {("BLOCK_DATA", int(float(r["ID"])))
            for r in rbd.sections["BLOCK_DATA"].rows
            if int(float(r.get("ParentId", 0))) == hid}
    assert kids and kids <= promoted
    assert all(new == (h.parent_id or 0)
               for *_x, new in plan.parent_rewrites)


def test_a_marker_id_only_skips_node_rows(single):
    """Blocks, nodes and hierarchies have separate id spaces."""
    rbd, hiers = single
    hid, h = next(iter(hiers.items()))
    plan = plan_dissolve(rbd, h, hid)
    marker = {h.in_id, h.out_id}
    assert not any(s == "NODE_DATA" and o in marker
                   for s, o, *_r in plan.parent_rewrites)


def test_links_touching_the_markers_are_cut_and_boundaries_rewired(single):
    rbd, hiers = single
    hid, h = next(iter(hiers.items()))
    plan = plan_dissolve(rbd, h, hid)
    lsec = rbd.sections["LINK_DATA"]
    for row, idx in plan.removed_links:
        assert lsec.rows[idx] is row          # the actual row, by position
    ids = [int(float(r["ID"])) for r, _ in plan.removed_links]
    assert len(ids) == len(set(ids)), "a link is removed once"
    assert plan.n_outside_in + plan.n_outside_out == len(plan.rewired_links)


def test_planning_does_not_change_the_file(single):
    rbd, hiers = single
    before = {n: [dict(r) for r in s.rows] for n, s in rbd.sections.items()}
    for hid, h in hiers.items():
        plan_dissolve(rbd, h, hid)
    after = {n: [dict(r) for r in s.rows] for n, s in rbd.sections.items()}
    assert before == after


def test_a_nested_hierarchys_contents_are_promoted_too():
    rbd, hiers = _load("NestedHierarchyCost")
    outer = next((i for i, h in hiers.items() if not h.parent_id), None)
    assert outer is not None
    plan = plan_dissolve(rbd, hiers[outer], outer)
    inner = {i for i, h in hiers.items() if h.parent_id == outer}
    assert inner
    assert {o for s, o, *_r in plan.parent_rewrites
            if s == "HIERARCHY_DATA"} >= inner


# -- import ---------------------------------------------------------------

def _plan_import(host_name, imp_name, hid=500):
    from raptor.gui.hierarchy_plan import plan_import
    host, imp = _load(host_name)[0], _load(imp_name)[0]
    return host, imp, plan_import(host, imp, imp_name, hid=hid,
                                  wrapper_ids=(hid, hid + 1, hid + 2))


def _ids(rows):
    return [int(float(r["ID"])) for r in rows]


def test_imported_ids_are_fresh_and_never_the_wrappers():
    host, _imp, plan = _plan_import("ComplexModel", "TwoBlockRedundancy")
    for rows, sec in ((plan.block_rows, "BLOCK_DATA"),
                      (plan.node_rows, "NODE_DATA"),
                      (plan.link_rows, "LINK_DATA")):
        used = {int(float(r["ID"])) for r in host.sections[sec].rows}
        new = _ids(rows)
        assert not set(new) & used and len(new) == len(set(new)), sec
        assert not set(new) & {500, 501, 502}, sec


def test_start_and_end_become_the_in_and_out_markers():
    _h, _i, plan = _plan_import("TwoBlockRedundancy", "TwoBlockRedundancy")
    by_id = {int(r["ID"]): r for r in plan.node_rows}
    assert by_id[plan.in_id]["Type"] == "4" and by_id[plan.in_id]["Name"] == "in"
    assert by_id[plan.out_id]["Type"] == "5"
    assert by_id[plan.out_id]["Name"] == "out"


def test_the_imported_home_level_hangs_under_the_new_hierarchy():
    _h, _i, plan = _plan_import("ComplexModel", "TwoBlockRedundancy")
    assert plan.block_rows
    assert {r["ParentId"] for r in plan.block_rows} == {"500"}
    assert {r["parent"] for r in plan.node_rows} == {"500"}


def test_the_defaults_template_row_is_not_imported():
    _h, imp, plan = _plan_import("ComplexModel", "TwoBlockRedundancy")
    real = [r for r in imp.sections["BLOCK_DATA"].rows
            if int(float(r["ID"])) != 0]
    assert len(plan.block_rows) == len(real)


def test_links_follow_the_remapped_elements():
    _h, _imp, plan = _plan_import("ComplexModel", "TwoBlockRedundancy")
    blocks = set(_ids(plan.block_rows))
    nodes = set(_ids(plan.node_rows))
    for r in plan.link_rows:
        for side, _pool in (("From", None), ("To", None)):
            t, i = r[side + "Type"], int(float(r[side + "ID"]))
            if t == "Block":
                assert i in blocks
            elif t == "Node":
                assert i in nodes
        assert r["pID"] == "500"


def test_a_model_without_exactly_one_start_and_end_is_refused():
    from raptor.gui.hierarchy_plan import plan_import
    host, imp = _load("ComplexModel")[0], _load("TwoBlockRedundancy")[0]
    imp.sections["NODE_DATA"].rows = [
        r for r in imp.sections["NODE_DATA"].rows
        if int(float(r["Type"])) != 3]
    with pytest.raises(ValueError, match="EXACTLY one Start"):
        plan_import(host, imp, "x.rbd", hid=500, wrapper_ids=(500, 501, 502))
