"""The whole-model-or-one-hierarchy picker: home first with a divider, a
short list, and search by typing."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


ROWS = [(i, f"Unit_{i:03d}") for i in range(1, 301)]


def _picker(**kwargs):
    from raptor.gui.widgets.hierarchy_picker import HierarchyPicker
    picker = HierarchyPicker(**kwargs)
    picker.set_hierarchies(ROWS)
    return picker


def test_home_is_first_with_an_icon_and_a_divider_under_it(qapp):
    picker = _picker()
    assert picker.itemText(0) == "Whole model" and picker.itemData(0) == 0
    assert not picker.itemIcon(0).isNull()
    assert picker.itemText(1) == ""                   # the divider
    assert picker.itemText(2) == "Unit_001"
    assert picker.count() == len(ROWS) + 2
    assert picker.is_home()


def test_the_list_is_short_whatever_the_style(qapp):
    from raptor.gui.widgets.hierarchy_picker import MAX_VISIBLE, picker_style
    picker = _picker()
    assert picker.maxVisibleItems() == MAX_VISIBLE <= 15
    assert "combobox-popup: 0" in picker.styleSheet()
    assert "combobox-popup: 0" in picker_style("border: 1px solid red;")


def test_typing_narrows_to_names_containing_the_text(qapp):
    from PySide6.QtCore import Qt
    picker = _picker()
    completer = picker.completer()
    assert completer.filterMode() == Qt.MatchFlag.MatchContains
    completer.setCompletionPrefix("_27")
    found = {completer.completionModel().index(r, 0).data()
             for r in range(completer.completionCount())}
    assert found == {f"Unit_27{d}" for d in range(10)}


def test_a_typed_name_selects_it_and_nonsense_is_put_back(qapp):
    picker = _picker()
    picker.setCurrentIndex(picker.findData(7))
    picker.setEditText("unit_123")
    picker._restore_text()
    assert picker.currentData() == 123
    picker.setEditText("no such hierarchy")
    picker._restore_text()
    assert picker.currentData() == 123 and picker.currentText() == "Unit_123"


def test_refilling_keeps_the_choice_and_home_goes_back(qapp):
    picker = _picker()
    picker.setCurrentIndex(picker.findData(42))
    picker.set_hierarchies(ROWS[:100])
    assert picker.currentData() == 42
    picker.set_hierarchies(ROWS[:10])                 # 42 is gone
    assert picker.is_home()
    picker.setCurrentIndex(picker.findData(3))
    picker.go_home()
    assert picker.is_home() and picker.currentData() == 0


def test_no_hierarchies_means_no_divider(qapp):
    from raptor.gui.widgets.hierarchy_picker import HierarchyPicker
    picker = HierarchyPicker(home_data=None)
    assert picker.count() == 1 and picker.currentData() is None


def test_the_run_panel_scope_has_a_home_button(qapp):
    from raptor.gui.ui.run_panel import RunPanel
    panel = RunPanel()
    panel.set_hierarchies([(5, "Pumps"), (6, "  Pump_A")])
    seen = []
    panel.scope_changed.connect(seen.append)
    assert not panel._scope_home.isEnabled()
    panel._scope.setCurrentIndex(panel._scope.findData(6))
    assert panel.hierarchy_scope() == 6 and panel._scope_home.isEnabled()
    panel._scope_home.click()
    assert panel.hierarchy_scope() == 0 and seen == [6, 0]
    assert not panel._scope_home.isEnabled()


def test_the_home_button_keeps_its_place_when_options_appears(qapp):
    from raptor.gui.ui.run_panel import RunPanel
    panel = RunPanel()
    panel.resize(1400, 300)
    panel.show()
    panel.set_hierarchies([(5, "Pumps")])
    qapp.processEvents()
    at_home = panel._scope_home.geometry()
    panel._scope.setCurrentIndex(panel._scope.findData(5))
    qapp.processEvents()
    assert panel._scope_button.isVisible() and panel._scope_home.isVisible()
    assert panel._scope_home.geometry() == at_home
    # Left of Options, right of the picker.
    assert (panel._scope.geometry().right() < at_home.left()
            < panel._scope_button.geometry().left())


def test_the_home_button_follows_the_picker(qapp):
    from raptor.gui.widgets.hierarchy_picker import home_button
    picker = _picker(home_data=None)
    button = home_button(picker)
    assert not button.isEnabled()
    picker.setCurrentIndex(picker.findData(9))
    assert button.isEnabled()
    button.click()
    assert picker.is_home() and not button.isEnabled()


def test_the_home_icon_is_redrawn_when_the_theme_changes(qapp):
    """Drawn once in the light theme's text colour, it was dark on dark
    after switching to the dark theme."""
    from raptor.gui.resources.design_tokens import ThemeManager
    from raptor.gui.widgets.hierarchy_picker import home_button
    theme = ThemeManager.instance()
    was_dark = theme.is_dark
    picker = _picker()
    button = home_button(picker)

    def ink(icon):
        image = icon.pixmap(16, 16).toImage()
        colours = [image.pixelColor(x, y) for x in range(16) for y in range(16)]
        opaque = [c for c in colours if c.alpha() > 128]
        return sum(c.lightness() for c in opaque) / max(1, len(opaque))

    try:
        theme.set_dark(False)
        light = (ink(button.icon()), ink(picker.itemIcon(0)))
        theme.set_dark(True)
        dark = (ink(button.icon()), ink(picker.itemIcon(0)))
    finally:
        theme.set_dark(was_dark)
    # Dark ink on the light theme, light ink on the dark one.
    assert light[0] < 128 < dark[0]
    assert light[1] < 128 < dark[1]
