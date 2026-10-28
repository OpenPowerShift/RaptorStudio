"""`WriteSPFfile` run on Failure Effects View, as Raptor 7 does.

Raptor 7 does not reason about the graph: it puts the model in Failure Effects View
and, for each block that is Running or Standby, fails it, runs `CheckColds; AnalyzeSystem`
and writes a row when the system is Red. So dependencies, cold standby and standby
switching all count, which a static walk of the graph cannot do (`spf.py`, kept as an
oracle, is that walk). The quirks are Raptor 7's:

* a candidate is tested only if it is Running or Standby when its turn comes;
* unphased models restore with `RefreshAllFEV` after every candidate, phased ones with
  `RefreshPhase`, which repairs Repairing/PM blocks and failed events but leaves an Idle
  block Idle and keeps hatching only until the explicit clear;
* in an unphased model every event is "failed" with `ChangeEventState(Success, ...)`,
  which changes nothing on an event that is already Success, so such events
  never appear; the phased branch uses `Failure`;
* names are cut to 20 characters;
* the criticality column (only when the option is on) is `critFactor`, reset to 1.0 before
  each candidate and reduced by dependency back-flow while it is failed.
"""
from __future__ import annotations

import multiprocessing as _mp
import os as _os

from raptor.core.engine.fev import FevSession
from raptor.core.engine.spf import render_spf_file
from raptor.core.engine.system_status import SystemColor
from raptor.core.model import BlockStatus, EventStatus
from raptor.core.fileio.rbd import RbdFile

#: Below this many candidates a process pool costs more than it saves.
_PARALLEL_MIN_CANDIDATES = 200


def _name(thing, kind: str) -> str:
    return thing.name or f"{kind} {thing.id}"


def _chunk_unphased(args) -> list[dict]:
    """Test one contiguous slice of blocks/events, in Raptor order.

    Each candidate is independent: the session starts all-good and is
    refreshed after every candidate, exactly as `unphased_rows` does. So
    slices can run in separate processes without changing which rows are
    found or their order (the caller merges in slice order).
    """
    text, file_name, kind, ids = args
    from raptor.core.fileio.rbds import parse_rbds
    fev = FevSession(parse_rbds(text, file_name))
    fev.refresh_all()
    rows: list[dict] = []
    if kind == "block":
        for oid in ids:
            block = fev.model.blocks_by_id.get(oid)
            if block is None:
                continue
            fev.criticality = 1.0
            if block.op_status in (BlockStatus.RUNNING, BlockStatus.STANDBY):
                fev._set_block(block, BlockStatus.REPAIRING)
                fev._analyze()
                if fev.system_color == SystemColor.RED:
                    rows.append({"kind": "Block", "id": block.id,
                                 "name": _name(block, "Block"),
                                 "crit": fev.criticality})
                fev.refresh_all()
    else:
        for oid in ids:
            event = fev.model.events_by_id.get(oid)
            if event is None:
                continue
            fev.criticality = 1.0
            fev._set_event(event, EventStatus.SUCCESS)      # sic: Success 
            fev._analyze()
            if fev.system_color == SystemColor.RED:
                rows.append({"kind": "Event", "id": event.id,
                             "name": _name(event, "Event"),
                             "crit": fev.criticality})
            fev.refresh_all()
    return rows


def _chunks(items: list, n: int) -> list[list]:
    n = max(1, min(n, len(items)))
    base, extra = divmod(len(items), n)
    out: list[list] = []
    at = 0
    for i in range(n):
        size = base + (1 if i < extra else 0)
        if size:
            out.append(items[at:at + size])
        at += size
    return out


def _run_unphased_chunks(rbd: RbdFile, kind: str, ids: list[int],
                         workers: int) -> list[dict]:
    from raptor.core.fileio.rbds import render_rbds
    text = render_rbds(rbd)
    file_name = getattr(rbd, "file_name", "") or "model.rbds"
    chunks = _chunks(ids, max(1, int(workers)) * 4)
    if len(chunks) <= 1:
        return _chunk_unphased((text, file_name, kind, ids))
    ctx = _mp.get_context(_os.environ.get("RAPTOR_MP_CONTEXT", "spawn"))
    payloads = [(text, file_name, kind, chunk) for chunk in chunks]
    with ctx.Pool(min(len(chunks), max(1, int(workers)))) as pool:
        parts = pool.map(_chunk_unphased, payloads)
    return [row for part in parts for row in part]


