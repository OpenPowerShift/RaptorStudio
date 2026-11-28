"""The canvas state-colour legend: shown only while a Debug session or an
animated run colours the blocks, read from the live colour table, pinned to
the bottom-right of the viewport, never part of the scene."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent.parent
MODEL = str(REPO / "new_docs" / "models" / "Bakery1.rbds")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_settings():
    from PySide6.QtCore import QSettings
    QSettings().clear()
    yield
    QSettings().clear()


@pytest.fixture
def win(qapp):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbds(MODEL), MODEL)
    w.show()
    qapp.processEvents()
    w._dirty = False
    yield w
    w._invalidate_step_session()
    w._dirty = False
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _shown(view) -> bool:
    return view.state_legend_shown()


def test_hidden_by_default(win):
    view = win._diagram_dock.view
    assert not _shown(view)
    assert win._state_legend_action.isChecked()


def test_shown_during_debug_and_hidden_after(qapp, win):
    view = win._diagram_dock.view
    win._step_action.trigger()
    qapp.processEvents()
    assert win._step_session is not None
    assert _shown(view)
    win._invalidate_step_session()
    qapp.processEvents()
    assert not _shown(view)


def test_shown_during_animation_hidden_when_it_ends(win):
    view = win._diagram_dock.view
    view.update_live_states(view.blocks)       # what the animated run paints
    assert _shown(view)
    view.clear_live_states()
    assert not _shown(view)


def test_hidden_when_the_model_closes(win):
    from raptor.core.fileio.rbds import load_rbds
    view = win._diagram_dock.view
    view.update_live_states(view.blocks)
    assert _shown(view)
    view.load_rbd(load_rbds(MODEL))
    assert not _shown(view)


def test_not_shown_for_failure_effects_view(win):
    view = win._diagram_dock.view
    view.set_fev_view_open(True)
    view.update_live_states(view.blocks)
    assert not _shown(view)
    view.set_fev_view_open(False)


def test_view_toggle_hides_shows_and_persists(qapp, win):
    from raptor.gui.ui import settings
    view = win._diagram_dock.view
    view.update_live_states(view.blocks)
    assert _shown(view)
    win._state_legend_action.setChecked(False)      # mid-session
    assert not _shown(view)
    assert settings.get_bool("appearance/state_legend", True) is False
    win._state_legend_action.setChecked(True)
    assert _shown(view)
    win._state_legend_action.setChecked(False)
    view.clear_live_states()
    view.update_live_states(view.blocks)
    assert not _shown(view), "the setting holds for the next session"


def test_entries_match_the_colour_table_exactly(win):
    from raptor.gui.ui.diagram_live import LiveOverlayMixin
    from raptor.gui.ui.state_legend import ORDER, legend_entries
    table = LiveOverlayMixin._LIVE_STATE_COLOURS
    entries = legend_entries()
    assert {k: c for k, _l, c in entries} == dict(table)
    assert len(entries) == len(table)
    assert all(label for _k, label, _c in entries)
    # a state added to the table without a place in ORDER still gets an entry
    assert set(ORDER) <= set(table)
    view = win._diagram_dock.view
    view.update_live_states(view.blocks)
    assert view._state_legend.entries == entries


def test_positioned_bottom_right_after_resize(qapp, win):
    view = win._diagram_dock.view
    view.update_live_states(view.blocks)
    for size in ((1000, 700), (700, 500)):
        win.resize(*size)
        qapp.processEvents()
        vp = view.viewport().rect()
        g = view._state_legend.geometry()
        assert vp.contains(g)
        assert vp.right() - g.right() <= 12
        assert vp.bottom() - g.bottom() <= 12
        assert g.left() > vp.center().x() - g.width()   # right-aligned


def test_transparent_to_mouse_and_not_in_the_scene(win):
    from PySide6.QtCore import Qt
    view = win._diagram_dock.view
    before = len(view._scene.items())
    view.update_live_states(view.blocks)
    legend = view._state_legend
    assert legend.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert legend.parent() is view.viewport()
    assert len(view._scene.items()) == before
    assert legend not in [i for i in view._scene.items()
                          if hasattr(i, "widget") and i.widget() is legend]
