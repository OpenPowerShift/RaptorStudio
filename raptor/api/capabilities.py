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
)

SURFACES = ("api", "cli", "mcp", "gui")
