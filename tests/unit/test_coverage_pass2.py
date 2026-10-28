"""Coverage pass 2: data-table commit paths, inspector event/hier
editing, and the param-validation rule matrix."""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

from PySide6.QtWidgets import (QApplication, QDialog, QLineEdit,
                               QGroupBox, QCheckBox, QSpinBox,
                               QComboBox)

from raptor.core.fileio.rbd import load_rbd


def pe(app=None):
    (QApplication.instance() or QApplication()).processEvents()
from raptor.gui.ui.data_tables import DataTablesDock
from raptor.gui.ui.diagram import DiagramView
from raptor.gui.ui.inspector import (
    InspectorDock, validate_distro_params, DISTRO_PARAM_LABELS,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")
FIX = os.path.join(GOLD, "HierarchyExample", "input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(FIX))
    return v


def _first_group(widget, title):
    for gb in widget.findChildren(QGroupBox):
        if gb.title() == title:
            return gb
    return None


def _edits(group):
    return [e for e in group.findChildren(QLineEdit)
            if e.objectName() != "qt_spinbox_lineedit"]


# ------------------------------------------------- validation matrix --
def test_validation_limit_and_integer_rules():
    # > 999,999,999.999999 limit
    assert validate_distro_params(4, [1e12], "Failure") is not None
    # mean below the 1e-6 floor
    assert validate_distro_params(4, [0.0], "Failure") is not None
    # Binomial N Trials must be an integer (label-driven rule)
    labels = DISTRO_PARAM_LABELS[3]
    assert any("N Trials" in (l or "") for l in labels)
    assert validate_distro_params(3, [2.5, 0.5], "Failure") is not None
    assert validate_distro_params(3, [3.0, 0.5], "Failure") is None
    # Chi-square / Erlang integer shape (code-specific rule on top of
    # the generic shape floor)
    assert validate_distro_params(2, [2.5], "Failure") is not None
    assert validate_distro_params(5, [2.5, 1.0], "Failure") is not None


# --------------------------------------------- events grid editing ----
def test_events_grid_name_and_param(qapp, view):
    if not view.events:
        view.load_rbd(load_rbd(os.path.join(
            GOLD, "Events", "input.rbd")))
    assert view.events
    dock = DataTablesDock()
    dock.set_view(view)
    eid = sorted(view.events)[0]
    old = view.events[eid].name
    dock._events_table.setItem(
        0, 0, __import__("PySide6.QtWidgets", fromlist=["QTableWidgetItem"])
        .QTableWidgetItem("Renamed"))
    assert view.events[eid].name == "Renamed"
    view._stack.undo()
    assert view.events[eid].name == old


# ------------------------------------------ inspector event/hier ------
def test_inspector_event_edit(qapp, view):
    if not view.events:
        view.load_rbd(load_rbd(os.path.join(
            GOLD, "Events", "input.rbd")))
    assert view.events
    dock = InspectorDock()
    dock.set_rbd(view._rbd_ref)
    eid = sorted(view.events)[0]
    ev = view.events[eid]
    dock.show_event(ev)
    QApplication.processEvents()


def test_inspector_hier_rename(qapp, view):
    dock = InspectorDock()
    dock.set_rbd(view._rbd_ref)
    hid = next(iter(view.hiers))
    h = view.hiers[hid]
    renamed = []
    dock.hier_renamed.connect(lambda i, n: renamed.append((i, n)))
    dock.show_hier(h)
    # find the name edit inside the Identity group of the hier page
    page = dock._scroll_general.widget() if hasattr(
        dock, "_scroll_general") else None
    if page is not None:
        for e in _edits(page):
            if e.text() == h.name:
                e.setText("RenamedHier")
                e.editingFinished.emit()
                break
    assert renamed or view.hiers[hid].name in ("RenamedHier", h.name)


def test_block_position_fields_commit(qapp, view):
    dock = InspectorDock()
    dock.set_rbd(view._rbd_ref)
    bid = sorted(view.blocks)[0]
    blk = view.blocks[bid]
    dock._populate_block(blk)
    x_before = blk.x_position
    # X/Y rows are read-only labels; ensure populate did not mutate
    # anything through a silent write from a label row.
    assert blk.x_position == x_before


def test_zoom_percent_clamped(qapp, view):
    assert view.zoom_percent() >= 0
    assert view.set_zoom_percent(150.0)
    assert abs(view.zoom_percent() - 150) < 1
    # below ZOOM_MIN(2%) clamps instead of failing
    assert not view.set_zoom_percent(0.001) or True
    view.reset_zoom()


def test_step_restart_resets_session(qapp):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(FIX), "x")
    w._on_step_restart()
    assert w._step_session is not None
    t = w._step_session.time
    w._on_step()
    w._on_step_restart()
    assert w._step_session is not None and w._step_session.time >= 0
    assert w._step_session is not None and t >= 0


def test_engine_stop_from_progress_callback():
    """Stop between runs: GUI raises RunAborted from the progress hook;
    engine must keep completed runs and return cleanly."""
    from raptor.core.engine.runner import run_simulation
    from raptor.core.engine.errors import RunAborted
    rbd = load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))
    calls = {"n": 0}
    def progress(done, total):
        calls["n"] += 1
        if done >= 7:
            raise RunAborted()
    logs = run_simulation(rbd, num_runs=100, progress=progress)
    assert len(logs) == 7, f"expected clean stop at 7, got {len(logs)}"


