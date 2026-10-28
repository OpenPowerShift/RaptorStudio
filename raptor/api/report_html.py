"""A run's report as print-ready HTML, and that HTML as a PDF.

Export PDF and Print both come here, so a report looks the same on paper
whichever way it is made. The caller gathers the content (the GUI renders
the diagram and graph images); this module lays it out:

* a cover with the title block, the run information and the key figures;
* a contents page with page numbers;
* one section per part the caller asked for -- diagrams, model data,
  results grouped as on screen (Summary, Logistics, Sparing, Resources,
  Cost, Capacity, Weak link), graphs, per-run figures, the event log and
  the raw reports;
* the model name, section and page number on every page.

Tables are the ones the spreadsheet export writes (`raptor.api.tables`),
shown with `raptor.api.presentation`'s labels and number formats. A table
too wide for a portrait page gets a landscape page of its own. Long text
(the End-of-Run report, the event log) is capped, with a note saying how
much was left out and where the whole of it is.

`weasyprint` renders the PDF. Nothing here imports Qt.
"""
from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from raptor.api.presentation import RunInfo, display_rows, fmt, kind_of
from raptor.api.tables import Table

#: More columns than this and a table goes on a landscape page.
PORTRAIT_MAX_COLS = 7
#: Rows of the event log a report shows; the rest are counted, not shown.
EVENT_LOG_ROWS = 2000
#: Lines of one raw report a report shows.
RAW_LINES = 1500

#: The results sections, in reading order, and what each one is.
RESULT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("Summary", "The system's measures over all runs."),
    ("Logistics", "Maintenance measures and the time spent in each "
                  "health state."),
    ("Sparing", "Spares used by each block, and the stock of each spare "
                "pool."),
    ("Resources", "How many of each repair resource were available."),
    ("Cost", "Costs by block and in total, averaged over the runs."),
    ("Capacity", "Flow through the system and each node."),
    ("Weak link", "Each element's own measures, to find the ones that "
                  "limit the system."),
)

#: Key figures on the cover: (measure label prefix, card title).
_KEY_FIGURES = (("Availability", "Availability"), ("MTBDE", "MTBDE"),
                ("MDT", "MDT"), ("Reliability", "Reliability"),
                ("System failures", "System failures"),
                ("Total cost", "Total cost"))


@dataclass
class Figure:
    """An image the caller has written into the report's folder."""
    title: str
    file: str                      # relative to the PDF's base folder
    caption: str = ""
    part: int = 1                  # strip number, when a figure is cut
    of: int = 1                    # into this many strips (read in order)


@dataclass
class TextBlock:
    """Report text shown as it is (monospaced, not reflowed)."""
    title: str
    lines: Sequence[str]
    where: str = ""                # where the full text can be had


@dataclass
class ReportContent:
    title: str
    revision: str = ""
    date: str = ""
    orientation: str = "portrait"            # the default page
    info: RunInfo | None = None
    diagrams: list[Figure] = field(default_factory=list)
    inputs: list[Table] = field(default_factory=list)
    results: list[Table] = field(default_factory=list)
    #: Shown under Summary: Raptor 7's censoring/sample notes.
    notes: list[str] = field(default_factory=list)
    #: Shown in the Weak link section when it has no tables.
    weak_link_note: str = ""
    want_weak_link: bool = False
    graphs: list[Figure] = field(default_factory=list)
    per_run: Table | None = None
    event_log: Table | None = None
    raw: list[TextBlock] = field(default_factory=list)


# --------------------------------------------------------------------------
# HTML pieces
# --------------------------------------------------------------------------

def _e(text: Any) -> str:
    return html.escape("" if text is None else str(text))


#: Most value columns one table shows on a landscape page; a wider table
#: is shown as several, each repeating the row-label column.
LANDSCAPE_MAX_COLS = 11


