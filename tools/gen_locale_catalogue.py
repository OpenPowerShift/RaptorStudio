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
    "_add_table_header": (1,),
}

#: MessageBox entry points: (title position, text position).
MESSAGE_BOXES = {"warning": (1, 2), "critical": (1, 2),
                 "information": (1, 2), "about": (1, 2)}

#: Calls whose argument at each position is a list/tuple of UI strings.
#: (positions, because `add_table` takes a title then the headers).
SEQUENCE_HELPERS: dict[str, tuple[int, ...]] = {
    "setHorizontalHeaderLabels": (0,),
    "_make_table": (0,),
    "add_table": (0, 1),
}


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
    if name in SEQUENCE_HELPERS:
        # setHorizontalHeaderLabels([...]) / header_labels((...)):
        # every element of the list/tuple is a UI string.
        for index in SEQUENCE_HELPERS[name]:
            if index >= len(node.args):
                continue
            arg = node.args[index]
            if isinstance(arg, (ast.List, ast.Tuple)):
                for element in arg.elts:
                    if (value := _literal(element)) and _ui_string(value):
                        sink.append((value, None))
            elif (value := _literal(arg)) and _ui_string(value):
                # a title argument next to the list ("Pools", "Events")
                sink.append((value, None))
        return
    positions: tuple[int, ...] = ()
    context: str | None = None
    if name == "tr" and isinstance(func, (ast.Name, ast.Attribute)):
        # tr("text", "Context") / tr("text"), however it is imported:
        # `tr(...)` and `i18n.tr(...)` are both calls to the same helper.
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


#: The report's column vocabulary (Raptor 7's Final Results grammar). The
#: GUI humanises these into table headers, so the strings never appear as
#: literals in `raptor/gui` and the scan cannot see them. Keep in step with
#: the engine's report writers; the i18n tests check the golden reports.
REPORT_COLUMNS: tuple[str, ...] = (
    "%Armed", "%Blue", "%Brown", "%Done", "%Failure", "%Green", "%Idle",
    "%Orange", "%PM", "%PMHold", "%Red", "%RepHold", "%Repairing",
    "%Running", "%Standby", "%Success", "%Yellow", "%Failures",
    "%Downtime", "%Cost", "AoMax", "AoMean",
    "AoMin", "AoSEM", "AoStdDev", "AveCapacity", "AveFlow", "AveLostFlow",
    "AveNumRes", "AveStock", "AveUsed", "Block", "CapableRate", "Disposal",
    "DoMax", "DoMean", "DoMin", "DoSEM", "DoStdDev", "Done", "EmerShip",
    "EndNumRes", "EndStock", "Event", "Events", "Failure", "FlowLoss",
    "Good", "HierNum", "Hierarchy", "Idle", "InHier", "InitNumRes",
    "InitNumSpares", "InitSpares", "Initial", "Item", "MDT", "MTBDE",
    "MaxCapacity", "MaxFlow", "MaxLostFlow", "MaxNumRes", "MaxStock",
    "MaxUsed", "Maximum", "Mean", "MinCapacity", "MinFlow", "MinLostFlow",
    "MinNumRes", "MinStock", "MinUsed", "Minimum", "NewSpares", "Node",
    "NumDelays", "Operating", "PM", "PMHold", "PerTimeCost", "PerUseCost",
    "Purchase", "RMax", "RMean", "RMin", "RSEM", "RStdDev", "RepHold",
    "Repair", "Resources", "SEM", "SparePools", "SpareSource", "Sparing",
    "St.Dev", "StDev", "Standby", "Success", "Total", "UsageRate", "Value",
    "blockName", "poolName", "resourceName",
)

#: Header words the GUI adds itself, beside the report's own columns.
EXTRA_HEADERS: tuple[str, ...] = ("Measure",)


def report_column_labels() -> list[str]:
    """The humanised headers the GUI builds from the report's columns."""
    try:
        from raptor.api.presentation import humanise
    except Exception:  # noqa: BLE001 - the generator must still run
        return []
    labels = {humanise(column) for column in REPORT_COLUMNS}
    labels |= set(EXTRA_HEADERS)
    return sorted(labels)


