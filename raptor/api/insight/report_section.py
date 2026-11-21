"""The front matter of a report: the Story, its charts and the checks.

Built from the same artefacts as the application, command line and MCP, so a
reviewer reading the PDF sees the same headline, figures and findings as the
analyst.  Qt-free; `raptor.api.report_html` lays it out.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class InsightSection:
    story: dict[str, Any] | None = None
    charts: list[Any] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    suppressed: list[dict[str, Any]] = field(default_factory=list)
    measure_ids: list[str] = field(default_factory=list)


def from_run(workspace: Any, run_id: str, *, rbd: Any = None,
             suppressed: list[dict[str, Any]] | None = None) -> InsightSection:
    """Assemble the report's front matter for one finished run."""
    from raptor.api.insight import drivers
    from raptor.api.insight.story import from_run as story_from_run
    from raptor.api.run import run_dir
    from raptor.api.run_tables import read_per_run, run_currency

    story = story_from_run(workspace, run_id)
    per_run = read_per_run(run_dir(workspace, run_id), run_id) or []
    attribution = None
    try:
        attribution = drivers.read(workspace, run_id)
    except Exception:  # noqa: BLE001 - a report without drivers is still a report
        attribution = None
    try:
        from raptor.api.insight.cost_series import stored_runs
        cost_runs = stored_runs(workspace, run_id)
    except OSError:
        cost_runs = None
    return from_parts(story, per_run, attribution=attribution, rbd=rbd,
                      suppressed=suppressed, cost_runs=cost_runs,
                      currency=(getattr(rbd, "currency", None)
                                or run_currency(workspace, run_id)))


def from_parts(story: Any, per_run: list[dict], *,
               attribution: dict[str, Any] | None = None,
               rbd: Any = None, charts_in: list[Any] | None = None,
               findings: list[dict[str, Any]] | None = None,
               suppressed: list[dict[str, Any]] | None = None,
               cost_runs: list[dict[str, Any]] | None = None,
               currency: str | None = None) -> InsightSection:
    """Build the section from data the caller already holds (the GUI path),
    money in `currency` (default: `rbd`'s, else "$").  See `_from_parts`."""
    from raptor.api.insight import charts as chart_data
    from raptor.api.insight.axes import currency as _currency
    with _currency(currency or getattr(rbd, "currency", None)):
        section = _from_parts(story, per_run, attribution=attribution, rbd=rbd,
                              charts_in=charts_in, findings=findings,
                              suppressed=suppressed, cost_runs=cost_runs)
        section.charts = [chart_data.with_currency_stamp(spec)
                          for spec in section.charts]
    return section


