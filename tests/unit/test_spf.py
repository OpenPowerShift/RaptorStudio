"""Golden fidelity for the Ch.10 "Single Point Failure File" listing
(Raptor 7's `WriteSPFfile`), found the way the original finds it: by failing each
block in Failure Effects View (`raptor/core/engine/spf_fev.py`).

Sixteen real files, one per fixture's `legacy/rpt_single_point_failures_<Fixture>.txt`, are
reproduced byte for byte, including the header's filename/date, the blank-line layout, the
20-character name padding, the per-phase layout, model/file row order and CRLF endings.
Six were captured in September (series/parallel, hierarchy, phases); ten on 2026-10-10 for
what a static walk of the graph cannot see: a dependency inducing a failure, a dependency on
a hierarchy, a cold-standby partner, dependent stress in phases, a big phased model with
events (`LadL8BEvents`), and an unphased model whose only event is in series (real omits it:
the unphased branch calls `ChangeEventState(Success, ...)`).

The header echoes the save-dialog filename and generation date, which are session
artifacts, so the test parses them out of each capture and feeds them back to the renderer.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import golden_fixtures as gf  # noqa: E402
from raptor.core.engine.spf import render_spf_file, spf_date_string  # noqa: E402
from raptor.core.engine.spf_fev import (  # noqa: E402
    build_spf_listing, phased_sections, single_point_failures,
)
from raptor.core.engine.fev import FevSession  # noqa: E402
from raptor.core.fileio.rbd import load_rbd  # noqa: E402

GOLDEN = REPO / "tests" / "golden"

_HEADER_RE = re.compile(
    r"^Single Point Failure File        (\S+)            (\d+ \w{3} \d{4})$")

# fixture -> real capture filename (in the fixture's own legacy/ folder)
CASES = {
    "SPFProbe": "rpt_single_point_failures_SPFProbe.txt",
    "Cascade": "rpt_single_point_failures_Cascade.txt",
    "MotorDemo": "rpt_single_point_failures_MotorDemo.txt",
    "HierarchyExample": "rpt_single_point_failures_Hierarchy.txt",
    "Phasing3": "rpt_single_point_failures_Phasing3.txt",
    "PhasingDemo": "rpt_single_point_failures_PhasingDemo.txt",
    # 2026-10-10
    "BlockDependency": "rpt_single_point_failures_BlockDependency.txt",
    "CS_DepOnStandbyPeer": "rpt_single_point_failures_CS_DepOnStandbyPeer.txt",
    "CS_HigherCold": "rpt_single_point_failures_CS_HigherCold.txt",
    "LadL8CEvents": "rpt_single_point_failures_LadL8CEvents.txt",
    "CS_MidSwitchDemote": "rpt_single_point_failures_CS_MidSwitchDemote.txt",
    "DepHierProbe": "rpt_single_point_failures_DepHierProbe.txt",
    "DependentStressProbe": "rpt_single_point_failures_DependentStressProbe.txt",
    "LadL6Full": "rpt_single_point_failures_LadL6Full.txt",
    "LadL8BEvents": "rpt_single_point_failures_LadL8BEvents.txt",
    "LadL4Block": "rpt_single_point_failures_LadL4Block.txt",
    "NodeStandbyBackflowProbe": "rpt_single_point_failures_NodeStandbyBackflowProbe.txt",
    "MixedDepPerc": "rpt_single_point_failures_MixedDepPerc.txt",
    "SpfEventUnphasedProbe": "rpt_single_point_failures_SpfEventUnphasedProbe.txt",
}


#: Real files written with the developer option that adds the Criticality column
#: (captured 2026-10-10 from a copy launched with the month switch; nothing in this
#: program reproduces the switch). `critFactor` is reset to 1.0 per candidate and cut by
#: dependency percentages while it is failed: 0.75, 0.675 (two dependents), 0.54 (phased).
CRITICALITY_CASES = {
    "LadL4Block": "rpt_single_point_failures_criticality_LadL4Block.txt",
    "LadL8BEvents": "rpt_single_point_failures_criticality_LadL8BEvents.txt",
    # 2026-10-11: phased models with a block that is Idle when each phase starts, which the candidate rule skips
    "CutRepairIdleProbe": "rpt_single_point_failures_criticality_CutRepairIdleProbe.txt",
    "LadL8A": "rpt_single_point_failures_criticality_LadL8A.txt",
}


def _rbd(fixture):
    return load_rbd(str(gf.resolve_rbd_path(fixture)))


def _capture(fixture):
    name = CASES[fixture]
    return (GOLDEN / fixture / "legacy" / name).read_bytes().decode("latin-1")


def _header(real: str):
    m = _HEADER_RE.match(real.split("\r\n")[0])
    assert m, f"unexpected real header: {real.splitlines()[0]!r}"
    return m.group(1), m.group(2)


@pytest.mark.parametrize("fixture", list(CASES))
def test_spf_listing_matches_real_byte_for_byte(fixture):
    real = _capture(fixture)
    name_of_file, date = _header(real)
    assert build_spf_listing(_rbd(fixture), name_of_file, date) == real


@pytest.mark.parametrize("fixture", list(CRITICALITY_CASES))
def test_criticality_column_matches_real_byte_for_byte(fixture):
    real = (GOLDEN / fixture / "legacy" / CRITICALITY_CASES[fixture]).read_bytes().decode("latin-1")
    name_of_file, date = _header(real)
    assert build_spf_listing(_rbd(fixture), name_of_file, date, password=True) == real


def test_unphased_sets_and_order_match_real():
    # Model/file order, not alphabetical (HierarchyExample: Thermal first).
    assert [r["name"] for r in single_point_failures(_rbd("SPFProbe"))] == ["Series"]
    assert single_point_failures(_rbd("Cascade")) == []
    assert [r["name"] for r in single_point_failures(_rbd("HierarchyExample"))] == \
        ["Thermal", "Attitude"]
    assert [r["name"] for r in single_point_failures(_rbd("MotorDemo"))] == \
        ["Casing", "Armature", "Magnets", "Brushes", "PowerSupply"]


def test_a_dependency_makes_the_master_a_single_point():
    """Real's BlockDependency file lists the dependent block, which a static walk missed."""
    assert [r["name"] for r in single_point_failures(_rbd("BlockDependency"))] == ["unnamed"]


