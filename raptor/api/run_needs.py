"""What each result needs switched on before the run, and the switch on
every surface.

A chart, table or layer that a default run does not produce says which
option it needs. Before this table each surface worded that alone, and the
words drifted: one message said "attribution on" without naming a
parameter, another sent the reader to a dialog that was not the shortest
way, and a third named a check box by a label it does not have. Every
message now comes from here, so the switch is named the same way, and
exactly, wherever the reader is.

`message` is the surface-neutral sentence the API raises (so MCP and the
command line both show it); `gui_hint` is the window's wording, with the
control's real label and where it is; `turn_on` edits an experiment so the
window can offer one click that switches the options on and runs again.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from raptor.core import run_experiment as _rx

#: The Run panel's recording check boxes and their hints, re-exported so the
#: GUI's translation catalogue can list them without importing the engine
#: layer (`raptor.gui` reaches `raptor.core.run_experiment` only through here).
RECORDING_FIELDS = _rx.RECORDING_FIELDS
RECORDING_REQUIRES = _rx.RECORDING_REQUIRES
STATS_START_TOOLTIP = _rx._STATS_START_TOOLTIP

#: Where the window keeps the run options.
RECORDING = "Run panel ▸ Recording"
ANALYSIS = "Run panel ▸ Results to calculate"


@dataclass(frozen=True)
class Need:
    """One option a result depends on."""
    what: str          # what the result needs, as a noun phrase
    mcp: str           # the MCP call or argument that turns it on
    cli: str           # the command-line flag or command
    label: str         # the window's control, exactly as it is labelled
    place: str         # where that control is


NEEDS: dict[str, Need] = {
    "attribution": Need(
        "downtime attribution", "simulate(attribution=true)",
        "raptor explain MODEL", "Detailed event log", RECORDING),
    "availability_plot": Need(
        "the availability plot", "simulate(availability_plot=true)",
        "raptor run -r ao-plot", "Availability plot", RECORDING),
    "capacity": Need(
        "capacity analysis", "configure_system(capacity=true)",
        "--analysis capacity", "Capacity", ANALYSIS),
    "capacity_plot": Need(
        "the capacity plot", "configure_system(capacity=true)",
        "-r capacity-plot", "Capacity plot", RECORDING),
    "cost": Need(
        "cost analysis", "configure_system(cost=true)",
        "--analysis cost", "Cost", ANALYSIS),
    "cost_plot": Need(
        "the interim cost report", "simulate(interim_cost=true)",
        "raptor run -r interim-cost", "Interim cost", RECORDING),
    "weak_link": Need(
        "Weak Link analysis", "configure_system(weak_link=true)",
        "--analysis weak", "Weak Link", ANALYSIS),
    "importance": Need(
        "importance measures (mode both, for RAW and RRW)",
        "simulate(importance=\"both\")", "--importance both",
        "Importance measures", ANALYSIS),
}

#: What a window control needs ticked first (the panel refuses the
#: capacity plot without capacity analysis, so the click must tick both).
_REQUIRES = {"capacity_plot": ("capacity",), "cost_plot": ("cost",)}

#: The other direction: what each Run panel option FEEDS, keyed by the
#: panel's own control key (`analyses` entries, `recording` keys and
#: "importance"). The panel's note beside every check box is written from
#: this, so it names the results the option gives -- and, unticked, the
#: results that will be empty -- in the same words as the results' own
#: "needs …" messages above (review #286: "is it clear what is required
#: for different analysis results?").
FEEDS: dict[str, str] = {
    "weak": "the Weak Link results and the canvas weak-link bands",
    "cost": "the Cost results and the cost charts",
    "capacity": "the Capacity results (flow against the limits)",
    "importance": "the Importance results and the importance chart",
    "event_log": ("the Event Log tab, Downtime drivers, the Story's causes, "
                  "the Advisor's downtime checks and the canvas downtime "
                  "layers"),
    "ao_plot": "the availability-over-time chart (Charts)",
    "capacity_plot": "the flow-over-time chart (Charts)",
    "cost_plot": "the cost-over-time chart (Charts)",
}


def feeds_note(key: str, on: bool) -> str:
    """The note beside a Run panel option: what it gives when ticked, and
    what will be empty when it is not."""
    what = FEEDS[key]
    if on:
        return f"For {what}."
    return f"Off: {what[0].lower() + what[1:]} will be empty."


def _expanded(keys: Iterable[str]) -> list[str]:
    out: list[str] = []
    for key in keys:
        if key not in NEEDS:
            raise KeyError(f"unknown run need {key!r}")
        for item in (*_REQUIRES.get(key, ()), key):
            if item not in out:
                out.append(item)
    return out


def how(*keys: str, surface: str | None = None) -> str:
    """'turn it on (MCP: …; command line: …; in the window tick … under …)
    and run again': the switch on every surface, or on `surface` alone
    ("mcp", "cli", "gui"): a command-line user was read MCP calls."""
    needs = [NEEDS[k] for k in _expanded(keys)]
    it = "it" if len(needs) == 1 else "them"
    if surface == "mcp":
        calls = ", ".join(dict.fromkeys(n.mcp for n in needs))
        return f"turn {it} on with {calls} and run again"
    if surface == "cli":
        flags = " ".join(dict.fromkeys(n.cli for n in needs))
        return f"run again with {flags}"
    if surface == "gui":
        ticks = " and ".join(f"{n.label} under {n.place}" for n in needs)
        return f"tick {ticks} and run again"
    mcp = ", ".join(dict.fromkeys(n.mcp for n in needs))
    cli = " ".join(dict.fromkeys(n.cli for n in needs))
    ticks = " and ".join(f"{n.label} under {n.place}" for n in needs)
    return (f"turn {it} on (MCP: {mcp}; command line: {cli}; in the window "
            f"tick {ticks}) and run again")


def message(*keys: str, subject: str = "this chart",
            surface: str | None = None) -> str:
    """'this chart needs …: turn it on (…) and run again.' One sentence
    for every surface, or for `surface` alone."""
    what = " and ".join(NEEDS[k].what for k in _expanded(keys))
    return f"{subject} needs {what}: {how(*keys, surface=surface)}"


def gui_hint(*keys: str) -> str:
    """The window's wording: the exact control label and where it is."""
    needs = [NEEDS[k] for k in _expanded(keys)]
    places: dict[str, list[str]] = {}
    for n in needs:      # one "under …" per place: "Weak Link and Cost under …"
        places.setdefault(n.place, []).append(f"<b>{n.label}</b>")
    ticks = " and ".join(f"{' and '.join(labels)} under {place}"
                         for place, labels in places.items())
    return f"Tick {ticks}, then run again."


