"""Command search lists every runnable command once (WP-1.6)."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from raptor.gui.command_table import COMMANDS
from raptor.gui.ui.command_search import CommandSearch
from raptor.gui.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def win(qapp):
    return MainWindow()


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
