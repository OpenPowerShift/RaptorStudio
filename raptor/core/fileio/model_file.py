"""Opening a model file: the one loader the GUI, the CLI and the MCP server
all go through (the CLI and MCP via `raptor.api.workspace`)."""
from __future__ import annotations

from pathlib import Path

from raptor.core.fileio.rbd import RbdFile


def load_model_file(path: str | Path) -> RbdFile:
    """Read an `.rbds`, `.rbdz` or `.rbd` model, chosen by extension (any
    case).

    An `.rbds` is checked against the format's JSON Schema on the way in
    (`rbds_schema`): an error raises `RbdsFormatError` naming the element and
    field, a file from a newer RAPTOR Studio raises `ValueError` saying which
    version saved it, and warnings (unknown keys) are left on
    `RbdFile.format_warnings` for `validate` to report.  An `.rbdz` is the
    same document inside a ZIP archive, with result files beside it; its
    model takes the identical checked path."""
    text = str(path)
    if text.lower().endswith(".rbdz"):
        from raptor.core.fileio.rbdz import load_model
        return load_model(text)
    if text.lower().endswith(".rbds"):
        from raptor.core.fileio.rbds import load_rbds
        return load_rbds(text)
    from raptor.core.fileio.rbd import load_rbd
    return load_rbd(text)
