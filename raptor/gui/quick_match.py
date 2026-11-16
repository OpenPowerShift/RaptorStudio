"""Prefix ranking for Quick Add and command search (WP-1.6), with no Qt.

Every whitespace-separated query token must be a prefix of some word in the
candidate's name, category or keywords.  An exact prefix of the whole name
outranks a word prefix, which outranks a keyword match; ties go to the
caller's recency order.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Candidate:
    text: str
    category: str = ""
    keywords: tuple[str, ...] = ()
    data: Any = None
    recent: int = 0          # lower is more recent


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def score(query: str, candidate: Candidate) -> int | None:
    """0 = exact name, 1 = name prefix, 2 = word prefix, 3 = keyword."""
    tokens = _words(query)
    if not tokens:
        return 0
    name_words = _words(candidate.text)
    others = _words(candidate.category) + [
        word for keyword in candidate.keywords for word in _words(keyword)]
    text = candidate.text.strip().lower()
    if text == query.strip().lower():
        return 0
    prefixed = text.startswith(query.strip().lower())
    best = 3
    for token in tokens:
        if any(word.startswith(token) for word in name_words):
            rank = 1 if prefixed else 2
        elif any(word.startswith(token) for word in others):
            rank = 3
        else:
            return None
        best = min(best, rank)
    return best


def rank(query: str, candidates: Sequence[Candidate]) -> list[Candidate]:
    """Candidates matching every token, best first, recency as the tie."""
    scored = []
    for order, candidate in enumerate(candidates):
        value = score(query, candidate)
        if value is not None:
            scored.append((value, candidate.recent, order, candidate))
    if not query.strip():
        scored = [(0, candidate.recent, order, candidate)
                  for order, candidate in enumerate(candidates)]
    scored.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in scored]
