"""GUI: the analytical dialog and the age fields in the Inspector."""
from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbds import load_rbds, save_rbds
from raptor.core.fileio.typed import block_from_row
from raptor.gui.ui.analytic_dialog import AnalyticDialog
from raptor.gui.ui.diagram import DiagramView
from raptor.gui.ui.derived_dialog import DerivedResultsDialog

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS = os.path.join(REPO, "new_docs", "models")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _dialog(qapp, name, *, current_file=None):
    class Win(QWidget):
        _rbd = load_rbds(os.path.join(MODELS, f"{name}.rbds"))
        _current_file = current_file

    win = Win()
    dialog = AnalyticDialog(win)
    # Nothing solves on opening; Compute does.
    assert dialog._notice_title.text() in ("Ready to compute",
                                           "Enter a mission time")
    dialog.compute()
    dialog.wait_for_result()
    return win, dialog


def _card_texts(dialog):
    from PySide6.QtWidgets import QLabel
    return [label.text() for label in dialog._summary_page.findChildren(QLabel)
            if label.objectName() == "MetricValue"]


def test_analytic_dialog_computes_reliability(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    assert dialog._pages.currentIndex() == 1
    assert dialog._result["reliability"] == pytest.approx(0.38460369, rel=1e-6)
    assert any(text.startswith("0.384604") for text in _card_texts(dialog))
    # Per-element table: every element, sorted by criticality, N/A last.
    table = dialog._elements
    assert table.rowCount() == 3
    assert table.item(0, 0).text() == "Battery_Pack"
    assert dialog.tab_shown(dialog._tab_curve)
    assert not dialog.tab_shown(dialog._tab_equation)
    assert dialog._copy.isEnabled()
    dialog.close()


def test_analytic_dialog_switches_to_availability(qapp):
    _win, dialog = _dialog(qapp, "Vendo1")
    dialog._mode.setCurrentIndex(1)
    dialog.compute()
    dialog.wait_for_result()
    assert dialog._result["mode"] == "availability"
    assert dialog._result["availability"] == pytest.approx(0.9706853, rel=1e-6)
    assert any(text.startswith("0.970685") for text in _card_texts(dialog))
    # Reliability-only settings are hidden, and so is the curve tab.
    assert dialog._times.isHidden() or not dialog._times.isVisibleTo(dialog)
    assert not dialog.tab_shown(dialog._tab_curve)
    assert dialog._elements.horizontalHeaderItem(3).text() == "MTTR"
    dialog.close()


def test_analytic_dialog_refuses_phases_without_a_traceback(qapp):
    _win, dialog = _dialog(qapp, "AirlinerMission")
    assert dialog._pages.currentIndex() == 0
    assert "outside the static solver" in dialog._notice_title.text()
    assert "choose a Phase" in dialog._notice_body.text()
    assert not dialog._copy.isEnabled()
    dialog.close()


def test_a_tab_that_does_not_apply_leaves_no_gap(qapp):
    """Availability has no Curve: its tab is taken out, not hidden (a
    hidden tab under the styled bar left a gap before Assumptions)."""
    _win, dialog = _dialog(qapp, "Lantern1")
    labels = lambda: [dialog._tabs.tabText(i).split(" (")[0]  # noqa: E731
                      for i in range(dialog._tabs.count())]
    assert labels() == ["Summary", "Elements", "Curve", "Assumptions"]
    dialog._mode.setCurrentIndex(dialog._mode.findData("availability"))
    dialog.compute()
    dialog.wait_for_result()
    assert labels() == ["Summary", "Elements", "Assumptions"]
    dialog._equation.setChecked(True)
    dialog._mode.setCurrentIndex(dialog._mode.findData("reliability"))
    dialog.compute()
    dialog.wait_for_result()
    assert labels() == ["Summary", "Elements", "Curve", "Equation",
                        "Assumptions"]
    dialog.close()


def test_analytic_dialog_explains_a_bad_setting(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog._bx.setText("ten")
    dialog.compute()
    dialog.wait_for_result()
    assert dialog._pages.currentIndex() == 0
    assert "numbers" in dialog._notice_body.text()
    dialog._bx.setText("120")
    dialog.compute()
    dialog.wait_for_result()
    assert "BX percentage" in dialog._notice_body.text()
    dialog.close()


def test_analytic_dialog_flags_stale_settings(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog._bx.setText("5")
    assert "press Compute" in dialog._banner.text()
    dialog.compute()
    dialog.wait_for_result()
    assert list(dialog._result["bx"]) == ["5"]
    assert "press Compute" not in dialog._banner.text()
    dialog.close()


def test_analytic_dialog_equation_tab_and_copy(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog._equation.setChecked(True)
    dialog.compute()
    dialog.wait_for_result()
    assert dialog.tab_shown(dialog._tab_equation)
    assert "R_Switch" in dialog._equation_view.toPlainText()
    dialog._copy_result()
    assert "Battery_Pack" in QApplication.clipboard().text()
    dialog.close()


def test_analytic_dialog_non_time_terminated_asks_for_a_mission_time(qapp):
    class Win(QWidget):
        _rbd = load_rbds(os.path.join(MODELS, "Lantern1.rbds"))

    Win._rbd.sections["SYSTEM_DATA"].rows[0]["TerminationType"] = "FailSim"
    dialog = AnalyticDialog(Win())
    dialog.wait_for_result()
    assert dialog._pages.currentIndex() == 0
    assert "mission time" in dialog._notice_title.text().lower()
    dialog._at.setValue(48.0)
    dialog.compute()
    dialog.wait_for_result()
    assert dialog._pages.currentIndex() == 1
    dialog.close()


def test_analytic_dialog_does_not_change_the_model(qapp):
    win, dialog = _dialog(qapp, "Vendo1")
    from raptor.core.fileio.rbds import render_rbds
    before = render_rbds(win._rbd)
    dialog.compute()
    dialog.wait_for_result()
    assert render_rbds(win._rbd) == before
    dialog.close()


def test_age_fields_survive_undo_and_rbds_round_trip(qapp):
    view = DiagramView()
    view.load_rbd(load_rbds(os.path.join(MODELS, "Lantern1.rbds")))
    bid = sorted(view.blocks)[0]
    view.set_block_duty_cycle(bid, 0.4)
    view.set_block_start_age(bid, 12.5)
    view.set_block_restoration_factor(bid, 0.75)
    view.set_block_restoration_type(bid, 2)
    block = view.blocks[bid]
    assert block.duty_cycle == pytest.approx(0.4)
    assert block.start_age == pytest.approx(12.5)
    assert block.restoration_factor == pytest.approx(0.75)
    assert block.restoration_type == 2

    view._stack.undo()
    assert view.blocks[bid].restoration_type == 1   # set by the factor edit
    view._stack.redo()
    assert view.blocks[bid].restoration_type == 2
    view._stack.undo()
    view._stack.undo()                  # the factor and its Type I, together
    assert view.blocks[bid].restoration_type == 0
    assert view.blocks[bid].restoration_factor == 0.0
    view._stack.redo()
    view._stack.redo()

    view.write_back_to_rbd(view._rbd_ref)
    path = os.path.join(os.path.dirname(__file__), "age_roundtrip.rbds")
    try:
        save_rbds(view._rbd_ref, path)
        again = load_rbds(path)
        row = next(r for r in again.sections["BLOCK_DATA"].rows
                   if int(float(r["ID"])) == bid)
        reloaded = block_from_row(row)
        assert reloaded.duty_cycle == pytest.approx(0.4)
        assert reloaded.start_age == pytest.approx(12.5)
        assert reloaded.restoration_factor == pytest.approx(0.75)
        assert reloaded.restoration_type == 2
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_derived_results_dialog_shows_classifications_and_criticality(qapp):
    from types import SimpleNamespace

    report = SimpleNamespace(
        availability=0.97, inherent_availability=0.98,
        achieved_availability=0.975, mttff=100.0,
        mtbf_total=200.0, mtbe_total=250.0,
        num_component_cms=3, num_component_pms=1,
        red_corrective_time=5.0, red_pm_time=1.0,
        red_logistics_time=0.0, red_done_time=0.0, red_other_time=0.0,
        criticality={1: {"name": "Pump", "failure": 0.6,
                         "downtime": 0.4, "cost": 0.2,
                         "downing_event": None}})

    class Results:
        _report_data = [report]

    class Win(QWidget):
        _results = Results()

    win = Win()
    dialog = DerivedResultsDialog(win, [report])
    from PySide6.QtWidgets import QTabWidget
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    assert tabs.count() == 2
    dialog.close()


def test_scope_controls_appear_only_when_the_model_has_them(qapp):
    _win, plain = _dialog(qapp, "Lantern1")
    assert plain._phase.count() == 0
    assert not plain._form.isRowVisible(plain._phase)
    assert not plain._form.isRowVisible(plain._standby)
    plain.close()
    _win, phased = _dialog(qapp, "AirlinerMission")
    assert phased._form.isRowVisible(phased._phase)
    assert phased._phase.count() == 7          # prompt, five phases, all
    assert phased._phase.itemData(6) == "all"
    assert phased._result is None              # refused until a phase is chosen
    phased._phase.setCurrentIndex(3)
    phased._ignore.setChecked(True)
    phased._ignore_pm.setChecked(True)
    phased._ignore_spares.setChecked(True)
    phased._ignore_resources.setChecked(True)
    phased._standby.setCurrentIndex(1)
    phased.compute()
    phased.wait_for_result()
    assert phased._result["scope"]["standby"] is None   # no standby here
    assert "Phase 3" in phased._scope_note.text()
    assert "standby" not in phased._scope_note.text()
    assert phased._result["scope"]["phase"] == 3
    assert phased._result["scope"]["ignore"] == [
        "pm", "repairs", "resources", "spares"]
    phased.close()


def test_all_phases_solves_each_and_shows_one_at_a_time(qapp, tmp_path):
    _win, dialog = _dialog(qapp, "AirlinerMission")
    for box in (dialog._ignore, dialog._ignore_pm, dialog._ignore_spares,
                dialog._ignore_resources):
        box.setChecked(True)
    dialog._phase.setCurrentIndex(dialog._phase.findData("all"))
    dialog.compute()
    dialog.wait_for_result()
    assert dialog._pages.currentIndex() == 1
    assert not dialog._phase_picker_row.isHidden()
    assert dialog._phase_picker.count() == 5
    first = dialog._result
    assert first["scope"]["phase"] == 1
    dialog._phase_picker.setCurrentIndex(3)
    assert dialog._result["scope"]["phase"] == 4
    assert dialog._result["reliability"] != first["reliability"]
    # This result saves every phase; each phase is also kept on its own.
    path = dialog.export_result(str(tmp_path / "phases.xlsx"))
    sheets = _sheets(path)
    assert "Phase 1 Summary" in sheets and "Phase 5 Summary" in sheets
    both = str(tmp_path / "both.xlsx")
    dialog.export_both(both)
    dialog.wait_for_result()
    sheets = _sheets(both)
    assert "Reliability phase 5 Summary" in sheets
    assert "Availability phase 1 Summary" in sheets
    dialog._phase.setCurrentIndex(dialog._phase.findData(2))
    assert not dialog._busy and dialog._result["scope"]["phase"] == 2
    assert dialog._phase_picker_row.isHidden()
    dialog.close()


def test_dependency_control_appears_only_when_the_model_has_them(qapp):
    _win, plain = _dialog(qapp, "Lantern1")
    assert not plain._form.isRowVisible(plain._dependencies)
    assert plain._dependencies.currentData() is None
    plain.close()

    _win, dependent = _dialog(qapp, "EspressoDep1")
    assert dependent._form.isRowVisible(dependent._dependencies)
    assert dependent._dependencies.currentData() is None
    assert dependent._result is None            # refused until a choice
    assert "choose a Dependencies treatment" in dependent._notice_body.text()

    dependent._dependencies.setCurrentIndex(2)  # down
    dependent.compute()
    dependent.wait_for_result()
    assert dependent._result["scope"]["dependencies"] == "down"
    assert "dependency treated as down" in dependent._scope_note.text()

    # Opening another model must not carry the previous choice over.
    dependent._fill_scope_controls(load_rbds(os.path.join(MODELS,
                                                          "Lantern1.rbds")))
    assert dependent._dependencies.currentIndex() == 0
    assert not dependent._form.isRowVisible(dependent._dependencies)
    dependent.close()


def test_dependency_up_is_offered_and_changes_the_answer(qapp):
    _win, dialog = _dialog(qapp, "EspressoDep1")
    dialog._ignore.setChecked(True)
    dialog._dependencies.setCurrentIndex(1)     # up
    dialog.compute()
    dialog.wait_for_result()
    up = dialog._result["reliability"]

    dialog._dependencies.setCurrentIndex(2)     # down
    dialog.compute()
    dialog.wait_for_result()
    down = dialog._result["reliability"]
    assert up > down
    dialog.close()


def test_bx_card_says_what_the_percentage_means(qapp):
    from PySide6.QtWidgets import QLabel
    _win, dialog = _dialog(qapp, "Lantern1")
    subs = [label.text() for label in dialog._summary_page.findChildren(QLabel)
            if label.objectName() == "MetricSub"]
    assert "10% of systems fail by this time" in subs
    assert "node" not in dialog._banner.text()
    dialog.close()


def test_export_writes_a_workbook_and_copy_explains_the_bdd(qapp, tmp_path):
    import zipfile

    from PySide6.QtWidgets import QApplication
    _win, dialog = _dialog(qapp, "Lantern1")
    out = dialog.export_result(str(tmp_path / "result.xlsx"))
    assert out is not None
    with zipfile.ZipFile(out) as book:
        assert "xl/workbook.xml" in book.namelist()
        names = book.read("xl/workbook.xml").decode()
    for sheet in ("Summary", "Elements", "Curve", "Assumptions"):
        assert sheet in names
    dialog._copy_result()
    assert "binary decision diagram" in QApplication.clipboard().text()
    dialog.close()


def test_derived_dialog_copies_and_exports(qapp, tmp_path):
    from types import SimpleNamespace

    run = SimpleNamespace(operational_availability=0.9, criticality={
        1: {"name": "Pump", "failure": 0.5, "downtime": 0.25, "cost": 0.1}})
    parent = QWidget()
    dialog = DerivedResultsDialog(parent, [run])
    dialog.copy_current()
    assert "Operational availability\t0.9" in QApplication.clipboard().text()
    dialog._tabs.setCurrentIndex(1)
    dialog.copy_current()
    assert "Pump\t50.00" in QApplication.clipboard().text()
    assert dialog.export_xlsx(str(tmp_path / "d.xlsx")) is not None
    assert (tmp_path / "d.xlsx").stat().st_size > 0
    # The same tabs as OpenDocument, by the file's extension.
    assert dialog.export_xlsx(str(tmp_path / "d.ods")) == \
        str(tmp_path / "d.ods")
    import zipfile
    with zipfile.ZipFile(tmp_path / "d.ods") as z:
        content = z.read("content.xml").decode()
    assert 'table:name="Availability"' in content
    assert 'table:name="Criticality"' in content
    dialog.close()


def test_analytic_dialog_shows_determinate_progress(qapp):
    """One percentage for the whole solve (the solver's `_Overall` figure),
    the time left instead of the pass count, never stepping back."""
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog._set_busy(True)
    dialog._token = 7
    dialog._on_progress(7, "Building BDD", 25, 1000)
    assert dialog._progress_label.text() == "2% · Building BDD"
    dialog._on_progress(6, "stale", 900, 1000)
    assert dialog._progress_label.text() == "2% · Building BDD"
    dialog._on_progress(7, "Working out MTTF — pass 50 of about 100, "
                           "about 3 s left", 500, 1000)
    assert dialog._progress_label.text() == \
        "50% · Working out MTTF — about 3 s left"
    dialog._on_progress(7, "Working out MTTF", 480, 1000)
    assert dialog._progress_label.text() == "50% · Working out MTTF"
    dialog._set_busy(False)
    dialog.close()


def test_bdd_limit_is_comma_grouped_and_updates_the_header(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog._max_bdd_nodes.setValue(1_250_000)
    assert "1,250,000" in dialog._limits_host.toolTip()
    assert dialog._max_bdd_nodes.text().replace("\u202f", ",").replace(",", "") \
        .isdigit() or "1,250,000" in dialog._max_bdd_nodes.text()
    dialog.close()


def test_hierarchy_combo_lists_and_solves_one_hierarchy(qapp):
    class Win(QWidget):
        _rbd = load_rbd(os.path.join(REPO, "tests", "golden",
                                     "HierarchyExample", "input.rbd"))

    dialog = AnalyticDialog(Win())
    dialog.wait_for_result()
    labels = [dialog._hierarchy.itemText(i)
              for i in range(dialog._hierarchy.count())]
    assert any("500path" in label for label in labels)
    index = next(i for i in range(dialog._hierarchy.count())
                 if "500path" in dialog._hierarchy.itemText(i))
    dialog._hierarchy.setCurrentIndex(index)
    dialog._dependencies.setCurrentText("Treat as up")
    dialog._ignore.setChecked(True)
    dialog._ignore_pm.setChecked(True)
    dialog._ignore_spares.setChecked(True)
    dialog._ignore_resources.setChecked(True)
    dialog.compute()
    dialog.wait_for_result()
    assert dialog._result is not None
    assert dialog._result["hierarchy"] == "500path"
    assert "500path" in dialog._scope_note.text()
    assert "hierarchy 500path" in dialog._banner.text()
    dialog.close()


def test_cancel_stops_a_worker_before_it_reports_a_result(qapp, monkeypatch):
    import queue
    import threading
    from raptor.gui.ui import analytic_dialog as module

    def slow_solve(*_args, progress=None, **_kwargs):
        progress("Building BDD", 1, 10)
        return {}

    monkeypatch.setattr(module, "analyse_rbd_set",
                        lambda *a, **k: [slow_solve(*a, **k)])
    inbox: queue.Queue = queue.Queue()
    progress: queue.Queue = queue.Queue()
    cancelled = threading.Event()
    cancelled.set()
    module._solve(1, object(), "model", {}, inbox, progress, cancelled)
    token, result, error = inbox.get_nowait()
    assert token == 1 and result is None
    assert error[0] == "cancelled"


def test_cancel_button_stops_a_running_gui_solve(qapp, monkeypatch):
    import threading
    from raptor.gui.ui import analytic_dialog as module

    started = threading.Event()

    def slow_solve(*_args, progress=None, **_kwargs):
        started.set()
        for step in range(200):
            progress("Building BDD", step, 200)
            time.sleep(0.01)
        return {}

    monkeypatch.setattr(module, "analyse_rbd_set",
                        lambda *a, **k: [slow_solve(*a, **k)])

    class Win(QWidget):
        _rbd = load_rbds(os.path.join(MODELS, "Lantern1.rbds"))

    dialog = AnalyticDialog(Win())
    dialog.compute()
    assert started.wait(2.0)
    dialog._cancel_solve()
    assert dialog._banner.text() == "Cancelling…"
    dialog.wait_for_result()
    assert dialog._notice_title.text() == "Analysis cancelled"
    assert dialog._pages.currentIndex() == 0
    assert not dialog._cancel.isVisible()
    dialog.close()


def test_choosing_the_suggested_treatment_replaces_the_refusal(qapp):
    """A refusal suggests a choice; making it must replace the refusal at
    once (it stayed on screen), and Compute then solves."""
    _win, dialog = _dialog(qapp, "AirlinerMission")
    dialog.show()
    assert dialog._pages.currentIndex() == 0
    dialog._phase.setCurrentIndex(1)
    assert dialog._notice_title.text() == "Ready to compute"
    assert not dialog._busy
    dialog.compute()
    assert dialog._notice_title.text() == "Working out the results…"
    dialog.wait_for_result()
    assert dialog._pages.currentIndex() == 1
    assert dialog._result is not None
    dialog.close()


def test_a_pick_from_the_fix_buttons_list_solves(qapp, monkeypatch):
    _win, dialog = _dialog(qapp, "AirlinerMission")
    dialog.show()
    monkeypatch.setattr(dialog._phase, "showPopup", lambda: None)
    dialog._fix.click()                     # "Choose a phase…"
    dialog._phase.setCurrentIndex(1)
    assert dialog._busy
    dialog.wait_for_result()
    assert dialog._pages.currentIndex() == 1
    dialog.close()


def test_a_new_treatment_resets_the_summary_and_going_back_is_cached(
        qapp, monkeypatch):
    from raptor.gui.ui import analytic_dialog as module
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog.show()
    first = dialog._result
    dialog._equation.setChecked(True)
    assert dialog._pages.currentIndex() == 0        # not the old answer
    assert dialog._notice_title.text() == "Ready to compute"
    dialog.compute()
    dialog.wait_for_result()
    assert dialog._pages.currentIndex() == 1
    # Back to the first settings: their result at once, with no solve.
    monkeypatch.setattr(module, "analyse_rbd_set", None)   # a solve would fail
    dialog._equation.setChecked(False)
    assert not dialog._busy
    assert dialog._pages.currentIndex() == 1
    assert dialog._result is first
    assert "Shown from earlier" in dialog._banner.text()
    dialog.compute()                                   # same: no solve
    assert not dialog._busy and dialog._result is first
    dialog.close()


def _sheets(path):
    import zipfile
    return zipfile.ZipFile(path).read("xl/workbook.xml").decode()


def test_export_both_solves_the_other_mode_and_keeps_the_page(
        qapp, tmp_path):
    """Export ▸ Reliability and availability: the current settings in both
    modes, the other worked out first; the page keeps its own result."""
    _win, dialog = _dialog(qapp, "Lantern1")
    shown = dialog._result
    target = str(tmp_path / "both.xlsx")
    assert dialog.export_both(target) is None      # a solve was started
    assert dialog._busy
    dialog.wait_for_result()
    assert dialog._result is shown and dialog._pages.currentIndex() == 1
    assert dialog._mode.currentData() == "reliability"
    sheets = _sheets(target)
    assert "Reliability Summary" in sheets and "Availability Summary" in sheets
    # Now both are known: the second export writes at once, no solve.
    again = str(tmp_path / "again.xlsx")
    assert dialog.export_both(again) == again and not dialog._busy
    dialog.close()


def test_export_both_says_why_when_the_other_mode_is_refused(
        qapp, tmp_path, monkeypatch):
    from raptor.gui.ui import analytic_dialog as module
    warned = []
    monkeypatch.setattr(module.msgbox, "warning",
                        lambda *args: warned.append(args))
    _win, dialog = _dialog(qapp, "Lantern1")
    shown = dialog._result
    dialog._max_bdd_nodes.setValue(2)          # the other solve will refuse
    dialog.export_both(str(tmp_path / "no.xlsx"))
    dialog.wait_for_result()
    assert warned and "not written" in warned[0][2]
    assert not (tmp_path / "no.xlsx").exists()
    assert dialog._result is shown and dialog._pages.currentIndex() == 1
    dialog.close()


def test_the_ready_notice_says_earlier_results_are_kept(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog._equation.setChecked(True)
    assert dialog._notice_title.text() == "Ready to compute"
    assert "Earlier results are kept" in dialog._notice_body.text()
    dialog.close()


def test_a_model_edit_is_not_answered_from_the_cache(qapp):
    win, dialog = _dialog(qapp, "Lantern1")
    first = dialog._result
    win._model_revision = getattr(win, "_model_revision", 0) + 1
    dialog.compute()
    assert dialog._busy
    dialog.wait_for_result()
    assert dialog._result is not first
    dialog.close()


def test_closing_the_model_then_opening_another_starts_clean(qapp):
    """Closing the file cleared nothing, and opening another then said "The
    model has changed since this result" over the fresh page."""
    win, dialog = _dialog(qapp, "Lantern1")
    win._model_revision = 1
    dialog._solved_revision = 1
    win._rbd, win._model_revision = None, 2           # File > Close
    dialog._check_model()
    assert dialog._notice_title.text() == "No model open"
    assert dialog._result is None and not dialog._export.isEnabled()
    win._rbd = load_rbds(os.path.join(MODELS, "Vendo1.rbds"))
    win._model_revision = 3                           # File > Open
    dialog._check_model()
    dialog._check_model()                             # the watch ticks again
    assert dialog._notice_title.text() == "Ready to compute"
    assert "has changed" not in dialog._banner.text()
    dialog.close()


def test_a_change_during_a_solve_marks_the_result_out_of_date(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog.compute()
    dialog._bx.setText("5")
    dialog.wait_for_result()
    assert dialog._pages.currentIndex() == 1
    assert "press Compute" in dialog._banner.text()
    dialog.close()


def test_notice_hides_the_previous_results_scope_line(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog._scope_note.setText("This result assumes: something")
    dialog._scope_note.show()
    dialog._bx.setText("ten")
    dialog.compute()
    assert dialog._scope_note.isHidden()
    dialog.close()


def test_a_model_edit_marks_the_result_out_of_date(qapp):
    win, dialog = _dialog(qapp, "Lantern1")
    win._model_revision = 0
    dialog.compute()
    dialog.wait_for_result()
    dialog._check_model()
    assert "model has changed" not in dialog._banner.text()
    win._model_revision = 1
    dialog._check_model()
    assert "model has changed" in dialog._banner.text()
    dialog.compute()
    dialog.wait_for_result()
    assert "model has changed" not in dialog._banner.text()
    dialog.close()


def test_a_different_model_starts_over(qapp):
    win, dialog = _dialog(qapp, "Lantern1")
    win._rbd = load_rbds(os.path.join(MODELS, "Vendo1.rbds"))
    dialog._check_model()
    assert dialog._shown_rbd() is win._rbd
    # The old model's results are gone, not shown for the new one.
    assert dialog._pages.currentIndex() == 0
    assert dialog._result is None
    dialog.close()


def test_a_new_model_with_a_reused_id_still_starts_over(qapp):
    """The dialog compared id()s; a model opened after the old one was freed
    can get the same id, and the old model's results stayed on screen."""
    _win, dialog = _dialog(qapp, "Lantern1")
    assert dialog._pages.currentIndex() == 1
    import weakref

    class Gone:
        pass
    dialog._shown_rbd = weakref.ref(Gone())     # the old model, freed
    dialog._check_model()
    assert dialog._pages.currentIndex() == 0 and dialog._result is None
    dialog.close()


def test_a_different_file_starts_over_even_for_the_same_object(qapp):
    win, dialog = _dialog(qapp, "Lantern1", current_file="/a/Lantern1.rbds")
    win._current_file = "/b/Other.rbds"
    dialog.refresh_for_model()
    assert dialog._pages.currentIndex() == 0 and dialog._result is None
    dialog.close()


def test_limits_sit_behind_advanced(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog.show()
    assert not dialog._limits_host.isVisible()
    dialog._advanced.setChecked(True)
    assert dialog._limits_host.isVisible()
    dialog.close()


def test_a_repairs_refusal_offers_one_click_fixes(qapp):
    _win, dialog = _dialog(qapp, "WaterTreatment")
    dialog.show()
    assert dialog._pages.currentIndex() == 0
    assert dialog._fix.isVisible()
    assert "Ignore repairs" in dialog._fix.text()
    assert dialog._alt_fix.isVisible()
    dialog._fix.click()
    dialog.wait_for_result()
    assert dialog._ignore.isChecked()
    # This model also has PM: the next refusal offers the next fix.
    assert "preventive maintenance" in dialog._fix.text()
    dialog.close()


def test_rates_say_per_time_unit(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    from PySide6.QtWidgets import QLabel
    subs = [label.text() for label in dialog._summary_page.findChildren(QLabel)
            if label.objectName() == "MetricSub"]
    assert "per hour" in subs
    dialog.close()


def test_turning_restoration_off_clears_the_factor(qapp):
    view = DiagramView()
    view.load_rbd(load_rbds(os.path.join(MODELS, "Lantern1.rbds")))
    bid = sorted(view.blocks)[0]
    view.set_block_restoration_factor(bid, 0.6)
    assert view.blocks[bid].restoration_type == 1
    view.set_block_restoration_type(bid, 0)
    assert view.blocks[bid].restoration_type == 0
    assert view.blocks[bid].restoration_factor == 0.0
    view._stack.undo()
    assert view.blocks[bid].restoration_type == 1
    assert view.blocks[bid].restoration_factor == pytest.approx(0.6)


def test_limit_shows_its_memory_and_a_size_refusal_offers_more(qapp):
    _win, dialog = _dialog(qapp, "WaterTreatment")
    dialog.show()
    assert "MB" in dialog._bdd_memory.text() or "GB" in dialog._bdd_memory.text()
    assert dialog._max_bdd_nodes.maximum() >= 2_000_000
    for box in (dialog._ignore, dialog._ignore_pm, dialog._ignore_spares,
                dialog._ignore_resources):
        box.setChecked(True)
    dialog._max_bdd_nodes.setValue(20)
    dialog.compute()
    dialog.wait_for_result()
    assert dialog._pages.currentIndex() == 0
    assert "limit of 20 nodes" in dialog._notice_body.text()
    assert dialog._fix.isVisible() and "Try a limit of" in dialog._fix.text()
    assert dialog._advanced.isChecked()
    assert dialog._outlook_note.isVisible()
    dialog._fix.click()
    dialog.wait_for_result()
    assert dialog._pages.currentIndex() == 1
    dialog.close()


def test_closing_the_window_stops_a_running_solve(qapp, monkeypatch):
    import threading
    from raptor.gui.ui import analytic_dialog as module

    started = threading.Event()
    steps: list[int] = []

    def slow_solve(*_args, progress=None, **_kwargs):
        started.set()
        for step in range(500):
            progress("Building BDD", step, 500)
            steps.append(step)
            time.sleep(0.01)
        return {}

    monkeypatch.setattr(module, "analyse_rbd_set",
                        lambda *a, **k: [slow_solve(*a, **k)])

    class Win(QWidget):
        _rbd = load_rbds(os.path.join(MODELS, "Lantern1.rbds"))

    dialog = AnalyticDialog(Win())
    dialog.show()
    dialog.compute()
    assert started.wait(2.0)
    dialog.close()                         # the window's close button
    dialog._worker.join(2.0)
    assert not dialog._worker.is_alive()
    assert len(steps) < 400


def test_shut_down_cancels_and_waits_for_the_worker(qapp, monkeypatch):
    import threading
    from raptor.gui.ui import analytic_dialog as module

    started = threading.Event()

    def slow_solve(*_args, progress=None, **_kwargs):
        started.set()
        for step in range(500):
            progress("Computing importance", step, 500)
            time.sleep(0.01)
        return {}

    monkeypatch.setattr(module, "analyse_rbd_set",
                        lambda *a, **k: [slow_solve(*a, **k)])

    class Win(QWidget):
        _rbd = load_rbds(os.path.join(MODELS, "Lantern1.rbds"))

    dialog = AnalyticDialog(Win())
    dialog.compute()
    assert started.wait(2.0)
    dialog.shut_down()
    assert not dialog._worker.is_alive()


def test_cards_keep_their_height_and_reflow_when_the_window_shrinks(qapp):
    """A short or narrow window used to squash the cards and cut the big
    numbers in half; now the page scrolls and the cards reflow."""
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog.show()
    dialog.resize(900, 720)
    qapp.processEvents()
    assert dialog._summary_page._columns == 3
    dialog.resize(560, 560)
    for _ in range(3):
        qapp.processEvents()
    assert dialog._summary_page._columns < 3
    for card in dialog._summary_page._cards:
        assert card.height() >= card.sizeHint().height()
    dialog.close()
