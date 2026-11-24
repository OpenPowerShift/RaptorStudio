"""UI localisation: Qt Linguist (.ts -> .qm), loaded at start-up.

Qt's own translator is the best fit for a Qt application: it is already in
the runtime, understands contexts and plurals, and Qt Linguist edits the
catalogue. The alternatives are compared in docs/LOCALISATION.md.

Two kinds of source string exist:

* data-driven chrome (``CommandSpec.text``, ``Page.title``, ``Group.title``)
  which a source scan cannot see, and
* ordinary widget text written as Python literals (``QLabel("Recent")``,
  ``setToolTip("...")`` ...).

``tools/gen_locale_catalogue.py`` collects both into one ``.ts`` file.  The
first group is translated where the actions and tabs are built.  The second
is translated after a widget tree is constructed by :func:`translate_widgets`
-- walking the tree is far more robust than remembering to wrap a thousand
literal call sites, and it keeps the source English.
"""
from __future__ import annotations

import re
import weakref
from functools import lru_cache
from pathlib import Path
from collections.abc import Iterable
from typing import cast

from PySide6.QtCore import (
    QCoreApplication, QLibraryInfo, QLocale, QModelIndex, QTranslator, Qt,
)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication,
    QComboBox, QDoubleSpinBox, QGroupBox, QLabel, QLineEdit, QMenu,
    QPlainTextEdit, QSpinBox, QStatusBar, QTabWidget, QTextEdit, QWidget,
)

from raptor.gui import chart_strings  # noqa: E402

_LOCALE_DIR = Path(__file__).resolve().parent / "locale"
_TRANSLATOR: QTranslator | None = None
_QT_TRANSLATOR: QTranslator | None = None
_CURRENT = "en"
#: True once any non-English catalogue was installed in this process: an
#: English pass is then needed to put the English source back on screen.
_EVER_TRANSLATED = False
_SUPPORTED = {
    "en": "English",
    "pt_BR": "Português (Brasil)",
}

#: The context the source scan writes widget literals to. Data-driven
#: strings keep their own contexts (Page/Group/Command) because that is how
#: they are looked up where the ribbon is built.
WIDGET_CONTEXT = "Gui"

#: Source code only: never translate the documentation or the MCP server.
#: (The generator enforces this; the constant documents the boundary.)
TRANSLATED_PACKAGES = ("raptor/gui",)

#: Windows already passed through :func:`translate_widgets`, so a focus
#: change inside the same window does not walk its whole tree again.
_TRANSLATED_WINDOWS: weakref.WeakSet = weakref.WeakSet()
#: Guards recursive translation while a widget setter emits focus events.
_TRANSLATING = False


def _catalogue(locale: str) -> Path:
    return _LOCALE_DIR / f"raptor_{locale}.qm"


def _catalogue_candidates(wanted: str) -> list[str]:
    """Locales to try for *wanted*, most specific first.

    ``pt_BR`` finds its own catalogue; a bare ``pt`` (or a system locale with
    no catalogue) falls back to Brazilian Portuguese, the one Portuguese
    catalogue shipped.  ``fr_CA`` would similarly fall back to ``fr``.
    """
    wanted = wanted.replace("-", "_")
    out = [wanted]
    if "_" in wanted:
        out.append(wanted.split("_", 1)[0])
    out.append("pt_BR" if wanted.split("_", 1)[0] == "pt" else wanted)
    return list(dict.fromkeys(out))


def _coverage(code: str) -> tuple[int, int]:
    """`(translated, total)` messages in the source `.ts`, or (0, 0)."""
    path = _LOCALE_DIR / f"raptor_{code}.ts"
    if not path.is_file():
        return 0, 0
    try:
        import xml.etree.ElementTree as ET
        root = ET.parse(path).getroot()
    except Exception:  # noqa: BLE001 - a damaged catalogue is just 0
        return 0, 0
    total = done = 0
    for message in root.iter("message"):
        total += 1
        translation = message.find("translation")
        if translation is not None and translation.get("type") != "unfinished" \
                and (translation.text or "").strip():
            done += 1
    return done, total


