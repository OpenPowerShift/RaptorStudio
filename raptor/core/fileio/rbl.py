"""Raptor 7's block library file, `.rbl` (Raptor 7's `ReadLib` and Export Library).

A plain text table. The first line is `Raptor7.0/<model>/<Day_Mon_dd_hh:mm:ss_yyyy>` (Raptor 6.0 files
start `Raptor6.0` and carry fewer columns), the second a column heading, then one block per line:

    name  FDist FVal1 FVal2 FVal3  RDist RVal1 RVal2 RVal3  PreLDT PostLDT  Yes|No  PreVal1-3  PostVal1-3

`read_rbl` reproduces what Raptor 7 does with a file: duplicate names get `-2`, `-3`..., a line
that starts with a blank is skipped, a missing field takes its default, a failure or repair
distribution outside 1-22 (or 16) refuses the whole file, and each distribution's parameters are
clamped into its legal range. `write_rbl` writes the same layout (column padding included).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from raptor.core.engine.report_format import real_to_str

HEADER_COLUMNS = ("Block_Name    FDist  FVal1  FVal2  FVal3  RDist  RVal1  Rval2  RVal3  PreLDT  PostLDT  "
                  "Depend PreVal1 PreVal2 PreVal3  PostVal1 PostVal2 PostVal3")

#: Parameters per distribution (`GetNumParams`): how many of the three values a block's own list
#: carries, and so how many the writer prints before padding with `0.0`.
_NPARAMS = {1: 2, 2: 1, 3: 2, 4: 2, 5: 2, 6: 3, 7: 2, 8: 2, 9: 2, 10: 2, 11: 3, 12: 3, 13: 1, 14: 3,
            15: 3, 17: 1, 18: 0, 19: 1, 20: 0, 21: 2, 22: 2}


class RblError(ValueError):
    """A library file Raptor 7 refuses (not formatted as a library, or an invalid distribution)."""


@dataclass
class LibEntry:
    name: str
    fail_dist: int = 4
    fail_values: list[str] = field(default_factory=lambda: ["100", "", ""])
    repair_dist: int = 7
    repair_values: list[str] = field(default_factory=lambda: ["10", "2", ""])
    pre_dist: str = "0"
    post_dist: str = "0"
    dependent: bool = False
    pre_vals: list[str] = field(default_factory=lambda: ["", "", ""])
    post_vals: list[str] = field(default_factory=lambda: ["", "", ""])
    version: int = 7


def _real(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        return 0.0


def _int_string(text: str) -> str:
    return str(math.trunc(_real(text)))


def _clamp_params(dist: int, v: list[str]) -> None:
    """The per-distribution range checks of `ReadLib`; `v` holds the three
    value strings and is edited in place, in the source's order (later checks see earlier fixes)."""
    hi = 999999999.999999
    small = 0.000001

    def bad(i: int, lo: float = small, up: float = hi) -> bool:
        return _real(v[i]) < lo or _real(v[i]) > up

    if dist == 1:
        for i in (0, 1):
            if bad(i):
                v[i] = "1"
    elif dist == 2:
        if math.trunc(_real(v[0])) != _real(v[0]):
            v[0] = _int_string(v[0])
        if _real(v[0]) > 999999999.0:
            v[0] = "999999999"
    elif dist == 3:
        if math.trunc(_real(v[0])) != _real(v[0]):
            v[0] = _int_string(v[0])
        if _real(v[0]) > 999999999.0:
            v[0] = "999999999"
        if _real(v[1]) < small or _real(v[1]) > 1.0:
            v[1] = "0.8"
    elif dist == 4:
        if bad(0):
            v[0] = "100"
        if _real(v[1]) < 0.0 or _real(v[1]) > hi:
            v[1] = "0"
    elif dist == 5:
        if math.trunc(_real(v[0])) != _real(v[0]):
            v[0] = _int_string(v[0])
        if _real(v[0]) > 999999999.0:
            v[0] = "999999999"
        if bad(1):
            v[1] = "1"
    elif dist in (6, 11, 15):
        for i in (0, 1):
            if bad(i):
                v[i] = "1"
        if _real(v[2]) < 0.0 or _real(v[2]) > hi:
            v[2] = "0"
    elif dist == 7:
        if bad(0):
            v[0] = "10"
        if bad(1):
            v[1] = "2"
    elif dist == 8:
        if _real(v[0]) < 0.0 or _real(v[0]) > hi:
            v[0] = "1"
        if bad(1):
            v[1] = "1"
    elif dist == 9:
        if math.trunc(_real(v[0])) != _real(v[0]):
            v[0] = _int_string(v[0])
        if _real(v[0]) < 0.0:
            v[0] = "0"
        if math.trunc(_real(v[1])) != _real(v[1]):
            v[1] = _int_string(v[1])
        if _real(v[1]) <= 0.0 or _real(v[1]) > 999999999.0:
            v[0] = "1"                     # (sic: the source resets the lower bound, not the upper)
        if _real(v[0]) > _real(v[1]):
            v[0] = v[1]
    elif dist == 10:
        if _real(v[0]) < 0.0:
            v[0] = "0"
        if _real(v[1]) <= 0.0 or _real(v[1]) > hi:
            v[1] = "1"
        if _real(v[0]) > _real(v[1]):
            v[0] = v[1]
    elif dist == 12:
        for i in (0, 1, 2):
            if bad(i):
                v[i] = "1"
    elif dist == 13:
        if bad(0):
            v[0] = "1"
    elif dist == 14:
        if _real(v[0]) < 0.0:
            v[0] = "0"
        if _real(v[2]) <= 0.0 or _real(v[2]) > hi:
            v[2] = "999999999"
        if _real(v[0]) >= _real(v[1]):
            v[0] = real_to_str(_real(v[1]) / 2.0)
        if _real(v[1]) >= _real(v[2]):
            v[1] = real_to_str(_real(v[2]) / 2.0)
            if _real(v[0]) >= _real(v[1]):
                v[0] = real_to_str(_real(v[1]) / 2.0)


