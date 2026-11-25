"""A Charts-list group heading shows an overview of its charts."""
from __future__ import annotations

import itertools
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from raptor.gui import i18n
from raptor.gui.resources.design_tokens import ThemeManager
from raptor.gui.ui import chart_descriptions
from raptor.gui.ui.charts_page import ChartsPage
from tests.unit.test_charts_page import _inputs, _series


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(qapp):
    page = ChartsPage()
    page.resize(900, 600)
    per_run, attribution, importance = _inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    page.set_series(*_series())
    page.show()
    yield page
    page.close()


def heading(page, group):
    return page._heading(group)


def test_a_heading_shows_the_overview_not_the_empty_chart(page):
    page.nav.setCurrentItem(heading(page, "Over time"))
    assert page.selected_group() == "Over time"
    assert page.selected_key() is None
    assert page.stack.currentWidget() is page.overview
    assert page.overview.heading.text() == "Over time"
    assert not page.export_button.isEnabled()
    assert page.selected_spec() is None


def test_arrowing_onto_a_heading_shows_its_overview(page):
    page.select("trial-outcome")
    page.nav.setFocus()
    # one step up from the first chart is its heading
    QTest.keyClick(page.nav, Qt.Key.Key_Up)
    assert page.selected_group() == "The answer"
    assert page.stack.currentWidget() is page.overview


def test_cards_are_the_groups_members_and_unavailable_ones_say_why(page):
    page.nav.setCurrentItem(heading(page, "Over time"))
    keys = [card.data.key for card in page.overview.cards]
    members = [h.child(i).data(0, Qt.ItemDataRole.UserRole)
               for h in [heading(page, "Over time")]
               for i in range(h.childCount())]
    assert keys == members
    page.set_inputs(per_run=_inputs()[0], units="hours")
    page.nav.setCurrentItem(heading(page, "Cost"))
    card = page.overview.cards[0]
    assert not card.available
    assert "Cost" in card.text.text()
    assert card.data.description != card.text.text()


def test_every_chart_has_a_description(page):
    for entry in page._entries.values():
        assert chart_descriptions.describe(entry.key), entry.key


def test_clicking_a_card_opens_its_chart(page):
    page.nav.setCurrentItem(heading(page, "The answer"))
    card = page.overview.cards[1]
    QTest.mouseClick(card, Qt.MouseButton.LeftButton)
    assert page.selected_key() == card.data.key
    assert page.stack.currentWidget() is page.chart_scroll


def test_enter_and_space_open_a_card(page):
    page.nav.setCurrentItem(heading(page, "The answer"))
    card = page.overview.cards[2]
    card.setFocus()
    QTest.keyClick(card, Qt.Key.Key_Return)
    assert page.selected_key() == card.data.key
    page.nav.setCurrentItem(heading(page, "The answer"))
    page.overview.cards[3].setFocus()
    QTest.keyClick(page.overview.cards[3], Qt.Key.Key_Space)
    assert page.selected_key() == page.overview.cards[3].data.key


def test_an_unavailable_card_opens_the_explanation(page):
    page.set_inputs(per_run=_inputs()[0], units="hours")
    page.nav.setCurrentItem(heading(page, "Cost"))
    QTest.mouseClick(page.overview.cards[0], Qt.MouseButton.LeftButton)
    assert page.stack.currentIndex() == 1
    assert page.missing_text.text()


def _click(page, item, dx=40):
    rect = page.nav.visualItemRect(item)
    point = QPoint(rect.left() + dx, rect.center().y())
    QTest.mouseClick(page.nav.viewport(), Qt.MouseButton.LeftButton, pos=point)


def test_folding_still_works(page):
    head = heading(page, "Over time")
    assert head.isExpanded()
    # the arrow only folds
    _click(page, head, dx=3)
    assert not head.isExpanded()
    assert page.selected_group() != "Over time"
    _click(page, head, dx=3)
    assert head.isExpanded()
    # the text shows the overview, and a second click folds
    _click(page, head)
    assert page.selected_group() == "Over time" and head.isExpanded()
    _click(page, head)
    assert not head.isExpanded()
    assert page.selected_group() == "Over time"
    # clicking a folded, other heading unfolds it and shows it
    other = heading(page, "Why it goes down")
    other.setExpanded(False)
    _click(page, other)
    assert other.isExpanded() and page.selected_group() == "Why it goes down"