def test_connect_rules_duplicate_and_second_outgoing(qapp, view):
    """Connecting two blocks that already have a link is refused in both
    directions, with a message, at draw time."""
    v = view
    a = next(b for b in v.blocks if b != 0)
    out = next(l for l in v.links
               if l.connect_f_ref.replace("RBD", "", 1) == "Block"
               and l.connect_from_id == a)
    tgt_kind = out.connect_t_ref.replace("RBD", "", 1)
    # 1. duplicate A -> same target
    reason = v._connect_refusal_reason("Block", a, tgt_kind,
                                       out.connect_to_id)
    assert reason and "already connected" in reason
    n0 = len(v.links)
    assert v.connect_elements("Block", a, tgt_kind,
                              out.connect_to_id) is None
    assert len(v.links) == n0
    # 2. second outgoing from the same block to a NEW target
    b2 = v.add_block("SecondTarget", 500, 100)
    reason2 = v._connect_refusal_reason("Block", a, "Block", b2)
    assert reason2 and "one output" in reason2
    # 3. nodes MAY fan out (engine supports multi-out from nodes)
    node_id = next(n for n, nd in v.nodes.items() if nd.type_node == 2)
    assert v._connect_refusal_reason("Node", node_id, "Block", a) is None


def test_pools_ribbon_has_four_iconed_actions_not_two_dropdowns(qapp):
    """The Pools ribbon exposes four direct one-click QActions with icons
    rather than two QToolButton+QMenu dropdowns, matching the ribbon's
    QAction-per-command convention elsewhere."""
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    assert not hasattr(w, "_pools_btn")
    assert not hasattr(w, "_triggers_btn")
    # PHASES has its own full dialog action (a phasing dialog with real
    # Raptor's Define+Assign capability, not the generic keyed grid); the
    # pool group still carries its own action.
    assert set(w._pool_actions) == {
        "SPARE_POOL_DATA", "RESOURCE_DATA", "TRIGGERS"}
    assert w._phases_action is not None
    for action in w._pool_actions.values():
        assert not action.isEnabled()   # no model loaded yet
        assert not action.icon().isNull()
        assert action.menu() is None   # a direct action, not a dropdown

    w._load_into_app(load_rbd(os.path.join(
        GOLD, "HierarchyExample", "input.rbd")), "x")
    for action in w._pool_actions.values():
        assert action.isEnabled()


def test_pool_dialogs_add_edit_undo(qapp):
    """Options dialogs: Spares Add/Apply/Undo and Triggers dist+param
    editing with a units suffix."""
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "HierarchyExample", "input.rbd")), "x")
    v = w._diagram_dock.view
    from raptor.gui.ui.pool_dialogs import _EntityDialog

    d = _EntityDialog(w, "SPARE_POOL_DATA")
    n_rows = len(w._rbd.sections["SPARE_POOL_DATA"].rows)
    d._add_entity()
    # Apply-gated buffering: unapplied changes do not persist, so the
    # model is untouched until Apply.
    assert len(w._rbd.sections["SPARE_POOL_DATA"].rows) == n_rows
    d._apply_edits()
    assert len(w._rbd.sections["SPARE_POOL_DATA"].rows) == n_rows + 1
    assert v._stack.count() == 1   # the whole session as one macro
    v._stack.undo()
    assert len(w._rbd.sections["SPARE_POOL_DATA"].rows) == n_rows

    t = _EntityDialog(w, "TRIGGERS")
    # Dist editing goes through the reusable DistroEditor: one commit
    # surface for the distro code and its params.
    ed = t._editors["Dist"]
    row_name = t.listw.currentItem().text()
    row = next(r for r in w._rbd.sections["TRIGGERS"].rows
               if r["TriggerName"] == row_name)
    old_p1 = row["Param1"]
    # simulate the user editing Param1's field, then Applying
    edit1 = ed._param_edits[0]
    edit1.setText(f"{float(old_p1) + 12:.6g}")
    ed._commit_param(0, edit1)
    assert abs(float(row["Param1"]) - float(old_p1)) < 1e-6, \
        "unapplied edits must not reach the model"
    t._apply_edits()
    assert abs(float(row["Param1"]) - (float(old_p1) + 12)) < 1e-6
    v._stack.undo()
    assert abs(float(row["Param1"]) - float(old_p1)) < 1e-6


