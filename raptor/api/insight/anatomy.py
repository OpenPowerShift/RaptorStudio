"""Outage anatomy: every outage as a path, laid out as an icicle.

`attribution.aggregate` gives the commonest outage sequences: what started
the outage, then what kept the system down, step by step, each with its share
of total downtime. Here they are merged into a prefix tree and laid out in
columns: column 0 is how outages start, each next column what followed. A
cell's height is its share of downtime, so the tallest path through the
picture is what most of the downtime looks like.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Cell:
    depth: int
    top: float          # 0..1 down the picture
    height: float       # share of all downtime
    element: str
    cause: str
    outages: int
    ends_here: float    # share of downtime in outages that end at this step


@dataclass
class _Node:
    element: str = ""
    cause: str = ""
    share: float = 0.0
    outages: int = 0
    ends: float = 0.0
    children: dict[tuple[str, str], _Node] = field(default_factory=dict)


def icicle(sequences: list[dict[str, Any]], max_depth: int = 4,
           min_share: float = 0.0) -> list[Cell]:
    """Cells for the outage-anatomy picture, largest first within each parent.
    Paths longer than `max_depth` are cut there; cells smaller than
    `min_share` are left out (their parents keep their full height)."""
    root = _Node()
    for seq in sequences:
        node = root
        for step in seq["steps"][:max_depth]:
            key = (step["element"], step["cause"])
            child = node.children.get(key)
            if child is None:
                child = node.children[key] = _Node(key[0], key[1])
            child.share += seq["share"]
            child.outages += seq["outages"]
            node = child
        node.ends += seq["share"]
    cells: list[Cell] = []

    def place(node: _Node, depth: int, top: float) -> None:
        y = top
        for child in sorted(node.children.values(), key=lambda c: (-c.share, c.element, c.cause)):
            if child.share >= min_share:
                cells.append(Cell(depth, y, child.share, child.element, child.cause,
                                  child.outages, child.ends))
                place(child, depth + 1, y)
            y += child.share

    place(root, 0, 0.0)
    return cells
