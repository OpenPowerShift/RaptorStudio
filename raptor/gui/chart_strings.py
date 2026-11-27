"""The chart strings the GUI translates but the catalogue scan cannot see.

Kept free of imports so `raptor.gui.i18n` can read it."""
from __future__ import annotations

#: Sources the catalogue scan cannot see (they are built in `raptor.api`).
#: Titles first, then the pieces a subtitle is made of.
SOURCES: tuple[str, ...] = (
    # button texts set through `ChartsPage._retext` (not a literal setText)
    "Baseline: none", "Baseline: set", "Compare: top 4", "Scope: System",
    "Compare: top 5", "Compare: top 6", "Compare: top 8", "Compare: top 10",
    "Compare: top 12",
    # the Show control's tooltip notes (raptor.api.insight.ranking)
    "Drawn as bubbles, which overlap past about 50: the All items table "
    "lists every element.",
    "A line chart stays readable up to about six lines: the All items table "
    "lists every hierarchy.",
    "One panel each: more than 12 do not fit a page.",
    "One strip each: more than 12 do not fit a page.",
    # the all-items table (raptor.api.insight.ranking)
    "rank", "name", "level / path", "share", "cumulative share", "main cause",
    "standard error", "share of downtime", "lost energy", "outages a run",
    "members", "starters", "on the chart", "before", "after", "change",
    "status", "Share is of the items listed here.",
    "Ranked by the size of the change.",
    # list headings
    "All charts", "The answer", "By hierarchy", "Over time", "Why it goes down",
    "Lost capacity", "Year by year", "Cost", "What matters most",
    # chart titles
    "Run-to-run outcome", "Chance of reaching a value", "Reliability over time",
    "Availability ladder", "Has the run settled?", "Hierarchy scorecard",
    "Hierarchy Pareto", "Where the downtime sits", "Hierarchy treemap",
    "Hierarchy sunburst",
    "When did each hierarchy's downtime build up?",
    "Is the hierarchy ranking stable?", "Which hierarchy is worse, run by run?",
    "Hierarchy change against baseline", "Hierarchy by cause",
    "Hierarchy by phase", "Outage paths across hierarchies",
    "When was each hierarchy down?", "Which components fail most often?",
    "Availability year by year", "How often is a year below target?",
    "Capacity delivered year by year", "Where the money goes", "Cost treemap",
    "Importance quadrant", "Availability through a run",
    "Availability interval by interval", "Availability across all runs",
    "Capacity through a run", "Capacity across all runs",
    "Who causes the downtime", "Where availability is lost",
    "From full availability to the achieved one",
    "What each kind of availability keeps", "Downtime treemap",
    "Downtime by phase", "Phase by cause", "What an outage looks like",
    "Run-to-run state ribbon", "Run-to-run system state",
    "How long is an outage?", "Frequent or long outages?",
    "Who loses the capacity", "Downtime against lost capacity",
    "Downtime share against lost capacity", "Lost capacity by cause",
    "Lost capacity treemap", "Lost capacity by hierarchy and cause",
    "Cost by category", "Cumulative operating cost over time",
    "Operating cost per year", "Run-to-run availability",
    "Run-to-run dependability", "Chance of reaching availability",
    "Chance of reaching dependability",
    # measure words inside titles
    "availability", "dependability", "share of system downtime",
    # subtitle pieces
    "one dot per quantile slice of the runs",
    "read across from a share of runs to the value they reached",
    "the chance the system has not yet gone down by each time",
    "any outage counts, planned maintenance included", "Kaplan–Meier",
    "a run still up at its end is censored",
    "all component failures, including those that did not down the system",
    "each outage charged to the element that started it, stacked by what "
    "that element was doing",
    "each step is a share of the loss, in the availability ladder's order",
    "each step is a cause's share of the loss",
    "the gap between rungs is what the next cause takes away",
    "log scale: many short outages or a few long ones",
    "x = system outages it starts a run, y = how long they last, area = "
    "share of downtime",
    "x = share of outages started, y = outage length relative to the "
    "average (1 = average), area = share of downtime",
    "x = RAW (what losing it costs), y = RRW (what fixing it buys)",
    "the band is +/- 2 standard errors", "it should stop moving",
    "life-cycle cost a run", "capacity-related rows are separated from the rest",
    "mean cumulative cost in each operating category",
    "one-off capital is separate",
    "capacity-related costs are the lost-flow and system-downtime lines",
    "across the runs", "mean cost per run, by element, largest first",
    "capacity share against downtime share, same element order",
    "points far above the diagonal remove more capacity than their downtime "
    "suggests",
    "each step is a cause's share of the summed marginal contributions",
    "each step is a cause's share of the lost energy",
    "the box holds the middle half, the whiskers 9 in 10, the bar is the median",
    "the dark band holds the middle half, the light one 9 in 10, the line is "
    "the median",
    "each dot is one run-year",
    "each dot is one of the quantiles P1 to P99 of the run-years",
    "one card per hierarchy: share of system downtime, ±2 standard errors, "
    "members and starters",
    "badges compare with the baseline run",
    "share of system downtime, stacked by main cause",
    "outer blocks are hierarchies, inner cells are elements",
    "area = share of system downtime", "colour = main cause",
    "click a cell to locate",
    "area = share of the lost flow",
    "area = mean life-cycle cost per run", "colour = largest part of it",
    "cumulative system downtime caused by each hierarchy",
    "system is the dashed reference", "each dot is a run",
    "the dashed line is each hierarchy's mean",
    "change in downtime a run", "bars right are worse",
    "each row adds to 100 % of that hierarchy's downtime",
    "share of each hierarchy's downtime in each phase", "rows add to 100 %",
    "each block is a hierarchy", "colour = what started its outages",
    "each row is a hierarchy",
    "green = no downtime attributed to it (the system may be down elsewhere)",
    "red = down because of it",
    "inner ring: top level", "each next ring is one level deeper",
    "share of all system downtime in each phase, in phase order",
    "non-mission phases included",
    "share of each phase's downtime by cause",
    "each column is the next step", "width = share of downtime",
    "one row per trial, sorted by downtime", "red = system down",
    "the share of time so far spent fully up, degraded and down",
    "the share of each recording interval the system was up",
    "each run's availability so far: where the runs agree",
    "flow delivered against a fully working system",
    "flow at each moment: where the runs agree",
    "first step: the element; next: what kept it down",
    "lost flow → cause", "lost flow → hierarchy → cause",
    "generated flow → delivered and lost → cause",
    "generated flow → delivered and lost → hierarchy → cause",
    "top level", "every level",
    # the Sankey's fixed nodes
    "Lost", "Lost capacity", "Delivered", "Generated energy",
    "Not attributed", "Not in this selection",
    # the causes an outage is charged to (`CAUSE_LABELS` in the API)
    "Hands-on repair", "Waiting for a spare",
    "Waiting for people or equipment", "Logistics delay",
    "Planned maintenance", "Waiting to start maintenance",
    "Held by another element", "Not repairable", "Other",
    # axis titles (a trailing "(hours)" is a unit and is translated apart)
    "and", "more", "baseline %1", "%1 caused %2",
    # clauses of the run-to-run caption, read apart from the others
    "%1: %2", ", mean %1",
    "; the middle half of the runs lie between %1 and %2 (Q1 to Q3)",
    "; %1 of %2 (%3) met the target", "system", "runs", "time", "simulated time", "cumulative downtime",
    "change in downtime", "share of runs", "share of downtime", "share of loss",
    "fraction", "cause", "hierarchy", "hierarchies", "element", "phase", "step",
    "year", "duration", "outages", "width", "height", "reliability",
    "component failures a run", "outages it started a run",
    "mean outage length", "run-years", "operating cost a year",
    "operating cost", "running mean", "share of the total",
    "availability (axis zoomed)",
    # marks and key words drawn inside the chart
    "Q1", "Q3", "median", "1 in 4", "3 in 4", "half", "Q1, median, Q3",
    "lost capacity", "downtime", "system up", "system down",
    "± 2 standard errors", "= no change", "new (not in baseline)",
    "no spread to judge", "each dot a run", "first",
    "No chart for this result.", "Nothing to show for this run.",
    "Make this panel taller (or float it) to see the chart.",
    "non-mission", "the baseline", "at least", "at most",
    "planned maintenance", "logistics, spares, crews",
    "flow generated", "full capacity (highest recorded)",
    "capacity lost", "flow delivered", "capacity available",
    "every run (lowest to highest)", "8 runs in 10", "mean flow",
    "capacity target", "flow", "share of lost capacity", "share of lost energy",
    "capacity-weighted availability", "the lost flow", "the cost",
    "system downtime", "flow \u00d7 time", "failures a run",
    # the note under the charts over time (`results_graphs_mixin`)
    "The charts over time use all %1 recorded rows, from %2; each run is "
    "reduced to about %3 points for drawing, keeping its highs, lows and "
    "ends. Export \u25b8 Save the recorded plot rows keeps every row.",
    "Inherent (repair only)", "Achieved (+ planned maintenance)",
    "Operational (+ logistics, spares, crews)",
    # why a chart cannot show a tag (`ChartsPage._limit_for_tags`)
    "Run-by-run shares are kept for a whole tag, not for a tag inside a "
    "hierarchy: choose the tag itself.",
    "Tags overlap and cut across hierarchies, so this chart cannot show "
    "one. Use a ranking, the run-to-run charts or Compare.",
    "The outage timeline is kept per hierarchy, not per tag: use small "
    "multiples or run-by-run for tags.",
    # counted nouns in a caption
    "%1 run", "%1 runs", "%1 hierarchy", "%1 hierarchies",
    "%1 tag", "%1 tags", "%1 hierarchies and tags",
    # small-multiples subtitle pieces when tags are compared
    "one panel per tag", "one panel per hierarchy or tag", "%1 part",
    "%1 parts",
)

