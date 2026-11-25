""""Did you know?" tips for the Start page (U21).

Each tip names one thing that is easy to miss, in a sentence or two, and
optionally the command that shows it (the Start page offers a *Show me*
button that runs it, and puts the command's live shortcut in the text
where the tip says ``{shortcut}``). Keep each tip true of this version:
`tests/unit/test_tips.py` checks every command id exists.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tip:
    text: str
    command: str | None = None
    #: Show me is offered only with a model open, unless the command shows
    #: something without one (the command search, the shortcut list): on
    #: the Start page with nothing open most commands have nothing to show.
    needs_model: bool = True


TIPS: tuple[Tip, ...] = (
    Tip("Press {shortcut} and type what you want to do: every command in the "
        "ribbon, found by name, with the arrow keys to pick one.", "view.command_search",
        needs_model=False),
    Tip("Right-click a block, event or hierarchy ▸ Out of Service to cut it "
        "(down all run) or link it (never fails) for a what-if, without "
        "touching its data. Every result then says so."),
    Tip("What If compares changes against your model with the same random "
        "numbers, pair by pair, so a small difference is a real one.", "analyse.whatif"),
    Tip("Not sure how many trials are enough? Trials to Precision keeps running "
        "until the answer is as tight as you ask for.", "sim.precision"),
    Tip("Recommend tries a tested change for each block that drives downtime "
        "and tells you what each one would gain.", "sim.recommend"),
    Tip("Sensitivity shows which inputs move the answer most, so you know "
        "which numbers are worth getting right.", "analyse.sensitivity"),
    Tip("Export ({shortcut}) saves the report, the tables or the diagram from "
        "one window: pick PDF, Excel, OpenDocument, SVG or PNG.", "file.export"),
    Tip("After a run, Show on Canvas colours the diagram by a result. "
        "Hierarchies take the total of what is inside them, so the costliest "
        "group stands out before you open it."),
    Tip("On the run-to-run chart, drag across a stretch of time to zoom into "
        "it, hover to read who is down, and double-click to zoom out."),
    Tip("The Advisor finds likely mistakes as you build. Ignore a finding with "
        "a reason and it is kept with the model, so the next person knows why.",
        "view.advisor_toggle"),
    Tip("Right-click a block ▸ Duplicate ▸ Duplicate in Parallel adds a "
        "redundant copy, with the split and join nodes it needs."),
    Tip("Select a block and press Space: Quick Add repeats it in series, or "
        "in parallel as 1 of 2, 2 of 3 or 1 of 3, wired in where it is.",
        "model.quick_add"),
    Tip("Extract Hierarchy saves one hierarchy as a model of its own, to "
        "check that part on its own.", "sim.extract_hierarchy"),
    Tip("Compare two result sets side by side, or two models with Diff, to see "
        "exactly what a change did.", "sim.compare"),
    Tip("Press Alt to see a key for every ribbon button, and {shortcut} for "
        "the full list of shortcuts.", "help.shortcuts", needs_model=False),
)

SETTING = "start/next_tip"


def tip_for_launch() -> int:
    """The tip to show this time, moving on one each launch."""
    from raptor.gui.ui import settings
    index = settings.get_int(SETTING, 0) % len(TIPS)
    settings.set_value(SETTING, (index + 1) % len(TIPS))
    return index
