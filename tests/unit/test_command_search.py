"""Command search lists every runnable command once (WP-1.6)."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from pathlib import Path

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.command_table import COMMANDS
from raptor.gui.ui.command_search import CommandSearch
from raptor.gui.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def win(qapp):
    window = MainWindow()
    gold = Path(__file__).resolve().parents[1] / "golden"
    window._load_into_app(load_rbd(str(gold / "TwoBlockRedundancy/input.rbd")),
                          "x")
    return window


def test_every_command_with_a_handler_appears_once(win):
    dialog = CommandSearch(win)
    ids = [dialog.results.item(i).data(256)
           for i in range(dialog.results.count())]
    commands = [value for value in ids if isinstance(value, str)]
    expected = [spec.id for spec in COMMANDS if spec.handler]
    assert len(commands) == len(set(commands))
    assert set(commands) == set(expected)
    assert any(isinstance(value, tuple) and value[0] == "element"
               for value in ids)


def test_filter_finds_duplicate_and_disabled_commands_explain(win):
    dialog = CommandSearch(win)
    dialog.query.setText("duplicate")
    ids = [dialog.results.item(i).data(256)
           for i in range(dialog.results.count())]
    assert "home.duplicate" in ids
    disabled = [dialog.results.item(i) for i in range(dialog.results.count())
                if not (dialog.results.item(i).flags()
                        & Qt.ItemFlag.ItemIsEnabled)]
    for item in disabled:
        assert "(" in item.text() and ")" in item.text()


def test_arrow_keys_move_the_list_while_typing(win, qapp):
    """U19: Up and Down in the query box did nothing."""
    from PySide6.QtGui import QPalette
    from PySide6.QtTest import QTest

    dialog = CommandSearch(win)
    dialog.query.setText("a")
    assert dialog.results.currentRow() == 0
    QTest.keyClick(dialog.query, Qt.Key.Key_Down)
    first_move = dialog.results.currentRow()
    assert first_move > 0
    assert dialog.results.item(first_move).flags() & Qt.ItemFlag.ItemIsEnabled
    QTest.keyClick(dialog.query, Qt.Key.Key_Up)
    assert dialog.results.currentRow() < first_move
    assert dialog.query.text() == "a"     # the keys went to the list, not the text
    palette = dialog.results.palette()
    assert (palette.color(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight)
            == palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight))


def test_one_export_window_picks_the_writer_by_file_type():
    """U22: five Export commands each opened their own save window."""
    from raptor.gui.ui.export_mixin import ExportMixin, export_choice

    types = ExportMixin.EXPORT_TYPES
    assert export_choice(types, "/x/r.xlsx", types[0][0]) == ("_export_workbook_xlsx", ".xlsx")
    assert export_choice(types, "/x/r", "Diagram as SVG (*.svg)") == ("_export_diagram_svg", ".svg")
    assert export_choice(types, "/x/r", "") == ("_export_pdf", ".pdf")


def test_the_export_window_writes_the_chosen_type(win, tmp_path, monkeypatch):
    from raptor.gui.ui import filedialogs as fdg

    target = tmp_path / "diagram"
    monkeypatch.setattr(fdg, "getSaveFileName",
                        lambda *a, **k: (str(target), "Diagram as SVG (*.svg)"))
    win._export_any()
    assert (tmp_path / "diagram.svg").read_text().lstrip().startswith("<?xml")
