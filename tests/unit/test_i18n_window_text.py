"""A pt-BR window shows no English UI text, and the generator saw why it did.

The catalogue reported 100 % while the dock titles, the Run panel's labels and
the Results ribbon's threshold row stayed English: the scan read only the
constructors and setters on a whitelist, so `QDockWidget("Inspector")`,
`QFormLayout.addRow("Seed:", ...)`, `(label, widget)` pairs and the labels the
Run panel takes from `raptor.core` were never extracted, and "complete" meant
"every extracted string".  These tests look at the window itself.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QDockWidget, QLabel, QWidget,
)

from raptor.gui import i18n

ROOT = Path(__file__).resolve().parents[2]

#: English chrome that must not survive in a Brazilian Portuguese window.
ENGLISH_UI = (
    "Model Explorer", "Part Library", "Data Tables", "Inspector",
    "Statistics from:", "Stop at:", "Trials:", "Seed:", "Model scope:",
    "System red cost:", "Flow generated:", "Cost of lost flow:",
    "Capacity plot", "Interim cost", "Availability plot",
    "Results to calculate", "Size on disk",
    "Metric", "Green ≥", "Red <", "Minimize ", "Close Run",
    "Float ", "cores", "Whole model",
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pt_window(qapp):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    old = settings.get("appearance/language", "system")
    settings.set_value("startup/show_start_page", False)
    settings.set_value("appearance/language", "pt_BR")
    window = MainWindow()
    window.show()
    qapp.processEvents()
    i18n.translate_widgets(window)
    qapp.processEvents()
    try:
        yield window
    finally:
        window._dirty = False
        window.close()
        settings.set_value("appearance/language", old)
        i18n.install(qapp, "en")


def _texts(window: QWidget) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for widget in window.findChildren(QWidget):
        if widget.property("raptor_i18n_data"):
            continue
        pairs = [("title", widget.windowTitle()), ("tip", widget.toolTip()),
                 ("name", widget.accessibleName())]
        if isinstance(widget, (QLabel, QAbstractButton)):
            pairs.append(("text", widget.text()))
        found += [(kind, text) for kind, text in pairs if text]
    return found


def test_a_portuguese_window_has_no_english_chrome(pt_window):
    texts = _texts(pt_window)
    leftovers = sorted({(kind, text[:70]) for kind, text in texts
                        for english in ENGLISH_UI
                        if english in text and kind != "name"})
    assert not leftovers, leftovers


def test_dock_titles_are_translated_and_keyed_by_a_stable_id(pt_window):
    from raptor.gui.ui.dock_chrome import dock_id
    docks = {dock_id(d): d.windowTitle()
             for d in pt_window.findChildren(QDockWidget)}
    assert docks["Model Explorer"] == "Explorador de modelo"
    assert docks["Part Library"] == "Biblioteca de peças"
    assert docks["Data Tables"] == "Tabelas de dados"
    assert docks["Inspector"] == "Inspetor"
    # the chrome and the compact-layout policy still find every dock by id
    for ident in ("Model Explorer", "Inspector", "Results"):
        assert ident in pt_window._dock_chrome


def test_the_title_bar_buttons_and_stub_follow_the_language(pt_window):
    from raptor.gui.ui.dock_chrome import dock_id
    entry = next(e for d, e in pt_window._dock_chrome_entries.items()
                 if dock_id(d) == "Inspector")
    assert entry["min_btn"].toolTip() == "Minimizar Inspetor"
    assert entry["close_btn"].toolTip() in ("Fechar", "Fechar Inspetor")


def test_the_run_panel_reads_in_portuguese(pt_window):
    labels = {w.text() for w in pt_window._run_panel.findChildren(QLabel)}
    assert "Estatísticas a partir de:" in labels
    assert "Parar em:" in labels
    boxes = {w.text() for w in pt_window._run_panel.findChildren(QAbstractButton)}
    assert "Gráfico de disponibilidade" in boxes
    assert "Gráfico de capacidade (requer Capacidade)" in boxes


def test_status_and_template_sentences_translate(pt_window):
    pt_window._statusbar.showMessage(
        "Loaded: Plant.rbds — 12 blocks, 3 nodes, 0 events, 15 links")
    assert pt_window._statusbar.currentMessage() == (
        "Carregado: Plant.rbds — 12 blocos, 3 nós, 0 eventos, 15 vínculos")
    assert i18n.translate_source("8/8 cores") == "8/8 núcleos"
    assert i18n.translate_source("Minimize Advisor") == "Minimizar Advisor"


def test_a_note_assembled_from_two_sentences_translates_whole(pt_window):
    note = ("Off: the Cost results and the cost charts will be empty.\n\n"
            "Cost totals over time.")
    got = i18n.translate_source(note)
    assert "Desligado" in got and "Totais de custo" in got
    # one sentence without an entry leaves the whole text alone
    odd = "Off: the Cost results and the cost charts will be empty. Nonsense."
    assert i18n.translate_source(odd) == odd


def _generator():
    spec = importlib.util.spec_from_file_location(
        "gen_locale_catalogue", ROOT / "tools" / "gen_locale_catalogue.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_generator_extracts_docks_and_form_rows():
    scanned, _other = _generator().scanned_strings()
    for source in ("Model Explorer", "Part Library", "Inspector",
                   "Statistics from:", "Stop at:", "Mode:"):
        assert source in scanned, source
    swept = _generator().sweep_strings()
    assert "Green ≥" in swept and "Metric" in swept


def test_the_run_panels_core_vocabulary_is_in_the_catalogue():
    sources = set(i18n.dynamic_sources())
    assert "Detailed event log" in sources
    assert "Capacity plot (needs Capacity)" in sources
    assert any(s.startswith("Off: the Cost results") for s in sources)
