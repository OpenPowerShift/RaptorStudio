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


def test_focus_with_a_layer_keeps_the_shares_that_matter(win):
    view = win._diagram_dock.view
    win._downtime_overlay_action.setChecked(True)
    win._focus_action.setChecked(True)
    QApplication.processEvents()
    assert win._focus_box.isVisible() or not win.isVisible()
    assert not win._focus_box.isHidden()
    fractions = view._layer_fractions
    assert fractions and max(fractions.values()) == pytest.approx(1.0)
    win._focus_slider.setValue(50)
    kept = {item for item, f in fractions.items() if f >= 0.5}
    for item in fractions:
        assert item.opacity() == (1.0 if item in kept else 0.25)
    assert view.focus_kept() == len(kept)
    assert f"{len(kept)} kept" in win._focus_label.text()
    win._focus_slider.setValue(0)                  # everything painted stays
    assert all(item.opacity() == 1.0 for item in fractions)
    # with the layer off, focus is the selection alone and the slider goes
    win._downtime_overlay_action.setChecked(False)
    QApplication.processEvents()
    assert view._layer_fractions == {}
    assert win._focus_box.isHidden()
    assert all(item.opacity() == 0.25 for item in view._block_items.values())
    win._focus_action.setChecked(False)
    assert all(item.opacity() == 1.0 for item in view._block_items.values())


def test_the_advisor_layer_marks_what_the_findings_are_about(win):
    from raptor.api.advisor.canvas import advisor_layer
    action = win._advisor_overlay_action
    layer = advisor_layer(win._advisor_panel.findings())
    assert layer["elements"] and action.isEnabled()
    win._overlay_combo.setCurrentText("Advisor findings")
    QApplication.processEvents()
    assert action.isChecked() and not win._downtime_overlay_action.isChecked()
    view = win._diagram_dock.view
    assert view._last_result_visuals["kind"] == "advisor"
    items = {"block": view._block_items, "node": view._node_items,
             "event": view._event_items, "hier": view._hier_items}
    shown = [(items[r["kind"]][r["id"]], r) for r in layer["elements"]
             if r["id"] in items[r["kind"]]]
    assert shown
    for item, row in shown:
        assert item._anal_label.toPlainText().endswith(str(row["count"]))
        assert item._live_colour is not None
    assert "Advisor findings on" in win._statusbar.currentMessage()
    win._overlay_combo.setCurrentText("No canvas overlay")
    QApplication.processEvents()
    assert view._last_result_visuals is None and not action.isChecked()


def test_a_rolled_up_hierarchy_shows_its_top_contributors(win):
    view = win._diagram_dock.view
    win._downtime_overlay_action.setChecked(True)
    QApplication.processEvents()
    scented = [h for h in view._hier_items.values() if getattr(h, "_scent", None)]
    assert scented
    bar = scented[0]._scent
    segments = bar.childItems()
    assert 1 <= len(segments) <= 3
    assert sum(s.rect().width() for s in segments) <= bar.rect().width() + 1e-6
    assert "of the hierarchy's total" in bar.toolTip()
    win._downtime_overlay_action.setChecked(False)
    QApplication.processEvents()
    assert not any(getattr(h, "_scent", None) for h in view._hier_items.values())