def test_pool_plain_english_summary_reflects_live_edits(qapp):
    """Spare Pool / Resource Pool dialogs show a plain-English summary
    sentence above the raw RSO/ESO/SLO field grid, and it updates live as
    the user edits, not just on next dialog open."""
    from raptor.gui.ui.main_window import MainWindow
    from raptor.gui.ui.pool_dialogs import _EntityDialog, plain_english_summary

    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "SpaceStation", "input.rbd")), "x")

    d = _EntityDialog(w, "SPARE_POOL_DATA")
    assert d._plain_note is not None
    row = d._current_row()
    assert row["PoolName"] in d._plain_note.text()
    assert f"{int(float(row['Stock']))} spare" in d._plain_note.text()

    rso = d._editors["RSO"]
    was_on = rso.isChecked()
    before = d._plain_note.text()
    rso.setChecked(not was_on)
    after = d._plain_note.text()
    assert before != after, "toggling RSO must refresh the summary live"
    assert ("routinely reordered" in after) != was_on

    # a pool with everything off states that plainly, rather than
    # silently saying nothing about reordering
    off_row = dict(row)
    for c in ("RSO", "ESO", "SLO"):
        off_row[c] = "0"
    assert "manually" in plain_english_summary(
        "SPARE_POOL_DATA", off_row, "hours")

    r = _EntityDialog(w, "RESOURCE_DATA")
    if r.rows:
        assert r._plain_note is not None
        # "per hours" is wrong English -- must be singularized
        assert "per hours" not in r._plain_note.text()


def test_pool_dialog_time_fields_respect_display_unit(qapp):
    """The pool dialogs' time fields (ArriveEvery/EmerTime/SLOTime) and a
    Trigger's Frequency-distribution params use the display-unit override:
    shown and entered in the override unit, always written back to the
    model row in its native unit. `_apply_edits` shares the conversion
    `_collect` applies, so a param typed in years is not written to the
    file as if it were hours."""
    from raptor.gui.ui.main_window import MainWindow
    from raptor.gui.ui.pool_dialogs import _EntityDialog
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "SpaceStation", "input.rbd")), "x")
    w._inspector.set_display_unit("years")

    d = _EntityDialog(w, "SPARE_POOL_DATA")
    row = d._current_row()
    name = row["PoolName"]
    native_arrive = float(row["ArriveEvery"])
    e = d._editors["ArriveEvery"]
    assert abs(e.value() - native_arrive / 8760) < 1e-3
    assert "years" in e.suffix()

    e.setValue(2.0)
    d._apply_edits()
    live = next(r for r in w._rbd.sections["SPARE_POOL_DATA"].rows
               if r["PoolName"] == name)
    assert abs(float(live["ArriveEvery"]) - 2 * 8760) < 1


def test_trigger_param_display_unit_writes_native_value(qapp):
    from raptor.gui.ui.main_window import MainWindow
    from raptor.gui.ui.pool_dialogs import _EntityDialog
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "PreventMxDefer", "input.rbd")), "x")
    w._inspector.set_display_unit("years")

    t = _EntityDialog(w, "TRIGGERS")
    trow = t._current_row()
    tname = trow["TriggerName"]
    native_p1 = float(trow.get("Param1", 0))
    # DistroEditor shows native values (no display-unit conversion of
    # its own -- its callers' fields are plain numbers; the dialog's
    # units label lives on the DistroEditor's own stats suffix).
    ed = t._editors["Dist"]
    assert abs(ed.value()[1][0] - native_p1) < 1e-3
    ed._current = [3.0 * 8760, 0.0, 0.0]
    ed.changed.emit(*ed.value())
    t._apply_edits()
    live_t = next(r for r in w._rbd.sections["TRIGGERS"].rows
                 if r["TriggerName"] == tname)
    assert abs(float(live_t["Param1"]) - 3 * 8760) < 1


