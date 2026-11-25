"""The lost-capacity Sankey's shared layout: stacked links, one scale."""
from __future__ import annotations

import itertools

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


def test_a_short_downstream_column_hangs_near_its_sources():
    nodes, _links, boxes, _bands = _laid_out()
    lost = boxes["lost"]
    hier_nodes = [n for n in nodes if str(n["id"]).startswith("hier-")]
    top = min(boxes[str(n["id"])][0] for n in hier_nodes)
    end = max(boxes[str(n["id"])][0] + boxes[str(n["id"])][1] for n in hier_nodes)
    mid = (top + end) / 2
    assert lost[0] <= mid <= lost[0] + lost[1]


@pytest.mark.parametrize("width", [400.0, 620.0, 1300.0])
@pytest.mark.parametrize("columns", [2, 3, 4])
def test_columns_never_overlap_and_links_keep_a_run(width, columns):
    from raptor.api.insight.sankey_layout import MIN_RUN, NODE_W, horizontal
    left, right, span, fracs = horizontal(width, columns, 400.0, 400.0, 60.0, 50.0)
    assert left >= 0 and right >= 0 and left + right + span == pytest.approx(width)
    assert len(fracs) == columns
    xs = [left + span * f for f in fracs]
    assert all(b - a >= NODE_W + MIN_RUN - 1e-6 for a, b in itertools.pairwise(xs))
    assert xs[-1] + NODE_W <= width


def test_narrow_labels_neither_overlap_nor_leave_the_area():
    """The Qt painter at ~400 px: every drawn label rectangle stays inside the
    chart and none overlaps another."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from raptor.gui.ui.chart_widget import ChartWidget
    app = QApplication.instance() or QApplication([])
    spec = charts.capacity_sankey(_attribution())
    widget = ChartWidget()
    widget.resize(400, 560)
    rects: list = []
    original = QPainter.drawText
    inside = {"on": False}
    paint = widget._paint_sankey

    def wrapped(*a, **k):
        inside["on"] = True
        try:
            return paint(*a, **k)
        finally:
            inside["on"] = False
    widget._paint_sankey = wrapped

    def spy(self, *args):
        if inside["on"] and args and hasattr(args[0], "width") and len(args) >= 3 and isinstance(args[2], str):
            rects.append((args[0], args[2]))
        return original(self, *args)
    QPainter.drawText = spy  # type: ignore[method-assign]
    try:
        widget.set_spec(spec)
        image = QImage(400, 560, QImage.Format.Format_ARGB32)
        widget.render(image)
    finally:
        QPainter.drawText = original  # type: ignore[method-assign]
    app.processEvents()
    assert rects, "the painter drew no labels"
    texts = list(rects)
    for i, (a, _ta) in enumerate(texts):
        assert a.left() >= -1 and a.right() <= 401
        for b, _tb in texts[i + 1:]:
            if a.width() > 1 and b.width() > 1:
                inter = a.intersected(b)
                assert inter.width() <= 1 or inter.height() <= 1


def test_middle_column_labels_keep_the_node_name_at_400_px():
    from raptor.api.insight.sankey_layout import fit_lines
    adv = lambda text: 6.0 * len(text)  # noqa: E731 - a fixed-pitch measure
    # name + amount, then the name alone, then two lines, then cut (>= 6 chars)
    assert fit_lines("Intake", ["82,748 (64 %)", "82,748"], 200, adv) == ["Intake · 82,748 (64 %)"]
    assert fit_lines("Intake", ["82,748 (64 %)", "82,748"], 100, adv) == ["Intake · 82,748"]
    assert fit_lines("Intake", ["82,748 (64 %)", "82,748"], 40, adv) == ["Intake"]
    assert fit_lines("Generated energy", [], 70, adv) == ["Generated", "energy"]
    cut = fit_lines("Manutencao planejada", [], 48, adv)
    assert len(cut) == 1 and cut[0].startswith("Manute") and cut[0].endswith("…")


def test_the_narrow_painter_draws_every_middle_name():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from raptor.gui.ui.chart_widget import ChartWidget
    QApplication.instance() or QApplication([])
    spec = charts.capacity_sankey(_attribution())
    widget = ChartWidget()
    widget.resize(400, 560)
    drawn: list[str] = []
    original = QPainter.drawText
    inside = {"on": False}
    paint = widget._paint_sankey

    def wrapped(*a, **k):
        inside["on"] = True
        try:
            return paint(*a, **k)
        finally:
            inside["on"] = False
    widget._paint_sankey = wrapped

    def spy(self, *args):
        if inside["on"] and len(args) >= 3 and isinstance(args[2], str):
            drawn.append(args[2])
        return original(self, *args)
    QPainter.drawText = spy  # type: ignore[method-assign]
    try:
        widget.set_spec(spec)
        widget.render(QImage(400, 560, QImage.Format.Format_ARGB32))
    finally:
        QPainter.drawText = original  # type: ignore[method-assign]
    text = " ".join(drawn)
    for name in ("Lost", "Delivered"):
        assert name in text, name
    assert any(part.startswith("Generated") for part in drawn)


def test_a_tiny_node_still_gets_a_visible_band():
    nodes = [{"id": "a", "column": 0, "value": 10000.0},
             {"id": "big", "column": 1, "value": 9999.0},
             {"id": "tiny", "column": 1, "value": 1.0}]
    links = [{"source": "a", "target": "big", "value": 9999.0},
             {"source": "a", "target": "tiny", "value": 1.0}]
    boxes, _bands = layout(nodes, links, HEIGHT)
    assert boxes["tiny"][1] >= 4.0


def test_svg_headline_nodes_carry_the_unit():
    svg = render(charts.capacity_sankey(_attribution()))
    assert "flow × time" in svg
