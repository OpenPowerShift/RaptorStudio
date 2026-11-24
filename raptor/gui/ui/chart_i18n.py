"""Chart text in the GUI's language.

A `ChartSpec` is shared with the command line, the MCP server and the SVG
export, and those stay English.  The Charts page hands the widget a *copy*
with its text translated, so only what is drawn in the GUI changes: title,
subtitle, axis titles, the app's own words inside the series (a cause, the
dashed "system" line), mark labels, heatmap headings and the caption.

Names are data and are never translated.  Every composed sentence is read
through a template (`raptor.gui.chart_strings`) whose pieces are marked as
data (kept as written) or as app words (translated); an unknown sentence
leaves the whole text in English.  Numbers are kept exactly as produced.
"""
from __future__ import annotations

import dataclasses
import re
from functools import lru_cache
from typing import Any

from PySide6.QtCore import QCoreApplication

from raptor.gui import chart_strings, i18n

_PAREN = re.compile(r"^(?P<head>.+?) \((?P<note>[^()]+)\)$", re.DOTALL)
#: A data piece never spans a sentence break, so a general template such as
#: "%1 had no downtime." cannot swallow the sentences before it.
_DATA = r"((?:(?!\. ).)+?)"


def _english() -> bool:
    return i18n.current_locale() == "en"


def _exact(text: str) -> str:
    """The catalogue's entry for exactly `text`, or `text`."""
    for context in (i18n.WIDGET_CONTEXT, "General"):
        got = QCoreApplication.translate(context, text)
        if got != text:
            return got
    return text


def word(text: str) -> str:
    """App vocabulary drawn by the widget, in the GUI language.

    An exact entry, a unit in brackets (``time (hours)``), then a template;
    anything else is returned as written."""
    if not text or _english():
        return text
    got = _exact(text)
    if got != text:
        return got
    return i18n.translate_source(text)


def fmt(template: str, *pieces: Any) -> str:
    """A catalogued sentence with its pieces filled in (data kept)."""
    form = template if _english() else _exact(template)
    return re.sub(r"%(\d)", lambda m: str(pieces[int(m.group(1)) - 1]), form)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _word_strict(text: str) -> str | None:
    """`text` as a translated app word (also in lower case), or None."""
    got = _exact(text)
    if got != text:
        return got
    if text[:1].islower():
        got = _exact(text[:1].upper() + text[1:])
        if got != text[:1].upper() + text[1:]:
            return _lower_first(got)
    return None


def cause_label(cause: str, labels: dict[str, str] | None = None) -> str:
    """The key's name for a cause, in the GUI language."""
    from raptor.api.insight.attribution import CAUSE_LABELS
    text = {**CAUSE_LABELS, **(labels or {})}.get(cause, cause)
    return word(text) if cause in CAUSE_LABELS else text


# -- titles and subtitles ------------------------------------------------


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


def translate_subtitle(text: str, scope: dict[str, Any] | None = None) -> str:
    """A composed subtitle, piece by piece (``" · "`` joins the pieces).

    `scope` is the chart's scope: the whole system is the app's word
    "System", a hierarchy keeps its own name."""
    if not text or _english():
        return text
    whole = scope is not None and scope.get("kind") == "system"
    done = []
    for part in text.split(" · "):
        if whole and part == "scope: System":
            part = i18n.tr("scope: %1").replace("%1", word("System"))
            done.append(part)
            continue
        done.append(_piece(part))
    return " · ".join(done)


def translate_title(text: str) -> str:
    if not text or _english():
        return text
    # a template first: "... (run 1)" would otherwise read as a unit in brackets
    return _exact(text) if _exact(text) != text else (
        i18n.translate_template(text) or i18n.tr(text))


# -- captions ------------------------------------------------------------


@lru_cache(maxsize=1)
def _caption_patterns() -> tuple[tuple[re.Pattern[str], str, str], ...]:
    built = []
    for template, kinds in chart_strings.CAPTIONS.items():
        pieces = re.split(r"(%\d)", template)
        body = ""
        for piece in pieces:
            if re.fullmatch(r"%\d", piece):
                kind = kinds[int(piece[1]) - 1]
                body += ("(.+?)" if kind == "l" else
                         "(at least|at most)" if kind == "a" else _DATA)
            else:
                body += re.escape(piece)
        literal = sum(len(p) for p in pieces if not re.fullmatch(r"%\d", p))
        built.append((literal, re.compile(f"^{body}(?= |$)", re.DOTALL),
                      template, kinds))
    built.sort(key=lambda item: -item[0])
    return tuple((rx, tpl, kinds) for _n, rx, tpl, kinds in built)


_COUNTED = re.compile(r"^(?P<n>[\d,]+) (?P<noun>run|runs|hierarchy|"
                      r"hierarchies|part|parts)$")
_CAUSED = re.compile(r"^(?P<name>.+) caused (?P<qty>\S+(?: \S+)?)$")


def _quantity_piece(text: str) -> str:
    """A number, and a unit word after it, translated."""
    match = re.match(r"^(?P<n>[-\d.,]+)(?: (?P<unit>\S+))?$", text)
    if match is None or not match["unit"]:
        return text
    return f"{match['n']} {i18n.translate_unit(match['unit'])}"


