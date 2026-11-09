"""Re-run the shared size-aware auto-layout over the bundled examples.

    .venv/bin/python tools/relayout_examples.py           # rewrite models
    .venv/bin/python tools/relayout_examples.py --check   # report only

Every level (the home page and each hierarchy) is placed by
`raptor.core.fileio.legacy_layout.normalize_legacy_layout`, the same engine
the GUI's Auto Layout button uses: on the 1.25 grid, with block/oval sizes
measured from their names. This is the reproducible repair for a bundled
model that was hand-placed, or authored before the engine became
size-aware; it changes coordinates (and the legacy view zooms) only, never
element data or run settings.

The tutorial generators (`new_docs/tools/gen_models.py` and
`gen_advanced_models.py`) already run this pass when they build a model.
Use this tool after pulling a layout change, then regenerate the
start-page thumbnails and documentation screenshots:

    .venv/bin/python tools/make_example_thumbnails.py
    .venv/bin/python new_docs/tools/capture_screens.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODELS = ROOT / "new_docs" / "models"


def _coordinates(rbd) -> dict[tuple[str, str], tuple[str, str]]:
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for section in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA", "HIERARCHY_DATA"):
        rows = rbd.sections.get(section)
        if not rows:
            continue
        for row in rows.rows:
            if str(row.get("ID", "0")) == "0":
                continue
            try:
                out[(section, str(row["ID"]))] = (
                    str(row["xPos"]), str(row["yPos"]))
            except KeyError:
                continue
    return out


def _view_state(rbd) -> dict[tuple[str, str], tuple[str, str]]:
    """Coordinates plus the saved view zooms/origins they imply."""
    out = _coordinates(rbd)
    system = rbd.sections.get("SYSTEM_DATA")
    if system and system.rows:
        row = system.rows[0]
        for col in ("zoomVal", "xOrigin", "yOrigin"):
            out[("SYSTEM_DATA", col)] = (str(row.get(col, "")), "")
    for row in (rbd.sections.get("HIERARCHY_DATA").rows
                if rbd.sections.get("HIERARCHY_DATA") else []):
        if str(row.get("ID", "0")) == "0":
            continue
        for col in ("zoom", "xOrigin", "yOrigin"):
            out[("HIERARCHY_DATA", f"{row['ID']}.{col}")] = (
                str(row.get(col, "")), "")
    return out


def relayout(path: Path) -> bool:
    """Re-lay one model in place. Returns True when the saved view moved."""
    import json

    from raptor.core.fileio.legacy_layout import normalize_legacy_layout
    from raptor.core.fileio.rbds import load_rbds, save_rbds

    rbd = load_rbds(str(path))
    before = _view_state(rbd)
    normalize_legacy_layout(rbd, force=True)
    after = _view_state(rbd)
    if before == after:
        return False
    # Keep the file's own meta (not every bundled model carries a
    # file_name, and adding one would be unrelated churn).
    meta = json.loads(path.read_text(encoding="utf-8")).get("meta") or {}
    save_rbds(rbd, str(path), meta=meta)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="report models that would move without rewriting them")
    parser.add_argument(
        "paths", nargs="*", type=Path,
        help="model files (default: every .rbds in new_docs/models/)")
    args = parser.parse_args(argv)

    paths = args.paths or sorted(MODELS.glob("*.rbds"))
    if not paths:
        print("relayout_examples.py: no models found", file=sys.stderr)
        return 1

    moved = []
    for path in paths:
        if relayout(path):
            moved.append(path)
            print(("would move " if args.check else "moved ") + str(path.relative_to(ROOT)))
        else:
            print("unchanged  " + str(path.relative_to(ROOT)))

    if args.check and moved:
        print(f"relayout_examples.py: {len(moved)} model(s) are not on the "
              f"current layout; run without --check", file=sys.stderr)
        return 1
    print(f"relayout_examples.py: OK -- {len(paths)} model(s), "
          f"{len(moved)} changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
