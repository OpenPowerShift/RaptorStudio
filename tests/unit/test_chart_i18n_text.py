"""Chart text in the GUI language beyond title and subtitle: axis titles, the
app's words inside the series, scorecard tiles, heatmap headings and the
captions.  Names and numbers are data; SVG, CSV, CLI and MCP stay English."""
from __future__ import annotations

import copy
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.api.insight import charts
from raptor.gui import chart_strings
from raptor.gui.ui import chart_i18n
from raptor.gui.ui.chart_widget import ChartWidget
from raptor.gui.ui.charts_page import ChartsPage
from raptor.render import charts_svg
from tests.unit.test_charts_page import _hierarchy_inputs


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pt(qapp):
    from raptor.gui import i18n
    i18n.install(qapp, "pt_BR")
    try:
        yield i18n
    finally:
        i18n.install(qapp, "en")


def _rows(names=("Intake", "Filtration", "High_Lift"), causes=("pm", "repair", "repair")):
    out = []
    for index, (name, cause) in enumerate(zip(names, causes, strict=True)):
        share = (0.6, 0.0, 0.1)[index]
        out.append({
            "kind": "hier", "id": 10 * (index + 1), "name": name, "path": [name],
            "depth": 1, "parent": 0, "members": [], "member_count": 2,
            "starter_count": 1, "elements": 1,
            "dtci": {"mean": share, "sem": 0.02 if share else None},
            "red_time": {"mean": 5.0 * share},
            "causes": {cause: 1.0} if share else {}, "main_cause": cause if share else None,
            "phase_shares": {"Cruise": 1.0} if share else {},
            "trials": [{"red_time": 5 * share, "dtci": share * f}
                       for f in (0.8, 1.0, 1.2, 0.9)]})
    return out


def _attribution(names=("Intake", "Filtration", "High_Lift"),
                 causes=("pm", "repair", "repair")):
    rows = _rows(names, causes)
    return {"runs": 4, "units": "hours", "causes": {"repair": 0.5, "pm": 0.5},
            "elements": [], "sequences": [], "hierarchies": rows,
            "phases": [{"name": "Cruise", "mission": True,
                        "share_of_downtime": 1.0, "availability": 0.95,
                        "drivers": []}],
            "outages": {"runs": [
                {"start": 0.0, "end": 100.0, "red": [[10.0, 20.0]],
                 "hier": {str(r["id"]): [[10.0, 20.0]] for r in rows[:1]}}
                for _ in range(4)]}}


def _spec(key, **kwargs):
    return charts.chart_named(key, attribution=kwargs.pop("attribution", _attribution()),
                              units="hours", **kwargs)


# -- one test per caption template -----------------------------------------