def button_text(*keys: str) -> str:
    """'Turn on Detailed event log and run' for the one-click fix."""
    labels = [NEEDS[k].label for k in _expanded(keys)]
    return f"Turn on {' and '.join(labels)} and run"


def turn_on(experiment: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    """A copy of *experiment* with every option in *keys* switched on."""
    out = {**experiment,
           "analyses": list(experiment.get("analyses") or []),
           "recording": dict(experiment.get("recording") or {})}
    for key in _expanded(keys):
        if key == "attribution":
            out["recording"]["event_log"] = True
        elif key == "availability_plot":
            out["recording"]["ao_plot"] = True
        elif key == "capacity_plot":
            out["recording"]["capacity_plot"] = True
        elif key == "cost_plot":
            out["recording"]["cost_plot"] = True
        elif key in ("capacity", "cost", "weak_link"):
            name = "weak" if key == "weak_link" else key
            if name not in out["analyses"]:
                out["analyses"].append(name)
        elif key == "importance":
            out["importance"] = "both"
    return out


def is_on(experiment: dict[str, Any], key: str) -> bool:
    """Whether *experiment* already has *key* switched on."""
    recording = experiment.get("recording") or {}
    return {
        "attribution": bool(recording.get("event_log")),
        "availability_plot": bool(recording.get("ao_plot")),
        "capacity_plot": bool(recording.get("capacity_plot")),
        "cost_plot": bool(recording.get("cost_plot")),
        "capacity": "capacity" in (experiment.get("analyses") or []),
        "cost": "cost" in (experiment.get("analyses") or []),
        "weak_link": "weak" in (experiment.get("analyses") or []),
        "importance": experiment.get("importance") == "both",
    }[key]