def _counted_piece(text: str) -> str | None:
    match = _COUNTED.match(text)
    if match is None:
        return None
    form = _exact(f"%1 {match['noun']}")
    return None if form == f"%1 {match['noun']}" else \
        form.replace("%1", match["n"])


def _baseline_piece(text: str) -> str | None:
    if text == "the baseline":
        return _word_strict(text)
    if text.startswith("baseline "):
        form = i18n.tr("baseline %1")
        return None if form == "baseline %1" else \
            form.replace("%1", text[len("baseline "):])
    return None


def _caused_piece(text: str) -> str | None:
    form = _exact("%1 caused %2")
    items = []
    for item in text.split(", "):
        match = _CAUSED.match(item)
        if match is None or form == "%1 caused %2":
            return None
        items.append(form.replace("%1", match["name"])
                     .replace("%2", _quantity_piece(match["qty"])))
    return ", ".join(items)


def _piece_text(kind: str, text: str) -> str | None:
    """One caption piece in the GUI language, or None when it cannot be."""
    if kind == "d":
        return text
    if kind == "q":
        return _quantity_piece(text)
    if kind in ("w", "a"):
        return _word_strict(text)
    if kind == "m":
        return _word_strict(text) or text
    return {"c": _counted_piece, "b": _baseline_piece,
            "l": _caused_piece}[kind](text)


def _fill(form: str, pieces: list[str]) -> str:
    return re.sub(r"%(\d)", lambda m: pieces[int(m.group(1)) - 1], form)


def _sentence(text: str) -> tuple[str, int] | None:
    """The first caption sentence at the start of `text`, translated, and
    how many characters it used."""
    for rx, template, kinds in _caption_patterns():
        match = rx.match(text)
        if match is None:
            continue
        form = _exact(template)
        pieces = []
        for kind, raw in zip(kinds, match.groups(), strict=True):
            done = _piece_text(kind, raw)
            if done is None:
                break
            pieces.append(done)
        else:
            return _fill(form, pieces), match.end()
    return None


_ANSWER_FRAGMENT = re.compile(
    r"^(?P<head>.+?: [\d,]+ runs?)"
    r"(?P<mean>, mean [^;]+?)?"
    r"(?P<mid>; the middle half of the runs lie between .+? and .+? "
    r"\(Q1 to Q3\))?"
    r"(?P<met>; \d+ of \d+ \(.+?\) met the target)?\.(?: |$)", re.DOTALL)


def _split(pattern: str, text: str) -> tuple[str, ...]:
    found = re.match(pattern, text, re.DOTALL)
    return found.groups() if found is not None else ()


def _answer_head(text: str) -> tuple[str, int] | None:
    """The run-to-run caption: ``Availability: 30 runs, mean ...; ...``,
    its optional clauses each catalogued apart."""
    match = _ANSWER_FRAGMENT.match(text)
    if match is None:
        return None
    label, _, count = match["head"].rpartition(": ")
    counted = _piece_text("c", count)
    if counted is None:
        return None
    out = fmt("%1: %2", _piece_text("m", label), counted)
    if match["mean"]:
        out += fmt(", mean %1", match["mean"][len(", mean "):])
    if match["mid"]:
        low, high = _split(r"^; the middle half of the runs lie between "
                           r"(.+?) and (.+?) \(Q1 to Q3\)$", match["mid"])
        out += fmt("; the middle half of the runs lie between %1 and %2 "
                   "(Q1 to Q3)", low, high)
    if match["met"]:
        k, n, share = _split(r"^; (\d+) of (\d+) \((.+?)\) met the target$",
                             match["met"])
        out += fmt("; %1 of %2 (%3) met the target", k, n, share)
    return out + ".", match.end()


_LADDER_ITEM = re.compile(r"^(?P<label>Inherent \(repair only\)|Achieved "
                          r"\(\+ planned maintenance\)|Operational \(\+ "
                          r"logistics, spares, crews\)) (?P<value>[\d.,]+ %)$")
_NOTHING = re.compile(r"^(?P<what>(?:planned maintenance|logistics, spares, "
                      r"crews)(?: and (?:planned maintenance|logistics, "
                      r"spares, crews))*) took nothing away in these runs"
                      r"\.$", re.IGNORECASE)


def _ladder(text: str) -> str | None:
    """The availability ladder's caption: ``Inherent (repair only) 99.7 %;
    Achieved ... 99.4 %. Logistics, spares, crews took nothing away...``"""
    head, _, tail = text.partition(". ")
    items = []
    for part in head.rstrip(".").split("; "):
        match = _LADDER_ITEM.match(part)
        if match is None:
            return None
        label = _exact(match["label"])
        if label == match["label"]:
            return None
        items.append(f"{label} {match['value']}")
    out = "; ".join(items)
    if not tail:
        return out + ("." if text.endswith(".") else "")
    match = _NOTHING.match(tail)
    if match is None:
        return None
    names = []
    for part in match["what"].lower().split(" and "):
        got = _word_strict(part)
        if got is None:
            return None
        names.append(got)
    joined = f" {word('and')} ".join(names)
    return out + ". " + fmt("%1 took nothing away in these runs.",
                            joined[:1].upper() + joined[1:])


