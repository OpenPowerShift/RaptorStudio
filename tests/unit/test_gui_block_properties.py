"""Block Properties fields that were displayed but not editable: PM
enable and its five option checkboxes, the block-level Dependency tab's
master-element selector and default action, Resources/Custom-sparing
new-spares assignment, and the Advanced tab's standby stress and
growth/decay. All round-trip through `typed.py` as plain, non-lossy
bool/int/float/str columns, checked with a real save/reload round trip.
"""
from __future__ import annotations

import os
import tempfile

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QRadioButton

from raptor.core.fileio.rbd import load_rbd, save_rbd
from raptor.core.fileio.typed import block_from_row
from raptor.core.model import SparingType
from raptor.gui.ui.diagram import DiagramView
from raptor.gui.ui.inspector import InspectorDock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOLD = os.path.join(REPO, "tests", "golden")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def v(qapp):
    view = DiagramView()
    view.load_rbd(load_rbd(os.path.join(GOLD, "HierarchyExample", "input.rbd")))
    return view


def _roundtrip(v, rbd, bid):
    """Save and reload, returning the freshly re-parsed RBDBlock rather
    than the live typed object."""
    v.write_back_to_rbd(rbd)
    path = os.path.join(tempfile.mkdtemp(), "roundtrip.rbd")
    save_rbd(rbd, path)
    reloaded = load_rbd(path)
    row = next(r for r in reloaded.sections["BLOCK_DATA"].rows
              if int(float(r["ID"])) == bid)
    return block_from_row(row)


# ---------------------------------------------------------------------
# PM enable + option checkboxes
# ---------------------------------------------------------------------

def test_pm_enable_and_options_editable_with_undo(v):
    bid = sorted(v.blocks)[0]
    assert not v.blocks[bid].uses_pm

    v.set_block_pm_enabled(bid, True)
    assert v.blocks[bid].uses_pm
    before = v.blocks[bid].pm_refresh
    v.set_block_pm_option(bid, "refresh", not before)
    assert v.blocks[bid].pm_refresh == (not before)

    v._stack.undo()
    assert v.blocks[bid].pm_refresh == before
    v._stack.undo()
    assert not v.blocks[bid].uses_pm


def test_pm_fields_persist_through_save_reload(v):
    bid = sorted(v.blocks)[0]
    v.set_block_pm_enabled(bid, True)
    v.set_block_pm_option(bid, "spare_needed", True)
    v.set_block_pm_option(bid, "mis_defer", True)
    b2 = _roundtrip(v, v._rbd_ref, bid)
    assert b2.uses_pm
    assert b2.pm_spare_needed
    assert b2.pm_mis_defer


def test_pm_option_noop_when_pm_not_enabled_touches_nothing_else(v):
    """A no-op (value already matches) must not push an undo step."""
    bid = sorted(v.blocks)[0]
    same = v.blocks[bid].pm_refresh
    n = v._stack.count()
    v.set_block_pm_option(bid, "refresh", same)
    assert v._stack.count() == n


def test_inspector_pm_tab_has_enable_and_option_checkboxes(v):
    """UI-level: the tab must actually contain a QCheckBox for the
    enable flag AND toggling it must emit the right signal (not just
    the model-level setter, which the tests above already cover)."""
    dock = InspectorDock()
    dock.set_rbd(v._rbd_ref)
    bid = sorted(v.blocks)[0]
    dock._populate_block(v.blocks[bid])
    QApplication.processEvents()

    page = dock._scroll_pm.widget()
    boxes = page.findChildren(QCheckBox)
    assert len(boxes) == 1, "PM disabled: only the enable checkbox shows"
    captured = []
    dock.block_pm_enabled_changed.connect(
        lambda bid_, on: captured.append((bid_, on)))
    boxes[0].setChecked(True)
    assert captured == [(bid, True)]


# ---------------------------------------------------------------------
# Block Dependency tab master-element selector + default action
# ---------------------------------------------------------------------

