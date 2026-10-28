"""Reader for Raptor 7's `.rbd` save-file format.

Format (reverse-engineered from Raptor 7 files -- see
tests/golden/*/input.rbd):

    Version<ver> <appname>        <filename>        <timestamp>
    Phases=       <n>
    Blocks=       <n>
    Events=       <n>
    Nodes=        <n>
    Links=        <n>
    SpPools=      <n>
    ResPools=     <n>
    Triggers=     <n>
    Hiers=        <n>
    HierDepths=   <n>
    /
    /___...SYSTEM_DATA...___
    <header row: column names>
    <one data row>
    EndOfSystemData
    /___...HIERARCHY_DATA...___
    <header row>
    <0+ data rows>
    EndOfHierarchyData
    ... (BLOCK_DATA, EVENT_DATA, NODE_DATA, COMMENTS, LINK_DATA,
         SPARE_POOL_DATA, RESOURCE_DATA, PHASES, TRIGGERS, each the same
         shape: a section-marker line containing the section name, one
         header row, zero or more data rows, then either an explicit
         `EndOf<Name>Data` sentinel or (COMMENTS section only, observed in
         Raptor 7 fixtures with zero comments) no sentinel at all — the next
         section's marker line ends it instead.)

`parse_rbd` reads section structure and row counts and returns each row as a
dict of `{column_name: raw_string_value}`; converting those raw strings into
typed `raptor.core.model` dataclasses is `typed.py`'s job. Columns are
whitespace-delimited (no field contains embedded whitespace), which holds for
every fixture parsed so far.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_COUNT_LINE = re.compile(r"^(\w+)=\s*(\d+)\s*$")
_SECTION_MARKER = re.compile(r"^/_+([A-Z_ ]+?)_*$")

#: Section name (as it appears in the `/___NAME___` marker line) -> its
#: `EndOf<...>Data` sentinel. Hardcoded from what's actually observed in
#: Raptor 7 fixtures (tests/golden/*/input.rbd) rather than derived by string
#: manipulation — the naming isn't fully regular (TRIGGERS's sentinel is
#: singular "EndOfTriggerData", not "EndOfTriggersData"), so guessing it
#: programmatically yields a row-count-off-by-one (the sentinel line itself
#: parsed as a bogus extra data row). COMMENTS has no observed sentinel at
#: all in Raptor 7 fixtures (only ever seen empty) — left unmapped; its section
#: is still terminated correctly by the next-section-marker fallback in
#: `parse_rbd`.
_END_MARKERS: dict[str, str] = {
    "SYSTEM_DATA": "EndOfSystemData",
    "HIERARCHY_DATA": "EndOfHierarchyData",
    "BLOCK_DATA": "EndOfBlockData",
    "EVENT_DATA": "EndOfEventData",
    "NODE_DATA": "EndOfNodeData",
    "LINK_DATA": "EndOfLinkData",
    "SPARE_POOL_DATA": "EndOfSparePoolData",
    "RESOURCE_DATA": "EndOfResourceData",
    "PHASES": "EndOfPhasesData",
    "TRIGGERS": "EndOfTriggerData",
}

#: (section_name, column_name) -> exact token count that column consumes on
#: each data row. A naive 1-token-per-column zip silently misaligns every
#: column after a multi-token one (e.g. `SysStreamsArray`, one column
#: header but 11 space-separated integers of real data — one per system
#: stream A-J plus Ancillary). Add more entries here if a new fixture turns
#: up another multi-token column.
_MULTI_TOKEN_COLUMNS: dict[tuple[str, str], int] = {
    ("SYSTEM_DATA", "SysStreamsArray"): 11,
}

#: Phase-per-column data (`BLOCK_DATA`/`EVENT_DATA`'s `"P 001"`, `"P 002"`,
#: ... and `NODE_DATA`'s `"P001"`, `"P002"`, ...) is RAGGED, not fixed-width
#: like `_MULTI_TOKEN_COLUMNS`: a row only carries these trailing tokens at
#: all when its own `Phasing`/`Phases` flag column is `"1"` — an unphased
#: row's raw line simply ENDS after its last ordinary column, with zero
#: phase tokens, not zero-padded ones (e.g. a `Phasing=0` row has no
#: trailing tokens at all, while a `Phasing=1` row has one two-token
#: `"C:   0.500"`-style value per phase). `NODE_DATA` uses the SAME
#: conditional mechanism but ONE token per phase column (a bare status
#: letter `C`/`A`/`L` for a hierarchy's own Out-marker node, or a bare
#: k-value integer for an ordinary phased node — both single tokens, no
#: `X:` prefix). `HIERARCHY_DATA` needs NO special handling despite having
#: its own `Phs` flag column -- a phased hierarchy's actual per-phase values
#: live on its Out-marker `NODE_DATA` row instead, so `HIERARCHY_DATA`'s own
#: row shape is always fixed-width.
_PHASE_COLUMN_RE = re.compile(r"^P ?\d{3}$")
_PHASE_FLAG_COLUMN: dict[str, str] = {
    "BLOCK_DATA": "Phasing",
    "EVENT_DATA": "Phasing",
    "NODE_DATA": "Phases",
}
_PHASE_TOKEN_WIDTH: dict[str, int] = {
    "BLOCK_DATA": 2,
    "EVENT_DATA": 2,
    "NODE_DATA": 1,
}

#: Columns `OpenFile` reads with `ReadInt` --
#: i.e. the file format stores each as ONE integer token. `ReadInt` stops at
#: the first character it cannot use, so a decimal value does not just read
#: as its whole part: it leaves the remaining digits in the token stream,
#: shifting every later read and crashing Raptor 7 with `MODSIM Runtime
#: error: Access Violation` while the file is opening (confirmed 2026-10-27
#: with a 3,470-block model whose only two fractional cells were
#: `LINK_DATA.nomFlow`/`MaxFlow` -- they came from a capacity flow entered
#: through the API as 133.76). `render_rbd` truncates these columns to whole
#: numbers on the way out (matching `typed.row_int` and the engine, so the
#: exported file means what RAPTOR Studio simulated), `input_rules` refuses
#: a fractional value on entry, and `validate()` reports one already in a
#: file. Note `SYSTEM_DATA.NumRuns` is deliberately absent -- the reader
#: takes it with `ReadReal`, and real files write it as `10.000000`.
_LEGACY_INT_COLUMNS: dict[str, frozenset[str]] = {
    "SYSTEM_DATA": frozenset({
        "ShowNegErrMsg", "IgnoreStartFail", "flowGenerated", "weakAnalysis",
        "costAnalysis", "capacityAnalysis", "NotUsed",
    }),
    "HIERARCHY_DATA": frozenset({
        "ID", "Parent", "In", "Out", "isCNd", "in", "out", "level", "Phs",
        "myDepth", "extraInt1", "extraInt2",
    }),
    "BLOCK_DATA": frozenset({
        "ID", "ParentId", "In", "Out", "Phasing", "SimStart", "FDist",
        "#FParam", "FStream", "RDist", "#RParam", "RStream", "GDType", "RSO",
        "Stock", "NewSp", "ESO", "SLO", "Level", "Quan", "Pre-LDTDist",
        "Post-LDTDist", "NumRes", "#Res1Rep", "#Res1pmRep", "usesPM",
        "PMSpare", "PMRefresh", "PMMisDefer", "PMFailReset", "PMReqDefer",
        "pmTriggered", "Dist", "DepNum", "defDepStateIdle", "addDone",
    }),
    "EVENT_DATA": frozenset({
        "ID", "ParentId", "In", "Out", "Phasing", "Distro", "failStream",
    }),
    "NODE_DATA": frozenset({
        "ID", "parent", "Type", "k", "n", "Out", "Cold", "PR", "Autos",
        "k*", "Phases", "depNum", "divFlow", "fullflow", "repCap", "repNA",
    }),
    "LINK_DATA": frozenset({
        "ID", "FromID", "ToID", "pID", "ColdPri", "CapPri", "nomFlow",
        "MaxFlow",
    }),
    "SPARE_POOL_DATA": frozenset({
        "RSO", "Stock", "NewSp", "ESO", "SLO", "Level", "Quan",
    }),
    "RESOURCE_DATA": frozenset({"NumIn", "Phases"}),
    "PHASES": frozenset({"Num", "Mission", "Dist"}),
    "TRIGGERS": frozenset({"IDnum", "Repeats", "Dist"}),
}


def _integer_token(section: str, column: str, text: str) -> str:
    """A cell the legacy reader consumes with `ReadInt`, as a whole-number
    token. Truncation (not rounding) matches `typed.row_int` and the engine,
    so a fractional value means in the legacy file what it meant in the
    simulation. Non-numeric cells (`"none"`, `"C"`/`"L"` phase status) pass
    through: some integer columns legitimately carry a keyword."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return text
    if not math.isfinite(value):
        return text
    whole = int(value)
    if whole != value:
        log.warning(
            "%s.%s holds %s, which the legacy .rbd format cannot represent; "
            "writing %d (Raptor 7 reads the column with ReadInt and crashes "
            "on a decimal)", section, column, text, whole)
    return str(whole)


