"""Paste Special: copy chosen parts of one element onto others, with no Qt.

A plain paste makes a whole new element. Paste Special instead takes ONE
element (the first on the clipboard, or a model element for the API) and
copies chosen groups of it onto elements that already exist:

* the block data groups (`clipboard_plan.DATA_GROUPS`: Failure, Repair,
  Logistics delays, Repair crews, Spares, PM, Costs),
* Dependency and Phasing, which are not plain data (see below),
* Tags (add to, or replace, the target's own) and Comment (replace, append,
  or fill only where empty).

Name, position, id, parent and links are never touched.

`plan_special` returns what would change and `apply_plan` writes it, so the
GUI can show "will change N fields on M elements" before it pushes the same
list as one undo step, and the API/MCP/CLI apply it directly.

Dependency and Phasing
----------------------
A dependency names another element by id, and phase columns are model-wide
(`P 001`... exist on every block), so both are only meaningful inside the
model they came from. Between models they are refused for that group, not
guessed at. Within one model a dependency is not copied onto the element it
points at (a block cannot follow itself).
"""
from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from raptor.core import tags as tag_model
from raptor.core.edit.clipboard_plan import DATA_GROUPS, Buffer
from raptor.core.fileio.comments import comment_text, find_comment_row
from raptor.core.fileio.typed import row_int

#: Dependency and phasing columns of a BLOCK_DATA row.
DEPENDENCY_COLUMNS = ("DepNum", "DepNothing", "DepIdle", "DepPM", "DepFail",
                      "DepPMThresh", "defDepStateIdle", "DepType")
_PHASE_COLUMN = re.compile(r"^P \d+$")

#: Groups of the dialog, in display order: (heading, groups).
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Reliability", ("Failure", "Repair", "Logistics delays")),
    ("Maintenance", ("Repair crews", "Spares", "PM")),
    ("Costs", ("Costs",)),
    ("Behaviour", ("Dependency", "Phasing")),
    ("Organisation", ("Tags", "Comment")),
)
ORGANISATION = ("Tags", "Comment")
BLOCK_ONLY = tuple(DATA_GROUPS) + ("Dependency", "Phasing")
ALL_GROUPS: tuple[str, ...] = tuple(g for _h, gs in SECTIONS for g in gs)
#: What a plain "paste data" used to do and still does by default.
DEFAULT_GROUPS = ("Failure", "Repair")
TAG_MODES = ("add", "replace")
COMMENT_MODES = ("replace", "append", "empty")

_KINDS = ("block", "node", "event", "hier")
_SECTION = {"block": "BLOCK_DATA", "node": "NODE_DATA",
            "event": "EVENT_DATA", "hier": "HIERARCHY_DATA"}
_NAME_COLUMN = {"block": "blockName", "node": "Name",
                "event": "eventName", "hier": "hierarchyName"}
#: DepType -> kind of the element a positive DepNum names
_DEP_KIND = {"RBDBlock": "block", "RBDNode": "node", "RBDEvent": "event",
             "RBDHier": "hier"}


def check_groups(groups: Iterable[str], tag_mode: str = "add",
                 comment_mode: str = "replace") -> list[str]:
    """`groups` as a list, or ValueError naming what is not recognised."""
    chosen = list(groups)
    unknown = [g for g in chosen if g not in ALL_GROUPS]
    if unknown:
        raise ValueError(f"unknown data group(s) "
                         f"{', '.join(map(repr, unknown))}; the groups are "
                         f"{', '.join(ALL_GROUPS)}")
    if tag_mode not in TAG_MODES:
        raise ValueError(f"tag_mode must be one of {', '.join(TAG_MODES)}, "
                         f"not {tag_mode!r}")
    if comment_mode not in COMMENT_MODES:
        raise ValueError(f"comment_mode must be one of "
                         f"{', '.join(COMMENT_MODES)}, not {comment_mode!r}")
    return chosen


@dataclass
class Source:
    """The element copied from, as plain data."""
    kind: str
    id: int
    row: dict
    tags: list[str] = field(default_factory=list)
    #: `{tag: {"colour", "description"}}` for `tags`, as the source model has it
    tag_info: dict[str, dict[str, str]] = field(default_factory=dict)
    #: stored form (`~` separates lines)
    comment: str = ""
    #: the target model is the one this came from
    same_model: bool = True


@dataclass(frozen=True)
class Target:
    kind: str
    id: int
    row: dict

    @property
    def ref(self) -> str:
        return tag_model.element_ref(self.kind, self.id)

    @property
    def name(self) -> str:
        return str(self.row.get(_NAME_COLUMN[self.kind]) or "")