def test_the_empty_chart_is_never_shown_for_a_heading(page):
    for head in page._groups():
        page.nav.setCurrentItem(head)
        assert page.stack.currentWidget() is page.overview
        assert page.overview.cards


def test_pictures_are_drawn_lazily_then_cached(page):
    page.nav.setCurrentItem(heading(page, "The answer"))
    overview = page.overview
    assert overview.pending() > 0           # the page is up before the pictures
    assert not overview.cards[0].thumb.has_picture()
    overview.flush()
    assert all(card.thumb.has_picture() for card in overview.cards)
    first = page._thumbs["trial-outcome"][2]
    # the same result reuses it
    page.nav.setCurrentItem(heading(page, "Over time"))
    page.nav.setCurrentItem(heading(page, "The answer"))
    assert overview.pending() == 0
    assert page._thumb_cached("trial-outcome", page.overview._render_width) is first


def test_pictures_are_redrawn_for_new_inputs_and_theme(page):
    page.nav.setCurrentItem(heading(page, "The answer"))
    page.overview.flush()
    first = page._thumb_cached("trial-outcome", page.overview._render_width)
    assert first is not None
    page.set_inputs(per_run=_inputs()[0], units="hours", run_id="r_2")
    assert page._thumb_cached("trial-outcome", page.overview._render_width) is None
    page.nav.setCurrentItem(heading(page, "The answer"))
    page.overview.flush()
    second = page._thumb_cached("trial-outcome", page.overview._render_width)
    assert second is not None
    # the theme changes the look, so a held picture is not reused
    manager = ThemeManager.instance()
    dark = manager.is_dark
    try:
        manager.set_dark(not dark)
        assert page._thumb_cached("trial-outcome", page.overview._render_width) is None
    finally:
        manager.set_dark(dark)


def test_time_charts_draw_without_switching_the_page(page):
    page.nav.setCurrentItem(heading(page, "Over time"))
    page.overview.flush()
    assert page.stack.currentWidget() is page.overview
    assert page.overview.cards[0].thumb.has_picture()


def test_a_narrow_dock_gets_one_column(page):
    page.nav.setCurrentItem(heading(page, "The answer"))
    page.resize(420, 600)
    page.overview.resize(250, 500)
    QApplication.processEvents()
    page.overview._reflow()
    assert page.overview._columns == 1
    page.resize(1400, 600)
    page.overview.resize(1100, 500)
    page.overview._reflow()
    assert page.overview._columns >= 2


def test_the_overview_reads_in_portuguese(page):
    try:
        i18n.install(QApplication.instance(), "pt_BR")
        page.set_inputs(per_run=_inputs()[0], units="hours", run_id="r_3")
        page.nav.setCurrentItem(heading(page, "The answer"))
        assert page.overview.heading.text() == "A resposta"
        card = page.overview.cards[0]
        assert card.data.title == "Resultado de execução a execução"
        assert card.text.text().startswith("Um ponto por execução")
        page.overview.flush()
        assert page._thumb_cached("trial-outcome", page.overview._render_width) is not None
    finally:
        i18n.install(QApplication.instance(), "en")


def test_scope_controls_do_not_apply_to_the_overview(page):
    page.select("capacity-band")
    page.nav.setCurrentItem(heading(page, "Over time"))
    for widget in (page.floor_button, page.target_button, page.compare_button,
                   page.run_box, page.capacity_axis_button):
        assert not widget.isVisible()


def _reasons(page):
    return [e.missing for e in page._entries.values() if e.missing]