def coverage(code: str) -> dict[str, str | int | float | bool]:
    """Coverage of one shipped catalogue (a report, never a guess)."""
    done, total = _coverage(code)
    percent = round(100 * done / total, 1) if total else 0.0
    return {"locale": code, "translated": done, "total": total,
            "percent": percent, "complete": bool(total) and done == total}


def coverage_report() -> list[dict[str, str | int | float | bool]]:
    """`coverage()` for every non-English shipped catalogue."""
    report = []
    if _LOCALE_DIR.is_dir():
        for path in sorted(_LOCALE_DIR.glob("raptor_*.qm")):
            code = path.stem.removeprefix("raptor_")
            if code and code != "en":
                report.append(coverage(code))
    return report


def available_locales() -> list[tuple[str, str]]:
    """`(code, label)` for English and every shipped catalogue.

    A partial catalogue says so in its label; it is never presented as a
    complete translation.
    """
    out = [("en", "English")]
    for entry in coverage_report():
        code = str(entry["locale"])
        name = _SUPPORTED.get(code, code)
        if entry["total"] and not entry["complete"]:
            name = (f"{name} — {entry['translated']} of "
                    f"{entry['total']} strings ({entry['percent']}%)")
        out.append((code, name))
    return out


def current_locale() -> str:
    return _CURRENT


def _requested() -> str:
    try:
        from raptor.gui.ui import settings
        value = str(settings.get("appearance/language", "system") or "system")
    except Exception:  # noqa: BLE001 - before QSettings is usable
        value = "system"
    if value == "system":
        return QLocale.system().name()
    return value


def _qtbase_paths(code: str) -> list[Path]:
    """Where Qt may keep ``qtbase_<code>.qm`` (installed and bundled)."""
    out = [Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
           / f"qtbase_{code}.qm"]
    # PyInstaller puts the PySide6 translations next to the bundle; also
    # accept our own translations folder for source checkouts that copy it.
    out.append(Path(__file__).resolve().parent / "translations"
               / f"qtbase_{code}.qm")
    return out


def install(app: QApplication | None = None,  # noqa: C901
            locale: str | None = None) -> str:
    """Install the catalogue for *locale* (or the user's preference).

    A catalogue is looked up by full locale (`pt_BR`) and then by language
    (`pt`). A bare Portuguese locale falls back to Brazilian Portuguese.
    English, or a missing catalogue, removes the translator.
    """
    global _TRANSLATOR, _QT_TRANSLATOR, _CURRENT, _EVER_TRANSLATED
    app = app or cast("QApplication | None", QApplication.instance())
    if app is None:
        return "en"
    wanted = (locale or _requested()).replace("-", "_")
    chosen = "en"
    path: Path | None = None
    for code in _catalogue_candidates(wanted):
        candidate = _catalogue(code)
        if candidate.is_file():
            chosen, path = code, candidate
            break
    if _TRANSLATOR is not None:
        app.removeTranslator(_TRANSLATOR)
        _TRANSLATOR = None
    if _QT_TRANSLATOR is not None:
        app.removeTranslator(_QT_TRANSLATOR)
        _QT_TRANSLATOR = None
    loaded = False
    if path is not None:
        translator = QTranslator(app)
        if translator.load(str(path)):
            app.installTranslator(translator)
            _TRANSLATOR = translator
            loaded = True
    if chosen != "en":
        # Qt's own buttons and file dialogs (qtbase_pt_BR.qm). Try the exact
        # code, then its language (`pt` ships as `pt_BR`/`pt_PT`).
        for code in _catalogue_candidates(chosen):
            qtbase = next((p for p in _qtbase_paths(code) if p.is_file()),
                          None)
            if qtbase is not None:
                translator = QTranslator(app)
                if translator.load(str(qtbase)):
                    app.installTranslator(translator)
                    _QT_TRANSLATOR = translator
                break
    if not loaded and chosen != "en":
        # A requested catalogue that cannot be read must not leave the
        # application claiming it is translated.
        chosen = "en"
    _CURRENT = chosen
    _EVER_TRANSLATED = _EVER_TRANSLATED or chosen != "en"
    _TRANSLATED_WINDOWS.clear()
    _lookup.cache_clear()
    app.setProperty("raptorLocale", chosen)
    _install_focus_hook(app)
    return chosen