def targets_in(rbd: Any, refs: Iterable[tuple[str, int]]) -> list[Target]:
    """`(kind, id)` pairs that exist in `rbd`, as targets."""
    wanted = set(refs)
    out = []
    for kind in _KINDS:
        sec = rbd.sections.get(_SECTION[kind])
        for row in (sec.rows if sec is not None else []):
            oid = row_int(row.get("ID"), -1)
            if oid > 0 and (kind, oid) in wanted:
                out.append(Target(kind, oid, row))
    return out


# -- the source -------------------------------------------------------------

def comment_items(rbd: Any, buffer: Iterable[tuple[str, dict]]
                  ) -> list[tuple[str, dict]]:
    """The comments of the elements in a copy buffer, as `("comment",
    {"ref", "text"})` buffer items (stored form), so a plain copy and
    paste carries them like tags."""
    section = rbd.sections.get("COMMENTS")
    out: list[tuple[str, dict]] = []
    for kind, row in buffer:
        if kind not in _KINDS:
            continue
        oid = row_int(row.get("ID"), 0)
        name = str(row.get(_NAME_COLUMN[kind]) or "")
        text = comment_text(section, kind, oid, name)
        if text.strip("~ "):
            out.append(("comment", {"ref": tag_model.element_ref(kind, oid),
                                    "text": text}))
    return out


def source_from_buffer(buffer: Buffer, *, same_model: bool
                       ) -> Source | None:
    """The first copied element (a block if there is one, then an event, a
    hierarchy, a node) with the tags and comment that travelled with it."""
    elements = {k: [r for kk, r in buffer if kk == k] for k in _KINDS}
    kind = next((k for k in ("block", "event", "hier", "node")
                 if elements[k]), None)
    if kind is None:
        return None
    row = elements[kind][0]
    oid = row_int(row.get("ID"), 0)
    ref = tag_model.element_ref(kind, oid)
    tags: list[str] = []
    comment = ""
    info: dict[str, dict[str, str]] = {}
    for k, item in buffer:
        if k == "tags" and item.get("ref") == ref:
            try:
                values = json.loads(str(item.get("tags") or "[]"))
            except ValueError:
                values = []
            if isinstance(values, list):
                tags = tag_model.clean_tags({ref: values}).get(ref, [])
        elif k == "comment" and item.get("ref") == ref:
            comment = str(item.get("text") or "")
    for k, item in buffer:
        if k == "taginfo" and item.get("tag") in tags:
            info[str(item["tag"])] = {
                "colour": str(item.get("colour") or ""),
                "description": str(item.get("description") or "")}
    return Source(kind, oid, row, tags, info, comment, same_model)


def source_from_model(rbd: Any, kind: str, row: dict) -> Source:
    """A model element as a source (the API's `source` selector)."""
    oid = row_int(row.get("ID"), 0)
    ref = tag_model.element_ref(kind, oid)
    tags = tag_model.tags_for(rbd, ref)
    info = tag_model.clean_tag_info(getattr(rbd, "tag_info", None))
    name = str(row.get(_NAME_COLUMN[kind]) or "")
    return Source(kind, oid, row, tags,
                  {t: dict(info[t]) for t in tags if info.get(t)},
                  comment_text(rbd.sections.get("COMMENTS"), kind, oid, name),
                  True)


# -- what applies -----------------------------------------------------------

def _is_self(source: Source, target: Target) -> bool:
    return (source.same_model and source.kind == target.kind
            and source.id == target.id)


def _phase_columns(row: Mapping[str, Any]) -> list[str]:
    return [c for c in row if _PHASE_COLUMN.match(c)]


def _group_columns(group: str, source_row: Mapping[str, Any]) -> list[str]:
    if group == "Dependency":
        return list(DEPENDENCY_COLUMNS)
    if group == "Phasing":
        return ["Phasing", *_phase_columns(source_row)]
    return list(DATA_GROUPS[group])


def _dependency_blocked(source: Source, target: Target) -> str:
    """Why this source's dependency cannot go on this block ("" when it can)."""
    num = row_int(source.row.get("DepNum"), 0)
    if num <= 0:
        return ""                 # independent, own node or whole system
    if not source.same_model:
        return "a dependency on another element means nothing in another model"
    kind = _DEP_KIND.get(str(source.row.get("DepType", "")))
    if kind == target.kind and num == target.id:
        return "a block cannot follow itself"
    return ""