def test_block_dep_master_and_default_idle_editable_with_undo(v):
    bid = sorted(v.blocks)[0]
    assert v.blocks[bid].dependency_num == 0
    before_idle = v.blocks[bid].def_dep_state_idle

    v.set_block_dep_master(bid, -2, "")
    assert v.blocks[bid].dependency_num == -2
    v.set_block_dep_default_idle(bid, not before_idle)
    assert v.blocks[bid].def_dep_state_idle == (not before_idle)

    v._stack.undo()
    assert v.blocks[bid].def_dep_state_idle == before_idle
    v._stack.undo()
    assert v.blocks[bid].dependency_num == 0


def test_block_dep_fields_persist_and_dep_type_survives_empty(v):
    """dep_type is "" for independent/local/system (0/-1/-2) -- writing a
    bare empty string into the whitespace-delimited row would vanish on
    save and shift every later column left by one. Confirms the
    write-back placeholder ("None") round-trips correctly and that an
    untouched sibling block's own row stays correct (it would fail if
    columns had shifted)."""
    bid = sorted(v.blocks)[0]
    other_bid = next(b for b in v.blocks if b != bid)
    orig_other = block_from_row(next(
        r for r in load_rbd(os.path.join(
            GOLD, "HierarchyExample", "input.rbd")).sections["BLOCK_DATA"].rows
        if int(float(r["ID"])) == other_bid))

    v.set_block_dep_master(bid, -2, "")
    v.write_back_to_rbd(v._rbd_ref)
    path = os.path.join(tempfile.mkdtemp(), "roundtrip.rbd")
    save_rbd(v._rbd_ref, path)
    reloaded = load_rbd(path)

    row = next(r for r in reloaded.sections["BLOCK_DATA"].rows
              if int(float(r["ID"])) == bid)
    b2 = block_from_row(row)
    assert b2.dependency_num == -2
    assert b2.dep_type in ("", "None")   # never the raw "" that corrupts

    sibling_row = next(r for r in reloaded.sections["BLOCK_DATA"].rows
                       if int(float(r["ID"])) == other_bid)
    sibling = block_from_row(sibling_row)
    assert sibling.name == orig_other.name
    assert sibling.fail_values == orig_other.fail_values
    assert sibling.pm_freq == orig_other.pm_freq


def test_block_dep_master_dependent_on_block(v):
    bid = sorted(v.blocks)[0]
    target_bid = next(b for b in v.blocks if b != bid)
    v.set_block_dep_master(bid, target_bid, "RBDBlock")
    b2 = _roundtrip(v, v._rbd_ref, bid)
    assert b2.dependency_num == target_bid
    assert b2.dep_type == "RBDBlock"


def test_inspector_block_deps_tab_reuses_shared_widget_not_node_signal(v):
    """The block Dependency tab reuses `_dependency_group`, which was
    written for Nodes -- confirms it emits the block signal, not
    `node_dep_changed`, when driven from a block's own tab."""
    dock = InspectorDock()
    dock.set_rbd(v._rbd_ref)
    bid = sorted(v.blocks)[0]
    dock._populate_block(v.blocks[bid])
    QApplication.processEvents()

    page = dock._scroll_deps.widget()
    radios = {r.text(): r for r in page.findChildren(QRadioButton)
             if r.text() in ("Independent", "Locally dependent",
                             "System dependent", "Operating state",
                             "Idle state")}
    assert {"Independent", "Locally dependent", "System dependent",
           "Operating state", "Idle state"} <= set(radios)

    block_captured = []
    node_captured = []
    dock.block_dep_master_changed.connect(
        lambda bid_, dn, dt: block_captured.append((bid_, dn, dt)))
    dock.node_dep_changed.connect(
        lambda *a: node_captured.append(a))
    radios["System dependent"].setChecked(True)
    assert block_captured == [(bid, -2, "")]
    assert node_captured == []


# ---------------------------------------------------------------------
# Resource pool assignment + Custom-sparing new-spares
# ---------------------------------------------------------------------