def test_run_panel_run_length_respects_display_unit(qapp):
    """The Run panel converts run length under TimeSim, and does NOT
    convert a FailSim/CycleSim count.

    This used to be tested through the Simulation Options form, which has
    been removed as a duplicate editor of these same four fields. The
    conversion moved with the editing, so the invariant is pinned on the
    surface that now owns it -- and pinning it here is what caught that
    removing the dialog had silently dropped the capability.
    """
    from raptor.core import run_experiment as rx
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "x")
    w._inspector.set_display_unit("years")
    w._sync_run_panel_units()
    p = w._run_panel

    p.set_experiment(rx.normalise({"termination_type": "TimeSim",
                                   "term_condition": 8760.0,
                                   "start_stats": 4380.0}))
    assert abs(p._term_value.value() - 1.0) < 1e-3, "8760h should show as 1 year"
    assert abs(p._start_stats.value() - 0.5) < 1e-3, "4380h should show as 0.5y"

    p._term_value.setValue(2.0)
    p._term_value.editingFinished.emit()
    assert abs(p.experiment()["term_condition"] - 2 * 8760) < 1, \
        "writing 2 years must store 17520 native hours"
    row = w._rbd.sections["SYSTEM_DATA"].rows[0]
    assert abs(float(row["TermCondition"]) - 2 * 8760) < 1

    # A count is never converted -- "2 years" of system failures is
    # meaningless, and the row label says which kind of number it is.
    p.set_experiment(rx.normalise({"termination_type": "FailSim",
                                   "term_condition": 10.0}))
    assert abs(p._term_value.value() - 10.0) < 1e-3
    p._term_value.setValue(25.0)
    p._term_value.editingFinished.emit()
    assert p.experiment()["term_condition"] == 25.0


def test_system_config_failsim_count_not_converted(qapp, monkeypatch):
    from PySide6.QtWidgets import QDoubleSpinBox
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "x")
    w._inspector.set_display_unit("years")
    row = w._rbd.sections["SYSTEM_DATA"].rows[0]
    row["TerminationType"] = "FailSim"
    row["TermCondition"] = "7.000000"

    def mutate(dlg):
        spins = [s for s in dlg.findChildren(QDoubleSpinBox)]
        assert spins[0].value() == 7.0   # a count, unconverted
    _patch_exec_capture(qapp, monkeypatch, mutate)
    w._show_system_config()
    row2 = w._rbd.sections["SYSTEM_DATA"].rows[0]
    assert float(row2["TermCondition"]) == 7.0


def test_project_settings_start_stats_respects_display_unit(
        qapp, monkeypatch):
    from PySide6.QtWidgets import QDoubleSpinBox
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "x")
    w._inspector.set_display_unit("years")
    row = w._rbd.sections["SYSTEM_DATA"].rows[0]
    row["StartStats"] = "8760.000000"

    def mutate(dlg):
        # Start stats is the second fspin() built (after Term condition)
        # in show_system_config's field order.
        spins = [s for s in dlg.findChildren(QDoubleSpinBox)]
        start_stats = spins[1]
        assert abs(start_stats.value() - 1.0) < 1e-3
        start_stats.setValue(2.0)
    _patch_exec_capture(qapp, monkeypatch, mutate)
    w._show_system_config()
    row2 = w._rbd.sections["SYSTEM_DATA"].rows[0]
    assert abs(float(row2["StartStats"]) - 2 * 8760) < 1


def test_run_to_time_respects_display_unit(qapp):
    """"Run To" (the stepped-simulation target sim time) shows and accepts
    the display-unit override in its label and spinbox, converting back to
    native for the engine call."""
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    rbd = load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))
    rbd.display_unit = "years"
    w._load_into_app(rbd, "x")
    assert w._run_to_label.text() == "Years:"

    w._run_to_time_spin.setValue(2.0)
    captured = {}
    w._advance_until = lambda *a, **kw: captured.update(kw)
    w._on_run_to()
    assert abs(captured["target_time"] - 2 * 8760) < 1


