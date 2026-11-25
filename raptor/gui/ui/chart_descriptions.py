"""One line about each chart and each group, for the group overview.

The Charts page's list names a chart; the overview that opens when a group
heading is chosen says what each chart answers, so a reader can pick by
question.  The text is English here and translated where it is shown
(`i18n.tr`); `texts()` hands every string to the locale catalogue, which
cannot see a table.
"""
from __future__ import annotations

#: chart key -> what it shows, in one line
DESCRIPTIONS: dict[str, str] = {
    "trial-outcome": "One dot per run. A tight cluster means a dependable result; a wide spread means luck matters.",
    "ecdf": "Read across from a value to the share of runs that got that far. Steep and narrow is good.",
    "reliability-curve": "Falls as runs fail for the first time. Use it to judge a mission or a warranty period.",
    "availability-ladder": "Each step down is a cost: planned maintenance, then spares and logistics. Shows what to fix.",
    "convergence": "A flat line means more runs will not move the average. Still moving? Run more.",
    "hierarchy-scorecard": "One panel per hierarchy with its share of downtime. Start here to see who to look into.",
    "hierarchy-pareto": "The longest bar is the biggest source of downtime. Work on that hierarchy first.",
    "hierarchy-map": "Bigger area means more downtime, and parents hold their children. Find the hot spot at a glance.",
    "hierarchy-overlay": "Steep lines are where downtime piles up fastest, and when. Compare up to four hierarchies.",
    "hierarchy-small-multiples": "Rankings that change between runs are luck, not a real weakness. Stable ones are real.",
    "hierarchy-hops": "Play through the runs to see how often the worst hierarchy changes. Few changes mean a clear culprit.",
    "hierarchy-delta": "Bars up are worse than your saved baseline, bars down are better. Use it to check a change.",
    "hierarchy-heatmap": "Dark cells are a hierarchy hurt by a cause. Look for one dark column to fix everywhere.",
    "hierarchy-phase-heatmap": "Dark cells mark a hierarchy struggling in a phase. Useful when the mission has distinct stages.",
    "hierarchy-anatomy": "Each path is a chain from a cause up to the system going down. Thick paths happen most.",
    "hierarchy-ribbon": "One row per hierarchy, coloured when down. Overlaps show failures that happen together.",
    "availability-through-run": "Each drop is an outage. See when they cluster and how fast availability recovers.",
    "interval-availability": "Availability of each slice of the run, not the running average. Shows bad stretches clearly.",
    "availability-band": "The shaded band holds most runs. A wide band early on settles as the run goes on.",
    "capacity-through-run": "Steps down are lost capacity. Compare the dips with the line for full capacity.",
    "capacity-band": "The band holds most runs. Staying low means capacity is lost often, not just once.",
    "component-failures": "A long top bar is one weak component. Consider spares, redundancy or a better part.",
    "downtime-pareto": "Bars show downtime each element caused. A few long bars mean fixing a few things pays most.",
    "downtime-waterfall": "Each bar is a loss: repair, maintenance, then logistics. The biggest drop is where to act.",
    "downtime-treemap": "Bigger area means more downtime. Handy when there are too many elements for a bar chart.",
    "phase-downtime": "Tall bars are phases with the most downtime. Check whether it lands in a critical phase.",
    "phase-cause-heatmap": "Dark cells are a cause in a phase. Shows whether the same problem hits all phases or just one.",
    "outage-anatomy": "Follows one typical outage through failure, waiting and repair. Long waits mean spares or crews.",
    "ensemble-ribbon": "Each row is a run, coloured when down. Lines of colour in the same place mean a common time.",
    "outage-histogram": "Most outages short and a few very long? The long tail is where to look.",
    "frequency-duration": "Each bubble is an element: how often it takes the system down against how long each outage lasts.",
    "lost-capacity-pareto": "Bars show capacity each element lost. Differs from downtime when a failure only part-stops output.",
    "capacity-vs-downtime": "Each dot is an element: its downtime share against lost output. Dots at the left lose output without stopping.",
    "capacity-waterfall": "Each bar is capacity lost to one cause. The biggest bar is the one to tackle.",
    "lost-capacity-treemap": "Bigger area means more capacity lost. Handy when many elements share the loss.",
    "capacity-sankey": "Thick bands are the main routes of loss. Read left to right from hierarchy to cause.",
    "annual-fan": "Each year's spread across runs. A wide band means bad years happen; a falling middle means ageing.",
    "annual-dots": "Dots below the target line are missed years. Counts how often the target is missed.",
    "annual-capacity-fan": "Each year gets its own spread of delivered capacity. Shows if output slips as years pass.",
    "cost-pareto": "Long bars are the elements that cost most. Compare them with their downtime before cutting.",
    "cost-treemap": "Bigger area means higher cost. Helps spot spending in small elements added up.",
    "cost-categories": "Splits life-cycle cost into its categories. Find the biggest one and start there.",
    "cost-cumulative": "The slope is spend per time. A steepening line means costs are rising as the system ages.",
    "cost-annual": "One bar per year. Use it to budget, and to see if costs climb as parts wear.",
    "importance-quadrant": "Top right matters both when it fails and when improved. Spend spares and money there.",
}

