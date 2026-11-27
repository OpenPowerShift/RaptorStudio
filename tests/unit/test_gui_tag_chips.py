"""The Inspector's tag chips: the add box lists the model's other tags."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from raptor.gui.ui.tag_chips import TagChips

pytestmark = pytest.mark.gui

KNOWN = ["Critical", "Pumps", "Spares held", "Wet area"]


@pytest.fixture(autouse=True, scope="module")
def _qapp():
    # the widgets need an application; without this the file crashed when
    # run on its own (it only passed after another GUI test made one)
    return QApplication.instance() or QApplication([])


def _chips(tags=("Critical",), known=KNOWN):
    chips = TagChips(list(tags), list(known), {"Pumps": "#1f77b4"},
                     describe={"Pumps": "Pump skids"})
    chips.resize(320, 80)
    chips.show()
    QApplication.processEvents()
    return chips


def _listed(chips: TagChips) -> list[str]:
    model = chips._completer.completionModel()
    return [model.index(i, 0).data() for i in range(model.rowCount())]


def _popup_open(chips: TagChips) -> bool:
    return chips._popup_visible()


def test_the_arrow_lists_every_tag_not_on_the_element():
    chips = _chips()
    assert chips.arrow_action is not None
    assert chips.arrow_action.toolTip() and chips.arrow_action.text()
    chips.arrow_action.trigger()
    QApplication.processEvents()
    assert _popup_open(chips)
    assert _listed(chips) == ["Pumps", "Spares held", "Wet area"]


def test_clicking_the_empty_box_lists_them():
    chips = _chips()
    QTest.mouseClick(chips.add_edit, Qt.MouseButton.LeftButton,
                     pos=QPoint(10, chips.add_edit.height() // 2))
    QApplication.processEvents()
    assert _popup_open(chips)
    assert _listed(chips) == ["Pumps", "Spares held", "Wet area"]


def test_down_in_the_box_lists_them():
    chips = _chips()
    chips.add_edit.setFocus()
    QTest.keyClick(chips.add_edit, Qt.Key.Key_Down)
    QApplication.processEvents()
    assert _popup_open(chips)
    assert _listed(chips) == ["Pumps", "Spares held", "Wet area"]


def test_plain_focus_opens_no_popup():
    chips = _chips()
    chips.add_edit.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    assert not _popup_open(chips)


def test_typing_still_filters_with_contains_and_ignores_case():
    chips = _chips()
    chips.add_edit.setFocus()
    QTest.keyClicks(chips.add_edit, "ELD")
    QApplication.processEvents()
    assert _listed(chips) == ["Spares held"]


def test_rows_carry_a_swatch_and_the_description_tooltip():
    chips = _chips()
    row = {chips._model.item(i).text(): chips._model.item(i)
           for i in range(chips._model.rowCount())}
    assert not row["Pumps"].icon().isNull()
    assert row["Pumps"].toolTip() == "Pump skids"
    assert row["Wet area"].toolTip() == ""


def test_choosing_a_row_emits_the_whole_new_list_once():
    chips = _chips()
    seen: list[list[str]] = []
    chips.tags_edited.connect(seen.append)
    chips.show_tags()
    QApplication.processEvents()
    chips._completer.activated.emit("Spares held")
    assert seen == [["Critical", "Spares held"]]


def test_a_new_name_still_makes_a_new_tag():
    chips = _chips()
    seen: list[list[str]] = []
    chips.tags_edited.connect(seen.append)
    chips.add_edit.setText("Brand new")
    chips.add_edit.returnPressed.emit()
    assert seen == [["Critical", "Brand new"]]


def test_tags_on_the_element_are_not_offered():
    chips = _chips(tags=("Critical", "Pumps"))
    chips.show_tags()
    assert _listed(chips) == ["Spares held", "Wet area"]


def test_no_other_tags_means_no_arrow_and_no_popup():
    chips = _chips(tags=KNOWN)
    assert chips.arrow_action is None
    assert chips.add_edit.placeholderText() == "Add tag…"
    chips.show_tags()
    QTest.keyClick(chips.add_edit, Qt.Key.Key_Down)
    QApplication.processEvents()
    assert not _popup_open(chips)


def test_the_chip_is_as_tall_as_the_add_box_and_the_remove_sits_inside():
    chips = _chips()
    from PySide6.QtWidgets import QFrame
    chip = chips.findChild(QFrame, "TagChip")
    assert chip.height() == chips.add_edit.height()
    remove = chip.layout().itemAt(chip.layout().count() - 1).widget()
    assert chip.width() - (remove.x() + remove.width()) >= 3
    assert remove.y() >= 0 and remove.y() + remove.height() <= chip.height()
