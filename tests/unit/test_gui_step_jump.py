"""Jump in a stepped session: the rest of this run goes by and the next run stops at its
first change; Finish completes every run that is left."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests", "golden", "TwoBlockRedundancy", "input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    w = MainWindow()
    rbd = load_rbd(FIXTURE)
    rbd.section("SYSTEM_DATA").rows[0]["TermCondition"] = "30"
    rbd.section("SYSTEM_DATA").rows[0]["NumRuns"] = "3"
    w._load_into_app(rbd, None)
    return w


def test_jump_is_off_until_a_stepped_session_has_a_later_run(window, qapp):
    assert not window._jump_action.isEnabled()
    window._on_step_change()
    qapp.processEvents()
    assert window._step_session.num_runs == 3 and window._jump_action.isEnabled()
    window._jump_action.trigger()
    window._jump_action.trigger()
    assert window._step_session.run_index == 2
    assert not window._jump_action.isEnabled(), "the last run has no run to jump to"
    window._on_step_terminate()
    assert not window._jump_action.isEnabled()


def test_finishing_after_a_jump_reports_every_run(window, qapp):
    window._on_step_change()
    window._jump_action.trigger()
    assert window._step_session.run_index == 1 and window._step_session.time == 0.0
    window._on_step_finish()
    qapp.processEvents()
    assert window._step_session is None
    assert len(window._results._event_logs) == 3 and len(window._results._report_data) == 3
    assert window._results._manifest["trials"] == 3


def test_stepping_past_the_end_of_a_run_goes_on_into_the_next():
    """Raptor 7 stays in step mode across trials. Stepping off the end of
    run 1 used to call Finish, which ran every other trial unseen."""
    from PySide6.QtWidgets import QApplication
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        m = "tests/golden/TwoBlockRedundancy/input.rbd"
        w._load_into_app(load_rbd(m), m)
        w._set_exp_field("trials", 3)
        w._set_exp_field("term_condition", 50.0)
        for _ in range(2000):
            w._on_step()
            s = w._step_session
            if s is None or s.run_index >= 1:
                break
        assert w._step_session is not None, "stepping ended the batch"
        assert w._step_session.run_index == 1
        assert not w._results.has_results()
    finally:
        w._dirty = False
        w.close()
