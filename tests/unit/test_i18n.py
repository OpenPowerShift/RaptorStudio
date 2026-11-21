"""Qt Linguist catalogue loading and translated ribbon text."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.gui import i18n


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_french_catalogue_is_shipped_and_translates_key_strings(qapp):
    locales = i18n.available_locales()
    assert any(code == "fr" and name.startswith("Français")
               for code, name in locales)
    try:
        assert i18n.install(qapp, "fr") == "fr"
        assert i18n.tr("Run", "Command") == "Exécuter"
        assert i18n.tr("Model", "Page") == "Modèle"
        assert i18n.tr("Data", "Page") == "Données"
        # A missing entry falls back to the source language, never blank.
        assert i18n.tr("Not translated anywhere", "Command") == \
            "Not translated anywhere"
    finally:
        i18n.install(qapp, "en")


def test_an_unknown_locale_falls_back_to_english(qapp):
    try:
        assert i18n.install(qapp, "zz") == "en"
        assert i18n.tr("Run", "Command") == "Run"
    finally:
        i18n.install(qapp, "en")


def test_the_window_builds_its_ribbon_in_the_saved_language(qapp):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    old = settings.get("appearance/language", "system")
    settings.set_value("startup/show_start_page", False)
    settings.set_value("appearance/language", "fr")
    try:
        w = MainWindow()
        try:
            assert w._run_action.text() == "Exécuter"
            assert w._save_action.text() == "Enregistrer"
            assert w._ribbon.tab_bar.tabText(w._model_page_index) == "Modèle"
        finally:
            w._dirty = False
            w.close()
    finally:
        settings.set_value("appearance/language", old)
        i18n.install(qapp, "en")


def test_the_preferences_save_path_writes_the_language(qapp):
    from PySide6.QtWidgets import QComboBox, QDialog
    from raptor.gui.ui import dialogs, settings
    from raptor.gui.ui.main_window import MainWindow
    old = settings.get("appearance/language", "system")
    settings.set_value("startup/show_start_page", False)
    settings.set_value("appearance/language", "system")
    w = MainWindow()
    try:
        def accept_with_french(dlg):
            for combo in dlg.findChildren(QComboBox):
                if combo.findData("fr") >= 0:
                    combo.setCurrentIndex(combo.findData("fr"))
            return QDialog.DialogCode.Accepted
        old_exec = dialogs.exec_dialog
        dialogs.exec_dialog = accept_with_french
        try:
            dialogs.show_preferences(w)
        finally:
            dialogs.exec_dialog = old_exec
        assert settings.get("appearance/language") == "fr"
    finally:
        w._dirty = False
        w.close()
        settings.set_value("appearance/language", old)


def test_french_key_tips_use_stable_page_keys(qapp):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    old = settings.get("appearance/language", "system")
    settings.set_value("startup/show_start_page", False)
    settings.set_value("appearance/language", "fr")
    try:
        w = MainWindow()
        try:
            w._show_tab_tips()
            assert len(w._key_tip_badges) >= 9
            assert w._key_tip_tab_letter("D")
            assert w._key_tip_page == "Data"
            assert w._ribbon.tab_bar.currentIndex() == w._data_page_index
            # No KeyError and the run panel still opens from any tab.
            w._show_run_panel()
            assert w._ribbon.tab_bar.currentIndex() == w._simulate_page_index
        finally:
            w._dirty = False
            w.close()
    finally:
        settings.set_value("appearance/language", old)
        i18n.install(qapp, "en")


def test_the_command_palette_finds_translated_names(qapp):
    from raptor.gui.ui import settings
    from raptor.gui.ui.command_search import CommandSearch
    from raptor.gui.ui.main_window import MainWindow
    old = settings.get("appearance/language", "system")
    settings.set_value("startup/show_start_page", False)
    settings.set_value("appearance/language", "fr")
    try:
        w = MainWindow()
        try:
            search = CommandSearch(w)
            search._fill("exécuter")
            labels = [search.results.item(i).text()
                      for i in range(search.results.count())]
            assert any("Exécuter" in label for label in labels)
        finally:
            w._dirty = False
            w.close()
    finally:
        settings.set_value("appearance/language", old)
        i18n.install(qapp, "en")


def test_the_catalogue_is_generated_from_the_ribbon_and_fresh():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, str(root / "tools" / "gen_locale_catalogue.py"),
         str(root / "raptor" / "gui" / "locale" / "raptor_fr.ts"), "--check"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_the_installer_bundles_the_catalogue():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    spec = (root / "installer" / "raptor.spec").read_text()
    assert 'raptor" / "gui" / "locale"' in spec


def test_qt_standard_buttons_follow_the_language(qapp):
    from PySide6.QtWidgets import QDialogButtonBox
    try:
        i18n.install(qapp, "fr")
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        texts = [button.text() for button in box.buttons()]
        assert "Annuler" in texts
    finally:
        i18n.install(qapp, "en")


def test_the_cli_writes_the_same_settings_file_as_the_gui(monkeypatch, tmp_path):
    from raptor.cli.main import main
    from raptor.gui.ui import settings
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    old = settings.get("appearance/language", "system")
    try:
        assert main(["config", "set", "appearance/language", "fr"]) == 0
        assert settings.get("appearance/language") == "fr"
    finally:
        settings.set_value("appearance/language", old)
