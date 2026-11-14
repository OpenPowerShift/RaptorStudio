"""The Story data tree: which headline, which sections, and the same
numbers the tables show."""
from __future__ import annotations

import pytest

import pytest

from raptor.api.insight.dots import proportion_text, quantile_dots, split_at
from raptor.api.insight.story import build


def runs(n=20, **fields):
    base = {"availability": 0.99, "reliability": 0.8, "mtbde": 100.0,
            "mdt": 5.0, "total_time": 8760.0}
    base.update(fields)
    return [dict(base) for _ in range(n)]


def attribution(causes, elements=None):
    return {"runs": 20, "units": "hours", "causes": causes,
            "elements": elements or [],
            "system": {"red_time": {"mean": 10.0}, "outages": {"mean": 4.0}}}


def element(name, oid, share, cause="repair", causes=None):
    return {"kind": "block", "id": oid, "name": name,
            "dtci": {"mean": share, "sem": 0.01},
            "red_time": {"mean": share * 10.0},
            "causes": causes or {cause: 1.0}, "main_cause": cause}


# ---- dots -----------------------------------------------------------------

def test_dots_are_monotone_and_bounded_by_the_requested_count():
    dots = quantile_dots([i / 100 for i in range(101)], 40)
    assert 1 <= sum(d["count"] for d in dots) <= 40
    assert [d["value"] for d in dots] == sorted(d["value"] for d in dots)


def test_equal_values_stack_and_a_single_value_is_one_dot():
    assert quantile_dots([0.5, 0.5, 0.5], 40) == [{"value": 0.5, "count": 3}]
    assert quantile_dots([], 40) == []


def test_split_counts_at_least_and_says_it_in_words():
    met, total = split_at([0.9, 0.95, 0.99], 0.95)
    assert (met, total) == (2, 3)
    assert proportion_text(2, 3) == "67 runs in 100 met the target"
    assert proportion_text(3, 3) == "every run met the target"
    assert proportion_text(0, 3) == "no run met the target"


# ---- headline -------------------------------------------------------------

def test_the_first_requirement_is_the_headline_and_carries_its_verdict():
    story = build(
        runs(10, availability=0.99),
        requirements=[{"metric": "system.ao", "op": ">=", "value": 0.995,
                       "label": "Contract availability"}])
    h = story.headline
    assert h.label == "Contract availability" and h.target == 0.995
    assert h.met is False and h.gap_text.startswith("missed by")
    assert story.dots and story.provenance["runs"] == 10
    assert "missed" in h.sentence


def test_capacity_is_the_headline_when_capacity_data_exists():
    story = build(runs(5, capacity_weighted_availability=0.92,
                       availability=0.99))
    assert story.headline.field == "capacity_weighted_availability"
    assert story.headline.value == pytest.approx(0.92)


def test_a_phased_model_leads_with_mission_reliability():
    story = build(runs(5, reliability=0.75), phased=True)
    assert story.headline.field == "reliability"


def test_no_target_means_no_verdict_and_no_fake_precision():
    story = build(runs(5))
    assert story.headline.target is None and story.headline.met is None
    assert story.proportion == ""


# ---- drivers, findings, sections, numbers --------------------------------

def test_drivers_are_the_top_three_plus_an_others_share():
    story = build(runs(5), attribution=attribution(
        {"repair": 1.0},
        [element("A", 1, 0.5), element("B", 2, 0.3), element("C", 3, 0.15),
         element("D", 4, 0.05)]))
    assert [d.name for d in story.drivers] == ["A", "B", "C"]
    assert story.other_drivers_share == pytest.approx(0.05)
    assert story.drivers_available


def test_no_attribution_says_so_rather_than_showing_a_zero():
    story = build(runs(5))
    assert story.drivers == [] and story.drivers_available is False


def test_only_the_sections_the_model_uses_are_present():
    story = build(runs(5), attribution=attribution({"wait_spare": 0.2,
                                                    "repair": 0.8}))
    assert {s.key for s in story.sections} == {"spares"}
    quiet = build(runs(5))
    assert quiet.sections == []


def test_at_most_six_key_numbers_and_the_headline_is_first():
    story = build(runs(5, capacity_weighted_availability=0.9,
                       grand_total=1234.0, mean_flow=90.0, lost_flow=10.0))
    assert len(story.numbers) == 6
    assert story.numbers[0].field == story.headline.field


def test_findings_are_plain_data_and_capped_at_three():
    found = [{"rule": f"R-{i}", "title": f"T{i}", "message": "m",
              "severity": "advice", "basis": "statistical"} for i in range(5)]
    story = build(runs(5), findings=found)
    assert [f.rule for f in story.findings] == ["R-0", "R-1", "R-2"]
    assert story.to_dict()["headline"]["field"] == "availability"


def test_plain_text_reads_cleanly_with_and_without_a_target():
    """`explain --story` printed "Availability: 99.99 %  ()" with no target,
    ran its sections together, showed cost as 1.933e+05 and never gave the
    run length (it read a per-run key that does not exist)."""
    from raptor.api.insight.story import text_lines
    plain = build(runs(10, availability=0.9998584, grand_total=193331.0),
                  units="hours", cost_on=True,
                  attribution=attribution({"repair": 1.0}))
    lines = text_lines(plain)
    assert lines[0] == "Availability: 99.986 %"
    assert "" in lines                       # sections stay apart
    assert any("193,331" in line for line in lines)
    assert any("8,760 hours each" in line for line in lines)
    target = build(runs(10, availability=0.99), units="hours",
                   requirements=[{"metric": "system.ao", "op": ">=", "value": 0.995}])
    assert target.headline.gap_text == "missed by 0.5 points = 43.8 h a year"
    assert text_lines(target)[1] == target.headline.gap_text
