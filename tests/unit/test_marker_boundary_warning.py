"""A hierarchy marker set as a capacity boundary is flagged (#4 item 6).

Setting full_flow=0 or div_flow=0 on a hierarchy In/Out marker changes where
the capacity caps bind and can leave far less flow through the hierarchy
(2350 down to 300 on one fixture), with no error. None of the 306 markers in
the real Raptor 7 fixtures has either flag off, so the warning costs nothing
on real models.
"""
from __future__ import annotations

import copy
import glob
import os

import pytest

from raptor.api.validate import issues
from raptor.core.fileio.rbd import load_rbd

GOLD = os.path.join(os.path.dirname(__file__), "..", "golden")
CAP_HIER = os.path.join(GOLD, "CycleSimStartStatsCapacityProbe", "input.rbd")


def _warnings(rbd):
    return [i for i in issues(rbd) if "capacity boundary" in str(i)]


def test_a_marker_boundary_is_flagged_with_capacity_analysis_on():
    f = load_rbd(CAP_HIER)
    assert _warnings(f) == []
    g = copy.deepcopy(f)
    marked = 0
    for r in g.sections["NODE_DATA"].rows:
        if r.get("Type") == "4":
            r["fullflow"] = "0"
            marked += 1
    found = _warnings(g)
    assert marked and len(found) == marked
    assert "In marker" in str(found[0]) and "ordinary node" in str(found[0])


def test_an_out_marker_is_flagged_too():
    g = load_rbd(CAP_HIER)
    for r in g.sections["NODE_DATA"].rows:
        if r.get("Type") == "5":
            r["divFlow"] = "0"
    assert any("Out marker" in str(i) for i in _warnings(g))


def test_nothing_is_said_when_capacity_analysis_is_off():
    g = load_rbd(CAP_HIER)
    for r in g.sections["NODE_DATA"].rows:
        if r.get("Type") == "4":
            r["fullflow"] = "0"
    g.sections["SYSTEM_DATA"].rows[0]["capacityAnalysis"] = "0"
    assert _warnings(g) == []


def test_an_ordinary_boundary_node_is_not_flagged():
    g = load_rbd(CAP_HIER)
    for r in g.sections["NODE_DATA"].rows:
        if r.get("Type") == "2":
            r["fullflow"] = "0"
            break
    assert _warnings(g) == []


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(GOLD, "*", "input.rbd"))))
def test_no_real_raptor_7_model_is_flagged(path):
    assert _warnings(load_rbd(path)) == [], os.path.basename(os.path.dirname(path))
