"""Read an `.xlsx` or `.ods` sheet without adding a spreadsheet dependency.

Both formats are ZIP + XML. This reader keeps, for every cell, the raw
value AND the formula AND the workbook's cached result, so an import can
show that ``0.00042`` came from ``=1/2400`` and still use the value the
spreadsheet application actually calculated.

A formula with no cached value (a workbook written by a script rather than
saved by Excel/LibreOffice) is evaluated by a deliberately small, safe
arithmetic evaluator: numbers, cell references, `+ - * / ^ %`, comparisons,
parentheses and a short list of functions. It has no file, network or
macro access. Anything outside that subset raises :class:`FormulaError`
naming the formula; it is never silently treated as zero.
"""
from __future__ import annotations

import ast
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

MAX_ROWS = 20_000
MAX_COLUMNS = 512


class SheetError(Exception):
    """A workbook or sheet cannot be read."""


class FormulaError(SheetError):
    """A formula is outside the safe arithmetic subset."""


# ---------------------------------------------------------------------------
# cell model
# ---------------------------------------------------------------------------

@dataclass
class Cell:
    """One spreadsheet cell: the stored value, its formula, its cache."""
    raw: Any = None
    formula: str | None = None
    cached: Any = None
    #: Display text (used for text cells and headers).
    text: str = ""
    #: `n`, `s`, `b`, `e`, `str`, ...
    kind: str = ""
    #: The value this reader resolved (formula cache or safe evaluation).
    value: Any = None
    error: str = ""

    @property
    def numeric(self) -> float | None:
        try:
            number = float(self.value if self.value is not None else self.raw)
        except (TypeError, ValueError):
            return None
        return number if number == number and abs(number) != float("inf") else None