def _install_focus_hook(app: QApplication) -> None:
    """Translate a window the first time one of its widgets takes focus.

    Dialogs and docks that are built lazily (the File view, a run result,
    a modal built outside `exec_dialog`) are covered without an
    application-wide event filter -- the filter that used to be here
    received QtWebEngine's internal objects and segfaulted (#142). A focus
    signal carries an already-wrapped QWidget and is safe.
    """
    if getattr(app, "_raptor_focus_hook", False):
        return
    app._raptor_focus_hook = True  # type: ignore[attr-defined]

    def on_focus_changed(_old, new) -> None:
        if new is None or _TRANSLATING:
            return
        window = new.window() if isinstance(new, QWidget) else None
        if window is not None and window not in _TRANSLATED_WINDOWS:
            translate_widgets(window)

    app.focusChanged.connect(on_focus_changed)


# ---------------------------------------------------------------------------
# Text built at run time
# ---------------------------------------------------------------------------

#: Sentences the GUI assembles from model data ("Block: Pump  (ID 3)").  A
#: catalogue holds one entry per template, with ``%1``, ``%2`` .. for the
#: pieces; the value is the pieces that are themselves UI text (translated
#: in turn, e.g. an annotation's kind) as opposed to model data, which is
#: left exactly as written -- a block called "Mean" is a name, not a word.
#: A piece listed as 1 must translate or the template is skipped (so a
#: label that merely starts with "Failure" is left alone); -1 is a unit,
#: translated when known and kept otherwise; 100 is a number, or the template
#: is skipped ("Phase 2" is the app's words, "Phase Alpha" is somebody's).
#: A widget that shows model data is marked `raptor_i18n_data` and its text
#: is never read as a template at all.
TEMPLATES: dict[str, tuple[int, ...]] = {
    "Block: %1  (ID %2)": (),
    "Node: %1  (ID %2)": (),
    "Hierarchy: %1  (ID %2)": (),
    "Event: %1  (ID %2)": (),
    "Annotation: %1  (ID %2)": (1,),
    "Block %1 \u2014 not found": (),
    "Node %1 \u2014 not found": (),
    "Select a block, node, event or hierarchy on the canvas to see and edit "
    "its properties.\n\nThis model: %1 blocks \u00b7 %2 nodes \u00b7 %3 events "
    "\u00b7 %4 hierarchies": (),
    "Select a block, node, event or hierarchy on the canvas to see and edit "
    "its properties.\n\nThis model: %1 blocks \u00b7 %2 nodes \u00b7 %3 events "
    "\u00b7 %4 hierarchies \u00b7 time in %5": (-5,),
    # A distribution's parameter, named for what it describes: "Failure Mean".
    "Failure %1": (1,),
    "Repair %1": (1,),
    "Pre-LDT %1": (1,),
    "Post-LDT %1": (1,),
    "PM %1": (1,),
    "Use a saved rate or formula for %1": (1,),
    "Unknown (%1)": (),
    "Unknown distribution code #%1 \u2014 pick one to fix it": (),
    "Phase %1": (100,),
    " of %1": (100,),
    "Flow generated = %1": (),
    "Random number stream. System A..J are shared streams; independent "
    "streams draw from seeds 1..%1.": (),
    "System stream %1": (),
    "Stream %1 antithetic": (),
    "Stream %1": (),
    "%1 block - %2": (),
    "Dependency percentages must sum to 100 (got %1)": (),
    # Messages for an entry the Inspector refused.  The field is named by
    # its (translated) label; a column name or an engine message stays as
    # written.
    "%1 must be greater than 0": (1,),
    "%1 must be between 0 and 999,999,999": (),
    "%1. Not applied until the other fields make it valid.": (),
    "write %1/%2 for an even share, or a number": (),
    "%1 takes %2 or %3/%4; %5 belongs to the other column": (),
    "enter a flow (0 or more), %1, or %2/%3": (),
    "%1: all of the flow generated at Start (Project Settings). %2/%3: an "
    "even share across this node's %4 incoming paths. Or a whole number: a "
    "fixed flow.": (),
}