def test_block_resource_pool_couples_num_diff_res(v):
    bid = sorted(v.blocks)[0]
    # Start from a known state -- the fixture may already have a
    # resource assigned; clear it first so both directions of the
    # coupling get exercised regardless of the block's starting point.
    v.set_block_resource_pool(bid, "")
    assert v.blocks[bid].resource_type_count == 0

    v.set_block_resource_pool(bid, "Astronauts")
    assert v.blocks[bid].resource_pool_name == "Astronauts"
    assert v.blocks[bid].resource_type_count == 1, \
        "assigning a pool must flip resource_type_count too -- resource_pool_name alone is inert"

    v.set_block_resource_counts(bid, 3, 2)
    assert (v.blocks[bid].repair_resources_needed, v.blocks[bid].pm_resources_needed) == (3, 2)

    v.set_block_resource_pool(bid, "")
    assert v.blocks[bid].resource_type_count == 0


def test_block_resource_fields_persist_through_save_reload(v):
    bid = sorted(v.blocks)[0]
    v.set_block_resource_pool(bid, "Astronauts")
    v.set_block_resource_counts(bid, 4, 1)
    b2 = _roundtrip(v, v._rbd_ref, bid)
    assert b2.resource_pool_name == "Astronauts"
    assert b2.resource_type_count == 1
    assert (b2.repair_resources_needed, b2.pm_resources_needed) == (4, 1)


def test_block_resource_cleared_writes_safe_placeholder_not_empty_token(v):
    """Clearing back to no-resource must not write a bare empty string
    (which corrupts the whitespace-delimited row)."""
    bid = sorted(v.blocks)[0]
    v.set_block_resource_pool(bid, "Astronauts")
    v.set_block_resource_pool(bid, "")
    b2 = _roundtrip(v, v._rbd_ref, bid)
    assert b2.resource_type_count == 0
    assert b2.resource_pool_name != ""


def test_custom_sparing_new_spares_editable_only_meaningful_for_custom(v):
    bid = sorted(v.blocks)[0]
    v.blocks[bid].sparing_type = SparingType.CUSTOM
    v.set_block_new_spares(bid, 5, 12.0)
    assert (v.blocks[bid].new_spares,
           v.blocks[bid].new_spares_arrival) == (5, 12.0)
    b2 = _roundtrip(v, v._rbd_ref, bid)
    assert b2.new_spares == 5
    assert b2.new_spares_arrival == pytest.approx(12.0)


def test_inspector_resources_group_has_pool_combo_and_count_spinners(v):
    dock = InspectorDock()
    dock.set_rbd(v._rbd_ref)
    bid = sorted(v.blocks)[0]
    dock._populate_block(v.blocks[bid])
    QApplication.processEvents()

    page = dock._scroll_spares.widget()
    combos = page.findChildren(QComboBox)
    # Sparing-type combo + spare-pool combo + resource-pool combo.
    assert len(combos) >= 3
    captured = []
    dock.block_resource_pool_changed.connect(
        lambda bid_, name: captured.append((bid_, name)))
    res_combo = combos[-1]
    idx = res_combo.findText("Astronauts")
    if idx < 0:
        pytest.skip("fixture has no Astronauts resource pool defined")
    res_combo.setCurrentIndex(idx)
    res_combo.activated.emit(idx)
    assert captured == [(bid, "Astronauts")]


# ---------------------------------------------------------------------
# Advanced tab: standby stress + growth/decay
# ---------------------------------------------------------------------

def test_standby_stress_editable(v):
    bid = sorted(v.blocks)[0]
    v.set_block_standby_stress(bid, 0.6)
    assert v.blocks[bid].sb_stress == pytest.approx(0.6)
    b2 = _roundtrip(v, v._rbd_ref, bid)
    assert b2.sb_stress == pytest.approx(0.6)


