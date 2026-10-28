"""The PDF report's layout (`raptor.api.report_html`), without Qt: what the
cover, the contents and each section hold, which pages turn landscape,
how wide and long tables are handled, and that weasyprint makes a PDF."""
from __future__ import annotations

from pathlib import Path

from raptor.api.presentation import RunInfo
from raptor.api.report_html import (
    EVENT_LOG_ROWS, Figure, LANDSCAPE_MAX_COLS, RAW_LINES, ReportContent, TextBlock,
    build_html, write_pdf)
from raptor.api.tables import RESULTS, ResultsBundle, Table, result_tables

GOLD = Path(__file__).resolve().parents[1] / "golden"


def _results(name="LadL7Weak"):
    lines = (GOLD / name / "ours" / "rpt_final_results_ours.txt"
             ).read_text().splitlines()
    return result_tables(ResultsBundle(final_lines=lines, units="hours"))


def _content(**kw) -> ReportContent:
    base = dict(title="Probe", revision="B", date="2026-10-21",
                info=RunInfo(model="Probe", runs=60, termination="5,000 hours",
                             units="hours"),
                results=_results())
    base.update(kw)
    return ReportContent(**base)


def test_the_cover_has_the_title_block_run_info_and_key_figures():
    html = build_html(_content())
    assert '<h1 id="top">Probe</h1>' in html
    assert "Revision B · 2026-10-21" in html
    assert "<th>Runs</th><td>60</td>" in html
    # Availability's mean, to six places, as a card.
    assert '<div class="k">Availability</div><div class="v">0.967796</div>' \
        in html


def test_colophon_names_only_this_year_and_no_port_or_licence_history():
    """A printout carries a plain copyright line. The year is read when the
    report is built, so it is the year of the print, not of the model or
    of the application's launch."""
    from datetime import datetime as real_datetime

    class _Frozen(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2031, 4, 5)

    import raptor.api.report_html as report_html
    original = report_html.datetime
    report_html.datetime = _Frozen
    try:
        html = build_html(_content())
    finally:
        report_html.datetime = original
    assert ("Made with RAPTOR Studio. Copyright © 2031 OpenPowerShift."
            in html)
    assert "port of RAPTOR 7" not in html
    assert "Booz Allen Public License" not in html


def test_contents_lists_each_section_with_a_page_number():
    html = build_html(_content(want_weak_link=True))
    assert '<a href="#results">Results</a>' in html
    assert '<a href="#weak">Weak link analysis</a>' in html
    assert "target-counter(attr(href), page)" in html


def test_results_are_grouped_as_on_screen_with_readable_names():
    html = build_html(_content())
    for heading in ("Summary", "Logistics", "Sparing", "Cost"):
        assert f"<h3>{heading}</h3>" in html
    assert "<h4>Life-cycle cost</h4>" in html
    assert "MTBDE (hours)" in html and "Total_Cost" not in html


def test_weak_link_has_its_own_section_or_says_why_not():
    with_weak = build_html(_content(want_weak_link=True))
    assert "<h4>Block availability</h4>" in with_weak
    without = build_html(_content(
        want_weak_link=True, weak_link_note="It was off.",
        results=[t for t in _results() if t.section != "Weak link"]))
    assert "<p>It was off.</p>" in without


def test_a_section_with_a_wide_table_is_landscape():
    """Operating cost has ten columns: the Results section turns landscape
    as a whole, rather than one page at a time."""
    html = build_html(_content())
    assert '<section class="landscape"><h2 id="results">' in html
    narrow = [t for t in _results() if t.section == "Summary"]
    assert '<section><h2 id="results">' in build_html(_content(
        results=narrow))


def test_a_very_wide_table_is_split_into_column_groups():
    headers = ["Run"] + [f"M{i}" for i in range(25)]
    t = Table("results.per_run", "Each run", RESULTS, headers,
              [[1] + [0.5] * 25])
    html = build_html(_content(results=[], per_run=t))
    groups = -(-25 // LANDSCAPE_MAX_COLS)
    assert html.count("<th class=\"l\">Run</th>") == groups
    assert f"Part 1 of {groups}" in html


def test_long_text_and_logs_are_capped_and_say_so():
    log = Table("results.event_log", "Event log", RESULTS,
                ["Run", "Time", "Type", "Element", "State", "Detail"],
                [[1, i, "Block", "A", "Good", ""] for i in
                 range(EVENT_LOG_ROWS + 5)], text_cols={2, 3, 4, 5})
    raw = TextBlock("End of Run", [f"line {i}" for i in range(RAW_LINES + 7)],
                    where="Save it from the Raw tab.")
    html = build_html(_content(event_log=log, raw=[raw]))
    assert f"The first {EVENT_LOG_ROWS:,} of {EVENT_LOG_ROWS + 5:,} rows." \
        in html
    assert f"The first {RAW_LINES:,} of {RAW_LINES + 7:,} lines. Save it " \
        in html
    pre = html.split('<pre class="raw">')[1].split("</pre>")[0]
    assert pre.endswith(f"line {RAW_LINES - 1}")


def test_a_diagram_cut_into_strips_stays_under_one_caption():
    strips = [Figure("Home", f"d{i}.png", part=i, of=3) for i in (1, 2, 3)]
    html = build_html(_content(diagrams=strips + [Figure("Other", "o.png")]))
    assert html.count("<figcaption>Home</figcaption>") == 1
    assert 'class="strips"' in html and "shown in 3 strips" in html
    assert html.count('<div class="strip">') == 3
    assert "<figcaption>Other</figcaption>" in html


def test_censored_estimates_are_marked():
    lines = (GOLD / "LadL7Cap" / "ours" / "rpt_final_results_ours.txt"
             ).read_text().splitlines()
    html = build_html(_content(results=result_tables(ResultsBundle(
        final_lines=lines, units="hours"))))
    assert "MDT (hours, 39 runs)" in html


def test_weasyprint_writes_a_pdf(tmp_path):
    out = tmp_path / "r.pdf"
    write_pdf(build_html(_content()), out, tmp_path)
    assert out.read_bytes()[:5] == b"%PDF-"