TEMPLATES.update(chart_strings.TEMPLATES)

#: The laws a block carries; the Inspector labels their fields with these.
LAW_USES: tuple[str, ...] = ("Failure", "Repair", "Pre-LDT", "Post-LDT", "PM")

#: Time-unit words a model may carry (and the forms built from them).
UNIT_WORDS: tuple[str, ...] = (
    "seconds", "minutes", "hours", "kilo-hrs", "mega-hrs", "giga-hrs",
    "tera-hrs", "days", "months", "years", "cycles", "units", "miles",
    "kilometers", "PPM", "PPB", "pool-managed",
)


def dynamic_sources() -> list[str]:
    """Every catalogue source that no literal in the code spells out.

    The templates above, plus the distribution vocabulary kept in the
    engine package (the catalogue scan only reads ``raptor/gui``).  Used
    by ``tools/gen_locale_catalogue.py``.
    """
    from raptor.core.distro_rules import DISTRO_NAMES, DISTRO_PARAM_LABELS
    out = list(TEMPLATES)
    out += chart_strings.SOURCES
    out += chart_strings.CAPTIONS
    out += UNIT_WORDS
    out += [name for name in DISTRO_NAMES.values()]
    out += sorted({lab for labs in DISTRO_PARAM_LABELS.values()
                   for lab in labs if lab})
    out += ["Rate", "Mu", "Sigma", "None (unrepairable)"]
    # The five laws a block carries, named in the sentences built around
    # them ("Enter data... (Failure)").  Spelt out, not composed: each
    # reads differently in other languages.
    for use in LAW_USES:
        out += [f"{use} params:", f"Enter data\u2026 ({use})"]
    # An annotation's colour rows: what each one is called.
    for part in ("Line", "Border", "Fill", "Text"):
        low = part.lower()
        out += [f"{part} colour", f"Pick a {low} colour",
                f"Draw no {low} at all (stores null)"]
    out += ["Line width:", "Border width:"]
    # An arrow's two ends.
    for end in ("from", "to"):
        out += [f"Click, then click an element or annotation to attach the "
                f"arrow's {end} end to it (Esc cancels)",
                f"Detach the {end} end: it stays where it is and no longer "
                f"follows anything"]
    return list(dict.fromkeys(out))


@lru_cache(maxsize=1)
def _template_patterns() -> tuple[tuple[re.Pattern[str], str, tuple[int, ...]], ...]:
    """`(regex, template, translated pieces)`, most literal text first."""
    built = []
    for template, translated in TEMPLATES.items():
        pieces = re.split(r"(%\d)", template)
        body = "".join("(.+?)" if re.fullmatch(r"%\d", piece)
                       else re.escape(piece) for piece in pieces)
        literal = sum(len(p) for p in pieces if not re.fullmatch(r"%\d", p))
        built.append((literal, re.compile(f"^{body}$", re.DOTALL), template,
                      translated))
    built.sort(key=lambda item: -item[0])
    return tuple((rx, tpl, tr_) for _n, rx, tpl, tr_ in built)


