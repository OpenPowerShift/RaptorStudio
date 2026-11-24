"""ChartSpec -> SVG: well-formed, deterministic and honest about marks."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from raptor.api.insight import charts
from raptor.render import charts_svg


def _specs() -> dict[str, charts.ChartSpec]:
    per_run = [{"availability": v} for v in (0.9, 0.95, 0.99, 1.0)]
    attribution = {
        "runs": 4, "causes": {"repair": 0.6, "wait_spare": 0.4},
        "elements": [
            {"name": "Pump", "dtci": {"mean": 0.3, "sem": 0.02},
             "deci": {"mean": 0.5}, "red_time": {"mean": 4.0},
             "causes": {"repair": 1.0}},
            {"name": "Valve", "dtci": {"mean": 0.1, "sem": 0.01},
             "deci": {"mean": 0.2}, "red_time": {"mean": 1.0},
             "causes": {"wait_spare": 1.0}},
        ],
        "outage_durations": {"count": 4, "p50": 2.0, "p90": 8.0,
                             "hist": [{"lo": 1.0, "hi": 2.0, "count": 2.0},
                                      {"lo": 2.0, "hi": 4.0, "count": 2.0}]},
    }
    importance = {"metrics": {"Availability": {"rows": [
        {"block": "Pump", "raw": 3.0, "rrw": 2.0}]}}}
    ladder = {"inherent": {"value": 0.99}, "achieved": {"value": 0.97},
              "operational": {"value": 0.92}}
    cost = SimpleNamespace(blocks={
        1: SimpleNamespace(name="Pump", total=60.0),
        2: SimpleNamespace(name="Valve", total=40.0)})
    return {
        "dot": charts.trial_outcome(per_run, target=0.95),
        "ecdf": charts.ecdf(per_run, target=0.95),
        "pareto": charts.downtime_pareto(attribution),
        "waterfall": charts.downtime_waterfall(attribution, operational=0.92),
        "ladder": charts.availability_ladder(ladder),
        "histogram": charts.outage_histogram(attribution),
        "bubble": charts.frequency_duration(attribution),
        "quadrant": charts.importance_quadrant(importance),
        "convergence": charts.convergence(per_run, target=0.95),
        "cost": charts.cost_pareto(charts.element_costs([SimpleNamespace(cost=cost)])),
        "capacity": charts.lost_capacity_pareto(_capacity()),
        "scatter": charts.capacity_vs_downtime(_capacity()),
        "fan": charts.annual_fan(_periods()),
        "treemap": charts.downtime_treemap(_capacity()),
        "ribbon": charts.ensemble_ribbon({"runs": 2, "outages": {"runs": [
            {"start": 0, "end": 10, "red": [2, 4]},
            {"start": 0, "end": 10, "red": []}]}}),
        "icicle": charts.outage_anatomy({
            "runs": 2, "sequences": [
                {"steps": [{"element": "Pump", "cause": "repair"},
                           {"element": "Valve", "cause": "wait_spare"}],
                 "share": 0.6, "outages": 3},
                {"steps": [{"element": "Pump", "cause": "repair"}],
                 "share": 0.4, "outages": 2}]}),
    }


def _capacity():
    return {
        "runs": 4, "causes": {"repair": 0.7},
        "capacity": {"rule": "proportional", "generated": 100.0,
                     "elements": [{"name": "Pump", "share": {"mean": 0.6},
                                   "lost_energy": {"mean": 12.0},
                                   "through_dependents": {"mean": 0.0}}],
                     "causes": {"repair": {"mean": 7.0}}},
        "elements": [{"name": "Pump", "dtci": {"mean": 0.3},
                      "deci": {"mean": 0.5}, "red_time": {"mean": 4.0},
                      "causes": {}}],
    }


def _periods():
    return {"runs": 2, "periods_per_run": 2, "threshold": 0.95,
            "pooled": {"n": 4, "p5": 0.9, "p50": 0.97, "p95": 1.0},
            "by_index": [
                {"index": 1, "n": 2, "p5": 0.90, "p25": 0.94, "p50": 0.97,
                 "p75": 0.99, "p95": 1.0},
                {"index": 2, "n": 2, "p5": 0.88, "p25": 0.92, "p50": 0.96,
                 "p75": 0.98, "p95": 1.0}]}


@pytest.mark.parametrize("name", sorted(_specs()))
def test_each_chart_is_well_formed_svg(name):
    spec = _specs()[name]
    svg = charts_svg.render(spec)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert spec.title in svg
    # a long caption wraps over several text lines: every word is there
    shown = " ".join(t.text or "" for t in root.iter() if t.tag.endswith("text"))
    assert " ".join(spec.caption.split()) in " ".join(shown.split())
    assert "None" not in svg and ">nan<" not in svg.lower()
    assert len(svg) > 400


def test_the_same_spec_renders_the_same_bytes():
    spec = _specs()["pareto"]
    assert charts_svg.render(spec) == charts_svg.render(spec)


def test_a_pareto_names_every_cause_and_draws_its_pattern():
    svg = charts_svg.render(_specs()["pareto"])
    assert "pat-wait_spare" in svg
    from raptor.api.insight import palette
    assert palette.colour("repair") in svg  # the solid repair fill, made distinct by pattern
    assert "Pump" in svg and "Valve" in svg


def test_a_dot_plot_draws_the_target_line():
    svg = charts_svg.render(_specs()["dot"])
    assert "stroke-dasharray='5,3'" in svg
    assert "target 95 %" in svg


def test_a_waterfall_draws_one_step_per_cause():
    spec = _specs()["waterfall"]
    svg = charts_svg.render(spec)
    assert svg.count("<rect") >= len(spec.series)


def test_titles_are_escaped_not_injected():
    spec = charts.ChartSpec(kind="curve", title="A &lt; B",
                            caption="x < y")
    svg = charts_svg.render(spec)
    assert "<title" not in svg
    assert "A &lt; B" in svg or "A &amp;lt; B" in svg
    ET.fromstring(svg)


def test_write_round_trips_the_document(tmp_path):
    path = tmp_path / "chart.svg"
    charts_svg.write(_specs()["ladder"], path)
    assert path.read_text(encoding="utf-8").startswith("<svg")


# -- presentation quality: nothing shortened, page sizes, folding ----------

_LONG = "Primary_Coolant_Recirculation_Pump_Assembly_North"


def _texts(svg: str) -> list[str]:
    return [node.text or "" for node in ET.fromstring(svg).iter()
            if node.tag.endswith("text")]


def _long_attribution(n: int = 12) -> dict:
    names = [f"{_LONG}_{i}" for i in range(n)]
    shares = [0.5, 0.3] + [0.2 / (n - 2)] * (n - 2)
    return {
        "runs": 4, "causes": {"repair": 0.6, "wait_spare": 0.4},
        "elements": [{"name": name, "dtci": {"mean": share, "sem": 0.4},
                      "deci": {"mean": 0.1}, "red_time": {"mean": 1.0},
                      "causes": {"repair": 1.0}, "main_cause": "repair"}
                     for name, share in zip(names, shares, strict=True)],
    }


@pytest.mark.parametrize("page", [None, *charts_svg.PAGES])
@pytest.mark.parametrize("build", [
    lambda a: charts.downtime_pareto(a, top=12),
    lambda a: charts.downtime_treemap(a),
    lambda a: charts.downtime_waterfall(a, operational=0.9),
], ids=["pareto", "treemap", "waterfall"])
def test_no_label_is_ever_shortened(build, page):
    """Saved charts go into reports and slides: a name cut to "Prim…" is
    useless, so names wrap (or a treemap cell is numbered and named in a
    footnote) instead."""
    spec = build(_long_attribution())
    svg = charts_svg.render(spec, page=page)
    texts = _texts(svg)
    assert not any("…" in t for t in texts)
    joined = "".join(texts).replace(" ", "")
    for row in spec.series:
        assert str(row["name"]).replace(" ", "") in joined


def test_wrapping_breaks_names_at_underscores_and_never_inside_numbers():
    lines = charts_svg._wrap("Ground_Control_Station 10.4 %", 90)
    assert "".join(lines).replace(" ", "") == "Ground_Control_Station10.4%"
    assert all(len(line) <= int(90 / charts_svg.CHAR_W) for line in lines)
    assert not any(line.endswith("10.") for line in lines)


def test_a_share_axis_and_its_uncertainty_range_stop_at_100_percent():
    spec = charts.downtime_pareto(_long_attribution(3))
    texts = _texts(charts_svg.render(spec))
    assert "100 %" in texts
    assert not any(t in ("120 %", "150 %", "200 %") for t in texts)


def test_a_page_declares_its_physical_size():
    root = ET.fromstring(charts_svg.render(_specs()["pareto"], page="a4"))
    assert root.get("width") == "297mm" and root.get("height") == "210mm"
    assert root.get("viewBox") == "0 0 1123 794"
    with pytest.raises(ValueError, match="unknown page"):
        charts_svg.render(_specs()["pareto"], page="letter")


def test_cells_too_small_to_label_are_named_under_the_treemap():
    spec = charts.downtime_treemap(_long_attribution(30))
    texts = _texts(charts_svg.render(spec))
    footnote = " ".join(t for t in texts if t.startswith("Not labelled")
                        or "·" in t)
    assert "Not labelled in the chart" in footnote


def test_write_takes_a_page(tmp_path):
    path = tmp_path / "chart.svg"
    charts_svg.write(_specs()["ladder"], path, page="slide")
    assert "338.67mm" in path.read_text(encoding="utf-8")


def test_a_cell_with_no_cause_puts_other_in_the_key():
    # "(no single element)" and "N others" cells have no cause and are drawn
    # as Other (grey, dotted); the key used to leave Other out
    spec = charts.ChartSpec(
        "treemap", "Lost capacity treemap", "", "", "",
        series=[{"name": "Pump", "value": 0.7, "cause": "repair",
                 "x": 0, "y": 0, "w": 70, "h": 100},
                {"name": "(no single element)", "value": 0.3, "cause": None,
                 "x": 70, "y": 0, "w": 30, "h": 100}],
        provenance={"format": "percent"})
    labels = [label for label, _fill in charts_svg._key(spec)]
    assert "Other" in labels
    from raptor.gui.ui.chart_widget import ChartWidget
    assert ChartWidget._causes(spec) == ["repair", "other"]


def test_the_aria_label_names_the_hierarchy_scope():
    """The better-visualisation spec (P0.1): the scope goes in the title
    block and the SVG aria-label, so a screen reader hears it too."""
    from raptor.api.insight.charts import ChartSpec
    from raptor.render.charts_svg import render
    spec = ChartSpec("pareto", "Hierarchy Pareto", "", "", "",
                     series=[{"name": "Pump", "value": 0.4}],
                     scope={"kind": "hierarchy", "id": 2,
                            "name": "Pump house",
                            "path": ["Intake", "Pump house"]})
    assert "aria-label='Hierarchy Pareto (scope: Intake &gt; Pump house)'" in render(spec)
    spec.scope = None
    assert "aria-label='Hierarchy Pareto'" in render(spec)