#: group name -> what its charts have in common
GROUP_BLURBS: dict[str, str] = {
    "All charts": "Every chart this run can draw, group by group. Click one to open it.",
    "The answer": "Start here: did the system meet its target, and how sure can you be?",
    "By hierarchy": "Find which part of the model is responsible, by hierarchy.",
    "Over time": "See when the trouble happens. Needs the recorded plots turned on.",
    "Why it goes down": "Trace downtime to the elements and causes behind it, then decide what to fix.",
    "Lost capacity": "For models that deliver flow: what failures cost in output, not just uptime.",
    "Year by year": "Check each year on its own. Needs a run at least a year long.",
    "Cost": "See where money goes, by element, category and year. Needs cost analysis.",
    "What matters most": "Rank elements by how much improving them would help the whole system.",
}

#: shown under a chart that cannot be drawn
UNAVAILABLE = "Not available"


def reasons() -> list[str]:
    """Every fixed sentence the Charts page gives for a chart it cannot draw,
    and the button that fixes it (the catalogue cannot see them: they are
    built from `raptor.api.run_needs` and the table in `charts_page`)."""
    from raptor.api import run_needs
    hint = run_needs.gui_hint
    out = [
        "No runs yet: run the model (F5).",
        "This chart is system-wide and has no per-hierarchy series. Choose "
        "System, or use the hierarchy overlay, small multiples, heatmap or "
        "state ribbon.",
        "Needs at least 2 runs.",
        "This model has no hierarchy roll-up.",
        "The run kept no event log: turn on Detailed event log and run again.",
        "Work out the downtime drivers first (the hierarchy roll-up comes "
        "from the same replay).",
        "This run has no hierarchy roll-up: rerun it with attribution and a "
        "hierarchy roll-up.",
        "Work out the downtime drivers first.",
        "This run has no phase breakdown: rerun it with phases and "
        "attribution on.",
        "The model has no mission phases.",
        "This run kept no per-hierarchy outage timeline: run again with "
        "downtime attribution on.",
        "These hierarchies overlap (a parent includes its child): choose "
        "different branches, or one level.",
        "No hierarchy has downtime to compare in this run.",
        "This run has no per-hierarchy phase breakdown: rerun it with "
        "phases and attribution on.",
        "Set a baseline run first, then run again.",
        "The run is shorter than one recording interval, so no interval was "
        "completed. Lower the availability plot's interval under Run panel "
        "\u25b8 Recording, then run again.",
        "Needs the availability plot of at least 2 runs.",
        "Needs the capacity plot of at least 2 runs.",
        "Work out which elements caused each outage first (it replays this "
        "run's event log).",
        "This result has no per-component failure counts.",
        "Rerun with phases and attribution on to split each phase's downtime "
        "by cause.",
        "No outage timeline was kept for this run.",
        "No capacity was lost in these runs, so there is nothing to show.",
        "No outage was recorded, so every year was fully available: there "
        "is nothing to draw year by year.",
        "Needs a run at least one whole year long (8,760 hours).",
        "This chart cannot be drawn from this result.",
        "Work out the downtime drivers",
        "These charts are drawn from the availability plot. "
        + hint("availability_plot"),
        "These charts are drawn from the capacity plot. "
        + hint("capacity_plot"),
        "These charts replay the event log. " + hint("attribution"),
        "Lost capacity needs the capacity analysis. " + hint("capacity"),
        "Needs the capacity analysis as well. " + hint("capacity"),
        "Needs the cost analysis. " + hint("cost"),
        "Needs the interim cost report. " + hint("cost_plot"),
        "The quadrant needs RAW and RRW, so importance mode Both. "
        + hint("importance"),
        "Needs importance measures (mode Both). " + hint("importance"),
    ]
    for keys in (("attribution",), ("capacity",), ("attribution", "capacity"),
                 ("availability_plot",), ("capacity_plot",), ("cost",),
                 ("cost_plot",), ("cost", "cost_plot"), ("importance",)):
        out.append(run_needs.button_text(*keys))
    return out


def describe(key: str) -> str:
    return DESCRIPTIONS.get(key, "")


def texts() -> list[str]:
    """Every string here, for the locale catalogue."""
    return [*DESCRIPTIONS.values(), *GROUP_BLURBS.values(), UNAVAILABLE,
            *reasons()]