#: Titles and subtitle pieces built from model data or a number.  `%1` ..
#: are the pieces; the tuple lists which are themselves UI words (see
#: `raptor.gui.i18n.TEMPLATES`).
TEMPLATES: dict[str, tuple[int, ...]] = {
    # the Charts page's controls
    "Compare: %1 selected": (),
    "Target: %1": (),
    "Capacity target: %1": (),
    "year %1": (),
    "1 in 10 longer (%1)": (),
    "Scope: %1": (),
    "Run %1 of %2": (),
    "All %1 runs": (),
    "Run-to-run %1": (1,),
    "Chance of reaching %1": (1,),
    "Importance on %1": (),
    "Pareto inside %1": (),
    "Causes inside %1": (),
    "Phases inside %1": (),
    "Inside %1": (),
    "Availability through run %1": (),
    "Availability interval by interval, run %1": (),
    "Capacity through run %1": (),
    "When was each hierarchy down? (run %1)": (),
    "When was %1 down? (run %2)": (),
    "scope: %1": (),
    "level %1": (),
    "top %1 levels": (),
    "top %1 of %2": (),
    "the other %1 together %2": (),
    "one panel per hierarchy at %1": (1,),
    "each run's share of system downtime at %1": (1,),
    "share of %1's downtime, stacked by main cause": (),
    "each dot is %1 of the %2 run-years": (),
    "area = share of %1": (),
    "%1 of the %2 runs, evenly through the ranking, sorted by downtime": (),
    "Outage paths inside %1": (),
    # drawn by the chart widget
    "\u25bc %1 pts better": (), "\u25b2 %1 pts worse": (),
    "\u00b1 %1 pts": (),
    "%1 members \u00b7 %2 started outages": (),
    "%1 member \u00b7 %2 started outages": (),
    "%1 members \u00b7 %2 started outage": (),
    "%1 member \u00b7 %2 started outage": (),
    "target %1": (), "median %1": (),
    "capacity target %1 = %2": (),
    "target %1: %2 of runs reach it": (),
    "then (step %1)": (),
    "%1 (log scale)": (),
    "%1 \u00b7 drag across or Ctrl+wheel to zoom in": (),
    "%1 \u00b7 drag across or Ctrl+wheel to zoom in, double-click for the "
    "whole run": (),
    "%1 of %2 shown": (),
    "%1 of %2 shown: make the panel taller to see more": (),
    "%1 more not shown: make the panel taller": (),
    "%1 of %2 runs, best at the top": (),
    "%1 run, best at the top": (),
    "%1 runs, best at the top": (),
    "%1 (non-mission)": (),
}

