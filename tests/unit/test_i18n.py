"""Qt Linguist catalogue loading, full-GUI coverage and translated chrome."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QLabel, QPushButton, QTabWidget,
)

from raptor.gui import i18n

ROOT = Path(__file__).resolve().parents[2]
LOCALE = ROOT / "raptor" / "gui" / "locale"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pt(qapp):
    """Install Brazilian Portuguese for one test, then restore English."""
    i18n.install(qapp, "pt_BR")
    try:
        yield
    finally:
        i18n.install(qapp, "en")


def test_the_brazilian_catalogue_is_shipped_and_translates_key_strings(
        qapp, pt):
    locales = dict(i18n.available_locales())
    assert "pt_BR" in locales
    assert locales["pt_BR"].startswith("Português (Brasil)")
    assert i18n.current_locale() == "pt_BR"
    assert i18n.tr("Run", "Command") == "Executar"
    assert i18n.tr("Model", "Page") == "Modelo"
    assert i18n.tr("Data", "Page") == "Dados"
    assert i18n.tr("Preferences", "Gui") == "Preferências"
    assert i18n.tr("Open", "Command") == "Abrir"
    # A missing entry falls back to the source language, never blank.
    assert i18n.tr("Not translated anywhere", "Command") == \
        "Not translated anywhere"


def test_french_is_gone_and_only_catalogues_with_sources_are_offered():
    assert not list(LOCALE.glob("raptor_fr.*"))
    assert (LOCALE / "raptor_pt_BR.ts").is_file()
    assert (LOCALE / "raptor_pt_BR.qm").is_file()
    codes = [code for code, _name in i18n.available_locales()]
    assert codes == ["en", "pt_BR"]


def test_a_bare_portuguese_locale_uses_the_brazilian_catalogue(qapp):
    try:
        assert i18n.install(qapp, "pt") == "pt_BR"
        assert i18n.tr("Run", "Command") == "Executar"
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
    settings.set_value("appearance/language", "pt_BR")
    try:
        w = MainWindow()
        try:
            assert w._run_action.text() == "Executar"
            assert w._save_action.text() == "Salvar"
            assert w._ribbon.tab_bar.tabText(w._model_page_index) == "Modelo"
            assert w._ribbon.tab_bar.tabText(w._data_page_index) == "Dados"
        finally:
            w._dirty = False
            w.close()
    finally:
        settings.set_value("appearance/language", old)
        i18n.install(qapp, "en")


def test_the_widget_tree_translator_is_idempotent_and_restores_english(qapp):
    try:
        i18n.install(qapp, "pt_BR")
        dlg = QDialog()
        label = QLabel("Recent files", dlg)
        button = QPushButton("Open", dlg)
        tabs = QTabWidget(dlg)
        tabs.addTab(QLabel("page"), "Overview")
        combo = QComboBox(dlg)
        combo.addItem("hours")
        dlg.setWindowTitle("Preferences")
        i18n.translate_widgets(dlg)
        assert label.text() == "Arquivos recentes"
        assert button.text() == "Abrir"
        assert tabs.tabText(0) == "Visão geral"
        assert combo.itemText(0) == "horas"
        assert dlg.windowTitle() == "Preferências"
        # Running it again in the same language must not translate a
        # translation ("Arquivos recentes" has no catalogue entry).
        i18n.translate_widgets(dlg)
        assert label.text() == "Arquivos recentes"
        # Switching back restores the stored English source.
        i18n.install(qapp, "en")
        i18n.translate_widgets(dlg)
        assert label.text() == "Recent files"
        assert tabs.tabText(0) == "Overview"
    finally:
        i18n.install(qapp, "en")


def test_the_preferences_save_path_writes_the_language(qapp):
    from PySide6.QtWidgets import QDialog
    from raptor.gui.ui import dialogs, settings
    from raptor.gui.ui.main_window import MainWindow
    old = settings.get("appearance/language", "system")
    settings.set_value("startup/show_start_page", False)
    settings.set_value("appearance/language", "system")
    w = MainWindow()
    try:
        def accept_with_portuguese(dlg):
            for combo in dlg.findChildren(QComboBox):
                if combo.findData("pt_BR") >= 0:
                    combo.setCurrentIndex(combo.findData("pt_BR"))
            return QDialog.DialogCode.Accepted
        old_exec = dialogs.exec_dialog
        dialogs.exec_dialog = accept_with_portuguese
        try:
            dialogs.show_preferences(w)
        finally:
            dialogs.exec_dialog = old_exec
        assert settings.get("appearance/language") == "pt_BR"
    finally:
        w._dirty = False
        w.close()
        settings.set_value("appearance/language", old)


def test_portuguese_key_tips_keep_the_stable_page_keys(qapp):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    old = settings.get("appearance/language", "system")
    settings.set_value("startup/show_start_page", False)
    settings.set_value("appearance/language", "pt_BR")
    try:
        w = MainWindow()
        try:
            w._show_tab_tips()
            assert len(w._key_tip_badges) >= 9
            assert w._key_tip_tab_letter("D")
            assert w._key_tip_page == "Data"       # stable, not "Dados"
            assert w._ribbon.tab_bar.currentIndex() == w._data_page_index
            w._show_run_panel()
            assert w._ribbon.tab_bar.currentIndex() == w._simulate_page_index
        finally:
            w._dirty = False
            w.close()
    finally:
        settings.set_value("appearance/language", old)
        i18n.install(qapp, "en")


def test_the_command_palette_finds_translated_names_and_keywords(qapp):
    from raptor.gui.ui import settings
    from raptor.gui.ui.command_search import CommandSearch
    from raptor.gui.ui.main_window import MainWindow
    old = settings.get("appearance/language", "system")
    settings.set_value("startup/show_start_page", False)
    settings.set_value("appearance/language", "pt_BR")
    try:
        w = MainWindow()
        try:
            search = CommandSearch(w)
            search._fill("executar")
            labels = [search.results.item(i).text()
                      for i in range(search.results.count())]
            assert any("Executar" in label for label in labels)
            assert "Executar um comando" in search.windowTitle()
        finally:
            w._dirty = False
            w.close()
    finally:
        settings.set_value("appearance/language", old)
        i18n.install(qapp, "en")


def test_the_catalogue_is_generated_from_data_and_source_and_fresh():
    import subprocess
    import sys
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_locale_catalogue.py"),
         str(LOCALE / "raptor_pt_BR.ts"), "--check"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_the_catalogue_covers_data_driven_and_widget_contexts():
    import xml.etree.ElementTree as ET
    root = ET.parse(LOCALE / "raptor_pt_BR.ts").getroot()
    by_context = {
        context.findtext("name"): {
            message.findtext("source")
            for message in context.findall("message")
        }
        for context in root.findall("context")
    }
    assert {"Command", "Page", "Group", "Gui"} <= set(by_context)
    assert "Executar" in by_context["Command"] or \
        "Run" in by_context["Command"]
    assert "Preferences" in by_context["Gui"]
    assert "Overview" in by_context["Gui"]


def test_coverage_is_reported_and_never_claims_a_partial_file_is_complete(
        qapp, monkeypatch):
    report = {entry["locale"]: entry for entry in i18n.coverage_report()}
    assert report["pt_BR"]["total"] > 1000
    assert report["pt_BR"]["complete"] is True
    assert report["pt_BR"]["percent"] == 100.0

    # The label says how much is covered when a catalogue is partial.
    monkeypatch.setattr(i18n, "coverage_report", lambda: [{
        "locale": "pt_BR", "translated": 1, "total": 2,
        "percent": 50.0, "complete": False}])
    labels = dict(i18n.available_locales())
    assert "1 of 2 strings (50.0%)" in labels["pt_BR"]


def test_the_installer_bundles_both_catalogues():
    spec = (ROOT / "installer" / "raptor.spec").read_text()
    assert 'raptor" / "gui" / "locale"' in spec
    assert "qtbase_*.qm" in spec


def test_qt_standard_buttons_follow_the_language(qapp, pt):
    from PySide6.QtWidgets import QDialogButtonBox
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                           | QDialogButtonBox.StandardButton.Cancel)
    texts = [button.text() for button in box.buttons()]
    assert "Cancelar" in texts


def test_status_messages_translate_when_they_have_a_catalogue_entry(qapp, pt):
    bar = i18n.TranslatingStatusBar()
    bar.showMessage("Ready — Open an .rbd or .rbds file to begin (Ctrl+O)")
    assert bar.currentMessage().startswith("Pronto")


def test_the_cli_writes_the_same_settings_file_as_the_gui(monkeypatch, tmp_path):
    from raptor.cli.main import main
    from raptor.gui.ui import settings
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    old = settings.get("appearance/language", "system")
    try:
        assert main(["config", "set", "appearance/language", "pt_BR"]) == 0
        assert settings.get("appearance/language") == "pt_BR"
    finally:
        settings.set_value("appearance/language", old)


def test_table_headers_and_accessible_names_translate(qapp, pt):
    from PySide6.QtWidgets import QTableWidget
    table = QTableWidget(1, 2)
    table.setHorizontalHeaderLabels(["Name", "Block"])
    table.setAccessibleName("Live event log")
    table.setAccessibleDescription("Run shown in the live log")
    i18n.translate_widgets(table)
    assert table.horizontalHeaderItem(0).text() == "Nome"
    assert table.horizontalHeaderItem(1).text() == "Bloco"
    assert table.accessibleName() == "Registro de eventos ao vivo"
    assert table.accessibleDescription() == (
        "Execução mostrada no registro ao vivo")
    # The header pass owns the source, so English comes back on a switch.
    i18n.install(qapp, "en")
    i18n.translate_widgets(table)
    assert table.horizontalHeaderItem(0).text() == "Name"
    assert table.horizontalHeaderItem(1).text() == "Block"
    i18n.install(qapp, "pt_BR")


def test_a_widget_added_after_the_first_pass_is_translated_by_the_next(
        qapp, pt):
    """Late content is translated where it is built: the results dock and
    the Drivers page call `translate_widgets` once their pages exist."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    window = QWidget()
    layout = QVBoxLayout(window)
    i18n.translate_widgets(window)
    label = QLabel("Recent files", window)
    layout.addWidget(label)
    i18n.translate_widgets(window)      # the late-build funnel's second pass
    try:
        assert label.text() == "Arquivos recentes"
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_every_report_column_header_the_gui_builds_has_a_translation():
    """The report's own columns become table headers via `humanise`."""
    import xml.etree.ElementTree as ET
    from raptor.api.presentation import humanise
    golden = ROOT / "tests" / "golden"
    labels: set[str] = set()
    from raptor.api.tables import parse_final_sections
    for path in sorted(golden.glob("*/ours/rpt_final_results_ours.txt")):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for _title, columns, _rows in parse_final_sections(lines):
            labels.update(humanise(c) for c in columns if c)
    assert labels, "no golden report tables found"
    catalogue = {}
    root = ET.parse(LOCALE / "raptor_pt_BR.ts").getroot()
    for context in root.findall("context"):
        for message in context.findall("message"):
            translation = message.find("translation")
            catalogue[message.findtext("source") or ""] = (
                translation.text if translation is not None else "") or ""
    missing = sorted(label for label in labels
                     if not catalogue.get(label, "").strip())
    assert not missing, f"report headers without a translation: {missing}"


