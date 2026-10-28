"""The Run panel's Cores field (b7ab4531) and its aligned notes (afb1a404).

The ribbon's Cores box and the panel's field are one setting, an app
setting (`cores.KEY`) that never goes into the model.
"""
from __future__ import annotations

import copy

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from raptor.gui import cores

pytestmark = pytest.mark.gui

MODEL = "tests/golden/TwoBlockRedundancy/input.rbd"


@pytest.fixture
def two_cores(monkeypatch):
    """Pretend the machine has 4 cores, so the tests do not depend on it."""
    monkeypatch.setattr(cores, "available", lambda: 4)


@pytest.fixture
def win(two_cores):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.remove(cores.KEY)
    w = MainWindow()
    w._load_into_app(load_rbd(MODEL), MODEL)
    w._live_sim_action.setChecked(False)    # the choice is an app setting
    # The spin boxes were built with available() == 4 by the monkeypatch.
    assert w._cores_spin.maximum() == 4 and w._run_panel._cores.maximum() == 4
    yield w
    w._live_sim_action.setChecked(False)
    settings.remove("execution/default_animate")
    w._dirty = False
    w.close()
    settings.remove(cores.KEY)


def test_panel_set_cores_shows_value_note_and_tip_without_emitting(two_cores):
    from raptor.gui.ui.run_panel import RunPanel
    QApplication.instance() or QApplication([])
    p = RunPanel()
    seen = []
    p.cores_changed.connect(seen.append)
    p.set_cores(3, False, "A run is using these cores.", "tip text")
    assert p._cores.value() == 3 and not p._cores.isEnabled()
    assert p._cores.toolTip() == "tip text"
    assert p._cores_note.text() == "A run is using these cores."
    assert not p._cores_note.isHidden()
    p.set_cores(2, True, "", "")
    assert p._cores.value() == 2 and p._cores.isEnabled()
    assert p._cores_note.text() == "" and p._cores_note.isHidden()
    assert seen == [], "set_cores is display only"


def test_editing_the_panel_field_emits_cores_changed(two_cores):
    from raptor.gui.ui.run_panel import RunPanel
    QApplication.instance() or QApplication([])
    p = RunPanel()
    seen = []
    p.cores_changed.connect(seen.append)
    p._cores.setValue(3)
    assert seen == [], "emitted when editing finishes, not per keystroke"
    p._cores.editingFinished.emit()
    assert seen == [3]


def test_panel_field_drives_the_ribbon_box_and_the_setting(win):
    p = win._run_panel
    p._cores.setValue(3)
    p._cores.editingFinished.emit()
    assert cores.chosen() == 3
    assert win._cores_spin.value() == 3
    assert win._cores_chip.text().startswith("3/4 cores")
    assert p._cores.value() == 3


def test_panel_drives_the_read_only_ribbon_box(win):
    assert win._cores_spin.isReadOnly()
    p = win._run_panel._cores
    p.setValue(2); p.editingFinished.emit()
    assert cores.chosen() == 2
    assert win._cores_spin.value() == 2
    p.setValue(4); p.editingFinished.emit()
    assert win._cores_spin.value() == 4
    assert win._cores_chip.text().startswith("4/4 cores")


def test_animate_greys_the_panel_field_and_says_why(win):
    p = win._run_panel
    assert p._cores.isEnabled() and p._cores_note.isHidden()
    win._live_sim_action.setChecked(True)
    assert not p._cores.isEnabled()
    assert not win._cores_spin.isEnabled()
    assert not p._cores_note.isHidden()
    assert "Animate is on" in p._cores_note.text()
    assert "one core" in p._cores_note.text()
    assert "Animate is on" in p._cores.toolTip()
    win._live_sim_action.setChecked(False)
    assert p._cores.isEnabled() and win._cores_spin.isEnabled()
    assert p._cores_note.text() == "" and p._cores_note.isHidden()


def test_animate_leaves_the_chosen_number_alone(win):
    p = win._run_panel._cores
    p.setValue(3); p.editingFinished.emit()
    win._live_sim_action.setChecked(True)
    assert cores.chosen() == 3
    assert win._run_panel._cores.value() == 3
    assert "(Animate)" in win._cores_chip.text()
    assert win._cores_chip.text().startswith("1/4 cores")


def test_a_running_batch_greys_it_with_its_own_reason(win):
    win._run_in_progress = True
    try:
        win._sync_cores_box()
        assert not win._run_panel._cores.isEnabled()
        assert win._run_panel._cores_note.text() == \
            "A run is using these cores."
    finally:
        win._run_in_progress = False
    win._sync_cores_box()
    assert win._run_panel._cores.isEnabled()


