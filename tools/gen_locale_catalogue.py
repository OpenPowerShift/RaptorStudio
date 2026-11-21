#!/usr/bin/env python3
"""Generate/refresh a Qt Linguist .ts catalogue from the ribbon data.

    python tools/gen_locale_catalogue.py raptor/gui/locale/raptor_fr.ts --write

The source strings are data (`CommandSpec.text`, `Page.title`,
`Group.title`), which `pyside6-lupdate` cannot see. This generator keeps
existing translations, adds untranslated entries for new strings, and lets
`--check` fail CI when the committed file is stale. Compile with
`pyside6-lrelease FILE.ts -qm FILE.qm`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _existing(path: Path) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    if not path.is_file():
        return out
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return out
    for context in root.findall("context"):
        name = context.findtext("name") or ""
        for message in context.findall("message"):
            source = message.findtext("source") or ""
            translation = message.find("translation")
            text = translation.text if translation is not None else ""
            out[(name, source)] = text or ""
    return out


def catalogue(locale: str = "fr") -> ET.ElementTree:
    from raptor.gui.command_table import COMMANDS, LAYOUT
    entries: dict[str, list[str]] = {"Page": [], "Group": [], "Command": []}
    for page in LAYOUT:
        entries["Page"].append(page.title)
        for group in page.groups:
            entries["Group"].append(group.title)
    for spec in COMMANDS:
        if spec.text and spec.id not in ("home.undo", "home.redo"):
            entries["Command"].append(spec.text)
    # De-duplicate and keep first-seen order; translators read document order.
    for key in entries:
        entries[key] = list(dict.fromkeys(entries[key]))
    old = _existing(Path(sys.argv[1])) if len(sys.argv) > 1 else {}
    root = ET.Element("TS", {"version": "2.1", "language": locale})
    for context_name, sources in entries.items():
        context = ET.SubElement(root, "context")
        ET.SubElement(context, "name").text = context_name
        for source in sources:
            message = ET.SubElement(context, "message")
            ET.SubElement(message, "source").text = source
            translation = ET.SubElement(message, "translation")
            text = old.get((context_name, source), "")
            if text:
                translation.text = text
            else:
                translation.set("type", "unfinished")
    ET.indent(root, space="  ")
    return ET.ElementTree(root)


def render(locale: str = "fr") -> str:
    body = ET.tostring(catalogue(locale).getroot(), encoding="unicode")
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE TS>\n' + body + "\n")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: gen_locale_catalogue.py FILE.ts [--write|--check]",
              file=sys.stderr)
        return 2
    path = Path(args[0])
    text = render(path.stem.removeprefix("raptor_") or "fr")
    if "--write" in args:
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
        return 0
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return 0
    print(f"{path} is stale; run with --write", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
