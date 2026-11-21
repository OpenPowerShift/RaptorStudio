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
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    #: Remove the rate library and field formula links. Their numbers stay in
    #: the element rows; the names/descriptions are the identifying prose.
    rates: bool = True

    def normalised(self) -> RedactOptions:
        if self.annotations not in ANNOTATION_POLICIES:
            raise ValueError(
                f"annotations must be one of {', '.join(ANNOTATION_POLICIES)}; "
                f"got {self.annotations!r}")
        return RedactOptions(names=bool(self.names), comments=bool(self.comments),
                            annotations=self.annotations,
                            metadata=bool(self.metadata),
                            advisor=bool(self.advisor), rates=bool(self.rates))


@dataclass
class RedactReport:
    names: int = 0
    comments: int = 0
    text_annotations: int = 0
    graphic_annotations: int = 0
    metadata: int = 0
    advisor: int = 0
    rates: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def removed(self) -> int:
        return (self.names + self.comments + self.text_annotations
                + self.graphic_annotations + self.metadata + self.advisor
                + self.rates)

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
        if self.rates:
            parts.append(f"{self.rates} rate/formula record(s) removed")
        return "; ".join(parts) or "nothing removed"


_NAME_COLUMNS = (
    ("BLOCK_DATA", "blockName", "Block"),
    ("NODE_DATA", "Name", "Node"),
    ("EVENT_DATA", "eventName", "Event"),
    ("HIERARCHY_DATA", "hierarchyName", "Hierarchy"),
)


def _anonymous_name(kind: str, oid: int) -> str:
    return f"{kind} {oid}"


def _name_map(rbd: Any) -> dict[str, str]:
    """Original element name -> anonymous name, for anchors/comments."""
    out: dict[str, str] = {}
    for section, column, label in _NAME_COLUMNS:
        sec = getattr(rbd, "sections", {}).get(section)
        for row in (sec.rows if sec is not None else ()):
            oid = row_int(row.get("ID"), 0)
            if oid <= 0:
                continue
            old = str(row.get(column) or "").strip()
            if old:
                out.setdefault(old, _anonymous_name(label, oid))
    return out


def _clear_unknown(row: dict[str, Any]) -> int:
    removed = 0
    for key in ("_rbds_extra", "_extra"):
        if key in row:
            row.pop(key, None)
            removed += 1
    return removed


def declassify(rbd: Any, options: RedactOptions | None = None  # noqa: C901, PLR0912, PLR0915
               ) -> tuple[Any, RedactReport]:
    """Return `(copy, report)`; the original model is not changed."""
    opts = (options or RedactOptions()).normalised()
    out = copy.deepcopy(rbd)
    report = RedactReport()
    namemap = _name_map(out)

    if opts.names:
        for section, column, label in _NAME_COLUMNS:
            sec = getattr(out, "sections", {}).get(section)
            for row in (sec.rows if sec is not None else ()):
                oid = row_int(row.get("ID"), 0)
                if oid <= 0:
                    continue
                row[column] = _anonymous_name(label, oid)
                report.names += 1
        # Named pools/resources/triggers/phases and the references to them.
        keyed = (("SPARE_POOL_DATA", "PoolName", "Spare pool"),
                 ("RESOURCE_DATA", "PoolName", "Resource"),
                 ("TRIGGERS", "TriggerName", "Trigger"),
                 ("PHASES", "PhaseName", "Phase"))
        rename: dict[tuple[str, str], str] = {}
        for section, column, label in keyed:
            sec = getattr(out, "sections", {}).get(section)
            for index, row in enumerate(sec.rows if sec is not None else (), 1):
                old = str(row.get(column) or "").strip()
                if not old:
                    continue
                new = f"{label} {index}"
                row[column] = new
                rename[(section, old)] = new
                report.names += 1
        block = getattr(out, "sections", {}).get("BLOCK_DATA")
        for row in (block.rows if block is not None else ()):
            for col, section in (("SparePoolName", "SPARE_POOL_DATA"),
                                 ("Resource1Name", "RESOURCE_DATA"),
                                 ("pmTrig", "TRIGGERS")):
                old = str(row.get(col) or "").strip()
                if old and (section, old) in rename:
                    row[col] = rename[(section, old)]
        # Comment rows carry the element name too.
        comments = getattr(out, "sections", {}).get("COMMENTS")
        for row in (comments.rows if comments is not None else ()):
            old = str(row.get("Name") or "").strip()
            if old in namemap:
                row["Name"] = namemap[old]

    if opts.comments:
        sec = getattr(out, "sections", {}).get("COMMENTS")
        rows = list(sec.rows) if sec is not None else []
        report.comments += len(rows)
        if sec is not None:
            sec.rows = []

    anns = list(getattr(out, "annotations", None) or [])
    keep = []
    for ann in anns:
        item = dict(ann)
        # A comment note is a comment, not a decorative annotation.
        if opts.comments and item.get("comment_of") is not None:
            report.comments += 1
            continue
        kind = str(item.get("kind") or "")
        is_text = kind == "text"
        if opts.annotations == "all" or (
                opts.annotations == "text" and is_text):
            if is_text:
                report.text_annotations += 1
            else:
                report.graphic_annotations += 1
            continue
        if opts.names:
            anchor = item.get("anchor")
            if isinstance(anchor, dict):
                target = str(anchor.get("target") or "").strip()
                if target in namemap:
                    anchor = dict(anchor)
                    anchor["target"] = namemap[target]
                    item["anchor"] = anchor
                elif anchor.get("ref"):
                    # The reference is enough; do not carry the old name.
                    anchor = dict(anchor)
                    anchor.pop("target", None)
                    item["anchor"] = anchor
        if opts.annotations == "text" and not is_text:
            item["text"] = ""
        if opts.metadata:
            report.metadata += _clear_unknown(item)
        keep.append(item)
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
        for sec in getattr(out, "sections", {}).values():
            for row in sec.rows:
                report.metadata += _clear_unknown(row)
        if changed or report.metadata:
            report.metadata = max(1, report.metadata)

    if opts.advisor:
        ignored = (getattr(out, "advisor", None) or {}).get("ignored") or []
        report.advisor = len(ignored)
        if ignored:
            out.advisor = {}

    if opts.rates:
        report.rates = len(getattr(out, "rates", None) or []) + \
            len(getattr(out, "formulas", None) or {})
        if report.rates:
            out.rates = []
            out.formulas = {}

    report.notes.append(
        "IDs, links, distributions, costs and simulation data are unchanged; "
        "the copy still runs.")
    return out, report


def write_share_copy(rbd: Any, path: str | Path,
                     options: RedactOptions | None = None,
                     *, source: str | Path | None = None
                     ) -> tuple[Path, RedactReport]:
    """Write a declassified `.rbds` copy and return its report.

    `source` is the original model path when known. Restoring over it is
    refused: the feature promises never to change the original. The write is
    atomic (a failed write cannot truncate an existing destination).
    """
    copy_rbd, report = declassify(rbd, options)
    dest = Path(path)
    if dest.suffix.lower() != ".rbds":
        dest = dest.with_suffix(".rbds")
    if source is not None and dest.resolve() == Path(source).resolve():
        raise ValueError("the share-safe copy must not overwrite the source model")
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=dest.name + ".", suffix=".part",
                               dir=str(dest.parent))
    os.close(fd)
    try:
        from raptor.core.fileio.rbds import save_rbds
        save_rbds(copy_rbd, tmp, meta={"file_name": dest.name})
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dest, report
