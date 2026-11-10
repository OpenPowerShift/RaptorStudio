"""Re-run the shared size-aware auto-layout over the bundled examples.

    .venv/bin/python tools/relayout_examples.py           # rewrite models
    .venv/bin/python tools/relayout_examples.py --check   # report only

Every level (the home page and each hierarchy) is placed by
`raptor.core.fileio.legacy_layout.normalize_legacy_layout`, the same engine
the GUI's Auto Layout button uses: on the 1.25 grid, with block/oval sizes
measured from their names.

The tool writes LAYOUT ONLY. It patches the model JSON in place: element
`x`/`y`, the legacy view zooms/origins implied by the layout, and (when the
layout moved) drops a stale frozen `view_transform`. It never round-trips
through `save_rbds`, so metadata, defaults, run settings and every other
field stay byte-for-byte as they were. `--check` never writes; it reports
whether a model would move and exits 1, which makes it a safe freshness
gate.

The tutorial generators (`new_docs/tools/gen_models.py` and
`gen_advanced_models.py`) already run the layout engine when they build a
model. Use this tool after pulling a layout change, then regenerate the
start-page thumbnails and documentation screenshots:

    .venv/bin/python tools/make_example_thumbnails.py
    .venv/bin/python new_docs/tools/capture_screens.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODELS = ROOT / "new_docs" / "models"

#: layout section -> .rbds element list key
_ELEMENTS = {
    "BLOCK_DATA": "blocks",
    "NODE_DATA": "nodes",
    "EVENT_DATA": "events",
    "HIERARCHY_DATA": "hierarchies",
}


def _coords(rbd) -> dict[tuple[str, str], tuple[str, str]]:
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for section in _ELEMENTS:
        rows = rbd.sections.get(section)
        if not rows:
            continue
        for row in rows.rows:
            if str(row.get("ID", "0")) == "0":
                continue
            try:
                out[(section, str(int(float(row["ID"]))))] = (
                    str(row["xPos"]), str(row["yPos"]))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def _view_state(rbd) -> dict[tuple[str, str], str]:
    """Stored legacy view zooms/origins the layout engine derives."""
    out: dict[tuple[str, str], str] = {}
    system_row = (rbd.sections["SYSTEM_DATA"].rows[0]
                  if rbd.sections.get("SYSTEM_DATA") else {})
    out[("SYSTEM_DATA", "zoomVal")] = str(system_row.get("zoomVal", ""))
    rows = (rbd.sections.get("HIERARCHY_DATA").rows
            if rbd.sections.get("HIERARCHY_DATA") else [])
    for row in rows:
        if str(row.get("ID", "0")) == "0":
            continue
        hid = str(int(float(row["ID"])))
        out[("HIERARCHY_DATA", f"{hid}.zoom")] = str(row.get("zoom", ""))
    return out


def _patch(raw: dict, rbd) -> None:
    """Write the laid-out coordinates/zooms into the raw JSON document."""
    raw_elements = raw.get("elements") or {}
    by_section: dict[str, dict[str, dict]] = {}
    for section, jkey in _ELEMENTS.items():
        by_section[section] = {
            str(int(float(e.get("id", 0)))): e
            for e in raw_elements.get(jkey, [])
        }
    for section, rows in by_section.items():
        row_list = (rbd.sections.get(section).rows
                    if rbd.sections.get(section) else [])
        for row in row_list:
            if str(row.get("ID", "0")) == "0":
                continue
            target = rows.get(str(int(float(row["ID"]))))
            if target is None:
                continue
            target["x"] = float(row["xPos"])
            target["y"] = float(row["yPos"])

    system = raw.setdefault("system", {})
    legacy = system.get("legacy")
    if not isinstance(legacy, dict):
        legacy = {}
        system["legacy"] = legacy
    system_row = (rbd.sections["SYSTEM_DATA"].rows[0]
                  if rbd.sections.get("SYSTEM_DATA") else {})
    legacy["zoomVal"] = str(system_row.get("zoomVal", ""))
    # The current writer drops the legacy view origins because they are
    # always the 0/80 anchor; do not introduce keys it would strip.
    legacy.pop("xOrigin", None)
    legacy.pop("yOrigin", None)

    raw_hiers = {str(int(float(h.get("id", 0)))): h
                 for h in (raw.get("elements") or {}).get("hierarchies", [])}
    rows = (rbd.sections.get("HIERARCHY_DATA").rows
            if rbd.sections.get("HIERARCHY_DATA") else [])
    for row in rows:
        if str(row.get("ID", "0")) == "0":
            continue
        target = raw_hiers.get(str(int(float(row["ID"]))))
        if target is None:
            continue
        target_legacy = target.get("legacy")
        if not isinstance(target_legacy, dict):
            target_legacy = {}
            target["legacy"] = target_legacy
        target_legacy["zoom"] = str(row.get("zoom", ""))
        target_legacy.pop("xOrigin", None)
        target_legacy.pop("yOrigin", None)

    # A frozen scene transform was derived from the old coordinates; a
    # repositioned model must not keep it or it opens at the old scale.
    raw.pop("view_transform", None)


def relayout(path: Path, *, dry_run: bool = False) -> bool:
    """Re-lay one model. Returns True when its layout differs.

    `dry_run=True` performs the full layout and comparison but never
    writes, so `--check` cannot dirty the working tree.
    """
    from raptor.core.fileio.legacy_layout import normalize_legacy_layout
    from raptor.core.fileio.rbds import parse_rbds

    text = path.read_text(encoding="utf-8")
    raw = json.loads(text)
    rbd = parse_rbds(text, default_name=path.name)
    before = (_coords(rbd), _view_state(rbd))
    normalize_legacy_layout(rbd, force=True)
    after = (_coords(rbd), _view_state(rbd))
    if before == after:
        return False
    if dry_run:
        return True
    _patch(raw, rbd)
    path.write_text(json.dumps(raw, indent=1, ensure_ascii=False),
                    encoding="utf-8")
    return True


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


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
        if relayout(path, dry_run=args.check):
            moved.append(path)
            print(("would move " if args.check else "moved ") + _display(path))
        else:
            print("unchanged  " + _display(path))

    if args.check and moved:
        print(f"relayout_examples.py: {len(moved)} model(s) are not on the "
              f"current layout; run without --check", file=sys.stderr)
        return 1
    print(f"relayout_examples.py: OK -- {len(paths)} model(s), "
          f"{len(moved)} changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
