"""The GUI's view of the developer options: QSettings `dev/<key>` <-> options."""
from __future__ import annotations

from typing import Any

from raptor.core import dev_options as d


def _qsettings():
    from PySide6.QtCore import QSettings
    return QSettings()


def stored_values() -> dict[str, Any]:
    """Raw values stored under `dev/` (only keys that are set)."""
    s = _qsettings()
    out: dict[str, Any] = {}
    for f in d.DEV_FEATURES:
        key = f"dev/{f.key}"
        if s.contains(key):
            out[f.key] = s.value(key)
    return out


def current_options(env: dict[str, str] | None = None) -> d.DeveloperOptions:
    """Settings first, then `RAPTOR_DEV_FEATURES` over them. A bad stored
    value is ignored (the option stays at its default) rather than
    breaking the app."""
    opts = d.DeveloperOptions()
    for key, raw in stored_values().items():
        try:
            opts = d.from_mapping({key: raw}, opts)
        except d.DevOptionError:
            continue
    try:
        return d.from_environment(env, opts)
    except d.DevOptionError:
        return opts


def store(values: dict[str, Any]) -> None:
    s = _qsettings()
    for key, v in values.items():
        coerced = d.coerce(key, v)
        if isinstance(coerced, frozenset):
            coerced = ",".join(sorted(coerced))
        s.setValue(f"dev/{key}", coerced)


def clear() -> None:
    s = _qsettings()
    for f in d.DEV_FEATURES:
        s.remove(f"dev/{f.key}")
