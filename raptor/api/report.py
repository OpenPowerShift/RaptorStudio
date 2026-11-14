"""The run report as a file, without the GUI.

The GUI's File > Export PDF lays the report out with
`raptor.api.report_html`; this builds the same document from a stored run so
the command line (`raptor explain --report`) and the MCP server
(`export_report`) can hand a decision maker the same pages.  Qt-free: the
diagram comes from the SVG renderer, the charts from `charts_svg`.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Any

from raptor.api.report_html import Figure, ReportContent, build_html, write_pdf

#: What a report can carry, in the GUI dialog's order.
SECTIONS = ("diagram", "story", "results", "data_tables")
#: A reader's report: the answer and the picture; the tables are dozens of
#: pages on a real model, so they come on request.
DEFAULT_SECTIONS = ("diagram", "story")


class ReportError(ValueError):
    """The report cannot be written (unknown suffix, missing run)."""


def report_content(workspace: Any, run_id: str, *, title: str | None = None,
                   sections: tuple[str, ...] = DEFAULT_SECTIONS,
                   image_dir: Path | None = None,
                   inline_images: bool = False) -> ReportContent:
    """The report for stored run `run_id`.

    Images are written into `image_dir` (for a PDF) or, with
    `inline_images`, embedded as data URIs (a single-file HTML report).
    """
    from raptor.api.insight import report_section
    from raptor.api.presentation import run_info
    from raptor.api.run import run_dir
    from raptor.api.run_tables import bundle_from_run, read_summary
    from raptor.api.tables import report_input_tables, result_tables

    unknown = set(sections) - set(SECTIONS)
    if unknown:
        raise ReportError(f"unknown report sections: {', '.join(sorted(unknown))}"
                          f" (choose from {', '.join(SECTIONS)})")
    summary = read_summary(run_dir(workspace, run_id), run_id)
    model_id = summary.get("model_id")
    rbd = workspace.get(model_id).rbd if model_id else None
    name = str(summary.get("model_name") or "model")
    content = ReportContent(title=title or name)
    if rbd is not None:
        content.info = run_info(rbd, model=name, runs=int(summary.get("num_runs") or 0),
                                units=str(summary.get("units") or ""),
                                manifest={"analyses": summary.get("analyses") or []})
    if "diagram" in sections and rbd is not None:
        content.diagrams = [_diagram(rbd, image_dir, inline_images)]
    if "data_tables" in sections and rbd is not None:
        content.inputs = report_input_tables(rbd)
    if "results" in sections:
        bundle = bundle_from_run(workspace, run_id)
        content.results = [t for t in result_tables(bundle)
                           if t.section and t.section != "Weak link"]
        content.per_run = next((t for t in result_tables(bundle)
                                if t.key == "results.per_run"), None)
    if "story" in sections:
        content.insight = report_section.from_run(workspace, run_id, rbd=rbd)
    return content


def _diagram(rbd: Any, image_dir: Path | None, inline: bool) -> Figure:
    from raptor.render.svg import render_svg
    svg, _meta = render_svg(rbd)
    if inline or image_dir is None:
        data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        return Figure("Top level", f"data:image/svg+xml;base64,{data}")
    (image_dir / "diagram.svg").write_text(svg, encoding="utf-8")
    return Figure("Top level", "diagram.svg")


def write_report(workspace: Any, run_id: str, path: str | Path, *,
                 title: str | None = None,
                 sections: tuple[str, ...] = DEFAULT_SECTIONS,
                 ) -> Path:
    """Write the report of stored run `run_id` to `path`: `.pdf` or `.html`
    (one self-contained file).  Returns the path written."""
    out = Path(path)
    suffix = out.suffix.lower()
    if suffix not in (".pdf", ".html", ".htm"):
        raise ReportError(f"{out.name}: a report is .pdf or .html")
    if suffix == ".pdf":
        with tempfile.TemporaryDirectory(prefix="raptor_report_") as td:
            folder = Path(td)
            document = build_html(report_content(
                workspace, run_id, title=title, sections=sections,
                image_dir=folder))
            write_pdf(document, out, folder)
    else:
        out.write_text(build_html(report_content(
            workspace, run_id, title=title, sections=sections,
            inline_images=True)), encoding="utf-8")
    return out
