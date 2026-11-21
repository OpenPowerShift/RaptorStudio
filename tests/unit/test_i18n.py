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
    assert ("fr", "Français") in i18n.available_locales()
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