def _column_groups(table: Table) -> list[Table]:
    n = len(table.headers)
    if n <= LANDSCAPE_MAX_COLS + 1:
        return [table]
    groups = []
    for start in range(1, n, LANDSCAPE_MAX_COLS):
        cols = [0, *range(start, min(start + LANDSCAPE_MAX_COLS, n))]
        groups.append(Table(
            table.key, table.title, table.group,
            [table.headers[i] for i in cols],
            [[r[i] if i < len(r) else "" for i in cols] for r in table.rows],
            text_cols={k for k, i in enumerate(cols)
                       if i in table.text_cols},
            section=table.section))
    return groups


def _table_html(table: Table, *, caption: str | None = None,
                max_rows: int | None = None) -> str:
    groups = _column_groups(table)
    if len(groups) == 1:
        return _one_table_html(table, caption=caption, max_rows=max_rows)
    title = table.title if caption is None else caption
    out = []
    for n, g in enumerate(groups, 1):
        label = f"{title} ({n} of {len(groups)})" if title else \
            f"Part {n} of {len(groups)}"
        out.append(_one_table_html(g, caption=label, max_rows=max_rows,
                                   notes=n == len(groups)))
    return "".join(out)


def is_wide(table: Table) -> bool:
    return len(table.headers) > PORTRAIT_MAX_COLS


def _one_table_html(table: Table, *, caption: str | None = None,
                    max_rows: int | None = None, notes: bool = True) -> str:
    rows = display_rows(table)
    shown = rows if max_rows is None else rows[:max_rows]
    numeric = [i > 0 and i not in table.text_cols for i in
               range(len(table.headers))]
    head = "".join(f'<th class="{"n" if numeric[i] else "l"}">{_e(h)}</th>'
                   for i, h in enumerate(table.headers))
    body = []
    for row in shown:
        cells = []
        for i, c in enumerate(row):
            cls = "n" if i < len(numeric) and numeric[i] else "l"
            if c.startswith(">"):
                cls += " censored"
            cells.append(f'<td class="{cls}">{_e(c)}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    title = table.title if caption is None else caption
    parts = ['<div class="table-block">',
             f"<h4>{_e(title)}</h4>" if title else "",
             f'<table><thead><tr>{head}</tr></thead>'
             f'<tbody>{"".join(body)}</tbody></table>']
    if max_rows is not None and len(rows) > max_rows:
        parts.append(f'<p class="note">The first {max_rows:,} of '
                     f'{len(rows):,} rows.</p>')
    if notes:
        parts += [f'<p class="note">{_e(n)}</p>' for n in table.notes]
    parts.append("</div>")
    return "".join(parts)


def _summary_means(results: Sequence[Table]) -> dict[str, tuple[str, Any]]:
    summary = next((t for t in results if t.key == "results.summary"), None)
    if summary is None or "Mean" not in summary.headers:
        return {}
    col = summary.headers.index("Mean")
    return {str(r[0]): (str(r[0]), r[col]) for r in summary.rows
            if len(r) > col}


def _key_figures(content: ReportContent) -> str:
    means = _summary_means(content.results)
    cards = []
    for prefix, title in _KEY_FIGURES:
        hit = next(((lb, v) for k, (lb, v) in means.items()
                    if k.startswith(prefix)), None)
        if hit is None:
            continue
        label, value = hit
        unit = label[len(prefix):].strip(" ()")
        cards.append(
            f'<div class="card"><div class="k">{_e(title)}</div>'
            f'<div class="v">{_e(fmt(value, kind_of(label)))}</div>'
            f'<div class="u">{_e(("mean, " + unit) if unit else "mean")}'
            f"</div></div>")
    if not cards:
        return ""
    return '<div class="cards">' + "".join(cards) + "</div>"


def _cover(content: ReportContent) -> str:
    meta = " · ".join(x for x in (
        f"Revision {content.revision}" if content.revision else "",
        content.date) if x)
    parts = ['<section class="cover">',
             f'<h1 id="top">{_e(content.title)}</h1>',
             '<p class="subtitle">Reliability block diagram report</p>',
             f'<p class="meta">{_e(meta)}</p>' if meta else ""]
    if content.info is not None:
        rows = "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>"
                       for k, v in content.info.rows())
        parts.append(f'<table class="info">{rows}</table>')
    parts.append(_key_figures(content))
    parts.append("</section>")
    return "".join(parts)