def presentation_labels() -> list[str]:
    """Report section and table names the GUI shows as tab/card titles.

    They are defined in `raptor.api.presentation`, which stays English for
    the documentation and the MCP server, so the GUI scan cannot see them;
    the GUI copies them into tabs and table headers, where they are
    translated like any other chrome.
    """
    try:
        from raptor.api import presentation as p
    except Exception:  # noqa: BLE001 - the generator must still run
        return []
    values = set(p.SECTIONS.values())
    values |= set(getattr(p, "_SECTION_DEFAULT", {}).values())
    values |= set(getattr(p, "_TABLE_NAMES", {}).values())
    values |= set(getattr(p, "_PARTS", {}).values())
    # "Block availability", "Node states", ...: a part plus one of the
    # lower-case table names. The GUI composes these at run time, so the
    # scan cannot see them; the vocabulary is the API's own.
    words = [v for v in getattr(p, "_TABLE_NAMES", {}).values()
             if v and v.islower()]
    for part in getattr(p, "_PARTS", {}).values():
        for word in words:
            values.add(f"{part} {word}")
    return sorted(v for v in values if _ui_string(v))


#: Files whose every user-facing literal is catalogued, not only those passed
#: to a known constructor or setter.  The Inspector builds its forms through
#: its own row helpers and data tables of labels, which no call whitelist can
#: follow; its code is mostly UI text, so the filter below keeps the text and
#: drops identifiers, section keys and style sheets.
SWEEP_FILES = ("ui/inspector.py", "ui/inspector_block_mixin.py",
               "ui/inspector_node_mixin.py", "ui/inspector_rows_mixin.py",
               "ui/inspector_annotation_mixin.py", "ui/inspector_common.py")

#: Literals the sweep keeps out: file-format keys and codes that read like
#: words (a combo's item data, a section name, a column).
SWEEP_IGNORE = frozenset({
    "NONE", "INFINITE", "CUSTOM", "NA", "N/A", "COMMENTS", "TRIGGERS",
    "PHASES", "MAXFLOW", "FULLFLOW", "Anc", "PPM", "PPB",
})

#: UI text the filter cannot tell from a key (a lower-case first letter).
SWEEP_EXTRA = ("k* Target", "not a number", "a priority from 1 to 99")


def _sweep_text(value: str) -> bool:
    """Whether a literal in a SWEEP_FILES module reads as UI text."""
    if value in SWEEP_IGNORE or not _ui_string(value):
        return False
    if value != value.strip() or "{" in value or value.endswith(";"):
        return False                      # an f-string piece, a style sheet
    if any(css in value for css in ("background:", "border:", "color:")):
        return False
    if value.startswith("#") or value.startswith(".") or "_" in value:
        return False
    if " " not in value and value.isidentifier():
        # A bare word: UI text only when it is capitalised ("Identity"),
        # not a key ("block"), a CamelCase object name or a column name.
        return (value[0].isupper() and value[1:].islower()) or \
            value in ("PM", "ID", "LDT", "X", "Y")
    return value[0].isupper() or not value[0].isascii() or value[0] in "%(?"


def sweep_strings() -> list[str]:
    out: dict[str, None] = {}
    for name in SWEEP_FILES:
        path = ROOT / "raptor" / "gui" / name
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        skip: set[int] = set()
        for node in ast.walk(tree):
            # Docstrings, and every piece of an f-string (a fragment alone
            # is not a sentence; runtime templates live in i18n.TEMPLATES).
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) \
                    and node.body and isinstance(node.body[0], ast.Expr):
                skip.add(id(node.body[0].value))
            if isinstance(node, ast.JoinedStr):
                skip.update(id(v) for v in ast.walk(node))
            if isinstance(node, ast.Subscript):
                skip.update(id(v) for v in ast.walk(node.slice))
            if isinstance(node, ast.Call) and _called_name(node) in (
                    "get", "getattr", "setattr", "hasattr", "setProperty",
                    "setObjectName", "property", "setStyleSheet", "section",
                    "setdefault", "pop"):
                skip.update(id(v) for a in node.args for v in ast.walk(a))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in skip and _sweep_text(node.value):
                out[node.value] = None
    out.update(dict.fromkeys(SWEEP_EXTRA))
    # The one sentence the Inspector takes from the model-edit rules.
    from raptor.gui.model_edit import POOLED_COST_TIP
    out[POOLED_COST_TIP] = None
    return list(out)


