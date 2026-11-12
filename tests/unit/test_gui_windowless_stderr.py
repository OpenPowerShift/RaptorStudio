"""The GUI must start when `sys.stderr` is None (the Windows gui-script)."""
from __future__ import annotations

import faulthandler
import sys

from raptor.gui import app


def test_crash_backtrace_survives_a_missing_stderr(monkeypatch, tmp_path):
    log = tmp_path / "debug.log"
    monkeypatch.setattr(app, "DEBUG_LOG_PATH", log)
    monkeypatch.setattr(sys, "stderr", None)
    was_enabled = faulthandler.is_enabled()
    faulthandler.disable()
    try:
        app._enable_crash_backtrace()          # used to raise RuntimeError
        assert faulthandler.is_enabled()
        assert log.exists()                    # it went to the file instead
    finally:
        faulthandler.disable()
        monkeypatch.undo()                     # stderr back before re-enabling
        if was_enabled:
            faulthandler.enable()


def test_debug_tracing_survives_a_missing_stderr(monkeypatch, tmp_path):
    import logging
    monkeypatch.setattr(app, "DEBUG_LOG_PATH", tmp_path / "debug.log")
    monkeypatch.setattr(sys, "stderr", None)
    root = logging.getLogger()
    saved = list(root.handlers), root.level
    was_enabled = faulthandler.is_enabled()
    try:
        app._configure_debug_tracing()
        assert not any(type(h) is logging.StreamHandler for h in root.handlers)
    finally:
        root.handlers[:], root.level = saved
        faulthandler.disable()
        monkeypatch.undo()                     # stderr back before re-enabling
        if was_enabled:
            faulthandler.enable()
