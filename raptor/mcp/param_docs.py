"""Descriptions for the tools' input properties.

A client shows a property's `description` beside the argument, and a model
reads it when it fills one in. The tools' signatures carry types only, so the
server adds a description where none is declared. The text comes from
`COMMON` for arguments that mean the same thing in every tool, and otherwise
from the first sentence of the tool's own docstring that names the argument
in backticks. A property neither source covers is left without one rather
than given a guess.
"""
from __future__ import annotations

import re
from typing import Any

#: Arguments with one meaning across the tools.
COMMON: dict[str, str] = {
    "model_id": "Id of a loaded model, from open_model, build_model, create_model or list_models.",
    "run_id": "Id of a finished run in the workspace, from simulate, start_run/run_status or get_run_results.",
    "job_id": "Id of a background job, from start_run.",
    "selector": "One element: its name, or Kind:id such as Block:3, Node:2, Event:1, Hier:4 or Link:7.",
    "selectors": "Elements by name or Kind:id (Block:3, Node:2, Link:7).",
    "limit": "Most items to return in this page.",
    "offset": "Items to skip: pass the previous page's next_offset to continue.",
    "workers": ("Worker processes. Leave out for the server default (several cores for a large "
                "model); 1 gives the sequential numbers."),
    "num_runs": "Replications (trials). Leave out to use the model's own setting.",
    "trials": "Paired trials per scenario. Leave out for the tool's default.",
    "timeout_s": "Wall-clock limit for the call, in seconds.",
    "seed": "Non-negative seed that re-rolls the batch reproducibly.",
    "block_streams": ("separate: each block that shares a random stream gets its own for this run "
                      "(the model is not edited); model: the blocks' own. Leave out for the model's "
                      "setting."),
    "run_length": "Simulated time per run, in the model's units. Leave out for the model's own setting.",
    "library_path": "A part library (.rbdlib) file, relative to the workspace.",
    "confirm_long_run": "True runs even when the estimate exceeds timeout_s.",
    "collect_events": "Keep the event log of every run (larger output; needed for event-level analysis).",
    "availability_plot": "Record the availability-over-time plot for this run. Leave out for the model's setting.",
    "interim_cost": "Record the interim cost report for this run. Leave out for the model's setting.",
    "attribution": ("Also work out which element caused each system outage and why; read it with "
                    "downtime_drivers and advise."),
    "top": "How many of the leading items to return.",
    "folder": "Output folder, relative to the workspace.",
    "theme": "Colour theme of the drawing: light or dark.",
}

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`(])")


#: Abbreviations a sentence does not end at ("e.g. `>=`" cut a description
#: at "e.g.").
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "cf.", "approx.")


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text or "").strip()
    for i, abbr in enumerate(_ABBREVIATIONS):
        flat = flat.replace(abbr + " ", f"\x00{i}\x00 ")
    out = [s.strip() for s in _SENTENCE.split(flat) if s.strip()]
    for i, abbr in enumerate(_ABBREVIATIONS):
        out = [s.replace(f"\x00{i}\x00", abbr) for s in out]
    return out


def from_docstring(doc: str, name: str, limit: int = 300) -> str | None:
    """The first sentence of `doc` that names `name` in backticks."""
    mark = f"`{name}`"
    for sentence in _sentences(doc):
        if mark in sentence:
            return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"
    return None


def describe(properties: dict[str, Any], doc: str) -> int:
    """Fill in `description` on each property that lacks one; returns how many."""
    n = 0
    for name, prop in properties.items():
        if not isinstance(prop, dict) or prop.get("description"):
            continue
        text = from_docstring(doc, name) or COMMON.get(name)
        if name == "num_runs" and prop.get("default") is not None and text == COMMON[name]:
            # "Leave out to use the model's own setting" beside a default of 10
            text = f"Replications (trials); {prop['default']} when left out."
        if text:
            prop["description"] = text
            n += 1
    return n