_UNIT_SUFFIX = re.compile(r"^(?P<stem>.+?) \((?P<unit>.+)\)(?P<tail>:?)$",
                          re.DOTALL)


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _unit(context: str, unit: str) -> str:
    """A unit as written in a label: ``hours``, ``per hours``, ``ln(hours)``."""
    got = QCoreApplication.translate(context, unit)
    if got != unit:
        return got
    if unit.startswith("per "):
        return "por " + _unit(context, unit[4:])
    if unit.startswith("ln(") and unit.endswith(")"):
        return f"ln({_unit(context, unit[3:-1])})"
    return unit


def _from_template(context: str, source: str) -> str | None:
    """*source* read as one of the :data:`TEMPLATES`, or None."""
    for rx, template, translated in _template_patterns():
        match = rx.match(source)
        if match is None:
            continue
        form = QCoreApplication.translate(context, template)
        if form == template and "%" not in form:
            continue
        pieces = list(match.groups())
        complete = True
        for index in translated:
            if index == 100:
                if not _is_number(pieces[0]):
                    complete = False
                continue
            at = abs(index) - 1
            if at >= len(pieces):
                continue
            if index < 0:
                pieces[at] = _unit(context, pieces[at])
                continue
            got = _lookup(context, pieces[at], _CURRENT)
            complete = complete and got != pieces[at]
            pieces[at] = got
        if complete:
            return _fill(form, pieces)
    return None


def translate_template(source: str) -> str | None:
    """*source* read as one of the :data:`TEMPLATES` and translated, or None."""
    return _from_template(WIDGET_CONTEXT, source) if _CURRENT != "en" else None


def translate_unit(unit: str) -> str:
    """A unit word (``hours``, ``per hours``) in the GUI language."""
    return _unit(WIDGET_CONTEXT, unit) if _CURRENT != "en" else unit


def _fill(form: str, pieces: list[str]) -> str:
    """*form* with ``%1``, ``%2`` .. replaced by *pieces*."""
    def piece(match: re.Match[str]) -> str:
        at = int(match.group(1)) - 1
        return pieces[at] if at < len(pieces) else match.group(0)
    return re.sub(r"%(\d)", piece, form)


@lru_cache(maxsize=8192)
def _lookup(context: str, source: str, locale: str) -> str:
    """The translation of *source* (the English), or *source* itself.

    Order: an exact catalogue entry; the label without its colon; a label
    with a unit in brackets -- ``Failure Mean (hours):`` -- whose stem is
    translated and whose unit follows it; a template whose pieces are
    filled in from the text.  *locale* is only part of the cache key.
    """
    del locale
    got = QCoreApplication.translate(context, source)
    if got != source or context != WIDGET_CONTEXT:
        return got
    # A trailing colon belongs to the label, not to what it names.
    if source.endswith(":") and len(source) > 1:
        head = _lookup(context, source[:-1], _CURRENT)
        if head != source[:-1]:
            return head + ":"
    match = _UNIT_SUFFIX.match(source)
    if match is not None:
        stem, unit, tail = match["stem"], match["unit"], match["tail"]
        head = _lookup(context, stem, _CURRENT)
        if head != stem or _is_number(stem):
            return f"{head} ({_unit(context, unit)}){tail}"
    filled = _from_template(context, source)
    return source if filled is None else filled


def translate_source(source: str, context: str = WIDGET_CONTEXT) -> str:
    """Translate widget text, including text built at run time."""
    if not source or _CURRENT == "en":
        return source
    return _lookup(context, source, _CURRENT)


def tr(text: str, context: str = "General") -> str:
    """Translate one source string; returns it unchanged without a catalogue.

    A missing entry in *context* falls back to the shared widget context,
    so a string wrapped by hand still finds the catalogue entry the source
    scan produced.
    """
    if not text:
        return text
    translated = QCoreApplication.translate(context, text)
    if translated == text and context != WIDGET_CONTEXT:
        translated = QCoreApplication.translate(WIDGET_CONTEXT, text)
    if translated == text and _CURRENT != "en":
        translated = _lookup(WIDGET_CONTEXT, text, _CURRENT)
    return translated