def test_every_reason_and_action_reads_in_portuguese(qapp):
    per_run, attribution, importance = _inputs()
    scenarios = [
        {},
        {"per_run": per_run, "units": "hours"},
        {"per_run": per_run, "units": "hours", "drivers": "pending",
         "model_hierarchies": True, "model_phases": True},
        {"per_run": per_run, "units": "hours", "drivers": "unavailable",
         "model_hierarchies": True},
        {"per_run": per_run, "units": "minutes", "attribution": attribution,
         "importance": importance, "model_hierarchies": True},
        {"per_run": per_run, "units": "hours", "attribution": attribution,
         "model_phases": True},
    ]
    try:
        i18n.install(QApplication.instance(), "pt_BR")
        for kwargs in scenarios:
            page = ChartsPage()
            page.set_inputs(**kwargs)
            page.set_series(*_series() if kwargs.get("attribution") else ([], []))
            for entry in page._entries.values():
                for text in (entry.missing, entry.action):
                    if text:
                        assert i18n.translate_source(text) != text, text
            page.close()
    finally:
        i18n.install(QApplication.instance(), "en")


def test_the_explanation_panel_is_translated_and_english_is_unchanged(page):
    page.set_inputs(per_run=_inputs()[0], units="hours")
    page.select("cost-pareto")
    english = page.missing_text.text()
    assert "Needs the cost analysis" in english
    try:
        i18n.install(QApplication.instance(), "pt_BR")
        page.select("cost-pareto")
        assert "Precisa da análise de custo" in page.missing_text.text()
    finally:
        i18n.install(QApplication.instance(), "en")


def test_pictures_are_drawn_at_the_size_they_are_shown(page):
    page.nav.setCurrentItem(heading(page, "The answer"))
    page.resize(1300, 600)
    page.overview.resize(1000, 500)
    page.overview._reflow()
    page.overview.flush()
    wide = page.overview._render_width
    picture = page._thumb_cached("trial-outcome", wide)
    assert picture is not None
    assert picture.width() == round(wide * picture.devicePixelRatio())
    page.overview.resize(260, 500)
    page.overview._reflow()
    assert page.overview._render_width < wide
    assert page.overview._render_width >= 200


# -- All charts: every group on one page ---------------------------------

def test_all_charts_is_the_first_heading_above_the_answer(page):
    top = [page.nav.topLevelItem(i).text(0) for i in range(2)]
    assert top == ["ALL CHARTS", "THE ANSWER"]
    assert page.nav_combo.itemText(0) == "ALL CHARTS"
    # it is not a group: folding, the search and the group list skip it
    assert "All charts" not in [page._group_name(h) for h in page._groups()]


def test_all_charts_shows_every_group_under_its_own_heading(page):
    page.nav.setCurrentItem(heading(page, "All charts"))
    assert page.selected_group() == "All charts"
    assert page.selected_key() is None
    assert page.stack.currentWidget() is page.overview
    assert page.overview.heading.text() == "All charts"
    groups = [page._group_name(h) for h in page._groups()]
    assert page.overview.section_titles() == groups
    members = [str(h.child(i).data(0, Qt.ItemDataRole.UserRole))
               for h in page._groups() for i in range(h.childCount())]
    assert [card.data.key for card in page.overview.cards] == members
    page.overview.cards[1].setFocus()
    QTest.keyClick(page.overview.cards[1], Qt.Key.Key_Return)
    assert page.selected_key() == members[1]


def test_all_charts_follows_the_search(page):
    page.nav.setCurrentItem(heading(page, "All charts"))
    page.search.setText("outage")
    shown = page.overview.section_titles()
    assert shown and "The answer" not in shown
    assert all("outage" in (card.data.title + card.data.key).lower()
               for card in page.overview.cards)
    assert page.selected_group() == "All charts"
    page.search.setText("no chart is called this")
    assert page.overview.section_titles() == []
    assert page.overview.blurb.text() == "No chart matches the search."
    page.search.clear()
    assert len(page.overview.section_titles()) == len(page._groups())


def test_all_charts_survives_new_inputs(page):
    page.nav.setCurrentItem(heading(page, "All charts"))
    per_run, attribution, importance = _inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_2")
    assert page.selected_group() == "All charts"
    assert page.overview.section_titles()


def test_a_narrow_page_stacks_cards_without_overlap(page):
    page.nav.setCurrentItem(heading(page, "All charts"))
    page.resize(420, 600)
    QApplication.processEvents()
    QApplication.processEvents()
    cards = page.overview.cards
    for above, below in itertools.pairwise(cards):
        if above.parent() is below.parent():
            assert below.geometry().top() >= above.geometry().bottom()
