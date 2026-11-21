#!/usr/bin/env python3
"""Generate/refresh a Qt Linguist .ts catalogue for the GUI.

    python tools/gen_locale_catalogue.py raptor/gui/locale/raptor_pt_BR.ts --write
    python tools/gen_locale_catalogue.py raptor/gui/locale/raptor_pt_BR.ts --check
    python tools/gen_locale_catalogue.py raptor/gui/locale/raptor_pt_BR.ts --coverage

Two kinds of source string are collected:

* the ribbon's data (``CommandSpec.text``, ``Page.title``, ``Group.title``),
  which ``pyside6-lupdate`` cannot see, and
* ordinary GUI literals passed to Qt constructors/setters or wrapped in
  ``tr()``, found by scanning ``raptor/gui`` with :mod:`ast`.

The same string may appear in more than one context; Qt looks translations
up by context, so each context keeps its own entry.  Existing translations
are preserved, and a translator's glossary (``tools/locale_<code>.json``,
``{"English": "translated"}``) seeds newly added entries.  ``--check`` fails
CI when the committed file is stale; ``--coverage`` prints the translated
share so partial work is visible rather than implied complete.

Compile with ``pyside6-lrelease FILE.ts -qm FILE.qm``.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: GUI source only. The documentation and the MCP server keep English.
SCAN_ROOTS = (ROOT / "raptor" / "gui",)
SKIP_DIRS = {"locale", "__pycache__"}

#: Widget constructor -> positions of literal UI text.
CONSTRUCTORS: dict[str, tuple[int, ...]] = {
    "QLabel": (0,),
    "QPushButton": (0,),
    "QCheckBox": (0,),
    "QRadioButton": (0,),
    "QGroupBox": (0,),
    "QAction": (0,),
    "QMenu": (0,),
    "QToolButton": (0,),
    "QListWidgetItem": (0,),
    "QTableWidgetItem": (0,),
    "QStandardItem": (0,),
    "QTreeWidgetItem": (0,),
    "QProgressDialog": (0, 1),
    "QDialog": (0,),
    "QFileDialog": (1, 3),
    "QMessageBox": (1, 2),
}

#: Setter call -> positions of literal UI text.
SETTERS: dict[str, tuple[int, ...]] = {
    "setText": (0,),
    "setTitle": (0,),
    "setWindowTitle": (0,),
    "setToolTip": (0,),
    "setStatusTip": (0,),
    "setWhatsThis": (0,),
    "setPlaceholderText": (0,),
    "setAccessibleName": (0,),
    "setAccessibleDescription": (0,),
    "setTabText": (1,),
    "setHorizontalHeaderLabels": (0,),
    "setNameFilter": (0,),
    "setNameFilters": (0,),
    "setLabelText": (0,),
    "addTab": (1,),
    "insertTab": (2,),
    "addItem": (0,),
    "addItems": (0,),
    "addAction": (0,),
    "addSection": (0,),
    "showMessage": (0,),
    "setFormat": (0,),
    "setSuffix": (0,),
    "setPrefix": (0,),
}

#: Project helpers whose string parameters end up in widgets.
HELPERS: dict[str, tuple[int, ...]] = {
    "_pref_row": (1, 3),
    "_labeled_box": (0,),
    "_add_page_button": (1,),
    "_card": (0, 1),
    "_section": (0, 1),
    "_panel": (0,),
    "_say": (1,),
    "question_box": (1, 2),
    "add_box": (1,),
    "getOpenFileName": (1, 3),
    "getSaveFileName": (1, 3),
    "getExistingDirectory": (1,),
    "ask_redundancy": (1,),
    "redundancy_form": (1,),
    "_make_file_dialog": (0,),
}

#: MessageBox entry points: (title position, text position).
MESSAGE_BOXES = {"warning": (1, 2), "critical": (1, 2),
                 "information": (1, 2), "about": (1, 2)}


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


def _glossary(locale: str) -> dict[str, str]:
    """Optional English -> translation memory next to this tool."""
    path = Path(__file__).resolve().parent / f"locale_{locale}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in data.items() if k and v}


def _literal(node: ast.AST) -> str | None:
    """A plain string literal, never an f-string or concatenation."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _ui_string(value: str) -> bool:
    """Reject pure punctuation/spacing; keep anything a person can read."""
    return bool(value.strip()) and any(ch.isalpha() for ch in value)