def qt_plural(context: str, singular: str, plural: str, n: int) -> str:
    # Qt chooses the numerus form from `n`; `plural` is documentation for
    # the caller. The TS entry uses <numerusform> children.
    del plural
    return QCoreApplication.translate(context, singular, None, n)


# ---------------------------------------------------------------------------
# Widget-tree translation
# ---------------------------------------------------------------------------


def _originals(obj) -> dict[str, str]:
    """The English source of each translated property of *obj*."""
    store = getattr(obj, "_raptor_i18n_originals", None)
    if store is None:
        store = {}
        obj._raptor_i18n_originals = store  # noqa: SLF001
    return store


def _written(obj) -> dict[str, str]:
    """The text this module last put on each property of *obj*."""
    store = getattr(obj, "_raptor_i18n_written", None)
    if store is None:
        store = {}
        obj._raptor_i18n_written = store  # noqa: SLF001
    return store


def _translate_property(obj, key: str, get, set_, *, context: str) -> None:
    """Translate one text property, remembering the source for a rerun."""
    try:
        current = get()
    except (RuntimeError, AttributeError):
        return
    if not isinstance(current, str) or not current:
        return
    originals = _originals(obj)
    written = _written(obj)
    source = originals.get(key)
    if source is None or (key in written and current != written[key]):
        # First sight of this property, or the code replaced the text
        # since (a floated dock names its model, a label counts runs):
        # the new text is the English source, not a translation to undo.
        source = current
        originals[key] = current
    target = (_lookup(context, source, _CURRENT)
              if _CURRENT != "en" else source)
    if target and target != current:
        try:
            set_(target)
        except (RuntimeError, AttributeError):
            return
        current = target
    written[key] = current


def _translate_headers(view: QAbstractItemView) -> None:  # noqa: C901
    """Translate a table's or tree's header labels, in place.

    Headers are model data, not widget text, so the widget pass above never
    saw them. They are tracked on the model (one entry per section) because
    that is where the text lives and where a rebuilt model starts again.
    """
    model = view.model()
    if model is None:
        return
    # On the view, not the model: a QTableWidget hands back a fresh Python
    # wrapper for its model on every call, so attributes set there are lost
    # and the English source could never be restored.
    originals = _originals(view)
    written = _written(view)
    try:
        try:
            count = int(model.columnCount())
        except TypeError:
            # A model subclass whose override takes a parent index.
            count = int(model.columnCount(QModelIndex()))
    except (TypeError, RuntimeError):
        return
    # Horizontal only: a vertical header is a row label here (a block's
    # name, a sheet row), i.e. data, and translating it would corrupt the
    # value the page reads back.
    for section in range(count):
        try:
            text = model.headerData(section, Qt.Orientation.Horizontal)
        except (RuntimeError, AttributeError):
            return
        if not isinstance(text, str) or not text:
            continue
        key = f"col:{section}"
        source = originals.get(key)
        if source is None or (key in written and text != written[key]):
            source = text
            originals[key] = text
        target = (_lookup(WIDGET_CONTEXT, source, _CURRENT)
                  if _CURRENT != "en" else source)
        if target and target != text:
            model.setHeaderData(section, Qt.Orientation.Horizontal, target)
            text = target
        written[key] = text


def _translate_combo(box: QComboBox, only: Iterable[int] | None = None) -> None:
    """Translate combo entries, keeping their (programmatic) item data.

    *only* limits it to those entries (a combo of model names whose first
    entry is chrome).
    """
    originals = _originals(box)
    blocked = box.blockSignals(True)
    try:
        _translate_combo_items(box, originals, only)
    finally:
        box.blockSignals(blocked)


