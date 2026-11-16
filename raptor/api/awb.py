"""Availability Workbench interchange (headless API).

Thin public surface over :mod:`raptor.core.fileio.awb` so the CLI and GUI
never import the core file layer directly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from raptor.core.fileio.awb import (
    AwbFormatError, AwbReport, export_awb, import_awb, read_awb,
    write_awb,
)

__all__ = [
    "AwbFormatError", "AwbReport", "export", "import_project", "read",
    "write",
]


def export(rbd: Any, path: str | Path, *, source: str = "",
           write: bool = True) -> AwbReport:
    """Write a RAPTOR model as an AWB 5.0 project."""
    return export_awb(rbd, path, source=source, write=write)


def import_project(path: str | Path, *,
                   source: str = "") -> tuple[Any, AwbReport]:
    """Read an AWB 5.0 project; return a RAPTOR model and its report."""
    return import_awb(path, source=source)


def read(path: str | Path) -> dict:
    """Read the raw AWB tables (diagnostics and tests)."""
    return read_awb(path)


def write(path: str | Path, tables: dict) -> None:
    """Write raw AWB tables (diagnostics and tests)."""
    write_awb(path, tables)
