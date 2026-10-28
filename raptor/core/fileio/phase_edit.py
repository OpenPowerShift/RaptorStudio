"""Insert, delete and clear mission phases the way Raptor 7's Phases dialog does.

A phase is positional: the PHASES rows are numbered 1..n, and every phased block, event and node carries
one token per phase in the `P 001`.. (blocks, events) or `P001`.. (nodes) columns. Inserting or deleting a
phase therefore moves tokens, and the header's `Phases=` count and the number of `P` columns follow it. A
file where they disagree makes Raptor 7 read the next row's name as a token and crash, so this module is
the only place that changes the phase list of a model that has elements assigned to phases
(`rbd_builder`'s rule: build through the API, never by hand).

What Raptor 7 does (`PhaseBoxObj.AdjustTables`, `BeSelected`; confirmed byte for byte against
captures of Raptor 7 on `Phasing3`, `tests/data/phase_editing`):

* **Insert at position p** (`AdjustTables(TRUE, p)`, lines 7237-7325): later phases move one place later and the
  new phase gets, for every element that uses phasing:
  - a block: `A: 1.000` (Active, stress 1);
  - an event: `P: 0.000` (Prior);
  - a node: its own `k` (the number of good paths it needs), or `L` (linked) when `k` is 0;
  - a hierarchy: `A`, which is stored on the hierarchy's Out node as `1`.
  Adding a phase at the end is an insert at n + 1.
* **Delete phase p** (`AdjustTables(FALSE, p)`, 7326-7380): that phase's token goes and later ones move up.
* **Clear all** (`BeSelected` 108): every phase and every token goes. The elements keep their phasing flag
  (`usesPhasing` stays TRUE), exactly as Raptor 7 writes the file: `Phasing=1` on a row with no
  tokens and no `P` columns in the header.

A hierarchy has no phase columns of its own; its per-phase Active/Linked/Cut values are its Out node's tokens
(`SetPhases` on `outNode`, 7500-7513), and the `Phs` flag on the hierarchy row mirrors the Out node's flag.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from raptor.core.fileio.rbd import RbdFile, RbdSection
from raptor.core.fileio.rbdlib import make_phase_row


class PhaseEditError(ValueError):
    pass


_P_COLUMN = re.compile(r"^P ?\d{3}$")

#: section -> (phase flag column, token column style: blocks and events have a space, nodes do not)
_SECTIONS: dict[str, tuple[str, str]] = {
    "BLOCK_DATA": ("Phasing", "P {:03d}"),
    "EVENT_DATA": ("Phasing", "P {:03d}"),
    "NODE_DATA": ("Phases", "P{:03d}"),
}

_BLOCK_DEFAULT = "A: 1.000"
_EVENT_DEFAULT = "P: 0.000"


def _is_phased(row: dict, flag_col: str) -> bool:
    return row.get(flag_col, "") not in ("0", "")


def _p_columns(section: RbdSection) -> list[str]:
    return [c for c in section.columns if _P_COLUMN.match(c)]


def _node_default(row: dict) -> str:
    """A node's cell for a new phase: its own good-path count, `L` when that is 0 (`AdjustTables` 7305-7315).
    The Out node of a hierarchy is the hierarchy's row in the table: `A`, which is stored as 1."""
    if row.get("Type") == "5":
        return "1"
    k = int(float(row.get("k", "1")))
    if k == -1:
        k = 1                       # the reader's good-path count for k = -1 (`typed.node_from_row`)
    return "L" if k == 0 else str(k)


def _default(section: str, row: dict) -> str:
    if section == "BLOCK_DATA":
        return _BLOCK_DEFAULT
    if section == "EVENT_DATA":
        return _EVENT_DEFAULT
    return _node_default(row)


def phase_count(rbd: RbdFile) -> int:
    return len(rbd.sections["PHASES"].rows) if "PHASES" in rbd.sections else 0


def phase_problems(rbd: RbdFile) -> list[str]:
    """What would make Raptor 7 misread the phase data: the header count, the `P` columns and every phased
    row's tokens must all agree with the PHASES rows."""
    n = phase_count(rbd)
    out: list[str] = []
    declared = rbd.counts.get("Phases")
    if declared is not None and declared != n:
        out.append(f"header Phases={declared} but PHASES has {n} row(s)")
    for name, (flag_col, _style) in _SECTIONS.items():
        sec = rbd.sections.get(name)
        if sec is None:
            continue
        cols = _p_columns(sec)
        if n and len(cols) != n and any(_is_phased(r, flag_col) for r in sec.rows):
            out.append(f"{name} has {len(cols)} phase column(s) for {n} phase(s)")
        for r in sec.rows:
            if r.get("ID") == "0" or not _is_phased(r, flag_col):
                continue
            have = sum(1 for c in cols if c in r)
            if n and have != n:
                label = r.get("blockName") or r.get("eventName") or r.get("Name") or r.get("ID")
                out.append(f"{name} row {label} has {have} phase token(s) for {n} phase(s)")
    return out


