#!/usr/bin/env python3
"""Re-export shim — the real implementation lives in
``raptor.core.fileio.builder``.

Fixture build scripts that do ``import rbd_builder`` (with ``tools/`` on
``sys.path``) keep working unchanged.  New code should import from
``raptor.core.fileio.builder`` directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Re-export everything from the promoted module.
from raptor.core.fileio.builder import (  # noqa: F401, E402
    _DUMMY_ROW_SECTIONS,
    _SECTION_COUNT_KEY,
    add_row,
    get_row,
    load,
    next_id,
    save,
    set_block_dependency,
    set_fields,
    sync_counts,
    validate,
)

# Also re-export the symbols the original file's own __all__ / CLI
# used to expose.
__all__ = [
    "load", "save", "get_row", "set_fields", "add_row",
    "set_block_dependency", "next_id", "sync_counts", "validate",
    "_DUMMY_ROW_SECTIONS", "_SECTION_COUNT_KEY",
]

# --- CLI (unchanged) ---------------------------------------------------

def _main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "validate":
        print(f"usage: {argv[0]} validate <path/to/input.rbd>", file=sys.stderr)
        return 2
    rbd = load(argv[2])
    issues = validate(rbd)
    if not issues:
        print(f"{argv[2]}: no issues found")
        return 0
    for issue in issues:
        print(issue)
    return 1 if any(i.startswith("ERROR") for i in issues) else 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
