"""The Results > Show on Canvas layers and what each one needs.

`layer_availability` is the single rule for "can this layer be shown with
the results on screen?". It reads exactly what each layer's painter reads
(see `MainWindow._on_*_overlay_toggled`), so the combo, the ribbon actions
and the tests all agree. No widgets here.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, NamedTuple

from raptor.gui.i18n import tr


class Layer(NamedTuple):
    id: str
    label: str
    action_attr: str


#: Combo order. `id` is stable; `action_attr` is the MainWindow QAction.
LAYERS: tuple[Layer, ...] = (
    Layer("downtime", "Share of downtime", "_downtime_overlay_action"),
    Layer("lost_capacity", "Lost capacity", "_lost_capacity_overlay_action"),
    Layer("weak_link", "Weak link (own availability)", "_weak_color_action"),
    Layer("capacity", "Capacity", "_capacity_overlay_action"),
    Layer("cost", "Cost", "_cost_overlay_action"),
    Layer("spares", "Spares", "_spares_overlay_action"),
    Layer("importance", "Importance", "_importance_overlay_action"),
    Layer("advisor", "Advisor findings", "_advisor_overlay_action"),
)


def _has(runs: list[Any], field: str) -> bool:
    if field == "weak_stats":
        return any(d.weak_stats for d in runs)
    return any(getattr(d, field) is not None for d in runs)



def _no_weak() -> str:
    return tr("No weak link data in these results: enable Weak Link in the "
              "Run panel's Analysis section and run again.")


def _no_cost() -> str:
    return tr("No costs in these results: switch on Cost Analysis and run "
              "again.")


def _no_importance() -> str:
    return tr("No importance measures in these results: switch on Run "
              "▸ Analysis ▸ Importance measures and run again.")


def _no_capacity() -> str:
    return tr("No capacity data in these results: switch on Capacity "
              "analysis and run again.")


def _no_event_log() -> str:
    return tr("Needs the event log: tick Event log under Run panel "
              "▸ Recording and run again.")


def layer_availability(layer_id: str, report_data: Iterable[Any] | None, *,
                       has_event_log: bool = False,
                       has_importance: bool = False,
                       has_element_findings: bool = False) -> str | None:
    """None when the layer can be shown, else a short reason why not.

    `report_data` is the per-run report list of the results on screen
    (empty or None before any run). The keyword flags carry what does not
    live in it: the event log, the importance measures, and whether the
    Advisor has a finding that names an element.
    """
    if layer_id not in {layer.id for layer in LAYERS}:
        raise KeyError(layer_id)
    runs = list(report_data or [])
    if layer_id == "advisor":
        return None if has_element_findings else tr(
            "The Advisor has no finding that names an element in this "
            "model.")
    if not runs:
        return tr("Run the simulation first: this layer shows results.")
    # (satisfied, reason-function) pairs, checked in order
    needs = {
        "downtime": [(has_event_log, _no_event_log)],
        "lost_capacity": [(has_event_log, _no_event_log),
                          (_has(runs, "capacity"), _no_capacity)],
        "weak_link": [(_has(runs, "weak_stats"), _no_weak)],
        "spares": [(_has(runs, "weak_stats"), _no_weak)],
        "capacity": [(_has(runs, "capacity"), _no_capacity)],
        "cost": [(_has(runs, "cost"), _no_cost)],
        "importance": [(has_importance, _no_importance)],
    }[layer_id]
    for ok, reason in needs:
        if not ok:
            return reason()
    return None
