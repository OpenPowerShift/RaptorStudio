"""Charts tab: state that must survive a re-run, axis titles that must fit,
one dot size across HOPs rows, and the GUI-language chart text."""
from __future__ import annotations

import os
import re

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication

from raptor.api.insight import charts
from raptor.api.insight.axes import fit_dots, fit_dots_shared
from raptor.gui.ui.chart_widget import fit_axis_title
from raptor.gui.ui.charts_page import ChartsPage
from raptor.gui.ui.results import ResultsDock
from tests.unit.test_charts_page import _hierarchy_inputs
from tests.unit.test_hierarchy_hops import _attribution


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _filled(page: ChartsPage) -> None:
    per_run, attribution, importance = _hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")


def _dock_with_page(qapp) -> ResultsDock:
    dock = ResultsDock()
    _filled(dock._charts_page)
    return dock


# -- 1 and 2: a re-run of the same model keeps the baseline and the picks ---

def test_a_rerun_keeps_the_baseline_and_the_compare_picks(qapp):
    dock = _dock_with_page(qapp)
    page = dock._charts_page
    page._set_baseline()
    page.set_compare([200, 100])
    assert page.baseline_button.text() == "Baseline: set"
    rings: list[list] = []
    page.compare_changed.connect(rings.append)

    dock.clear(keep_chart_state=True)           # what a re-run does first
    assert page.baseline_button.text() == "Baseline: set"
    assert page.clear_baseline_action.isEnabled()
    assert page.selected_hierarchies() == [200, 100]
    assert rings == []                          # no flicker to "top 4"

    _filled(page)                               # the new results arrive
    assert page.selected_hierarchies() == [200, 100]
    assert rings[-1] == [200, 100]              # the canvas rings re-apply
    assert page._baseline_rows


def test_a_rerun_drops_picks_for_a_hierarchy_that_no_longer_exists(qapp):
    dock = _dock_with_page(qapp)
    page = dock._charts_page
    page.set_compare([100, 200])
    dock.clear(keep_chart_state=True)
    per_run, attribution, importance = _hierarchy_inputs()
    attribution = {**attribution, "hierarchies": [
        row for row in attribution["hierarchies"] if row["id"] == 100]}
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_2")
    assert page.selected_hierarchies() == [100]


def test_a_plain_clear_still_forgets_them_for_another_model(qapp):
    dock = _dock_with_page(qapp)
    page = dock._charts_page
    page._set_baseline()
    page.set_compare([100])
    seen: list[list] = []
    page.compare_changed.connect(seen.append)
    dock.clear()                                # model opened or closed
    assert page.baseline_button.text() == "Baseline: none"
    assert page._baseline_rows is None
    assert page.selected_hierarchies() == []
    assert seen[-1] == []


def test_the_run_paths_clear_with_chart_state_kept():
    """Foreground, background, step and discard-and-edit runs all keep it;
    opening a model (file_io) does not."""
    root = os.path.join(os.path.dirname(__file__), "..", "..", "raptor", "gui", "ui")
    for name in ("run_mixin.py", "background_run_mixin.py", "step_mixin.py",
                 "edit_guard_mixin.py"):
        text = open(os.path.join(root, name), encoding="utf-8").read()
        assert "_results.clear(keep_chart_state=True)" in text, name
    text = open(os.path.join(root, "file_io_mixin.py"), encoding="utf-8").read()
    assert "keep_chart_state" not in text


# -- 3: the x title is never clipped from both ends ---------------------------

def test_a_long_x_title_drops_the_zoom_hint_then_elides(qapp):
    fm = QFontMetrics(QFont())
    title = "simulated time (hours) · drag across or Ctrl+wheel to zoom in"
    assert fit_axis_title(fm, title, 2000) == title
    plain = "simulated time (hours)"
    width = fm.horizontalAdvance(plain) + 4
    assert fit_axis_title(fm, title, width) == plain       # hint goes first
    narrow = fit_axis_title(fm, title, 60)
    assert narrow.endswith("…") and not narrow.startswith("…")
    assert fm.horizontalAdvance(narrow) <= 60


# -- 4: one dot size for every HOPs row ---------------------------------------

def test_shared_dots_use_the_smallest_fit_for_every_row():
    piled = [10.0] * 40
    spread = [float(i) * 30 for i in range(5)]
    alone_piled = fit_dots(piled, 20.0, largest=10.0)[0]
    alone_spread = fit_dots(spread, 20.0, largest=10.0)[0]
    assert alone_piled < alone_spread                      # the old behaviour
    diameter, levels = fit_dots_shared([piled, spread], 20.0, largest=10.0)
    assert diameter == alone_piled
    assert len(levels) == 2 and len(levels[1]) == len(spread)


