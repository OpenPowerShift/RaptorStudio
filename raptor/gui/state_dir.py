"""Backward-compatible import for the private per-user state folder.

The implementation now lives in `raptor.core.state_dir` so the API and the
GUI can share it without an upward import.
"""
from __future__ import annotations

from raptor.core.state_dir import state_dir

__all__ = ["state_dir"]