def _toc(chapters: Sequence[tuple[str, str]]) -> str:
    items = "".join(f'<li><a href="#{cid}">{_e(title)}</a></li>'
                    for cid, title in chapters)
    return (f'<nav class="toc"><h2 class="toc-title">Contents</h2>'
            f"<ul>{items}</ul></nav>")


def _figures(figs: Sequence[Figure]) -> str:
    """One figure per title; a figure cut into strips keeps them together
    under one caption, stacked down the page in reading order."""
    out: list[str] = []
    i = 0
    while i < len(figs):
        f = figs[i]
        if f.of <= 1:
            out.append(f'<figure><figcaption>{_e(f.title)}'
                       f'</figcaption><img src="{_e(f.file)}" '
                       f'alt="{_e(f.title)}">'
                       + (f'<p class="note">{_e(f.caption)}</p>'
                          if f.caption else "") + "</figure>")
            i += 1
            continue
        strips = figs[i:i + f.of]
        i += f.of
        body = "".join(
            f'<div class="strip"><span class="part">{s.part} of {s.of}'
            f'</span><img src="{_e(s.file)}" alt="{_e(s.title)} '
            f'({s.part} of {s.of})"></div>' for s in strips)
        out.append(
            f'<figure class="strips"><figcaption>{_e(f.title)}</figcaption>'
            f'<p class="note">Too wide for one page at a readable size: shown '
            f'in {f.of} strips, read top to bottom. Each strip repeats a '
            f'little of the one before.</p>{body}</figure>')
    return "".join(out)


def _results_html(content: ReportContent, *, weak: bool) -> str:
    by_section: dict[str, list[Table]] = {}
    for t in content.results:
        by_section.setdefault(t.section or "Other", []).append(t)
    out = []
    for name, blurb in RESULT_SECTIONS:
        if (name == "Weak link") != weak:
            continue
        tables = by_section.get(name, [])
        if not tables:
            continue
        out.append(f"<h3>{_e(name)}</h3><p>{_e(blurb)}</p>")
        for t in tables:
            caption = "" if len(tables) == 1 and t.title == name else None
            out.append(_table_html(t, caption=caption))
        if name == "Summary" and content.notes:
            out += [f'<p class="note">{_e(n)}</p>'
                    for n in dict.fromkeys(content.notes)]
    if not weak:
        other = by_section.get("Other", [])
        out += [_table_html(t) for t in other]
    return "".join(out)


def _raw_html(blocks: Sequence[TextBlock]) -> str:
    out = []
    for b in blocks:
        lines = list(b.lines)
        shown = lines[:RAW_LINES]
        out.append(f'<div class="rawblock"><h3>{_e(b.title)}</h3>'
                   f'<pre class="raw">{_e(chr(10).join(shown))}</pre>')
        if len(lines) > RAW_LINES:
            more = f"The first {RAW_LINES:,} of {len(lines):,} lines."
            if b.where:
                more += f" {b.where}"
            out.append(f'<p class="note">{_e(more)}</p>')
        out.append("</div>")
    return "".join(out)


