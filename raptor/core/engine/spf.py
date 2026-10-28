"""The Single Point Failure file's text layout and date (`WriteSPFfile`, menu 10-5).

The single points are found by `spf_fev`, which runs Raptor 7's procedure on
Failure Effects View; this module only renders them. It used to hold a static walk
of the graph that could not see dependencies, cold standby or induced failures: Raptor 7's
files disagreed with it on every one of 10 such models captured (the series
event in an unphased model, an induced failure through a dependency, a cold-standby
partner), so it was deleted 2026-10-10.

`render_spf_file` is checked byte for byte against Raptor 7 files in `tests/unit/test_spf.py`.
"""
from __future__ import annotations

import datetime as _dt

from raptor.core.engine.report_format import real_to_sn

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def spf_date_string(when: _dt.datetime | None = None) -> str:
    """`outString`'s date tail: `DateTime`
    split into `day`, a 3-letter month, and a 4-digit year, rendered
    `"<day> <Mon> <year>"` with no leading zero on the day. The source
    extracts these with fixed `SUBSTR` offsets from the platform's own
    `DateTime` string; this takes the components directly."""
    when = when or _dt.datetime.now()
    return f"{when.day} {_MONTHS[when.month - 1]} {when.year}"


def render_spf_file(
    name_of_file: str,
    date: str,
    rows: list[dict],
    *,
    password: bool = False,
    phase_sections: list[tuple[str, list[dict]]] | None = None,
) -> str:
    """Render the SPF listing exactly as `WriteSPFfile` writes it.

    `name_of_file` is the save-dialog filename echoed in the header (not a
    model property); `date` is `spf_date_string()`. `rows` are the
    unphased-branch element dicts (`{"kind", "id", "name"}`) from
    `single_point_failures`.

    For a phased model pass `phase_sections` -- one `(phase_name, rows)`
    per phase, in phase order -- and the per-phase layout
   is emitted instead. `password` adds the
    source's `REALTOSN(1.0,15,6,0)` criticality column (the source
    hardcodes `critFactor:=1.0`).

    Returns the full file text with CRLF line endings (matching the real
    capture's `WriteLn`), including the trailing newline.
    """
    lines: list[str] = []
    # Raptor 7: header + THREE WriteLn (two blank lines).
    lines.append(f"Single Point Failure File        {name_of_file}            {date}")
    lines.append("")
    lines.append("")

    if phase_sections is not None:
        # column head + TWO WriteLn.
        if password:
            lines.append("   Phase                  Block                  Criticality ")
        else:
            lines.append("   Phase                  Block")
        lines.append("")
        for phase_name, prows in phase_sections:
            for d in prows:
                lines.append(
                    "   " + (phase_name + " " * 20)[:20]
                    + "   " + (d["name"] + " " * 20)[:20]
                    + ("   " + real_to_sn(d.get("crit", 1.0), 15, 6, 0) if password else "")
                )
            if not prows:
                lines.append("   No single point failures")
        return "\r\n".join(lines) + "\r\n"

    # column head + ONE WriteLn.
    lines.append("Block                  Criticality " if password else "Block")
    for d in rows:
        lines.append(
            "   " + (d["name"] + " " * 20)[:20]
            + ("   " + real_to_sn(d.get("crit", 1.0), 15, 6, 0) if password else "")
        )
    if not rows:
        # .
        lines.append("   No single point failures")
    return "\r\n".join(lines) + "\r\n"