def translate_caption(text: str) -> str:
    """A caption in the GUI language; unchanged when any sentence of it is
    not a known template."""
    if not text or _english():
        return text
    if text.startswith(("Inherent (repair only)", "Achieved (+ planned",
                        "Operational (+")):
        return _ladder(text) or text
    out: list[str] = []
    rest = text
    first = True
    while rest:
        got = _sentence(rest)
        if got is None and first:
            got = _answer_head(rest)
        if got is None:
            return text
        sentence, used = got
        out.append(sentence)
        rest = rest[used:].lstrip(" ")
        first = False
    return " ".join(out)


# -- the spec ------------------------------------------------------------


def _translate_series(spec: Any) -> tuple[list[dict[str, Any]], bool]:
    """The app's own words inside the series: the availability ladder's rung
    names, the cause named by a waterfall step, the dashed system line.  A
    hierarchy or element name is never touched."""
    series = spec.series
    changed = False
    out: list[dict[str, Any]] = []
    for item in series:
        new = item
        if spec.kind == "ladder" and isinstance(item.get("name"), str):
            got = word(item["name"])
            if got != item["name"]:
                new = {**item, "name": got}
        elif spec.kind == "waterfall" and isinstance(item.get("name"), str):
            from raptor.api.insight.attribution import CAUSE_LABELS
            if item["name"] == CAUSE_LABELS.get(str(item.get("cause"))):
                new = {**item, "name": word(item["name"])}
        elif (spec.kind == "timeline" and item.get("name") == "system"
              and item.get("hierarchy_id") == 0
              and item.get("tone") == "muted"):
            name = word("system")
            new = {**item, "name": name}
            if isinstance(item.get("end_text"), str):
                new["end_text"] = (name + item["end_text"][len("system"):]
                                   if item["end_text"].startswith("system")
                                   else item["end_text"])
        if new is not item and new != item:
            changed = True
        out.append(new)
    return (out if changed else series), changed


def _translate_marks(spec: Any) -> tuple[list[dict[str, Any]], bool]:
    marks = spec.marks
    out = []
    changed = False
    for mark in marks:
        new = mark
        label = mark.get("label")
        if (mark.get("kind") in ("quartile", "median") and isinstance(label, str)):
            got = word(label)
            if got != label:
                new = {**mark, "label": got}
                changed = True
        out.append(new)
    return (out if changed else marks), changed


def _translate_provenance(spec: Any) -> tuple[dict[str, Any], bool]:
    prov = spec.provenance
    new = dict(prov)
    from raptor.api.insight.attribution import CAUSE_LABELS
    columns = prov.get("columns")
    if isinstance(columns, list):
        cols = []
        for col in columns:
            label = col.get("label") if isinstance(col, dict) else None
            if not isinstance(label, str):
                cols.append(col)
            elif col.get("key") in CAUSE_LABELS and label == CAUSE_LABELS[col["key"]]:
                cols.append({**col, "label": word(label)})
            elif label.endswith(" (non-mission)"):
                cols.append({**col, "label": fmt(
                    "%1 (non-mission)", label[:-len(" (non-mission)")])})
            else:
                cols.append(col)
        new["columns"] = cols
    labels = prov.get("group_labels")
    if isinstance(labels, dict) and labels:
        new["group_labels"] = {key: word(val) if isinstance(val, str) else val
                               for key, val in labels.items()}
    nodes = prov.get("nodes")
    if isinstance(nodes, list):
        fixed = {"generated", "delivered", "lost", "unattributed"}
        new["nodes"] = [
            {**n, "name": word(n["name"])}
            if isinstance(n, dict) and n.get("id") in fixed
            and isinstance(n.get("name"), str) else n for n in nodes]
    if isinstance(prov.get("summary"), str):
        new["summary"] = translate_caption(prov["summary"])
    return new, new != prov


def translate_spec(spec: Any) -> Any:
    """A copy of `spec` whose text reads in the GUI language (its facets'
    too); the spec itself is untouched."""
    if spec is None or _english():
        return spec
    changes: dict[str, Any] = {}
    for field in ("title", "x_label", "y_label"):
        value = getattr(spec, field)
        got = translate_title(value) if field == "title" else word(value)
        if got != value:
            changes[field] = got
    subtitle = translate_subtitle(spec.subtitle, spec.scope)
    if subtitle != spec.subtitle:
        changes["subtitle"] = subtitle
    caption = translate_caption(spec.caption)
    if caption != spec.caption:
        changes["caption"] = caption
    series, did = _translate_series(spec)
    if did:
        changes["series"] = series
    marks, did = _translate_marks(spec)
    if did:
        changes["marks"] = marks
    provenance, did = _translate_provenance(spec)
    if did:
        changes["provenance"] = provenance
    facets = [translate_spec(facet) for facet in spec.facets]
    if any(new is not old for new, old in zip(facets, spec.facets)):
        changes["facets"] = facets
    return dataclasses.replace(spec, **changes) if changes else spec
