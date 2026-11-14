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
    from raptor.api.run_tables import read_per_run

    story = story_from_run(workspace, run_id)
    per_run = read_per_run(run_dir(workspace, run_id), run_id) or []
    attribution = None
    try:
        attribution = drivers.read(workspace, run_id)
    except Exception:  # noqa: BLE001 - a report without drivers is still a report
        attribution = None
    return from_parts(story, per_run, attribution=attribution, rbd=rbd,
                      suppressed=suppressed)


def from_parts(story: Any, per_run: list[dict],
               *, attribution: dict[str, Any] | None = None,
               rbd: Any = None, charts_in: list[Any] | None = None,
               findings: list[dict[str, Any]] | None = None,
               suppressed: list[dict[str, Any]] | None = None
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
        specs.append(chart_data.trial_outcome(
            per_run, field_name, label=label, target=target, units=units))
        from raptor.api.insight import measures
        specs.append(chart_data.availability_ladder(
            measures.availability_ladder(per_run)))
    if not provided and attribution:
        specs.append(chart_data.downtime_pareto(attribution))
        specs.append(chart_data.downtime_waterfall(
            attribution, _operational(per_run)))
        specs.append(chart_data.downtime_treemap(attribution))
        specs.append(chart_data.outage_anatomy(attribution))
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
