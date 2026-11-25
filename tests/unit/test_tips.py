"""U21: "Did you know?" tips on the Start page."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from raptor.gui.tips import TIPS


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_every_tip_names_a_real_command_and_fills_its_shortcut(qapp):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    try:
        for tip in TIPS:
            assert len(tip.text) < 260, tip.text
            if tip.command:
                assert tip.command in w._commands.specs, tip.command
            else:
                assert "{shortcut}" not in tip.text
        w._show_backstage("Home")
        page = w._backstage
        first = page._tip_index
        page._show_tip(first + 1)
        assert page._tip_index == (first + 1) % len(TIPS)
        assert "{shortcut}" not in page._tip_text.text()
        assert page._tip_count.text().strip().endswith(f"of {len(TIPS)}")
    finally:
        w._dirty = False
        w.close()


def test_the_tip_moves_on_each_launch(monkeypatch):
    from raptor.gui import tips
    from raptor.gui.ui import settings
    store = {}
    monkeypatch.setattr(settings, "get_int", lambda k, d=0: store.get(k, d))
    monkeypatch.setattr(settings, "set_value", lambda k, v: store.__setitem__(k, v))
    seen = [tips.tip_for_launch() for _ in range(len(TIPS) + 1)]
    assert seen[:len(TIPS)] == list(range(len(TIPS))) and seen[-1] == 0


def test_show_me_sits_right_and_shows_only_when_it_can_act(qapp):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    try:
        w._show_backstage("Home")
        page = w._backstage
        needs = next(i for i, t in enumerate(TIPS) if t.command and t.needs_model)
        free = next(i for i, t in enumerate(TIPS) if t.command and not t.needs_model)
        page._show_tip(needs)
        assert page._tip_show.isHidden()          # no model open: nothing to show
        page._show_tip(free)
        assert not page._tip_show.isHidden()      # the command search works anyway
        row = page._tip_show.parentWidget().layout()
        layout = next(row.itemAt(i).layout() for i in range(row.count())
                      if row.itemAt(i).layout() is not None)
        widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
        assert widgets[-1] is page._tip_show       # rightmost, after the stretch
        assert widgets[0].text() == "Next tip"
    finally:
        w._dirty = False
        w.close()