def test_an_unphased_series_event_is_never_listed():
    """Raptor 7: unphased events are 'failed' with Success. Confirmed by real."""
    rows = single_point_failures(_rbd("SpfEventUnphasedProbe"))
    assert [r["kind"] for r in rows] == ["Block"]


def test_phased_events_are_listed():
    """The phased branch really fails events : LadL6Full lists Ev1 in real."""
    sections = phased_sections(FevSession(_rbd("LadL6Full")))
    assert any(r["kind"] == "Event" for _, rows in sections for r in rows)


def test_phased_sections_match_real():
    p3 = phased_sections(FevSession(_rbd("Phasing3")))
    assert [name for name, _ in p3] == [
        "D000-075_Orbit", "D076-150_Orbit", "D151-225_Orbit",
        "D226-300_Orbit", "D301-360_Orbit"]
    assert [[r["name"] for r in rows] for _, rows in p3] == \
        [["Attitude", "Thermal"]] * 5

    pd = phased_sections(FevSession(_rbd("PhasingDemo")))
    assert [name for name, _ in pd] == [
        "D000-060_Orbit", "D061-120_Orbit", "D121-180_Orbit",
        "D181-240_Orbit", "D241-300_Orbit", "D301-360_Orbit"]
    assert [[r["name"] for r in rows] for _, rows in pd] == \
        [["Thermal", "Attitude"]] * 6


def test_running_it_twice_gives_the_same_file_and_leaves_the_session_initial():
    for name in ("HierarchyExample", "Phasing3", "LadL8BEvents"):
        fev = FevSession(_rbd(name))
        first = (phased_sections(fev) if fev.phased
                 else single_point_failures(_rbd(name)))
        if fev.phased:
            assert phased_sections(fev) == first
            assert fev.phase_number == 1
            fev.refresh_all()
        fresh = FevSession(_rbd(name))
        assert fev.snapshot() == fresh.snapshot()


def test_unphased_empty_listing_says_none():
    out = render_spf_file("x.txt", "19 Sep 2026", [])
    assert out == (
        "Single Point Failure File        x.txt            19 Sep 2026\r\n"
        "\r\n"
        "\r\n"
        "Block\r\n"
        "   No single point failures\r\n"
    )