@dataclass
class Sheet:
    name: str
    rows: list[list[Cell]] = field(default_factory=list)

    def cell(self, row: int, col: int) -> Cell | None:
        if 0 <= row < len(self.rows) and 0 <= col < len(self.rows[row]):
            return self.rows[row][col]
        return None

    def value(self, row: int, col: int) -> Any:
        cell = self.cell(row, col)
        return None if cell is None else cell.value

    def columns(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    def header(self) -> list[str]:
        if not self.rows:
            return []
        return [c.text or ("" if c.value is None else str(c.value))
                for c in self.rows[0]]

    def as_values(self, *, errors: bool = True) -> list[list[Any]]:
        out = []
        for row in self.rows:
            values = []
            for cell in row:
                if cell.error and errors:
                    raise FormulaError(cell.error)
                values.append(cell.value)
            out.append(values)
        return out


# ---------------------------------------------------------------------------
# cell references
# ---------------------------------------------------------------------------

_REF = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")


def col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def split_ref(ref: str) -> tuple[int, int]:
    match = _REF.match(ref.strip())
    if match is None:
        raise FormulaError(f"{ref!r} is not a cell reference")
    return int(match.group(2)) - 1, col_index(match.group(1))


# ---------------------------------------------------------------------------
# a small safe formula evaluator
# ---------------------------------------------------------------------------

_ALLOWED_CALLS = {
    "SUM", "MIN", "MAX", "AVERAGE", "ABS", "ROUND", "IF", "AND", "OR",
    "NOT", "SQRT", "EXP", "LN", "LOG", "LOG10", "POWER", "CELL", "RANGE",
}
_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
_CMPOPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _flatten(values: Any) -> list[float]:
    out: list[float] = []
    stack = list(values) if isinstance(values, (list, tuple)) else [values]
    while stack:
        value = stack.pop(0)
        if isinstance(value, (list, tuple)):
            stack[0:0] = list(value)
        elif isinstance(value, bool):
            out.append(1.0 if value else 0.0)
        elif isinstance(value, (int, float)):
            out.append(float(value))
        elif value is None:
            out.append(0.0)
        else:
            raise FormulaError(f"a text value ({value!r}) is not a number")
    return out


class _Formula:
    """Translate a spreadsheet formula to Python, validate it, run it."""

    def __init__(self, sheet: Sheet):
        self.sheet = sheet
        self._resolving: set[tuple[int, int]] = set()

    def cell_value(self, row: int, col: int) -> Any:
        cell = self.sheet.cell(row, col)
        if cell is None:
            return 0.0
        if cell.error:
            raise FormulaError(cell.error)
        if cell.formula and cell.value is None:
            self._resolve(row, col)
        return cell.value

    def _resolve(self, row: int, col: int) -> Any:
        cell = self.sheet.cell(row, col)
        if cell is None or not cell.formula:
            return None if cell is None else cell.value
        key = (row, col)
        if key in self._resolving:
            raise FormulaError("a formula refers to itself indirectly")
        self._resolving.add(key)
        try:
            cell.value = self.evaluate(cell.formula)
            return cell.value
        finally:
            self._resolving.discard(key)

    def evaluate(self, formula: str) -> Any:
        text = formula.strip()
        if text.lower().startswith("of:="):
            text = text[3:]
        if text.startswith("="):
            text = text[1:]
        text = re.sub(r"\b_xlfn\.", "", text)
        if not text:
            raise FormulaError("an empty formula")
        return self._eval_text(text)

    def _eval_text(self, text: str) -> Any:
        expr = self._translate(text)
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as exc:
            raise FormulaError(f"cannot read formula {text!r}") from exc
        self._validate(tree)
        try:
            return eval(compile(tree, "<formula>", "eval"),  # noqa: S307
                        {"__builtins__": {}},
                        {"CELL": self._cell, "RANGE": self._range,
                         "SUM": _fn_sum, "MIN": _fn_min, "MAX": _fn_max,
                         "AVERAGE": _fn_average, "ABS": abs, "ROUND": round,
                         "IF": _fn_if, "AND": _fn_and, "OR": _fn_or,
                         "NOT": _fn_not, "SQRT": _fn_sqrt, "EXP": _fn_exp,
                         "LN": _fn_ln, "LOG": _fn_log, "LOG10": _fn_log10,
                         "POWER": _fn_power})
        except FormulaError:
            raise
        except (ArithmeticError, ValueError, TypeError) as exc:
            raise FormulaError(f"formula {text!r} did not evaluate: {exc}") from exc

    def _translate(self, text: str) -> str:
        # Replace references with opaque placeholders before any function
        # name is considered: `LOG10(` is a function, not cell LOG10, and a
        # second regex pass must not rewrite the strings we inserted.
        tokens: list[str] = []

        def stash(value: str) -> str:
            tokens.append(value)
            return f"\x00{len(tokens) - 1}\x00"

        # Text literals are data, not references.
        text = re.sub(r'"(?:[^"]|"")*"', lambda m: stash(m.group(0)), text)
        # ODS writes references as [.A1] / [.A1:.B2].
        text = text.replace(";", ",")
        text = re.sub(
            r"\[\.?\$?([A-Za-z]{1,3})\$?(\d+)\s*:\s*\.?\$?([A-Za-z]{1,3})\$?(\d+)\]",
            lambda m: stash(
                f'RANGE("{m.group(1)}{m.group(2)}","{m.group(3)}{m.group(4)}")'),
            text)
        text = re.sub(
            r"\[\.?\$?([A-Za-z]{1,3})\$?(\d+)\]",
            lambda m: stash(f'CELL("{m.group(1)}{m.group(2)}")'),
            text)
        # A1:B2 ranges become RANGE("A1","B2").
        text = re.sub(
            r"\$?([A-Za-z]{1,3})\$?(\d+)\s*:\s*\$?([A-Za-z]{1,3})\$?(\d+)",
            lambda m: stash(
                f'RANGE("{m.group(1)}{m.group(2)}","{m.group(3)}{m.group(4)}")'),
            text)
        # A single reference; exclude a function call such as LOG10(.
        text = re.sub(
            r"(?<![A-Za-z0-9_])\$?([A-Za-z]{1,3})\$?(\d+)(?![A-Za-z0-9_])(?!\s*\()",
            lambda m: stash(f'CELL("{m.group(1)}{m.group(2)}")'),
            text)
        text = text.replace("^", "**")
        for index, token in enumerate(tokens):
            text = text.replace(f"\x00{index}\x00", token)
        return text

    @staticmethod
    def _validate(tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                if node.id not in _ALLOWED_CALLS:
                    raise FormulaError(
                        f"formula uses the name {node.id!r}, which the "
                        "import evaluator does not support")
            elif isinstance(node, ast.Call):
                if not (isinstance(node.func, ast.Name)
                        and node.func.id in _ALLOWED_CALLS):
                    raise FormulaError(
                        "formula calls a function the import evaluator "
                        "does not support")
            elif isinstance(node, (ast.Expression, ast.Load, ast.Constant,
                                   ast.Tuple, ast.List, ast.UnaryOp,
                                   ast.BinOp, ast.Compare, ast.operator,
                                   ast.unaryop, ast.cmpop)):
                continue
            elif isinstance(node, ast.keyword):
                raise FormulaError("named arguments are not supported")
            else:
                raise FormulaError(
                    f"formula uses {type(node).__name__}, which the import "
                    "evaluator does not support")

    def _cell(self, ref: str) -> Any:
        return self.cell_value(*split_ref(ref))

    def _range(self, start: str, end: str) -> list[float]:
        r1, c1 = split_ref(start)
        r2, c2 = split_ref(end)
        lo_r, hi_r = sorted((r1, r2))
        lo_c, hi_c = sorted((c1, c2))
        if (hi_r - lo_r + 1) * (hi_c - lo_c + 1) > 100_000:
            raise FormulaError("a formula range is too large")
        return [float(self.cell_value(r, c) or 0.0)
                for r in range(lo_r, hi_r + 1)
                for c in range(lo_c, hi_c + 1)]


def _fn_sum(*values: Any) -> float:
    return float(sum(_flatten(values)))


def _fn_min(*values: Any) -> float:
    vals = _flatten(values)
    return min(vals) if vals else 0.0


def _fn_max(*values: Any) -> float:
    vals = _flatten(values)
    return max(vals) if vals else 0.0


def _fn_average(*values: Any) -> float:
    vals = _flatten(values)
    return sum(vals) / len(vals) if vals else 0.0


def _fn_if(condition: Any, yes: Any, no: Any = 0.0) -> Any:
    return yes if bool(condition) else no


def _fn_and(*values: Any) -> bool:
    return all(bool(v) for v in _flatten(values))


def _fn_or(*values: Any) -> bool:
    return any(bool(v) for v in _flatten(values))


def _fn_not(value: Any) -> bool:
    return not bool(value)


def _fn_sqrt(value: Any) -> float:
    return float(value) ** 0.5


def _fn_exp(value: Any) -> float:
    import math
    return math.exp(float(value))


def _fn_ln(value: Any) -> float:
    import math
    return math.log(float(value))


def _fn_log(value: Any, base: float = 10.0) -> float:
    import math
    return math.log(float(value), float(base))


def _fn_log10(value: Any) -> float:
    import math
    return math.log10(float(value))


def _fn_power(value: Any, power: Any) -> float:
    return float(value) ** float(power)


# ---------------------------------------------------------------------------
# xlsx
# ---------------------------------------------------------------------------

_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
    "of": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}


def _xlsx_text(node: ET.Element) -> str:
    return "".join(t.text or "" for t in node.findall(".//main:t", _NS))


def _xlsx_workbook(zf: zipfile.ZipFile) -> tuple[list[tuple[str, str]], int]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    active = 0
    view = workbook.find("main:bookViews/main:workbookView", _NS)
    if view is not None:
        try:
            active = int(view.get("activeTab", "0"))
        except ValueError:
            active = 0
    rels: dict[str, str] = {}
    try:
        root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        for rel in root.findall("pkg:Relationship", _NS):
            rels[rel.get("Id", "")] = rel.get("Target", "")
    except KeyError:
        pass
    sheets: list[tuple[str, str]] = []
    for sheet in workbook.findall("main:sheets/main:sheet", _NS):
        rid = sheet.get(f"{{{_NS['rel']}}}id", "")
        target = rels.get(rid, "")
        if target:
            target = target.lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
        sheets.append((sheet.get("name", ""), target))
    return sheets, active


def _xlsx_cell(cell: ET.Element, shared: list[str]) -> Cell:
    kind = cell.get("t", "n")
    cached_text = cell.findtext("main:v", default=None, namespaces=_NS)
    formula = cell.findtext("main:f", default=None, namespaces=_NS)
    raw: Any = None
    text = ""
    if kind == "s":
        try:
            raw = shared[int(cached_text or 0)]
        except (ValueError, IndexError):
            raw = ""
        text = str(raw)
    elif kind == "inlineStr":
        node = cell.find("main:is", _NS)
        raw = _xlsx_text(node) if node is not None else ""
        text = str(raw)
    elif kind == "b":
        raw = str(cached_text) in ("1", "true", "TRUE")
        text = "TRUE" if raw else "FALSE"
    elif kind == "str":
        raw = cached_text or ""
        text = str(raw)
    elif kind == "e":
        raw = None
        text = str(cached_text or "")
    else:
        raw = _number(cached_text)
        text = "" if raw is None else str(raw)
    cached = raw if (formula is None or kind != "n") else _number(cached_text)
    cached = raw
    value = cached
    if formula is not None and cached_text is None:
        value = None                     # evaluated later
    return Cell(raw=raw, formula=formula, cached=cached, text=text,
                kind=kind, value=value)


def _number(text: Any) -> float | int | None:
    if text is None or text == "":
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _read_xlsx(path: Path, sheet: str | None) -> Sheet:  # noqa: C901, PLR0912
    if not zipfile.is_zipfile(path):
        raise SheetError(f"{path.name} is not an Excel workbook")
    with zipfile.ZipFile(path) as zf:
        try:
            sheets, active = _xlsx_workbook(zf)
        except KeyError as exc:
            raise SheetError(f"{path.name} has no workbook.xml") from exc
        if not sheets:
            raise SheetError(f"{path.name} has no worksheets")
        chosen = None
        if sheet:
            chosen = next((s for s in sheets if s[0].casefold() == sheet.casefold()),
                          None)
            if chosen is None:
                raise SheetError(f"{path.name} has no sheet named {sheet!r}")
        else:
            chosen = sheets[active] if 0 <= active < len(sheets) else sheets[0]
        name, target = chosen
        if not target or target not in zf.namelist():
            raise SheetError(f"{path.name}: sheet {name!r} is missing")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            shared = [_xlsx_text(si) for si in root.findall("main:si", _NS)]
        root = ET.fromstring(zf.read(target))
        rows: list[list[Cell]] = []
        for row_node in root.findall("main:sheetData/main:row", _NS):
            row: list[Cell] = []
            for cell_node in row_node.findall("main:c", _NS):
                ref = cell_node.get("r", "")
                try:
                    _r, col = split_ref(ref)
                except FormulaError:
                    col = len(row)
                while len(row) < col:
                    row.append(Cell())
                row.append(_xlsx_cell(cell_node, shared))
            rows.append(row)
            if len(rows) >= MAX_ROWS:
                break
    out = Sheet(name=name, rows=rows)
    _resolve_formulas(out)
    return out


# ---------------------------------------------------------------------------
# ods
# ---------------------------------------------------------------------------

_OFFICE_VALUE = f"{{{_NS['of']}}}value"
_OFFICE_TYPE = f"{{{_NS['of']}}}value-type"
_TABLE_FORMULA = f"{{{_NS['table']}}}formula"
_TABLE_REPEAT_COLS = f"{{{_NS['table']}}}number-columns-repeated"
_TABLE_REPEAT_ROWS = f"{{{_NS['table']}}}number-rows-repeated"


def _ods_text(cell: ET.Element) -> str:
    return "".join("".join(p.itertext())
                   for p in cell.findall("text:p", _NS))


def _read_ods(path: Path, sheet: str | None) -> Sheet:  # noqa: C901, PLR0912
    if not zipfile.is_zipfile(path):
        raise SheetError(f"{path.name} is not an OpenDocument spreadsheet")
    with zipfile.ZipFile(path) as zf:
        try:
            root = ET.fromstring(zf.read("content.xml"))
        except KeyError as exc:
            raise SheetError(f"{path.name} has no content.xml") from exc
        tables = root.findall("of:body/of:spreadsheet/table:table", _NS)
        if not tables:
            raise SheetError(f"{path.name} has no sheets")
        table = None
        if sheet:
            table = next((t for t in tables
                          if t.get(f"{{{_NS['table']}}}name", "").casefold()
                          == sheet.casefold()), None)
            if table is None:
                raise SheetError(f"{path.name} has no sheet named {sheet!r}")
        else:
            table = tables[0]
        name = table.get(f"{{{_NS['table']}}}name", "Sheet1")
        rows: list[list[Cell]] = []
        for row_node in table.findall("table:table-row", _NS):
            row: list[Cell] = []
            for cell_node in row_node.findall("table:table-cell", _NS):
                repeat = _repeat(cell_node.get(_TABLE_REPEAT_COLS))
                formula = cell_node.get(_TABLE_FORMULA)
                kind = cell_node.get(_OFFICE_TYPE, "")
                text = _ods_text(cell_node)
                raw: Any = None
                if kind in ("float", "percentage", "currency"):
                    raw = _number(cell_node.get(_OFFICE_VALUE))
                elif kind == "boolean":
                    raw = str(cell_node.get(_OFFICE_VALUE, "")).lower() in (
                        "true", "1")
                elif kind == "date" or kind == "time":
                    raw = cell_node.get(_OFFICE_VALUE) or text
                elif text:
                    raw = text
                cell = Cell(raw=raw, formula=formula, cached=raw, text=text,
                            kind=kind, value=raw)
                if formula and cell_node.get(_OFFICE_VALUE) is None:
                    cell.value = None
                for _ in range(min(repeat, MAX_COLUMNS)):
                    row.append(cell if repeat == 1 else Cell(
                        raw=cell.raw, formula=cell.formula, cached=cell.cached,
                        text=cell.text, kind=cell.kind, value=cell.value))
            repeat_rows = _repeat(row_node.get(_TABLE_REPEAT_ROWS))
            # Empty repeated rows are how a spreadsheet pads its grid; do
            # not turn a 1,048,576-row trailing pad into memory.
            if not any(c.text or c.value is not None for c in row):
                repeat_rows = min(repeat_rows, 1)
            for _ in range(min(repeat_rows, MAX_ROWS - len(rows))):
                rows.append([Cell(raw=c.raw, formula=c.formula,
                                  cached=c.cached, text=c.text, kind=c.kind,
                                  value=c.value) for c in row])
            if len(rows) >= MAX_ROWS:
                break
    out = Sheet(name=name, rows=rows)
    _resolve_formulas(out)
    return out


def _repeat(value: str | None) -> int:
    try:
        return max(1, int(value or 1))
    except ValueError:
        return 1


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------

def _resolve_formulas(sheet: Sheet) -> None:
    """Evaluate formula cells that had no workbook cache, once."""
    evaluator = _Formula(sheet)
    for row in sheet.rows:
        for cell in row:
            if cell.formula and cell.value is None and not cell.error:
                try:
                    cell.value = evaluator.evaluate(cell.formula)
                except FormulaError as exc:
                    cell.error = str(exc)


def read_sheet(path: str | Path, sheet: str | None = None) -> Sheet:
    """Read the named (or active/first) sheet of an `.xlsx` or `.ods` file."""
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".xlsx":
        return _read_xlsx(source, sheet)
    if suffix == ".ods":
        return _read_ods(source, sheet)
    raise SheetError(
        f"{source.name}: expected an .xlsx or .ods spreadsheet, got "
        f"{suffix or 'no extension'}")
