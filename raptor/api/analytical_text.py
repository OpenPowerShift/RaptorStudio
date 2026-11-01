"""Plain-text rendering of an analytical result.

Shared by the CLI and the GUI's Copy button so both print the same thing.
"""
from __future__ import annotations

from typing import Any


def fmt(value: float | None, digits: int = 9) -> str:
    """A number for display: ``N/A`` for undefined, 9 significant digits."""
    if value is None:
        return "N/A"
    if value == 0.0:
        return "0"
    return f"{value:.{digits}g}"


def _table(header: tuple[str, ...], rows: list[list[str]]) -> list[str]:
    """Left-aligned text table, two-space indented."""
    widths = [max(len(header[i]), *(len(row[i]) for row in rows))
              for i in range(len(header))]
    lines = ["  " + "  ".join(header[i].ljust(widths[i])
                              for i in range(len(header)))]
    lines.extend("  " + "  ".join(row[i].ljust(widths[i])
                                  for i in range(len(header)))
                 for row in rows)
    return lines


def summary_text(result: dict[str, Any]) -> str:  # noqa: C901, PLR0912
    lines = [
        f"Analytical analysis: {result['model']} ({result['mode']})",
        f"BDD nodes: {result['bdd_nodes']}",
        "",
    ]
    unit = result.get("units") or ""
    suffix = f" {unit}" if unit else ""
    if result["mode"] == "reliability":
        lines.extend([
            f"time:          {fmt(result['time'])}{suffix}",
            f"reliability:   {fmt(result.get('reliability'))}",
            f"pdf:           {fmt(result.get('pdf'))}",
            f"hazard:        {fmt(result.get('hazard'))}",
            f"MTTF:          {fmt(result.get('mttf'))}{suffix}",
        ])
        bx = result.get("bx") or {}
        if bx:
            lines.append("BX life:       " + ", ".join(
                f"B{key}={fmt(value)}{suffix}" for key, value in bx.items()))
    else:
        lines.extend([
            f"availability:  {fmt(result.get('availability'))}",
            f"unavailability: {fmt(result.get('unavailability'))}",
        ])
    if "conditional" in result:
        conditional = result["conditional"]
        lines.append(
            f"conditional:   R({fmt(conditional['age'])} + "
            f"{fmt(conditional['tau'])}) = "
            f"{fmt(conditional['reliability'])}")
    if result.get("equation"):
        lines.extend(["", "Equation:", result["equation"]])
    if result.get("assumptions"):
        lines.extend(["", "Assumptions:"])
        lines.extend(f"  - {item}" for item in result["assumptions"])
    if result.get("warnings"):
        lines.extend(["", "Warnings:"])
        lines.extend(f"  - {warning}" for warning in result["warnings"])
    components = result.get("components") or []
    if result["mode"] == "availability" and components:
        lines.extend(["", "Components:"])
        lines.extend(_table(
            ("name", "availability", "MTTF", "MTTR"),
            [[row["name"], fmt(row.get("availability")),
              fmt(row.get("mttf")), fmt(row.get("mttr"))]
             for row in components]))
    importance = result.get("importance") or []
    if importance:
        lines.extend(["", "Importance:"])
        lines.extend(_table(
            ("name", "birnbaum", "raw", "rrw", "VF", "criticality"),
            [[row["name"], fmt(row.get("birnbaum")), fmt(row.get("raw")),
              fmt(row.get("rrw")), fmt(row.get("vesely_fussell")),
              fmt(row.get("criticality"))] for row in importance]))
    if result.get("times"):
        lines.extend(["", "Curve:"])
        for point in result["times"]:
            lines.append(f"  t={fmt(point['time'])}  {fmt(point['value'])}")
    return "\n".join(lines)
