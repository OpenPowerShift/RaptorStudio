"""Headless GUI tests for semantic zoom and zoom-to-selection (ZOOM-2).

* level-of-detail: element text hides when zoomed out past
  ``LOD_LABELS_MIN_SCALE`` and returns when zoomed back in;
* ``zoom_to_selection`` fits the selected items;
* ``zoom_to_hierarchy`` descends into a selected hierarchy oval.

These are pure view-layer behaviours: none may touch the model or the
saved file.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from raptor.core.fileio.rbd import load_rbd  # noqa: E402
from raptor.gui.ui.diagram import LOD_LABELS_MIN_SCALE  # noqa: E402
from raptor.gui.ui.main_window import MainWindow  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests/golden/HierarchyExample/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(FIXTURE), None)
    return w


def _first_block_label(view):
    bid = sorted(view._block_items)[0]
    return view._block_items[bid]._label


def test_lod_hides_labels_zoomed_out(window):
    v = window._diagram_dock.view
    v.set_zoom_percent(2)            # 0.02: below even the far floor
    assert v.zoom_percent() <= int(LOD_LABELS_MIN_SCALE * 100)
    assert v._lod_labels_visible is False
    assert _first_block_label(v).isVisible() is False
    node = next(iter(v._node_items.values()))
    assert node._name_label.isVisible() is False


def test_lod_shows_labels_zoomed_in(window):
    v = window._diagram_dock.view
    v.set_zoom_percent(2)
    assert _first_block_label(v).isVisible() is False
    v.set_zoom_percent(100)           # 1.00 >= threshold
    assert v._lod_labels_visible is True
    assert _first_block_label(v).isVisible() is True


def test_zoom_to_selection_fits_selected(window):
    v = window._diagram_dock.view
    bid = sorted(v._block_items)[0]
    item = v._block_items[bid]
    item.setSelected(True)
    assert len(v._scene.selectedItems()) == 1
    n = v.zoom_to_selection()
    assert n == 1
    assert v._user_zoomed is True


def test_zoom_to_selection_without_selection_is_noop(window):
    v = window._diagram_dock.view
    v._scene.clearSelection()
    assert v.zoom_to_selection() == 0


def test_zoom_to_hierarchy_enters_selected_oval(window):
    v = window._diagram_dock.view
    hid = sorted(v._hier_items)[0]
    v._hier_items[hid].setSelected(True)
    assert v.zoom_to_hierarchy() is True
    assert v._current_parent == hid


def test_zoom_to_hierarchy_without_selection_is_false(window):
    v = window._diagram_dock.view
    v._scene.clearSelection()
    assert v.zoom_to_hierarchy() is False


def test_fit_items_empty_is_false(window):
    v = window._diagram_dock.view
    assert v.fit_items([]) is False


def test_labels_forced_context_restores(window):
    v = window._diagram_dock.view
    v.set_zoom_percent(2)
    assert _first_block_label(v).isVisible() is False
    with v.labels_forced():
        assert _first_block_label(v).isVisible() is True
    assert _first_block_label(v).isVisible() is False


def test_labels_forced_restores_on_exception(window):
    v = window._diagram_dock.view
    v.set_zoom_percent(2)
    with pytest.raises(RuntimeError), v.labels_forced():
        raise RuntimeError("boom")
    assert v._lod_enabled is True
    assert _first_block_label(v).isVisible() is False


def test_new_block_respects_lod(window):
    """An element inserted while zoomed out must not show its label."""
    v = window._diagram_dock.view
    v.set_zoom_percent(2)
    assert v._lod_labels_visible is False
    from raptor.core.fileio.rbdlib import make_block_row
    bid = v._insert_block(make_block_row(9001, name="Newbie"))
    assert v._block_items[bid]._label.isVisible() is False


def test_zoom_to_hierarchy_reports_refusal(window, monkeypatch):
    """Truthful result when descending is refused (e.g. depth cap)."""
    v = window._diagram_dock.view
    hid = sorted(v._hier_items)[0]
    v._hier_items[hid].setSelected(True)
    monkeypatch.setattr(v, "enter_level", lambda _hid: None)
    assert v.zoom_to_hierarchy() is False


def test_ribbon_actions_exist_and_enabled(window):
    # Zoom to Selection follows what it can act on: nothing selected means
    # nothing to fit, so it starts greyed out (see test_selection_commands).
    assert not window._zoom_selection_action.isEnabled()
    view = window._diagram_dock.view
    view._block_items[next(iter(view._block_items))].setSelected(True)
    window._refresh_commands()
    assert window._zoom_selection_action.isEnabled()
    # Zoom to Hierarchy needs a hierarchy oval to be selected; with none
    # selected it is greyed out and says why (see test_selection_commands).
    assert not window._zoom_hierarchy_action.isEnabled()
    assert "Unavailable" in window._zoom_hierarchy_action.toolTip()
    assert window._zoom_selection_action.shortcut().toString() == "Ctrl+Shift+0"


def test_lod_disabled_forces_labels_visible(window):
    v = window._diagram_dock.view
    v.set_zoom_percent(2)
    assert _first_block_label(v).isVisible() is False
    v.set_lod_enabled(False)
    assert _first_block_label(v).isVisible() is True
    v.set_lod_enabled(True)
    assert _first_block_label(v).isVisible() is False


def test_export_render_restores_lod(window):
    from PySide6.QtCore import QRectF
    v = window._diagram_dock.view
    v.set_zoom_percent(2)
    assert v._lod_labels_visible is False
    rect = getattr(v, "_content_rect", None) or \
        QRectF(v._scene.itemsBoundingRect())
    window._render_scene_png(QRectF(rect))     # must not leave LOD off
    assert v._lod_labels_visible is False
    assert v._lod_enabled is True


def test_lod_is_view_only(window):
    """LOD must not mutate the model: block positions survive a zoom out."""
    v = window._diagram_dock.view
    before = {bid: (b.x_position, b.y_position)
              for bid, b in v.blocks.items()}
    v.set_zoom_percent(5)
    assert v._lod_labels_visible is False
    after = {bid: (b.x_position, b.y_position) for bid, b in v.blocks.items()}
    assert before == after


# ---------------------------------------------------------------------------
# Miniature mode: below a quarter scale the canvas is shapes only.
#
# There is deliberately no counter-scaled-name middle mode: holding a name
# at its 100%-zoom size meant x5/x10 labels over the topology.
# ---------------------------------------------------------------------------

def test_below_a_quarter_scale_the_canvas_is_shapes_only(window):
    v = window._diagram_dock.view
    v.set_zoom_percent(15)
    assert LOD_LABELS_MIN_SCALE > 0.15
    assert v._lod_mode == "off"
    assert v._lod_labels_visible is False
    assert not _first_block_label(v).isVisible()
    blk = v._block_items[sorted(v._block_items)[0]]
    assert not blk._sub.isVisible()
    node = next(iter(v._node_items.values()))
    assert not node._name_label.isVisible()


def test_names_return_at_a_quarter_scale(window):
    v = window._diagram_dock.view
    v.set_zoom_percent(40)
    assert v._lod_mode == "near"
    assert _first_block_label(v).isVisible()
    assert _first_block_label(v).scale() == pytest.approx(1.0)


def test_no_label_is_counter_scaled_when_zoomed_out(window):
    """Names used to be scaled up to x10 below the near threshold; a
    miniature keeps every label at its normal size when it shows it."""
    v = window._diagram_dock.view
    v.set_zoom_percent(40)
    assert v._lod_mode == "near"
    assert _first_block_label(v).scale() == pytest.approx(1.0)
    v.set_zoom_percent(15)
    assert v._lod_mode == "off"


def test_hierarchy_names_hide_with_the_rest_in_the_miniature(qapp):
    """A zoomed-out model is a clean topology: ovals do not keep names the
    blocks have lost."""
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.resize(470, 410)
    v.load_rbd(load_rbds("new_docs/models/WaterTreatment.rbds"))
    v.show()
    qapp.processEvents()
    v.fit_all() if hasattr(v, "fit_all") else None
    v.set_zoom_percent(10)
    v._apply_lod()
    assert v._lod_mode == "off"
    assert v._hier_items
    assert not any(it._label.isVisible() for it in v._hier_items.values())
