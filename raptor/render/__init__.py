"""Diagram render worker.

This package holds ``worker.py`` -- a *standalone script* that runs in a
separate OS process under ``QT_QPA_PLATFORM=offscreen`` and renders one
block diagram to a PNG.  It is deliberately isolated from the rest of
the code base so that PySide6 is only imported by the worker (and the
existing GUI package), never by the Qt-free API / MCP layers.

The Qt-free wrapper that spawns it lives in :mod:`raptor.api.render`.
"""