def _chapters(content: ReportContent
              ) -> list[tuple[str, str, str, bool]]:
    """``[(id, title, html, landscape)]`` for every section that has
    something. A section is landscape as a whole when it holds a figure,
    report text or a table too wide for a portrait page, so the pages of
    one section do not turn back and forth."""
    ch: list[tuple[str, str, str, bool]] = []
    if content.diagrams:
        ch.append(("diagram", "Diagram", _figures(content.diagrams), True))
    if content.inputs:
        ch.append(("model", "Model data",
                   "".join(_table_html(t) for t in content.inputs),
                   any(is_wide(t) for t in content.inputs)))
    results = _results_html(content, weak=False)
    wide = any(is_wide(t) for t in content.results
               if t.section != "Weak link")
    if content.per_run is not None:
        results += ("<h3>Each run</h3>"
                    + _table_html(content.per_run, caption=""))
        wide = wide or is_wide(content.per_run)
    if results:
        ch.append(("results", "Results", results, wide))
    if content.want_weak_link:
        weak = _results_html(content, weak=True) or (
            f'<p>{_e(content.weak_link_note or "Weak link analysis was not run for these results.")}</p>')
        ch.append(("weak", "Weak link analysis", weak,
                   any(is_wide(t) for t in content.results
                       if t.section == "Weak link")))
    if content.graphs:
        ch.append(("graphs", "Graphs", _figures(content.graphs), True))
    if content.event_log is not None:
        ch.append(("events", "Event log",
                   _table_html(content.event_log, caption="",
                               max_rows=EVENT_LOG_ROWS),
                   is_wide(content.event_log)))
    if content.raw:
        ch.append(("raw", "Raw reports", _raw_html(content.raw), True))
    return ch


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------

_CSS = """
@page {
  size: A4 %(orientation)s;
  margin: 20mm 16mm 18mm 16mm;
  @top-left { content: string(doctitle); }
  @top-right { content: string(chapter); }
  @bottom-left { content: "RAPTOR Studio"; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); }
}
@page :first { @top-left { content: none; } @top-right { content: none; } }
@page wide { size: A4 landscape; }
@page :left, :right { }
@page { @top-left, @top-right, @bottom-left, @bottom-right {
  font-size: 7.5pt; color: #6f7c8a; } }

html { font-family: "Segoe UI", "Noto Sans", "DejaVu Sans", Arial, sans-serif;
       font-size: 9.5pt; color: #23282c; line-height: 1.35; }
h1 { string-set: doctitle content(); font-size: 26pt; color: #475569;
     margin: 0 0 2mm 0; font-weight: 600; }
h2 { string-set: chapter content(); break-before: page; font-size: 16pt;
     color: #475569; border-bottom: 2px solid #fb923c; padding-bottom: 1.5mm;
     margin: 0 0 4mm 0; font-weight: 600; }
h2.toc-title { string-set: none; break-before: auto; }
h3 { font-size: 12pt; color: #475569; margin: 6mm 0 1mm 0;
     break-after: avoid; }
h3 + p { margin: 0 0 2mm 0; color: #535e68; }
h4 { font-size: 9.5pt; color: #394046; margin: 4mm 0 1.5mm 0;
     break-after: avoid; }
p { margin: 1mm 0; }
.note { font-size: 8pt; color: #6f7c8a; margin: 1mm 0 2mm 0; }

.cover { padding-top: 30mm; }
.subtitle { font-size: 13pt; color: #535e68; margin: 0 0 6mm 0; }
.meta { color: #6f7c8a; margin-bottom: 10mm; }
table.info { border-collapse: collapse; margin: 0 0 10mm 0; width: auto; }
table.info th { text-align: left; font-weight: 600; color: #535e68;
                padding: 1mm 8mm 1mm 0; vertical-align: top;
                background: none; border: none; }
table.info td { padding: 1mm 0; border-bottom: 1px solid #edeef0; }
table.info tr:nth-child(even) td { background: none; }
.cards { display: flex; flex-wrap: wrap; gap: 4mm; }
.card { border: 1px solid #e0e2e6; border-left: 3px solid #fb923c;
        border-radius: 2mm; padding: 3mm 4mm; width: 46mm;
        background: #fbfbfb; }
.card .k { font-size: 8pt; color: #6f7c8a; text-transform: uppercase;
           letter-spacing: 0.04em; }
.card .v { font-size: 16pt; font-weight: 600; color: #23282c;
           font-variant-numeric: tabular-nums; }
.card .u { font-size: 7.5pt; color: #6f7c8a; }

nav.toc { break-before: page; }
nav.toc ul { list-style: none; padding: 0; margin: 4mm 0; }
nav.toc li { margin: 2mm 0; font-size: 11pt; }
nav.toc a { color: #23282c; text-decoration: none; }
nav.toc a::after { content: leader(".") target-counter(attr(href), page); }

.table-block { break-inside: auto; margin-bottom: 3mm; }
section.landscape { page: wide; }
table { border-collapse: collapse; font-size: 8pt; margin: 0;
        font-variant-numeric: tabular-nums; }
thead { display: table-header-group; }
th { background: #edeef0; color: #394046; font-weight: 600;
     border-bottom: 1px solid #95a0ad; padding: 1.2mm 2.5mm;
     vertical-align: bottom; }
td { padding: 0.9mm 2.5mm; border-bottom: 1px solid #edeef0; }
tr { break-inside: avoid; }
tbody tr:nth-child(even) td { background: #fbfbfb; }
.n { text-align: right; white-space: nowrap; }
.l { text-align: left; }
td.censored { color: #b45309; }

figure { margin: 0 0 6mm 0; break-inside: avoid; }
figure + figure { break-before: page; }
figure.strips { break-inside: auto; }
figure.strips .strip { position: relative; break-inside: avoid;
                       margin-bottom: 3mm; }
figure.strips img { max-height: 80mm; }
.strip .part { position: absolute; right: 1mm; top: 0; font-size: 7pt;
               color: #64748b; background: #ffffff; padding: 0 1mm;
               z-index: 2; }
figcaption { font-size: 11pt; font-weight: 600; color: #475569;
             margin-bottom: 2mm; }
figure img { max-width: 100%%; max-height: 150mm; object-fit: contain;
             border: 1px solid #edeef0; }

.rawblock + .rawblock { break-before: page; }
pre.raw { font-family: "DejaVu Sans Mono", "Cascadia Mono", Consolas,
          monospace; font-size: 6.5pt; line-height: 1.25;
          white-space: pre; overflow: hidden; background: #fbfbfb;
          border: 1px solid #edeef0; padding: 2mm; margin: 0; }

.colophon { margin-top: 10mm; padding-top: 2mm; border-top: 1px solid
            #e0e2e6; font-size: 7.5pt; color: #6f7c8a; }
"""