def test_the_setting_is_an_app_setting_never_written_into_the_model(win):
    rbd = win._rbd
    rows_before = copy.deepcopy(
        {name: sec.rows for name, sec in rbd.sections.items()})
    exp_before = copy.deepcopy(win._current_experiment())
    win._dirty = False
    win._run_panel._cores.setValue(3)
    win._run_panel._cores.editingFinished.emit()
    win._run_panel._cores.setValue(2)
    win._run_panel._cores.editingFinished.emit()
    from raptor.gui.ui import settings
    assert settings.get_int(cores.KEY, 0) == 2
    rows_after = {name: sec.rows for name, sec in win._rbd.sections.items()}
    assert rows_after == rows_before
    assert win._current_experiment() == exp_before
    assert not any("core" in k.lower() or "worker" in k.lower()
                   for k in win._current_experiment())
    assert not win._dirty, "changing cores is not a model edit"


def test_the_field_is_in_step_with_a_stored_setting_at_start(two_cores):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value(cores.KEY, 3)
    w = MainWindow()
    try:
        assert w._run_panel._cores.value() == 3
        assert w._cores_spin.value() == 3
    finally:
        w._dirty = False
        w.close()
        settings.remove(cores.KEY)


# -- the Analysis / Recording notes line up (afb1a404) ----------------------

def _notes_in(box):
    return [lb for lb in box.findChildren(QLabel)
            if lb.objectName() == "SectionNote"]


@pytest.mark.parametrize("title", ["Analysis", "Recording"])
def test_notes_start_at_the_same_x_in_one_grid_column(two_cores, title):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QGridLayout, QGroupBox
    from raptor.gui.ui.run_panel import RunPanel
    app = QApplication.instance() or QApplication([])
    p = RunPanel()
    p.resize(520, 900)
    p.show()
    app.processEvents()
    try:
        box = next(b for b in p.findChildren(QGroupBox)
                   if b.title() == title)
        notes = _notes_in(box)
        assert len(notes) >= 2
        grid = next(lay for lay in box.findChildren(QGridLayout))
        for note in notes:
            idx = grid.indexOf(note)
            _r, c, _rs, _cs = grid.getItemPosition(idx)
            assert c == 1, "notes sit in grid column 1"
        xs = {note.mapTo(p, QPoint(0, 0)).x() for note in notes}
        assert len(xs) == 1, f"notes start at different x: {sorted(xs)}"
        # and clear of the checkboxes' column
        boxes = [grid.itemAtPosition(grid.getItemPosition(
            grid.indexOf(n))[0], 0).widget() for n in notes]
        right_edge = max(b.mapTo(p, QPoint(0, 0)).x() + b.width()
                         for b in boxes)
        assert min(xs) >= right_edge
    finally:
        p.close()


def test_the_run_dock_hugs_its_content_instead_of_filling_the_tab(two_cores):
    """It used to stretch to the whole Simulate tab, leaving a field of
    empty space under the last section.

    Capping it from the panel's default `sizeHint()` was not enough: a
    `QScrollArea`'s hint is a fixed guess (~400 px) unrelated to its
    contents, so the dock stayed ~200 px too tall. The height now comes
    from the scrolled body's layout, and is exact in both directions --
    no dead space, and no scrollbar either.
    """
    from PySide6.QtWidgets import QApplication
    from raptor.gui.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    dock = win._run_dock
    dock.raise_()                      # the tab that is on show
    app.processEvents()
    app.processEvents()

    panel = win._run_panel
    assert not dock.isFloating()
    assert not dock.isHidden()
    assert panel._columns > 1, "this width should lay the sections out"
    assert abs(panel.height() - panel.content_height()) <= 2, (
        f"panel is {panel.height()} px for {panel.content_height()} px of "
        f"content")
    assert not panel._scroll.verticalScrollBar().isVisible()
    # The cap is the content plus the dock's own title bar, and it follows
    # the content rather than the screen.
    tb = dock.titleBarWidget()
    assert dock.maximumHeight() == (panel.content_height()
                                    + tb.sizeHint().height())
    # Floating is the user's own window: the cap gets out of the way.
    dock.setFloating(True)
    dock.fit_height()
    assert dock.maximumHeight() == 16777215
    dock.setFloating(False)
    win.close()


def test_opening_the_simulate_ribbon_fits_the_run_panel(two_cores):
    """Reported from use: right after clicking the Run panel, wrong when
    opening the Simulate ribbon instead.

    `_give_canvas_room` sets the shared bottom strip to 150 px at startup.
    A cap only stops the dock being too TALL; nothing grew it back, so with
    a model open the panel got 132 px for 295 px of content and scrolled.
    `raise_()` on an already-visible tab fires no show event, so the ribbon
    path never re-measured. (The previous test missed all of this because
    it never loaded a model.)
    """
    from PySide6.QtWidgets import QApplication
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    from pathlib import Path
    model = str(Path(__file__).resolve().parent.parent.parent
                / "new_docs" / "models" / "Lantern1.rbds")
    win._load_into_app(load_rbds(model), model)
    for _ in range(3):
        app.processEvents()

    win._ribbon.tab_bar.setCurrentIndex(win._simulate_page_index)
    for _ in range(3):
        app.processEvents()

    _dock, panel = win._run_dock, win._run_panel
    assert abs(panel.height() - panel.content_height()) <= 2, (
        f"panel is {panel.height()} px for {panel.content_height()} px of "
        f"content")
    assert not panel._scroll.verticalScrollBar().isVisible()
    win._dirty = False
    win.close()