def applicability(source: Source, targets: Sequence[Target]
                  ) -> dict[str, str]:
    """`{group: ""}` when the group can be pasted, else why not (for the
    dialog to grey it out with a tooltip)."""
    effective = [t for t in targets if not _is_self(source, t)]
    out: dict[str, str] = {}
    if not effective:
        reason = ("nothing is selected" if not targets else
                  "only the copied element is selected")
        return {g: reason for g in ALL_GROUPS}
    blocks = [t for t in effective if t.kind == "block"]
    for group in ALL_GROUPS:
        if group in ORGANISATION:
            out[group] = ""
        elif source.kind != "block":
            out[group] = "the copied element is not a block"
        elif not blocks:
            out[group] = "no block is selected"
        else:
            cols = _group_columns(group, source.row)
            if not any(c in source.row for c in cols) or not any(
                    c in t.row for t in blocks for c in cols):
                out[group] = "the model has no such columns"
            elif group == "Phasing" and not source.same_model:
                out[group] = ("phases belong to a model; copy within the "
                              "same model")
            elif group == "Phasing" and not any(
                    set(_phase_columns(t.row)) == set(
                        _phase_columns(source.row)) for t in blocks):
                out[group] = "the phase count differs"
            elif group == "Dependency" and all(
                    _dependency_blocked(source, t) for t in blocks):
                out[group] = (_dependency_blocked(source, blocks[0])
                              or "it cannot be copied here")
            else:
                out[group] = ""
    return out


# -- the plan ---------------------------------------------------------------

@dataclass
class SpecialPlan:
    #: block cell changes `{row, id, column, old, new}`
    cells: list[dict] = field(default_factory=list)
    #: `[{kind, id, ref, old, new}]` per element whose tags change
    tag_changes: list[dict] = field(default_factory=list)
    #: `(tags, tag_info)` of the model once tag changes are applied
    tags_after: tuple[dict, dict] | None = None
    #: `[{kind, id, name, old, new}]`, comments in stored form
    comments: list[dict] = field(default_factory=list)
    #: targets looked at, and those where nothing would change
    targets: int = 0
    unchanged: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def fields(self) -> int:
        """Cells, plus one per element whose tags and one per element whose
        comment change."""
        return len(self.cells) + len(self.tag_changes) + len(self.comments)

    @property
    def elements(self) -> int:
        """Distinct elements that change."""
        keys = {("block", int(c["id"] or 0)) for c in self.cells}
        keys |= {(c["kind"], c["id"]) for c in self.tag_changes}
        keys |= {(c["kind"], c["id"]) for c in self.comments}
        return len(keys)

    def summary(self) -> str:
        """One line for the dialog's preview."""
        if not self.fields:
            text = "Nothing would change"
            if self.targets:
                text += f": all {self.targets} already match"
            return text + "."
        return (f"Will change {self.fields} field"
                f"{'' if self.fields == 1 else 's'} on {self.elements} "
                f"element{'' if self.elements == 1 else 's'}; "
                f"{self.unchanged} already match.")


def _merged_comment(old: str, new: str, mode: str) -> str:
    if mode == "append":
        if not new.strip("~ "):
            return old
        return f"{old}~{new}" if old.strip("~ ") else new
    if mode == "empty":
        return old if old.strip("~ ") else new
    return new


def _plan_cells(plan: SpecialPlan, source: Source,
                effective: Sequence[Target], data_groups: Sequence[str]
                ) -> set[tuple[str, int]]:
    touched: set[tuple[str, int]] = set()
    skipped = False
    for target in effective:
        if target.kind != "block":
            continue
        for group in data_groups:
            if group == "Dependency" and _dependency_blocked(source, target):
                skipped = True
                continue
            if group == "Phasing" and (
                    not source.same_model
                    or set(_phase_columns(target.row))
                    != set(_phase_columns(source.row))):
                continue
            for column in _group_columns(group, source.row):
                if column not in source.row or column not in target.row:
                    continue
                old, new = target.row.get(column), source.row.get(column)
                if old != new:
                    plan.cells.append({"row": target.row,
                                       "id": target.row.get("ID", ""),
                                       "column": column, "old": old,
                                       "new": new})
                    touched.add((target.kind, target.id))
    if skipped:
        plan.notes.append("Some dependencies were not copied (on another "
                          "element, or on the block itself).")
    return touched


