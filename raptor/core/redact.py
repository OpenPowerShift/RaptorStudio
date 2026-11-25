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
    #: Remove element comments (the Comments layer) and comment notes drawn
    #: on the canvas.
    comments: bool = True
    #: "keep" (keep every annotation) | "text" (remove text annotations,
    #: keep shapes and arrows) | "all" (remove every annotation).
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
    #: What the copy still reveals even after every option was applied.
    risks: list[str] = field(default_factory=list)

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

    def risk_summary(self) -> str:
        """The residual-risk disclosure, one line per item."""
        return "\n".join(f"- {risk}" for risk in self.risks)


_NAME_COLUMNS = (
    ("BLOCK_DATA", "blockName", "Block"),
    ("NODE_DATA", "Name", "Node"),
    ("EVENT_DATA", "eventName", "Event"),
    ("HIERARCHY_DATA", "hierarchyName", "Hierarchy"),
)


def _anonymous_name(kind: str, oid: int) -> str:
    """A label the model's own name rules accept: no spaces, at most 20."""
    return f"{kind}_{oid}"


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
                anonymous = _anonymous_name(label, oid)
                out.setdefault(old, anonymous)
                # Comments and hand-written anchors do not always keep the
                # element's capitalisation; one lookup covers both.
                out.setdefault(old.casefold(), anonymous)
    return out


def _clear_unknown(row: dict[str, Any]) -> int:
    removed = 0
    for key in ("_rbds_extra", "_extra"):
        if key in row:
            row.pop(key, None)
            removed += 1
    return removed


#: The display names an anchor carries, each with the reference that locates
#: the same target without a name. An arrow has a tail and a head; a note or
#: shape has one target.
_ANCHOR_NAMES = (("target", "ref"), ("from", "from_ref"), ("to", "to_ref"))

#: Unticked reference cells. They are placeholders, not names: rewriting
#: them to "(unknown)" turned a healthy model into validation errors.
_REF_SENTINELS = {
    "SPARE_POOL_DATA": {"", "unnamed", "none"},
    "RESOURCE_DATA": {"", "none"},
    "TRIGGERS": {"", "none"},
}


def _scrub_anchor(anchor: dict[str, Any],
                  namemap: dict[str, str]) -> dict[str, Any]:
    """Return *anchor* with element names replaced or de-referenced.

    A known name becomes its anonymous label. An unknown name is dropped
    when the anchor still has its `*_ref`, which is enough to place the
    annotation. This is what stopped an arrow whose ends were attached by
    name from carrying both element names into the copy.
    """
    out = dict(anchor)
    # Older or hand-written files sometimes put a name in `ref` (or an
    # arrow's `from_ref`/`to_ref`) instead of `kind:id`; scrub those too.
    def mapped(old: str) -> str | None:
        return namemap.get(old) or namemap.get(old.casefold())

    for key in ("ref", "from_ref", "to_ref"):
        old = str(out.get(key) or "").strip()
        if (new := mapped(old)) is not None:
            out[key] = new
    for name_key, ref_key in _ANCHOR_NAMES:
        if name_key not in out:
            continue
        old = str(out.get(name_key) or "").strip()
        if (new := mapped(old)) is not None:
            out[name_key] = new
        elif out.get(ref_key):
            out.pop(name_key, None)
    return out


