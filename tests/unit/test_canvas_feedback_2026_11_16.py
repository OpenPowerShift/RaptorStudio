"""U10: the Weak tab's selection filter walked the id->item map and touched a
_BlockItem Qt had already deleted ("Internal C++ object already deleted")."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")



@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


MODEL = Path(__file__).resolve().parents[2] / "new_docs" / "models" / "HospitalPower.rbds"


def test_selected_names_skip_a_deleted_block_item(qapp):
    import shiboken6

    from raptor.core.fileio.model_file import load_model_file
    from raptor.gui.ui.main_window import MainWindow

    w = MainWindow()
    w._load_into_app(load_model_file(str(MODEL)), str(MODEL))
    view = w._diagram_dock.view
    items = [(bid, item) for bid, item in view._block_items.items() if bid != 0]
    (first_id, first), (_second_id, second) = items[0], items[1]
    first.setSelected(True)
    second.setSelected(True)
    shiboken6.delete(second)
    names = w._results._selected_block_names_fn()
    assert names == {view.blocks[first_id].name}


@pytest.mark.parametrize(("kind", "word"), [(1, "Start"), (2, "k-out-of-n"), (3, "End"),
                                            (4, "Hierarchy In"), (5, "Hierarchy Out")])
def test_a_node_says_what_kind_it_is(kind, word):
    """U11: hovering a hierarchy's In or Out said "k-of-n node"."""
    from raptor.gui.ui.diagram_item_core import node_kind_tip
    assert node_kind_tip(kind).startswith(word)
    assert (kind == 2) == ("k-out-of-n" in node_kind_tip(kind))