#: An English sentence for each caption template and what the GUI shows.
SAMPLES = {
    "%1 holds the largest share of system downtime (%2).": (
        "Intake holds the largest share of system downtime (85.3 %).",
        "Intake concentra a maior parcela do tempo de inatividade do sistema (85.3 %)."),
    "No hierarchy has downtime in these runs.": (
        "No hierarchy has downtime in these runs.",
        "Nenhuma hierarquia tem inatividade nestas execuções."),
    "This model has no hierarchy roll-up.": (
        "This model has no hierarchy roll-up.",
        "Este modelo não tem consolidação por hierarquia."),
    "%1 of system downtime is not inside a hierarchy.": (
        "Intake holds the largest share of system downtime (60 %). 12 % of "
        "system downtime is not inside a hierarchy.",
        "Intake concentra a maior parcela do tempo de inatividade do sistema (60 %). "
        "12 % do tempo de inatividade do sistema não está dentro de uma hierarquia."),
    "Showing %1 of %2 hierarchies.": (
        "No hierarchy has downtime in these runs. Showing 8 of 20 hierarchies.",
        "Nenhuma hierarquia tem inatividade nestas execuções. Exibindo 8 de 20 hierarquias."),
    "Showing %1 of %2 parts.": (
        "Showing 5 of 9 parts.", "Exibindo 5 de 9 partes."),
    "No downtime in %1.": (
        "Intake causes the largest share (85.3 % of system downtime). "
        "No downtime in Filtration, Pump house.",
        "Intake causa a maior parcela (85.3 % do tempo de inatividade do sistema). "
        "Sem inatividade em Filtration, Pump house."),
    "%1 had no downtime.": (
        "Intake holds the largest share of system downtime (85.3 %). Filtration had no downtime.",
        "Intake concentra a maior parcela do tempo de inatividade do sistema (85.3 %). "
        "Filtration não teve inatividade."),
    "%1 causes the largest share (%2 of system downtime).": (
        "Intake causes the largest share (85.3 % of system downtime).",
        "Intake causa a maior parcela (85.3 % do tempo de inatividade do sistema)."),
    "%1 causes the largest share (%2 of %3's downtime).": (
        "Pump_1 causes the largest share (40 % of Intake's downtime).",
        "Pump_1 causa a maior parcela (40 % da inatividade de Intake)."),
    "%1 causes the largest share (%2).": (
        "Intake_Pump_3 causes the largest share (32.3 %).",
        "Intake_Pump_3 causa a maior parcela (32.3 %)."),
    "%1 of the downtime was started by no single element.": (
        "12 % of the downtime was started by no single element. "
        "Pump causes the largest share (30 %).",
        "12 % da inatividade não foi iniciada por um único elemento. "
        "Pump causa a maior parcela (30 %)."),
    "No single element is behind the downtime.": (
        "No single element is behind the downtime.",
        "Nenhum elemento isolado é responsável pela inatividade."),
    "No element inside %1 caused downtime.": (
        "No element inside Intake caused downtime.",
        "Nenhum elemento dentro de Intake causou inatividade."),
    "Inside %1: %2 is the largest part (%3).": (
        "Inside Intake: Pump_1 is the largest part (40 %).",
        "Dentro de Intake: Pump_1 é a maior parte (40 %)."),
    "%1 has no downtime below it in this run.": (
        "Intake has no downtime below it in this run.",
        "Intake não tem inatividade abaixo dele nesta execução."),
    "Most system downtime is not inside a hierarchy.": (
        "Most system downtime is not inside a hierarchy.",
        "A maior parte da inatividade do sistema não está dentro de uma hierarquia."),
    "%1 is the largest hierarchy (%2 of system downtime).": (
        "Intake is the largest hierarchy (85.3 % of system downtime).",
        "Intake é a maior hierarquia (85.3 % do tempo de inatividade do sistema)."),
    "By the end of the run, %1.": (
        "By the end of the run, Intake caused 42.8 h, Disinfection caused 5.14 days.",
        "Ao final da execução, Intake causou 42.8 h, Disinfection causou 5.14 dias."),
    "Showing the top %1 of %2 hierarchies; use Compare to choose.": (
        "By the end of the run, Intake caused 42.8 h. Showing the top 4 of 9 "
        "hierarchies; use Compare to choose.",
        "Ao final da execução, Intake causou 42.8 h. Exibindo as 4 principais de 9 "
        "hierarquias; use Comparar para escolher."),
    "%1; each dot is one run's share of system downtime.": (
        "1 hierarchy; each dot is one run's share of system downtime.",
        "1 hierarquia; cada ponto é a parcela de uma execução no tempo de "
        "inatividade do sistema."),
    "%1 causes the most downtime in %2 of %3 runs.": (
        "Intake causes the most downtime in 30 of 30 runs.",
        "Intake causa a maior inatividade em 30 de 30 execuções."),
    "%1: mean share of system downtime %2 over %3 runs.": (
        "Intake: mean share of system downtime 85.3 % over 4 runs.",
        "Intake: parcela média do tempo de inatividade do sistema 85.3 % em 4 execuções."),
    "Run %1 of %2: %3.": (
        "Run 2 of 4: Intake 90 %, Lift 40 %. Intake causes the most downtime in 2 of 4 runs.",
        "Execução 2 de 4: Intake 90 %, Lift 40 %. Intake causa a maior inatividade em 2 de 4 execuções."),
    "%1 is down %2 a run more than %3 (± %4).": (
        "Intake is down 5.5 h a run more than baseline run 0 (± 1.2 h).",
        "Intake fica parado 5.5 h por execução a mais que a referência run 0 (± 1.2 h)."),
    "%1 is down %2 a run more than %3.": (
        "Intake is down 5.5 hours a run more than the baseline.",
        "Intake fica parado 5.5 horas por execução a mais que a referência."),
    "%1 is down %2 a run less than %3 (± %4).": (
        "Intake is down 24.4 h a run less than the baseline (± 5.5 h).",
        "Intake fica parado 24.4 h por execução a menos que a referência (± 5.5 h)."),
    "%1 is down %2 a run less than %3.": (
        "Intake is down 24.4 h a run less than baseline run 0.",
        "Intake fica parado 24.4 h por execução a menos que a referência run 0."),
    "%1 has the same downtime as %2 (± %3).": (
        "Intake has the same downtime as baseline run 0 (± 5.5 h).",
        "Intake tem a mesma inatividade que a referência run 0 (± 5.5 h)."),
    "%1 has the same downtime as %2.": (
        "Intake has the same downtime as the baseline.",
        "Intake tem a mesma inatividade que a referência."),
    "%1 is mostly %2.": (
        "Intake is mostly planned maintenance.",
        "Intake é principalmente manutenção planejada."),
    "%1 holds the most hierarchy downtime; all of it is in %2 (%3 of its downtime).": (
        "Intake holds the most hierarchy downtime; all of it is in Cruise (100 % of its downtime).",
        "Intake concentra a maior inatividade entre as hierarquias; toda ela está em "
        "Cruise (100 % da sua inatividade)."),
    "%1 holds the most hierarchy downtime; most of it is in %2 (%3 of its downtime).": (
        "Intake holds the most hierarchy downtime; most of it is in Cruise (75 % of its downtime).",
        "Intake concentra a maior inatividade entre as hierarquias; a maior parte está em "
        "Cruise (75 % da sua inatividade)."),
    "No hierarchy with downtime in this run has a per-phase breakdown; rerun with "
    "phases and attribution on.": (
        "No hierarchy with downtime in this run has a per-phase breakdown; rerun with "
        "phases and attribution on.",
        "Nenhuma hierarquia com inatividade nesta execução tem detalhamento por fase; "
        "execute novamente com fases e atribuição ativadas."),
    "No downtime inside %1 is split by phase.": (
        "No downtime inside Intake is split by phase.",
        "Nenhuma inatividade dentro de Intake é dividida por fase."),
    "%1 starts the largest share (%2 of downtime).": (
        "Intake starts the largest share (85.3 % of downtime).",
        "Intake inicia a maior parcela (85.3 % da inatividade)."),
    "The commonest start is %1 (%2 of downtime).": (
        "The commonest start is Intake_Pump_3 (32.3 % of downtime).",
        "O início mais comum é Intake_Pump_3 (32.3 % da inatividade)."),
    "No outage was recorded.": (
        "No outage was recorded.", "Nenhuma indisponibilidade foi registrada."),
    "%1; %2 of downtime attributed to them (system down %3).": (
        "4 hierarchies; 98 h of downtime attributed to them (system down 120 h).",
        "4 hierarquias; 98 h de inatividade atribuída a elas (sistema parado 120 h)."),
    "%1; %2 of downtime attributed to them.": (
        "1 hierarchy; 98 h of downtime attributed to them.",
        "1 hierarquia; 98 h de inatividade atribuída a elas."),
    "No downtime in this run is attributed to the named hierarchies; the unassigned "
    "row is the %1 the system was down.": (
        "No downtime in this run is attributed to the named hierarchies; the unassigned "
        "row is the 12 h the system was down.",
        "Nenhuma inatividade desta execução é atribuída às hierarquias nomeadas; a linha "
        "não atribuída corresponde aos 12 h em que o sistema ficou parado."),
    "The one run reached %1.": (
        "The one run reached 99.4 %.", "A única execução atingiu 99.4 %."),
    "Every run reached %1.": (
        "Every run reached 99.4 %.", "Todas as execuções atingiram 99.4 %."),
    "All %1 runs reached %2 %3; the best reached %4.": (
        "All 5 runs reached at most 99.4 %; the best reached 98 %.",
        "Todas as 5 execuções atingiram no máximo 99.4 %; a melhor atingiu 98 %."),
    "9 runs in 10 reached %1 %2; half reached %3 %4.": (
        "9 runs in 10 reached at least 99.243 %; half reached at least 99.428 %.",
        "9 em cada 10 execuções atingiram pelo menos 99.243 %; metade atingiu pelo "
        "menos 99.428 %."),
    "%1 of runs reached the target %2.": (
        "No runs. 50 % of runs reached the target 99 %.",
        "Nenhuma execução. 50 % das execuções atingiram a meta 99 %."),
    "No runs.": ("No runs.", "Nenhuma execução."),
    "From %1, Kaplan–Meier estimate.": (
        "From 30 runs, Kaplan–Meier estimate. No run failed.",
        "De 30 execuções, estimativa de Kaplan–Meier. Nenhuma execução falhou."),
    "From %1, Kaplan–Meier estimate; %2 ended before a failure and are censored.": (
        "From 1 run, Kaplan–Meier estimate; 1 ended before a failure and are censored. "
        "No run failed.",
        "De 1 execução, estimativa de Kaplan–Meier; 1 terminaram antes de uma falha e "
        "são censuradas. Nenhuma execução falhou."),
    "Reliability is %1 or less by %2 and %3 or less by %4.": (
        "From 30 runs, Kaplan–Meier estimate. Reliability is 90 % or less by 268 hours "
        "and 50 % or less by 2,190 hours.",
        "De 30 execuções, estimativa de Kaplan–Meier. A confiabilidade é de 90 % ou "
        "menos até 268 horas e de 50 % ou menos até 2,190 horas."),
    "Reliability is %1 or less by %2.": (
        "Reliability is 90 % or less by 268 hours.",
        "A confiabilidade é de 90 % ou menos até 268 horas."),
    "The last failure was at %1.": (
        "The last failure was at 2,286 hours.", "A última falha ocorreu em 2,286 horas."),
    "%1 runs first went down at exactly %2: a scheduled outage such as planned "
    "maintenance, not a random failure.": (
        "14 runs first went down at exactly 2,190 hours: a scheduled outage such as "
        "planned maintenance, not a random failure.",
        "14 execuções pararam pela primeira vez exatamente em 2,190 horas: uma "
        "indisponibilidade programada, como a manutenção planejada, e não uma falha "
        "aleatória."),
    "No run failed.": ("No run failed.", "Nenhuma execução falhou."),
    "No run recorded a time to first failure.": (
        "No run recorded a time to first failure.",
        "Nenhuma execução registrou um tempo até a primeira falha."),
    "After %1 runs the mean %2 is %3 ± %4 points (2 standard errors, about 95 %).": (
        "After 30 runs the mean Availability is 99.4269 % ± 0.062 points (2 standard "
        "errors, about 95 %). Median 99.4208 %.",
        "Após 30 execuções, a média de Disponibilidade é 99.4269 % ± 0.062 pontos "
        "(2 erros padrão, cerca de 95 %). Mediana 99.4208 %."),
    "After %1 runs the mean %2 is %3 ± %4 (2 standard errors, about 95 %).": (
        "After 30 runs the mean Cost is $1.2k ± $3 (2 standard errors, about 95 %).",
        "Após 30 execuções, a média de Custo é $1.2k ± $3 (2 erros padrão, cerca de 95 %)."),
    "Median %1.": ("Median 99.4208 %.", "Mediana 99.4208 %."),
    "Not enough runs to estimate the error.": (
        "Not enough runs to estimate the error.",
        "Não há execuções suficientes para estimar o erro."),
    "The system never went down in %1, so there is nothing to show.": (
        "The system never went down in 30 runs, so there is nothing to show.",
        "O sistema nunca parou em 30 execuções, então não há nada a mostrar."),
    "The system never went down, so there is nothing to show.": (
        "The system never went down, so there is nothing to show.",
        "O sistema nunca parou, então não há nada a mostrar."),
    "The largest single loss is %1 (%2 points).": (
        "The largest single loss is planned maintenance (0.353 points). The steps use "
        "the same Red-time split as the availability ladder.",
        "A maior perda isolada é manutenção planejada (0.353 pontos). As etapas usam a "
        "mesma divisão do tempo parado que a escada de disponibilidade."),
    "The steps use the same Red-time split as the availability ladder.": (
        "The steps use the same Red-time split as the availability ladder.",
        "As etapas usam a mesma divisão do tempo parado que a escada de disponibilidade."),
    "%1 took nothing away in these runs.": (
        "Inherent (repair only) 99.7 %; Achieved (+ planned maintenance) 99.7 %. "
        "Planned maintenance took nothing away in these runs.",
        "Inerente (somente reparo) 99.7 %; Alcançada (+ manutenção planejada) 99.7 %. "
        "Manutenção planejada não tirou nada nestas execuções."),
    "No availability reported.": (
        "No availability reported.", "Nenhuma disponibilidade informada."),
}

