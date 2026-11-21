"""Plan and apply a data-table import from an `.xlsx`/`.ods` spreadsheet.

The sheet is read by `raptor.core.fileio.sheet_reader`, which keeps each
cell's formula and cached value. This module maps headers to model fields,
validates every proposed value against the model's own distribution rules,
and returns a plan. Applying the plan produces a before/after snapshot of
only the affected `BLOCK_DATA` rows, which the GUI wraps in one undo
command; the command never leaves the model partly updated.

Supported columns (case and punctuation are ignored):

* `id` / `name` — identify the block (id wins; a name must be unique);
* `fail_dist`, `fail_param_1`..3 or `fail_params`;
* `repair_dist`, `repair_param_1`..3 or `repair_params`;
* `pm_freq`, `uses_pm`.

A formula cell is used at its cached value when the workbook has one, and
otherwise through the reader's safe arithmetic evaluator. The formula text
travels with the change, so the review dialog can show `=1/2400` beside
`0.000417`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from raptor.core.distro_rules import (
    DISTRO_NAMES, OFFERED, validate_distro_params,
)
from raptor.core.fileio.rbd import store_distribution_values
from raptor.core.fileio.rbdlib import DIST_NPARAMS, DIST_NPARAMS_DISK
from raptor.core.fileio.sheet_reader import Cell, Sheet
from raptor.core.fileio.typed import row_int

#: Canonical field -> accepted header spellings (already normalised).
_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "block_id", "blockid", "element_id", "key"),
    "name": ("name", "block_name", "blockname", "element", "element_name",
             "component", "component_name"),
    "fail_dist": ("fail_dist", "failure_dist", "failure_distribution",
                  "fdist", "f_dist", "failure_law"),
    "fail_params": ("fail_params", "failure_params", "failure_parameters"),
    "fail_param_1": ("fail_param_1", "failure_param_1", "fparam1",
                     "f_param_1", "fail_p1", "failure_parameter_1"),
    "fail_param_2": ("fail_param_2", "failure_param_2", "fparam2",
                     "f_param_2", "fail_p2", "failure_parameter_2"),
    "fail_param_3": ("fail_param_3", "failure_param_3", "fparam3",
                     "f_param_3", "fail_p3", "failure_parameter_3"),
    "repair_dist": ("repair_dist", "repair_distribution", "rdist", "r_dist",
                    "repair_law"),
    "repair_params": ("repair_params", "repair_parameters"),
    "repair_param_1": ("repair_param_1", "rparam1", "r_param_1",
                        "repair_p1", "repair_parameter_1"),
    "repair_param_2": ("repair_param_2", "rparam2", "r_param_2",
                        "repair_p2", "repair_parameter_2"),
    "repair_param_3": ("repair_param_3", "rparam3", "r_param_3",
                        "repair_p3", "repair_parameter_3"),
    "pm_freq": ("pm_freq", "pm_frequency", "preventive_maintenance_frequency",
                "maintenance_frequency"),
    "uses_pm": ("uses_pm", "use_pm", "pm", "pm_enabled",
                "preventive_maintenance"),
    "stock": ("stock", "initial_stock", "initial_spares", "spares"),
}
_ALIAS_LOOKUP = {alias: canonical for canonical, aliases in _ALIASES.items()
                 for alias in aliases}


def normalise_header(text: str) -> str:
    """`"Failure Param 1 (hours)"` -> `"failure_param_1"`."""
    text = str(text or "").strip().lower()
    text = text.split("(", 1)[0]
    text = text.replace("#", "")
    text = "".join(ch if ch.isalnum() else "_" for ch in text)
    return "_".join(part for part in text.split("_") if part)


def map_headers(headers: list[str]) -> tuple[dict[int, str], list[str]]:
    """`({column: canonical field}, [problems])`."""
    mapping: dict[int, str] = {}
    problems: list[str] = []
    seen: dict[str, int] = {}
    for col, header in enumerate(headers):
        key = normalise_header(header)
        if not key:
            continue
        canonical = _ALIAS_LOOKUP.get(key)
        if canonical is None:
            # A header with a unit suffix after the alias still maps: the
            # reader strips a parenthetical, this covers "fail_param_1_hours".
            canonical = next((field for alias, field in _ALIAS_LOOKUP.items()
                              if key.startswith(alias + "_")), None)
        if canonical is None:
            problems.append(f"column {col + 1} ({header!r}) is not a field "
                            "the import understands")
            continue
        if canonical in seen:
            problems.append(
                f"columns {seen[canonical] + 1} and {col + 1} both map to "
                f"{canonical!r}")
            continue
        seen[canonical] = col
        mapping[col] = canonical
    return mapping, problems


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------

@dataclass
class Change:
    ref: str
    id: int
    name: str
    field: str
    old: Any
    new: Any
    formula: str = ""

    def old_text(self) -> str:
        return _display(self.old)

    def new_text(self) -> str:
        return _display(self.new)


@dataclass
class Problem:
    row: int          # 1-based spreadsheet row, 0 for sheet-wide
    message: str
    ref: str = ""
    level: str = "error"      # "error" | "warning"

    def text(self) -> str:
        where = f"row {self.row}: " if self.row else ""
        return where + self.message


@dataclass
class ImportPlan:
    source: str
    sheet: str
    changes: list[Change] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    mapping: dict[int, str] = field(default_factory=dict)

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.level == "error"]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if p.level == "warning"]

    def has_errors(self) -> bool:
        return bool(self.errors)

    def summary(self) -> str:
        n = len(self.changes)
        blocks = len({c.ref for c in self.changes})
        text = f"{n} change(s) on {blocks} block(s)"
        if self.errors:
            text += f"; {len(self.errors)} error(s) block the import"
        return text


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (list, tuple)):
        return ", ".join(_display(v) for v in value)
    return str(value)


# ---------------------------------------------------------------------------
# reading the model
# ---------------------------------------------------------------------------

def _block_rows(rbd: Any) -> dict[int, dict[str, str]]:
    sec = (getattr(rbd, "sections", None) or {}).get("BLOCK_DATA")
    out: dict[int, dict[str, str]] = {}
    for row in (sec.rows if sec is not None else ()):
        oid = row_int(row.get("ID"), 0)
        if oid > 0:
            out[oid] = row
    return out


def _by_name(rows: dict[int, dict[str, str]]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for oid, row in rows.items():
        name = str(row.get("blockName") or "").strip().casefold()
        if name:
            out.setdefault(name, []).append(oid)
    return out


def _num(row: dict[str, str], column: str, default: float = 0.0) -> float:
    try:
        return float(row.get(column, default))
    except (TypeError, ValueError):
        return default


def _code(row: dict[str, str], column: str, default: int = 19) -> int:
    try:
        return int(float(row.get(column, default)))
    except (TypeError, ValueError):
        return default


def _params(row: dict[str, str], prefix: str, count: int) -> list[float]:
    return [_num(row, f"{prefix}Param{i}") for i in range(1, count + 1)]


def _count(row: dict[str, str], column: str, code: int) -> int:
    try:
        return int(float(row.get(column, DIST_NPARAMS.get(code, 3))))
    except (TypeError, ValueError):
        return DIST_NPARAMS.get(code, 3)


def _distro_code(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        code = int(float(value))
    except (TypeError, ValueError):
        text = str(value).strip().casefold()
        if text in ("none", "unrepairable", "none (unrepairable)"):
            return 18
        if text in ("fixed", "constant"):
            return 19
        for code, name in DISTRO_NAMES.items():
            if name.casefold() == text:
                return code
        return None
    return code if code in DISTRO_NAMES else None


def _parse_params(value: Any) -> list[float] | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [float(value)]
    text = str(value).strip()
    text = text.strip("()[]")
    parts = [p for p in text.replace(";", ",").replace(" ", ",").split(",")
             if p.strip()]
    try:
        return [float(p) for p in parts]
    except ValueError:
        return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().casefold()
    if text in ("yes", "true", "1", "on", "y"):
        return True
    if text in ("no", "false", "0", "off", "n"):
        return False
    return None


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------

def plan_import(rbd: Any, sheet: Sheet, *, source: str = "") -> ImportPlan:
    """Validate *sheet* against *rbd* and return the changes it would make."""
    plan = ImportPlan(source=source or sheet.name, sheet=sheet.name)
    rows = _block_rows(rbd)
    by_name = _by_name(rows)
    headers = sheet.header()
    plan.headers = headers
    mapping, header_problems = map_headers(headers)
    for text in header_problems:
        # A harmless Notes column should not block an otherwise valid
        # import; two columns claiming the same field should.
        plan.problems.append(Problem(
            1, text, level="error" if "both map" in text else "warning"))
    plan.mapping = mapping
    fields = set(mapping.values())
    if "id" not in fields and "name" not in fields:
        plan.problems.append(Problem(
            1, "the sheet needs an 'id' or 'name' column to identify blocks"))
        return plan
    if not ({"fail_dist", "fail_params", "fail_param_1", "fail_param_2",
             "fail_param_3", "repair_dist", "repair_params",
             "repair_param_1", "repair_param_2", "repair_param_3",
             "pm_freq", "uses_pm", "stock"} & fields):
        plan.problems.append(Problem(
            1, "the sheet has no column the import can apply (failure/repair "
               "data, PM frequency, stock)"))
        return plan

    by_col = {col: field for col, field in mapping.items()}
    for sheet_row, row_cells in enumerate(sheet.rows[1:], start=2):
        cells = {by_col[c]: cell for c, cell in enumerate(row_cells)
                 if c in by_col}
        if not cells or all(_blank(cell) for cell in cells.values()):
            continue
        for cell in cells.values():
            if cell.error:
                plan.problems.append(Problem(
                    sheet_row, cell.error, level="error"))
        if any(cell.error for cell in cells.values()):
            continue
        oid = _resolve(cells, rows, by_name, plan, sheet_row)
        if oid is None:
            continue
        _plan_row(plan, rows[oid], oid, cells, sheet_row)
    return plan


def _blank(cell: Cell) -> bool:
    return (cell.value is None or cell.value == "") and not cell.formula


def _resolve(cells: dict[str, Cell], rows: dict[int, dict[str, str]],
             by_name: dict[str, list[int]], plan: ImportPlan,
             sheet_row: int) -> int | None:
    id_cell = cells.get("id")
    name_cell = cells.get("name")
    if id_cell is not None and id_cell.value not in (None, ""):
        try:
            oid = int(float(id_cell.value))
        except (TypeError, ValueError):
            plan.problems.append(Problem(
                sheet_row, f"{id_cell.value!r} is not a block id"))
            return None
        if oid not in rows:
            plan.problems.append(Problem(
                sheet_row, f"block id {oid} is not in the model"))
            return None
        if name_cell is not None and name_cell.value not in (None, ""):
            actual = str(rows[oid].get("blockName") or "").casefold()
            if actual != str(name_cell.value).strip().casefold():
                plan.problems.append(Problem(
                    sheet_row,
                    f"block {oid} is named {rows[oid].get('blockName')!r}, "
                    f"not {name_cell.value!r}",
                    level="warning"))
        return oid
    if name_cell is None or name_cell.value in (None, ""):
        plan.problems.append(Problem(
            sheet_row, "no id or name; the row cannot identify a block"))
        return None
    key = str(name_cell.value).strip().casefold()
    ids = by_name.get(key, [])
    if not ids:
        plan.problems.append(Problem(
            sheet_row, f"no block named {name_cell.value!r}"))
        return None
    if len(ids) > 1:
        plan.problems.append(Problem(
            sheet_row,
            f"{len(ids)} blocks are named {name_cell.value!r}; add an id "
            "column or make the names unique"))
        return None
    return ids[0]


def _cell(cells: dict[str, Cell], field: str) -> Cell | None:
    return cells.get(field)


def _value(cells: dict[str, Cell], field: str) -> Any:
    cell = cells.get(field)
    return None if cell is None else cell.value


def _formula(cells: dict[str, Cell], field: str) -> str:
    cell = cells.get(field)
    return cell.formula or "" if cell is not None else ""


def _add(plan: ImportPlan, row: dict[str, str], oid: int, field: str,
         old: Any, new: Any, cells: dict[str, Cell], source_field: str) -> None:
    if _same(old, new):
        return
    plan.changes.append(Change(
        ref=f"block:{oid}", id=oid,
        name=str(row.get("blockName") or ""), field=field, old=old, new=new,
        formula=_formula(cells, source_field)))


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        try:
            aa, bb = list(a), list(b)
        except TypeError:
            return False
        return (len(aa) == len(bb)
                and all(_same(x, y) for x, y in zip(aa, bb, strict=True)))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
    return a == b


def _plan_row(  # noqa: C901, PLR0912, PLR0915
        plan: ImportPlan, row: dict[str, str], oid: int,
        cells: dict[str, Cell], sheet_row: int) -> None:
    for which, prefix, dist_field, params_field in (
            ("F", "F", "fail_dist", "fail_params"),
            ("R", "R", "repair_dist", "repair_params")):
        current_code = _code(row, f"{prefix}Dist")
        code_cell = _cell(cells, dist_field)
        code = current_code
        if code_cell is not None and code_cell.value not in (None, ""):
            parsed = _distro_code(code_cell.value)
            if parsed is None:
                plan.problems.append(Problem(
                    sheet_row, f"{code_cell.value!r} is not a known "
                    f"{'failure' if which == 'F' else 'repair'} distribution"))
                continue
            if parsed == 16:
                plan.problems.append(Problem(
                    sheet_row, "Empirical distributions cannot be imported "
                    "from a table; edit the data points in Data Tables"))
                continue
            offered = OFFERED.get("failure" if which == "F" else "repair", ())
            if offered and parsed not in offered:
                plan.problems.append(Problem(
                    sheet_row, f"{DISTRO_NAMES.get(parsed)} is not offered "
                    f"as a {'failure' if which == 'F' else 'repair'} "
                    "distribution"))
                continue
            code = parsed
        column = "F" if which == "F" else "R"
        current_count = _count(row, f"#{column}Param", current_code)
        current_params = _params(row, column, current_count)
        imported: list[float] | None = None
        formula_field = params_field
        if _value(cells, params_field) not in (None, ""):
            imported = _parse_params(_value(cells, params_field))
            if imported is None:
                plan.problems.append(Problem(
                    sheet_row, f"{params_field.replace('_', ' ')} is not "
                    "a list of numbers"))
                continue
        else:
            values = []
            present = False
            for index in (1, 2, 3):
                value = _value(cells, f"{'fail' if which == 'F' else 'repair'}"
                                     f"_param_{index}")
                if value is not None and value != "":
                    present = True
                    values.append(value)
                else:
                    values.append(current_params[index - 1]
                                  if index <= len(current_params) else 0.0)
            if present:
                imported = [float(v) for v in values]
                formula_field = next(
                    (f"{'fail' if which == 'F' else 'repair'}_param_{i}"
                     for i in (1, 2, 3)
                     if _formula(cells, f"{'fail' if which == 'F' else 'repair'}"
                                 f"_param_{i}")),
                    params_field)
        if code != current_code:
            _add(plan, row, oid, dist_field,
                 DISTRO_NAMES.get(current_code, current_code),
                 DISTRO_NAMES.get(code, code), cells, dist_field)
            if imported is None:
                # Keep the current numbers only if they are valid for the
                # new law; otherwise the row is refused rather than letting
                # a new distribution silently inherit nonsense parameters.
                if not _valid(code, current_params, which):
                    plan.problems.append(Problem(
                        sheet_row, f"changing {dist_field.replace('_', ' ')} "
                        f"to {DISTRO_NAMES.get(code)} needs its parameters "
                        "in the same row"))
                    continue
                imported = current_params
        if imported is not None:
            if not _valid(code, imported, which):
                error = validate_distro_params(
                    code, imported, "failure" if which == "F" else "repair")
                plan.problems.append(Problem(
                    sheet_row,
                    error or f"{params_field.replace('_', ' ')} do not fit "
                    f"{DISTRO_NAMES.get(code, code)}"))
                continue
            _add(plan, row, oid,
                 f"{'fail' if which == 'F' else 'repair'}_params",
                 current_params, imported, cells, formula_field)

    pm_cell = _cell(cells, "pm_freq")
    if pm_cell is not None and pm_cell.value not in (None, ""):
        try:
            value = float(pm_cell.value)
        except (TypeError, ValueError):
            plan.problems.append(Problem(
                sheet_row, f"PM frequency {pm_cell.value!r} is not a number"))
            value = None
        if value is not None:
            if not math.isfinite(value) or value < 0:
                plan.problems.append(Problem(
                    sheet_row, "PM frequency must be zero or greater"))
            else:
                _add(plan, row, oid, "pm_freq", _num(row, "PMFreq"), value,
                     cells, "pm_freq")
    pm_on = _cell(cells, "uses_pm")
    if pm_on is not None and pm_on.value not in (None, ""):
        flag = _bool(pm_on.value)
        if flag is None:
            plan.problems.append(Problem(
                sheet_row, f"{pm_on.value!r} is not yes/no"))
        else:
            _add(plan, row, oid, "uses_pm",
                 bool(_bool(row.get("usesPM"))), flag, cells, "uses_pm")
    stock = _cell(cells, "stock")
    if stock is not None and stock.value not in (None, ""):
        # Stock is meaningful only for Custom sparing. Say so once here;
        # the generic apply still writes the column, which the engine
        # reads for that mode.
        try:
            value = int(float(stock.value))
        except (TypeError, ValueError):
            plan.problems.append(Problem(
                sheet_row, f"stock {stock.value!r} is not a whole number"))
        else:
            if value < 0:
                plan.problems.append(Problem(
                    sheet_row, "stock must be zero or greater"))
            else:
                if (not _same(_num(row, "Stock"), value)
                        and str(row.get("SpType", "")) != "Custom"):
                    plan.problems.append(Problem(
                        sheet_row, "stock is only read by the engine for a "
                        "Custom sparing block; this value is stored but has "
                        "no effect here", level="warning"))
                _add(plan, row, oid, "stock", _num(row, "Stock"), value,
                     cells, "stock")


def _valid(code: int, values: list[float], which: str) -> bool:
    padded = (list(values) + [0.0, 0.0, 0.0])[:3]
    return validate_distro_params(
        code, padded, "failure" if which == "F" else "repair") is None


# ---------------------------------------------------------------------------
# applying
# ---------------------------------------------------------------------------

def row_snapshot(plan: ImportPlan, rbd: Any) -> tuple[dict[int, dict], dict[int, dict]]:
    """`(before, after)` copies of the BLOCK_DATA rows the plan touches.

    The caller stores `before` and applies `after`; undo swaps them back.
    Rows are keyed by block id, not by position."""
    rows = _block_rows(rbd)
    before: dict[int, dict[str, str]] = {}
    after: dict[int, dict[str, str]] = {}
    grouped: dict[int, list[Change]] = {}
    for change in plan.changes:
        grouped.setdefault(change.id, []).append(change)
    for oid, changes in grouped.items():
        row = rows.get(oid)
        if row is None:
            continue
        current = dict(row)
        before[oid] = dict(current)
        for change in changes:
            _apply_change(current, change)
        after[oid] = current
    return before, after


def _apply_change(row: dict[str, str], change: Change) -> None:
    field = change.field
    if field in ("fail_dist", "repair_dist"):
        which = "F" if field.startswith("fail") else "R"
        code = _distro_code(change.new)
        if code is None:
            return
        row[f"{which}Dist"] = str(code)
        # Keep the current parameters in the row; a parameter change in
        # the same plan is applied next and overwrites them.
        count = DIST_NPARAMS_DISK.get(code, DIST_NPARAMS.get(code))
        if count is not None:
            row[f"#{which}Param"] = str(count)
    elif field in ("fail_params", "repair_params"):
        which = "F" if field.startswith("fail") else "R"
        code = _code(row, f"{which}Dist")
        values = [float(v) for v in change.new]
        store_distribution_values(row, which, code, values)
    elif field == "pm_freq":
        row["PMFreq"] = f"{float(change.new):.6f}"
    elif field == "uses_pm":
        row["usesPM"] = "1" if change.new else "0"
    elif field == "stock":
        row["Stock"] = str(int(change.new))


def write_rows(rbd: Any, rows: dict[int, dict]) -> None:
    """Replace the named BLOCK_DATA rows in place (the rows are aliases the
    canvas/Data Tables may be reading, so mutate, never replace the list)."""
    sec = (getattr(rbd, "sections", None) or {}).get("BLOCK_DATA")
    if sec is None:
        return
    current = {row_int(row.get("ID"), 0): row for row in sec.rows}
    for oid, values in rows.items():
        row = current.get(oid)
        if row is None:
            continue
        row.clear()
        row.update(values)


def apply_to_rbd(rbd: Any, plan: ImportPlan) -> tuple[dict[int, dict], dict[int, dict]]:
    """Apply the plan to the model rows without undo; returns its snapshots.

    Used by tests, the command line and any caller that owns its own undo
    mechanism. The GUI uses `row_snapshot` + its own QUndoCommand so the
    whole import is one History step."""
    before, after = row_snapshot(plan, rbd)
    write_rows(rbd, after)
    return before, after