def test_phased_empty_section_says_none():
    out = render_spf_file("x.txt", "19 Sep 2026", [],
                          phase_sections=[("P1", [])])
    assert out == (
        "Single Point Failure File        x.txt            19 Sep 2026\r\n"
        "\r\n"
        "\r\n"
        "   Phase                  Block\r\n"
        "\r\n"
        "   No single point failures\r\n"
    )


def test_password_column_appends_criticality():
    rows = [{"kind": "Block", "id": 3, "name": "Series"}]
    out = render_spf_file("x.txt", "19 Sep 2026", rows, password=True)
    lines = out.split("\r\n")
    assert lines[3] == "Block                  Criticality "
    assert lines[4] == "   Series                 1.000000       "


def test_date_string_format():
    import datetime as dt
    assert spf_date_string(dt.datetime(2026, 9, 19)) == "19 Sep 2026"
    assert spf_date_string(dt.datetime(2026, 1, 5)) == "5 Jan 2026"


# ---------------------------------------------------------------------------
# Hierarchy-internal parallelism
# ---------------------------------------------------------------------------
#
# An MCP functional test review (the MCP functional test review, removed 2026-09-30, see git history, Test
# 26) reported `single_point_failures` flagging every block inside a
# hierarchy as an SPOF, even two that were wired in parallel -- i.e. the
# analysis supposedly ignores hierarchy-internal topology.  This isn't
# reproducible against a correctly-wired model: `_system_red_if_failed`
# recurses through `RBDHier`/`RBDNode` refs via the same global
# `nodes_by_id`/`links_by_id` graph regardless of nesting (see spf.py's
# docstring and its `HierarchyExample` byte-for-byte coverage above), so a
# parallel pair inside a hierarchy is correctly excluded.  This builds that
# exact topology --  Start -> HeatExchanger -> [PumpA || PumpB inside a
# hierarchy] -> PowerSupply -> End -- via `build_from_spec` (never
# hand-written rows) and pins the correct result as a regression test.

def _hier_parallel_spec() -> dict:
    def blk(name):
        return {"name": name, "fdist": 8, "fparams": (1000.0, 100.0, 0.0),
                "rdist": 8, "rparams": (10.0, 2.0, 0.0)}

    return {
        "name": "SPOFHierTest",
        "blocks": [
            dict(blk("HeatExchanger"), id=1),
            dict(blk("PowerSupply"), id=2),
            dict(blk("PumpA"), id=3, parent=1),
            dict(blk("PumpB"), id=4, parent=1),
        ],
        "nodes": [
            {"id": 1, "name": "Start", "type": 1, "n": 0, "out": 1},
            {"id": 2, "name": "HierIn", "type": 4, "parent": 1},
            {"id": 3, "name": "Join", "type": 2, "n": 2, "k": 1, "parent": 1},
            {"id": 4, "name": "HierOut", "type": 5, "parent": 1},
            {"id": 5, "name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "hierarchies": [
            {"id": 1, "name": "PumpPair", "in_id": 2, "out_id": 4},
        ],
        "links": [
            {"from": "Start", "to": "HeatExchanger"},
            {"from": "HeatExchanger", "to": ["Hier", 1]},
            {"from": ["Node", 2], "to": "PumpA"},
            {"from": ["Node", 2], "to": "PumpB"},
            {"from": "PumpA", "to": ["Node", 3]},
            {"from": "PumpB", "to": ["Node", 3]},
            {"from": ["Node", 3], "to": ["Node", 4]},
            {"from": ["Hier", 1], "to": "PowerSupply"},
            {"from": "PowerSupply", "to": "End"},
        ],
    }


def test_spf_excludes_parallel_pair_inside_hierarchy():
    from raptor.api.build import build_from_spec
    rbd = build_from_spec(_hier_parallel_spec())
    names = {row["name"] for row in single_point_failures(rbd)}
    assert names == {"HeatExchanger", "PowerSupply"}, (
        "PumpA/PumpB are parallel inside the PumpPair hierarchy (k=1, "
        f"n=2) and must not be SPOFs; got {names}"
    )