#: Run-to-run caption clauses are read apart from the sentences.
CLAUSES = ("%1: %2", ", mean %1",
           "; the middle half of the runs lie between %1 and %2 (Q1 to Q3)",
           "; %1 of %2 (%3) met the target")


def test_every_caption_template_has_a_sample():
    assert set(SAMPLES) == set(chart_strings.CAPTIONS)


@pytest.mark.parametrize("template", sorted(SAMPLES))
def test_caption_template_translates_with_names_kept(pt, template):
    english, expected = SAMPLES[template]
    assert chart_i18n.translate_caption(english) == expected


def test_run_to_run_caption_clauses_translate(pt):
    full = ("Availability: 30 runs, mean 99.4269 %; the middle half of the runs lie "
            "between 99.357 % and 99.538 % (Q1 to Q3); 12 of 30 (40 %) met the target.")
    assert chart_i18n.translate_caption(full) == (
        "Disponibilidade: 30 execuções, média 99.4269 %; a metade central das execuções "
        "fica entre 99.357 % e 99.538 % (Q1 a Q3); 12 de 30 (40 %) atingiram a meta.")
    assert chart_i18n.translate_caption("Availability: 1 run.") == \
        "Disponibilidade: 1 execução."
    assert all(clause in chart_strings.SOURCES for clause in CLAUSES)


