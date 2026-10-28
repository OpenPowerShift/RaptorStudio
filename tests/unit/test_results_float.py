"""Results > Float Window: the Results become an ordinary window (title
bar, taskbar entry), and closing that window puts them back in the main
window instead of hiding them."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "new_docs/models/HospitalPower.rbds"


def test_float_is_a_window_and_closing_it_docks_back():
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("files/save_before_run", "never")
    w = MainWindow()
    w.show()
    try:
        w._load_into_app(load_rbds(MODEL), MODEL)
        w._set_exp_field("trials", 2)
        w._run_simulation()
        d, act = w._results_dock, w._results_float_action
        act.setChecked(True)
        QApplication.processEvents()
        assert d.isFloating() and act.isChecked()
        flags = d.windowFlags()
        assert flags & Qt.WindowType.Window
        assert (flags & Qt.WindowType.Tool) != Qt.WindowType.Tool
        assert d.windowTitle().startswith("Results — HospitalPower")

        d.close()
        QApplication.processEvents()
        assert not d.isFloating() and d.isVisible()
        assert not act.isChecked()
        assert d.windowTitle() == "Results"

        act.setChecked(True)            # floats again, and back by the ribbon
        QApplication.processEvents()
        assert d.isFloating() and act.isChecked()
        act.setChecked(False)
        QApplication.processEvents()
        assert not d.isFloating()
    finally:
        w._dirty = False
        w.close()