@dataclass
class RbdSection:
    name: str
    columns: list[str]
    rows: list[dict[str, str]] = field(default_factory=list)


@dataclass
class RbdFile:
    version: str
    app_name: str
    file_name: str
    timestamp: str
    counts: dict[str, int]
    sections: dict[str, RbdSection]
    #: .rbds-only annotation layer (text / rectangle / ellipse drawn on
    #: top of the diagram). Legacy .rbd has no representation -- the
    #: GUI warns and drops these when saving legacy.
    annotations: list = field(default_factory=list)
    #: .rbds-only named "Rate" library (reusable expressions with a
    #: human-readable basis, e.g. "0.00015/km failure rate x 20000
    #: km/yr usage" -- entries are {"name": str, "expr": str,
    #: "value": float, "description": str}. `expr` may reference
    #: another rate via an embedded "@OtherRate" token (e.g.
    #: "@BaseRate * 1.4"), resolved one level deep against
    #: that rate's own `value` -- see expr_eval.py's module docstring.
    #: "description" is optional/may be absent on older entries (added
    #: after `name`/`expr`/`value`). Legacy .rbd has no representation.
    rates: list = field(default_factory=list)
    #: .rbds-only per-field formula/rate LINK -- the basis behind a
    #: field's plain numeric value, so reopening the model shows the
    #: original derivation instead of just a flattened float. Keyed by
    #: "<kind>:<element key>:<legacy column>" (e.g. "block:5:FParam1"),
    #: each entry is EITHER {"expr": <typed arithmetic text>, "value":
    #: float} or {"rate": <name in `rates`>, "value": float}. The
    #: field's own value in `sections` is ALWAYS the real, already-
    #: computed float -- this dict is documentation/re-editability on
    #: top of it, never the value's only source of truth. Legacy .rbd
    #: has no representation; the GUI confirms with the user and writes
    #: the plain values only.
    formulas: dict = field(default_factory=dict)
    #: .rbds-only DISPLAY-unit override: view/enter time values in e.g.
    #: years on an hours-native model without hand-converting. `None` =
    #: show the model's own native `units` unchanged. Never touches a
    #: stored value or the SYSTEM_DATA `units` column itself -- see
    #: `units_conv.py`'s own docstring; purely a display/entry-time
    #: transform applied by the GUI. Legacy .rbd has no representation
    #: (dropped with a warning, same as annotations -- losing a display
    #: preference is low-stakes, not a blocking confirmation like
    #: rates/formulas).
    display_unit: str | None = None
    #: .rbds-only PRINT TITLE BLOCK ({name, revision, date}), the lines
    #: that make a printout attributable. Lives with the model rather
    #: than in application settings for the same reason as
    #: `display_unit`: it describes THIS model, so two open models must
    #: not overwrite each other's, and a colleague printing the file
    #: should get the same attribution. The GUI's print dialog reads
    #: and writes this; the legacy .rbd format has no representation
    #: (same warn-and-drop precedent as annotations/display_unit).
    project_meta: dict | None = None
    #: .rbds-only RUN EXPERIMENT (see `raptor/gui/ui/run_experiment_mixin.py`):
    #: trials, termination, statistics start, enabled analyses and
    #: recording/plot selection. Travels with the model so a colleague
    #: gets the experiment the author actually ran, but is NOT part of
    #: the model's identity -- a run manifest (built at run start from
    #: this plus the model content hash) is what makes a result set
    #: self-describing, so changing the trial count here does not
    #: orphan the provenance of results already produced.
    #:
    #: The legacy .rbd format has columns for trials/termination/
    #: analyses but NOT for the recording selection, so reading one in
    #: derives an experiment from those columns (lossless) and writing
    #: one out is lossy -- hence the warn-and-drop precedent shared with
    #: annotations/display_unit.
    experiment: dict | None = None
    #: .rbds-only FROZEN canvas transform (scale/origin/grid-pitch),
    #: set by DiagramView._compute_model_transform the first time it
    #: derives one and reused on every subsequent load instead of
    #: re-deriving. Without this, moving even ONE element can shrink
    #: the model's global closest-spacing, which rescales the WHOLE
    #: canvas on reopen -- every untouched element visibly jumps even
    #: though its own stored xPos/yPos never changed. Legacy .rbd has no
    #: representation -- reopening a legacy file still re-derives fresh
    #: each time, same already-accepted limitation as
    #: annotations/display_unit.
    #: Keys: scale, origin_x, flip_base, dx_min, dy_min (all float).
    view_transform: dict | None = None
    #: Original RAPTOR's overwrite protection: the last SYSTEM_DATA
    #: integer is -1 for a "protected" file, which blocks saving
    #: Without this the port would
    #: happily overwrite a file Raptor 7 refuses to.
    protected_file: bool = False

    def section(self, name: str) -> RbdSection:
        return self.sections[name]


