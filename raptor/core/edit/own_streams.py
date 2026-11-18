"""Give blocks their own random-number streams (#292).

By default every block draws from one shared stream, System stream C
(203), as Raptor 7 does. That is statistically sound within a run. An
*independent* stream (1..110, one per entry of Raptor's seed table) gives a
block draws of its own, so editing one block does not shift every other
block's draws: paired comparisons are then less noisy.

There are only 110 independent streams, and the model's System streams
(A..K) already use some of the same seed-table entries. A block needs two
(failure and repair: one stream for both would reuse each uniform draw),
so a model has room for 49 blocks with streams of their own. After that
the pairs are reused, least-used first ("rolled over"): two blocks on one
pair share draws, which is still sound and still better than every block
sharing one stream. This module never hands out a negative (antithetic)
stream, which is the mirror of its positive twin and so not independent
of it.
"""
from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from raptor.core.rng import FETCH_RAPTOR_SEED

#: Raptor 7's System streams A..K, used when a model does not say.
DEFAULT_SYS_STREAMS = (7, 8, 9, 10, 70, 71, 72, 73, 74, 75, 101)

#: The highest independent stream number (the seed table's size).
MAX_INDEPENDENT = len(FETCH_RAPTOR_SEED) - 1

#: Shared System streams are numbered from here (201 = A).
SYSTEM_BASE = 200


def parse_sys_streams(text: str | None) -> tuple[int, ...]:
    """A SYSTEM_DATA `SysStreamsArray` cell, or Raptor 7's default."""
    parts = str(text or "").split()
    try:
        values = tuple(int(p) for p in parts)
    except ValueError:
        return DEFAULT_SYS_STREAMS
    return values or DEFAULT_SYS_STREAMS


def seed_entry(stream: int, sys_streams: Iterable[int]) -> int | None:
    """The seed-table entry a stream number draws from, or None.

    A System stream (201..) uses the entry its slot names; an independent
    stream uses its own number; the sign (antithetic) is ignored, because
    a stream and its mirror are not independent of each other.
    """
    stream = int(stream)
    if stream > SYSTEM_BASE:
        slots = list(sys_streams)
        index = stream - SYSTEM_BASE - 1
        return abs(slots[index]) if 0 <= index < len(slots) else None
    return abs(stream) or None


def free_streams(used: Iterable[int], sys_streams: Iterable[int]) -> list[int]:
    """Independent streams no System stream, block or event draws from."""
    sys_list = list(sys_streams)
    taken = {abs(s) for s in sys_list}
    for stream in used:
        entry = seed_entry(stream, sys_list)
        if entry is not None:
            taken.add(entry)
    return [n for n in range(1, MAX_INDEPENDENT + 1) if n not in taken]


def has_own_streams(fail: int, repair: int) -> bool:
    """True when a block already draws from two independent streams."""
    return 0 < int(fail) <= MAX_INDEPENDENT and \
        0 < int(repair) <= MAX_INDEPENDENT and int(fail) != int(repair)


def stream_pairs(sys_streams: Iterable[int]) -> list[tuple[int, int]]:
    """Every (failure, repair) pair of independent streams no System
    stream uses, in order: (1, 2), (3, 4), ... skipping the System ones."""
    free = free_streams((), sys_streams)
    return [(free[i], free[i + 1]) for i in range(0, len(free) - 1, 2)]


def capacity(sys_streams: Iterable[int]) -> int:
    """How many blocks can have streams no other block uses."""
    return len(stream_pairs(sys_streams))


def assign(count: int, in_use: Iterable[int],
           sys_streams: Iterable[int]) -> tuple[list[tuple[int, int]], int]:
    """`count` (failure, repair) pairs, the least-used first.

    `in_use` is every stream blocks and events already draw from. A pair
    nobody uses comes first, in order; once none is left the pairs are
    reused from the least-used up. Returns (the pairs, how many of them
    were already in use).
    """
    sys_list = list(sys_streams)
    usage: dict[int, int] = {}
    for stream in in_use:
        entry = seed_entry(stream, sys_list)
        if entry is not None:
            usage[entry] = usage.get(entry, 0) + 1
    pairs = stream_pairs(sys_list)
    if not pairs:
        return [], 0
    chosen: list[tuple[int, int]] = []
    reused = 0
    for _ in range(count):
        best = min(range(len(pairs)), key=lambda k: (
            max(usage.get(pairs[k][0], 0), usage.get(pairs[k][1], 0)), k))
        fail, repair = pairs[best]
        if usage.get(fail, 0) or usage.get(repair, 0):
            reused += 1
        usage[fail] = usage.get(fail, 0) + 1
        usage[repair] = usage.get(repair, 0) + 1
        chosen.append((fail, repair))
    return chosen, reused


