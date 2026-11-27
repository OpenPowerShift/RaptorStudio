"""#351: the N-others table, its count, and Depth inside a chosen hierarchy."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QTableWidget

from raptor.api.insight import charts
from raptor.api.insight import hierarchy as hier
from raptor.gui.ui.chart_widget import ChartWidget


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _hier(hid, name, parent, depth, direct, members):
    return {"id": hid, "name": name, "parent": parent, "depth": depth,
            "path": [name], "dtci": {"mean": 0.0}, "member_count": len(members),
            "direct_members": [{"kind": "block", "id": i} for i in direct],
            "members": [{"kind": "block", "id": i} for i in members]}


def _attribution():
    """Top (1) > Mid (2) > Leaf (3); blocks 1 in Top, 2 in Mid, 3-4 in Leaf,
    5 outside every hierarchy."""
    hiers = [_hier(1, "Top", 0, 1, [1], [1, 2, 3, 4]),
             _hier(2, "Mid", 1, 2, [2], [2, 3, 4]),
             _hier(3, "Leaf", 2, 3, [3, 4], [3, 4])]
    elements = [{"kind": "block", "id": i, "name": f"B{i}",
                 "dtci": {"mean": 0.2}, "main_cause": "failure"}
                for i in range(1, 6)]
    return {"elements": elements, "hierarchies": hiers, "runs": 4}


def test_depth_rolls_deeper_elements_into_their_hierarchy():
    att = _attribution()
    names = lambda spec: sorted(c["name"] for c in spec.series)  # noqa: E731
    assert names(charts.downtime_treemap(att)) == ["B1", "B2", "B3", "B4", "B5"]
    assert names(charts.downtime_treemap(att, depth=1)) == ["B5", "Top"]
    top = next(c for c in charts.downtime_treemap(att, depth=1).series
               if c["name"] == "Top")
    assert top["value"] == pytest.approx(0.8) and len(top["items"]) == 4
    assert names(charts.downtime_treemap(att, depth=2)) == ["B1", "B5", "Mid"]


def test_depth_counts_from_the_chosen_hierarchy():
    att = _attribution()
    mid = hier.resolve(att, 2)
    assert hier.depth_below(att, mid) == 1
    scoped = hier.scoped(att, mid)
    spec = charts.downtime_treemap(scoped, depth=1, scope_row=mid)
    assert sorted(c["name"] for c in spec.series) == ["B2", "Leaf"]


def test_a_second_fold_counts_items_not_cells():
    rows = [{"name": f"E{i}", "value": 1.0} for i in range(30)]
    first = charts._treemap(rows, title="t", subtitle="", fmt="percent",
                            what="x", runs=0, top=25, width=1000, height=600)
    folded = charts.fold_small(first, top=10)
    other = next(c for c in folded.series if c.get("items"))
    assert other["name"] == f"{len(other['items'])} others" == "20 others"


def test_the_items_table_keeps_each_value_on_its_row(qapp):
    items = [{"name": f"E{i:02d}", "value": float(i), "share": i / 100,
              "cause": "failure"} for i in range(1, 40)]
    spec = charts._treemap(items, title="t", subtitle="", fmt="percent",
                           what="x", runs=0, top=5, width=1000, height=600)
    group = next(c for c in spec.series if c.get("items"))
    widget = ChartWidget()
    widget.set_spec(spec)
    seen = {}
    original = QDialog.exec

    def grab(dialog):
        table = dialog.findChild(QTableWidget)
        seen["rows"] = [[table.item(r, c).text() if table.item(r, c) else ""
                         for c in range(table.columnCount())]
                        for r in range(table.rowCount())]
    QDialog.exec = grab
    try:
        widget.show_details(group)
    finally:
        QDialog.exec = original
    rows = seen["rows"]
    assert len(rows) == len(group["items"])
    assert all(cell for row in rows for cell in row[:3]), rows
    by_name = {row[0]: row for row in rows}
    assert by_name["E01"][1] != by_name["E02"][1]


def _down(att):
    """Give the hierarchies downtime from their members' shares."""
    by = {e["id"]: e["dtci"]["mean"] for e in att["elements"]}
    for h in att["hierarchies"]:
        h["dtci"] = {"mean": sum(by[m["id"]] for m in h["members"])}
        h["main_cause"] = "failure"
    return att


def test_the_pareto_inside_a_hierarchy_opens_levels_down():
    att = _down(_attribution())
    names = lambda depth: sorted(s["name"] for s in  # noqa: E731
                                 charts.hierarchy_pareto(att, 1, depth=depth).series)
    assert names(1) == ["B1", "Mid"]                 # direct contents
    assert names(2) == ["B1", "B2", "Leaf"]
    assert names(0) == ["B1", "B2", "B3", "B4"]      # every level
    spec = charts.hierarchy_pareto(att, 1, depth=0)
    assert sum(s["value"] for s in spec.series) == pytest.approx(1.0)
    assert "every level" in spec.subtitle
