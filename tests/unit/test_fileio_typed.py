"""Tests for `raptor.core.fileio.typed` against golden fixtures.

See typed.py's module docstring for which row mappings are checked against
a known fixture value and which are name-similarity only.
"""
from __future__ import annotations

import pathlib

import pytest

from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.typed import (
    block_from_row,
    event_from_row,
    link_from_row,
    node_from_row,
    system_config_from_row,
)
from raptor.core.model import SparingType

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"


def _fixture(name: str) -> pathlib.Path:
    path = GOLDEN / name / "input.rbd"
    if not path.exists():
        pytest.skip(f"golden fixture not present: {path}")
    return path


class TestBlockFromRow:
    def test_uniformprobe_real_block(self) -> None:
        # UniformProbe block 3 is the configuration the RNG recovery was
        # built on.
        f = load_rbd(str(_fixture("UniformProbe")))
        row = next(r for r in f.section("BLOCK_DATA").rows if r["ID"] == "3")
        block = block_from_row(row)
        assert block.name == "unnamed"
        assert block.id == 3
        assert block.fail_distro == 10  # Uniform Real
        assert block.fail_stream == 1
        assert block.fail_values == [0.0, 1.0, 0.0]
        assert block.repair_distro == 10
        assert block.repair_stream == 2
        assert block.repair_values == [0.0, 1.0, 0.0]
        assert block.sparing_type is SparingType.INFINITE

    def test_default_block_template_row_also_parses(self) -> None:
        # The default/template row (see test_fileio_rbd.py) uses different
        # distros (Exponential/LogNormal), so this confirms the mapper is
        # not hardcoded to Uniform Real.
        f = load_rbd(str(_fixture("UniformProbe")))
        row = next(r for r in f.section("BLOCK_DATA").rows if r["ID"] == "0")
        block = block_from_row(row)
        assert block.fail_distro == 4  # Exponential
        assert block.repair_distro == 7  # LogNormal

    def test_pre_post_pm_param_groups_did_not_collide(self) -> None:
        # pre_params, post_params and pm_params each come from their own
        # Param1/2/3 triple, not all three from the last one.
        f = load_rbd(str(_fixture("UniformProbe")))
        row = next(r for r in f.section("BLOCK_DATA").rows if r["ID"] == "3")
        block = block_from_row(row)
        assert block.pre_params == [0.0, 0.0, 0.0]
        assert block.post_params == [0.0, 0.0, 0.0]
        assert block.pm_params == [1.0, 0.0, 0.0]  # differs, so no collision


class TestEventFromRow:
    def test_all_five_events(self) -> None:
        # Matches test_event_rows_match_known_content in test_fileio_rbd.py.
        f = load_rbd(str(_fixture("Events")))
        events = [event_from_row(r) for r in f.section("EVENT_DATA").rows]
        assert [e.name for e in events] == ["P=0.91", "P=0.87", "P=0.65", "P=0.72", "P=0.53"]
        assert all(e.fail_distro == 17 for e in events)  # Bernoulli
        assert all(e.fail_stream == 203 for e in events)
        assert [e.fail_mean for e in events] == [0.91, 0.87, 0.65, 0.72, 0.53]


class TestNodeFromRow:
    def test_start_and_end_nodes(self) -> None:
        f = load_rbd(str(_fixture("UniformProbe")))
        nodes = [node_from_row(r) for r in f.section("NODE_DATA").rows]
        names = {n.node_name for n in nodes}
        assert names == {"start", "end"}


class TestSystemConfigFromRow:
    def test_events_fixture(self) -> None:
        f = load_rbd(str(_fixture("Events")))
        cfg = system_config_from_row(f.section("SYSTEM_DATA").rows[0])
        assert cfg.termination_type == "FailSim"
        assert cfg.num_runs == 1000
        # The stream list derives GetStream(203) -> sys_streams[3-1]=9 ->
        # FetchRaptorSeed(9).
        assert cfg.sys_streams == [7, 8, 9, 10, 70, 71, 72, 73, 74, 75, 101]
        assert cfg.gy_threshold == 0.95
        assert cfg.yr_threshold == 0.90

    def test_uniformprobe_fixture_different_termination_type(self) -> None:
        f = load_rbd(str(_fixture("UniformProbe")))
        cfg = system_config_from_row(f.section("SYSTEM_DATA").rows[0])
        assert cfg.termination_type == "TimeSim"
        assert cfg.num_runs == 500


class TestLinkFromRow:
    def test_links_connect_start_block_end(self) -> None:
        # UniformProbe's block has ID=3; start/end nodes are 1/2 (see
        # TestNodeFromRow) — links should chain start(1) -> block(3) -> end(2).
        f = load_rbd(str(_fixture("UniformProbe")))
        links = [link_from_row(r) for r in f.section("LINK_DATA").rows]
        pairs = {(link.connect_from_id, link.connect_to_id) for link in links}
        assert pairs == {(1, 3), (3, 2)}


class TestLargeRealFileStress:
    """Hierarchy1-7.rbd (149 blocks/61 nodes/309 links) — every row through
    every typed mapper, no exceptions, no silently-wrong distro/sparing
    values. See tests/golden/Hierarchy1-7/README.md."""

    def test_every_block_row_converts_without_error(self) -> None:
        f = load_rbd(str(_fixture("Hierarchy1-7")))
        blocks = [block_from_row(r) for r in f.section("BLOCK_DATA").rows]
        assert len(blocks) == 150  # 149 real + 1 default template
        # Fail/repair distros actually used in this file: Exponential(4),
        # LogNormal(7), None(18, the default-template placeholder) — see
        # the fixture README for why this doesn't cover all 22 distros.
        seen_distros = {b.fail_distro for b in blocks} | {b.repair_distro for b in blocks}
        assert seen_distros == {4, 7, 18}

    def test_every_node_row_converts_without_error(self) -> None:
        f = load_rbd(str(_fixture("Hierarchy1-7")))
        nodes = [node_from_row(r) for r in f.section("NODE_DATA").rows]
        assert len(nodes) == 61

    def test_every_link_row_converts_without_error(self) -> None:
        f = load_rbd(str(_fixture("Hierarchy1-7")))
        links = [link_from_row(r) for r in f.section("LINK_DATA").rows]
        assert len(links) == 309

    def test_system_config_matches_factory_defaults(self) -> None:
        f = load_rbd(str(_fixture("Hierarchy1-7")))
        cfg = system_config_from_row(f.section("SYSTEM_DATA").rows[0])
        # Same factory-default stream config as both other fixtures — see
        # Raptor 7 InitFactorySettings.
        assert cfg.sys_streams == [7, 8, 9, 10, 70, 71, 72, 73, 74, 75, 101]


def test_zero_ldt_pm_dist_normalized_to_fixed() -> None:
    """Raptor 7's `IF nextInt=0 THEN nextInt:=19` for the pre-LDT /
    post-LDT / PM distribution codes ."""
    from raptor.core.fileio.typed import _norm_dist
    assert _norm_dist(0) == 19
    assert _norm_dist(6) == 6
    assert _norm_dist(19) == 19


def test_end_node_is_reported_as_lowercase_end():
    """Raptor 7 reports a type-3 node as `end` whatever the row says."""
    rbd = load_rbd(str(_fixture("TwoBlockRedundancy")))
    row = next(r for r in rbd.sections["NODE_DATA"].rows if r.get("Type") == "3")
    row["Name"] = "End"
    node = node_from_row(row)
    # The model keeps the authored spelling so the canvas and the file rows
    # stay in step; only the reported name is lowercased.
    assert node.node_name == "End"
    assert node.report_name == "end"