def plan_all(blocks: list[tuple[int, int, int]], event_streams: Iterable[int],
             sys_streams: Iterable[int]) -> tuple[dict[int, tuple[int, int]], int]:
    """Streams that give every block its own pair.

    `blocks` is (block id, failure stream, repair stream). A block that
    already draws from two independent streams nobody else uses keeps
    them; every other block is given the least-used pair (`assign`).
    Returns ({block id: (failure, repair)} for the blocks to change, how
    many of those share a pair because the 49 ran out).
    """
    sys_list = list(sys_streams)
    counts: dict[int, int] = {}
    for _bid, f, r in blocks:
        for s in (f, r):
            e = seed_entry(s, sys_list)
            if e is not None:
                counts[e] = counts.get(e, 0) + 1
    for s in event_streams:
        e = seed_entry(s, sys_list)
        if e is not None:
            counts[e] = counts.get(e, 0) + 1
    sys_entries = {abs(s) for s in sys_list}
    keep: list[tuple[int, int, int]] = []
    change: list[tuple[int, int, int]] = []
    for bid, f, r in blocks:
        unique = (has_own_streams(f, r) and counts.get(abs(f)) == 1
                  and counts.get(abs(r)) == 1
                  and abs(f) not in sys_entries and abs(r) not in sys_entries)
        (keep if unique else change).append((bid, f, r))
    used = [s for _bid, f, r in keep for s in (f, r)] + list(event_streams)
    pairs, reused = assign(len(change), used, sys_list)
    plan = {bid: pair for (bid, _f, _r), pair in zip(change, pairs, strict=True)}
    return plan, reused


def _int(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def separated_for_run(rbd: Any) -> Any:
    """The model a run simulates when its experiment says
    ``streams: separate`` (Run panel ▸ More options ▸ Streams, ``raptor run
    --block-streams separate``, MCP ``block_streams``).

    `rbd` itself when the experiment keeps the model's streams or every
    block already has its own; else a copy whose blocks have the streams
    `plan_all` gives them. Only the block rows are copied, and the model
    the user edits is never changed, so switching back gives the model's
    own (Raptor 7-matching) numbers again. Applying it twice changes
    nothing the second time.
    """
    from raptor.core import run_experiment
    experiment = getattr(rbd, "experiment", None)
    if not isinstance(experiment, dict) or \
            run_experiment.normalise(experiment)["streams"] != "separate":
        return rbd
    sections = getattr(rbd, "sections", {})
    block_sec = sections.get("BLOCK_DATA")
    if block_sec is None:
        return rbd
    sys_sec = sections.get("SYSTEM_DATA")
    sys_row = sys_sec.rows[0] if sys_sec is not None and sys_sec.rows else {}
    sys_streams = parse_sys_streams(sys_row.get("SysStreamsArray"))
    blocks = [(_int(r.get("ID")), _int(r.get("FStream")),
               _int(r.get("RStream")))
              for r in block_sec.rows if _int(r.get("ID")) != 0]
    event_sec = sections.get("EVENT_DATA")
    events = [_int(r.get("failStream"))
              for r in (event_sec.rows if event_sec is not None else [])]
    plan, _reused = plan_all(blocks, events, sys_streams)
    if not plan:
        return rbd
    out = copy.copy(rbd)
    out.sections = dict(sections)
    new_sec = copy.copy(block_sec)
    rows = []
    for row in block_sec.rows:
        pair = plan.get(_int(row.get("ID")))
        if pair is not None and _int(row.get("ID")) != 0:
            row = {**row, "FStream": str(pair[0]), "RStream": str(pair[1])}
        rows.append(row)
    new_sec.rows = rows
    out.sections["BLOCK_DATA"] = new_sec
    return out