@dataclass
class PhaseEdit:
    """What an edit did, for the caller's undo text and status line."""
    action: str
    position: int
    rows_changed: int


def _retoken(rbd: RbdFile, fn) -> int:
    """Rewrite every phased row's tokens through `fn(section_name, row, tokens) -> tokens`, then make the
    header's `P` columns match the new phase count. Returns how many rows changed."""
    n = phase_count(rbd)
    changed = 0
    for name, (flag_col, style) in _SECTIONS.items():
        sec = rbd.sections.get(name)
        if sec is None:
            continue
        old_cols = _p_columns(sec)
        for row in sec.rows:
            if row.get("ID") == "0" or not _is_phased(row, flag_col):
                continue
            toks = [row[c] for c in old_cols if c in row]
            new = fn(name, row, toks)
            for c in old_cols:
                row.pop(c, None)
            for i, t in enumerate(new, 1):
                row[style.format(i)] = t
            changed += 1
        keep = [c for c in sec.columns if not _P_COLUMN.match(c)]
        sec.columns[:] = keep + [style.format(i) for i in range(1, n + 1)]
    return changed


def sync_phase_columns(rbd: RbdFile) -> None:
    """Make the header's `Phases=` count and the `P` columns of BLOCK/EVENT/NODE agree with the PHASES rows.

    A model read from `.rbds` carries each phased element's tokens as row cells but not the `P` columns (the
    template's header has none), so rendering it as `.rbd` dropped every phase value while still declaring
    `Phases=n`. Called after such a read and by `builder.sync_counts`.
    """
    n = phase_count(rbd)
    for name, (_flag, style) in _SECTIONS.items():
        sec = rbd.sections.get(name)
        if sec is None:
            continue
        keep = [c for c in sec.columns if not _P_COLUMN.match(c)]
        sec.columns[:] = keep + [style.format(i) for i in range(1, n + 1)]
    if "PHASES" in rbd.sections or n:
        rbd.counts["Phases"] = n


def _check_complete(rbd: RbdFile) -> None:
    problems = phase_problems(rbd)
    if problems:
        raise PhaseEditError("the phase data is not consistent before the edit: " + "; ".join(problems))


def insert_phase(rbd: RbdFile, position: int, *, name: str, dist: int = 19,
                 params: tuple = (100.0, 0.0, 0.0), mission: bool = True) -> PhaseEdit:
    """Insert a phase so that it becomes phase `position` (1..n+1; n+1 adds it at the end). A name already used by
    another phase is accepted, as Raptor 7 does (`real_ins1.rbd` inserts a second `D000-075_Orbit`); the GUI picks a
    fresh name itself."""
    _check_complete(rbd)
    phases = rbd.sections["PHASES"]
    n = len(phases.rows)
    if not 1 <= position <= n + 1:
        raise PhaseEditError(f"phase position {position} is outside 1..{n + 1}")
    if not name or " " in name:
        raise PhaseEditError("'Phase Name' field can't be blank or have blank spaces!")
    if len(name) > 20:
        raise PhaseEditError("'Phase Name' must be no greater than 20 characters!")
    phases.rows.insert(position - 1, make_phase_row(name, position, 1 if mission else 0, dist, params))
    for i, row in enumerate(phases.rows, 1):
        row["Num"] = str(i)

    def fn(section, row, toks):
        toks = list(toks)
        toks.insert(position - 1, _default(section, row))
        return toks
    changed = _retoken(rbd, fn)
    rbd.counts["Phases"] = n + 1
    return PhaseEdit("insert", position, changed)


def delete_phase(rbd: RbdFile, position: int) -> PhaseEdit:
    """Delete phase `position`; later phases move up one place."""
    _check_complete(rbd)
    phases = rbd.sections["PHASES"]
    n = len(phases.rows)
    if not 1 <= position <= n:
        raise PhaseEditError(f"phase position {position} is outside 1..{n}")
    del phases.rows[position - 1]
    for i, row in enumerate(phases.rows, 1):
        row["Num"] = str(i)

    def fn(_section, _row, toks):
        toks = list(toks)
        del toks[position - 1]
        return toks
    changed = _retoken(rbd, fn)
    rbd.counts["Phases"] = n - 1
    return PhaseEdit("delete", position, changed)


def clear_phases(rbd: RbdFile) -> PhaseEdit:
    """Remove every phase and every token. The elements keep their phasing flag, as in real."""
    _check_complete(rbd)
    phases = rbd.sections["PHASES"]
    phases.rows.clear()
    changed = _retoken(rbd, lambda _s, _r, _t: [])
    rbd.counts["Phases"] = 0
    return PhaseEdit("clear", 0, changed)