def scanned_strings() -> tuple[list[str], dict[str, list[str]]]:  # noqa: C901, PLR0912
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
            for table in _header_constants(tree):
                for value in table:
                    if _ui_string(value):
                        gui[value] = None
    return list(gui), {name: list(values) for name, values in other.items()}


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _header_constant_names(tree: ast.AST) -> set[str]:
    """Names (``COLUMNS``, ``self._HEADERS``) passed to a header setter."""
    wanted: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name not in SEQUENCE_HELPERS:
            continue
        for index in SEQUENCE_HELPERS[name]:
            if index >= len(node.args):
                continue
            arg = node.args[index]
            if isinstance(arg, ast.Name):
                wanted.add(arg.id)
            elif isinstance(arg, ast.Attribute):
                wanted.add(arg.attr)
    return wanted


def _header_constants(tree: ast.AST) -> list[list[str]]:
    """String lists that are handed to a header setter by name.

    ``header_labels(COLUMNS)`` and ``setHorizontalHeaderLabels(headers)``
    name their list, so the English labels are the literals in its
    assignment (usually a module or class constant).
    """
    wanted = _header_constant_names(tree)
    if not wanted:
        return []
    out: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [
            node.target]
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        if not any(isinstance(t, ast.Name) and t.id in wanted
                   for t in targets):
            continue
        strings = [s for e in node.value.elts if (s := _literal(e))]
        if strings:
            out.append(strings)
    return out


def catalogue(locale: str = "pt_BR",  # noqa: C901
              path: Path | None = None) -> ET.ElementTree:
    from raptor.gui.command_table import COMMANDS, LAYOUT
    entries: dict[str, list[str]] = {"Page": [], "Group": [], "Command": []}
    for page in LAYOUT:
        entries["Page"].append(page.title)
        for group in page.groups:
            entries["Group"].append(group.title)
    for spec in COMMANDS:
        if spec.id in ("home.undo", "home.redo"):
            continue
        if spec.text:
            entries["Command"].append(spec.text)
        if spec.tooltip:
            entries["Command"].append(spec.tooltip)

    gui, contexts = scanned_strings()
    entries.setdefault("Gui", []).extend(gui)
    entries.setdefault("Gui", []).extend(sweep_strings())
    from raptor.gui import i18n
    entries.setdefault("Gui", []).extend(i18n.dynamic_sources())
    entries.setdefault("Gui", []).extend(presentation_labels())
    entries.setdefault("Gui", []).extend(report_column_labels())
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


def _lrelease() -> str | None:
    """The Qt compiler shipped with PySide, on PATH or beside the venv."""
    import shutil
    found = shutil.which("pyside6-lrelease")
    if found:
        return found
    sibling = Path(sys.executable).with_name("pyside6-lrelease")
    return str(sibling) if sibling.is_file() else None


def _qm_is_current(ts_path: Path) -> bool:
    """A typo or a translator's edit must not ship as an un-compiled `.qm`."""
    qm_path = ts_path.with_suffix(".qm")
    if not qm_path.is_file():
        return False
    lrelease = _lrelease()
    if lrelease is None:
        # No compiler installed: the .ts is still checked, and the build
        # step is where a missing .qm shows up.
        return True
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / qm_path.name
        done = subprocess.run(
            [lrelease, str(ts_path), "-qm", str(candidate)],
            capture_output=True, text=True)
        if done.returncode != 0:
            return False
        return candidate.read_bytes() == qm_path.read_bytes()


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
    if not (path.is_file() and path.read_text(encoding="utf-8") == text):
        print(f"{path} is stale; run with --write", file=sys.stderr)
        return 1
    if not _qm_is_current(path):
        print(f"{path.with_suffix('.qm')} is stale; compile it with "
              "pyside6-lrelease (see docs/LOCALISATION.md)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
