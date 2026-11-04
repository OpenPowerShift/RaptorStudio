"""What the abbreviations in exported tables mean.

Spreadsheet exports put these on the header of a column (as a comment) and
list the ones a workbook uses on its Glossary tab. One line each: the
expansion, then what it measures. The longer, on-screen versions are the
Results dock's tooltips (`raptor.gui.ui.results.GLOSSARY`); both follow the
Raptor manual's definitions (Appendix F) and `docs/formulas.adoc`.

Nothing here imports Qt.
"""
from __future__ import annotations

#: Term (as it appears in a header or row label) -> meaning.
TERMS: dict[str, str] = {
    # system measures
    "MTBDE": "Mean Time Between Downing Events: average working time between "
             "events that take the system fully or partly down "
             "(uptime / downing events).",
    "MDT": "Mean Down Time: average time the system is fully down (red) per "
           "downing event (downtime / downing events).",
    "MTBM": "Mean Time Between Maintenance: average working time between any "
            "maintenance events, repairs and preventive maintenance.",
    "MTBMu": "Mean Time Between Maintenance, unscheduled: average working "
             "time between repairs.",
    "MTBMs": "Mean Time Between Maintenance, scheduled: average working time "
             "between preventive-maintenance events.",
    "MMT": "Mean Maintenance Time: average duration of a maintenance event, "
           "including logistics delays and waits for spares or resources.",
    "MMTu": "Mean Maintenance Time, unscheduled: average duration of a "
            "repair.",
    "MMTs": "Mean Maintenance Time, scheduled: average duration of "
            "preventive maintenance.",
    "MTTFF": "Mean Time To First Failure: average time to the system's first "
             "failure in a run. A run with no failure counts its run length, "
             "so the value is then a lower bound.",
    "MTBF": "Mean Time Between Failures: total time divided by the number of "
            "component failures.",
    "MTBE": "Mean Time Between Events: total time divided by the number of "
            "system failures.",
    "MTTF": "Mean Time To Failure: the expected failure time of a part.",
    "MTTR": "Mean Time To Repair: the expected repair time of a part.",
    "Operational availability": "Fraction of all time the system was at "
                                "least partly working (green or yellow).",
    "Inherent availability": "Availability counting only corrective repair "
                             "as downtime: uptime / (uptime + repair time).",
    "Achieved availability": "Availability counting corrective repair and "
                             "preventive maintenance as downtime.",
    "Dependability": "Fraction of mission time spent at least partly "
                     "working.",
    "Conditional reliability": "Of the missions that started with the "
                               "system up, the fraction completed without a "
                               "failure.",
    # capacity analysis
    "Units produced": "Total flow delivered by the system over the run: "
                      "mean flow multiplied by the run's length.",
    "Mean flow": "The system's (Start node's) flow, averaged over time, in "
                 "units per time unit.",
    "Mean capacity": "The flow the system could have carried, averaged over "
                     "time, in units per time unit.",
    "Capacity-weighted availability": "Mean flow divided by the flow "
                                      "generated: the share of the demand "
                                      "delivered. Availability only asks "
                                      "whether any flow got through.",
    "Lost flow": "Flow generated minus mean flow: the demand not delivered, "
                 "on average. Times the run length it is the product not "
                 "supplied.",
    # statistics
    "SEM": "Standard Error of the Mean: how much the mean would vary if the "
           "simulation were repeated (st. dev. / sqrt(runs)). Smaller is "
           "more precise.",
    "St. dev.": "Sample standard deviation: how much the per-run values "
                "spread.",
    # maintenance and logistics
    "CM": "Corrective maintenance: a repair after a failure.",
    "PM": "Preventive maintenance: scheduled maintenance before a failure.",
    "RSO": "Routine spares ordering: a fixed quantity arrives at a fixed "
           "interval.",
    "ESO": "Emergency spares ordering: a spare is shipped when none is in "
           "stock, after the emergency time.",
    "SLO": "Stock-level ordering: an order is placed when stock falls to the "
           "reorder level.",
    "DSI": "Descendant Status Indicator: the state of the elements inside a "
           "hierarchy, shown apart from the hierarchy's own state.",
    # importance (analytical solver and simulation)
    "Birnbaum": "Birnbaum importance: how far the system moves between this "
                "element never failing and always failed; the element the "
                "system is most sensitive to scores highest.",
    "RAW": "Risk Achievement Worth: system unavailability with this element "
           "failed, divided by now. 1 means no effect.",
    "RRW": "Risk Reduction Worth: system unavailability now, divided by that "
           "with this element perfect. 1 means nothing to gain.",
    "Fussell-Vesely": "Fussell-Vesely (Vesely-Fussell) importance: the share "
                      "of the system's unavailability this element is "
                      "responsible for.",
    "Criticality": "Criticality importance: the system unavailability "
                   "removable by making this element perfect, as a fraction "
                   "of the total.",
    "BDD": "Binary decision diagram: the exact structure function the "
           "analytical solver evaluates.",
}
