"""Developer options: the hidden switches of Raptor 7, as settings.

The original gated these behind a command-line password (`/password-<code>`),
`/dev`, `/d`, `/sd` and `/C130`, and a hidden "Avant-garde" tab of the
Simulation dialog. Here nothing is password protected: each is an ordinary
option, off by default, set from Preferences (GUI), the settings file, the
environment (`RAPTOR_DEV_FEATURES`) or the command line.

This module is Qt-free so the engine, the API, the CLI and the GUI share one
definition.

Precedence, highest first: explicit values (command line / API arguments),
the environment, the settings mapping (config file / QSettings).
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from typing import Any

#: Event-log categories of the Avant-garde tab's A-D boxes
#: ("Block Details", "Node Details", "System Details", "Phase Details").
EVENT_CATEGORIES = ("block", "node", "system", "phase")


@dataclass(frozen=True)
class DevFeature:
    key: str
    label: str
    description: str
    kind: str                     # "bool" | "int" | "float" | "text" | "set"
    group: str = "raptor7"        # "raptor7" developer feature of Raptor 7 | "port" addition
    source: str = ""              # Raptor 7's name for it (menu item or switch)


DEV_FEATURES: tuple[DevFeature, ...] = (
    DevFeature("event_log_filter", "Event log: categories",
               "Write only the chosen categories to the detailed event log "
               "(block, node, system, phase). Empty writes everything.",
               "set", source="WriteEvents; Avant-garde A-D"),
    DevFeature("event_log_component", "Event log: one component",
               "Also write every event of the named component, whatever the "
               "category filter says.", "text",
               source="Avant-garde 'detailed events for "
                      "this component'"),
    DevFeature("domain_tree", "Domain Tree file",
               "Write DomainTree.TXT: the upstream tree of the End node.",
               "bool", source="Avant-garde 'Domain Tree'"),
    DevFeature("core_file", "Core ('Kings and Serfs') file",
               "Write CoreFile.TXT.", "bool",
               source="Avant-garde 'Core File'"),
    DevFeature("cumulative_by_trial", "Cumulative parameters by trial",
               "Every N runs, append a row of cumulative statistics to a "
               "file. 0 = off.", "int",
               source="Avant-garde 'Cumulative Params by "
                      "Trial'"),
    DevFeature("cumulative_by_time", "Cumulative parameters by time",
               "Every T time units, write cumulative statistics across the "
               "runs. 0 = off.", "float",
               source="Avant-garde 'Cumulative Params by "
                      "Time'"),
    DevFeature("defer_trigger", "Defer trigger",
               "Name of the trigger that blocks commented 'maintDefer' wait for after a failure, "
               "holding their repair until it fires ('phaseTrigger' also frees them when a "
               "mission ends). 'file' uses the trigger saved in the model, else the first one. "
               "Empty = off.", "text",
               source="Avant-garde 'Trigger'"),
    DevFeature("random_draw_count", "Random draw count in Final Results",
               "Add a 'Random draws: N' line to the Final Results report.",
               "bool", source="password"),
    DevFeature("show_criticality", "Show criticality",
               "Show criticality in Failure Effects View, the single point "
               "failure file and the status bar.", "bool",
               source="password"),
    DevFeature("inspector_ids", "Show internal ids",
               "Show ids, parents and counts when hovering an element.",
               "bool", source="/dev switch"),
    DevFeature("diagnostics_log", "Diagnostics log",
               "Write a timestamped diagnostics trace.", "bool",
               source="/d switch"),
    DevFeature("sim_details", "Random draw log (simDetails)",
               "Write simDetails.TXT: every random draw. Appends across "
               "launches, as Raptor 7 does.", "bool",
               source="/sd switch"),
    DevFeature("mission_file", "Mission file",
               "Write a mission file: block id, time and system up/down at "
               "every block failure.", "bool", source="/C130 switch"),
)

_BY_KEY = {f.key: f for f in DEV_FEATURES}


@dataclass(frozen=True)
class DeveloperOptions:
    event_log_filter: frozenset[str] = frozenset()
    event_log_component: str = ""
    domain_tree: bool = False
    core_file: bool = False
    cumulative_by_trial: int = 0
    cumulative_by_time: float = 0.0
    defer_trigger: str = ""
    random_draw_count: bool = False
    show_criticality: bool = False
    inspector_ids: bool = False
    diagnostics_log: bool = False
    sim_details: bool = False
    mission_file: bool = False

    def any_enabled(self) -> bool:
        return self != DeveloperOptions()

    def enabled_keys(self) -> list[str]:
        default = DeveloperOptions()
        return [f.name for f in fields(self)
                if getattr(self, f.name) != getattr(default, f.name)]

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            d[f.name] = sorted(v) if isinstance(v, frozenset) else v
        return d


class DevOptionError(ValueError):
    """An option value or name that cannot be used."""


def coerce(key: str, value: Any) -> Any:
    """A raw value (text from a file or the command line, or an already typed
    value) to the option's type. Raises DevOptionError."""
    feat = _BY_KEY.get(key)
    if feat is None:
        raise DevOptionError(
            f"unknown developer option {key!r}; known: "
            f"{', '.join(sorted(_BY_KEY))}")
    try:
        if feat.kind == "bool":
            if isinstance(value, str):
                low = value.strip().lower()
                if low in ("1", "true", "yes", "on"):
                    return True
                if low in ("0", "false", "no", "off", ""):
                    return False
                raise ValueError(value)
            return bool(value)
        if feat.kind == "int":
            n = int(float(value))
            if n < 0:
                raise ValueError("negative")
            return n
        if feat.kind == "float":
            x = float(value)
            if x < 0:
                raise ValueError("negative")
            return x
        if feat.kind == "set":
            items = value.split(",") if isinstance(value, str) else list(value)
            items = [i.strip().lower() for i in items if str(i).strip()]
            bad = [i for i in items if i not in EVENT_CATEGORIES]
            if bad:
                raise ValueError(f"unknown {bad}; use {EVENT_CATEGORIES}")
            return frozenset(items)
        return str(value).strip()
    except (TypeError, ValueError) as exc:
        raise DevOptionError(f"{key}: bad value {value!r} ({exc})") from exc