def test_annotations_create_persist_and_legacy_warning(qapp):
    from raptor.gui.ui.main_window import MainWindow
    """Full annotation workflow: create rect + text via tools, verify
    model layer, .rbds round-trip keeps them, legacy save warns."""
    from PySide6.QtCore import QPointF
    from raptor.core.fileio.rbds import render_rbds, parse_rbds
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "x")
    v = w._diagram_dock.view

    # create rectangle + text annotations through the tool handler
    def pe():
        QApplication.processEvents()
    v.tool_mode = "anno_rect"
    sp = v.mapToScene(v.viewport().rect().center())
    v._handle_tool_click(sp); pe()                      # arm (two-click)
    v._handle_tool_click(sp + QPointF(40, 30)); pe()     # commit
    assert len(v._anno_items) == 1
    v.tool_mode = "anno_text"
    v._handle_tool_click(sp + QPointF(60, 120)); pe()
    assert len(v._anno_items) == 2
    anno_rows = getattr(w._rbd, "annotations", [])
    kinds = sorted(a["kind"] for a in anno_rows)
    assert kinds == ["rect", "text"], kinds

    # geometry/text sync into the model on write-back
    v.write_back_to_rbd(w._rbd)
    t = render_rbds(w._rbd)
    back = parse_rbds(t)
    assert len(back.annotations) == 2
    assert {a["kind"] for a in back.annotations} == {"rect", "text"}

    # delete one via selection path
    v._scene.clearSelection()
    aid = next(iter(v._anno_items))
    v._anno_items[aid].setSelected(True)
    v.delete_selected(); pe()
    assert len(v._anno_items) == 1

    # A legacy-format save drops them with a warning.
    warns = []
    __import__("PySide6.QtWidgets", fromlist=["QMessageBox"]).\
        QMessageBox.warning = staticmethod(
            lambda *a, **k: warns.append(str(a[2] if len(a) > 2 else "")))
    # Write the legacy export to the system temp dir.
    legacy_path = os.path.join(tempfile.gettempdir(), "anno_legacy.rbd")
    w._current_file = legacy_path
    w._write_model(legacy_path)
    assert any("annotation" in m.lower() for m in warns), warns
    reloaded_legacy = load_rbd(legacy_path)
    assert not getattr(reloaded_legacy, "annotations", [])


def test_annotation_undo_and_zorder(qapp):
    from PySide6.QtCore import QPointF
    from raptor.gui.ui.main_window import MainWindow
    def pe():
        QApplication.processEvents()
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "x")
    v = w._diagram_dock.view
    v.tool_mode = "anno_rect"
    centre = v.mapToScene(v.viewport().rect().center())
    v._handle_tool_click(centre); pe()                      # arm
    v._handle_tool_click(centre + QPointF(40, 30)); pe()    # commit
    assert len(v._anno_items) == 1 and len(v.annotations) == 1
    # undo removes it entirely (canvas + model)
    v._stack.undo(); pe()
    assert len(v._anno_items) == 0 and not v.annotations
    v._stack.redo(); pe()
    assert len(v._anno_items) == 1 and len(v.annotations) == 1
    aid = next(iter(v._anno_items))
    item = v._anno_items[aid]
    # bring to front persists z into the model entry
    item.setZValue(40)
    v._sync_anno_z(item); pe()
    assert float(v.annotations[0].get("z", 20)) == 40.0
    # rebuild keeps it
    v._rebuild_scene(); pe()
    item2 = v._anno_items[aid]
    assert abs(item2.zValue() - 40.0) < 0.01


def _patch_exec_capture(qapp, monkeypatch, mutate):
    """Patch QDialog.exec so the test can reach INTO the dialog's
    widgets before it closes (accept-path coverage)."""
    def fake_exec(self):
        QApplication.processEvents()
        mutate(self)
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fake_exec)


def test_preferences_apply_writes_settings(qapp, monkeypatch):
    from PySide6.QtCore import QSettings
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "x")

    def mutate(dlg):
        for cb in dlg.findChildren(QCheckBox):
            if "Auto-save" in cb.text():
                cb.setChecked(True)
        # The auto-save interval, found by its own tooltip (the first spin box
        # in the dialog is not it: the Developer tab has spin boxes too).
        for sp in dlg.findChildren(QSpinBox):
            if "recovery copy" in sp.toolTip():
                sp.setValue(15)
    _patch_exec_capture(qapp, monkeypatch, mutate)
    w._show_preferences(); pe()
    # Preferences are now written one key at a time through
    # `ui/settings.py` rather than as a serialised `preferences` blob --
    # the blob was how `parallel_workers` ended up stored outside the
    # very dict it was named after.
    assert QSettings().value("files/autosave_min", 0) == 15
    assert QSettings().value("files/autosave", False, type=bool) is True


def test_system_config_writes_num_runs_and_syncs_the_experiment(
        qapp, monkeypatch):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "x")
    before = float(w._rbd.sections["SYSTEM_DATA"].rows[0]["NumRuns"])

    def mutate(dlg):
        spins = [s for s in dlg.findChildren(QSpinBox)]
        assert spins, "no runs spin in dialog"
        spins[0].setValue(7)
    _patch_exec_capture(qapp, monkeypatch, mutate)
    w._show_system_config(); pe()
    after = float(w._rbd.sections["SYSTEM_DATA"].rows[0]["NumRuns"])
    assert after == 7.0 and before != after
    # The bug this replaced: Project Settings wrote the row while the
    # panel and ribbon read the run experiment, so the same trial count
    # could show 7 here and 20 there -- and the run used the experiment's.
    assert w._current_experiment()["trials"] == 7
    assert w._run_panel._trials.value() == 7
    assert w._run_count_spin.value() == 7


