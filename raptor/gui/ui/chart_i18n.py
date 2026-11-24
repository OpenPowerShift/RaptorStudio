"""Chart titles and subtitles in the GUI's language.

A `ChartSpec` is shared with the command line, the MCP server and the SVG
export, and those stay English.  The Charts page hands the widget a copy with
its title and subtitle translated, so only what is drawn in the GUI changes.
A subtitle is composed (``... · top level · scope: Intake``): each known
piece is translated and any piece that is not in the catalogue is kept as
written, so an unknown string falls back to English.  Captions (the long
sentence under a chart) are not translated.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any

from PySide6.QtCore import QCoreApplication

from raptor.gui import i18n

_PAREN = re.compile(r"^(?P<head>.+?) \((?P<note>[^()]+)\)$", re.DOTALL)


def _exact(text: str) -> str:
    """The catalogue's entry for exactly `text`, or `text`."""
    for context in (i18n.WIDGET_CONTEXT, "General"):
        got = QCoreApplication.translate(context, text)
        if got != text:
            return got
    return text


def _piece(text: str) -> str:
    """One piece of a subtitle in the GUI language, or as written.

    Order: an exact entry; a trailing parenthesis (a note) and the text
    before it; the ``; `` clauses; then the templates (``scope: %1``).  The
    templates come last because a template's ``%1`` would otherwise swallow
    a whole run of clauses."""
    if not text.strip():
        return text
    got = _exact(text)
    if got != text:
        return got
    match = _PAREN.match(text)
    if match is not None:
        head, note = _piece(match["head"]), _piece(match["note"])
        if head != match["head"] or note != match["note"]:
            return f"{head} ({note})"
    if "; " in text:
        parts = text.split("; ")
        done = [_piece(part) for part in parts]
        if done != parts:
            return "; ".join(done)
        return text
    return i18n.tr(text)


def translate_subtitle(text: str) -> str:
    """A composed subtitle, piece by piece (``" · "`` joins the pieces)."""
    if not text or i18n.current_locale() == "en":
        return text
    return " · ".join(_piece(part) for part in text.split(" · "))


def translate_title(text: str) -> str:
    if not text or i18n.current_locale() == "en":
        return text
    return i18n.tr(text)


def translate_spec(spec: Any) -> Any:
    """A copy of `spec` whose title and subtitle (and its facets') read in
    the GUI language; the spec itself, and its caption, are untouched."""
    if spec is None or i18n.current_locale() == "en":
        return spec
    title = translate_title(spec.title)
    subtitle = translate_subtitle(spec.subtitle)
    facets = [translate_spec(facet) for facet in spec.facets]
    if title == spec.title and subtitle == spec.subtitle and not any(
            new is not old for new, old in zip(facets, spec.facets)):
        return spec
    return dataclasses.replace(spec, title=title, subtitle=subtitle,
                               facets=facets)