def unphased_rows(fev: FevSession) -> list[dict]:
    rows: list[dict] = []
    fev.refresh_all()                                   # `RefreshAllFEV` 
    for block in list(fev.model.blocks_by_id.values()):
        fev.criticality = 1.0
        if block.op_status in (BlockStatus.RUNNING, BlockStatus.STANDBY):
            fev._set_block(block, BlockStatus.REPAIRING)
            fev._analyze()
            if fev.system_color == SystemColor.RED:
                rows.append({"kind": "Block", "id": block.id,
                             "name": _name(block, "Block"), "crit": fev.criticality})
            fev.refresh_all()                           # `RefreshAllFEV` 
    for event in list(fev.model.events_by_id.values()):
        fev.criticality = 1.0
        fev._set_event(event, EventStatus.SUCCESS)      # sic: Success, not Failure 
        fev._analyze()
        if fev.system_color == SystemColor.RED:
            rows.append({"kind": "Event", "id": event.id,
                         "name": _name(event, "Event"), "crit": fev.criticality})
        fev.refresh_all()                               # 
    return rows


def phased_sections(fev: FevSession) -> list[tuple[str, list[dict]]]:
    sections: list[tuple[str, list[dict]]] = []
    fev.refresh_all()
    for i, phase in enumerate(fev.model.phases, start=1):
        fev.goto_phase(i)                               # `FEVPhaseChange(i)` 
        rows: list[dict] = []
        for block in list(fev.model.blocks_by_id.values()):
            fev.criticality = 1.0
            if block.op_status in (BlockStatus.RUNNING, BlockStatus.STANDBY):
                fev._set_block(block, BlockStatus.REPAIRING)
                fev._analyze()
                if fev.system_color == SystemColor.RED:
                    rows.append({"kind": "Block", "id": block.id,
                                 "name": _name(block, "Block"), "crit": fev.criticality})
                fev.refresh_phase()                     # `RefreshPhase` 
        for event in list(fev.model.events_by_id.values()):
            fev.criticality = 1.0
            fev._set_event(event, EventStatus.FAILURE)  # the phased branch really fails it
            fev._analyze()
            if fev.system_color == SystemColor.RED:
                rows.append({"kind": "Event", "id": event.id,
                             "name": _name(event, "Event"), "crit": fev.criticality})
            fev.refresh_phase()                         # 
        sections.append((phase.phase_name, rows))
        fev.goto_phase(1)                               # `FEVPhaseChange(1)` 
    return sections


def compute(rbd: RbdFile, *, workers: int = 1):
    """(unphased rows, phased sections) -- one of the two is meaningful: phased
    sections for a model with phases, rows otherwise."""
    fev = FevSession(rbd)
    if fev.phased:
        return [], phased_sections(fev)
    block_ids = list(fev.model.blocks_by_id)
    event_ids = list(fev.model.events_by_id)
    if (workers and int(workers) > 1
            and len(block_ids) + len(event_ids) >= _PARALLEL_MIN_CANDIDATES):
        rows = _run_unphased_chunks(rbd, "block", block_ids, workers)
        if event_ids:
            rows += _run_unphased_chunks(rbd, "event", event_ids, workers)
        return rows, None
    return unphased_rows(fev), None


def single_point_failures(rbd: RbdFile, *, workers: int = 1) -> list[dict]:
    """Rows `{"kind","id","name","crit"}` for an unphased model; for a phased one
    the rows of every phase, each with its `phase` name."""
    rows, sections = compute(rbd, workers=workers)
    if sections is None:
        return rows
    return [dict(r, phase=name) for name, rs in sections for r in rs]


def build_spf_listing(rbd: RbdFile, name_of_file: str, date: str, *,
                      password: bool = False) -> str:
    """The SPF file text, CRLF, as `WriteSPFfile` writes it."""
    rows, sections = compute(rbd)
    return render_spf_file(name_of_file, date, rows, phase_sections=sections,
                           password=password)
