"""The conftest guard against modal dialogs nothing answers.

An unanswered modal in a headless test used to block until the watchdog
`os._exit`ed the whole pytest process with no summary -- which is how a
broken Export PDF (its error box opens modally) ended every full GUI run
at ~69 % for weeks. `_no_unanswered_modals` (tests/conftest.py) turns that
into an ordinary failure naming the dialog.
"""
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

pytestmark = pytest.mark.gui


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_blocking_static_dialogs_are_refused(qapp):
    with pytest.raises(AssertionError, match="would block forever"):
        QMessageBox.critical(None, "Boom", "x")


def test_an_unanswered_exec_fails_and_names_the_dialog(qapp):
    box = QMessageBox()
    box.setWindowTitle("Stuck")
    box.setText("nobody answers me")
    with pytest.raises(AssertionError, match="Stuck"):
        box.exec()


def test_a_dialog_that_closes_itself_is_unaffected(qapp):
    box = QMessageBox()
    QTimer.singleShot(0, box.accept)
    box.exec()


def test_a_patch_supplied_by_the_test_wins(qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: 7))
    assert QMessageBox.critical(None, "t", "x") == 7