def _from_parts(story: Any, per_run: list[dict],  # noqa: C901, PLR0912, PLR0915
                *, attribution: dict[str, Any] | None = None,
                rbd: Any = None, charts_in: list[Any] | None = None,
                findings: list[dict[str, Any]] | None = None,
                suppressed: list[dict[str, Any]] | None = None,
                cost_runs: list[dict[str, Any]] | None = None
                ) -> InsightSection:
    """Build the section from data the caller already holds (the GUI path).

    `story` may be a `Story` or its dict; `charts_in` overrides the figures
    the caller has already built.
    """
    from raptor.api.insight import charts as chart_data

    story_dict = story.to_dict() if hasattr(story, "to_dict") else dict(story)
    headline_dict = story_dict.get("headline") or {}
    provided = charts_in is not None
    specs = list(charts_in or ())
    if not provided and per_run:
        field_name = str(headline_dict.get("field") or "availability")
        label = str(headline_dict.get("label") or "Availability")
        target = headline_dict.get("target")
        units = str(story_dict.get("units") or "")
        higher = str(headline_dict.get("op") or ">=") != "<="
        specs.append(chart_data.trial_outcome(
            per_run, field_name, label=label, target=target, units=units,
            higher_is_better=higher))
        from raptor.api.insight import measures
        specs.append(chart_data.availability_ladder(
            measures.availability_ladder(per_run)))
        if len(per_run) >= 2:
            specs.append(chart_data.convergence(
                per_run, field_name, target=target, label=label))
        # R(t) only says something once a run has had a system failure
        if any(row.get("mttff") and not row.get("mttff_censored") for row in per_run):
            specs.append(chart_data.reliability_curve(per_run, units=units))
    if not provided and per_run and cost_runs:
        specs.append(chart_data.cost_categories(cost_runs))
        specs.append(chart_data.cost_cumulative(cost_runs))
        specs.append(chart_data.cost_annual(cost_runs))
    if not provided and attribution:
        capacity = attribution.get("capacity") or {}
        if capacity.get("elements"):
            # a capacity model's Story ranks lost capacity: the report
            # ranked only downtime and drew no capacity chart
            specs.append(chart_data.lost_capacity_pareto(attribution))
            specs.append(chart_data.capacity_waterfall(attribution))
        rows = attribution.get("hierarchies") or []
        has_downtime = any(float((row.get("dtci") or {}).get("mean") or 0.0) > 0
                           for row in rows)
        if rows and has_downtime:
            specs.append(chart_data.hierarchy_scorecard(attribution))
            specs.append(chart_data.hierarchy_pareto(attribution))
            specs.append(chart_data.hierarchy_treemap(attribution))
            specs.append(chart_data.hierarchy_heatmap(attribution))
            if (attribution.get("phases")
                    and any(row.get("phase_shares") for row in rows)):
                specs.append(chart_data.hierarchy_phase_heatmap(attribution))
        specs.append(chart_data.downtime_pareto(attribution))
        if any(row.get("num_component_failures") for row in per_run):
            # the redundant units that fail often but never down the system
            specs.append(chart_data.component_failure_pareto(per_run))
        # the same ladder split the Charts tab and run_chart draw
        from raptor.api.insight import measures
        specs.append(chart_data.downtime_waterfall(
            attribution, _operational(per_run),
            ladder=measures.availability_ladder(per_run) if per_run else None))
        if not rows:
            specs.append(chart_data.downtime_treemap(attribution))
        phases = attribution.get("phases") or []
        if any(row.get("share_of_downtime") for row in phases):
            specs.append(chart_data.phase_downtime(attribution))
            if any(row.get("causes") for row in phases):
                specs.append(chart_data.phase_cause_heatmap(attribution))
        specs.append(chart_data.outage_anatomy(attribution))
        if (attribution.get("outage_durations") or {}).get("count"):
            specs.append(chart_data.outage_histogram(attribution))
    story_findings = [finding if isinstance(finding, dict) else asdict(finding)
                      for finding in story_dict.get("findings") or ()]
    if findings is not None:
        story_findings = list(findings)
    elif rbd is not None:
        try:
            from raptor.api.advisor import advise_run
            story_findings = [finding.to_dict() for finding in advise_run(
                rbd, {}, per_run, attribution=attribution)]
        except Exception:  # noqa: BLE001 - keep the Story's findings
            pass
    measure_ids = _measure_ids_dict(story_dict, specs)
    return InsightSection(story=story_dict, charts=specs,
                          findings=story_findings,
                          suppressed=list(suppressed or ()),
                          measure_ids=measure_ids)


def _operational(per_run: list[dict]) -> float | None:
    if not per_run:
        return None
    from raptor.api.insight.measures import availability_ladder
    return availability_ladder(per_run)["operational"]["value"]


def _measure_ids_dict(story: dict[str, Any],
                      specs: list[Any]) -> list[str]:
    """The registry entries a reader of this report needs to know."""
    from raptor.api.insight.measures import for_metric
    ids: list[str] = []
    headline = story.get("headline") or {}
    measure = for_metric(str(headline.get("field") or ""))
    if measure is not None:
        ids.append(measure.id)
    for chip in story.get("numbers") or ():
        measure = for_metric(str(chip.get("field") or ""))
        if measure is not None and measure.id not in ids:
            ids.append(measure.id)
    for spec in specs:
        field_name = (spec.provenance or {}).get("measure")
        if field_name:
            measure = for_metric(str(field_name))
            if measure is not None and measure.id not in ids:
                ids.append(measure.id)
    return ids