def test_growth_decay_enable_type_rate_limit_with_undo(v):
    bid = sorted(v.blocks)[0]
    assert v.blocks[bid].gd_type == 0

    v.set_block_gd_enabled(bid, True)
    assert v.blocks[bid].gd_type == 1, "enabling defaults to Linear"
    v.set_block_gd_type(bid, 2)
    assert v.blocks[bid].gd_type == 2
    # A base below 1 decays, so the limit sits below the failure mean
    # (Raptor 7 refuses one above it).
    v.set_block_gd_rate_limit(bid, 0.9, 1.0)
    assert (v.blocks[bid].gd_rate, v.blocks[bid].gd_limit) == (0.9, 1.0)

    v.set_block_gd_enabled(bid, False)
    assert v.blocks[bid].gd_type == 0

    v._stack.undo()  # re-enable
    assert v.blocks[bid].gd_type == 2, "re-enabling restores the last type"


def test_growth_decay_persists_through_save_reload(v):
    bid = sorted(v.blocks)[0]
    v.set_block_gd_enabled(bid, True)
    v.set_block_gd_type(bid, 3)
    v.set_block_gd_rate_limit(bid, 1.2, 200.0)
    b2 = _roundtrip(v, v._rbd_ref, bid)
    assert b2.gd_type == 3
    assert b2.gd_rate == pytest.approx(1.2)
    assert b2.gd_limit == pytest.approx(200.0)


def test_growth_decay_gdtype_gt0_actually_simulates(v):
    """The engine's `combined_stress` GDType>0 path runs end-to-end: it does
    not crash the engine when driven from the GUI control."""
    from raptor.core.engine.runner import run_simulation_with_reports
    bid = sorted(v.blocks)[0]
    v.set_block_gd_enabled(bid, True)
    v.set_block_gd_type(bid, 2)
    v.set_block_gd_rate_limit(bid, 0.9, 1.0)
    v.write_back_to_rbd(v._rbd_ref)
    logs, _eor, fin = run_simulation_with_reports(
        v._rbd_ref, num_runs=2, date="fixed")
    assert logs and fin


def test_inspector_advanced_group_has_stress_and_gd_controls(v):
    dock = InspectorDock()
    dock.set_rbd(v._rbd_ref)
    bid = sorted(v.blocks)[0]
    dock._populate_block(v.blocks[bid])
    QApplication.processEvents()

    page = dock._scroll_failure.widget()
    boxes = [c for c in page.findChildren(QCheckBox)
            if "mean life" in c.text()]
    assert len(boxes) == 1
    gd_cb = boxes[0]
    assert not gd_cb.isChecked()

    captured = []
    dock.block_gd_enabled_changed.connect(
        lambda bid_, on: captured.append((bid_, on)))
    gd_cb.setChecked(True)
    assert captured == [(bid, True)]


# ---------------------------------------------------------------------
# Costs tab: pool-sparing blocks' spare/emergency-ship fields
# ---------------------------------------------------------------------

def test_costs_tab_disables_spare_fields_for_pool_sparing_blocks(v):
    """The engine reads spare_cost/emergency_shipping_cost only for
    SparingType.INFINITE, so editing them on a SPARE_POOL block is a
    silent no-op (the pool's own fields are authoritative). Real Raptor
    greys these out for that reason (Ch.12)."""
    from PySide6.QtWidgets import QDoubleSpinBox

    bid = sorted(v.blocks)[0]
    v.blocks[bid].sparing_type = SparingType.SPARE_POOL
    dock = InspectorDock()
    dock.set_rbd(v._rbd_ref)
    dock._populate_block(v.blocks[bid])
    QApplication.processEvents()
    spins = dock._scroll_costs.widget().findChildren(QDoubleSpinBox)
    assert len(spins) == 14
    assert not spins[-2].isEnabled() and not spins[-1].isEnabled(), \
        "Recurring spare / Emergency spare additional cost must be disabled"
    assert all(sp.isEnabled() for sp in spins[:-2])

    v.blocks[bid].sparing_type = SparingType.INFINITE
    dock._populate_block(v.blocks[bid])
    QApplication.processEvents()
    spins = dock._scroll_costs.widget().findChildren(QDoubleSpinBox)
    assert all(sp.isEnabled() for sp in spins)