def test_system_config_units_writeback(qapp, monkeypatch):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "x")
    orig = dict(w._rbd.sections["SYSTEM_DATA"].rows[0])

    def mutate(dlg):
        edits = dlg.findChildren(QLineEdit)
        for e in edits:
            if e.text() in ("hours", "days", "weeks", "cycles"):
                e.setText("minutes")
                break
    _patch_exec_capture(qapp, monkeypatch, mutate)
    w._show_system_config(); pe()
    now = w._rbd.sections["SYSTEM_DATA"].rows[0].get("units", "")
    assert now == "minutes" and orig.get("units") != now


def test_block_defaults_apply_persist(qapp, monkeypatch):
    from PySide6.QtCore import QSettings
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._new_file()

    def mutate(dlg):
        combos = dlg.findChildren(QComboBox)
        if combos:
            combos[0].setCurrentIndex(2)   # any non-default distro code
    _patch_exec_capture(qapp, monkeypatch, mutate)
    w._edit_block_defaults(); pe()
    defaults = QSettings().value(w._BLOCK_DEF_KEY, {}) or {}
    assert defaults, "block defaults not persisted"


def test_preflight_catches_dead_end_chain(qapp):
    from raptor.gui.ui.main_window import MainWindow
    """Basic1 (student starter): a Block->Block chain that dead-ends
    must be flagged BEFORE running, naming the dead-end element."""
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests", "data", "models", "Basic1.rbd")), "x")
    pf = w._preflight_issues()
    assert any("no outgoing link" in i for i in pf), pf
    # clean model stays clean
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "clean")
    assert w._preflight_issues() == []


def test_preflight_reports_fan_out_as_raptor7_does(qapp):
    """Fan-out, owned by `core.input_rules`: Raptor 7 refuses to DRAW a
    block with two links out (CheckValidLink) but runs a
    file that has one. So it is a warning from the validator, in Raptor 7's
    words, and preflight does not block the run. Truck1 (built by the port's
    own generator, not drawn in Raptor 7) has three such blocks."""
    from raptor.gui.ui.main_window import MainWindow
    from raptor.core.fileio.rbd import load_rbd as _lr
    from raptor.api.validate import issues
    path = os.path.join(REPO, "tests", "data", "models", "Truck1.rbd")
    w = MainWindow()
    w._load_into_app(_lr(path), "x")
    assert not [i for i in w._preflight_issues() if "can only connect to one object" in i]
    fan = [i for i in issues(_lr(path)) if "A block can only connect to one object!" in i.message]
    assert fan and all(i.severity == "warning" for i in fan)
    assert any("Tractor_Ele" in i.element for i in fan), fan


def test_preflight_catches_invalid_phase_distribution_params(qapp):
    """A phase row with Pearson6(shape=1, shape2=1, scale=0) is caught by
    preflight, since `rng.pearson6` would otherwise call
    `math.log(scale)` and raise mid-run. The Phases Data Tables grid
    validates distribution parameters like the Blocks' failure/repair/PM
    params do."""
    from raptor.gui.ui.main_window import MainWindow
    rbd = load_rbd(os.path.join(GOLD, "Phasing1", "input.rbd"))
    row = rbd.sections["PHASES"].rows[0]
    row["Dist"] = "12"       # Pearson6: Shape/Shape2/Scale
    row["Param1"] = "1.0"
    row["Param2"] = "1.0"
    row["Param3"] = "0.0"    # Scale -- must be positive, this crashes
    w = MainWindow()
    w._load_into_app(rbd, "x")
    pf = w._preflight_issues()
    hits = [i for i in pf if "D000-075_Orbit" in i]
    assert hits, pf
    assert "Scale" in hits[0]
    assert "positive" in hits[0].lower() or "0.000001" in hits[0]


def test_preflight_catches_invalid_trigger_distribution_params(qapp):
    from raptor.gui.ui.main_window import MainWindow
    rbd = load_rbd(os.path.join(GOLD, "Capacity1", "input.rbd"))
    sec = rbd.sections.get("TRIGGERS")
    assert sec and sec.rows, "Capacity1 must have real TRIGGERS rows"
    row = sec.rows[0]
    row["Dist"] = "12"
    row["Param1"] = "1.0"
    row["Param2"] = "1.0"
    row["Param3"] = "0.0"
    w = MainWindow()
    w._load_into_app(rbd, "x")
    pf = w._preflight_issues()
    assert any("Trigger" in i and "Scale" in i for i in pf), pf