def test_availability_ladder_caption_translates(pt):
    text = ("Inherent (repair only) 99.7791 %; Achieved (+ planned maintenance) 99.4269 %; "
            "Operational (+ logistics, spares, crews) 99.4269 %. Logistics, spares, crews "
            "took nothing away in these runs.")
    assert chart_i18n.translate_caption(text) == (
        "Inerente (somente reparo) 99.7791 %; Alcançada (+ manutenção planejada) 99.4269 %; "
        "Operacional (+ logística, sobressalentes, equipes) 99.4269 %. Logística, "
        "sobressalentes, equipes não tirou nada nestas execuções.")


def test_an_unknown_sentence_leaves_the_whole_caption_english(pt):
    known = "Intake holds the largest share of system downtime (85.3 %)."
    assert chart_i18n.translate_caption(known) != known
    mixed = known + " Something the app never said."
    assert chart_i18n.translate_caption(mixed) == mixed
    assert chart_i18n.translate_caption("Something new.") == "Something new."


def test_a_name_that_looks_like_an_app_word_is_data_in_a_caption(pt):
    out = chart_i18n.translate_caption(
        "Planned maintenance causes the largest share (85.3 % of system downtime). "
        "No downtime in system, Other.")
    assert out == ("Planned maintenance causa a maior parcela (85.3 % do tempo de "
                   "inatividade do sistema). Sem inatividade em system, Other.")


