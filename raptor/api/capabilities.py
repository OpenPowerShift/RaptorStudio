"""Every user-facing capability added by the Advisor and insight work, with
the door to it on each surface: Python API, command line, MCP server, GUI.

`tests/unit/test_surface_parity.py` checks that each door exists, so a
capability cannot be added to one surface and forgotten on the others. A
surface that deliberately has no door says why in `absent`.

The logic lives in `raptor.api`; the other three surfaces only format what
it returns.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Capability:
    id: str
    summary: str
    api: str                       # dotted path of the function
    cli: str | None = None         # sub-command, optionally "command --flag"
    mcp: str | None = None         # tool name
    gui: str | None = None         # "command:<id>" or "widget:<dotted class>"
    long_running: bool = False     # must report progress and be cancellable
    absent: dict[str, str] = field(default_factory=dict)   # surface -> why there is no door


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "advise.model", "What looks wrong or unwise in a model",
        api="raptor.api.advisor.advise_model", cli="advise", mcp="advise",
        gui="command:view.advisor_toggle"),
    Capability(
        "advise.run", "How far to trust a run, and whether targets are met",
        api="raptor.api.advisor.advise_stored_run", cli="advise --target", mcp="advise",
        gui="command:view.advisor_toggle", long_running=True),
    Capability(
        "advise.rules", "List the Advisor's rules",
        api="raptor.api.advisor.list_rules", cli="advise --list-rules",
        mcp="list_advisor_rules",
        absent={"gui": "a documentation listing; in the application each finding "
                       "shows its own rule id and reasoning"}),
    Capability(
        "advise.fix", "Apply a finding's suggested fix",
        api="raptor.api.advisor.fixes.apply", mcp="apply_fix",
        gui="widget:raptor.gui.ui.advisor_dock.FindingCard",
        absent={"cli": "the finding prints the PATH=VALUE to pass to `raptor set`; "
                       "a one-step `advise --fix` is planned"}),
    Capability(
        "insight.attribution", "Run with downtime attribution",
        api="raptor.api.run.run", cli="explain", mcp="simulate",
        gui="widget:raptor.gui.ui.drivers_page.DriversPage", long_running=True),
    Capability(
        "insight.drivers", "What causes the downtime: elements, causes, outage paths",
        api="raptor.api.insight.drivers.read", cli="explain", mcp="downtime_drivers",
        gui="widget:raptor.gui.ui.drivers_page.DriversPage"),
    Capability(
        "insight.periods", "Availability period by period with percentiles (a bad year)",
        api="raptor.api.insight.drivers.periods", cli="explain --yearly",
        mcp="annual_availability",
        gui="widget:raptor.gui.ui.drivers_page.DriversPage"),
    Capability(
        "insight.capacity", "Lost capacity per element and capacity-weighted importance",
        api="raptor.api.insight.capacity_attribution.capacity_runs",
        cli="explain --capacity-rule", mcp="simulate",
        gui="widget:raptor.gui.ui.drivers_page.DriversPage", long_running=True),
    Capability(
        "insight.story", "The answer-first Story: headline, dots, drivers, findings",
        api="raptor.api.insight.story.from_run", cli="explain --story",
        mcp="run_story", gui="widget:raptor.gui.ui.story_page.StoryPage"),
    Capability(
        "whatif.scenario", "Compare a change with Base using paired trials",
        api="raptor.api.scenarios.runner.compare", cli="whatif",
        mcp="what_if", long_running=True,
        absent={"gui": "the Analyse tab's What If bar and Scenarios tab are "
                       "designed but not built; use 'raptor whatif' or MCP "
                       "what_if"}),
    Capability(
        "whatif.sensitivity", "Which inputs the answer is most sensitive to",
        api="raptor.api.scenarios.sensitivity.tornado",
        cli="sensitivity", mcp="sensitivity", long_running=True,
        absent={"gui": "the Analyse tab's Tornado view is designed but not "
                       "built; use 'raptor sensitivity' or MCP sensitivity"}),
    Capability(
        "optimise.spares", "The cheapest spare stock that meets a target",
        api="raptor.api.optimise.spares.optimise_spares",
        cli="optimise", long_running=True,
        absent={"mcp": "the MCP optimiser tools are designed but not built; "
                       "use 'raptor optimise spares'",
                "gui": "the Analyse tab's Optimise view is designed but not "
                       "built"}),
    Capability(
        "optimise.pm", "Is planned maintenance worthwhile, and at what interval",
        api="raptor.api.optimise.pm.optimise_pm", cli="optimise",
        long_running=True,
        absent={"mcp": "the MCP PM optimiser tool is designed but not built; "
                       "use 'raptor optimise pm'",
                "gui": "the Analyse tab's Optimise view is designed but not "
                       "built"}),
    Capability(
        "optimise.resources", "How many crews or tools are needed",
        api="raptor.api.optimise.resources.optimise_resources",
        cli="optimise", long_running=True,
        absent={"mcp": "the MCP resource sizer is designed but not built; "
                       "use 'raptor optimise resources'",
                "gui": "the Analyse tab's Optimise view is designed but not "
                       "built"}),
    Capability(
        "dist.judgement", "Fit a life law from judged quantiles",
        api="raptor.api.distributions.from_quantiles", cli="fit",
        absent={"mcp": "the judgement wizard is a command-line and editor "
                       "affordance; an assistant can call the same fit with "
                       "the API",
                "gui": "the distribution editor's 'Estimate from judgement' "
                       "dialog is designed but not built; use 'raptor fit'"}),
    Capability(
        "edit.duplicate", "Duplicate elements with fresh ids and names",
        api="raptor.api.edit.duplicate_elements", cli="edit",
        mcp="duplicate_elements", gui="command:home.duplicate"),
    Capability(
        "edit.paste_data", "Copy one block's data groups onto others",
        api="raptor.api.edit.copy_element_data", cli="edit",
        mcp="copy_element_data", gui="command:home.paste_data"),
    Capability(
        "view.command_search", "Run any command by name",
        api="raptor.gui.quick_match.rank",
        cli=None, mcp=None, gui="command:view.command_search",
        absent={"cli": "an interactive search is a GUI affordance; the "
                       "commands themselves have CLI forms",
                "mcp": "a command by name is not a task an assistant needs; "
                       "the underlying tools are exposed directly"}),
    Capability(
        "model.quick_add", "Add an element at the cursor by name",
        api="raptor.gui.quick_match.rank",
        cli=None, mcp=None, gui="command:model.quick_add",
        absent={"cli": "adding elements is done with the model-building "
                       "commands (raptor edit, set and build tools)",
                "mcp": "use add_block/add_node/add_event/add_hierarchy"}),
    Capability(
        "edit.replace", "Find and replace in a model table column",
        api="raptor.api.tables.replace_in_table", cli="tables",
        mcp="replace_in_table", gui="command:data.find_replace"),
    Capability(
        "insight.ribbon", "All trials as one state ribbon",
        api="raptor.api.insight.charts.ensemble_ribbon", cli="explain",
        mcp="run_chart", gui="widget:raptor.gui.ui.charts_page.ChartsPage"),
    Capability(
        "library.part_types", "Reuse a block's data as a named part type",
        api="raptor.api.library.links.link_block", cli="library",
        mcp="list_part_types",
        gui="widget:raptor.gui.ui.library_dock.LibraryDock"),
    Capability(
        "library.assemblies", "Save a group of elements and insert fresh copies",
        api="raptor.api.library.assembly.insert_assembly", cli="library",
        mcp="insert_assembly",
        gui="widget:raptor.gui.ui.library_dock.LibraryDock"),
    Capability(
        "insight.capacity_periods",
        "Capacity-weighted availability year by year (flow delivered / flow possible)",
        api="raptor.api.insight.periods.period_summary", cli="explain --yearly",
        mcp="annual_availability",
        gui="widget:raptor.gui.ui.drivers_page.DriversPage"),
    Capability(
        "insight.phases", "Downtime by mission phase: availability, share and driver per phase",
        api="raptor.api.insight.attribution.model_phases", cli="explain",
        mcp="downtime_drivers",
        gui="widget:raptor.gui.ui.charts_page.ChartsPage"),
    Capability(
        "export.result_tables",
        "Export drivers, lost capacity and year-by-year results as tables",
        api="raptor.api.run_tables.bundle_from_run", cli="tables --drivers",
        mcp="export_tables", gui="command:file.export_tables"),
    Capability(
        "insight.precision", "Run until the answer is precise enough",
        api="raptor.api.scenarios.stopping.run_to_precision",
        cli="run --precision", mcp="simulate", long_running=True,
        absent={"gui": "the Run panel still takes a fixed run count; use the "
                       "command line or MCP precision setting until the "
                       "radio ships"}),
    Capability(
        "export.report", "The run's report as a PDF or one HTML file: story, charts, diagram",
        api="raptor.api.report.write_report", cli="explain --report",
        mcp="export_report", gui="command:file.export_pdf"),
    Capability(
        "insight.charts", "The chart set: dots, Pareto, waterfall, ladder, histogram",
        api="raptor.api.insight.charts.chart_named",
        cli="explain --chart", mcp="run_chart",
        gui="widget:raptor.gui.ui.charts_page.ChartsPage"),
    Capability(
        "insight.chart_export",
        "Charts for reports and slides: page sizes, no shortened labels, small items folded",
        api="raptor.render.charts_svg.render",
        cli="explain --chart-page", mcp="run_chart",
        gui="widget:raptor.gui.ui.charts_page.ChartsPage"),
    Capability(
        "insight.canvas_downtime",
        "Each element's share of downtime, main cause and certainty on the diagram",
        api="raptor.api.insight.canvas.canvas_layer", cli="explain",
        mcp="downtime_drivers", gui="command:results.downtime_overlay"),
    Capability(
        "insight.canvas_lost_capacity",
        "Each element's share of the lost flow on the diagram, links as wide as their flow",
        api="raptor.api.insight.canvas.canvas_layer", cli="explain",
        mcp="downtime_drivers", gui="command:results.lost_capacity_overlay"),
)

SURFACES = ("api", "cli", "mcp", "gui")
