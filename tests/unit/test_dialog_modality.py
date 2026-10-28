"""The application's own dialogs block only their own window, so the Help viewer stays usable behind them."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QPushButton, QVBoxLayout

from raptor.gui.ui.modality import exec_dialog

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _helper_clickable_while_open(qapp, run) -> bool:
    main = QMainWindow()
    main.resize(300, 200)
    main.show()
    helper = QDialog(None)                       # top-level, like the Help viewer
    button = QPushButton("x", helper)
    clicked: list[int] = []
    button.clicked.connect(lambda: clicked.append(1))
    QVBoxLayout(helper).addWidget(button)
    helper.show()
    dlg = QDialog(main)

    def probe():
        # through the window system, as a user's click arrives: a blocked window never sees it
        QTest.mouseClick(helper.windowHandle(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         button.mapTo(helper, button.rect().center()))
        qapp.processEvents()
        dlg.accept()

    QTimer.singleShot(300, probe)
    run(dlg)
    helper.close()
    main.close()
    return bool(clicked)


def test_a_plain_exec_blocks_the_help_window(qapp):
    """The behaviour `exec_dialog` exists to avoid."""
    assert not _helper_clickable_while_open(qapp, lambda dlg: dlg.exec())


def test_exec_dialog_leaves_other_top_level_windows_usable(qapp):
    assert _helper_clickable_while_open(qapp, exec_dialog)
