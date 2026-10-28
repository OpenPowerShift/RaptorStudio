"""How many processor cores a full-speed run uses, in one place.

The setting `execution/max_workers` is 0 for automatic (every core: the
workers are separate processes, and the operating system keeps the window
responsive) or an explicit count. Animate always uses
one core: its whole point is to show each event as it happens.
"""
from __future__ import annotations

import os

from raptor.gui.ui import settings

KEY = "execution/max_workers"


def available() -> int:
    """Cores on this machine."""
    return max(1, os.cpu_count() or 1)


def automatic() -> int:
    """The automatic choice: every core. It used to leave one free, which
    on a 2-core machine meant the one-core path and different numbers from
    every multi-core machine."""
    return available()


def chosen() -> int:
    """Cores a full-speed run uses now."""
    n = settings.get_int(KEY, 0)
    return automatic() if n <= 0 else min(n, available())


def describe(animate: bool) -> str:
    """The run mode in words, for the Run panel and tooltips."""
    if animate:
        return "Animate — 1 core, every event shown"
    n = chosen()
    return f"Full speed — {n} of {available()} core{'s' if available() > 1 else ''}"
