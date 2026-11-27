"""The Charts page's Show control and the table of every item."""
from __future__ import annotations

import csv
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from raptor.api.insight import ranking
from raptor.gui.ui import ranked_items_dialog
from raptor.gui.ui.charts_page import ChartsPage
from tests.unit.insight.test_ranking import N, _attribution, _per_run

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page():
    page = ChartsPage()
    page.resize(1200, 700)
    page.set_inputs(per_run=_per_run(), attribution=_attribution(), units="hours")
    page.show()
    yield page
    page.close()


def _drawn(page) -> int:
    spec = page.selected_spec()
    return len(spec.series) or len(spec.facets)


def _choose(page, count) -> None:
    page.show_box.setCurrentIndex(page.show_box.findData(count or 0))


def test_show_changes_how_many_items_a_pareto_draws(page):
    assert page.select("Who causes the downtime")
    assert page.show_box.isVisibleTo(page)
    assert [page.show_box.itemText(i) for i in range(page.show_box.count())] == [
        "Top 5", "Top 10", "Top 20", "Top 50", "All"]
    assert _drawn(page) == 10                     # the chart's own default
    _choose(page, 20)
    assert _drawn(page) == 20
    _choose(page, None)
    assert _drawn(page) == N
    _choose(page, 5)
    assert _drawn(page) == 5


def test_the_choice_is_remembered_per_chart(page):
    page.select("Who causes the downtime")
    _choose(page, 5)
    page.select("Who loses the capacity")
    assert page.show_box.currentData() == 10      # its own default
    _choose(page, 20)
    page.select("Who causes the downtime")
    assert page.show_box.currentData() == 5 and _drawn(page) == 5
    page.select("Who loses the capacity")
    assert page.show_box.currentData() == 20 and _drawn(page) == 20


def test_show_and_all_items_are_hidden_where_the_chart_is_no_ranking(page):
    for title in ("Run-to-run outcome", "How long is an outage?"):
        assert page.select(title)
        assert not page.show_box.isVisibleTo(page)
        assert not page.items_button.isVisibleTo(page)
        assert not page.all_items_action.isEnabled()
    page.select("Who causes the downtime")
    assert page.items_button.isVisibleTo(page)
    assert page.all_items_action.isEnabled()


def test_comparison_charts_offer_more_than_four_and_cap_each_chart(page):
    page.select("Is the hierarchy ranking stable?")
    assert page.compare_button.text() == "Compare: top 4"
    _choose(page, 8)
    assert _drawn(page) == 8
    assert page.compare_button.text() == "Compare: top 8"
    assert [page.show_box.itemText(i) for i in range(page.show_box.count())][-1] == "Top 12"
    page.select("When did each hierarchy's downtime build up?")
    assert [page.show_box.itemText(i) for i in range(page.show_box.count())] == [
        "Top 4", "Top 5", "Top 6"]
    assert "six lines" in page.show_box.toolTip()


def test_picked_hierarchies_go_past_four_and_the_overlay_says_it_cut_them(page):
    refs = [100 + i for i in range(1, 9)]
    page.set_compare(refs)
    assert page.selected_hierarchies() == refs
    page.select("Is the hierarchy ranking stable?")
    assert _drawn(page) == 8
    assert page.picker("compare").limit == 12
    page.select("When did each hierarchy's downtime build up?")
    assert "Showing the first 6 of 8 picked" in page.selected_spec().caption
    assert page.picker("compare").limit == 6
    assert not page.show_box.isVisibleTo(page)    # the picks decide the count


def test_all_items_lists_the_11th_item_and_beyond(page, monkeypatch):
    page.select("Who loses the capacity")
    assert _drawn(page) == 10
    shown = {}

    def run(dialog):
        shown["dialog"] = dialog
        shown["names"] = [dialog.table_widget.item(r, 1).text()
                          for r in range(dialog.table_widget.rowCount())]
        return 0
    monkeypatch.setattr(QDialog, "exec", run)
    assert page.show_all_items()
    assert len(shown["names"]) == N               # not 10
    assert shown["names"][10] == "E11" and shown["names"][-1] == f"E{N:02d}"
    headings = [c.title for c in shown["dialog"].ranked.columns]
    assert headings[:2] == ["rank", "name"] and "cumulative share" in headings
    dialog = shown["dialog"]
    # sortable by the number, not the text
    from PySide6.QtCore import Qt
    dialog.table_widget.sortItems(0, Qt.SortOrder.DescendingOrder)
    assert dialog.table_widget.item(0, 1).text() == f"E{N:02d}"


def test_the_all_items_button_opens_the_table(page, monkeypatch):
    page.select("Who causes the downtime")
    opened = []
    monkeypatch.setattr(ranked_items_dialog, "show_ranked_items",
                        lambda spec, parent=None: opened.append(spec) or True)
    page.items_button.click()
    assert len(opened) == 1 and len(opened[0].series) == 10


def test_export_csv_writes_every_item(page, tmp_path, monkeypatch):
    page.select("Which components fail most often?")
    assert _drawn(page) == 10
    path = tmp_path / "all.csv"
    monkeypatch.setattr("raptor.gui.ui.chart_widget.ask_chart_path",
                        lambda *a, **k: str(path))
    captured = {}

    def run(dialog):
        captured["dialog"] = dialog
        dialog.export_button.click()
        return 0
    monkeypatch.setattr(QDialog, "exec", run)
    assert page.show_all_items()
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[0][:2] == ["rank", "name"]
    assert len(rows) == 1 + N
    assert rows[11][1] == "C11"
    assert rows[-1][1] == f"C{N:02d}"


def test_the_export_menu_and_chart_menu_save_all_items(page, tmp_path, monkeypatch):
    page.select("Who causes the downtime")
    path = tmp_path / "menu.csv"
    monkeypatch.setattr("raptor.gui.ui.chart_widget.ask_chart_path",
                        lambda *a, **k: str(path))
    page.all_items_action.trigger()
    with open(path, newline="", encoding="utf-8") as handle:
        assert len(list(csv.reader(handle))) == 1 + N
    # the right-click menu lists the same two actions
    texts = [action.text() for action in
             page.chart.context_menu(page.chart.rect().center()).actions()]
    assert "All items…" in texts and "Export all items (CSV)…" in texts
    page.select("Run-to-run outcome")
    texts = [action.text() for action in
             page.chart.context_menu(page.chart.rect().center()).actions()]
    assert "All items…" not in texts


def test_save_all_items_csv_works_for_a_folded_chart_too(page, tmp_path):
    page.select("Who loses the capacity")
    page.fold_box.setCurrentIndex(page.fold_box.findData("top:10"))
    assert any("others" in str(r.get("name")) for r in page.chart._spec.series)
    out = tmp_path / "folded.csv"
    page.save_all_items_csv(str(out))
    with open(out, newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 1 + N
    assert not any("others" in row[1] for row in rows[1:])


def test_a_chart_with_many_rows_grows_and_scrolls(page):
    page.select("Who loses the capacity")
    _choose(page, 5)
    short = page.chart.minimumHeight()
    _choose(page, None)
    assert page.chart.minimumHeight() > short
    assert ranking.is_ranked(page.selected_spec())