def _split_row(line: str) -> list[str]:
    return line.split()


_PHASE_HEADER_NUM_RE = re.compile(r"^\d{3}$")


def _merge_phase_header_tokens(tokens: list[str]) -> list[str]:
    """`BLOCK_DATA`/`EVENT_DATA`'s phase columns are literally named `"P
    001"`, `"P 002"`, ... in the Raptor 7 file — a space-containing header
    name, not one token. A plain `.split()` (as `_split_row` does for
    every other header) breaks `"P 001"` into two separate tokens `"P"`,
    `"001"`; re-merge any `"P"` token immediately followed by a 3-digit
    token back into one `"P 001"`-style column name, matching what
    `_zip_row`'s `_PHASE_COLUMN_RE` expects. `NODE_DATA`'s own phase
    columns (`"P001"`, no space) are already single tokens and pass
    through unchanged."""
    merged: list[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "P" and i + 1 < len(tokens) and _PHASE_HEADER_NUM_RE.match(tokens[i + 1]):
            merged.append(f"P {tokens[i + 1]}")
            i += 2
        else:
            merged.append(tokens[i])
            i += 1
    return merged


def _disambiguate_columns(columns: list[str]) -> list[str]:
    """`BLOCK_DATA`'s header repeats generic names ("Param1"/"Param2"/
    "Param3") three times over — once each for its Pre-LDT, Post-LDT, and
    PM-trigger distribution parameter groups. A plain `dict(zip(columns,
    values))` silently collides these, keeping only the last group's
    values. Disambiguate every column name that appears more than once by
    appending `#1`, `#2`, ... (1-indexed, in header order) to *every*
    occurrence — including the first — so it's never ambiguous whether a
    given key is disambiguated or not."""
    from collections import Counter

    counts = Counter(columns)
    seen: dict[str, int] = {}
    result = []
    for col in columns:
        if counts[col] > 1:
            seen[col] = seen.get(col, 0) + 1
            result.append(f"{col}#{seen[col]}")
        else:
            result.append(col)
    return result


def _zip_row(section_name: str, columns: list[str], values: list[str]) -> dict[str, str]:
    """Pair `columns` with `values`, honoring `_MULTI_TOKEN_COLUMNS` widths
    (a naive 1:1 zip silently misaligns everything after a multi-token
    column — see that dict's docstring) and `_PHASE_COLUMN_RE`'s ragged
    per-row phase columns (see that constant's own docstring) — a row
    whose own `Phasing`/`Phases` flag column is falsy has NO trailing
    tokens for any phase column at all, so those keys are simply absent
    from the returned dict rather than set to `""`; callers already use
    `.get(...)` for every optional field elsewhere in this codebase."""
    flag_col = _PHASE_FLAG_COLUMN.get(section_name)
    phase_width = _PHASE_TOKEN_WIDTH.get(section_name, 1)
    row: dict[str, str] = {}
    vi = 0
    is_phased = False
    for col in columns:
        if flag_col is not None and _PHASE_COLUMN_RE.match(col):
            if not is_phased:
                continue
            row[col] = " ".join(values[vi : vi + phase_width])
            vi += phase_width
            continue
        width = _MULTI_TOKEN_COLUMNS.get((section_name, col), 1)
        row[col] = " ".join(values[vi : vi + width])
        vi += width
        if col == flag_col:
            is_phased = row[col] not in ("0", "")
    return row


def parse_rbd(text: str) -> RbdFile:
    lines = text.splitlines()
    if not lines:
        raise ValueError("empty .rbd content")

    header_parts = lines[0].split(None, 2)
    if not header_parts or not header_parts[0].startswith("Version"):
        raise ValueError(f"unrecognized .rbd header line: {lines[0]!r}")
    version = header_parts[0]
    if not version.startswith("Version7"):
        # Version 5/6 files have separate readers + `UpdateVersion`
        # back-fill in Raptor 7; the port
        # has no such migration, so accepting them would misparse.
        raise ValueError(
            f"unsupported RAPTOR file version {version!r}: only Version 7 "
            f"files can be opened (open Version 5/6 files in Raptor 7 "
            f"RAPTOR to migrate them first)")
    # remainder of the header line is one of
    #   "<app_name>   <file_name>   <timestamp>"  (Student saves) or
    #   "<file_name>   <timestamp>"               (release saves, no
    #   app token) — disambiguated by whether the first token names a
    #   .rbd file rather than an application. app_name/file_name are
    #   single tokens, timestamp is everything after the file_name token.
    rest = lines[0][len(version):].split()
    if rest and rest[0].lower().endswith(".rbd"):
        app_name = ""
        file_name = rest[0]
        timestamp = " ".join(rest[1:])
    else:
        app_name = rest[0] if rest else ""
        file_name = rest[1] if len(rest) > 1 else ""
        timestamp = " ".join(rest[2:]) if len(rest) > 2 else ""

    counts: dict[str, int] = {}
    i = 1
    while i < len(lines):
        m = _COUNT_LINE.match(lines[i])
        if not m:
            break
        counts[m.group(1)] = int(m.group(2))
        i += 1

    sections: dict[str, RbdSection] = {}
    while i < len(lines):
        line = lines[i]
        m = _SECTION_MARKER.match(line)
        if not m:
            i += 1
            continue
        section_name = m.group(1).strip("_ ").strip()
        i += 1
        if i >= len(lines):
            break
        columns = _disambiguate_columns(_merge_phase_header_tokens(_split_row(lines[i])))
        i += 1
        rows: list[dict[str, str]] = []
        end_marker = _END_MARKERS.get(section_name)
        while i < len(lines):
            candidate = lines[i]
            if candidate.strip() == "" and not columns:
                i += 1
                continue
            if end_marker is not None and candidate.startswith(end_marker):
                i += 1
                break
            if _SECTION_MARKER.match(candidate):
                # no explicit EndOf marker (observed for empty COMMENTS
                # sections) — next section's marker line ends this one.
                break
            if candidate.strip() == "/":
                # bare "/" separator line seen after empty COMMENTS header
                i += 1
                continue
            values = _split_row(candidate)
            if section_name == "COMMENTS" and len(values) > len(columns):
                # The comment is FREE TEXT to end of line (source reads
                # one word then `ReadLine`, Raptor 7); a plain
                # split keeps only its first word and a save would drop
                # the rest.
                head = candidate.split(None, len(columns) - 1)
                values = head[:-1] + [head[-1].rstrip()]
            if values:
                rows.append(_zip_row(section_name, columns, values))
                i += 1
                if section_name == "BLOCK_DATA":
                    i = _read_empirical_points(lines, i, rows[-1])
                continue
            i += 1
        sections[section_name] = RbdSection(name=section_name, columns=columns, rows=rows)

    return RbdFile(
        version=version,
        app_name=app_name,
        file_name=file_name,
        timestamp=timestamp,
        counts=counts,
        sections=sections,
        protected_file=_is_protected(sections),
    )


#: Row keys (not columns) holding an empirical distribution's data points, as
#: space-joined numbers. The legacy file carries them on a continuation line
#: after the block's own line, not in `FParam1..3` (which are written as 0).
EMPIRICAL_POINT_KEYS = (("FDist", "#FParam", "FEmp"), ("RDist", "#RParam", "REmp"))


def _read_empirical_points(lines: list[str], i: int, row: dict[str, str]) -> int:
    """Consume the continuation line(s) after a block row whose failure and/or
    repair distribution is Empirical (16), storing the points on the row.
    Port of `OpenFile`'s `failEmp`/`repEmp` reads: the
    reader takes `numFailParams` reals (then `numRepairParams`) from the
    stream, skipping line breaks. Returns the index of the next unread line."""
    for dist_col, count_col, key in EMPIRICAL_POINT_KEYS:
        if _as_int(row.get(dist_col)) != 16:
            continue
        wanted = _as_int(row.get(count_col))
        points: list[str] = []
        while len(points) < wanted and i < len(lines):
            points.extend(_split_row(lines[i]))
            i += 1
        row[key] = " ".join(points[:wanted])
    return i


def store_distribution_values(row: dict[str, str], which: str, dist: int,
                              values: list[float]) -> None:
    """Write a block's failure (`which="F"`) or repair (`"R"`) values onto its
    `BLOCK_DATA` row the way Raptor 7 stores them. For Empirical (16) that is
    the data points sorted ascending on the continuation line, the count in
    `#FParam`/`#RParam` and 0 in the three parameter columns (`GetEmpirical`
    sorts on OK, `SaveFile` writes the points). For any other distribution the first three
    values go in the parameter columns and a stale point list is dropped.
    The caller keeps the count of a non-Empirical distribution."""
    dist_col, count_col, key = next(k for k in EMPIRICAL_POINT_KEYS if k[0][0] == which)
    prefix = f"{which}Param"
    row[dist_col] = str(dist)
    if dist == 16:
        points = sorted(float(v) for v in values)
        row[key] = " ".join(f"{p:.6f}" for p in points)
        row[count_col] = str(len(points))
        for n in (1, 2, 3):
            row[f"{prefix}{n}"] = "0.000000"
        return
    row.pop(key, None)
    padded = (list(values) + [0.0, 0.0, 0.0])[:3]
    for n, v in enumerate(padded, start=1):
        row[f"{prefix}{n}"] = f"{float(v):.6f}"


def _as_int(text: str | None) -> int:
    try:
        return int(float(text or "0"))
    except ValueError:
        return 0


def _is_protected(sections: dict[str, RbdSection]) -> bool:
    """Raptor 7's `ProtectedFile` flag: last SYSTEM_DATA integer is
    -1. Unknown/missing column -> not protected."""
    sec = sections.get("SYSTEM_DATA")
    if not sec or not sec.rows or not sec.columns:
        return False
    last_col = sec.columns[-1]
    return sec.rows[0].get(last_col, "").strip() == "-1"


def load_rbd(path: str) -> RbdFile:
    with open(path, encoding="latin-1") as f:
        parsed = parse_rbd(f.read())
    # Raptor 7's reports title themselves with the DISK filename that was
    # opened, not the header's internal file_name. The internal name is
    # only a fallback when no path is known (GUI paste).
    import os
    parsed.file_name = os.path.basename(path)
    return parsed


#: Order the header count lines appear in, in every Raptor 7 fixture seen.
_COUNT_ORDER = [
    "Phases", "Blocks", "Events", "Nodes", "Links",
    "SpPools", "ResPools", "Triggers", "Hiers", "HierDepths",
]

#: `{header key: (section name, row-count offset)}` -- matches every
#: fixture's own header vs section row counts (`Blocks` is the one
#: exception: `BLOCK_DATA` always carries one extra `ID=0`
#: sentinel/template row the header count excludes, hence `-1`; every
#: other section's row count matches its header count exactly, including
#: when both are 0). `render_rbd` derives these counts from the actual
#: section content instead of trusting whatever `f.counts` happened to
#: hold, so a header/content mismatch cannot recur for any section listed
#: here, no matter how the caller edited it. `Phases`/`HierDepths` are
#: deliberately not in this map -- `Phases`'s exact row semantics and
#: `HierDepths`'s section (it doesn't correspond to any parsed section at
#: all) aren't established, and no fixture populates either with real
#: data; `render_rbd` keeps echoing whatever `f.counts` already has for
#: those two rather than guessing.
_COUNT_SECTIONS: dict[str, tuple[str, int]] = {
    "Phases": ("PHASES", 0),
    "Blocks": ("BLOCK_DATA", -1),
    "Events": ("EVENT_DATA", 0),
    "Nodes": ("NODE_DATA", 0),
    "Links": ("LINK_DATA", 0),
    "SpPools": ("SPARE_POOL_DATA", 0),
    "ResPools": ("RESOURCE_DATA", 0),
    "Triggers": ("TRIGGERS", 0),
    "Hiers": ("HIERARCHY_DATA", 0),
}

_DISAMBIG_SUFFIX = re.compile(r"#\d+$")


def _undisambiguate(column: str) -> str:
    """Inverse of `_disambiguate_columns` for a single column name — strips
    a trailing `#<digit>` suffix, but only the kind `_disambiguate_columns`
    adds (a literal `#` immediately followed by digits at the very end).
    Deliberately does NOT touch columns like `#FParam`/`#RParam` (real
    column names starting with `#`, not disambiguation artifacts) since
    the suffix pattern only matches a trailing `#digits`, not a leading one."""
    return _DISAMBIG_SUFFIX.sub("", column)


#: Section order the legacy reader HARD-REQUIRES. OpenFile
#: walks the file strictly positionally — after each section's
#: `EndOf<...>Data` it blindly ReadLine-skips the next marker + header, so
#: a file whose sections appear in any other order is misparsed as
#: garbage (crash/corrupt diagram). Every fixture observed follows it.
_LEGACY_SECTION_ORDER = [
    "SYSTEM_DATA", "HIERARCHY_DATA", "BLOCK_DATA", "EVENT_DATA",
    "NODE_DATA", "COMMENTS", "LINK_DATA", "SPARE_POOL_DATA",
    "RESOURCE_DATA", "PHASES", "TRIGGERS",
]

#: The legacy reader ALWAYS consumes 4 trailing tokens per HIERARCHY_DATA
#: row beyond the columns our own template carries (emptyInt emptyInt
#: emptyReal emptyReal in OpenFile; Raptor 7 fixtures name them extraInt1
#: extraInt2 extraReal1 extraReal2 and mirror ID/ID/xOrigin/yOrigin).
#: A row missing them shifts every subsequent ReadInt/ReadReal by 4 tokens
#: per hierarchy row, corrupting the whole rest of the parse. Readers
#: discard the values, so mirroring the Raptor 7 fixtures' convention is
#: fidelity-for-free.
_HIER_EXTRA_COLUMNS = ["extraInt1", "extraInt2",
                       "extraReal1", "extraReal2"]


def _canonical_empty_columns(name: str) -> list[str] | None:
    """The column header Raptor 7 writes for a section that has NO rows, or None for a section whose
    header is always derived from its rows (SYSTEM/BLOCK/NODE/LINK).

    A model built from a spec (`build_from_spec`, so MCP `build_model`) leaves every empty section with no
    header, or HIERARCHY_DATA with only its four trailing tokens, and omits COMMENTS. Raptor 7 opens such a
    file to a blank window with no error (2026-10-01, found while building the verification ladder's first
    generated model), so an empty section always gets its canonical header here."""
    from raptor.core.fileio import rbdlib
    cols = {
        "HIERARCHY_DATA": rbdlib.HIER_COLS, "EVENT_DATA": rbdlib.EVENT_COLS,
        "COMMENTS": rbdlib.COMMENT_COLS, "SPARE_POOL_DATA": rbdlib.SPARE_COLS,
        "RESOURCE_DATA": rbdlib.RES_COLS, "PHASES": rbdlib.PHASE_COLS, "TRIGGERS": rbdlib.TRIGGER_COLS,
    }.get(name)
    return list(cols) if cols is not None else None


def _legacy_section_items(f: RbdFile):
    """Yield (name, section) in the order the legacy reader requires;
    unknown (future) sections keep their relative order after the known
    ones. A known section that is absent is written empty, with its canonical header (see
    `_canonical_empty_columns`): the reader walks the file positionally and expects every one."""
    known = [n for n in _LEGACY_SECTION_ORDER if n in f.sections or _canonical_empty_columns(n) is not None]
    extra = [n for n in f.sections if n not in _LEGACY_SECTION_ORDER]
    for name in known + extra:
        sec = f.sections.get(name)
        if sec is None:
            sec = RbdSection(name=name, columns=_canonical_empty_columns(name) or [], rows=[])
        yield name, sec


def _hier_output_columns_and_row_pad(section: RbdSection):
    """Return (output_columns, per-row extra values source) for
    HIERARCHY_DATA so every row always carries the 4 trailing tokens the
    legacy reader unconditionally reads. Columns already carrying them
    (legacy-loaded files) pass through untouched; hand-built sections
    get the Raptor 7 fixtures' column names appended and rows padded by
    mirroring ID/ID/xOrigin/yOrigin."""
    missing = [c for c in _HIER_EXTRA_COLUMNS
               if c not in section.columns]
    if not missing:
        return section.columns, None

    def pad(row: dict) -> list[str]:
        rid = row.get("ID", "") or "0"
        xo = row.get("xOrigin", "") or "0.000000"
        yo = row.get("yOrigin", "") or "80.000000"
        return [rid, rid, xo, yo]

    return section.columns + missing, pad


#: Legacy release version stamped into a rendered `.rbd` header whenever the
#: `RbdFile` carries a version token that isn't a real `Version7...` release
#: string. `build_from_spec` creates `RbdFile(version="rbds", ...)` (the
#: .rbds format's own marker), which is NOT a valid legacy header: `parse_rbd`
#: rejects anything not starting with `Version7`, so without this `render_rbd`
#: emits an unreadable file and `simulate(workers>1)` fails because its
#: parallel path serialises the model to legacy `.rbd` for its workers
#: (orchestrator.py, `render_rbd` -> `parse_rbd`). This is the same release
#: version the golden fixtures and `NEW_MODEL_TEXT` carry.
LEGACY_VERSION = "Version7_7.0.11"


def _derive_columns(section) -> list[str]:
    """Column order for a section whose ``columns`` list is empty.

    Models built from a spec carry rows but no header (``build_from_spec``
    does not populate ``section.columns``), so without this the legacy
    writer emits a blank header and `parse_rbd` drops every row -- which
    also silently emptied the model handed to parallel workers.  The row
    dicts come from the rbdlib constructors in canonical legacy order, so
    their key order is the right column order; any keys that appear only
    on later rows are appended.
    """
    cols: list[str] = []
    seen: set[str] = set()
    for row in section.rows:
        for key in row:
            if key in ("FEmp", "REmp"):   # continuation lines, not columns
                continue
            if key not in seen:
                seen.add(key)
                cols.append(key)
    return cols


def render_rbd(f: RbdFile) -> str:
    """Serialize an `RbdFile` back to `.rbd` text.

    NOT byte-exact against the Raptor 7 engine's own writer (Raptor 7 files use
    variable-width column padding for human alignment; this writer uses a
    single space between tokens). Round-trips *semantically*: parsing this
    output back with `parse_rbd` reproduces the same header/counts/section/
    row structure — verified in tests/unit/test_fileio_rbd.py against both
    Raptor 7 fixtures.

    Legacy-loadability (matching OpenFile in the Raptor 7 app's 
    source): the header is written RELEASE-style (no "Student" token — note
    the Student edition refuses release-version files) with the DISK
    filename, sections are emitted in the reader's hard-required order,
    every HIERARCHY_DATA row carries the 4 trailing tokens its token-stream
    reader always consumes, and the file terminates with `EndOfFile` like
    every Raptor 7 fixture.
    """
    lines: list[str] = []
    # Normalise a non-legacy version token (e.g. an .rbds-built model's
    # "rbds") to a real release version, so the header `parse_rbd` (and the
    # Raptor 7 engine) accepts. See LEGACY_VERSION's docstring.
    version = f.version
    if not version.startswith("Version7"):
        version = LEGACY_VERSION
    lines.append(f"{version}        {f.file_name}        {f.timestamp}")
    for key in _COUNT_ORDER:
        if key in _COUNT_SECTIONS:
            section_name, offset = _COUNT_SECTIONS[key]
            count = max(0, len(f.sections[section_name].rows) + offset)
        else:
            count = f.counts.get(key, 0)
        lines.append(f"{key}=       {count}")
    lines.append("/")
    for section_name, section in _legacy_section_items(f):
        lines.append(f"/{'_' * 3}{section_name}{'_' * 3}")
        columns = [c for c in (list(section.columns) or _derive_columns(section))
                   if c not in ("FEmp", "REmp")]
        canonical = _canonical_empty_columns(section_name)
        if canonical is not None and not section.rows:
            columns = canonical
        row_pad = None
        if section_name == "HIERARCHY_DATA":
            columns, row_pad = _hier_output_columns_and_row_pad(
                RbdSection(name=section.name, columns=columns, rows=section.rows))
        raw_columns = [_undisambiguate(c) for c in columns]
        lines.append(" ".join(raw_columns))
        # Whitespace-delimited format: an EMPTY value emits NO token, so
        # the Raptor 7 reader's positional ReadInt/ReadString sequence
        # misaligns on that row and every row after it (e.g. 40 blocks with
        # `pmTrig=""` export as 86-token rows, and Raptor 7 reads the
        # dependency columns as the PM distribution and alerts "Unknown
        # distribution selected!"). Raptor 7 files always write a placeholder
        # for string columns (the template row's own convention: pmTrig
        # 'none', depType 'None', Resource1Name 'None'). Fill empties from
        # the section's ID=0 template row, else infer the column's type from
        # its other rows: 'none' for string columns, "0" for numeric ones --
        # a 'none' token in a numeric column crashes the typed readers
        # (float('none')). Golden fixtures carry no empty values, so this is
        # a no-op on Raptor 7 files.
        template_row = next((r for r in section.rows
                             if str(r.get("ID", "")).strip() == "0"), None)

        def _column_is_numeric(c: str, section=section) -> bool:
            for r2 in section.rows:
                v2 = r2.get(c, "")
                if v2:
                    try:
                        float(v2)
                        return True
                    except ValueError:
                        return False
            return False

        def _filled(c: str, own: str, template_row=template_row) -> str:
            if own != "":
                return own
            if template_row is not None:
                tv = template_row.get(c, "")
                if tv != "":
                    return tv
            if _column_is_numeric(c):
                return "0"
            return "none"

        # Phase columns are RAGGED (see `_PHASE_COLUMN_RE`): an unphased
        # row's line ENDS after its last ordinary column. Padding them
        # with 'none'/'0' crashes Raptor 7 on open -- the reader does
        # not consume those tokens, so the next row's name becomes 'none'.
        # `parse_rbd` silently skips them too, so a semantic round-trip
        # test does not catch this.
        flag_col = _PHASE_FLAG_COLUMN.get(section_name)
        # Rows use the section's own columns when it has them; a spec-built
        # model has none, so fall back to the derived header order.
        row_columns = [c for c in (list(section.columns) or columns)
                       if c not in ("FEmp", "REmp")]
        int_columns = _LEGACY_INT_COLUMNS.get(section_name, frozenset())

        def _row_is_phased(row: dict, flag_col=flag_col) -> bool:
            return flag_col is not None and row.get(flag_col, "") not in ("0", "")

        for row in section.rows:
            phased = _row_is_phased(row)
            out_columns = [c for c in row_columns
                           if phased or flag_col is None
                           or not _PHASE_COLUMN_RE.match(c)]
            values = []
            for c in out_columns:
                text = _filled(c, row.get(c, ""))
                if c in int_columns:
                    text = _integer_token(section_name, c, text)
                values.append(text)
            if row_pad is not None:
                values += row_pad(row)
            lines.append(" ".join(values))
            for _dist, _count, key in EMPIRICAL_POINT_KEYS:
                if row.get(key):
                    lines.append(" " * 25 + "  ".join(f"{float(p):16.6f}" for p in row[key].split()))
        end_marker = _END_MARKERS.get(section_name)
        if end_marker:
            lines.append(end_marker)
        elif section_name == "COMMENTS":
            # No EndOf marker exists for COMMENTS (see _END_MARKERS'
            # docstring) -- Raptor 7 files instead have a bare "/" line here,
            # which `parse_rbd` skips over as noise (see its `candidate.
            # strip() == "/"` branch) rather than storing it. Load-bearing,
            # not cosmetic: omitting it crashes the Raptor 7 engine (it parses
            # past LINK_DATA's own section marker as if it were more
            # COMMENTS data, corrupting everything read afterward), even
            # though every other formatting difference from the legacy
            # writer's padding is tolerated fine.
            lines.append("/")
    lines.append("EndOfFile")
    return "\n".join(lines) + "\n"


#: Raptor 7 draws the system's status symbol by loading
#: `SUBSTR(1,4,systemImage)+"_G"` (and `"_Y"`/`"_R"`) from its image library
#: Only these four-character families
#: exist. This port does not model the picture -- `.rbds` deliberately does
#: not store `imageString` (`rbds._emit_system`) -- so an export must name a
#: real family or Simulate stops with `GRAPHICS ERROR 220: Object 'None_G'
#: not found in library` after the analysis phase (2026-10-27, BESS model:
#: the port's template default is "None"). `Bull's_eye` is the release's own
#: default (`rbdlib.make_system_row`) and the commonest value in the captured corpus.
_LEGACY_IMAGE_FAMILIES = frozenset({
    "Airc", "Arti", "Bull", "Circ", "Comp", "Equa", "Face", "Heli", "Mete",
    "Miss", "Netw", "Radi", "Rado", "Sate", "Ship", "Stop", "Tank", "Trai",
    "Truc",
})
LEGACY_SYSTEM_IMAGE = "Bull's_eye"


def ensure_legacy_system_image(f: RbdFile) -> None:
    """Give SYSTEM_DATA an ``imageString`` Raptor 7 can actually load.

    The legacy app resolves the status faces through a fixed library, so a
    value the port never uses (its template says ``None``) crashes it at the
    start of a run. Keep a real family name untouched; replace anything
    else, including an empty cell.
    """
    sec = f.sections.get("SYSTEM_DATA")
    if not sec or not sec.rows:
        return
    row = sec.rows[0]
    name = str(row.get("imageString", "") or "").strip()
    if not name or name[:4] not in _LEGACY_IMAGE_FAMILIES:
        row["imageString"] = LEGACY_SYSTEM_IMAGE


def save_rbd(f: RbdFile, path: str) -> None:
    # Raptor 7's reports title themselves with the DISK filename that was
    # opened (see load_rbd) — same rule on save, so the header's
    # internal file_name is the file actually being written instead of
    # a stale/foreign name (e.g. "model.rbds" leaking from an .rbds
    # template parse into a legacy .rbd header).
    import copy
    import os
    f.file_name = os.path.basename(path)
    # Serialise a COPY: the legacy writer's compatibility passes cap
    # names at 20 characters and re-grid bad coordinates. Doing that in
    # place silently changed the open model (a later .rbds save kept the
    # truncated names) with no undo entry and no canvas refresh.
    out = copy.deepcopy(f)
    # Grid-compatibility: models authored in this app's own fractional
    # coordinates render in Raptor 7 as a tiny cluster of overlapping
    # boxes "split across 4 grid squares". Re-layout badly off-grid models
    # onto the legacy 1.25 grid before serialising; already-clean files
    # (every Raptor 7 fixture, even mildly off-grid ones) pass through
    # untouched — see legacy_layout.py.
    from raptor.core.fileio.legacy_layout import (normalize_legacy_layout,
                                                  repair_link_parents,
                                                  normalize_node_kofn,
                                                  cap_legacy_names)
    from raptor.core.fileio.rbds import sync_derived
    sync_derived(out)        # link and parameter counts Raptor 7 trusts
    ensure_legacy_system_image(out)   # status faces: "Bull's_eye", not "None"
    repair_link_parents(out)
    normalize_node_kofn(out)
    # Legacy .rbd only: element names capped at Raptor 7's 20-char
    # convention, with ~n distinguishers on truncated collisions. The
    # .rbds keeps the full names.
    cap_legacy_names(out)
    normalize_legacy_layout(out)
    with open(path, "w", encoding="latin-1") as fh:
        fh.write(render_rbd(out))