def _copyright_notice() -> str:
    """The colophon, with the year read when the report is built.

    That is the moment the report is printed or exported -- not when the
    application started, nor when the model was created -- so a printout
    made in a later year carries that later year."""
    year = datetime.now().year
    return (f"Made with RAPTOR Studio. Copyright © {year} OpenPowerShift.")


def build_html(content: ReportContent) -> str:
    """The whole report as one HTML document."""
    orientation = ("landscape" if content.orientation == "landscape"
                   else "portrait")
    chapters = _chapters(content)
    body = [_cover(content)]
    if len(chapters) > 1:
        body.append(_toc([(cid, title) for cid, title, _, _ in chapters]))
    for cid, title, inner, landscape in chapters:
        cls = ' class="landscape"' if landscape else ""
        body.append(f'<section{cls}><h2 id="{cid}">{_e(title)}</h2>{inner}'
                    f"</section>")
    body.append(f'<p class="colophon">{_copyright_notice()}</p>')
    return ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{_e(content.title)}</title>"
            f"<style>{_CSS % {'orientation': orientation}}</style>"
            "</head><body>" + "".join(body) + "</body></html>")


def write_pdf(document: str, path: str | Path, base_dir: str | Path) -> None:
    """Render *document* to a PDF at *path*; images resolve against
    *base_dir*."""
    import weasyprint
    weasyprint.HTML(string=document, base_url=str(base_dir)).write_pdf(
        str(path))
