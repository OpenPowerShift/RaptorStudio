"""Auto layout uses the element's drawn size, not a fixed block box."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from raptor.api.build import build_from_spec
from raptor.core.fileio import element_size
from raptor.core.fileio.legacy_layout import layout_container_positions
from raptor.gui.ui.diagram import DiagramView


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _model(names):
    blocks = [{"name": n, "id": i + 10} for i, n in enumerate(names)]
    nodes = [{"id": 1, "name": "Start", "type": "start"},
             {"id": 2, "name": "End", "type": "end"}]
    links = [{"from": "Start", "to": names[0]}]
    links += [{"from": names[i], "to": names[i + 1]}
              for i in range(len(names) - 1)]
    links += [{"from": names[-1], "to": "End"}]
    return build_from_spec({"name": "Sizes", "blocks": blocks,
                            "nodes": nodes, "links": links,
                            "system": {"term_condition": 100,
                                       "num_runs": 1}},
                           auto_layout=False)


def _long(n):
    return "VeryLongBlockName" + str(n) * 3


def test_long_name_layout_keeps_adjacent_columns_clear(qapp):
    names = [_long(1), _long(2)]
    model = _model(names)
    view = DiagramView()
    view.load_rbd(model)
    view.auto_layout()
    items = sorted(view._block_items.values(),
                   key=lambda it: it.sceneBoundingRect().left())
    assert len(items) == 2
    left, right = items
    assert left.sceneBoundingRect().right() < right.sceneBoundingRect().left()


def test_hierarchy_ovals_in_a_series_keep_one_row():
    members = [("node", 1)] + [("hier", i) for i in range(2, 6)] + [("node", 6)]
    edges = ([ (("node", 1), ("hier", 2)) ]
             + [(("hier", i), ("hier", i + 1)) for i in range(2, 5)]
             + [(("hier", 5), ("node", 6))])
    sizes = {k: (element_size.model_half_width(
        "NODE_DATA" if k[0] == "node" else "HIERARCHY_DATA",
        "H" if k[0] == "hier" else "", 64.0), 0.5)
        for k in members}
    pos = layout_container_positions(members, edges, [("node", 1)],
                                     pins={("node", 6)}, sizes=sizes,
                                     scale=64.0)
    assert len({round(pos[k][1], 6) for k in members}) == 1


def test_long_name_scale_reserves_more_room_than_a_short_one():
    short = build_from_spec({"name": "S", "blocks": [{"name": "A", "id": 1}],
                             "nodes": [{"id": 2, "name": "Start",
                                        "type": "start"},
                                       {"id": 3, "name": "End",
                                        "type": "end"}],
                             "links": [{"from": "Start", "to": "A"},
                                       {"from": "A", "to": "End"}],
                             "system": {"term_condition": 100,
                                        "num_runs": 1}},
                            auto_layout=False)
    long = _model([_long(9)])
    from raptor.core.fileio import view_transform
    assert view_transform._column_pitch_scale(long) >= \
        view_transform._column_pitch_scale(short)


def test_long_names_do_not_overlap_in_a_top_to_bottom_layout():
    """TB spreads rows across the canvas width, so a wide block must claim
    its drawn width there too -- a fixed 2.5-unit row pitch overlapped
    two 200 px names."""
    members = [("block", i) for i in range(4)]
    edges = [(members[i], members[i + 1]) for i in range(3)]
    sizes = {k: (element_size.model_half_width(
        "BLOCK_DATA", _long(k[1]), element_size.REFERENCE_SCALE),
        element_size.model_half_height(
            "BLOCK_DATA", _long(k[1]), element_size.REFERENCE_SCALE))
        for k in members}
    pos = layout_container_positions(members, edges, [members[0]],
                                     pins={members[-1]}, sizes=sizes,
                                     scale=element_size.REFERENCE_SCALE,
                                     direction="TB")
    for a in members:
        for b in members:
            if a >= b:
                continue
            (xa, ya), (xb, yb) = pos[a], pos[b]
            assert (abs(xa - xb) >= sizes[a][0] + sizes[b][0] - 1e-6
                    or abs(ya - yb) >= sizes[a][1] + sizes[b][1] - 1e-6), \
                f"{a} and {b} overlap at {pos[a]} / {pos[b]}"


def test_rerunning_auto_layout_on_long_names_converges(qapp):
    """The layout pitch is measured at the fixed reference scale, so a
    second pass must not ratchet the view scale (the first pass used to
    feed its own output scale back in)."""
    model = _model([_long(1), _long(2), _long(3)])
    view = DiagramView()
    view.load_rbd(model)
    view.auto_layout()
    first = view._scale
    view.auto_layout()
    assert view._scale == pytest.approx(first)
    items = sorted(view._block_items.values(),
                   key=lambda it: it.sceneBoundingRect().left())
    import itertools
    for left, right in itertools.pairwise(items):
        assert left.sceneBoundingRect().right() < right.sceneBoundingRect().left()


def test_a_large_wrapped_layout_does_not_run_the_quadratic_refine():
    """A 500-element chain wraps into bands, where refinement cannot help.
    Scoring it anyway walked every edge (and the quadratic crossing scan)
    before the budget could stop the search, taking tens of seconds."""
    from raptor.core.fileio import legacy_layout

    members = [("block", i) for i in range(500)]
    edges = [(members[i], members[i + 1]) for i in range(499)]
    calls = []
    original = legacy_layout._seg_seg

    def spy(*args):
        calls.append(1)
        return original(*args)

    legacy_layout._seg_seg = spy
    try:
        legacy_layout.layout_container_positions(
            members, edges, [members[0]], pins={members[-1]})
    finally:
        legacy_layout._seg_seg = original
    assert calls == [], "the wrapped layout scored edges it cannot refine"


def test_a_reopened_saved_layout_keeps_the_derived_scale(qapp, tmp_path):
    """After Auto Layout the frozen transform comes from the exact drawn
    extents. Saving and reopening must carry that scale, not re-derive a
    different one from the fixed block box."""
    from raptor.core.fileio.model_file import load_model_file
    from raptor.core.fileio.rbds import save_rbds

    model = _model([_long(1), _long(2), _long(3)])
    view = DiagramView()
    view.load_rbd(model)
    view.auto_layout()
    rbd = view._rbd_ref
    scale = float(rbd.view_transform["scale"])
    path = tmp_path / "long_names.rbds"
    save_rbds(rbd, str(path))
    reopened = load_model_file(path)
    assert float(reopened.view_transform["scale"]) == pytest.approx(scale)
    xs = sorted({float(r["xPos"]) for r in reopened.section("BLOCK_DATA").rows
                 if str(r.get("ID")) != "0"})
    assert len(xs) == 3
