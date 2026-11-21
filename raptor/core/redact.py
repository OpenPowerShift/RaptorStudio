"""Make a share-safe copy of a model.

Declassification is deliberate loss: names become type labels, comments and
chosen annotation layers are removed, and project metadata can be cleared.
The simulation structure, IDs, links and numerical data are untouched, so
the copy still runs and can still be diffed against the original without
revealing the original labels.

Nothing here mutates the open model; every function works on a deep copy.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raptor.core.fileio.rbds import save_rbds
from raptor.core.fileio.typed import row_int

ANNOTATION_POLICIES = ("keep", "text", "all")


@dataclass(frozen=True)
class RedactOptions:
    """What to remove from the shared copy."""
    #: Anonymise element and hierarchy names and project title.
    names: bool = True
    #: Remove element comments (the Comments layer).
    comments: bool = True
    #: "keep" | "text" (text annotations only) | "all".
    annotations: str = "all"
    #: Clear the print title block, currency/display preferences and
    #: unknown extension keys.
    metadata: bool = True
    #: Clear Advisor ignore reasons (they are free text).
    advisor: bool = True

    def normalised(self) -> RedactOptions:
        policy = self.annotations if self.annotations in ANNOTATION_POLICIES \
            else "keep"
        return RedactOptions(names=bool(self.names), comments=bool(self.comments),
                            annotations=policy,
                            metadata=bool(self.metadata),
                            advisor=bool(self.advisor))


@dataclass
class RedactReport:
    names: int = 0
    comments: int = 0
    text_annotations: int = 0
    graphic_annotations: int = 0
    metadata: int = 0
    advisor: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def removed(self) -> int:
        return (self.names + self.comments + self.text_annotations
                + self.graphic_annotations + self.metadata + self.advisor)

    def summary(self) -> str:
        parts = []
        if self.names:
            parts.append(f"{self.names} name(s) replaced")
        if self.comments:
            parts.append(f"{self.comments} comment(s) removed")
        if self.text_annotations:
            parts.append(f"{self.text_annotations} text annotation(s) removed")
        if self.graphic_annotations:
            parts.append(f"{self.graphic_annotations} shape/arrow annotation(s) removed")
        if self.metadata:
            parts.append("project metadata cleared")
        if self.advisor:
            parts.append("Advisor notes cleared")
        return "; ".join(parts) or "nothing removed"


_NAME_COLUMNS = (
    ("BLOCK_DATA", "blockName", "Block"),
    ("NODE_DATA", "Name", "Node"),
    ("EVENT_DATA", "eventName", "Event"),
    ("HIERARCHY_DATA", "hierarchyName", "Hierarchy"),
)


def _anonymous_name(kind: str, oid: int) -> str:
    return f"{kind} {oid}"


def declassify(rbd: Any, options: RedactOptions | None = None  # noqa: C901, PLR0912
               ) -> tuple[Any, RedactReport]:
    """Return `(copy, report)`; the original model is not changed."""
    opts = (options or RedactOptions()).normalised()
    out = copy.deepcopy(rbd)
    report = RedactReport()

    if opts.names:
        for section, column, label in _NAME_COLUMNS:
            sec = getattr(out, "sections", {}).get(section)
            for row in (sec.rows if sec is not None else ()):
                oid = row_int(row.get("ID"), 0)
                if oid <= 0:
                    continue
                row[column] = _anonymous_name(label, oid)
                report.names += 1
        out.file_name = "shared-model.rbds"

    if opts.comments:
        sec = getattr(out, "sections", {}).get("COMMENTS")
        rows = list(sec.rows) if sec is not None else []
        report.comments += len(rows)
        if sec is not None:
            sec.rows = []

    anns = list(getattr(out, "annotations", None) or [])
    keep = []
    for ann in anns:
        kind = str(ann.get("kind") or "")
        is_text = kind == "text"
        if opts.annotations == "all" or (
                opts.annotations == "text" and is_text):
            if is_text:
                report.text_annotations += 1
            else:
                report.graphic_annotations += 1
        else:
            keep.append(ann)
    if anns:
        out.annotations = keep

    if opts.metadata:
        changed = False
        for attr in ("project_meta", "display_unit", "view_transform",
                     "targets", "currency", "rbds_extra"):
            value = getattr(out, attr, None)
            if value not in (None, "", {}, "$"):
                changed = True
            setattr(out, attr, None if attr in ("project_meta", "display_unit",
                                                 "view_transform")
                    else {} if attr in ("targets", "rbds_extra")
                    else "$" if attr == "currency" else value)
        if changed:
            report.metadata = 1

    if opts.advisor:
        ignored = (getattr(out, "advisor", None) or {}).get("ignored") or []
        report.advisor = len(ignored)
        if ignored:
            out.advisor = {}

    report.notes.append(
        "IDs, links, distributions, costs and simulation data are unchanged; "
        "the copy still runs.")
    return out, report


def write_share_copy(rbd: Any, path: str | Path,
                     options: RedactOptions | None = None
                     ) -> tuple[Path, RedactReport]:
    """Write a declassified `.rbds` copy and return its report."""
    copy_rbd, report = declassify(rbd, options)
    dest = Path(path)
    if dest.suffix.lower() != ".rbds":
        dest = dest.with_suffix(".rbds")
    save_rbds(copy_rbd, str(dest))
    return dest, report