# -- real specs -------------------------------------------------------------

HIERARCHY_KEYS = ("hierarchy-scorecard", "hierarchy-pareto", "hierarchy-treemap",
                  "hierarchy-overlay", "hierarchy-small-multiples", "hierarchy-hops",
                  "hierarchy-heatmap", "hierarchy-anatomy", "hierarchy-ribbon")


@pytest.mark.parametrize("key", HIERARCHY_KEYS)
def test_real_hierarchy_captions_translate_and_keep_every_name(pt, key):
    spec = _spec(key)
    shown = chart_i18n.translate_spec(spec)
    assert shown.caption != spec.caption, spec.caption
    for name in ("Intake", "Filtration", "High_Lift"):
        assert (name in shown.caption) == (name in spec.caption)
    assert shown.x_label != spec.x_label or shown.y_label != spec.y_label \
        or key == "hierarchy-heatmap"


def test_delta_caption_and_axis_translate(pt):
    spec = _spec("hierarchy-delta", baseline=_attribution()["hierarchies"][:2],
                 baseline_label="run 0")
    shown = chart_i18n.translate_spec(spec)
    assert "referência run 0" in shown.caption
    assert any(name in shown.caption for name in ("Intake", "High_Lift"))
    assert shown.x_label == "variação da inatividade (horas)"
    assert shown.y_label == "hierarquia"


