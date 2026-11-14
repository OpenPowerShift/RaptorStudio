"""Squarified treemap layout (Bruls, Huizing & van Wijk 2000).

Qt-free, about eighty lines: turn weighted items into rectangles that fill a
given box, keeping each rectangle close to square.  The downtime treemap
uses it so a model with thousands of blocks can be read at a glance, and a
hierarchy can be followed downwards by repeated layout.

`layout(items, width, height)` returns one rectangle per item in the same
order; areas are proportional to the weights (tested to 1e-9).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def aspect(self) -> float:
        long_side = max(self.w, self.h)
        short_side = min(self.w, self.h)
        return long_side / short_side if short_side > 0 else float("inf")

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


def _worst(row: list[float], side: float) -> float:
    total = sum(row)
    if total <= 0 or side <= 0:
        return float("inf")
    return max(max(side * side * value / (total * total),
                   (total * total) / (side * side * value))
               for value in row if value > 0) if row else 0.0


def _layout_row(row: list[float], rect: Rect) -> tuple[list[Rect], Rect]:
    total = sum(row)
    if total <= 0:
        return ([Rect(rect.x, rect.y, 0.0, 0.0) for _ in row], rect)
    if rect.w >= rect.h:
        width = total / rect.h if rect.h > 0 else 0.0
        out, y = [], rect.y
        for value in row:
            height = rect.h * value / total
            out.append(Rect(rect.x, y, width, height))
            y += height
        return out, Rect(rect.x + width, rect.y, max(0.0, rect.w - width), rect.h)
    height = total / rect.w if rect.w > 0 else 0.0
    out, x = [], rect.x
    for value in row:
        width = rect.w * value / total
        out.append(Rect(x, rect.y, width, height))
        x += width
    return out, Rect(rect.x, rect.y + height, rect.w, max(0.0, rect.h - height))


def layout(items: Iterable[Any], width: float, height: float,
           value: Any = None) -> list[Rect]:
    """Lay out items by weight, largest first, filling `width` x `height`.

    `value` reads the weight from an item (default: the item itself when it
    is a number, else `item["value"]`).  Zero-weight items get a zero rect.
    The result is in the caller's order.
    """
    def weight(item: Any) -> float:
        if value is not None:
            return float(value(item))
        if isinstance(item, (int, float)):
            return float(item)
        return float(item.get("value", 0.0))

    original = list(items)
    weights = [max(0.0, weight(item)) for item in original]
    order = sorted(range(len(original)), key=lambda i: weights[i], reverse=True)
    rects: list[Rect] = [Rect(0.0, 0.0, 0.0, 0.0) for _ in original]
    if width <= 0 or height <= 0 or not weights:
        return rects
    total = sum(weights)
    if total <= 0:
        return rects
    scale = (width * height) / total
    values = [weights[i] * scale for i in order]
    free = Rect(0.0, 0.0, width, height)
    index = 0
    while index < len(values) and values[index] > 0:
        side = min(free.w, free.h)
        row = [values[index]]
        best = _worst(row, side)
        end = index + 1
        while end < len(values):
            candidate = row + [values[end]]
            if _worst(candidate, side) > best:
                break
            row = candidate
            best = _worst(row, side)
            end += 1
        placed, free = _layout_row(row, free)
        for offset, rect in enumerate(placed):
            rects[order[index + offset]] = rect
        index = end
    return rects


def group(items: list[dict[str, Any]], by: str = "group",
          value: str = "value") -> list[dict[str, Any]]:
    """Sum items into groups for a nested treemap; ungrouped items stay."""
    totals: dict[str, float] = {}
    for item in items:
        key = str(item.get(by) or "Other")
        totals[key] = totals.get(key, 0.0) + float(item.get(value) or 0.0)
    return [{"name": name, "value": amount,
             "children": [item for item in items
                          if str(item.get(by) or "Other") == name]}
            for name, amount in sorted(totals.items(),
                                       key=lambda kv: -kv[1])]