# ---------------------------------------------------------------------
# Sparing-type row write-back on-disk string
# ---------------------------------------------------------------------

@pytest.mark.parametrize("target", [SparingType.CUSTOM, SparingType.SPARE_POOL])
def test_sparing_type_survives_save_reload(v, target):
    """`set_block_sparing_type`'s row write-back must use the title-cased
    on-disk SpType string from `typed._SPARING_TYPE_BY_NAME` (e.g.
    "Custom"), not `SparingType.name` ("CUSTOM"); otherwise the value
    reverts to SparingType.NONE on reload."""
    bid = sorted(v.blocks)[0]
    v.set_block_sparing_type(bid, target.name)
    assert v.blocks[bid].sparing_type == target
    b2 = _roundtrip(v, v._rbd_ref, bid)
    assert b2.sparing_type == target


@pytest.mark.parametrize("target", [SparingType.COLD_POOL, SparingType.RESOURCE])
def test_pool_object_sparing_types_reload_as_none(v, target):
    """Real Raptor's loader turns any block SpType other than Infinite,
    Pooled and Custom into None: ColdPool and Resource
    are pool-object types a block never carries, so they do not survive."""
    bid = sorted(v.blocks)[0]
    v.set_block_sparing_type(bid, target.name)
    b2 = _roundtrip(v, v._rbd_ref, bid)
    assert b2.sparing_type == SparingType.NONE


# ---------------------------------------------------------------------
# Block Defaults widened from failure-dist-only
# ---------------------------------------------------------------------

def test_block_defaults_widened_fields_seed_new_blocks(qapp):
    """Manual 20-18: Block Defaults is "a modified Block Properties dialog
    box". It covers repair distribution/param and sparing as well as
    failure distribution, since those are the fields `add_block` seeds
    independently of the cloned template row."""
    from raptor.gui.ui.main_window import MainWindow
    win = MainWindow()
    win._load_into_app(load_rbd(os.path.join(GOLD, "HierarchyExample",
                                              "input.rbd")), None)
    win._block_defaults()  # sanity: no crash with nothing saved yet
    from PySide6.QtCore import QSettings
    QSettings().setValue(win._BLOCK_DEF_KEY, {
        "fdist": 15, "fparams": [50.0, 7.0, 0.0],
        "rdist": 8, "rparams": [5.0, 1.0, 0.0],
        "sparing": "Custom",
    })
    v = win._diagram_dock.view
    d = win._block_defaults()
    v.default_block_fdist = d["fdist"]
    v.default_block_fparams = d["fparams"]
    v.default_block_rdist = d["rdist"]
    v.default_block_rparams = d["rparams"]
    v.default_block_sparing = d["sparing"]

    bid = v.add_block("Fresh", 0, 0)
    b = v.blocks[bid]
    assert b.fail_distro == 15 and list(b.fail_values[:2]) == [50.0, 7.0]
    assert b.repair_distro == 8 and list(b.repair_values[:2]) == [5.0, 1.0]
    assert b.sparing_type == SparingType.CUSTOM
    b2 = _roundtrip(v, v._rbd_ref, bid)
    assert b2.fail_distro == 15
    assert b2.repair_distro == 8
    assert b2.sparing_type == SparingType.CUSTOM


def test_block_defaults_reset_reverts_to_factory_settings(qapp):
    from raptor.gui.ui.main_window import MainWindow
    from PySide6.QtCore import QSettings
    win = MainWindow()
    win._load_into_app(load_rbd(os.path.join(GOLD, "HierarchyExample",
                                              "input.rbd")), None)
    QSettings().setValue(win._BLOCK_DEF_KEY, {
        "fdist": 15, "fparams": [50.0, 7.0, 0.0], "rdist": 8, "rparams": [5.0, 1.0, 0.0],
        "sparing": "Custom",
    })
    assert win._block_defaults() != win._BLOCK_FACTORY_DEFAULTS
    QSettings().setValue(win._BLOCK_DEF_KEY, win._BLOCK_FACTORY_DEFAULTS)
    assert win._block_defaults() == win._BLOCK_FACTORY_DEFAULTS