def test_axis_titles_series_marks_and_heatmap_headings(pt):
    overlay = chart_i18n.translate_spec(_spec("hierarchy-overlay"))
    assert overlay.x_label == "tempo simulado (horas)"
    assert overlay.y_label == "inatividade acumulada (horas)"
    names = [s["name"] for s in overlay.series]
    assert names[-1] == "sistema" and "Intake" in names
    assert overlay.series[-1]["end_text"].startswith("sistema ")
    heat = chart_i18n.translate_spec(_spec("hierarchy-heatmap"))
    labels = [c["label"] for c in heat.provenance["columns"]]
    assert "Reparo direto" in labels and "Manutenção planejada" in labels
    keys = [c["key"] for c in heat.provenance["columns"]]
    assert "repair" in keys                           # keys are never translated
    hops = chart_i18n.translate_spec(_spec("hierarchy-hops"))
    assert hops.provenance["summary"].startswith("Intake causa")
    ladder = chart_i18n.translate_spec(charts.availability_ladder(
        {"inherent": {"value": 0.99}, "achieved": {"value": 0.98},
         "operational": {"value": 0.97}, "runs": 4}))
    assert ladder.series[0]["name"] == "Inerente (somente reparo)"
    assert ladder.series[0]["key"] == "inherent"
    outcome = chart_i18n.translate_spec(charts.trial_outcome(
        [{"availability": v} for v in (0.9, 0.95, 0.99, 0.97, 0.96)]))
    assert [m["label"] for m in outcome.marks if m["kind"] == "quartile"] == \
        ["Q1", "mediana", "Q3"]
    assert outcome.x_label == "Disponibilidade" and outcome.y_label == "execuções"


def test_names_that_look_like_app_words_are_never_translated(pt):
    tricky = ("system", "Planned maintenance", "Other")
    attribution = _attribution(names=tricky, causes=("pm", "repair", "repair"))
    for key in HIERARCHY_KEYS:
        spec = _spec(key, attribution=attribution)
        shown = chart_i18n.translate_spec(spec)
        before = {str(r.get("name") or r.get("label")) for r in spec.series
                  if r.get("hierarchy_id") != 0}
        after = {str(r.get("name") or r.get("label")) for r in shown.series
                 if r.get("hierarchy_id") != 0}
        assert before == after, key
        # an ordinary caption sentence still reads in Portuguese around the names
        if "Planned maintenance" in spec.caption:
            assert "Planned maintenance" in shown.caption
    overlay = chart_i18n.translate_spec(_spec("hierarchy-overlay", attribution=attribution))
    lines = {s["hierarchy_id"]: s["name"] for s in overlay.series}
    assert lines[0] == "sistema"                       # the dashed reference
    assert lines[10] == "system"                       # a hierarchy of that name
    card = chart_i18n.translate_spec(_spec("hierarchy-scorecard", attribution=attribution))
    assert [r["name"] for r in card.series][0] in tricky
    assert "Planned maintenance" in card.caption or "system" in card.caption \
        or "Other" in card.caption


