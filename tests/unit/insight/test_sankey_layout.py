"""The lost-capacity Sankey's shared layout: stacked links, one scale."""
from __future__ import annotations

import pytest

from raptor.api.insight import charts, hierarchy as hier
from raptor.api.insight.sankey_layout import layout, spread_labels
from raptor.render.charts_svg import render

HEIGHT = 400.0


def _attribution():
    def elem(i, lost, causes):
        return {"kind": "block", "id": i, "name": f"E{i}",
                "lost_energy": {"mean": lost}, "causes": causes}

    def row(hid, name, members, causes):
        return {"kind": "hier", "id": hid, "name": name, "path": [name],
                "depth": 1, "parent": 0,
                "members": [{"kind": "block", "id": m} for m in members],
                "member_count": len(members), "starter_count": 0, "elements": 0,
                "dtci": {"mean": 0.1}, "red_time": {"mean": 1.0},
                "causes": causes, "main_cause": None}
    return {"runs": 1, "units": "hours",
            "elements": [elem(1, 60.0, {"repair": .5, "pm": .5}),
                         elem(2, 25.0, {"repair": 1.0}),
                         elem(3, 12.0, {"spares": .75, "pm": .25}),
                         elem(4, 0.5, {"pm": 1.0})],
            "hierarchies": [row(10, "A", [1], {}), row(20, "B", [2], {}),
                            row(30, "C", [3], {}), row(40, "D", [4], {})],
            "capacity": {"rule": "proportional", "generated": 200.0,
                         "delivered_energy": {"mean": 900.0},
                         "lost_energy": {"mean": 97.5},
                         "causes": {}, "elements": [
                             elem(1, 60.0, {"repair": .5, "pm": .5}),
                             elem(2, 25.0, {"repair": 1.0}),
                             elem(3, 12.0, {"spares": .75, "pm": .25}),
                             elem(4, 0.5, {"pm": 1.0})]}}


def _laid_out(**kw):
    nodes, links = hier.sankey(_attribution(), **kw)
    boxes, bands = layout(nodes, links, HEIGHT)
    return nodes, links, boxes, bands


@pytest.mark.parametrize("kw", [{}, {"show_delivered": False},
                                {"by_hierarchy": False},
                                {"by_hierarchy": False, "show_delivered": False}])
def test_links_stack_without_overlap_and_fill_their_nodes(kw):
    nodes, links, boxes, bands = _laid_out(**kw)
    for end, pick in (("source", 1), ("target", 2)):
        for node in nodes:
            nid = str(node["id"])
            slots = sorted((band[pick], band[0])
                           for link, band in zip(links, bands, strict=True)
                           if str(link[end]) == nid and band)
            top, height = boxes[nid]
            y = top
            for start, thick in slots:
                assert start == pytest.approx(y, abs=1e-6), nid  # adjacent
                y += thick
            if slots:
                assert y <= top + height + 1.0, nid
                # a conserved node is filled exactly (within the 1 px floors)
                assert y == pytest.approx(top + height, abs=1.0) or (
                    float(node["value"]) > sum(
                        float(ln["value"]) for ln in links if str(ln[end]) == nid)
                    - 1e-9)


def test_one_scale_for_links_and_nodes():
    nodes, links, boxes, bands = _laid_out()
    values = {str(n["id"]): float(n["value"]) for n in nodes}
    big = [(ln, b) for ln, b in zip(links, bands, strict=True)
           if b and float(ln["value"]) * 1.0 > 0.2 * max(values.values())]
    for link, (thick, _ys, _yt) in big:
        # thickness / value is the same factor as node height / value
        k = thick / float(link["value"])
        src = str(link["source"])
        assert boxes[src][1] / values[src] == pytest.approx(k, rel=1e-6)
        tgt = str(link["target"])
        assert boxes[tgt][1] / values[tgt] == pytest.approx(k, rel=1e-6)


def test_the_busiest_column_fills_the_height_and_nothing_overflows():
    nodes, _links, boxes, _bands = _laid_out()
    tops = [boxes[str(n["id"])][0] for n in nodes]
    ends = [boxes[str(n["id"])][0] + boxes[str(n["id"])][1] for n in nodes]
    assert min(tops) >= -1e-6 and max(ends) <= HEIGHT + 1e-6
    cols: dict[int, float] = {}
    for n in nodes:
        cols[int(n["column"])] = cols.get(int(n["column"]), 0.0) + boxes[str(n["id"])][1]
    # column 1 (delivered, lost) is the busiest here: it nearly fills it
    assert max(cols.values()) > HEIGHT * 0.95


def test_tiny_links_stay_visible_and_inside_their_node():
    nodes, links, boxes, bands = _laid_out()
    for _link, band in zip(links, bands, strict=True):
        assert band[0] >= 1.0
    for node in nodes:
        assert boxes[str(node["id"])][1] >= 2.0


def test_later_columns_are_ordered_to_avoid_crossings():
    nodes, links, boxes, bands = _laid_out()
    # at each node, outgoing links are ordered by their targets' position
    for node in nodes:
        outs = sorted(((band[1], boxes[str(link["target"])][0])
                       for link, band in zip(links, bands, strict=True)
                       if str(link["source"]) == str(node["id"]) and band))
        targets = [t for _s, t in outs]
        assert targets == sorted(targets)


def test_labels_are_spread_and_kept_inside():
    got = spread_labels([("a", 2.0, 12.0), ("b", 3.0, 12.0), ("c", 99.0, 12.0)],
                        0.0, 100.0)
    assert got["a"] >= 6.0
    assert got["b"] - got["a"] >= 12.0 - 1e-9
    assert got["c"] <= 94.0 + 1e-9 and got["c"] - got["b"] >= 12.0 - 1e-9


def test_svg_links_leaving_a_node_start_at_distinct_heights():
    import re
    spec = charts.capacity_sankey(_attribution())
    svg = render(spec)
    paths = re.findall(r"<path d='M([\d.]+),([\d.]+) C", svg)
    assert len(paths) == len(spec.series)
    # links from "lost" share an x; stacked, none begins at another's height
    lost_x = {}
    for x, y in paths:
        lost_x.setdefault(x, []).append(float(y))
    for ys in lost_x.values():
        assert len(set(ys)) == len(ys)


def test_both_renderers_use_the_shared_layout():
    import inspect

    from raptor.gui.ui import chart_widget
    from raptor.render import charts_svg
    assert "sankey_layout" in inspect.getsource(charts_svg._sankey)
    assert "sankey_layout" in inspect.getsource(chart_widget.ChartWidget._paint_sankey)