def _translate_indexed(obj, key: str, current: str, set_) -> None:
    """Translate one indexed entry (a combo item, a tab title).

    The entry is tracked like a widget property: when the code has
    replaced its text since this module last wrote it (a combo refilled
    for a new model, the Details tabs rebuilt for a new result set), the
    new text is the English source. Keying the source by position alone
    put the FIRST text ever seen back on whatever now sits at that index.
    """
    originals = _originals(obj)
    written = _written(obj)
    source = originals.get(key)
    if source is None or (key in written and current != written[key]):
        source = current
        originals[key] = current
    target = (_lookup(WIDGET_CONTEXT, source, _CURRENT)
              if _CURRENT != "en" else source)
    if target and target != current:
        set_(target)
        current = target
    written[key] = current


def _translate_combo_items(box: QComboBox, originals: dict,
                          only: Iterable[int] | None = None) -> None:
    del originals
    for index in (range(box.count()) if only is None else only):
        if not 0 <= index < box.count():
            continue
        _translate_indexed(box, f"item:{index}", box.itemText(index),
                           lambda text, i=index: box.setItemText(i, text))


def source_text(obj, key: str, current: str) -> str:
    """The English text behind *current* on an indexed entry (a tab title).

    *current* is what the widget shows now: what this module wrote there
    is a translation of the source, anything else is a new source.
    """
    written = _written(obj)
    if key in written and current == written[key]:
        return _originals(obj).get(key, current)
    return current


def needs_pass() -> bool:
    """Whether :func:`translate_widgets` can change anything right now."""
    return _CURRENT != "en" or _EVER_TRANSLATED


def translate_tab_strip(tabs: QTabWidget) -> None:
    """Translate just a tab widget's titles and tool tips, not its pages."""
    if needs_pass():
        _translate_tabs(tabs)


def _translate_tabs(tabs: QTabWidget) -> None:
    originals = _originals(tabs)
    blocked = tabs.blockSignals(True)
    try:
        _translate_tab_items(tabs, originals)
    finally:
        tabs.blockSignals(blocked)


def _translate_tab_items(tabs: QTabWidget, originals: dict) -> None:
    del originals
    for index in range(tabs.count()):
        _translate_indexed(tabs, f"tab:{index}", tabs.tabText(index),
                           lambda text, i=index: tabs.setTabText(i, text))
        tip = tabs.tabToolTip(index)
        if tip:
            _translate_indexed(
                tabs, f"tabtip:{index}", tip,
                lambda text, i=index: tabs.setTabToolTip(i, text))