def test_edit_block_defaults_dialog_reset_button_restores_factory_values(
        qapp, monkeypatch):
    from PySide6.QtWidgets import QDialog, QPushButton, QComboBox
    from PySide6.QtCore import QSettings
    from raptor.gui.ui.main_window import MainWindow
    from raptor.gui.ui import dialogs
    win = MainWindow()
    win._load_into_app(load_rbd(os.path.join(GOLD, "HierarchyExample",
                                              "input.rbd")), None)
    QSettings().setValue(win._BLOCK_DEF_KEY, {
        "fdist": 15, "fparams": [50.0, 7.0, 0.0], "rdist": 8, "rparams": [5.0, 1.0, 0.0],
        "sparing": "Custom",
    })

    def fill(self):
        for btn in self.findChildren(QPushButton):
            if btn.text() == "Reset Defaults":
                btn.click()
        combos = self.findChildren(QComboBox)
        assert combos[1].currentData() == \
            win._BLOCK_FACTORY_DEFAULTS["fdist"]
        assert combos[-1].currentData() == \
            win._BLOCK_FACTORY_DEFAULTS["sparing"]
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fill)
    dialogs.edit_block_defaults(win)
    assert win._block_defaults() == win._BLOCK_FACTORY_DEFAULTS


def _defaults_dialog_probe(win, monkeypatch, probe):
    from PySide6.QtWidgets import QDialog
    from raptor.gui.ui import dialogs
    monkeypatch.setattr(QDialog, "exec", lambda self: probe(self) or QDialog.Rejected)
    dialogs.edit_block_defaults(win)


def test_block_template_combo_is_greyed_with_a_reason_until_a_library_is_imported(qapp, monkeypatch):
    from PySide6.QtWidgets import QComboBox
    from raptor.gui.ui.main_window import MainWindow
    win = MainWindow()
    win._load_into_app(load_rbd(os.path.join(GOLD, "HierarchyExample", "input.rbd")), None)
    seen = {}

    def probe(dlg):
        c = dlg.findChild(QComboBox)
        seen.update(name=c.accessibleName(), enabled=c.isEnabled(), tip=c.toolTip(), items=c.count())
    _defaults_dialog_probe(win, monkeypatch, probe)
    assert seen["name"] == "Block template" and not seen["enabled"] and seen["items"] == 1
    assert "Import Library" in seen["tip"]


def test_block_template_combo_fills_the_distributions_from_the_library(qapp, monkeypatch):
    from PySide6.QtWidgets import QComboBox, QDoubleSpinBox
    from raptor.gui.ui.block_library import load_library
    from raptor.gui.ui.main_window import MainWindow
    win = MainWindow()
    win._load_into_app(load_rbd(os.path.join(GOLD, "HierarchyExample", "input.rbd")), None)
    win._block_library = load_library(os.path.join(GOLD, "Phasing3", "legacy", "lib_ch20_shipped.rbl"))
    assert win._block_library
    seen = {}

    def probe(dlg):
        tcombo, fcombo, *_rest = dlg.findChildren(QComboBox)
        assert tcombo.isEnabled() and tcombo.itemText(0) == "Default" and tcombo.count() == 1 + len(win._block_library)
        row = win._block_library[0]["row"]
        tcombo.setCurrentIndex(1)
        seen["fdist_ok"] = fcombo.currentData() == int(float(row["FDist"]))
        seen["fparam_ok"] = abs(dlg.findChildren(QDoubleSpinBox)[0].value() - float(row["FParam1"])) < 1e-9
        tcombo.setCurrentIndex(0)
        seen["back"] = fcombo.currentData() == win._BLOCK_FACTORY_DEFAULTS["fdist"]
    _defaults_dialog_probe(win, monkeypatch, probe)
    assert seen == {"fdist_ok": True, "fparam_ok": True, "back": True}


