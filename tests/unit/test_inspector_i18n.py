"""The Inspector in Brazilian Portuguese: translated after every rebuild,
fitted to the dock, and put back to English exactly."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QGroupBox, QLabel, QScrollArea,
)

from raptor.core.fileio.model_file import load_model_file
from raptor.core.fileio.typed import block_from_row, node_from_row
from raptor.gui import i18n
from raptor.gui.resources.design_tokens import ThemeManager
from raptor.gui.ui.inspector import InspectorDock
from raptor.gui.ui.inspector_common import WrappingCheckBox

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "new_docs" / "models" / "DC1.rbds"
NARROW = 340          # the dock's minimum width at 100%


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    ThemeManager.instance().set_dark(False)
    ThemeManager.instance().set_scale(100)


@pytest.fixture
def pt(qapp):
    i18n.install(qapp, "pt_BR")
    try:
        yield
    finally:
        i18n.install(qapp, "en")


@pytest.fixture
def dock(qapp):
    rbd = load_model_file(str(MODEL))
    widget = InspectorDock()
    widget.set_rbd(rbd)
    widget.resize(NARROW, 900)
    widget.show()
    widget._test_rbd = rbd          # keep it alive
    yield widget
    widget.close()


def _block_id(dock: InspectorDock) -> int:
    rows = dock._rbd.sections["BLOCK_DATA"].rows
    return next(b.id for b in map(block_from_row, rows) if b.id)


def _show_block(dock: InspectorDock) -> None:
    dock.show_block(_block_id(dock), None)
    dock.ensure_all_tabs_built()
    QApplication.processEvents()


def _texts(dock: InspectorDock) -> dict[int, str]:
    """Every label, button and group title currently in the dock."""
    out = {}
    for widget in dock.findChildren(QLabel):
        out[id(widget)] = widget.text()
    for widget in dock.findChildren(QAbstractButton):
        out[id(widget)] = widget.text()
    for widget in dock.findChildren(QGroupBox):
        out[id(widget)] = widget.title()
    return out


def _all_text(dock: InspectorDock) -> str:
    return "\n".join(_texts(dock).values())


def test_a_block_is_in_portuguese_after_selection_and_english_otherwise(
        qapp, dock):
    _show_block(dock)
    english = _all_text(dock)
    assert "Failure Mean (hours):" in english

    i18n.install(qapp, "pt_BR")
    try:
        _show_block(dock)           # a rebuild, with no window-wide pass
        text = _all_text(dock)
        for expected in ("Bloco: ", "Tipo:", "Média de falha (horas):",
                         "Localização de falha (horas):",
                         "Estresse de espera:", "Restauração:",
                         "Distribuição de falha", "Identificação",
                         "Estimar pela experiência"):
            assert expected in text, expected
        assert "Failure Mean" not in text
        assert dock._tabs.tabText(1) == "✕ Falha"
        assert dock._tabs.tabToolTip(1).startswith("Distribuição de falha")
    finally:
        i18n.install(qapp, "en")


def test_every_inspector_kind_translates_on_rebuild(qapp, dock, pt):
    nodes = [node_from_row(r) for r in dock._rbd.sections["NODE_DATA"].rows]
    dock.show_node(nodes[0].id, None)
    dock.ensure_all_tabs_built()
    QApplication.processEvents()
    assert _all_text(dock).count("Nó: ") == 1
    assert "Fluxo de entrada" in _all_text(dock)
    dock.show_system_config()
    QApplication.processEvents()
    assert "Configurações da simulação" in _all_text(dock)
    assert dock._header.text() == "Configurações do projeto"
    dock.clear_selection()
    assert "Selecione um bloco" in dock._empty.text()


def test_no_page_overflows_the_dock_in_either_language(qapp, dock):
    for locale in ("en", "pt_BR"):
        i18n.install(qapp, locale)
        try:
            _show_block(dock)
            for index in range(dock._tabs.count()):
                if not dock._tabs.isTabVisible(index):
                    continue
                dock._tabs.setCurrentIndex(index)
                QApplication.processEvents()
                scroll = dock._tabs.widget(index)
                assert isinstance(scroll, QScrollArea)
                assert scroll.horizontalScrollBar().maximum() == 0, (
                    locale, dock._tabs.tabText(index))
                # No label is cut: one that cannot wrap fits its text.
                for label in scroll.widget().findChildren(QLabel):
                    if label.isVisibleTo(scroll) and not label.wordWrap() \
                            and label.text():
                        assert label.sizeHint().width() <= label.width() + 1, (
                            locale, label.text())
        finally:
            i18n.install(qapp, "en")


def test_the_dock_stays_fitted_at_150_percent_and_dark(qapp, dock):
    theme = ThemeManager.instance()
    i18n.install(qapp, "pt_BR")
    try:
        theme.set_dark(True)
        theme.set_scale(150)
        dock.resize(int(NARROW * 1.5), 1200)
        _show_block(dock)
        for index in range(dock._tabs.count()):
            if dock._tabs.isTabVisible(index):
                dock._tabs.setCurrentIndex(index)
                QApplication.processEvents()
                bar = dock._tabs.widget(index).horizontalScrollBar()
                assert bar.maximum() == 0, dock._tabs.tabText(index)
    finally:
        theme.set_scale(100)
        theme.set_dark(False)
        i18n.install(qapp, "en")


def test_switching_language_at_runtime_and_back_restores_english_exactly(
        qapp, dock):
    _show_block(dock)
    nodes = [node_from_row(r) for r in dock._rbd.sections["NODE_DATA"].rows]
    for show in (lambda: _show_block(dock),
                 lambda: dock.show_node(nodes[0].id, None),
                 lambda: dock.show_system_config()):
        show()
        dock.ensure_all_tabs_built()
        before = _texts(dock)
        tabs_before = [(dock._tabs.tabText(i), dock._tabs.tabToolTip(i))
                       for i in range(dock._tabs.count())]
        header = dock._header.text()
        i18n.install(qapp, "pt_BR")
        try:
            i18n.translate_widgets(dock)          # what the window does
            assert _texts(dock) != before
            assert dock._header.text() != header or header == ""
        finally:
            i18n.install(qapp, "en")
        i18n.translate_widgets(dock)
        assert _texts(dock) == before
        assert [(dock._tabs.tabText(i), dock._tabs.tabToolTip(i))
                for i in range(dock._tabs.count())] == tabs_before
        assert dock._header.text() == header


def test_a_rebuild_adds_little_time(qapp, dock):
    """The pass runs on every click; it must stay cheap."""
    def run(locale: str) -> float:
        i18n.install(qapp, locale)
        try:
            _show_block(dock)
            dock._user_tab_index = 1
            samples = []
            for _ in range(8):
                start = time.perf_counter()
                dock.show_block(_block_id(dock), None)
                samples.append(time.perf_counter() - start)
            return sorted(samples)[len(samples) // 2]
        finally:
            i18n.install(qapp, "en")
    assert run("pt_BR") - run("en") < 0.05      # measured: about 1 ms


@pytest.mark.parametrize("source, expected", [
    ("Failure Mean (hours):", "Média de falha (horas):"),
    ("Repair Standard Dev (hours):", "Desvio padrão de reparo (horas):"),
    ("Failure Rate (per hours):", "Taxa de falha (por horas):"),
    ("Failure Mu (ln(hours)):", "μ de falha (ln(horas)):"),
    ("Failure Shape", "Forma de falha"),
    ("4380 (hours)", "4380 (horas)"),
    ("Block: Pump  (ID 3)", "Bloco: Pump  (ID 3)"),
    ("Annotation: Rectangle  (ID 3)", "Anotação: Retângulo  (ID 3)"),
    ("Failure Mean must be greater than 0",
     "Média de falha deve ser maior que 0"),
])
def test_text_built_at_run_time_is_translated_from_the_catalogue(
        pt, source, expected):
    assert i18n.translate_source(source) == expected


def test_model_data_that_looks_like_a_phrase_is_left_alone(pt):
    # Names, whatever words they happen to contain.
    for name in ("Failure Pump", "PM Pump", "Block: Mean (x)", "Mean pump"):
        assert i18n.translate_source(name) == name
    assert i18n.translate_source("Block: Mean  (ID 3)") == \
        "Bloco: Mean  (ID 3)"


def test_english_is_the_identity(qapp):
    i18n.install(qapp, "en")
    assert i18n.translate_source("Failure Mean (hours):") == \
        "Failure Mean (hours):"


def test_the_first_entry_of_a_names_combo_translates_but_stays_english_for_the_model(
        qapp, dock):
    """"(none)" is chrome; the pool names after it are data."""
    i18n.install(qapp, "pt_BR")
    try:
        dock.show_block(_block_id(dock), None)
        dock.ensure_all_tabs_built()
        combos = [c for c in dock.findChildren(QComboBox)
                  if c.property("raptor_i18n_data_items")
                  and c.itemData(0) == ""]
        assert combos
        for combo in combos:
            assert combo.itemText(0) == "(nenhum)"
            assert all(combo.itemText(i) == combo.itemData(i)
                       for i in range(1, combo.count()))
        emitted: list = []
        dock.pool_name_changed.connect(lambda _b, name: emitted.append(name))
        pool = next(c for c in combos
                    if c.toolTip().startswith("Pool de sobressalentes"))
        pool.setCurrentIndex(0)
        pool.activated.emit(0)
        assert emitted == ["(none)"]       # what the model has always got
    finally:
        i18n.install(qapp, "en")


def test_the_units_combo_keeps_the_models_own_words(qapp, dock):
    i18n.install(qapp, "pt_BR")
    try:
        dock.show_system_config()
        units = next(c for c in dock.findChildren(QComboBox)
                     if c.isEditable())
        assert "hours" in [units.itemText(i) for i in range(units.count())]
    finally:
        i18n.install(qapp, "en")


def test_a_checkbox_wraps_instead_of_forcing_the_dock_wider(qapp):
    box = WrappingCheckBox("Include node analysis information for this node "
                           "in the output tables and reports")
    box.show()
    assert box.minimumSizeHint().width() < 120
    assert box.sizeHint().width() > 300
    assert box.heightForWidth(150) > box.heightForWidth(600)
    assert box.text().startswith("Include node")      # still a real checkbox
    box.close()


def _costs_texts(dock):
    page = dock._scroll_costs.widget()
    labels = [lbl.text() for lbl in page.findChildren(QLabel)]
    boxes = [b.text() for b in page.findChildren(QAbstractButton)]
    return labels, boxes


def test_costs_tab_is_fully_translated_and_keeps_units_together(qapp, pt, dock):
    _show_block(dock)
    labels, boxes = _costs_texts(dock)
    assert "Sempre adicionar custo de conclusão ao final da execução" in boxes
    assert not any("Done" in b for b in boxes)
    units = [t for t in labels if "(/" in t]
    assert units
    for text in units:
        # no break opportunity between "/" and the unit word
        assert "(/⁠tempo)" in text, text


def test_costs_unit_labels_are_english_again_in_english(qapp, dock):
    _show_block(dock)
    labels, _boxes = _costs_texts(dock)
    assert any(t.startswith("Running (/") for t in labels)


def test_empty_inspector_heading_and_results_banner_translate(qapp, dock):
    i18n.install(qapp, "pt_BR")
    try:
        _show_block(dock)
        dock.clear_selection()
        assert dock._header.text() == "Nenhuma seleção"
        dock.set_results_notice(True)
        assert dock._lock_text.text().startswith("Há resultados na tela")
    finally:
        i18n.install(qapp, "en")
    dock.clear_selection()
    assert dock._header.text() == "No selection"
    dock.set_results_notice(False)
    dock.set_results_notice(True)
    assert dock._lock_text.text().startswith("Results are on screen")


def test_canvas_overlay_combo_is_translated():
    i18n.install(QApplication.instance() or QApplication([]), "pt_BR")
    try:
        for source, want in (("No canvas overlay", "Sem sobreposição no diagrama"),
                             ("Share of downtime", "Parcela da inatividade"),
                             ("Advisor findings", "Constatações do Advisor")):
            assert i18n.translate_source(source) == want
    finally:
        i18n.install(QApplication.instance(), "en")