def test_scorecard_widget_words_translate(pt):
    assert chart_i18n.cause_label("pm") == "Manutenção planejada"
    assert chart_i18n.cause_label("repair") == "Reparo direto"
    assert chart_i18n.cause_label("wait_spare") == "Aguardando um sobressalente"
    assert chart_i18n.fmt("%1 members · %2 started outages", 4, 2) == "4 membros · 2 iniciadores"
    assert chart_i18n.word("each dot a run") == "cada ponto, uma execução"
    assert chart_i18n.word("± 2 standard errors") == "± 2 erros padrão"
    assert chart_i18n.fmt("± %1 pts", "6.6") == "± 6.6 pts"
    from raptor.gui.ui import chart_widget
    assert chart_widget._badge_text("▼ 52 pts better") == "▼ 52 pts melhor"
    assert chart_widget._badge_text("▲ 1.8 pts worse") == "▲ 1.8 pts pior"
    assert chart_widget._badge_text("= no change") == "= sem alteração"
    assert chart_widget._badge_text("new (not in baseline)") == "novo (fora da referência)"


def test_a_translated_chart_paints_every_hierarchy_kind(pt, qapp):
    widget = ChartWidget()
    widget.resize(900, 560)
    for key in HIERARCHY_KEYS:
        widget.set_spec(chart_i18n.translate_spec(_spec(key)))
        assert not widget.grab().toImage().isNull(), key


# -- the English GUI and every other surface stay as they were ---------------

def test_english_gui_leaves_specs_exactly_unchanged(qapp):
    for key in HIERARCHY_KEYS:
        spec = _spec(key)
        before = copy.deepcopy(spec)
        assert chart_i18n.translate_spec(spec) is spec
        assert spec == before
    assert chart_i18n.translate_caption("Intake had no downtime.") == "Intake had no downtime."
    assert chart_i18n.word("each dot a run") == "each dot a run"
    assert chart_i18n.fmt("target %1", "99 %") == "target 99 %"
    assert chart_i18n.cause_label("pm") == "Planned maintenance"


@pytest.mark.parametrize("key", HIERARCHY_KEYS)
def test_translating_never_touches_the_shared_spec_or_its_svg(pt, key):
    spec = _spec(key)
    before_svg = charts_svg.render(spec)
    before = copy.deepcopy(spec)
    shown = chart_i18n.translate_spec(spec)
    assert shown is not spec
    assert spec == before
    assert charts_svg.render(spec) == before_svg
    assert "Portuguese" not in before_svg and "Reparo" not in before_svg
    assert spec.to_dict() == before.to_dict()          # CLI/MCP/CSV read this


# -- the Charts page --------------------------------------------------------

def _page():
    page = ChartsPage()
    per_run, attribution, importance = _hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    return page


def test_rebuilt_compare_and_scope_menus_are_translated(pt, qapp):
    page = _page()
    page._update_compare_menu()
    page._update_scope_menu()
    first = [a.text() for a in page.compare_menu.actions() if a.text()]
    assert first[0] == "Quatro principais por tempo de inatividade"
    assert [a.text() for a in page.scope_menu.actions()][0] == "Sistema"
    # model data in the same menus stays exactly as written
    assert "Intake" in [a.text().strip() for a in page.scope_menu.actions()]


def test_the_tool_row_wraps_instead_of_clipping_at_a_narrow_width(pt, qapp):
    page = _page()
    page.select("hierarchy-hops")
    page.resize(470, 700)
    page.show()
    qapp.processEvents()
    visible = [w for w in (page.scope_button, page.follow_button, page.compare_button,
                           page.baseline_button, page.export_button)
               if w.isVisibleTo(page)]
    assert visible
    for widget in visible:
        assert widget.width() >= widget.sizeHint().width() - 1, widget.text()
        assert widget.geometry().right() <= page.width()
    tops = {w.geometry().top() for w in visible}
    assert len(tops) > 1                               # more than one row
    assert page.compare_button.text() == "Comparar: 4 principais"
    page.resize(1300, 700)
    qapp.processEvents()
    assert len({w.geometry().top() for w in visible}) == 1
    pane = page.export_button.parentWidget()
    assert page.export_button.geometry().right() > pane.width() - 40   # still at the right
    page.close()
