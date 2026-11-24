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
    "trial-outcome": "How the result varies from one run to the next, against the target.",
    "ecdf": "The chance that a run reaches or beats any given value.",
    "reliability-curve": "The chance the system is still running without failing, as time passes.",
    "availability-ladder": "Availability from the repair-only ideal down to what the run achieved.",
    "convergence": "Whether more runs would still change the average.",
    "hierarchy-scorecard": "Each hierarchy's availability and downtime side by side.",
    "hierarchy-pareto": "Hierarchies ranked by the downtime they cause.",
    "hierarchy-map": "Where the downtime sits in the hierarchy, as nested areas.",
    "hierarchy-overlay": "How each chosen hierarchy's downtime builds up over the run.",
    "hierarchy-small-multiples": "Whether the ranking of hierarchies holds from run to run.",
    "hierarchy-hops": "Which hierarchy is worse in each run, one run at a time.",
    "hierarchy-delta": "How each hierarchy's downtime changed against a saved baseline.",
    "hierarchy-heatmap": "Which causes hurt each hierarchy most.",
    "hierarchy-phase-heatmap": "Which mission phases hurt each hierarchy most.",
    "hierarchy-anatomy": "The paths an outage takes up through the hierarchies.",
    "hierarchy-ribbon": "When each hierarchy was down during one run.",
    "availability-through-run": "Availability across the length of one run.",
    "interval-availability": "Availability in each recording interval of a run.",
    "availability-band": "The spread of availability over time across every run.",
    "capacity-through-run": "Delivered capacity across the length of one run.",
    "capacity-band": "The spread of delivered capacity over time across every run.",
    "component-failures": "Which components fail most often.",
    "downtime-pareto": "The elements that cause the most downtime.",
    "downtime-waterfall": "Where availability is lost, step by step, from the ideal.",
    "downtime-treemap": "Downtime by element, as areas.",
    "phase-downtime": "How much downtime falls in each mission phase.",
    "phase-cause-heatmap": "Which causes bite in which phase.",
    "outage-anatomy": "What a typical outage looks like from start to finish.",
    "ensemble-ribbon": "System state through time, one band per run.",
    "outage-histogram": "How outage lengths are spread.",
    "frequency-duration": "Whether downtime comes from frequent short outages or a few long ones.",
    "lost-capacity-pareto": "The elements that lose the most capacity.",
    "capacity-vs-downtime": "Whether an element's lost capacity follows its downtime.",
    "capacity-waterfall": "Where capacity is lost, by cause, from the full rating.",
    "lost-capacity-treemap": "Lost capacity by element, as areas.",
    "capacity-sankey": "How lost capacity flows from hierarchy to cause.",
    "annual-fan": "Availability in each year of the run, with the spread across runs.",
    "annual-dots": "How often a year falls below the target.",
    "annual-capacity-fan": "Capacity delivered in each year, with the spread across runs.",
    "cost-pareto": "The elements that cost the most.",
    "cost-treemap": "Cost by element, as areas.",
    "cost-categories": "Operating cost split by category.",
    "cost-cumulative": "Operating cost building up over the run.",
    "cost-annual": "Operating cost in each year.",
    "importance-quadrant": "Which elements matter most to availability, and which would pay back.",
}

#: group name -> what its charts have in common
GROUP_BLURBS: dict[str, str] = {
    "The answer": "How the system did, over every run.",
    "By hierarchy": "Where the downtime sits, hierarchy by hierarchy.",
    "Over time": "How the system behaved as the run went on.",
    "Why it goes down": "Which elements and causes bring the system down.",
    "Lost capacity": "What the downtime cost in delivered capacity.",
    "Year by year": "How each year of the run fared.",
    "Cost": "Where the money goes.",
    "What matters most": "Which elements to look after first.",
}

#: shown under a chart that cannot be drawn
UNAVAILABLE = "Not available"


def describe(key: str) -> str:
    return DESCRIPTIONS.get(key, "")


def texts() -> list[str]:
    """Every string here, for the locale catalogue."""
    return [*DESCRIPTIONS.values(), *GROUP_BLURBS.values(), UNAVAILABLE]