def _from_call(node: ast.Call,  # noqa: C901, PLR0912 - a literal whitelist
               sink: list[tuple[str, str | None]]) -> None:
    func = node.func
    name = (func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute) else None)
    if name is None:
        return
    positions: tuple[int, ...] = ()
    context: str | None = None
    if name == "tr" and isinstance(func, ast.Name):
        # tr("text", "Context") / tr("text")
        positions = (0,)
        if len(node.args) > 1 and (ctx := _literal(node.args[1])):
            context = ctx
        elif any(k.arg == "context" and (ctx := _literal(k.value))
                 for k in node.keywords):
            context = ctx
    elif name in CONSTRUCTORS:
        positions = CONSTRUCTORS[name]
    elif name in SETTERS:
        positions = SETTERS[name]
    elif name in HELPERS:
        positions = HELPERS[name]
    elif name in MESSAGE_BOXES:
        positions = MESSAGE_BOXES[name]
        # msgbox.warning(...) / QMessageBox.warning(...)
        if not (isinstance(func, ast.Attribute)
                and (getattr(func.value, "id", "") in ("msgbox", "QMessageBox")
                     or getattr(func.value, "attr", "") in ("msgbox",
                                                           "QMessageBox"))):
            positions = ()
    if not positions:
        return
    for index in positions:
        if index < len(node.args) and (value := _literal(node.args[index])):
            sink.append((value, context))
    for keyword in node.keywords:
        if keyword.arg in ("text", "title", "label", "tooltip", "message",
                           "placeholder", "caption", "filter"):
            if (value := _literal(keyword.value)):
                sink.append((value, context))


def scanned_strings() -> tuple[list[str], dict[str, list[str]]]:
    """`(widget-context sources, {explicit context: sources})`."""
    gui: dict[str, None] = {}
    other: dict[str, dict[str, None]] = {}
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if SKIP_DIRS.intersection(path.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            hits: list[tuple[str, str | None]] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    _from_call(node, hits)
            for value, context in hits:
                if not _ui_string(value):
                    continue
                if context:
                    other.setdefault(context, {})[value] = None
                else:
                    gui[value] = None
    return list(gui), {name: list(values) for name, values in other.items()}


def catalogue(locale: str = "pt_BR",
              path: Path | None = None) -> ET.ElementTree:
    from raptor.gui.command_table import COMMANDS, LAYOUT
    entries: dict[str, list[str]] = {"Page": [], "Group": [], "Command": []}
    for page in LAYOUT:
        entries["Page"].append(page.title)
        for group in page.groups:
            entries["Group"].append(group.title)
    for spec in COMMANDS:
        if spec.text and spec.id not in ("home.undo", "home.redo"):
            entries["Command"].append(spec.text)

    gui, contexts = scanned_strings()
    entries.setdefault("Gui", []).extend(gui)
    for context_name, sources in contexts.items():
        entries.setdefault(context_name, []).extend(sources)
    for key in entries:
        entries[key] = list(dict.fromkeys(entries[key]))

    old = _existing(path) if path is not None else {}
    glossary = _glossary(locale)
    root = ET.Element("TS", {"version": "2.1", "language": locale})
    for context_name in entries:
        context = ET.SubElement(root, "context")
        ET.SubElement(context, "name").text = context_name
        for source in entries[context_name]:
            message = ET.SubElement(context, "message")
            ET.SubElement(message, "source").text = source
            translation = ET.SubElement(message, "translation")
            text = old.get((context_name, source), "") or glossary.get(
                source, "")
            if text:
                translation.text = text
            else:
                translation.set("type", "unfinished")
    ET.indent(root, space="  ")
    return ET.ElementTree(root)


def render(locale: str = "pt_BR", path: Path | None = None) -> str:
    body = ET.tostring(catalogue(locale, path).getroot(), encoding="unicode")
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE TS>\n' + body + "\n")


def _counts(tree: ET.ElementTree) -> tuple[int, int]:
    root = tree.getroot()
    total = done = 0
    for message in root.iter("message"):
        total += 1
        translation = message.find("translation")
        if translation is not None and translation.get("type") != "unfinished" \
                and (translation.text or "").strip():
            done += 1
    return done, total


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: gen_locale_catalogue.py FILE.ts [--write|--check|"
              "--coverage]", file=sys.stderr)
        return 2
    path = Path(args[0])
    locale = path.stem.removeprefix("raptor_") or "pt_BR"
    text = render(locale, path)
    if "--coverage" in args:
        done, total = _counts(catalogue(locale, path))
        percent = round(100 * done / total, 1) if total else 0.0
        print(f"{locale}: {done} of {total} strings translated "
              f"({percent}%)")
        return 0
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
