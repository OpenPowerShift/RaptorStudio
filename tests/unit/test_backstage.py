"""The full-page File view: start page, examples gallery, recent list.

It opens at start-up (`MainWindow.show_start_page`), so the first screen
says what to do -- a blank model, an example, a recent file, or the User
Guide -- instead of an empty canvas.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from raptor.gui.examples import EXAMPLES, TUTORIALS, thumbnail

pytestmark = pytest.mark.gui

ROOT = Path(__file__).resolve().parents[2]
MODEL = str(ROOT / "tests/golden/TwoBlockRedundancy/input.rbd")


@pytest.mark.parametrize("ex", (*EXAMPLES, *TUTORIALS),
                         ids=lambda e: e.file)
def test_every_example_exists_and_has_both_thumbnails(ex):
    assert (ROOT / "new_docs" / "models" / ex.file).is_file()
    for dark in (False, True):
        assert thumbnail(ex, dark).is_file(), (
            f"{thumbnail(ex, dark).name} missing: run "
            "tools/make_example_thumbnails.py")


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("files/recent", [MODEL])
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    yield w
    w._dirty = False
    w.close()


def test_start_up_opens_on_the_home_page(win):
    win.show_start_page()
    bs = win._backstage
    assert bs.isVisible()
    assert bs._pages.currentWidget() is bs._home_page
    rows = bs._home_page.findChildren(QPushButton, "BackstageRecentRow")
    assert [r.toolTip() for r in rows] == [MODEL]


def test_the_start_page_can_be_turned_off(win):
    from raptor.gui.ui import settings
    from raptor.gui.ui.backstage import START_KEY
    settings.set_value(START_KEY, False)
    try:
        win.show_start_page()
        assert getattr(win, "_backstage", None) is None \
            or not win._backstage.isVisible()
    finally:
        settings.set_value(START_KEY, True)


def test_no_start_page_over_an_open_model(win):
    from raptor.core.fileio.rbd import load_rbd
    win._load_into_app(load_rbd(MODEL), MODEL)
    win.show_start_page()
    assert getattr(win, "_backstage", None) is None \
        or not win._backstage.isVisible()


def test_the_new_page_lists_every_tutorial_example(win):
    win._show_backstage("New")
    shown = {c.property("example").file for c in win._backstage._cards
             if c.property("example") is not None}
    assert {ex.file for ex in (*EXAMPLES, *TUTORIALS)} <= shown


def test_an_example_opens_as_an_unsaved_copy(win):
    win._show_backstage("New")
    card = next(c for c in win._backstage._cards
                if c.property("example") is EXAMPLES[0])
    card.click()
    assert not win._backstage.isVisible()
    assert win._current_file is None          # Save cannot overwrite it
    assert win._rbd is not None and win._diagram_dock.view.blocks


def test_the_blank_card_makes_a_new_model(win):
    win._show_backstage("New")
    blank = next(c for c in win._backstage._cards
                 if c.property("example") is None)
    blank.click()
    assert win._rbd is not None
    assert [b.name for b in win._diagram_dock.view.blocks.values()] == ["Block_1"]


def test_search_filters_the_recent_list(win, tmp_path):
    from raptor.gui.ui import settings
    a, b = tmp_path / "pump_station.rbd", tmp_path / "boiler.rbd"
    for p in (a, b):
        p.write_bytes(Path(MODEL).read_bytes())
    settings.set_value("files/recent", [str(a), str(b)])
    win._show_backstage("Recent")
    bs = win._backstage
    bs._search.setText("pump")
    assert bs.visible_paths() == [str(a)]
    bs._search.setText("nothing-like-this")
    assert bs.visible_paths() == [] and bs._empty.isVisible()


def test_print_and_export_tiles_refresh_their_unavailable_reason(win):
    """The File view is built before a model opens; its tiles must follow
    the actions afterwards, not keep "open or create a model first"."""
    win._show_backstage("Print and Export")
    bs = win._backstage
    assert all("Unavailable:" in tile.toolTip() for tile in bs._tiles
               if not tile.property("tileAction").isEnabled())
    win._new_file()
    QApplication.processEvents()
    assert all("Unavailable:" not in tile.toolTip() for tile in bs._tiles)
    assert all(tile.isEnabled() for tile in bs._tiles)


def test_ai_assistant_is_a_top_level_file_view_link_to_the_docs(
        win, monkeypatch):
    fired = []
    monkeypatch.setattr(
        win, "_open_doc",
        lambda name, anchor=None: fired.append((name, anchor)))
    win._show_backstage()
    bs = win._backstage
    button = next(b for b in bs.findChildren(QPushButton, "BackstageNavItem")
                  if b.text() == "AI Assistant")
    button.click()
    assert fired == [("manual.html", "ai-assistant")]
    assert not bs.isVisible()          # the help viewer replaces the view


def test_the_learn_links_run_the_help_commands(win, monkeypatch):
    fired = []
    monkeypatch.setattr(win._guide_action, "trigger", lambda: fired.append(1))
    win._show_backstage("Home")
    btn = next(b for b in win._backstage.findChildren(QPushButton, "BackstageLearn")
               if b.text() == "User Guide")
    btn.click()
    assert fired == [1]


def test_it_follows_the_window_size(win):
    win._show_backstage()
    win.resize(1100, 700)
    QApplication.processEvents()
    assert win._backstage.size() == win.size()


def test_escape_closes_the_file_view(win):
    """Two Esc shortcuts (the window's and the File view's) were ambiguous,
    so Qt fired neither and Esc never closed the view."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app = QApplication.instance()
    win.show_start_page()
    app.processEvents()
    assert win._backstage.isVisible()
    QTest.keyClick(win, Qt.Key.Key_Escape)
    app.processEvents()
    assert not win._backstage.isVisible()


def test_a_dock_raised_while_the_file_view_is_open_stays_under_it(win):
    """A run finishing raised the Results dock over the File view."""
    from PySide6.QtWidgets import QWidget
    win._show_backstage()
    app = QApplication.instance()
    win._results_dock.raise_()
    app.processEvents()
    widgets = [c for c in win.children() if isinstance(c, QWidget)
               and c.isVisible()]
    assert widgets[-1] is win._backstage


def test_no_model_ok_badge_without_a_model(win):
    win._dirty = False
    win._close_current_file()
    QApplication.processEvents()
    assert not win._validation_badge.isVisible()
    win._new_file()
    QApplication.processEvents()
    assert win._validation_badge.isVisible()
    assert win._untitled_save_path().endswith("Untitled.rbds")


def test_down_arrow_from_the_search_box_moves_into_the_list(win):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    win._show_backstage("Recent")
    bs = win._backstage
    QTest.keyClick(bs._search, Qt.Key.Key_Down)
    assert bs._list.hasFocus() or bs._list.currentItem() is not None
    assert bs._list.currentItem() is bs._list.topLevelItem(0)
