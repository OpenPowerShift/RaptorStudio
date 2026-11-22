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

import weakref
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication, QLibraryInfo, QLocale, QModelIndex, QTranslator, Qt,
)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication, QComboBox, QGroupBox,
    QLabel, QLineEdit, QMenu, QPlainTextEdit, QStatusBar, QTabWidget,
    QTextEdit, QWidget,
)

_LOCALE_DIR = Path(__file__).resolve().parent / "locale"
_TRANSLATOR: QTranslator | None = None
_QT_TRANSLATOR: QTranslator | None = None
_CURRENT = "en"
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


def coverage(code: str) -> dict[str, int | float | bool]:
    """Coverage of one shipped catalogue (a report, never a guess)."""
    done, total = _coverage(code)
    percent = round(100 * done / total, 1) if total else 0.0
    return {"locale": code, "translated": done, "total": total,
            "percent": percent, "complete": bool(total) and done == total}


def coverage_report() -> list[dict[str, int | float | bool]]:
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
    global _TRANSLATOR, _QT_TRANSLATOR, _CURRENT
    app = app or QApplication.instance()
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
    return translated


def qt_plural(context: str, singular: str, plural: str, n: int) -> str:
    # Qt chooses the numerus form from `n`; `plural` is documentation for
    # the caller. The TS entry uses <numerusform> children.
    del plural
    return QCoreApplication.translate(context, singular, None, n)


def header_labels(labels) -> list[str]:
    """Translate table/tree header labels as they are set.

    Header data lives in the item model, and this PySide build cannot
    marshal ``headerDataChanged`` (Qt.Orientation) to Python, so a change
    made after the window's first pass cannot be noticed. Translating at
    the point of setting keeps every table, including one repopulated
    after a run, in the chosen language.
    """
    return [tr(str(label)) for label in labels]


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
    target = (QCoreApplication.translate(context, source)
              if _CURRENT != "en" else source)
    if target and target != current:
        try:
            set_(target)
        except (RuntimeError, AttributeError):
            return
        current = target
    written[key] = current


def _translate_headers(view: QAbstractItemView) -> None:
    """Translate a table's or tree's header labels, in place.

    Headers are model data, not widget text, so the widget pass above never
    saw them. They are tracked on the model (one entry per section) because
    that is where the text lives and where a rebuilt model starts again.
    """
    model = view.model()
    if model is None:
        return
    originals = _originals(model)
    written = _written(model)

    def count_of(kind: str, orientation: Qt.Orientation) -> int:
        """Rows/columns, whichever overload this model exposes."""
        method = model.columnCount if kind == "col" else model.rowCount
        try:
            return int(method())
        except TypeError:
            # A model subclass whose override takes a parent index.
            return int(method(QModelIndex()))

    for orientation, tag in ((Qt.Orientation.Horizontal, "col"),
                             (Qt.Orientation.Vertical, "row")):
        try:
            count = count_of(tag, orientation)
        except (TypeError, RuntimeError):
            continue
        for section in range(count):
            try:
                text = model.headerData(section, orientation)
            except (RuntimeError, AttributeError):
                return
            if not isinstance(text, str) or not text:
                continue
            key = f"{tag}:{section}"
            source = originals.get(key)
            if source is None or (key in written and text != written[key]):
                source = text
                originals[key] = text
            target = (QCoreApplication.translate(WIDGET_CONTEXT, source)
                      if _CURRENT != "en" else source)
            if target and target != text:
                model.setHeaderData(section, orientation, target)
                text = target
            written[key] = text


def _translate_combo(box: QComboBox) -> None:
    """Translate combo entries, keeping their (programmatic) item data."""
    originals = _originals(box)
    blocked = box.blockSignals(True)
    try:
        _translate_combo_items(box, originals)
    finally:
        box.blockSignals(blocked)


def _translate_combo_items(box: QComboBox, originals: dict) -> None:
    for index in range(box.count()):
        key = f"item:{index}"
        source = originals.setdefault(key, box.itemText(index))
        target = (QCoreApplication.translate(WIDGET_CONTEXT, source)
                  if _CURRENT != "en" else source)
        if target and target != box.itemText(index):
            box.setItemText(index, target)


def _translate_tabs(tabs: QTabWidget) -> None:
    originals = _originals(tabs)
    blocked = tabs.blockSignals(True)
    try:
        _translate_tab_items(tabs, originals)
    finally:
        tabs.blockSignals(blocked)


def _translate_tab_items(tabs: QTabWidget, originals: dict) -> None:
    for index in range(tabs.count()):
        key = f"tab:{index}"
        source = originals.setdefault(key, tabs.tabText(index))
        target = (QCoreApplication.translate(WIDGET_CONTEXT, source)
                  if _CURRENT != "en" else source)
        if target and target != tabs.tabText(index):
            tabs.setTabText(index, target)


def translate_widgets(root: QWidget) -> None:  # noqa: C901 - one flat pass
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
    _TRANSLATING = True
    try:
        widgets = [root, *root.findChildren(QWidget)]
        for widget in widgets:
            _translate_property(widget, "windowTitle", widget.windowTitle,
                                widget.setWindowTitle,
                                context=WIDGET_CONTEXT)
            if isinstance(widget, QGroupBox):
                _translate_property(widget, "title", widget.title,
                                    widget.setTitle, context=WIDGET_CONTEXT)
            if isinstance(widget, (QLabel, QAbstractButton)):
                _translate_property(widget, "text", widget.text,
                                    widget.setText, context=WIDGET_CONTEXT)
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
                _translate_property(widget, "placeholder",
                                    widget.placeholderText,
                                    widget.setPlaceholderText,
                                    context=WIDGET_CONTEXT)
            if isinstance(widget, QTabWidget):
                _translate_tabs(widget)
            if isinstance(widget, QComboBox):
                _translate_combo(widget)
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


def exec_translated(dialog):
    """Translate a dialog's tree, then run it modally.

    Used by the few dialogs that build and execute themselves outside
    `modality.exec_dialog` (a QMessageBox, a count form, the library save
    box). Returns the dialog's `exec()` code.
    """
    translate_widgets(dialog)
    return dialog.exec()


class TranslatingStatusBar(QStatusBar):
    """A status bar that translates the static messages it is given.

    Status messages are written where the event happens (245 call sites),
    many as f-strings that cannot be looked up. A message that matches a
    catalogue entry is translated here; a dynamic one is left as written,
    which is the honest fallback rather than a half-substituted sentence.
    """

    def showMessage(self, message: str, timeout: int = 0) -> None:  # noqa: N802
        super().showMessage(tr(message, WIDGET_CONTEXT), timeout)