#: Caption sentences, built in `raptor.api.insight.charts` from model data.
#: Each value says what the pieces are, one letter per `%N`: ``d`` data (a
#: name or a number, kept as written), ``q`` a quantity (a number, and a unit
#: word that is translated), ``w`` an app word (translated, or the caption
#: stays English), ``a`` "at least" or "at most", ``m`` a measure word
#: (translated when known), ``c`` a counted noun ("3 hierarchies"), ``b`` a
#: baseline phrase, ``l`` a list of "NAME caused QTY".  A caption is read
#: sentence by sentence; one sentence that matches no template leaves the
#: whole caption in English.
CAPTIONS: dict[str, str] = {
    # hierarchy scorecard, Pareto, treemap, map
    "%1 holds the largest share of system downtime (%2).": "dd",
    "No hierarchy has downtime in these runs.": "",
    # added under any chart of a tag once the tags changed after the run
    "Tag results are fixed when the model runs; the tags changed since "
    "this run, so run again to update them.": "",
    "This model has no hierarchy roll-up.": "",
    "%1 of system downtime is not inside a hierarchy.": "d",
    "Showing %1 of %2 hierarchies.": "dd",
    "Showing %1 of %2 parts.": "dd",
    "No downtime in %1.": "d",
    "%1 had no downtime.": "d",
    "%1 causes the largest share (%2 of system downtime).": "dd",
    "%1 causes the largest share (%2 of %3's downtime).": "ddd",
    "%1 causes the largest share (%2).": "dd",
    "%1 of the downtime was started by no single element.": "d",
    "No single element is behind the downtime.": "",
    "No element inside %1 caused downtime.": "d",
    "Inside %1: %2 is the largest part (%3).": "ddd",
    "%1 has no downtime below it in this run.": "d",
    "Most system downtime is not inside a hierarchy.": "",
    "%1 is the largest hierarchy (%2 of system downtime).": "dd",
    # overlay and small multiples
    "By the end of the run, %1.": "l",
    "Showing the top %1 of %2 hierarchies; use Compare to choose.": "dd",
    "%1; each dot is one run's share of system downtime.": "c",
    # HOPs
    "%1 causes the most downtime in %2 of %3 runs.": "ddd",
    "%1: mean share of system downtime %2 over %3 runs.": "ddd",
    "Run %1 of %2: %3.": "ddd",
    # delta
    "%1 is down %2 a run more than %3 (\u00b1 %4).": "dqbq",
    "%1 is down %2 a run more than %3.": "dqb",
    "%1 is down %2 a run less than %3 (\u00b1 %4).": "dqbq",
    "%1 is down %2 a run less than %3.": "dqb",
    "%1 has the same downtime as %2 (\u00b1 %3).": "dbq",
    "%1 has the same downtime as %2.": "db",
    # heatmaps
    "%1 is mostly %2.": "dw",
    "%1 holds the most hierarchy downtime; all of it is in %2 (%3 of its "
    "downtime).": "ddd",
    "%1 holds the most hierarchy downtime; most of it is in %2 (%3 of its "
    "downtime).": "ddd",
    "No hierarchy with downtime in this run has a per-phase breakdown; rerun "
    "with phases and attribution on.": "",
    "No downtime inside %1 is split by phase.": "d",
    # outage paths and ribbon
    "%1 starts the largest share (%2 of downtime).": "dd",
    "The commonest start is %1 (%2 of downtime).": "dd",
    "No outage was recorded.": "",
    "%1; %2 of downtime attributed to them (system down %3).": "cdq",
    "%1; %2 of downtime attributed to them.": "cd",
    "No downtime in this run is attributed to the named hierarchies; the "
    "unassigned row is the %1 the system was down.": "q",
    # the answer: run-to-run, chance of reaching, reliability, convergence
    "The one run reached %1.": "d",
    "Every run reached %1.": "d",
    "All %1 runs reached %2 %3; the best reached %4.": "dadd",
    "9 runs in 10 reached %1 %2; half reached %3 %4.": "adad",
    "%1 of runs reached the target %2.": "dd",
    "No runs.": "",
    "From %1, Kaplan\u2013Meier estimate.": "c",
    "From %1, Kaplan\u2013Meier estimate; %2 ended before a failure and are "
    "censored.": "cd",
    "Reliability is %1 or less by %2 and %3 or less by %4.": "dqdq",
    "Reliability is %1 or less by %2.": "dq",
    "The last failure was at %1.": "q",
    "%1 runs first went down at exactly %2: a scheduled outage such as "
    "planned maintenance, not a random failure.": "dq",
    "No run failed.": "",
    "No run recorded a time to first failure.": "",
    "After %1 runs the mean %2 is %3 \u00b1 %4 points (2 standard errors, "
    "about 95 %).": "dmdd",
    "After %1 runs the mean %2 is %3 \u00b1 %4 (2 standard errors, about "
    "95 %).": "dmdd",
    "Median %1.": "d",
    "Not enough runs to estimate the error.": "",
    "The system never went down in %1, so there is nothing to show.": "c",
    "The system never went down, so there is nothing to show.": "",
    "The largest single loss is %1 (%2 points).": "wd",
    "The steps use the same Red-time split as the availability ladder.": "",
    "%1 took nothing away in these runs.": "d",
    "No availability reported.": "",
    # the capacity charts over time
    "Run %1 delivered %2 of its %3; it ran below full %4 of the time, lowest "
    "flow %5 of %6.": "ddwddd",
    "Run %1: lowest flow %2.": "dd",
    "%1 delivered %2 of their %3; the mean flow fell as low as %4 of %5.": "cdwdd",
    "It met the capacity target.": "",
    "It missed the capacity target %1.": "d",
    "%1 of the %2 runs delivered at or above the capacity target.": "dd",
    # lost capacity by cause, and the capacity target's verdict
    "The largest loss is %1 (%2 of lost energy).": "wd",
    "The largest loss is %1 (%2 of the summed contributions).": "wd",
    "Delivered %1 of the generated flow: it met the capacity target %2.": "dd",
    # the lost-capacity Sankey
    "Lost flow is %1 flow \u00d7 time a run; the largest hierarchy is %2.": "dd",
    "Lost flow is %1 flow \u00d7 time a run; the largest cause is %2.": "dw",
    "Lost flow is %1 flow \u00d7 time a run.": "d",
    "%1 of the lost flow is not inside these hierarchies.": "d",
    "%1 of the lost flow is not attributed to a cause.": "d",
    "%1 of the lost flow is not attributed to a single element.": "d",
    "Showing the lost flow only: it is %1 of the generated flow (%2 was "
    "delivered). Press Delivered to draw the whole flow.": "dd",
    # other capacity, outage and cost captions
    "No capacity limit applies while the system is up (Raptor records it as "
    "unlimited).": "",
    "Where no capacity limit applies the capacity line runs along the top "
    "edge (Raptor records it as unlimited).": "",
    "%1 loses the largest share of capacity (%2).": "dd",
    "These are overlapping marginal contributions, so they need not add to "
    "100 %.": "",
    "Points above the diagonal are understated by availability alone.": "",
    "Half of the outages end by %1; one in ten lasts over %2.": "qq",
    "There was one outage in %1 runs; it lasted %2.": "dq",
    "Low right, frequent short outages: improve reliability. High left, rare "
    "long ones: repair and logistics.": "",
    "%1 carries the largest cost (%2).": "dd",
    "No element has a cost in this run.": "",
    "%1 fails most often: %2 failures a run on average (%3 of all component "
    "failures). The runs average %4 component failures each.": "dddd",
    "Only 1 year, too few to show a trend; median %1.": "d",
    "Only %1 years, too few to show a trend; median %2.": "dd",
    "%1 years; the median moves from %2 to %3.": "ddd",
    "%1 run-years; no target was set.": "d",
    "%1 of the %2 dots are at %3.": "ddd",
    "%1 occupies the largest area (%2 of %3).": "ddw",
    "%1 occupies the largest area (%2, %3 of %4).": "dddw",
    "%1; best %2 up, worst %3 up.": "cdd",
    "Delivered %1 of the generated flow: it missed the capacity target %2 by "
    "%3 points (loss allowed %4, lost %5).": "ddddd",
}
