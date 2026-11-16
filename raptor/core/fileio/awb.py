"""Availability Workbench (AWB) project interchange.

AWB 5.0 saves a project as one ``.awb`` file: a small, uncompressed binary
table store.  This module reads that store and writes the subset AWB needs
for a Reliability Block Diagram (RBD) project, so RAPTOR models can move to
AWB and AWB projects can move back to RAPTOR without either program being
installed.

The format and the mapping are documented in ``docs/AWB_FORMAT.md``.  The
conversion is deliberately honest about what it cannot carry: every
conversion returns an :class:`AwbReport` whose ``not_in_target`` list names
the features the destination program has no equivalent for, with a count.
The CLI prints the report as a summary before it writes anything.
"""
from __future__ import annotations

import json
import math
import struct
import uuid
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from collections.abc import Iterable
from typing import Any

from raptor.core.fileio.rbd import RbdFile, RbdSection
from raptor.core.units_conv import HOURS_PER_UNIT

# ---------------------------------------------------------------------------
# Errors and report
# ---------------------------------------------------------------------------


class AwbFormatError(ValueError):
    """The file is not an AWB 5.0 table store we understand."""


@dataclass
class AwbLoss:
    """One kind of feature the destination cannot represent."""

    feature: str
    count: int
    detail: str = ""
    where: str = ""          # "AWB" or "RAPTOR"

    def as_dict(self) -> dict[str, Any]:
        return {"feature": self.feature, "count": self.count,
                "detail": self.detail, "where": self.where}


@dataclass
class AwbReport:
    """What a conversion did, and what it could not carry."""

    direction: str                  # "rbds-to-awb" or "awb-to-rbds"
    source: str
    target: str
    counts: dict[str, int] = field(default_factory=dict)
    not_in_target: list[AwbLoss] = field(default_factory=list)
    not_in_source: list[AwbLoss] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_losses(self) -> bool:
        return bool(self.not_in_target or self.not_in_source)

    def add(self, feature: str, count: int, detail: str = "",
            where: str | None = None) -> None:
        if count <= 0:
            return
        where = where or ("AWB" if self.direction == "rbds-to-awb"
                          else "RAPTOR")
        self.not_in_target.append(AwbLoss(feature, count, detail, where))

    def add_source(self, feature: str, count: int, detail: str = "") -> None:
        """A feature the source had that the destination also lacks, but
        which is not the direction's main ``not_in_target`` list."""
        if count <= 0:
            return
        self.not_in_source.append(
            AwbLoss(feature, count, detail,
                    "AWB" if self.direction == "rbds-to-awb" else "RAPTOR"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "raptor-awb-report/1",
            "direction": self.direction,
            "source": self.source,
            "target": self.target,
            "counts": dict(self.counts),
            "not_in_target": [x.as_dict() for x in self.not_in_target],
            "not_in_source": [x.as_dict() for x in self.not_in_source],
            "notes": list(self.notes),
            "warnings": list(self.warnings),
        }

    def summary_text(self) -> str:
        target = "AWB" if self.direction == "rbds-to-awb" else "RAPTOR"
        source = "RAPTOR" if target == "AWB" else "AWB"
        lines = [
            f"{source} -> {target} interchange summary",
            f"  source: {self.source}",
            f"  target: {self.target}",
            "  converted: " + _counts_text(self.counts),
        ]
        if self.not_in_target:
            lines.append(f"  not represented in {target}:")
            for loss in self.not_in_target:
                lines.append(f"    - {loss.feature}: {loss.count}"
                             + (f" ({loss.detail})" if loss.detail else ""))
        else:
            lines.append(f"  not represented in {target}: none")
        if self.not_in_source:
            other = "AWB" if target == "RAPTOR" else "RAPTOR"
            lines.append(f"  not represented in {other}:")
            for loss in self.not_in_source:
                lines.append(f"    - {loss.feature}: {loss.count}"
                             + (f" ({loss.detail})" if loss.detail else ""))
        for note in self.notes:
            lines.append(f"  note: {note}")
        for warning in self.warnings:
            lines.append(f"  warning: {warning}")
        return "\n".join(lines)

    def summary_markdown(self) -> str:
        target = "AWB" if self.direction == "rbds-to-awb" else "RAPTOR"
        source = "RAPTOR" if target == "AWB" else "AWB"
        lines = [
            f"# {source} -> {target} interchange summary",
            "",
            f"- **Source:** `{self.source}`",
            f"- **Target:** `{self.target}`",
            f"- **Converted:** {_counts_text(self.counts) or 'nothing'}",
            "",
            f"## Not represented in {target}",
            "",
        ]
        if self.not_in_target:
            lines += ["| Feature | Count | Detail |", "|---|---:|---|"]
            for loss in self.not_in_target:
                lines.append(f"| {loss.feature} | {loss.count} | "
                             f"{loss.detail or ''} |")
        else:
            lines.append("None.")
        if self.not_in_source:
            other = "AWB" if target == "RAPTOR" else "RAPTOR"
            lines += ["", f"## Not represented in {other}", "",
                      "| Feature | Count | Detail |", "|---|---:|---|"]
            for loss in self.not_in_source:
                lines.append(f"| {loss.feature} | {loss.count} | "
                             f"{loss.detail or ''} |")
        if self.notes:
            lines += ["", "## Notes", ""]
            lines += [f"- {n}" for n in self.notes]
        if self.warnings:
            lines += ["", "## Warnings", ""]
            lines += [f"- {w}" for w in self.warnings]
        return "\n".join(lines) + "\n"


def _counts_text(counts: dict[str, int]) -> str:
    parts = [f"{n} {name.replace('_', ' ')}"
             for name, n in counts.items() if n]
    return ", ".join(parts) if parts else "0 elements"


# ---------------------------------------------------------------------------
# The AWB table store
# ---------------------------------------------------------------------------

#: Column types in an AWB table record.
_T_STR, _T_INT, _T_DOUBLE, _T_BOOL = 0, 1, 2, 3

_SCHEMA_FILE = "awb_tables.json"


def _load_schema() -> tuple[dict[str, list[tuple[str, int]]], dict[str, dict]]:
    text = resources.files("raptor.core.fileio").joinpath(_SCHEMA_FILE).read_text()
    doc = json.loads(text)
    tables = {name: [(str(c), int(t)) for c, t in cols]
              for name, cols in doc["tables"].items()}
    defaults = {name: dict(row) for name, row in doc.get("defaults", {}).items()}
    return tables, defaults


_SCHEMA, _DEFAULTS = _load_schema()

#: The six tables a minimal AWB RBD project needs.  AWB adds the other
#: tables its modules use when it opens the project (verified with AWB 5.0).
AWB_TABLES = ("Project", "FailureModels", "FailureModelCorrectiveTasks",
              "RbdBlocks", "RbdNodes", "RbdConnections")


def _default_for_type(kind: int) -> Any:
    return {_T_STR: "", _T_INT: 0, _T_DOUBLE: 0.0, _T_BOOL: 0}[kind]


def _row(name: str, **overrides: Any) -> dict[str, Any]:
    """One row with every column at its type default, then *overrides*."""
    defaults = _DEFAULTS.get(name, {})
    row = {col: _default_for_type(kind) for col, kind in _SCHEMA[name]}
    row.update({k: v for k, v in defaults.items()
                if k in row and not isinstance(v, (dict, list))})
    row.update(overrides)
    return row


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise AwbFormatError("truncated integer in AWB table store")
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return value, pos


def _write_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read_string(data: bytes, pos: int) -> tuple[str, int]:
    size, pos = _read_varint(data, pos)
    end = pos + size
    if end > len(data):
        raise AwbFormatError("truncated string in AWB table store")
    return data[pos:end].decode("utf-8", "replace"), end


def _write_string(out: bytearray, text: str) -> None:
    raw = str(text).encode("utf-8")
    out += _write_varint(len(raw))
    out += raw


@dataclass
class AwbTable:
    name: str
    columns: list[tuple[str, int]]
    rows: list[dict[str, Any]] = field(default_factory=list)


def read_awb(path: str | Path) -> dict[str, AwbTable]:  # noqa: C901, PLR0912
    """Read an AWB 5.0 ``.awb`` project into its tables.

    The file starts with a little-endian ``uint32`` table count, then each
    table is: name, uint32 column count, (column name, uint32 type) pairs,
    uint32 row count, and the values in column order.  Strings are a
    varint length followed by UTF-8; ints are int32, doubles are IEEE-754
    little-endian, bools are one byte.
    """
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise AwbFormatError(f"cannot read {path}: {exc}") from exc
    if len(data) < 4:
        raise AwbFormatError("not an AWB project (file is too short)")
    count, pos = struct.unpack_from("<I", data, 0)[0], 4
    tables: dict[str, AwbTable] = {}
    try:
        for _ in range(count):
            name, pos = _read_string(data, pos)
            if not name:
                raise AwbFormatError("empty table name")
            ncols, pos = _read_int32(data, pos)
            if ncols < 0 or ncols > 100_000:
                raise AwbFormatError(f"bad column count in table {name!r}")
            columns: list[tuple[str, int]] = []
            for _ in range(ncols):
                col, pos = _read_string(data, pos)
                kind, pos = _read_int32(data, pos)
                if kind not in (_T_STR, _T_INT, _T_DOUBLE, _T_BOOL):
                    raise AwbFormatError(
                        f"unknown column type {kind} in {name}.{col}")
                columns.append((col, kind))
            nrows, pos = _read_int32(data, pos)
            if nrows < 0 or nrows > 10_000_000:
                raise AwbFormatError(f"bad row count in table {name!r}")
            rows: list[dict[str, Any]] = []
            for _ in range(nrows):
                row: dict[str, Any] = {}
                for col, kind in columns:
                    if kind == _T_STR:
                        row[col], pos = _read_string(data, pos)
                    elif kind == _T_INT:
                        row[col], pos = _read_int32(data, pos)
                    elif kind == _T_DOUBLE:
                        row[col], pos = _read_double(data, pos)
                    else:
                        if pos >= len(data):
                            raise AwbFormatError("truncated bool in AWB table")
                        row[col] = data[pos]
                        pos += 1
                rows.append(row)
            tables[name] = AwbTable(name, columns, rows)
    except (struct.error, IndexError) as exc:
        raise AwbFormatError("truncated AWB table store") from exc
    if pos != len(data):
        # Trailing bytes are harmless in some writers, but a mismatch is
        # useful evidence that we read the file wrongly.
        raise AwbFormatError(
            f"unexpected {len(data) - pos} trailing byte(s) in AWB project")
    if "Project" not in tables:
        raise AwbFormatError("AWB project has no Project table")
    return tables


def _read_int32(data: bytes, pos: int) -> tuple[int, int]:
    if pos + 4 > len(data):
        raise AwbFormatError("truncated int32 in AWB table store")
    return struct.unpack_from("<i", data, pos)[0], pos + 4


def _read_double(data: bytes, pos: int) -> tuple[float, int]:
    if pos + 8 > len(data):
        raise AwbFormatError("truncated double in AWB table store")
    return struct.unpack_from("<d", data, pos)[0], pos + 8


def write_awb(path: str | Path, tables: dict[str, AwbTable]) -> None:
    """Write tables in the AWB 5.0 table-store format."""
    out = bytearray(struct.pack("<I", len(tables)))
    for name, table in tables.items():
        _write_string(out, name)
        out += struct.pack("<I", len(table.columns))
        for col, kind in table.columns:
            _write_string(out, col)
            out += struct.pack("<I", kind)
        out += struct.pack("<I", len(table.rows))
        for row in table.rows:
            for col, kind in table.columns:
                value = row.get(col, _default_for_type(kind))
                if kind == _T_STR:
                    _write_string(out, value)
                elif kind == _T_INT:
                    out += struct.pack("<i", int(value))
                elif kind == _T_DOUBLE:
                    out += struct.pack("<d", float(value))
                else:
                    out.append(int(value) & 0xFF)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(out))


