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
