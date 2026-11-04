"""Block labels on the canvas: the formatter is wired, not copied.

The old line under a block name printed the distribution's FIRST PARAMETER
and called it F/R. For an exponential law that happened to be the mean; for
a Weibull it was the shape. These tests keep that bug gone, and keep the
View ribbon's mode and phase controls connected to the canvas.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from raptor.core.fileio.rbd import load_rbd  # noqa: E402
from raptor.gui.ui import block_labels, settings  # noqa: E402
from raptor.gui.ui.main_window import MainWindow  # noqa: E402

pytestmark = pytest.mark.gui

GOLD = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "golden")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_label_mode():
    yield
    settings.remove("appearance/block_labels")


def _block(fail=4, fail_vals=(100.0, 0.0, 0.0), *, phasing=False,
           kinds=(), values=()) -> SimpleNamespace:
    """A block-shaped object with only the fields `block_labels` reads."""
    return SimpleNamespace(
        fail_distro=fail, fail_values=list(fail_vals),
        repair_distro=4, repair_values=[5.0, 0.0, 0.0],
        uses_phasing=phasing, phase_type=list(kinds),
        phase_value=list(values))


# ---------------------------------------------------------------------------
# The formatter itself
# ---------------------------------------------------------------------------

def test_mean_mode_prints_the_mean_not_the_first_parameter():
    # Weibull (shape=2, scale=100): mean = 100 * Gamma(1.5) = 88.6, not 2.
    lines = block_labels.sublabel(_block(15, (2.0, 100.0, 0.0)),
                                  mode="mean", units="h")
    assert lines[0].startswith("MTTF 8")
    assert not lines[0].startswith("MTTF 2 ")
    assert lines[1] == "MTTR 5 h"


def test_spread_rate_and_off_modes():
    b = _block()
    spread = block_labels.sublabel(b, mode="spread", units="h")
    assert "±" in spread[0]
    rate = block_labels.sublabel(b, mode="rate")
    assert rate[0].startswith("λ ") and rate[0].endswith("/h")
    assert block_labels.sublabel(b, mode="off") == []


def test_a_cut_phase_says_so_and_stress_shortens_the_mean():
    b = _block(phasing=True, kinds=("C", "A"), values=(1.0, 2.0))
    assert block_labels.sublabel(b, mode="mean", phase=0) == \
        ["cut in this phase"]
    # Mean 100 h at 2x stress -> 50 h.
    assert block_labels.sublabel(b, mode="mean", phase=1)[0] == "MTTF 50"


def test_tooltip_keeps_the_numbers_when_the_label_is_off():
    b = _block()
    tip = block_labels.tooltip(b, mode="off", units="h")
    assert "Failure mean 100 h" in tip
    assert "Failure rate 0.01/h" in tip


def test_describe_matches_sublabel_and_tooltip_in_every_mode():
    """`describe` is the one-pass replacement for calling `sublabel` and
    `tooltip` separately; the canvas must show exactly what those two
    would have. The cut-phase check used to run BEFORE the mode check, so
    "Off" still drew "cut in this phase"."""
    blocks = [
        _block(),                                     # exponential
        _block(15, (2.0, 100.0, 0.0)),                # Weibull
        _block(17, (0.5,)),                           # Bernoulli: no mean
        _block(4, (100.0, 0.0, 0.0), phasing=True,    # cut / stress / link
               kinds=("C", "A", "L"), values=(1.0, 2.0, 3.0)),
    ]
    for block in blocks:
        for mode in ("mean", "spread", "rate", "off", "not-a-mode"):
            for phase in (-1, 0, 1, 2, 5):
                for units in ("", "h"):
                    lines, tip = block_labels.describe(
                        block, mode=mode, phase=phase, units=units)
                    assert lines == block_labels.sublabel(
                        block, mode=mode, phase=phase, units=units), \
                        f"{mode}/{phase}/{units!r}"
                    assert tip == block_labels.tooltip(
                        block, mode=mode, phase=phase, units=units), \
                        f"{mode}/{phase}/{units!r}"


# ---------------------------------------------------------------------------
# Wired to the canvas
# ---------------------------------------------------------------------------

def _open(name: str) -> tuple[MainWindow, object]:
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(GOLD, name, "input.rbd")), None)
    return w, w._diagram_dock.view


def test_canvas_starts_with_means_and_a_tooltip(qapp):
    w, v = _open("TwoBlockRedundancy")
    bid = next(b for b in v._block_items if b != 0)
    item = v._block_items[bid]
    assert "MTTF" in item._sub.toPlainText()
    assert "MTTR" in item._sub.toPlainText()
    assert "Failure mean" in item._sub.toolTip()
    w._dirty = False
    w.close()


def test_the_setting_repaints_every_visible_block(qapp):
    w, v = _open("TwoBlockRedundancy")
    bid = next(b for b in v._block_items if b != 0)
    item = v._block_items[bid]

    settings.set_value("appearance/block_labels", "off")
    v.refresh_block_labels()
    assert item._sub.toPlainText() == ""
    # Off hides the lines, it does not lose the story.
    assert "Failure mean" in item._sub.toolTip()

    settings.set_value("appearance/block_labels", "rate")
    v.refresh_block_labels()
    assert "λ" in item._sub.toPlainText()

    w._dirty = False
    w.close()


def test_show_phase_changes_the_law_the_label_describes(qapp):
    w, v = _open("PhasingDemo")
    # Phase 1 doubles the stress on Thermal (2435 h -> 1217.5 h).
    v.set_label_phase(0)
    assert "1,218" in v._block_items[4]._sub.toPlainText()
    # Back to the model's own data.
    v.set_label_phase(-1)
    assert "MTTF 2,435" in v._block_items[4]._sub.toPlainText()
    w._dirty = False
    w.close()


def test_view_ribbon_has_mode_and_phase_combos(qapp):
    w, v = _open("PhasingDemo")
    combo = w._block_label_combo
    assert [combo.itemData(i) for i in range(combo.count())] == \
        [key for key, _label in block_labels.MODES]
    assert combo.currentData() == "mean"
    phase = w._block_phase_combo
    assert phase.itemData(0) == -1
    assert phase.count() == 7              # Model + the six PHASES rows

    combo.setCurrentIndex(combo.findData("spread"))
    assert settings.get("appearance/block_labels") == "spread"
    assert "±" in v._block_items[4]._sub.toPlainText()

    phase.setCurrentIndex(phase.findData(0))
    assert v._label_phase == 0
    assert "1,218" in v._block_items[4]._sub.toPlainText()

    w._dirty = False
    w.close()


@pytest.mark.parametrize("which,code,gone", [("R", 18, "MTTR"),
                                             ("F", 18, "MTTF")])
def test_a_distribution_change_repaints_the_label(qapp, which, code, gone):
    """Changing a law's TYPE (repair to None, say) left the old MTTR under
    the block: only a parameter edit repainted the label. Undo puts it
    back."""
    w, v = _open("TwoBlockRedundancy")
    bid = next(b for b in v._block_items
               if b != 0 and gone in v._block_items[b]._sub.toPlainText())
    item = v._block_items[bid]
    w._on_block_dist_type_changed(bid, which, code)
    assert gone not in item._sub.toPlainText()
    v._stack.undo()
    assert gone in item._sub.toPlainText()
    w._dirty = False
    w.close()


def test_a_phase_edit_repaints_the_label_of_the_phase_shown(qapp):
    w, v = _open("PhasingDemo")
    v.set_label_phase(0)
    n = len(v.blocks[4].phase_type)
    # Stress 4 on every phase: Thermal's 2435 h becomes 608.75 h.
    v.set_element_phasing("block", 4, True, [("A", 4.0)] * n)
    assert "MTTF 608.8" in v._block_items[4]._sub.toPlainText()
    v._stack.undo()
    assert "1,218" in v._block_items[4]._sub.toPlainText()
    w._dirty = False
    w.close()
