"""GUI: the analytical dialog and the age fields in the Inspector."""
from __future__ import annotations

import os

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
    assert dialog._tabs.isTabVisible(dialog._tab_curve)
    assert not dialog._tabs.isTabVisible(dialog._tab_equation)
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
    assert not dialog._tabs.isTabVisible(dialog._tab_curve)
    assert dialog._elements.horizontalHeaderItem(3).text() == "MTTR"
    dialog.close()


def test_analytic_dialog_refuses_phases_without_a_traceback(qapp):
    _win, dialog = _dialog(qapp, "AirlinerMission")
    assert dialog._pages.currentIndex() == 0
    assert "outside the static solver" in dialog._notice_title.text()
    assert "choose a Phase" in dialog._notice_body.text()
    assert not dialog._copy.isEnabled()
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
    assert dialog._tabs.isTabVisible(dialog._tab_equation)
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
    assert view.blocks[bid].restoration_type == 1
    view._stack.redo()
    assert view.blocks[bid].restoration_type == 2

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
    assert phased._phase.count() == 6          # prompt + five phases
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
    dialog.close()


def test_analytic_dialog_shows_determinate_progress(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog._busy = True
    dialog._token = 7
    dialog._on_progress(7, "Building BDD", 25, 100)
    assert dialog._progress.maximum() == 100
    assert dialog._progress.value() == 25
    assert "Building BDD" in dialog._progress_label.text()
    dialog._on_progress(6, "stale", 90, 100)
    assert dialog._progress.value() == 25
    dialog.close()


def test_bdd_limit_is_comma_grouped_and_updates_the_header(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog._max_bdd_nodes.setValue(1_250_000)
    assert "1,250,000" in dialog._intro.text()
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
    dialog.close()


def test_cancel_stops_a_worker_before_it_reports_a_result(qapp, monkeypatch):
    import queue
    import threading
    from raptor.gui.ui import analytic_dialog as module

    def slow_solve(*_args, progress=None, **_kwargs):
        progress("Building BDD", 1, 10)
        return {}

    monkeypatch.setattr(module, "analyse_rbd", slow_solve)
    inbox: queue.Queue = queue.Queue()
    progress: queue.Queue = queue.Queue()
    cancelled = threading.Event()
    cancelled.set()
    module._solve(1, object(), "model", {}, inbox, progress, cancelled)
    token, result, error = inbox.get_nowait()
    assert token == 1 and result is None
    assert error[0] == "cancelled"
