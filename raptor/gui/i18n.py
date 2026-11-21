"""UI localisation: Qt Linguist (.ts -> .qm), loaded at start-up.

Qt's own translator is the best fit for a Qt application: it is already in
the runtime, understands contexts and plurals, and Qt Linguist edits the
catalogue. The alternatives are compared in docs/LOCALISATION.md.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication, QLibraryInfo, QLocale, QTranslator,
)
from PySide6.QtWidgets import QApplication

_LOCALE_DIR = Path(__file__).resolve().parent / "locale"
_TRANSLATOR: QTranslator | None = None
_QT_TRANSLATOR: QTranslator | None = None
_CURRENT = "en"
_SUPPORTED = {
    "en": "English",
    "fr": "Français",
}


def _catalogue(locale: str) -> Path:
    return _LOCALE_DIR / f"raptor_{locale}.qm"


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


def available_locales() -> list[tuple[str, str]]:
    """`(code, label)` for English and every shipped catalogue.

    A partial catalogue says so in its label; it is never presented as a
    complete translation.
    """
    out = [("en", "English")]
    if _LOCALE_DIR.is_dir():
        for path in sorted(_LOCALE_DIR.glob("raptor_*.qm")):
            code = path.stem.removeprefix("raptor_")
            if code and code != "en":
                name = _SUPPORTED.get(code, code)
                done, total = _coverage(code)
                out.append((code, f"{name} ({done} of {total} strings)"
                            if total else name))
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


def install(app: QApplication | None = None,  # noqa: C901
            locale: str | None = None) -> str:
    """Install the catalogue for *locale* (or the user's preference).

    A catalogue is looked up by full locale (`fr_CA`) and then by language
    (`fr`). English, or a missing catalogue, removes the translator.
    """
    global _TRANSLATOR, _QT_TRANSLATOR, _CURRENT
    app = app or QApplication.instance()
    if app is None:
        return "en"
    wanted = (locale or _requested()).replace("-", "_")
    candidates = [wanted]
    if "_" in wanted:
        candidates.append(wanted.split("_", 1)[0])
    chosen = "en"
    path: Path | None = None
    for code in candidates:
        candidate = _catalogue(code)
        if candidate.is_file():
            chosen, path = code.split("_", 1)[0], candidate
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
        # Qt's own buttons and file dialogs (qtbase_fr.qm).
        qtbase = Path(QLibraryInfo.path(
            QLibraryInfo.LibraryPath.TranslationsPath)) / f"qtbase_{chosen}.qm"
        if qtbase.is_file():
            translator = QTranslator(app)
            if translator.load(str(qtbase)):
                app.installTranslator(translator)
                _QT_TRANSLATOR = translator
    if not loaded and chosen != "en":
        # A requested catalogue that cannot be read must not leave the
        # application claiming it is translated.
        chosen = "en"
    _CURRENT = chosen
    app.setProperty("raptorLocale", chosen)
    return chosen


def tr(text: str, context: str = "General") -> str:
    """Translate one source string; returns it unchanged without a catalogue."""
    if not text:
        return text
    return QCoreApplication.translate(context, text)


def qt_plural(context: str, singular: str, plural: str, n: int) -> str:
    # Qt chooses the numerus form from `n`; `plural` is documentation for
    # the caller. The TS entry uses <numerusform> children.
    del plural
    return QCoreApplication.translate(context, singular, None, n)
