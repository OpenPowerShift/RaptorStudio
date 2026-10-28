"""The shared model-to-canvas transform, with no Qt."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from raptor.core.fileio import view_transform as vt
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"


def _rbd(points, extra_sections=None):
    rows = [{"ID": str(i + 1), "xPos": str(x), "yPos": str(y)}
            for i, (x, y) in enumerate(points)]
    secs = {"BLOCK_DATA": SimpleNamespace(rows=rows)}
    secs.update(extra_sections or {})
    return SimpleNamespace(sections=secs, view_transform=None)


def test_module_needs_no_qt():
    code = ("import sys, raptor.core.fileio.view_transform;"
            "sys.exit(any(m.startswith('PySide') for m in sys.modules))")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_a_frozen_transform_is_reused_verbatim():
    r = _rbd([(0, 0), (10, 10)])
    r.view_transform = {"scale": 3.5, "origin_x": 1.25, "flip_base": 9.0,
                        "dx_min": 2.0, "dy_min": 4.0}
    assert vt.ensure(r) == vt.ViewTransform(3.5, 1.25, 9.0, 2.0, 4.0)


def test_ensure_freezes_what_it_derives():
    r = _rbd([(0, 0), (10, 5)])
    first = vt.ensure(r)
    assert r.view_transform == first.as_dict()
    r.sections["BLOCK_DATA"].rows[0]["xPos"] = "3"     # moving an element
    assert vt.ensure(r) == first                       # ... changes nothing


def test_the_scale_keeps_the_closest_neighbours_apart():
    # 2 units apart -> (108 + 52) / 2 = 80 px per unit
    t = vt.derive(_rbd([(0, 0), (2, 0)]))
    assert t.scale == 80.0
    assert (t.origin_x, t.flip_base) == (0.0, 0.0)


def test_an_incidental_small_gap_is_not_a_grid():
    """Gaps of 10, 10 and 4: the smallest gap
    is not a shared pitch, so no grid is claimed."""
    t = vt.derive(_rbd([(0, 0), (10, 0), (20, 0), (24, 0)]))
    assert t.dx_min == 0.0
    t = vt.derive(_rbd([(0, 0), (10, 0), (20, 0), (30, 0)]))
    assert t.dx_min == 10.0


def test_a_degenerate_span_is_clamped():
    t = vt.derive(_rbd([(0, 0), (0.0001, 0)]))
    assert 4.0 <= t.scale <= 300.0


def test_an_empty_model_still_has_a_usable_transform():
    t = vt.derive(_rbd([]))
    assert t.scale == vt.LEGACY_SCALE and t.origin_x == 0.0


def test_the_template_row_does_not_position_anything():
    r = _rbd([(500, 500), (0, 0), (2, 0)])
    r.sections["BLOCK_DATA"].rows[0]["ID"] = "0"
    assert vt.derive(r).origin_x == 0.0


def test_the_api_and_the_gui_derive_the_same_transform():
    """They used to be two hand-synchronised copies, and had drifted."""
    from raptor.api.annotations import derive_view_transform
    for p in sorted(GOLD.glob("*/input.rbd"))[:15]:
        r = load_rbd(str(p))
        assert derive_view_transform(r) == vt.derive(r).as_dict(), p.parent.name


def _mixed(blocks, nodes):
    """Blocks and nodes at (x, y), all on the home level."""
    b = [{"ID": str(i + 1), "xPos": str(x), "yPos": str(y), "ParentId": "0"}
         for i, (x, y) in enumerate(blocks)]
    n = [{"ID": str(i + 1), "xPos": str(x), "yPos": str(y), "parent": "0"}
         for i, (x, y) in enumerate(nodes)]
    return SimpleNamespace(sections={"BLOCK_DATA": SimpleNamespace(rows=b),
                                     "NODE_DATA": SimpleNamespace(rows=n)},
                           view_transform=None)


def test_two_nodes_need_less_room_than_two_blocks():
    """A run of nodes used to be charged as two blocks, so it drew as long
    empty links. Two nodes 1.25 apart need (18 + 18 + 40) / 1.25 px/unit."""
    t = vt.derive(_mixed([], [(0, 0), (1.25, 0)]))
    assert t.scale == (2 * vt.NODE_R + vt.NODE_GAP) / 1.25


def test_a_node_beside_a_block_is_charged_as_one_of_each():
    t = vt.derive(_mixed([(2.5, 0)], [(0, 0)]))
    assert t.scale == (vt.NODE_R + vt.BLOCK_W / 2 + vt.MIN_ELEMENT_GAP) / 2.5


def test_the_tightest_pair_decides():
    # block-block 2.5 apart (64) beats node-block 2.5 apart (49.6)
    t = vt.derive(_mixed([(2.5, 0), (5.0, 0)], [(0, 0)]))
    assert t.scale == (vt.BLOCK_W + vt.MIN_ELEMENT_GAP) / 2.5


def test_auto_layout_puts_neighbouring_node_columns_a_grid_step_apart():
    """Start -> split node -> two blocks -> join -> End: Start and the split
    are both nodes, so they are NODE_PITCH apart; node-to-block columns
    keep the full LAYER_PITCH."""
    from raptor.core.fileio.legacy_layout import (
        LAYER_PITCH, NODE_PITCH, layout_container_positions)
    S, sp, a, b, j, E = (("node", 1), ("node", 2), ("block", 1),
                         ("block", 2), ("node", 3), ("node", 4))
    edges = [(S, sp), (sp, a), (sp, b), (a, j), (b, j), (j, E)]
    pos = layout_container_positions([S, sp, a, b, j, E], edges, [S],
                                     pins={E})
    assert pos[sp][0] - pos[S][0] == NODE_PITCH
    assert pos[a][0] - pos[sp][0] == LAYER_PITCH
    assert pos[E][0] - pos[j][0] == NODE_PITCH