def test_svg_hops_rows_share_one_dot_radius():
    from raptor.render.charts_svg import render
    spec = charts.chart_named("hierarchy-hops", attribution=_attribution())
    for row in spec.series:
        assert row.get("dots") is not None
    spec.series[0]["dots"] = [{"value": 0.0, "count": 60}]
    spec.series[1]["dots"] = [{"value": 0.2, "count": 1},
                              {"value": 0.8, "count": 1}]
    radii = set(re.findall(r"<circle [^>]*r='([0-9.]+)'", render(spec)))
    assert len(radii) == 1, radii


# -- 8: chart text in the GUI language ------------------------------------------

@pytest.fixture
def portuguese(qapp):
    from raptor.gui import i18n
    i18n.install(qapp, "pt_BR")
    try:
        yield i18n
    finally:
        i18n.install(qapp, "en")


def test_dynamic_chart_buttons_and_hops_label_translate_and_switch_back(
        qapp, portuguese):
    page = ChartsPage()
    _filled(page)
    page.set_compare([100, 200])
    page._set_baseline()
    assert page.compare_button.text() == "Comparar: 2 selecionadas"
    assert page.baseline_button.text() == "Referência: definida"
    assert page.scope_button.text() == "Escopo: sistema"
    page.set_scope(100, follow=False)
    assert page.scope_button.text() == "Escopo: Intake"       # the name is data
    page.select("hierarchy-hops")
    assert re.fullmatch(r"Todas as \d+ execuções", page.hops_label.text())
    page._hops_tick()
    assert re.fullmatch(r"Execução 1 de \d+", page.hops_label.text())
    portuguese.install(qapp, "en")
    portuguese.translate_widgets(page)      # the language switch's own pass
    assert page.compare_button.text() == "Compare: 2 selected"
    assert page.baseline_button.text() == "Baseline: set"
    assert page.scope_button.text() == "Scope: Intake"
    assert re.fullmatch(r"Run 1 of \d+", page.hops_label.text())
    page._clear_baseline()
    assert page.baseline_button.text() == "Baseline: none"


def test_chart_list_headings_and_titles_translate_but_keep_their_keys(
        qapp, portuguese):
    page = ChartsPage()
    _filled(page)
    heads = [item.text(0) for item in page._groups()]
    assert "A RESPOSTA" in heads and "POR HIERARQUIA" in heads
    assert page._group_name(page._groups()[0]) == "The answer"
    shown = {item.data(0, 0x100).__str__(): item.text(0)
             for item in page._chart_items()}
    assert shown["trial-outcome"] == "Resultado de execução a execução"
    assert page.select("hierarchy-scorecard")                  # keys still work
    assert page.select("Hierarchy scorecard")                  # as do titles
    assert page.chart.spec().title == "Painel de hierarquias"


def test_translate_spec_translates_title_and_composed_subtitle_only(portuguese):
    from raptor.gui.ui.chart_i18n import translate_spec
    spec = charts.ChartSpec(
        "pareto", "Hierarchy Pareto",
        "share of system downtime, stacked by main cause · top level · "
        "scope: Intake > Pump house",
        caption="Intake holds the largest share.")
    out = translate_spec(spec)
    assert out is not spec
    assert out.title == "Pareto de hierarquias"
    assert out.subtitle == ("parcela do tempo de inatividade do sistema, "
                            "empilhada pela causa principal · nível superior "
                            "· escopo: Intake > Pump house")
    assert out.caption == spec.caption                 # captions stay English
    assert spec.title == "Hierarchy Pareto"            # the shared spec is untouched
    note = translate_spec(charts.ChartSpec(
        "pareto", "Where the money goes",
        "mean cost per run, by element, largest first (top 10 of 16; the "
        "other 6 together 25 %)"))
    assert "(10 maiores de 16; os outros 6 juntos somam 25 %)" in note.subtitle
    unknown = translate_spec(charts.ChartSpec("pareto", "Something new", "x"))
    assert unknown.title == "Something new"            # falls back to English


def test_english_gui_leaves_the_spec_alone(qapp):
    from raptor.gui.ui.chart_i18n import translate_spec
    spec = charts.ChartSpec("pareto", "Hierarchy Pareto", "top level")
    assert translate_spec(spec) is spec
