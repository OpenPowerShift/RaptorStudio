"""The view options (link style, block labels) are saved in the .rbds."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "new_docs/models/DC2.rbds"


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._open_path(MODEL)
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _pick(combo, key):
    combo.setCurrentIndex(combo.findData(key))


def test_view_options_round_trip_through_the_file(win, tmp_path):
    from raptor.gui.ui.diagram_items import link_routing, set_link_routing
    _pick(win._link_routing_combo, "orthogonal")
    _pick(win._block_label_combo, "rate")
    path = str(tmp_path / "v.rbds")
    assert win._write_model(path)
    from raptor.core.fileio.rbds import load_rbds
    assert load_rbds(path).view == {"link_routing": "orthogonal",
                                    "block_labels": "rate"}
    # Another setting is current when the file is opened: the file wins,
    # and the View controls show it.
    set_link_routing("straight")
    _pick(win._block_label_combo, "mean")
    win._load_into_app(load_rbds(path), path)
    QApplication.processEvents()
    assert link_routing() == "orthogonal"
    assert win._link_routing_combo.currentData() == "orthogonal"
    assert win._block_label_combo.currentData() == "rate"
    # ...and inside a hierarchy the links are drawn that way too.
    assert win._diagram_dock.view.view_options()["link_routing"] == "orthogonal"
    set_link_routing("straight")


def test_a_file_without_view_options_keeps_the_current_ones(win):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.diagram_items import link_routing, set_link_routing
    rbd = load_rbds(MODEL)
    rbd.view = {}
    set_link_routing("angled")
    win._load_into_app(rbd, MODEL)
    assert link_routing() == "angled"
    set_link_routing("straight")


def test_changing_link_style_or_labels_marks_the_file_dirty(win):
    win._dirty = False
    _pick(win._link_routing_combo, "angled")
    assert win._dirty
    win._dirty = False
    _pick(win._block_label_combo, "off")
    assert win._dirty
    _pick(win._link_routing_combo, "straight")
    _pick(win._block_label_combo, "mean")


def test_unknown_values_are_ignored(win):
    from raptor.gui.ui.diagram_items import link_routing
    before = link_routing()
    win._diagram_dock.view.apply_view_options(
        {"link_routing": "wavy", "block_labels": "huge"})
    assert link_routing() == before