def translate_widgets(root: QWidget) -> None:  # noqa: C901, PLR0912 - one flat pass
    """Translate the English text in *root*'s widget tree, in place.

    Idempotent: the English source is kept on each object, so calling it
    again with another language (or with English) restores the source
    rather than translating a translation.  Only the chrome properties are
    touched -- labels, buttons, group boxes, tab titles, combo items,
    placeholders, tooltips, window titles and QActions -- never model data
    a widget happens to display.
    """
    global _TRANSLATING
    if root is None or _TRANSLATING or not isinstance(root, QWidget):
        return
    if _CURRENT == "en" and not _EVER_TRANSLATED:
        # English is the source: a walk could only rewrite text with
        # itself. Skipping it keeps an English session free of the pass
        # entirely (it runs after every result set and on focus changes).
        _TRANSLATED_WINDOWS.add(root.window())
        return
    _TRANSLATING = True
    try:
        widgets = [root, *root.findChildren(QWidget)]
        for widget in widgets:
            if widget.property("raptor_ignore_i18n"):
                continue
            _translate_property(widget, "windowTitle", widget.windowTitle,
                                widget.setWindowTitle,
                                context=WIDGET_CONTEXT)
            if isinstance(widget, QGroupBox):
                _translate_property(widget, "title", widget.title,
                                    widget.setTitle, context=WIDGET_CONTEXT)
            shows_data = bool(widget.property("raptor_i18n_data"))
            if isinstance(widget, (QLabel, QAbstractButton)) and not shows_data:
                _translate_property(widget, "text", widget.text,
                                    widget.setText, context=WIDGET_CONTEXT)
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
                _translate_property(widget, "placeholder",
                                    widget.placeholderText,
                                    widget.setPlaceholderText,
                                    context=WIDGET_CONTEXT)
            if isinstance(widget, QTabWidget):
                _translate_tabs(widget)
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)) and not shows_data:
                _translate_property(widget, "suffix", widget.suffix,
                                    widget.setSuffix, context=WIDGET_CONTEXT)
            if isinstance(widget, QComboBox):
                # A combo listing model names (hierarchies, pools, groups)
                # keeps its tooltip translated but never its items: a
                # block called "Start" is a name, not the word "Start".
                # The entries listed in `raptor_i18n_ui_items` (the "(none)"
                # in front of the names) are chrome and still translate.
                if not widget.property("raptor_i18n_data_items"):
                    _translate_combo(widget)
                else:
                    _translate_combo(widget, widget.property(
                        "raptor_i18n_ui_items") or ())
            if isinstance(widget, QAbstractItemView):
                _translate_headers(widget)
            if isinstance(widget, QWidget):
                _translate_property(widget, "accessibleName",
                                    widget.accessibleName,
                                    widget.setAccessibleName,
                                    context=WIDGET_CONTEXT)
                _translate_property(widget, "accessibleDescription",
                                    widget.accessibleDescription,
                                    widget.setAccessibleDescription,
                                    context=WIDGET_CONTEXT)
                _translate_property(widget, "toolTip", widget.toolTip,
                                    widget.setToolTip,
                                    context=WIDGET_CONTEXT)
                _translate_property(widget, "statusTip", widget.statusTip,
                                    widget.setStatusTip,
                                    context=WIDGET_CONTEXT)
        actions = [*root.findChildren(QAction)]
        menus = root.findChildren(QMenu)
        for action in actions:
            _translate_property(action, "text", action.text,
                                action.setText, context=WIDGET_CONTEXT)
            _translate_property(action, "toolTip", action.toolTip,
                                action.setToolTip, context=WIDGET_CONTEXT)
            _translate_property(action, "statusTip", action.statusTip,
                                action.setStatusTip,
                                context=WIDGET_CONTEXT)
        for menu in menus:
            _translate_property(menu, "title", menu.title, menu.setTitle,
                                context=WIDGET_CONTEXT)
        _TRANSLATED_WINDOWS.add(root.window())
    finally:
        _TRANSLATING = False


def name_dialog(dialog) -> None:
    """Give a dialog the accessible name a screen reader announces.

    Its title is the one translated name it already has; a stand-in passed
    by a test or an embedder (no QWidget) is left alone.
    """
    if (hasattr(dialog, "setAccessibleName")
            and not dialog.accessibleName() and dialog.windowTitle()):
        dialog.setAccessibleName(dialog.windowTitle())


def exec_translated(dialog):
    """Translate a dialog's tree, then run it modally.

    Used by the few dialogs that build and execute themselves outside
    `modality.exec_dialog` (a QMessageBox, a count form, the library save
    box). Returns the dialog's `exec()` code.
    """
    translate_widgets(dialog)
    name_dialog(dialog)
    return dialog.exec()


class TranslatingStatusBar(QStatusBar):
    """A status bar that translates the static messages it is given.

    Status messages are written where the event happens (245 call sites),
    many as f-strings that cannot be looked up. A message that matches a
    catalogue entry is translated here; a dynamic one is left as written,
    which is the honest fallback rather than a half-substituted sentence.
    """

    def showMessage(self, message: str, timeout: int | None = 0) -> None:  # noqa: N802
        super().showMessage(tr(message, WIDGET_CONTEXT), timeout or 0)