def test_block_defaults_saved_before_all_three_parameters_were_kept_still_load(qapp):
    from PySide6.QtCore import QSettings
    from raptor.gui.ui.main_window import MainWindow
    win = MainWindow()
    QSettings().setValue(win._BLOCK_DEF_KEY, {"fdist": 6, "fparam": 3.0, "rdist": 7, "rparam": 12.0, "sparing": "Infinite"})
    d = win._block_defaults()
    assert d["fparams"][0] == 3.0 and d["rparams"][0] == 12.0
    assert d["rparams"][1:] == win._BLOCK_FACTORY_DEFAULTS["rparams"][1:]
    QSettings().setValue(win._BLOCK_DEF_KEY, win._BLOCK_FACTORY_DEFAULTS)


def test_a_template_with_three_parameters_gives_new_blocks_all_three(qapp, monkeypatch):
    from PySide6.QtWidgets import QComboBox
    from raptor.gui.ui.main_window import MainWindow
    win = MainWindow()
    win._load_into_app(load_rbd(os.path.join(GOLD, "HierarchyExample", "input.rbd")), None)
    win._block_library = [{"name": "Pump", "row": {"FDist": "11", "FParam1": "1.7", "FParam2": "400", "FParam3": "0",
                                                   "RDist": "7", "RParam1": "8", "RParam2": "2", "RParam3": "0"}}]

    def probe(dlg):
        dlg.findChildren(QComboBox)[0].setCurrentIndex(1)
        return QDialog.Accepted
    from PySide6.QtWidgets import QDialog
    from raptor.gui.ui import dialogs
    monkeypatch.setattr(QDialog, "exec", probe)
    dialogs.edit_block_defaults(win)
    v = win._diagram_dock.view
    b = v.blocks[v.add_block("Fresh", 0, 0)]
    assert b.fail_distro == 11 and list(b.fail_values[:2]) == [1.7, 400.0]
    from PySide6.QtCore import QSettings
    QSettings().setValue(win._BLOCK_DEF_KEY, win._BLOCK_FACTORY_DEFAULTS)


def test_choosing_geometric_growth_brings_raptor7s_own_corrections(v):
    """Raptor 7 checks the dialog at OK and puts back base 1 when it refuses a
    base of 0, keeping a limit it accepts (the
    stored limit is the failure mean while growth is off, and a rising base
    accepts a limit at or above the mean); the Inspector commits the type on
    its own, so the type change carries the correction in the same undo
    step instead of being refused."""
    from raptor.core.input_rules import dialog_fail_mean
    bid = sorted(v.blocks)[0]
    blk = v.blocks[bid]
    mean = dialog_fail_mean(blk.fail_distro, list(blk.fail_values))
    v.set_block_gd_enabled(bid, True)
    v.set_block_gd_type(bid, 2)
    assert (blk.gd_type, blk.gd_rate) == (2, 1.0)
    assert blk.gd_limit >= mean
    v._stack.undo()
    assert v.blocks[bid].gd_type == 1


def test_a_typed_value_raptor7_refuses_is_refused(v, monkeypatch):
    """The stagger of a frequency PM at or above its frequency
   : Raptor 7's message, and the edit is undone."""
    import raptor.gui.ui.msgbox as mb
    shown = []
    monkeypatch.setattr(mb, "warning", lambda *a, **k: shown.append(a[2]))
    bid = sorted(v.blocks)[0]
    before = v.blocks[bid].pm_freq
    v.set_block_pm_freq(bid, 10.0)
    v.set_block_pm_stagger(bid, 50.0)
    assert shown and "PM stagger value cannot be greater than or equal to PM frequency!" in shown[-1]
    assert v.blocks[bid].pm_stagger < 10.0 and v.blocks[bid].pm_freq == 10.0 != before
    assert not v._stack.canRedo(), "the refused edit must not come back through Redo"