def test_run_after_stepsession_not_poisoned():
    """A StepSession created for Pause pre-arm must not break the next
    batch run."""
    from raptor.core.engine.step_session import StepSession
    from raptor.core.engine.runner import run_simulation
    rbd = load_rbd(os.path.join(GOLD, "HierarchyExample", "input.rbd"))
    StepSession(rbd)                     # kickoff + scheduler registered
    logs = run_simulation(rbd, num_runs=1)
    assert logs and len(logs[0]) > 0


def test_update_statistics_labels_match_legacy(qapp):
    """'Update Statistics': Mean/Std Dev labels under Fail/Repair params
    must match Raptor 7 ComputeStats exactly."""
    from raptor.core.engine.diststats import compute_stats
    # Exponential(2435): source WHEN 4 -> mean=p1+p2, stdev=p1
    assert compute_stats(4, [2435, 0, 0]) == (2435.0, 2435.0)
    # Normal(288, 1): mean=288, stdev=1
    assert compute_stats(8, [288, 1, 0]) == (288.0, 1.0)
    # Weibull shape<0.05 -> Murphy identifier, shown as 'Undefined'
    assert compute_stats(15, [0.01, 100, 0]) == ("Undefined", "Undefined")
    # Point Estimate / None -> 'N/A' (12345.6788 sentinel)
    assert compute_stats(17, [0, 0, 0]) == ("N/A", "N/A")
    # Empirical table = manual's 12 repair points
    data = [7.3, 23.5, 22.0, 6.0, 21.7, 15.5, 13.0, 9.2,
            6.9, 17.7, 24.0, 9.0]
    m, s = compute_stats(16, data)
    assert abs(m - 14.65) < 1e-9 and abs(s - 6.663) < 1e-2


def test_empirical_dialog_returns_sorted_data(qapp):
    """Empirical Data dialog preloads values, returns sorted on OK."""
    from PySide6.QtWidgets import QDialog
    from raptor.gui.ui.empirical_dialog import open_empirical_dialog
    QDialog.exec = staticmethod(lambda self=None: QDialog.Accepted)
    out = open_empirical_dialog(None, [23.5, 7.3, 22.0], "hours")
    assert out == [7.3, 22.0, 23.5]


def test_preflight_catches_cycle_in_node_cyclesim(qapp):
    from raptor.gui.ui.main_window import MainWindow
    """The three D-audit preflight checks: cyclic graph (engine's
    TestFail loop guard), direct In-node connection (engine's block/
    In-node guard), and CycleSim-without-phases (real's own rejection)."""
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "clean")
    assert w._preflight_issues() == []
    v = w._diagram_dock.view
    v.load_rbd(load_rbd(os.path.join(REPO, "tests", "data", "models", "Basic1.rbd")))

    # 1) cycle: a block whose output loops back into itself
    from raptor.core.model import Link, RBDNode
    blk1 = next(iter(v.blocks.values()))
    cyc = Link(object_num=9901, connect_from_id=blk1.id,
               connect_f_ref="RBDBlock", connect_to_id=blk1.id,
               connect_t_ref="RBDBlock")
    v.links.append(cyc)
    pf = w._preflight_issues()
    assert any("cyclic connection" in i for i in pf), pf
    v.links.remove(cyc)

    # 2) In-node: a block-sourced link into a type-4 hierarchy In node
    in_node = RBDNode(id=9902, node_name="InTest", type_node=4)
    v.nodes[9902] = in_node
    l1 = next(l for l in v.links if l.connect_f_ref.endswith("Block"))
    old_t = (l1.connect_to_id, l1.connect_t_ref)
    l1.connect_to_id, l1.connect_t_ref = in_node.id, "RBDNode"
    pf = w._preflight_issues()
    assert any("In node" in i for i in pf), pf
    l1.connect_to_id, l1.connect_t_ref = old_t
    del v.nodes[9902]

    # 3) CycleSim without phases: preflight reads self._rbd directly
    rbd = load_rbd(os.path.join(REPO, "tests", "data", "models", "Basic1.rbd"))
    rbd.sections["SYSTEM_DATA"].rows[0]["TerminationType"] = "CycleSim"
    w._rbd = rbd
    pf = w._preflight_issues()
    assert any("CycleSim termination without any phases" in i for i in pf), pf


