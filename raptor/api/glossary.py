"""What the abbreviations in exported tables mean.

A view of `raptor.api.insight.measures`: one definition per measure, written
for a spreadsheet header comment and the workbook's Glossary tab.  Terms
that are not result measures (an ordering scheme, a statistic's name) stay
in `ABBREVIATIONS`.

Nothing here imports Qt.
"""
from __future__ import annotations

from raptor.api.insight import measures

#: Terms that are not result measures.
ABBREVIATIONS: dict[str, str] = {
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
    # importance
    "Criticality": "Criticality importance: the system unavailability "
                   "removable by making this element perfect, as a fraction "
                   "of the total.",
    "BDD": "Binary decision diagram: the exact structure function the "
           "analytical solver evaluates.",
}

#: Labels the exports have always defined, in the order they appear.
_MEASURE_LABELS = (
    "MTBDE", "MDT", "MTBM", "MTBMu", "MTBMs", "MMT", "MMTu", "MMTs",
    "MTTFF", "MTBF", "MTBE", "MTTF", "MTTR",
    "Operational availability", "Inherent availability",
    "Achieved availability", "Dependability", "Conditional reliability",
    "Units produced", "Mean flow", "Mean capacity",
    "Capacity-weighted availability", "Lost flow",
    "Birnbaum", "RAW", "RRW", "Fussell-Vesely",
)

#: Term (as it appears in a header or row label) -> meaning.
TERMS: dict[str, str] = {
    label: measures.glossary_line(measure.id)
    for label in _MEASURE_LABELS
    if (measure := measures.for_label(label)) is not None
}
TERMS.update(ABBREVIATIONS)
