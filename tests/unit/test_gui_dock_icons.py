"""Every dock tab carries its ribbon glyph in the tab and title bar.

Dock tabs are the one navigation surface that had text only: Explorer,
Inspector, Failure Effects, Results, Data Tables, Run and Live Log all
looked alike. Each dock now sets the same painted icon as its ribbon
command; QMainWindow's tab bar reads `windowIcon`, and the custom
minimise titlebars draw that same icon themselves.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_every_dock_has_its_ribbon_icon(qapp):
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1500, 900)
    win.show()
    qapp.processEvents()
    try:
        docks = (
            win._explorer_dock, win._inspector_dock,
            win._fev_inspector_dock, win._live_log_dock,
            win._results_dock, win._data_tables_dock, win._run_dock,
        )
        for dock in docks:
            assert not dock.windowIcon().isNull(), dock.objectName()
        # The custom minimise titlebars must paint that same icon, not
        # just keep it in the window property.
        from PySide6.QtWidgets import QLabel
        for dock in (win._explorer_dock, win._inspector_dock,
                     win._results_dock, win._data_tables_dock,
                     win._run_dock):
            titlebar = dock.titleBarWidget()
            labels = titlebar.findChildren(QLabel) if titlebar else []
            assert any(not label.pixmap().isNull() for label in labels), \
                dock.objectName()
    finally:
        win.close()


def test_every_dock_tab_shows_an_icon(qapp):
    from PySide6.QtWidgets import QTabBar
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1500, 900)
    win.show()
    qapp.processEvents()
    try:
        # QTabWidget-owned bars are the Inspector/Results inner tabs, and
        # the ribbon's own page strip is not a dock tab either.
        ribbon_bar = win._ribbon.tab_bar
        bars = [bar for bar in win.findChildren(QTabBar)
                if bar.objectName() != "qt_tabwidget_tabbar"
                and bar is not ribbon_bar and bar.count()]
        assert bars
        for bar in bars:
            for index in range(bar.count()):
                assert not bar.tabIcon(index).isNull(), bar.tabText(index)
    finally:
        win.close()