def test_model_data_in_a_combo_is_never_translated(qapp, pt):
    """A block called "Run" must keep its name in a model-data combo."""
    from PySide6.QtWidgets import QComboBox
    combo = QComboBox()
    combo.setProperty("raptor_ignore_i18n", True)
    combo.addItems(["Run", "Total", "Idle"])
    i18n.translate_widgets(combo)
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "Run", "Total", "Idle"]


def test_a_data_items_combo_keeps_its_names_and_translates_its_tooltip(
        qapp, pt):
    """`raptor_i18n_data_items` leaves the entries alone, not the chrome."""
    combo = QComboBox()
    combo.setProperty("raptor_i18n_data_items", True)
    combo.setToolTip("Run")
    combo.addItems(["Run", "B"])
    i18n.translate_widgets(combo)
    assert [combo.itemText(i) for i in range(combo.count())] == ["Run", "B"]
    assert combo.toolTip() == i18n.tr("Run", i18n.WIDGET_CONTEXT)


@pytest.mark.parametrize("locale", ["en", "pt_BR"])
def test_refilled_combos_and_rebuilt_tabs_are_not_put_back(qapp, locale):
    """A combo refilled, or a tab retitled, after a pass keeps the new text.

    The Results dock rebuilds its Details tabs for every result set and
    translates the dock again: the pass used to restore the first titles it
    ever saw by position, so a second model's "Resources" tab read
    "Extended results" and every later tab was off by one.
    """
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    i18n.install(qapp, locale)
    try:
        root = QWidget()
        lay = QVBoxLayout(root)
        combo = QComboBox()
        combo.addItems(["Summary", "Sparing"])
        tabs = QTabWidget()
        tabs.addTab(QLabel("a"), "Summary")
        tabs.addTab(QLabel("b"), "Sparing")
        lay.addWidget(combo)
        lay.addWidget(tabs)
        i18n.translate_widgets(root)
        combo.clear()
        combo.addItems(["Summary", "Resources", "Sparing"])
        tabs.setTabText(1, "Resources")
        tabs.addTab(QLabel("c"), "Sparing")
        i18n.translate_widgets(root)
        tr = (lambda t: i18n.tr(t, i18n.WIDGET_CONTEXT)) if locale != "en" \
            else (lambda t: t)
        expected = [tr("Summary"), tr("Resources"), tr("Sparing")]
        assert [combo.itemText(i) for i in range(combo.count())] == expected
        assert [tabs.tabText(i) for i in range(tabs.count())] == expected
        i18n.install(qapp, "en")
        i18n.translate_widgets(root)
        assert [tabs.tabText(i) for i in range(tabs.count())] == [
            "Summary", "Resources", "Sparing"]
    finally:
        i18n.install(qapp, "en")


def test_templates_do_not_rewrite_model_data(qapp, pt):
    """A label the app wrote translates; one showing a user's name does not."""
    app_label = QLabel("Phase 2")
    data_label = QLabel("Phase 2")
    data_label.setProperty("raptor_i18n_data", True)
    named = QLabel("Phase Alpha")          # not a number: not the app's words
    i18n.translate_widgets(app_label)
    i18n.translate_widgets(data_label)
    i18n.translate_widgets(named)
    assert app_label.text() == "Fase 2"
    assert data_label.text() == "Phase 2"
    assert named.text() == "Phase Alpha"


def test_a_data_spin_suffix_is_not_a_template(qapp, pt):
    from PySide6.QtWidgets import QSpinBox
    app_spin, data_spin = QSpinBox(), QSpinBox()
    app_spin.setSuffix(" of 4")
    data_spin.setSuffix(" of 4")
    data_spin.setProperty("raptor_i18n_data", True)
    i18n.translate_widgets(app_spin)
    i18n.translate_widgets(data_spin)
    assert app_spin.suffix() != " of 4"
    assert data_spin.suffix() == " of 4"