# ---------------------------------------------------------------------------
# Small helpers shared by both directions
# ---------------------------------------------------------------------------

#: AWB RBD object types in a connection.
_AWB_BLOCK, _AWB_REPEAT, _AWB_NODE = 20, 21, 22

#: AWB failure-model distribution codes (AWB 5.0).
_AWB_EXPONENTIAL = 0
_AWB_WEIBULL = 1
_AWB_BI_WEIBULL = 2
_AWB_TRI_WEIBULL = 3
_AWB_FIXED = 4
_AWB_LOGNORMAL = 5
_AWB_NORMAL = 6
_AWB_BUFFER = 7
_AWB_PHASED_BI = 8
_AWB_PHASED_TRI = 9

#: RAPTOR distribution codes (raptor.core.fileio.rbdlib).
_R_EXPO, _R_LOGNORM, _R_NORM, _R_WEIBULL, _R_NONE, _R_FIXED = 4, 7, 8, 15, 18, 19

#: An essentially infinite MTTF, used for a block AWB says never fails.
_NEVER = 9.0e8


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(out) or math.isinf(out) else out


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _text(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _hours_factor(units: str | None) -> float:
    return HOURS_PER_UNIT.get((units or "hours").strip().lower(), 1.0)


def _safe_id(text: str, limit: int = 40) -> str:
    """AWB IDs are short alphanumeric labels; make one without changing the
    readable part where possible."""
    cleaned = "".join(ch if ch.isalnum() or ch in "_-." else "_"
                      for ch in (text or "item")).strip("_") or "item"
    return cleaned[:limit]


def _unique(base: str, used: set[str], limit: int = 40) -> str:
    candidate = _safe_id(base, limit)
    if candidate not in used:
        used.add(candidate)
        return candidate
    stem = candidate[: max(1, limit - 4)]
    n = 2
    while f"{stem}_{n}" in used:
        n += 1
    candidate = f"{stem}_{n}"[:limit]
    used.add(candidate)
    return candidate


def _guid() -> str:
    return str(uuid.uuid4())


def _dist_mean(code: int, params: Iterable[Any]) -> float | None:
    from raptor.api.distributions import describe_dist
    try:
        info = describe_dist(code, [float(x) for x in list(params)[:3]])
    except Exception:  # noqa: BLE001 - a report must never hide the model
        return None
    mean = info.get("mean")
    return None if mean is None else float(mean)


def _rows(tables: dict[str, AwbTable], name: str) -> list[dict[str, Any]]:
    table = tables.get(name)
    return [] if table is None else table.rows


def _has_rows(tables: dict[str, AwbTable], name: str) -> int:
    return len(_rows(tables, name))


# ---------------------------------------------------------------------------
# RAPTOR -> AWB
# ---------------------------------------------------------------------------


def _system_row(rbd: RbdFile) -> dict[str, Any]:
    sec = rbd.sections.get("SYSTEM_DATA")
    return sec.rows[0] if sec and sec.rows else {}


def _model_units(rbd: RbdFile) -> str:
    return _text(_system_row(rbd).get("units"), "hours")


def _model_trials(rbd: RbdFile) -> int:
    exp = getattr(rbd, "experiment", None) or {}
    if exp.get("trials") is not None:
        return max(1, _int(exp["trials"], 1))
    return max(1, _int(_system_row(rbd).get("NumRuns"), 1))


def _model_lifetime_hours(rbd: RbdFile) -> tuple[float, bool]:
    """(lifetime in hours, was a time-terminated model)."""
    exp = getattr(rbd, "experiment", None) or {}
    units = _model_units(rbd)
    factor = _hours_factor(units)
    if exp.get("term_condition") is not None:
        return max(1.0, _num(exp["term_condition"]) * factor), \
            str(exp.get("termination_type", "TimeSim")) == "TimeSim"
    row = _system_row(rbd)
    return max(1.0, _num(row.get("TermCondition"), 8760.0) * factor), \
        _text(row.get("TerminationType"), "TimeSim") == "TimeSim"


@dataclass
class _Page:
    """One AWB page: where its superblock is drawn and what it owns."""

    page: int
    parent: int
    name: str
    raptor_id: str = ""
    parent_id: str = ""


def _hierarchy_depth(rbd: RbdFile) -> dict[str, int]:
    rows = rbd.sections.get("HIERARCHY_DATA")
    if not rows:
        return {}
    by_id = {_text(r.get("ID")): r for r in rows.rows}
    depth: dict[str, int] = {}

    def walk(hid: str, stack: tuple[str, ...] = ()) -> int:
        if hid in depth:
            return depth[hid]
        if hid in stack:
            depth[hid] = 0
            return 0
        parent = _text(by_id.get(hid, {}).get("Parent"), "0")
        d = 0 if parent in ("", "0") or parent not in by_id else walk(parent, (*stack, hid)) + 1
        depth[hid] = d
        return d

    for hid in by_id:
        walk(hid)
    return depth


def export_awb(rbd: RbdFile, path: str | Path, *,  # noqa: C901, PLR0912, PLR0915
               source: str = "", write: bool = True) -> AwbReport:
    """Write *rbd* as an AWB 5.0 project and return the loss report.

    ``write=False`` builds the same report and tables without touching the
    destination, so a strict conversion can refuse before anything exists
    on disk.
    """
    src = source or getattr(rbd, "file_name", "") or "model"
    dest = str(path)
    report = AwbReport("rbds-to-awb", src, dest)

    units = _model_units(rbd)
    factor = _hours_factor(units)
    if units.lower() != "hours":
        report.notes.append(
            f"times converted from {units} to hours for AWB "
            f"(factor {factor:g})")

    blocks = [r for r in _section_list(rbd, "BLOCK_DATA")
              if _text(r.get("ID")) not in ("", "0")]
    nodes = [r for r in _section_list(rbd, "NODE_DATA")
             if _text(r.get("ID")) not in ("", "0")]
    hierarchies = [r for r in _section_list(rbd, "HIERARCHY_DATA")
                   if _text(r.get("ID")) not in ("", "0")]
    links = _section_list(rbd, "LINK_DATA")
    events = [r for r in _section_list(rbd, "EVENT_DATA")
              if _text(r.get("ID")) not in ("", "0")]

    report.counts.update(blocks=len(blocks), nodes=len(nodes),
                         hierarchies=len(hierarchies), links=len(links),
                         events=len(events))
    if not blocks:
        raise ValueError("an AWB project needs at least one RBD block")

    # ---- pages and row indices -------------------------------------------
    # AWB's page index is the RbdBlocks row index of the superblock that owns
    # it: row 0 is the project root and owns page 0; a hierarchy's row owns
    # the page its contents are drawn on.
    used_ids: set[str] = {"1"}     # the root superblock's own Id
    hrows: dict[str, dict[str, Any]] = {}
    hpages: dict[str, _Page] = {}
    ordered_h = sorted(hierarchies, key=lambda r: (_hierarchy_depth(rbd).get(
        _text(r.get("ID")), _int(r.get("level"), 1)), _text(r.get("ID"))))
    next_row = 1
    for h in ordered_h:
        hid = _text(h.get("ID"))
        parent_id = _text(h.get("Parent"), "0")
        hpage = _Page(next_row, 0, _text(h.get("hierarchyName"), hid), hid,
                      parent_id)
        hpages[hid] = hpage
        hrows[hid] = _row(
            "RbdBlocks", Id=_unique(_text(h.get("hierarchyName"), hid), used_ids),
            Description=_text(h.get("hierarchyName"), hid), Super=1,
            FailureModel=-1, Consequence=-1, Bom=-1, Bitmap=-1,
            Page=0, XPosition=_num(h.get("xPos")),
            YPosition=_num(h.get("yPos")), Width=100.0, Height=60.0,
            PageScale=1.0, Guid=_guid(), NonMaintainable=0, NonOperational=0,
            IncludeInSystemPlot=1, MaxCapacity=0.0, MaxCapacity2=0.0, MaxCapacity3=0.0,
            MaxCapacity4=0.0, LoadFactor=1.0)
        next_row += 1

    def page_for_parent(parent_id: str) -> int:
        return 0 if parent_id in ("", "0") else hpages.get(parent_id, _Page(0, 0, "")).page

    # ---- failure models ---------------------------------------------------
    fm_by_key: dict[tuple, int] = {}
    fm_rows: list[dict[str, Any]] = []
    task_rows: list[dict[str, Any]] = []
    dist_simplified: dict[str, int] = {}
    repair_simplified: dict[str, int] = {}
    no_repair = 0
    no_failure = 0

    def failure_model(block: dict[str, Any]) -> int:  # noqa: C901, PLR0912, PLR0915
        nonlocal no_repair, no_failure
        awb_std = 0.0
        fdist = _int(block.get("FDist"), _R_EXPO)
        fparams = [_num(block.get("FParam1")), _num(block.get("FParam2")),
                   _num(block.get("FParam3"))]
        rdist = _int(block.get("RDist"), _R_FIXED)
        rparams = [_num(block.get("RParam1")), _num(block.get("RParam2")),
                   _num(block.get("RParam3"))]
        key = (fdist, tuple(round(x, 9) for x in fparams), rdist,
               tuple(round(x, 9) for x in rparams))
        if key in fm_by_key:
            return fm_by_key[key]
        fm_i = len(fm_rows)
        fm_by_key[key] = fm_i
        name = _text(block.get("blockName"), "block")
        fm_id = _unique(f"{name}_FM", used_ids)
        awb_dist = _AWB_EXPONENTIAL
        mttf = 8760.0
        eta, beta, gamma = 8760.0, 1.0, 0.0
        if fdist == _R_EXPO:
            mttf = max(1e-9, fparams[0])
            if fparams[1] not in (0.0,):
                dist_simplified["exponential location ignored"] = \
                    dist_simplified.get("exponential location ignored", 0) + 1
        elif fdist == _R_WEIBULL:
            awb_dist = _AWB_WEIBULL
            beta, eta, gamma = fparams[0], fparams[1], fparams[2]
            mttf = eta
        elif fdist == _R_LOGNORM:
            awb_dist = _AWB_LOGNORMAL
            mttf, awb_std = max(1e-9, fparams[0]), max(0.0, fparams[1])
        elif fdist == _R_NORM:
            awb_dist = _AWB_NORMAL
            mttf, awb_std = max(1e-9, fparams[0]), max(0.0, fparams[1])
        elif fdist == _R_FIXED:
            awb_dist = _AWB_FIXED
            mttf = max(1e-9, fparams[0])
        elif fdist == _R_NONE:
            awb_dist = _AWB_FIXED
            mttf = _NEVER
            no_failure += 1
        else:
            mean = _dist_mean(fdist, fparams)
            if mean is not None and mean > 0:
                mttf = mean
            label = _raptor_dist_name(fdist)
            dist_simplified[label] = dist_simplified.get(label, 0) + 1
        # AWB holds all times in hours.
        mttf *= factor
        eta *= factor
        gamma *= factor
        row = _row(
            "FailureModels", Id=fm_id,
            Description=f"Shared failure model for {name}" if not fm_rows else name,
            Type=-1, DataLinkCause=-1, FmMttf=mttf,
            FmDistribution=awb_dist, FmWeibullSet=-1,
            FmStd=awb_std, FmEta1=eta, FmBeta1=beta, FmGamma1=gamma,
            FmEta2=8760.0 * factor, FmBeta2=1.0, FmGamma2=0.0,
            FmEta3=8760.0 * factor, FmBeta3=1.0, FmGamma3=0.0,
            StandbyFailurePercent=50.0, StandbyAgeingPercent=50.0,
            StartUpFailureProb=0.0, FmDormant=0, Guid=_guid())
        fm_rows.append(row)

        # Corrective task: AWB's task duration is a fixed duration in the
        # sample projects (Distribution=2, Std=0).  RAPTOR's mean is the
        # honest equivalent when its repair is a distribution.
        if rdist == _R_NONE:
            task_duration = 0.0
            no_repair += 1
        elif rdist == _R_FIXED:
            task_duration = max(0.0, rparams[0])
        else:
            mean = _dist_mean(rdist, rparams)
            task_duration = max(0.0, mean if mean is not None else rparams[0])
            label = _raptor_dist_name(rdist)
            repair_simplified[label] = repair_simplified.get(label, 0) + 1
        task_rows.append(_row(
            "FailureModelCorrectiveTasks", FailureModel=fm_i,
            Description=f"Corrective repair for {fm_id}",
            TaskId=_unique(f"CM{fm_i + 1}", used_ids),
            TaskDuration=task_duration * factor, RampTime=0.0,
            Std=0.0, Eta=24.0, Beta=2.0, Gamma=0.0,
            WeibullSet=-1, Distribution=2, OperationalCost=0.0))
        return fm_i

    # ---- blocks and nodes -------------------------------------------------
    # AWB block rows: root first, then hierarchy superblocks, then ordinary
    # blocks.  AWB finds a page's contents by the row index of its superblock,
    # so the hierarchy rows must come before the ordinary blocks they own.
    block_rows: list[dict[str, Any]] = []
    block_index: dict[str, int] = {}
    block_page: dict[str, int] = {}
    root = _row("RbdBlocks", Id="1", Description=_text(rbd.file_name, "RAPTOR model"),
                Super=1, Page=-1, FailureModel=-1, Consequence=-1, Bom=-1,
                Bitmap=-1, XPosition=0.0, YPosition=0.0, Width=100.0,
                Height=60.0, PageScale=1.0, Guid=_guid(), NonMaintainable=0,
                NonOperational=0, IncludeInSystemPlot=1,
                MaxCapacity=0.0, MaxCapacity2=0.0, MaxCapacity3=0.0,
                MaxCapacity4=0.0, LoadFactor=1.0)
    block_rows.append(root)
    block_rows.extend(hrows[hid] for hid in hpages)
    # hrows keys are in insertion order of hpages; keep them aligned.

    # The row index of each hierarchy is its page; recompute from block_rows
    # so a later change cannot silently desynchronise the two.
    row_of_hier: dict[str, int] = {}
    for i, row in enumerate(block_rows):
        for hid, hrow in hrows.items():
            if row is hrow:
                row_of_hier[hid] = i
    for hid, hpage in hpages.items():
        hpage.page = row_of_hier[hid]
        hpage.parent = (0 if hpage.parent_id in ("", "0")
                        else hpages[hpage.parent_id].page)
        hpages[hid] = hpage
        hrows[hid]["Page"] = hpage.parent

    for b in blocks:
        bid = _text(b.get("ID"))
        parent = _text(b.get("ParentId"), "0")
        page = page_for_parent(parent)
        fm_i = failure_model(b)
        idx = len(block_rows)
        block_index[bid] = idx
        block_page[bid] = page
        nonmaint = 1 if _int(b.get("RDist"), _R_FIXED) == _R_NONE else 0
        block_rows.append(_row(
            "RbdBlocks", Id=_unique(_text(b.get("blockName"), f"B{bid}"), used_ids),
            Description=_text(b.get("blockName"), f"Block {bid}"),
            Super=0, FailureModel=fm_i, Consequence=-1, Bom=-1, Bitmap=-1,
            Page=page, XPosition=_num(b.get("xPos")), YPosition=_num(b.get("yPos")),
            Width=100.0, Height=60.0, PageScale=1.0, Guid=_guid(),
            NonMaintainable=nonmaint, NonOperational=0,
            IncludeInSystemPlot=1, MaxCapacity=0.0, MaxCapacity2=0.0, MaxCapacity3=0.0,
            MaxCapacity4=0.0, LoadFactor=1.0,
            LogicMode=0, StandbyMode=0))

    # AWB nodes: vote count carries RAPTOR's k-of-n requirement.  AWB has
    # no separate marker types, so start/end/in/out all become ordinary nodes.
    node_rows: list[dict[str, Any]] = []
    node_index: dict[str, int] = {}
    node_page: dict[str, int] = {}
    for n in nodes:
        nid = _text(n.get("ID"))
        parent = _text(n.get("parent"), "0")
        page = page_for_parent(parent)
        idx = len(node_rows)
        node_index[nid] = idx
        node_page[nid] = page
        vote = _int(n.get("k"), 1)
        node_rows.append(_row(
            "RbdNodes", Id=_unique(_text(n.get("Name"), f"N{nid}"), used_ids),
            Page=page, Vote=max(1, vote), XPosition=_num(n.get("xPos")),
            YPosition=_num(n.get("yPos")), OperationalCapacityTarget=0.0,
            LocalStandby=0, NotLogic=0, BackgroundColor=-1, Guid=_guid()))

    # ---- connections ------------------------------------------------------
    conn_rows: list[dict[str, Any]] = []
    conn_id: set[str] = set()
    skipped_links = 0
    for link in links:
        ftype = _text(link.get("FromType"))
        ttype = _text(link.get("ToType"))
        fid = _text(link.get("FromID"))
        tid = _text(link.get("ToID"))
        if ftype == "Event" or ttype == "Event":
            skipped_links += 1
            continue
        if ftype == "Block":
            if fid not in block_index:
                skipped_links += 1
                continue
            fobj, findex, fpage = _AWB_BLOCK, block_index[fid], block_page[fid]
        elif ftype == "Hier":
            if fid not in row_of_hier:
                skipped_links += 1
                continue
            fobj, findex = _AWB_BLOCK, row_of_hier[fid]
            fpage = hpages[fid].parent
        elif ftype == "Node":
            if fid not in node_index:
                skipped_links += 1
                continue
            fobj, findex, fpage = _AWB_NODE, node_index[fid], node_page[fid]
        else:
            skipped_links += 1
            continue
        if ttype == "Block":
            if tid not in block_index:
                skipped_links += 1
                continue
            tobj, tindex, tpage = _AWB_BLOCK, block_index[tid], block_page[tid]
        elif ttype == "Hier":
            if tid not in row_of_hier:
                skipped_links += 1
                continue
            tobj, tindex = _AWB_BLOCK, row_of_hier[tid]
            tpage = hpages[tid].parent
        elif ttype == "Node":
            if tid not in node_index:
                skipped_links += 1
                continue
            tobj, tindex, tpage = _AWB_NODE, node_index[tid], node_page[tid]
        else:
            skipped_links += 1
            continue
        page = fpage if fpage == tpage else (fpage if ttype in ("Block", "Hier") else tpage)
        conn_rows.append(_row(
            "RbdConnections", Id=_unique(f"C{len(conn_rows) + 1}", conn_id),
            Page=page, Type=0, Color=-1, InputObjectType=fobj,
            InputObjectIndex=findex, OutputObjectType=tobj,
            OutputObjectIndex=tindex, Guid=_guid()))

    # ---- project ----------------------------------------------------------
    lifetime, time_terminated = _model_lifetime_hours(rbd)
    trials = _model_trials(rbd)
    project = _row(
        "Project", Id="<PROJECTID>", Description=_text(rbd.file_name, "RAPTOR model"),
        FileName=dest, Notes=f"Created by RAPTOR Studio from {src}",
        ProductVersion="5.0", ProjectVersionLong="5.0.0.0",
        LifeTime=lifetime, LastModule=1, AvsimCurrentRbdPage=0,
        AvsimNoSimulationsRequested=trials, AvsimNoSimulationsCompleted=0,
        AvsimCapacityCalcsPerformed=0, AvsimShowCapacity=0,
        AvsimEquateCapacity=0, AvsimEquateCapacityUnit="",
        AvsimEquateCapacityValue=1.0, AvsimEquateCapacityUnit2="",
        AvsimEquateCapacityValue2=1.0, AvsimEquateCapacityUnit3="",
        AvsimEquateCapacityValue3=1.0, AvsimEquateCapacityUnit4="",
        AvsimEquateCapacityValue4=1.0,
        WeibullCurrentWeibullSet=-1, ProcessCurrentProcessSet=-1,
        ALTCurrentALTSet=-1, Guid=_guid())

    tables: dict[str, AwbTable] = {
        "Project": AwbTable("Project", _SCHEMA["Project"], [project]),
        "FailureModels": AwbTable("FailureModels", _SCHEMA["FailureModels"], fm_rows),
        "FailureModelCorrectiveTasks": AwbTable(
            "FailureModelCorrectiveTasks", _SCHEMA["FailureModelCorrectiveTasks"],
            task_rows),
        "RbdBlocks": AwbTable("RbdBlocks", _SCHEMA["RbdBlocks"], block_rows),
        "RbdNodes": AwbTable("RbdNodes", _SCHEMA["RbdNodes"], node_rows),
        "RbdConnections": AwbTable("RbdConnections", _SCHEMA["RbdConnections"],
                                   conn_rows),
    }
    if write:
        write_awb(dest, tables)

    report.counts.update(blocks=len(blocks), nodes=len(nodes),
                         hierarchies=len(hierarchies), links=len(conn_rows),
                         failure_models=len(fm_rows))
    if skipped_links:
        report.warnings.append(
            f"{skipped_links} link(s) could not be exported (an endpoint was "
            "missing or was an event)")
    if not time_terminated:
        report.notes.append(
            "AWB runs for a fixed lifetime, so a run stopped by failures or "
            f"cycles was written with a {lifetime / 8760:g}-year lifetime")
    if dist_simplified:
        for label, count in sorted(dist_simplified.items()):
            report.add("failure distribution simplified to exponential",
                       count, f"{label} -> exponential with its mean MTTF")
    if repair_simplified:
        for label, count in sorted(repair_simplified.items()):
            report.add("repair distribution simplified to a fixed duration",
                       count, f"{label} -> AWB task duration = its mean")
    if no_repair:
        report.add("no-repair blocks", no_repair,
                   "exported with NonMaintainable=1 and a zero-duration task")
    if no_failure:
        report.add("no-failure blocks", no_failure,
                   "exported with a fixed time of 9e8 hours (never fails)")
    _report_raptor_features(rbd, report, blocks, nodes, links, events,
                            hierarchies, units, factor)
    return report


def _report_raptor_features(rbd: RbdFile, report: AwbReport,  # noqa: C901, PLR0912, PLR0915
                            blocks: list[dict[str, Any]],
                            nodes: list[dict[str, Any]],
                            links: list[dict[str, Any]],
                            events: list[dict[str, Any]],
                            hierarchies: list[dict[str, Any]],
                            units: str, factor: float) -> None:
    """Add the RAPTOR features AWB has no equivalent for."""
    if events:
        report.add("failure events", len(events),
                   "AWB RBDs use blocks; events are not exported")
    if getattr(rbd, "annotations", None):
        report.add("diagram annotations", len(rbd.annotations),
                   "text boxes, rectangles and arrows are not exported")
    if getattr(rbd, "rates", None):
        report.add("rate definitions", len(rbd.rates),
                   "values are exported as plain numbers")
    if getattr(rbd, "formulas", None):
        report.add("formula-linked fields", len(rbd.formulas),
                   "values are exported as plain numbers")
    if getattr(rbd, "triggers", None) or _section_rows(rbd.sections, "TRIGGERS"):
        report.add("triggers", max(len(getattr(rbd, "triggers", []) or []),
                                   _section_rows(rbd.sections, "TRIGGERS")),
                   "AWB has no equivalent of RAPTOR's trigger logic")
    if getattr(rbd, "spare_pools", None) or _section_rows(rbd.sections, "SPARE_POOL_DATA"):
        report.add("spare pools", max(len(getattr(rbd, "spare_pools", []) or []),
                                      _section_rows(rbd.sections, "SPARE_POOL_DATA")),
                   "AWB spares are not linked to blocks by this export")
    if getattr(rbd, "resources", None) or _section_rows(rbd.sections, "RESOURCE_DATA"):
        report.add("resource pools", max(len(getattr(rbd, "resources", []) or []),
                                         _section_rows(rbd.sections, "RESOURCE_DATA")),
                   "AWB labour and equipment resources are not populated")
    phases = _section_rows(rbd.sections, "PHASES")
    if phases:
        report.add("mission phases", phases,
                   "AWB phase rules are not populated")
    if getattr(rbd, "out_of_service", None):
        report.add("out-of-service elements", len(rbd.out_of_service),
                   "AWB's OutOfService rules are not populated")
    analyses = [str(a).lower() for a in
                ((getattr(rbd, "experiment", None) or {}).get("analyses") or [])]
    capacity = _text(_system_row(rbd).get("capacityAnalysis"))
    flow_links = sum(1 for link in links
                     if _int(link.get("nomFlow"), -2) not in (-2, -1)
                     or _int(link.get("MaxFlow"), -1) not in (-1, 0))
    if "capacity" in analyses or capacity not in ("", "0", "None") or flow_links:
        report.add("capacity analysis", flow_links or 1,
                   "AWB capacity flow and capacity targets are not populated")
    dep = sum(1 for b in blocks if _text(b.get("DepType"), "None") not in ("None", ""))
    if dep:
        report.add("block dependencies", dep,
                   "AWB dependency rules are not populated")
    pm = sum(1 for b in blocks if _int(b.get("usesPM"), 0))
    if pm:
        report.add("preventive maintenance", pm,
                   "AWB scheduled tasks are not populated")
    phasing = sum(1 for b in blocks if _int(b.get("Phasing"), 0))
    phasing += sum(1 for n in nodes if _int(n.get("Phases"), 0))
    phasing += sum(1 for h in hierarchies if _int(h.get("Phs"), 0))
    if phasing:
        report.add("phased element schedules", phasing,
                   "per-element phase status is not exported")
    aged = sum(1 for b in blocks
               if _num(b.get("_DutyCycle"), 1.0) != 1.0
               or _num(b.get("_StartAge"), 0.0) != 0.0
               or _num(b.get("_RestoreFactor"), 0.0) != 0.0
               or _int(b.get("_RestoreType"), 0) != 0
               or _int(b.get("GDType"), 0) != 0
               or _num(b.get("SBStress"), 0.0) != 0.0)
    if aged:
        report.add("age, restoration and growth settings", aged,
                   "AWB initial age is 0 and restoration/growth are not set")
    # RAPTOR's own default is 1.0 in every cost column; only a value that
    # differs from both "unset" and that default is real data.
    costs = sum(1 for b in blocks
                if any(_num(b.get(col), 0.0) not in (0.0, 1.0) for col in
                       ("initial", "Running", "standby", "idle", "repHold",
                        "repPerTime", "repFixed", "donePerTime", "doneFixed",
                        "pmHoldCost", "pmCost", "pmFixedCost", "perSpare",
                        "emerShipping")))
    if costs:
        report.add("block cost data", costs,
                   "AWB cost fields are not populated")
    standby = sum(1 for n in nodes if _int(n.get("Cold"), 0) or _int(n.get("PR"), 0))
    if standby:
        report.add("node standby/priority settings", standby,
                   "AWB nodes have only a vote count")
    link_priority = sum(1 for link in links
                        if _int(link.get("ColdPri"), 50) != 50
                        or _num(link.get("AProb"), 1.0) != 1.0
                        or _num(link.get("AutoTime"), 0.0) != 0.0
                        or _num(link.get("ManTime"), 0.0) != 0.0
                        or _int(link.get("CapPri"), 50) != 50)
    if link_priority:
        report.add("link switching and priority", link_priority,
                   "AWB connections carry no switching data")
    if getattr(rbd, "link_grips", None):
        report.add("link routing grips", len(rbd.link_grips),
                   "AWB lays connections out itself")
    if getattr(rbd, "view", None) or getattr(rbd, "view_transform", None):
        report.add("view settings", 1, "AWB uses its own diagram view")
    if getattr(rbd, "advisor", None):
        report.add("advisor state", len(getattr(rbd, "advisor", {}).get("ignored", [])),
                   "ignored findings are RAPTOR-only")
    if getattr(rbd, "project_meta", None):
        report.add("print title block", 1, "AWB stores its own project notes")
    if getattr(rbd, "display_unit", None):
        report.add("display unit override", 1,
                   "AWB always shows the model in hours")
    if units.lower() != "hours":
        report.notes.append(
            f"AWB stores times in hours; RAPTOR's {units} were converted "
            f"with factor {factor:g}")


def _raptor_dist_name(code: int) -> str:
    try:
        from raptor.api.distributions import dist_name
        return dist_name(code)
    except Exception:  # noqa: BLE001
        return f"distribution {code}"


def _section_rows(sections: dict[str, RbdSection], name: str) -> int:
    sec = sections.get(name)
    return 0 if sec is None else len(sec.rows)


def _section_list(rbd: RbdFile, name: str) -> list[dict[str, Any]]:
    sec = rbd.sections.get(name)
    return [] if sec is None else sec.rows


# ---------------------------------------------------------------------------
# AWB -> RAPTOR
# ---------------------------------------------------------------------------


def import_awb(path: str | Path, *,  # noqa: C901, PLR0912, PLR0915
               source: str = "") -> tuple[RbdFile, AwbReport]:
    """Read an AWB project and return a RAPTOR model plus the loss report."""
    src = source or str(path)
    tables = read_awb(path)
    report = AwbReport("awb-to-rbds", src, "RAPTOR model")
    _check_import_features(tables, report)

    blocks = _rows(tables, "RbdBlocks")
    nodes = _rows(tables, "RbdNodes")
    conns = _rows(tables, "RbdConnections")
    fms = _rows(tables, "FailureModels")
    tasks = _rows(tables, "FailureModelCorrectiveTasks")
    repeats = _rows(tables, "RbdRepeatBlocks")
    labels = _rows(tables, "RbdLabels")

    # AWB page index: 0 is the top level; a superblock's row index owns the
    # page its contents are drawn on.  Row 0 is the project root (Page -1).
    root_i = next((i for i, b in enumerate(blocks)
                   if _int(b.get("Page"), 0) == -1 and _int(b.get("Super"), 0) == 1),
                  None)
    page_owner: dict[int, str] = {0: "0"}
    hierarchy_page: dict[str, int] = {}
    super_rows: dict[int, dict[str, Any]] = {}
    for i, b in enumerate(blocks):
        if i == root_i:
            continue
        if _int(b.get("Super"), 0) == 1:
            super_rows[i] = b
            hierarchy_page[_text(b.get("Id"), f"H{i}")] = i
            page_owner[i] = _text(b.get("Id"), f"H{i}")
    # A non-superblock page owner (AWB permits nodes/blocks as page roots in
    # some versions); keep the top-level owner for it.
    for b in blocks:
        page_owner.setdefault(_int(b.get("Page"), 0), "0")

    # ---- names and ids ----------------------------------------------------
    used_block_names: set[str] = set()
    used_hier_names: set[str] = set()
    used_node_names: set[str] = set()
    block_id: dict[int, int] = {}
    repeat_id: dict[int, int] = {}
    node_id: dict[int, int] = {}
    hier_id: dict[int, int] = {}
    elements: dict[str, list[dict[str, Any]]] = {
        "blocks": [], "nodes": [], "hierarchies": []}
    comments: list[dict[str, Any]] = []
    next_block = next_node = next_hier = 1
    x_by_page: dict[int, list[float]] = {}

    def block_name(text: str) -> str:
        base = _safe_id(text, 20)
        return _unique(base, used_block_names, 20)

    def node_name(text: str) -> str:
        base = _safe_id(text, 20)
        return _unique(base, used_node_names, 20)

    # ---- hierarchies first (parents before children) ----------------------
    # AWB's page owner is the superblock's row index; the hierarchy tree is
    # implicit in the Page column.  Emit a hierarchy per superblock row, then
    # set each hierarchy's RAPTOR parent from the page it is drawn on.
    ordered_super = sorted(super_rows.items(),
                           key=lambda kv: _super_depth(kv[0], blocks))
    for i, b in ordered_super:
        hid = next_hier
        next_hier += 1
        hier_id[i] = hid
        name = _unique(_safe_id(_text(b.get("Id"), f"H{i}")), used_hier_names, 20)
        elements["hierarchies"].append({
            "id": hid, "name": name, "parent": 0,
            "x": _num(b.get("XPosition")), "y": _num(b.get("YPosition")),
            "in_id": 0, "out_id": 0,
        })
        comments.append({"type": "Hierarchy", "id": hid, "name": name,
                         "text": _comment_text(b)})
    for i, b in ordered_super:
        parent_page = _int(b.get("Page"), 0)
        owner_row = _page_owner_row(parent_page, page_owner, hierarchy_page)
        parent = 0 if owner_row == 0 else hier_id.get(owner_row, 0)
        target = next(h for h in elements["hierarchies"] if h["id"] == hier_id[i])
        target["parent"] = parent

    # ---- ordinary blocks --------------------------------------------------
    block_losses: dict[str, int] = {}
    for i, b in enumerate(blocks):
        if i == root_i or i in super_rows:
            continue
        if _text(b.get("Id")) == "" and _int(b.get("FailureModel"), -1) == -1:
            continue
        bid = next_block
        next_block += 1
        block_id[i] = bid
        page = _int(b.get("Page"), 0)
        owner_row = _page_owner_row(page, page_owner, hierarchy_page)
        parent = 0 if owner_row == 0 else hier_id.get(owner_row, 0)
        name = block_name(_text(b.get("Id"), f"B{i}"))
        fdist, fparams, rdist, rparams = _block_data(
            b, fms, tasks, block_losses)
        x, y = _num(b.get("XPosition")), _num(b.get("YPosition"))
        x_by_page.setdefault(page, []).append(x)
        elements["blocks"].append({
            "id": bid, "name": name, "parent": parent, "x": x, "y": y,
            "fail_dist": fdist, "fail_params": fparams,
            "repair_dist": rdist, "repair_params": rparams,
        })
        comments.append({"type": "Block", "id": bid, "name": name,
                         "text": _comment_text(b)})
    for feature, count in sorted(block_losses.items()):
        detail = {
            "multi-mode or buffer failure distribution":
                "AWB multi-mode/buffer laws kept their first Weibull mode only",
            "dormant failures":
                "a dormant AWB failure is modelled as never repaired",
            "start-up failure probability":
                "AWB start-up failures are not modelled by RAPTOR",
            "non-maintainable blocks":
                "AWB marks the block non-maintainable; RAPTOR repair is off",
        }.get(feature, "")
        report.add(feature, count, detail)

    # ---- repeat blocks ----------------------------------------------------
    for i, r in enumerate(repeats):
        ref = _int(r.get("ReferenceBlock"), -1)
        if ref not in block_id:
            report.warnings.append(
                f"repeat block {i} refers to block {ref}, which is not an "
                "ordinary block; it was skipped")
            continue
        bid = next_block
        next_block += 1
        repeat_id[i] = bid
        page = _int(r.get("Page"), 0)
        owner_row = _page_owner_row(page, page_owner, hierarchy_page)
        parent = 0 if owner_row == 0 else hier_id.get(owner_row, 0)
        name = block_name(_text(r.get("Id"), f"R{i}"))
        elements["blocks"].append({
            "id": bid, "name": name, "parent": parent,
            "x": _num(r.get("XPosition")), "y": _num(r.get("YPosition")),
            "fail_dist": _R_FIXED, "fail_params": [_NEVER, 0.0, 0.0],
            "repair_dist": _R_FIXED,
            "repair_params": [_num(_system_row_from_tables(tables)
                                   .get("LifeTime"), 87600.0), 0.0, 0.0],
            "dep_type": "RBDBlock", "dep_num": block_id[ref],
            "dep_nothing": 0.0, "dep_idle": 100.0, "dep_pm": 0.0,
            "dep_fail": 0.0,
        })
        comments.append({"type": "Block", "id": bid, "name": name,
                         "text": "AWB repeat block: same physical item as "
                                 f"block {block_id[ref]}; never fails and is "
                                 "idle whenever that block is down."})

    # ---- nodes ------------------------------------------------------------
    # AWB's nodes carry only a vote count.  Start/end/in/out are inferred from
    # the connections: no incoming = start/in, no outgoing = end/out.
    incoming: dict[int, int] = {}
    outgoing: dict[int, int] = {}
    for c in conns:
        if _int(c.get("InputObjectType"), _AWB_BLOCK) == _AWB_NODE:
            outgoing[_int(c.get("InputObjectIndex"), 0)] = \
                outgoing.get(_int(c.get("InputObjectIndex"), 0), 0) + 1
        if _int(c.get("OutputObjectType"), _AWB_NODE) == _AWB_NODE:
            incoming[_int(c.get("OutputObjectIndex"), 0)] = \
                incoming.get(_int(c.get("OutputObjectIndex"), 0), 0) + 1
    for i, n in enumerate(nodes):
        nid = next_node
        next_node += 1
        node_id[i] = nid
        page = _int(n.get("Page"), 0)
        owner_row = _page_owner_row(page, page_owner, hierarchy_page)
        parent = 0 if owner_row == 0 else hier_id.get(owner_row, 0)
        vote = _int(n.get("Vote"), 1)
        if vote > 1:
            ntype, k = 2, vote
        elif page == 0:
            ntype = 1 if incoming.get(i, 0) == 0 else (
                3 if outgoing.get(i, 0) == 0 else 2)
            k = -1 if ntype in (1, 3) else 1
        else:
            ntype = 4 if incoming.get(i, 0) == 0 else (
                5 if outgoing.get(i, 0) == 0 else 2)
            k = -1 if ntype in (4, 5) else 1
        name = node_name(_text(n.get("Id"), f"N{i}"))
        elements["nodes"].append({
            "id": nid, "name": name, "type": ntype, "parent": parent,
            "x": _num(n.get("XPosition")), "y": _num(n.get("YPosition")),
            "k": k,
        })
    # Bind each hierarchy to its input and output nodes and create them when
    # the AWB page has none (common: AWB superblocks have no explicit nodes).
    for i, _b in ordered_super:
        hid = hier_id[i]
        content_page = i
        page_nodes = [n for n in elements["nodes"]
                      if _node_page(n, nodes, node_id) == content_page]
        ins = [n["id"] for n in page_nodes if n["type"] == 4]
        outs = [n["id"] for n in page_nodes if n["type"] == 5]
        if not ins:
            nid = next_node; next_node += 1
            x = min(x_by_page.get(content_page, [-100.0])) - 120.0
            elements["nodes"].append({"id": nid, "name": node_name("in"),
                                      "type": 4, "parent": hid, "x": x,
                                      "y": 0.0, "k": -1})
            ins = [nid]
        if not outs:
            nid = next_node; next_node += 1
            x = max(x_by_page.get(content_page, [400.0])) + 120.0
            elements["nodes"].append({"id": nid, "name": node_name("out"),
                                      "type": 5, "parent": hid, "x": x,
                                      "y": 0.0, "k": -1})
            outs = [nid]
        h = next(h for h in elements["hierarchies"] if h["id"] == hid)
        h["in_id"], h["out_id"] = ins[0], outs[0]

    # Top level needs a start and an end too.
    top_nodes = [n for n in elements["nodes"] if n["parent"] == 0]
    if not any(n["type"] == 1 for n in top_nodes):
        nid = next_node; next_node += 1
        elements["nodes"].append({"id": nid, "name": node_name("start"),
                                  "type": 1, "parent": 0,
                                  "x": min(x_by_page.get(0, [-100.0])) - 120.0,
                                  "y": 0.0, "k": -1})
    if not any(n["type"] == 3 for n in top_nodes):
        nid = next_node; next_node += 1
        elements["nodes"].append({"id": nid, "name": node_name("end"),
                                  "type": 3, "parent": 0,
                                  "x": max(x_by_page.get(0, [400.0])) + 120.0,
                                  "y": 0.0, "k": -1})

    # ---- links ------------------------------------------------------------
    links: list[dict[str, Any]] = []
    next_link = 1
    for c in conns:
        ftype = _int(c.get("InputObjectType"), _AWB_BLOCK)
        ttype = _int(c.get("OutputObjectType"), _AWB_BLOCK)
        findex = _int(c.get("InputObjectIndex"), 0)
        tindex = _int(c.get("OutputObjectIndex"), 0)
        fend = _endpoint(ftype, findex, block_id, repeat_id, node_id, hier_id,
                         blocks, page_owner, hierarchy_page)
        tend = _endpoint(ttype, tindex, block_id, repeat_id, node_id, hier_id,
                         blocks, page_owner, hierarchy_page)
        if fend is None or tend is None:
            report.warnings.append(
                f"connection {_text(c.get('Id'), str(next_link))} refers to an "
                "object this reader does not recognise; skipped")
            continue
        page = _int(c.get("Page"), 0)
        owner_row = _page_owner_row(page, page_owner, hierarchy_page)
        links.append({
            "id": next_link,
            "from": list(fend), "to": list(tend),
            "parent": 0 if owner_row == 0 else hier_id.get(owner_row, 0),
        })
        next_link += 1

    # ---- close open ends on each page ------------------------------------
    # AWB pages may have no explicit input/output nodes at all (Example1 is
    # built that way): the page's first and last items are the ends.  RAPTOR
    # needs a Start/End (or a hierarchy In/Out) on every level, so add the
    # missing links, with an ordinary node as a split/join when a page has
    # several open ends.
    def add_link(src: tuple[str, int], dst: tuple[str, int], parent: int) -> None:
        nonlocal next_link
        links.append({"id": next_link, "from": list(src), "to": list(dst),
                      "parent": parent})
        next_link += 1

    def add_junction(owner: int, name: str, x: float, y: float) -> int:
        nonlocal next_node
        nid = next_node
        next_node += 1
        elements["nodes"].append({"id": nid, "name": node_name(name),
                                  "type": 2, "parent": owner, "x": x,
                                  "y": y, "k": 1})
        return nid

    page_owner_ids: list[tuple[int, int]] = [(0, 0)]
    for i, _b in ordered_super:
        page_owner_ids.append((i, hier_id[i]))
    for _page, owner in page_owner_ids:
        if owner == 0:
            ins = [n["id"] for n in elements["nodes"]
                   if n["parent"] == 0 and n["type"] == 1]
            outs = [n["id"] for n in elements["nodes"]
                    if n["parent"] == 0 and n["type"] == 3]
        else:
            hier: dict[str, Any] | None = None
            for candidate in elements["hierarchies"]:
                if candidate["id"] == owner:
                    hier = candidate
                    break
            if hier is None:
                continue
            ins, outs = [hier["in_id"]], [hier["out_id"]]
        if not ins or not outs:
            continue
        items: list[tuple[str, int]] = [
            ("Block", b["id"]) for b in elements["blocks"] if b["parent"] == owner]
        items += [("Hier", h["id"]) for h in elements["hierarchies"]
                  if h["parent"] == owner]
        # Do not use the page's own in/out nodes as flow items.
        items = [it for it in items if it[1] not in ins + outs]
        if not items:
            continue
        indeg = {it: 0 for it in items}
        outdeg = {it: 0 for it in items}
        for link in links:
            link_src = tuple(link["from"])
            link_dst = tuple(link["to"])
            if link_src in outdeg:
                outdeg[link_src] += 1
            if link_dst in indeg:
                indeg[link_dst] += 1
        open_in = [it for it in items if indeg[it] == 0]
        open_out = [it for it in items if outdeg[it] == 0]
        if not open_in and not open_out:
            continue
        if len(open_in) == 1:
            add_link(("Node", ins[0]), open_in[0], owner)
        elif open_in:
            j = add_junction(owner, "page_in", 0.0, 0.0)
            add_link(("Node", ins[0]), ("Node", j), owner)
            for it in open_in:
                add_link(("Node", j), it, owner)
        if len(open_out) == 1:
            add_link(open_out[0], ("Node", outs[0]), owner)
        elif open_out:
            j = add_junction(owner, "page_out", 0.0, 0.0)
            for it in open_out:
                add_link(it, ("Node", j), owner)
            add_link(("Node", j), ("Node", outs[0]), owner)

    # ---- annotations from AWB labels -------------------------------------
    annotations: list[dict[str, Any]] = []
    for lab in labels:
        if _int(lab.get("Type"), 0) != 0:
            continue
        annotations.append({
            "kind": "text", "id": len(annotations) + 1,
            "parent": 0 if _int(lab.get("Page"), 0) == 0 else hier_id.get(
                _page_owner_row(_int(lab.get("Page"), 0), page_owner,
                                hierarchy_page), 0),
            "x": _num(lab.get("XPosition")), "y": _num(lab.get("YPosition")),
            "w": max(20.0, _num(lab.get("Width"), 100.0)),
            "h": max(10.0, _num(lab.get("Height"), 20.0)),
            "text": _text(lab.get("LabelText")),
            "z": 20, "border_colour": None, "border_width": 0,
            "line_style": "solid", "fill_colour": None,
            "text_colour": "#132238", "font_family": "sans-serif",
            "font_size": max(6.0, _num(lab.get("FontSize"), 10.0)),
            "font_bold": False, "font_italic": False,
        })

    # ---- project settings -------------------------------------------------
    project = _rows(tables, "Project")[0] if _rows(tables, "Project") else {}
    lifetime = max(1.0, _num(project.get("LifeTime"), 87600.0))
    trials = max(1, _int(project.get("AvsimNoSimulationsRequested"), 1000))
    report.counts.update(blocks=len(elements["blocks"]),
                         nodes=len(elements["nodes"]),
                         hierarchies=len(elements["hierarchies"]),
                         links=len(links), failure_models=len(fms),
                         repeats=len(repeats), labels=len(annotations))
    if annotations:
        report.notes.append(
            f"{len(annotations)} AWB label(s) imported as RAPTOR annotations")
    if repeats:
        report.notes.append(
            f"{len(repeats)} AWB repeat block(s) imported as never-failing "
            "blocks that depend on the original block")
    if _text(project.get("Description")):
        report.notes.append(
            f"AWB project description: {_text(project.get('Description'))}")

    doc = {
        "format": "raptor-studio",
        "version": 4,
        "meta": {"generator": "RAPTOR Studio AWB importer"},
        "system": {"units": "hours"},
        "experiment": {
            "trials": trials,
            "termination_type": "TimeSim",
            "term_condition": lifetime,
        },
        "elements": elements,
        "links": links,
        "annotations": annotations,
        "comments": comments,
    }
    from raptor.core.fileio.rbds import parse_rbds
    rbd = parse_rbds(json.dumps(doc), default_name=Path(src).stem)
    return rbd, report


def _super_depth(row: int, blocks: list[dict[str, Any]]) -> int:
    """Depth of a superblock row by following its Page back to page 0."""
    depth = 0
    seen: set[int] = set()
    while row not in seen:
        seen.add(row)
        page = _int(blocks[row].get("Page"), 0) if 0 <= row < len(blocks) else 0
        if page == 0:
            return depth
        # The page's owner is the superblock row whose index equals `page`.
        row = page
        depth += 1
        if row < 0 or row >= len(blocks):
            return depth
    return depth


def _page_owner_row(page: int, page_owner: dict[int, str],
                    hierarchy_page: dict[str, int]) -> int:
    """The AWB superblock row that owns *page* (0 when it is top level)."""
    owner = page_owner.get(page, "0")
    return 0 if owner == "0" else hierarchy_page.get(owner, 0)


def _node_page(node: dict[str, Any], nodes: list[dict[str, Any]],
               node_id: dict[int, int]) -> int:
    for i, n in enumerate(nodes):
        if node_id.get(i) == node["id"]:
            return _int(n.get("Page"), 0)
    return -1


def _endpoint(kind: int, index: int, block_id: dict[int, int],
              repeat_id: dict[int, int], node_id: dict[int, int],
              hier_id: dict[int, int], blocks: list[dict[str, Any]],
              page_owner: dict[int, str],
              hierarchy_page: dict[str, int]) -> tuple[str, int] | None:
    if kind == _AWB_BLOCK:
        if index in block_id:
            return ("Block", block_id[index])
        # A superblock is an ordinary row in RbdBlocks too; links to it are
        # links to the RAPTOR hierarchy element.
        if index in hier_id:
            return ("Hier", hier_id[index])
        return None
    if kind == _AWB_REPEAT:
        return ("Block", repeat_id[index]) if index in repeat_id else None
    if kind == _AWB_NODE:
        return ("Node", node_id[index]) if index in node_id else None
    return None


def _block_data(block: dict[str, Any], fms: list[dict[str, Any]],  # noqa: C901, PLR0912
                tasks: list[dict[str, Any]],
                losses: dict[str, int]) -> tuple[int, list[float], int, list[float]]:
    """RAPTOR failure and repair laws for one AWB block.

    *losses* accumulates the things AWB can do and RAPTOR cannot, so the
    report can give one count per kind rather than one line per block.
    """
    fm_i = _int(block.get("FailureModel"), -1)
    if fm_i < 0 or fm_i >= len(fms):
        return _R_FIXED, [_NEVER, 0.0, 0.0], _R_FIXED, [1.0, 0.0, 0.0]
    fm = fms[fm_i]
    dist = _int(fm.get("FmDistribution"), _AWB_EXPONENTIAL)
    mttf = max(1e-9, _num(fm.get("FmMttf"), 8760.0))
    eta = max(1e-9, _num(fm.get("FmEta1"), mttf))
    beta = max(1e-9, _num(fm.get("FmBeta1"), 1.0))
    gamma = max(0.0, _num(fm.get("FmGamma1"), 0.0))
    if dist == _AWB_EXPONENTIAL:
        fdist, fparams = _R_EXPO, [mttf, 0.0, 0.0]
    elif dist == _AWB_WEIBULL:
        fdist, fparams = _R_WEIBULL, [beta, eta, gamma]
    elif dist == _AWB_FIXED:
        fdist, fparams = _R_FIXED, [mttf, 0.0, 0.0]
    elif dist == _AWB_LOGNORMAL:
        fdist, fparams = _R_LOGNORM, [mttf, max(0.0, _num(fm.get("FmStd"))), 0.0]
    elif dist == _AWB_NORMAL:
        fdist, fparams = _R_NORM, [mttf, max(0.0, _num(fm.get("FmStd"))), 0.0]
    else:
        # Bi-/tri-Weibull, buffer and phased laws have no exact RAPTOR
        # equivalent in the friendly model.  Keep the first mode and say so.
        fdist, fparams = _R_WEIBULL, [beta, eta, gamma]
        losses["multi-mode or buffer failure distribution"] = (
            losses.get("multi-mode or buffer failure distribution", 0) + 1)
    if _int(fm.get("FmDormant"), 0):
        losses["dormant failures"] = losses.get("dormant failures", 0) + 1
        return fdist, fparams, _R_NONE, [1.0, 0.0, 0.0]
    if _num(fm.get("StartUpFailureProb"), 0.0) > 0.0:
        losses["start-up failure probability"] = (
            losses.get("start-up failure probability", 0) + 1)
    task = next((t for t in tasks if _int(t.get("FailureModel"), -1) == fm_i), None)
    if task is None:
        return fdist, fparams, _R_FIXED, [0.0, 0.0, 0.0]
    duration = max(0.0, _num(task.get("TaskDuration")) + _num(task.get("RampTime")))
    tdist = _int(task.get("Distribution"), 2)
    std = max(0.0, _num(task.get("Std")))
    if _int(block.get("NonMaintainable"), 0):
        losses["non-maintainable blocks"] = (
            losses.get("non-maintainable blocks", 0) + 1)
        return fdist, fparams, _R_NONE, [1.0, 0.0, 0.0]
    if tdist == 0:
        return fdist, fparams, _R_EXPO, [max(1e-9, duration), 0.0, 0.0]
    if tdist == 1:
        return fdist, fparams, _R_LOGNORM, [max(1e-9, duration), std, 0.0]
    if tdist == 3:
        return fdist, fparams, _R_WEIBULL, [
            max(1e-9, _num(task.get("Beta"), 2.0)), max(1e-9, duration),
            max(0.0, _num(task.get("Gamma")))]
    if std > 0:
        return fdist, fparams, _R_NORM, [max(1e-9, duration), std, 0.0]
    return fdist, fparams, _R_FIXED, [max(0.0, duration), 0.0, 0.0]


def _comment_text(row: dict[str, Any]) -> str:
    parts = [_text(row.get("Description"))]
    for key in ("Notes1", "Notes2", "Notes3", "Notes4"):
        text = _text(row.get(key))
        if text:
            parts.append(text)
    return " | ".join(p for p in parts if p)


def _system_row_from_tables(tables: dict[str, AwbTable]) -> dict[str, Any]:
    rows = _rows(tables, "Project")
    return rows[0] if rows else {}


def _check_import_features(tables: dict[str, AwbTable], report: AwbReport) -> None:  # noqa: C901, PLR0912
    """Name the AWB features RAPTOR has no equivalent for."""
    if _rows(tables, "Consequences"):
        report.add("consequence and cost models", len(_rows(tables, "Consequences")),
                   "AWB financial/safety/environmental consequences are not "
                   "modelled by RAPTOR")
    if _rows(tables, "Phases") or _rows(tables, "RbdBlockRuleAssignments"):
        report.add("mission phases and phase rules",
                   max(len(_rows(tables, "Phases")),
                       len(_rows(tables, "RbdBlockRuleAssignments"))),
                   "AWB phase schedules and rules are not imported")
    if _rows(tables, "FailureModelScheduledTasks"):
        report.add("planned maintenance tasks",
                   len(_rows(tables, "FailureModelScheduledTasks")),
                   "AWB scheduled/inspection tasks are not imported")
    if _rows(tables, "Spares") or _rows(tables, "FailureModelCorrTaskSpares"):
        report.add("spares and sparing rules",
                   max(len(_rows(tables, "Spares")),
                       len(_rows(tables, "FailureModelCorrTaskSpares"))),
                   "AWB spare holdings and logistics are not imported")
    if _rows(tables, "Labor") or _rows(tables, "Equipment"):
        report.add("labour and equipment resources",
                   max(len(_rows(tables, "Labor")), len(_rows(tables, "Equipment"))),
                   "AWB resource pools are not imported")
    if _rows(tables, "RcmLocations") or _rows(tables, "RcmCauses"):
        report.add("RCM location hierarchy", max(
            len(_rows(tables, "RcmLocations")), len(_rows(tables, "RcmCauses"))),
            "the RCMCost module is not imported")
    if _rows(tables, "Gates") or _rows(tables, "PrimaryEvents"):
        report.add("fault trees", max(len(_rows(tables, "Gates")),
                                      len(_rows(tables, "PrimaryEvents"))),
                   "fault trees are not imported")
    if _rows(tables, "WeibullSets") or _rows(tables, "WeibullSetItems"):
        report.add("Weibull sets", max(len(_rows(tables, "WeibullSets")),
                                       len(_rows(tables, "WeibullSetItems"))),
                   "linked historical data is flattened to its first mode")
    if _rows(tables, "LibraryBitmaps") or _rows(tables, "LibraryBitmapBytes"):
        report.add("bitmaps", max(len(_rows(tables, "LibraryBitmaps")),
                                  len(_rows(tables, "LibraryBitmapBytes"))),
                   "AWB diagram bitmaps are not imported")
    if _rows(tables, "RbdLabels"):
        # Labels are imported as annotations (a win, not a loss); note it in
        # the report rather than in not_in_target.
        pass
    capacity = any(_num(b.get("MaxCapacity"), 100000.0) not in (100000.0, 0.0)
                   for b in _rows(tables, "RbdBlocks"))
    if capacity or _int(_system_row_from_tables(tables)
                        .get("AvsimCapacityCalcsPerformed"), 0):
        report.add("capacity flow and capacity targets", 1,
                   "AWB capacity products are not imported")
    nonop = sum(1 for b in _rows(tables, "RbdBlocks")
                if _int(b.get("NonOperational"), 0))
    if nonop:
        report.add("non-operational blocks", nonop,
                   "AWB non-operational state is not imported")
    standby = sum(1 for b in _rows(tables, "RbdBlocks")
                  if _int(b.get("StandbyMode"), 0) or _int(b.get("LogicMode"), 0))
    standby += sum(1 for n in _rows(tables, "RbdNodes")
                   if _int(n.get("LocalStandby"), 0) or _int(n.get("NotLogic"), 0))
    if standby:
        report.add("standby and NOT logic", standby,
                   "AWB standby/NOT arrangements are not imported")
    curtailed = sum(1 for b in _rows(tables, "RbdBlocks")
                    if _int(b.get("Consequence"), -1) >= 0)
    if curtailed:
        report.add("block consequence links", curtailed,
                   "AWB consequence assignments are not imported")
