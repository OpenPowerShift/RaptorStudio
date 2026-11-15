"""Results > Show on canvas: share of downtime and lost capacity layers."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "tests/fixtures/ReleaseProbe32/input.rbd"


@pytest.fixture
def win():
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("files/save_before_run", "never")
    w = MainWindow()
    w._load_into_app(load_rbd(MODEL), MODEL)
    exp = dict(w._current_experiment())
    w._set_exp_field("analyses", sorted(set(exp["analyses"]) | {"capacity"}))
    w._set_exp_field("trials", 6)
    w._confirm_long_batch = lambda n: True
    w._run_simulation()
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _top_block(w):
    v = w._diagram_dock.view
    rows = v._last_result_visuals["layer"]["elements"]
    return next((v._block_items[r["id"]], r) for r in rows
                if r["kind"] == "block" and r["id"] in v._block_items)


def test_the_downtime_layer_colours_letters_and_labels_the_blocks(win):
    action = win._downtime_overlay_action
    assert action.isEnabled()
    action.setChecked(True)
    QApplication.processEvents()
    assert action.isChecked()
    assert win._overlay_combo.currentText() == "Share of downtime"
    item, row = _top_block(win)
    assert item._live_colour is not None
    assert item._result_badge[0] == row["glyph"]
    assert item._result_ring == row["uncertain"]
    assert item._anal_label.toPlainText().startswith(f"{100 * row['value']:.3g} %")
    assert "largest share of system downtime" in win._statusbar.currentMessage()
    # every hierarchy on the level shows what its contents add up to
    view = win._diagram_dock.view
    assert any(h._anal_label.toPlainText().startswith("Σ")
               for h in view._hier_items.values())


def test_the_lost_capacity_layer_draws_links_as_wide_as_their_flow(win):
    win._lost_capacity_overlay_action.setChecked(True)
    QApplication.processEvents()
    assert not win._downtime_overlay_action.isChecked()   # one layer at a time
    view = win._diagram_dock.view
    widths = {link._flow_width for link in view._links}
    assert 10.0 in widths and 1.0 in widths
    assert view._last_result_visuals["layer"]["layer"] == "lost-capacity"


def test_turning_a_layer_off_clears_rings_and_link_widths(win):
    win._downtime_overlay_action.setChecked(True)
    win._downtime_overlay_action.setChecked(False)
    QApplication.processEvents()
    view = win._diagram_dock.view
    assert all(link._flow_width is None for link in view._links)
    assert not any(item._result_ring for item in view._block_items.values())
    assert view._last_result_visuals is None