def read_rbl(text: str) -> tuple[list[LibEntry], bool]:
    """The entries of a library file, and whether any lines started with a blank (Raptor 7 warns
    that those were not used). Raises `RblError` for a file it would refuse."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    head = lines[0] if lines else ""
    if head.startswith("Raptor6.0"):
        version = 6
    elif head.startswith("Raptor7.0"):
        version = 7
    else:
        raise RblError("It does not appear to be properly formatted.")
    entries: list[LibEntry] = []
    names: list[str] = []
    blank_lines = False
    for line in lines[2:]:
        if line.strip() == "":
            continue
        if line[0] in " \t":
            blank_lines = True
            continue
        tokens = line.split()
        name = tokens[0]
        rest = tokens[1:]
        base = name
        matches = 1
        while name in names:
            matches += 1
            name = f"{base}-{matches}"
        names.append(name)
        e = LibEntry(name=name, version=version)
        it = iter(rest)

        def take(it=it) -> str | None:
            return next(it, None)
        fd = take()
        if fd is not None:
            d = int(_real(fd))
            if d == 16 or d < 1 or d > 22:
                raise RblError("There is an invalid distribution in the library file.")
            e.fail_dist = d
            e.fail_values = [take() or "0", take() or "0", take() or "0"]
            if version == 6:
                take()
            rd = take()
            if rd is not None:
                d = int(_real(rd))
                if d == 16 or d < 1 or d > 22:
                    raise RblError("There is an invalid distribution in the library file.")
                e.repair_dist = d
                e.repair_values = [take() or "0", take() or "0", take() or "0"]
                if version == 6:
                    take()
                pre = take()
                if pre is not None:
                    e.pre_dist = pre
                    post = take()
                    if post is not None:
                        e.post_dist = post
                        dep = take()
                        if dep is not None:
                            e.dependent = dep.lower() == "yes"
                            if version == 7:
                                param_values = [take() for _ in range(3)]
                                if param_values[0] is not None:
                                    e.pre_vals = [x or "0" for x in param_values]
                                    qv = [take() for _ in range(3)]
                                    if qv[0] is not None:
                                        e.post_vals = [x or "0" for x in qv]
        entries.append(e)
    for e in entries:
        _clamp_params(e.fail_dist, e.fail_values)
        _clamp_params(e.repair_dist, e.repair_values)
        if e.version == 6:
            for attr in ("pre_dist", "post_dist"):
                v = _real(getattr(e, attr))
                if v < 0.0 or v > 999999999.999999:
                    setattr(e, attr, "0")
        else:
            for attr in ("pre_dist", "post_dist"):
                if int(_real(getattr(e, attr))) < 1 or int(_real(getattr(e, attr))) > 22:
                    setattr(e, attr, "0")
    return entries, blank_lines


def _vals_text(values: list[float], n: int) -> str:
    out = "".join(real_to_str(x) + "  " for x in values[:n])
    return out + "0.0  " * (3 - min(n, 3))


def write_rbl(blocks: list[dict], model_name: str, stamp: str) -> str:
    """The text of a library file for `blocks` (BLOCK_DATA-style dicts with `name`, `fail_dist`,
    `fail_values`, `repair_dist`, `repair_values`, `pre_dist`, `pre_vals`, `post_dist`, `post_vals`,
    `dependent`). `stamp` is `Tue_Jan_04_10:35:11_2005`. Lines end CRLF, as Raptor 7 writes."""
    longest = max((len(b["name"]) for b in blocks), default=0)
    out = [f"Raptor7.0/{model_name}/{stamp}", HEADER_COLUMNS]
    for b in blocks:
        line = b["name"] + " " * max(longest - len(b["name"]) + 2, 0)
        if b["fail_dist"] != 16:
            line += f"{b['fail_dist']}  " + _vals_text(b["fail_values"], _NPARAMS.get(b["fail_dist"], 3))
        else:
            line += "4  100.000000  0.000000  0.0  "
        if b["repair_dist"] != 16:
            line += f"{b['repair_dist']}  " + _vals_text(b["repair_values"], _NPARAMS.get(b["repair_dist"], 3))
        else:
            line += "7  10.000000  2.000000  0.0  "
        line += f"{b['pre_dist']}  {b['post_dist']}  " + ("Yes  " if b["dependent"] else "No   ")
        line += _vals_text(b["pre_vals"], _NPARAMS.get(int(_real(str(b["pre_dist"]))), 3))
        line += _vals_text(b["post_vals"], _NPARAMS.get(int(_real(str(b["post_dist"]))), 3))
        out.append(line)
    return "".join(f"{ln}\r\n" for ln in out)


def _floats(row: dict, *columns: str) -> list[float]:
    out = []
    for c in columns:
        try:
            out.append(float(row.get(c, "0") or 0))
        except ValueError:
            out.append(0.0)
    return out


def blocks_from_rows(rows: list[dict]) -> list[dict]:
    """`write_rbl`'s block dicts from BLOCK_DATA rows (the `ID=0` template row skipped), in file
    order -- the order the original walks its block group in."""
    blocks = []
    for r in rows:
        try:
            if int(float(r.get("ID", "0"))) == 0:
                continue
        except ValueError:
            continue
        try:
            dep = int(float(r.get("DepNum", "0") or 0))
        except ValueError:
            dep = 0
        blocks.append(dict(
            name=r.get("blockName", ""),
            fail_dist=int(float(r.get("FDist", "4") or 4)),
            fail_values=_floats(r, "FParam1", "FParam2", "FParam3"),
            repair_dist=int(float(r.get("RDist", "7") or 7)),
            repair_values=_floats(r, "RParam1", "RParam2", "RParam3"),
            pre_dist=str(int(float(r.get("Pre-LDTDist", "0") or 0))),
            pre_vals=_floats(r, "Param1#1", "Param2#1", "Param3#1"),
            post_dist=str(int(float(r.get("Post-LDTDist", "0") or 0))),
            post_vals=_floats(r, "Param1#2", "Param2#2", "Param3#2"),
            dependent=dep == -2))
    return blocks


def template_row(entry: LibEntry) -> dict:
    """An entry as a partial BLOCK_DATA row: the columns a library update overwrites (what Raptor 7's
    Import does to a same-named block with every box ticked). A version-6 library's LDTs are
    fixed values (`Fixed` distribution 19)."""
    nf = _NPARAMS.get(entry.fail_dist, 3)
    nr = _NPARAMS.get(entry.repair_dist, 3)
    row = {
        "blockName": entry.name,
        "FDist": str(entry.fail_dist), "#FParam": str(nf),
        "FParam1": entry.fail_values[0], "FParam2": entry.fail_values[1], "FParam3": entry.fail_values[2],
        "RDist": str(entry.repair_dist), "#RParam": str(nr),
        "RParam1": entry.repair_values[0], "RParam2": entry.repair_values[1], "RParam3": entry.repair_values[2],
        "DepNum": "-2" if entry.dependent else "0", "DepType": "None",
    }
    if entry.version == 6:
        row.update({"Pre-LDTDist": "19", "Param1#1": entry.pre_dist, "Param2#1": "0", "Param3#1": "0",
                    "Post-LDTDist": "19", "Param1#2": entry.post_dist, "Param2#2": "0", "Param3#2": "0"})
    else:
        row.update({"Pre-LDTDist": entry.pre_dist, "Param1#1": entry.pre_vals[0],
                    "Param2#1": entry.pre_vals[1], "Param3#1": entry.pre_vals[2],
                    "Post-LDTDist": entry.post_dist, "Param1#2": entry.post_vals[0],
                    "Param2#2": entry.post_vals[1], "Param3#2": entry.post_vals[2]})
    return row