def _plan_tags(plan: SpecialPlan, rbd: Any, source: Source,
               effective: Sequence[Target], tag_mode: str
               ) -> set[tuple[str, int]]:
    touched: set[tuple[str, int]] = set()
    scratch = SimpleNamespace(
        tags=tag_model.clean_tags(getattr(rbd, "tags", None)),
        tag_info=tag_model.clean_tag_info(getattr(rbd, "tag_info", None)))
    added: set[str] = set()
    for target in effective:
        old = tag_model.tags_for(scratch, target.ref)
        new = (list(source.tags) if tag_mode == "replace"
               else [*old, *[t for t in source.tags if t not in old]])
        tag_model.set_tags(scratch, target.ref, new)
        new = tag_model.tags_for(scratch, target.ref)
        if new != old:
            plan.tag_changes.append({"kind": target.kind, "id": target.id,
                                     "ref": target.ref, "old": old,
                                     "new": new})
            touched.add((target.kind, target.id))
            added.update(t for t in new if t not in old)
    # a tag new to the target model keeps the colour and description it had
    # where it came from; one the target already defines is not recoloured
    # (the same rule as a plain paste)
    for tag in sorted(added, key=str.casefold):
        entry = source.tag_info.get(tag)
        if entry and not tag_model.clean_tag_info(scratch.tag_info).get(tag):
            tag_model.set_tag_info(
                scratch, tag,
                colour=tag_model.clean_colour(entry.get("colour")),
                description=str(entry.get("description") or ""))
    if plan.tag_changes:
        plan.tags_after = (tag_model.clean_tags(scratch.tags),
                           tag_model.clean_tag_info(scratch.tag_info))
    return touched


def _plan_comments(plan: SpecialPlan, rbd: Any, source: Source,
                   effective: Sequence[Target], comment_mode: str
                   ) -> set[tuple[str, int]]:
    touched: set[tuple[str, int]] = set()
    section = rbd.sections.get("COMMENTS")
    for target in effective:
        old = comment_text(section, target.kind, target.id, target.name)
        new = _merged_comment(old, source.comment, comment_mode)
        if new != old:
            plan.comments.append({"kind": target.kind, "id": target.id,
                                  "name": target.name, "old": old,
                                  "new": new})
            touched.add((target.kind, target.id))
    return touched


def plan_special(rbd: Any, source: Source, targets: Sequence[Target],
                 groups: Collection[str], *, tag_mode: str = "add",
                 comment_mode: str = "replace") -> SpecialPlan:
    """What pasting `groups` of `source` onto `targets` would change in
    `rbd` (the model the targets are in). Nothing is written."""
    chosen = check_groups(groups, tag_mode, comment_mode)
    plan = SpecialPlan()
    effective = [t for t in targets if not _is_self(source, t)]
    plan.targets = len(effective)
    touched: set[tuple[str, int]] = set()
    data_groups = [g for g in chosen if g in BLOCK_ONLY]
    if data_groups and source.kind == "block":
        touched |= _plan_cells(plan, source, effective, data_groups)
    if "Tags" in chosen:
        touched |= _plan_tags(plan, rbd, source, effective, tag_mode)
    if "Comment" in chosen:
        touched |= _plan_comments(plan, rbd, source, effective, comment_mode)
    plan.unchanged = sum(1 for t in effective
                         if (t.kind, t.id) not in touched)
    return plan


# -- applying without a view (the API) --------------------------------------

def write_comment(rbd: Any, kind: str, oid: int, name: str,
                  stored: str) -> bool:
    """Set one element's COMMENTS row (stored form). The same row rules as
    the GUI's undoable write: an ID row is reused, a legacy no-ID row is
    stamped unless another element shares its name, and a blank comment on
    an element with no row adds none."""
    from raptor.core.fileio.comments import is_legacy_comment_row, names_agree
    from raptor.core.fileio.rbdlib import COMMENT_COLS
    from raptor.core.fileio.rbd import RbdSection
    sec = rbd.sections.get("COMMENTS")
    if sec is None:
        if not stored:
            return False
        sec = RbdSection("COMMENTS", list(COMMENT_COLS), [])
        rbd.sections["COMMENTS"] = sec
    row = find_comment_row(sec, kind, oid, name)
    shared = False
    if row is not None and is_legacy_comment_row(row):
        column = _NAME_COLUMN[kind]
        shared = any(
            0 < row_int(r.get("ID"), 0) != oid
            and names_agree(str(r.get(column) or ""), name)
            for r in rbd.sections[_SECTION[kind]].rows)
        if shared:
            row = None
    if row is None:
        if not stored and not shared:
            return False
        sec.rows.append({"Type": kind, "ID": str(oid), "Name": name,
                         "Comment": stored})
    else:
        if is_legacy_comment_row(row):
            row["ID"] = str(oid)
        if row.get("Comment", "") == stored:
            return False
        row["Comment"] = stored
    return True


def apply_plan(rbd: Any, plan: SpecialPlan) -> int:
    """Write a plan into `rbd` directly (no undo); the number of fields
    written."""
    for change in plan.cells:
        change["row"][change["column"]] = change["new"]
    if plan.tags_after is not None:
        rbd.tags, rbd.tag_info = plan.tags_after
    for change in plan.comments:
        write_comment(rbd, change["kind"], change["id"], change["name"],
                      change["new"])
    return plan.fields