def _scrub_tags(out: Any) -> int:
    """Tags and their descriptions are prose a user typed: "Acme Site 7
    pumps" names the site as surely as a block name does. Rename them
    `tag_N` (consistently, so every element keeps the same grouping) and
    drop descriptions. Colours and membership are kept: they are
    structure, not identity."""
    from raptor.core import tags as tg
    tags = tg.clean_tags(getattr(out, "tags", None))
    info = tg.clean_tag_info(getattr(out, "tag_info", None))
    if not tags and not info:
        return 0
    tag_map: dict[str, str] = {}

    def tag_of(old: str) -> str:
        if old not in tag_map:
            tag_map[old] = f"tag_{len(tag_map) + 1}"
        return tag_map[old]

    out.tags = {ref: [tag_of(t) for t in values] for ref, values in tags.items()}
    # The default colour comes from the name, so keep each tag's colour
    # explicitly: the shared copy looks the same as the original.
    out.tag_info = {}
    for old in sorted(set(tag_map) | set(info), key=str.casefold):
        new = tag_of(old)
        colour = (info.get(old) or {}).get("colour") or tg.default_colour(old)
        tg.set_tag_info(out, new, colour=colour)
    return len(tag_map)


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
        keyed = (("SPARE_POOL_DATA", "PoolName", "SparePool"),
                 ("RESOURCE_DATA", "PoolName", "Resource"),
                 ("TRIGGERS", "TriggerName", "Trigger"),
                 ("PHASES", "PhaseName", "Phase"))
        rename: dict[tuple[str, str], str] = {}
        rename_fold: dict[tuple[str, str], str] = {}
        labels = {section: label for section, _column, label in keyed}
        for section, column, label in keyed:
            sec = getattr(out, "sections", {}).get(section)
            for index, row in enumerate(sec.rows if sec is not None else (), 1):
                old = str(row.get(column) or "").strip()
                if not old:
                    continue
                new = f"{label}_{index}"
                row[column] = new
                rename[(section, old)] = new
                rename_fold[(section, old.casefold())] = new
                report.names += 1
        block = getattr(out, "sections", {}).get("BLOCK_DATA")
        for row in (block.rows if block is not None else ()):
            for col, section in (("SparePoolName", "SPARE_POOL_DATA"),
                                 ("Resource1Name", "RESOURCE_DATA"),
                                 ("pmTrig", "TRIGGERS")):
                old = str(row.get(col) or "").strip()
                if not old or old.casefold() in _REF_SENTINELS[section]:
                    continue
                target = (rename.get((section, old))
                          or rename_fold.get((section, old.casefold())))
                # A dangling reference still names the pool/trigger it
                # intended; replace it too so no old label survives.
                row[col] = target or f"{labels[section]}_unknown"
                if row[col] != old:
                    report.names += 1
        # Comment rows carry the element name too.
        comments = getattr(out, "sections", {}).get("COMMENTS")
        for row in (comments.rows if comments is not None else ()):
            old = str(row.get("Name") or "").strip()
            renamed = namemap.get(old) or namemap.get(old.casefold())
            if renamed is not None:
                row["Name"] = renamed
        report.names += _scrub_tags(out)

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
                item["anchor"] = _scrub_anchor(anchor, namemap)
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
    report.risks.extend(_residual_risks(opts))
    return out, report


def _residual_risks(opts: RedactOptions) -> list[str]:
    """What declassification cannot remove, and what this run kept."""
    risks = ["The structure, element IDs, links, positions, costs and "
             "numerical data remain: the topology and the numbers can still "
             "identify a system even without its names."]
    if not opts.names:
        risks.append("Element and hierarchy names are kept.")
    if not opts.comments:
        risks.append("Comments and comment notes are kept as free text.")
    if opts.annotations == "keep":
        risks.append("All annotations are kept, including their text.")
    elif opts.annotations == "text":
        risks.append("Shape and arrow annotations are kept without their "
                     "captions; their positions still describe the diagram.")
    if not opts.metadata:
        risks.append("Project metadata and unknown extension keys are kept.")
    if not opts.advisor:
        risks.append("Advisor ignore reasons are kept as free text.")
    if not opts.rates:
        risks.append("Rate names, descriptions and field formulas are kept.")
    return risks


def write_share_copy(rbd: Any, path: str | Path,
                     options: RedactOptions | None = None,
                     *, source: str | Path | None = None
                     ) -> tuple[Path, RedactReport]:
    """Write a declassified `.rbds` copy and return its report.

    `source` is the original model path when known. Restoring over it is
    refused: the feature promises never to change the original. The write is
    atomic (a failed write cannot truncate an existing destination).
    """
    dest = Path(path)
    if dest.suffix.lower() != ".rbds":
        dest = dest.with_suffix(".rbds")
    if source is not None and dest.resolve() == Path(source).resolve():
        raise ValueError("the share-safe copy must not overwrite the source model")
    copy_rbd, report = declassify(rbd, options)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=dest.name + ".", suffix=".part",
                               dir=str(dest.parent))
    os.close(fd)
    try:
        from raptor.core.fileio.rbds import save_rbds
        save_rbds(copy_rbd, tmp, meta={"file_name": dest.name})
        try:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
        except OSError:
            pass
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dest, report