def from_mapping(values: Mapping[str, Any] | None,
                 base: DeveloperOptions | None = None) -> DeveloperOptions:
    """Options from `{key: raw value}`; unknown keys are an error (so a typo
    in a settings file is not silently ignored)."""
    opts = base or DeveloperOptions()
    if not values:
        return opts
    return replace(opts, **{k: coerce(k, v) for k, v in values.items()})


def parse_enable_list(text: str | None) -> dict[str, Any]:
    """`RAPTOR_DEV_FEATURES` / `--dev-features`: `all`, `none`, or
    `key[=value],key,...` (a bare bool key means true; a bare `int`/`float`
    key needs a value). The `event_log_filter` set is given as
    `event_log_filter=block+node`."""
    if text is None:
        return {}
    t = text.strip()
    if t.lower() in ("", "none"):
        return {}
    out: dict[str, Any] = {}
    if t.lower() == "all":
        for f in DEV_FEATURES:
            if f.kind == "bool" and f.key not in ("sim_details",
                                                  "diagnostics_log",
                                                  "mission_file"):
                out[f.key] = True
        return out
    for part in t.split(","):
        part = part.strip()
        if not part:
            continue
        key, eq, val = part.partition("=")
        key = key.strip()
        feat = _BY_KEY.get(key)
        if feat is None:
            raise DevOptionError(
                f"unknown developer option {key!r}; known: "
                f"{', '.join(sorted(_BY_KEY))}")
        if not eq:
            if feat.kind != "bool":
                raise DevOptionError(f"{key} needs a value: {key}=...")
            val = "true"
        out[key] = val.replace("+", ",") if feat.kind == "set" else val
    return out


def from_environment(env: Mapping[str, str] | None = None,
                     base: DeveloperOptions | None = None) -> DeveloperOptions:
    env = os.environ if env is None else env
    return from_mapping(parse_enable_list(env.get("RAPTOR_DEV_FEATURES")), base)
