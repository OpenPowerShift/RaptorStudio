"""The one measure registry: every reported metric has a definition."""
from __future__ import annotations

import pytest

from raptor.api import metric_definitions
from raptor.api.insight import measures


def test_every_reported_metric_has_a_measure():
    missing = [row["key"] for row in metric_definitions.definitions()
               if measures.for_metric(row["key"]) is None]
    assert missing == []


def test_every_measure_answers_a_question_and_names_its_formula():
    for measure in measures.MEASURES:
        assert measure.answers.endswith("?")
        assert measure.formula.strip()
        assert measure.name and measure.short


def test_the_availability_ladder_reads_the_engine_fields():
    per_run = [{"inherent_availability": 0.99, "achieved_availability": 0.98,
                "operational_availability": 0.96},
               {"inherent_availability": 0.97, "achieved_availability": 0.95,
                "operational_availability": 0.90}]
    ladder = measures.availability_ladder(per_run)
    assert ladder["inherent"]["value"] == pytest.approx(0.98)
    assert ladder["achieved"]["value"] == pytest.approx(0.965)
    assert ladder["operational"]["value"] == pytest.approx(0.93)
    assert all(entry["source"] for entry in ladder.values()
               if isinstance(entry, dict) and "value" in entry)


def test_a_missing_availability_is_none_not_zero():
    ladder = measures.availability_ladder([{"operational_availability": 0.9}])
    assert ladder["inherent"]["value"] is None
    assert ladder["achieved"]["value"] is None
    assert ladder["operational"]["value"] == pytest.approx(0.9)


def test_an_unknown_measure_names_the_known_ids():
    with pytest.raises(ValueError, match="availability"):
        measures.get("not_a_measure")


def test_the_glossary_bottleneck_terms_have_a_measure():
    from raptor.api.glossary import TERMS
    for short in ("MTBDE", "MDT", "MMT", "RRW", "RAW", "Birnbaum"):
        assert any(short in m.short or short in m.name for m in measures.MEASURES), short
    assert "Operational availability" in TERMS


def test_every_gui_glossary_key_reaches_a_measure():
    from raptor.gui.ui.results_common import GLOSSARY
    missing = [label for label in GLOSSARY if measures.for_label(label) is None]
    assert missing == []


def test_the_gui_and_the_export_glossary_say_the_same_thing():
    from raptor.api.glossary import TERMS
    from raptor.gui.ui.results_common import GLOSSARY
    for label in GLOSSARY:
        measure = measures.for_label(label)
        if measure is not None:
            assert GLOSSARY[label] == measures.explainer_text(measure.id)
    for label, text in TERMS.items():
        measure = measures.for_label(label)
        if measure is not None:
            assert text == measures.glossary_line(measure.id)


def test_the_ladder_rungs_add_up_to_the_reported_downtime():
    per_run = [{"total_time": 100.0, "downtime": 10.0,
                "red_corrective_time": 6.0, "red_pm_time": 2.0,
                "red_logistics_time": 1.0, "red_done_time": 1.0,
                "red_other_time": 0.0}]
    ladder = measures.availability_ladder(per_run)
    assert ladder["balanced"] is True
    assert ladder["rungs"]["repair"] == pytest.approx(6.0)
    assert ladder["inherent"]["value"] == pytest.approx(0.94)
    assert ladder["achieved"]["value"] == pytest.approx(0.92)
    assert ladder["operational"]["value"] == pytest.approx(0.90)
