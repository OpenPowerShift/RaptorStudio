"""Cloning and creating file rows, and planning a collapse -- no Qt."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from raptor.core.fileio.rbd import load_rbd
from raptor.gui import row_ops
from raptor.gui.hierarchy_plan import plan_collapse

GOLD = Path(__file__).resolve().parents[1] / "golden"


def _rbd(name):
    return load_rbd(str(GOLD / name / "input.rbd"))


def test_modules_need_no_qt():
    code = ("import sys, raptor.gui.row_ops, raptor.gui.hierarchy_plan;"
            "sys.exit(any(m.startswith('PySide') for m in sys.modules))")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_names_become_single_tokens():
    assert row_ops.sanitize_name("Main  Pump 2") == "Main_Pump_2"
    assert row_ops.sanitize_name("   ") == "unnamed"


def test_next_id_skips_live_elements_and_file_rows():
    rbd = _rbd("EventDepProbe")
    in_rows = {int(float(r["ID"])) for r in rbd.sections["NODE_DATA"].rows}
    nid = row_ops.next_id(rbd, "NODE_DATA", "ID", {1000})
    assert nid not in in_rows and nid != 1000
    assert row_ops.next_id(None, "NODE_DATA", "ID", {1, 2}) == 3


def test_template_row_is_a_copy_of_the_defaults_row():
    rbd = _rbd("EventDepProbe")
    t = row_ops.template_row(rbd, "BLOCK_DATA", "ID")
    assert t is not None and t["ID"] in ("0", "0.000000")
    t["blockName"] = "mutated"
    assert all(r.get("blockName") != "mutated"
               for r in rbd.sections["BLOCK_DATA"].rows)
    assert row_ops.template_row(rbd, "NO_SUCH", "ID") is None


def test_a_missing_section_is_created_before_comments():
    rbd = _rbd("TwoBlockRedundancy")
    rbd.sections.pop("HIERARCHY_DATA", None)
    sec = row_ops.ensure_section(rbd, "HIERARCHY_DATA")
    keys = list(rbd.sections)
    assert sec.rows == [] and sec.columns == row_ops.HIER_COLUMNS
    if "COMMENTS" in keys:
        assert keys.index("HIERARCHY_DATA") + 1 == keys.index("COMMENTS")
    assert row_ops.ensure_section(rbd, "HIERARCHY_DATA") is sec


def test_new_hierarchy_ids_are_disjoint_across_sections():
    rbd = _rbd("TwoBlockRedundancy")
    used = {1, 2, 3, 4, 5}
    h, i, o = row_ops.build_hierarchy_rows(
        rbd, name="Loop A", x=10.0, y=5.0, parent=0, parent_level=None,
        used_ids=used, node_ids={1, 2, 3})
    ids = {int(h["ID"]), int(i["ID"]), int(o["ID"])}
    assert len(ids) == 3 and not ids & used
    assert h["hierarchyName"] == "Loop_A" and h["level"] == "1"
    assert (h["in"], h["out"]) == (i["ID"], o["ID"])
    assert (i["Type"], o["Type"]) == ("4", "5")
    assert i["parent"] == o["parent"] == h["ID"]


def test_a_nested_hierarchy_is_one_level_deeper():
    rbd = _rbd("TwoBlockRedundancy")
    h, *_ = row_ops.build_hierarchy_rows(
        rbd, name="x", x=0, y=0, parent=7, parent_level=2, used_ids=set(),
        node_ids=set())
    assert h["level"] == "3" and h["Parent"] == "7"


def _links(rbd):
    return rbd.sections["LINK_DATA"].rows


def test_collapse_splits_boundary_links_and_moves_internal_ones():
    rbd = _rbd("TwoBlockRedundancy")
    blocks = sorted({int(float(r["ID"])) for r in
                     rbd.sections["BLOCK_DATA"].rows} - {0})
    plan = plan_collapse(rbd, hid=99, in_id=90, out_id=91, parent=0,
                         blocks=blocks[:1], nodes=[], events=[])
    assert plan.parent_moves == [("BLOCK_DATA", blocks[0], "0")]
    adds = [r for op, r, _ in plan.link_ops if op == "add"]
    dels = [r for op, r, _ in plan.link_ops if op == "del"]
    assert dels and adds
    links_with_block = [r for r in _links(rbd)
                        if (r["FromType"] == "Block"
                            and int(float(r["FromID"])) == blocks[0])
                        or (r["ToType"] == "Block"
                            and int(float(r["ToID"])) == blocks[0])]
    assert len(dels) == len(links_with_block)
    new_ids = [int(float(r["ID"])) for r in adds
               if r["pID"] == "99" and r not in dels]
    existing = {int(float(r["ID"])) for r in _links(rbd)}
    fresh = [i for i in new_ids if i not in existing]
    assert len(fresh) == len(set(fresh))          # unique fresh ids


def test_planning_a_collapse_leaves_the_file_alone():
    rbd = _rbd("ComplexModel")
    before = [dict(r) for r in _links(rbd)]
    plan_collapse(rbd, hid=99, in_id=90, out_id=91, parent=0,
                  blocks=[1, 2], nodes=[], events=[])
    assert [dict(r) for r in _links(rbd)] == before


@pytest.mark.parametrize("sel", [[], [10**6]])
def test_an_empty_or_unknown_selection_changes_nothing(sel):
    rbd = _rbd("TwoBlockRedundancy")
    plan = plan_collapse(rbd, hid=99, in_id=90, out_id=91, parent=0,
                         blocks=sel, nodes=[], events=[])
    assert plan.link_ops == []