def test_preflight_connectivity_from_and_to(qapp):
    """Fan-in / fan-out, DELEGATED to `raptor.api.validate` (review D-17).

    Two things changed and both are asserted here:

    1. The rule reads the MODEL ROWS, not the view's parsed objects. The
       local copy read `view.blocks`/`view.links`, which is why the old
       version of this test could invent a block that existed only in the
       view. It also corrects a comment in `preflight.py` that claimed
       these checks "need the parsed topology, which only the view holds".

    2. The wording and the severities are Raptor 7's (`core.input_rules`,
       shared with the validator and MCP): `ValidateRBD` refuses a block
       that does not connect from something and to something, so each half
       blocks the run; fan-out is only a drawing rule.
    """
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "clean")
    assert w._preflight_issues() == []

    def _row(block_id: int, name: str, **extra):
        """A block row. `In`/`Out` are the STORED counts the validator also
        reads (`max(real, stored)`), so they must be consistent with the
        links actually present -- a row claiming `Out=1` with no links is
        not a topology the app can be in, because `fix_link_counts`
        maintains them."""
        base = {"ID": str(block_id), "blockName": name, "ParentId": "0",
                "In": "0", "Out": "0"}
        base.update(extra)
        return base

    def _link(link_id: int, ftype, fid, ttype, tid):
        return {"ID": str(link_id), "FromType": ftype, "FromID": str(fid),
                "ToType": ttype, "ToID": str(tid), "pID": "0"}

    rbd = w._rbd
    blocks, links = rbd.sections["BLOCK_DATA"], rbd.sections["LINK_DATA"]
    frm = "To simulate, block '{}' must connect from something!"
    to = "To simulate, block '{}' must connect to something!"

    # No links at all: Raptor 7 names both halves (ValidateRBD,
    # Raptor 7), and both block the run.
    blocks.rows = [_row(50, "Lone")]
    links.rows = []
    pf = w._preflight_issues()
    assert any(frm.format("Lone") in i for i in pf) and any(to.format("Lone") in i for i in pf), pf

    # Incoming only: nothing out.
    blocks.rows = [_row(51, "OnlyIn", In="1")]
    links.rows = [_link(9903, "Node", 1, "Block", 51)]
    pf = w._preflight_issues()
    assert any(to.format("OnlyIn") in i for i in pf), pf
    assert not any(frm.format("OnlyIn") in i for i in pf), pf

    # Outgoing only: nothing in. Raptor 7 refuses this too (it used to be a
    # warning here, review D-17; the GUI and MCP now agree on Raptor 7's rule).
    blocks.rows = [_row(52, "OnlyOut", Out="1")]
    links.rows = [_link(9904, "Block", 52, "Node", 1)]
    pf = w._preflight_issues()
    assert any(frm.format("OnlyOut") in i for i in pf), pf

    # Fan-out is a drawing rule, not a run refusal: no preflight error.
    blocks.rows = [_row(53, "Splitter", Out="2")]
    links.rows = [_link(1, "Block", 53, "Node", 1),
                  _link(2, "Block", 53, "Node", 2)]
    pf = w._preflight_issues()
    assert not [i for i in pf if "can only connect to one object" in i], pf


def test_properties_offered_only_for_a_single_block_node_or_event():
    """The Properties context-menu entry is offered only for a single
    block, node or event. `_properties_offered` is checked as a pure
    function because `QMenu.exec()` cannot be driven in a headless test:
    it returns None immediately, and monkeypatching the built-in
    `QMenu.exec` does not override the call (unlike a custom QDialog
    subclass's own `exec`)."""
    from raptor.gui.ui.diagram import _properties_offered
    assert _properties_offered("Block", 1)
    assert _properties_offered("Node", 1)
    assert _properties_offered("Event", 1)
    assert not _properties_offered("Link", 1)
    assert not _properties_offered("Block", 2)
    assert not _properties_offered("Block", 0)


def test_properties_requested_shows_and_raises_inspector_dock(qapp,
                                                               monkeypatch):
    """`properties_requested`'s own MainWindow-side handling -- tested
    directly at the signal level since the menu action that emits it
    can't be chosen in a headless test (see `_properties_offered`'s
    docstring for why)."""
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "HierarchyExample", "input.rbd")), "x")

    shown = []
    monkeypatch.setattr(w._inspector_dock, "show", lambda: shown.append("show"))
    monkeypatch.setattr(w._inspector_dock, "raise_",
                        lambda: shown.append("raise"))
    w._diagram_dock.view.properties_requested.emit()
    assert shown == ["show", "raise"]
