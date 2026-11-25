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

The reader is bounded on purpose: a sheet is at most `MAX_ROWS` rows by
`MAX_COLUMNS` columns, a single XML part at most `MAX_XML_BYTES`, and a
formula at most `MAX_FORMULA_LEN` characters. A malformed archive (bad
ZIP, bad XML) raises :class:`SheetError`, never a raw parser exception.
"""
from __future__ import annotations

import ast
import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

MAX_ROWS = 20_000
MAX_COLUMNS = 512
#: A workbook can name cell SR1 in every row with ~50 bytes: padding to
#: that column would build 10M Cell objects for a sheet with one value per
#: row. Bound the padded rectangle, not just the bytes.
MAX_CELLS = 2_000_000
#: A single XML part of a workbook or ODS (`sheet1.xml`, `content.xml`, ...)
#: may not decompress beyond this. The entry's declared size is checked
#: before reading, so a compressed "zip bomb" cannot expand into memory.
MAX_XML_BYTES = 64 * 1024 * 1024
#: Reject a pathological formula before `ast.parse` recurses on it.
MAX_FORMULA_LEN = 10_000


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
    #: The 1-based spreadsheet row for each entry in `rows`, when the file
    #: records it (an `.xlsx` omits blank rows, so the two differ).
    row_numbers: list[int] = field(default_factory=list)

    def source_row(self, index: int) -> int:
        """The spreadsheet row number of `rows[index]`, for messages."""
        if 0 <= index < len(self.row_numbers) and self.row_numbers[index]:
            return self.row_numbers[index]
        return index + 1

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


def _short(text: str, limit: int = 80) -> str:
    """A formula as it appears in an error message: long formulas would
    otherwise fill the review dialog with one line."""
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _flatten(values: Any) -> list[float]:
    out: list[float] = []
    stack = list(values if isinstance(values, (list, tuple)) else [values])
    index = 0
    while index < len(stack):
        value = stack[index]
        index += 1
        if isinstance(value, (list, tuple)):
            stack[index:index] = list(value)
        elif isinstance(value, bool):
            out.append(1.0 if value else 0.0)
        elif isinstance(value, (int, float)):
            out.append(float(value))
        elif value is None:
            out.append(0.0)
        else:
            raise FormulaError(f"a text value ({value!r}) is not a number")
    return out


class _PowToPower(ast.NodeTransformer):
    """Rewrite ``a ** b`` (translated from `a ^ b`) as ``POWER(a, b)``.

    Python's ``int ** int`` builds an integer as large as the exponent asks:
    ``=9^9^9`` would hang and exhaust memory before any check could run.
    `POWER` converts to float, so an absurd exponent raises OverflowError,
    which the evaluator reports as a `FormulaError`.
    """

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.op, ast.Pow):
            return ast.Call(func=ast.Name(id="POWER", ctx=ast.Load()),
                            args=[node.left, node.right], keywords=[])
        return node


class _IfToTernary(ast.NodeTransformer):
    """Rewrite ``IF(test, yes, no)`` as Python's conditional expression.

    A spreadsheet evaluates only the branch it returns; a function call
    would evaluate both, so ``=IF(A1=0, 0, 1/A1)`` would fail on the
    division by zero that the formula exists to avoid.
    """

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if (isinstance(node.func, ast.Name) and node.func.id == "IF"
                and 2 <= len(node.args) <= 3 and not node.keywords):
            return ast.IfExp(
                test=node.args[0], body=node.args[1],
                orelse=node.args[2] if len(node.args) == 3
                else ast.Constant(0.0))
        return node


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
        if len(text) > MAX_FORMULA_LEN:
            raise FormulaError(
                f"formula is longer than {MAX_FORMULA_LEN} characters")
        expr = self._translate(text)
        try:
            tree = ast.parse(expr, mode="eval")
        except (SyntaxError, RecursionError) as exc:
            raise FormulaError(
                f"cannot read formula {_short(text)!r}") from exc
        tree = _PowToPower().visit(tree)
        tree = _IfToTernary().visit(tree)
        ast.fix_missing_locations(tree)
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
        except (ArithmeticError, ValueError, TypeError, NameError,
                RecursionError, MemoryError) as exc:
            raise FormulaError(
                f"formula {_short(text)!r} did not evaluate: {exc}") from exc

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
        # A reference into another sheet would silently read THIS sheet's
        # cell of the same address, so refuse it rather than import a
        # plausible wrong number.
        if "!" in text or re.search(r"\[[^\]]*[^\][:]\.[^\]]*\]", text):
            raise FormulaError(
                "a formula refers to another sheet, which the import "
                "evaluator does not support")
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
        # Excel's `%` is a postfix operator (50% is 0.5), not Python's
        # modulo. A stashed reference ends with the placeholder's NUL, so
        # both it and a closing bracket count as an operand here.
        text = re.sub(r"(?<=[\d)\x00])\s*%", "/100", text)
        # A spreadsheet writes equality as `=` and inequality as `<>`;
        # Python needs `==` and `!=` (and `<=`/`>=` are already Python).
        text = text.replace("<>", "!=")
        text = re.sub(r"(?<![<>=!])=(?!=)", "==", text)
        text = text.replace("^", "**")
        for index, token in enumerate(tokens):
            text = text.replace(f"\x00{index}\x00", token)
        return text

    @staticmethod
    def _validate(tree: ast.AST) -> None:
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                # A function name is allowed only where it is called:
                # a bare `SUM` would otherwise evaluate to the function.
                if node.id not in called:
                    raise FormulaError(
                        f"formula uses the name {node.id!r}, which the "
                        "import evaluator does not support")
            elif isinstance(node, ast.Call):
                if not (isinstance(node.func, ast.Name)
                        and node.func.id in _ALLOWED_CALLS):
                    raise FormulaError(
                        "formula calls a function the import evaluator "
                        "does not support")
            elif isinstance(node, ast.Constant):
                if isinstance(node.value, str) and len(node.value) > 200:
                    raise FormulaError("a formula string literal is too long")
                continue
            elif isinstance(node, (ast.Expression, ast.Load, ast.UnaryOp,
                                   ast.BinOp, ast.Compare, ast.IfExp,
                                   ast.operator, ast.unaryop, ast.cmpop)):
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
        numbers: list[float] = []
        for r in range(lo_r, hi_r + 1):
            for c in range(lo_c, hi_c + 1):
                value = self.cell_value(r, c)
                # SUM/AVERAGE in Excel ignore blank and text cells rather
                # than failing the whole formula; follow that here.
                if isinstance(value, bool):
                    numbers.append(1.0 if value else 0.0)
                elif isinstance(value, (int, float)):
                    number = float(value)
                    if math.isfinite(number):
                        numbers.append(number)
        return numbers


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
    number = float(value)
    if number < 0:
        raise FormulaError("SQRT of a negative number")
    return math.sqrt(number)


def _fn_exp(value: Any) -> float:
    try:
        return math.exp(float(value))
    except OverflowError:
        raise FormulaError("the formula result is too large") from None


def _fn_ln(value: Any) -> float:
    return math.log(float(value))


def _fn_log(value: Any, base: float = 10.0) -> float:
    return math.log(float(value), float(base))


def _fn_log10(value: Any) -> float:
    return math.log10(float(value))


def _fn_power(value: Any, power: Any) -> float:
    try:
        result = float(value) ** float(power)
    except OverflowError:
        raise FormulaError("the formula result is too large") from None
    if isinstance(result, complex):
        # a negative base with a fractional exponent: Excel reports #NUM!
        raise FormulaError("a negative value cannot be raised to a "
                           "fractional power")
    return float(result)


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


def _xml_part(zf: zipfile.ZipFile, name: str, filename: str) -> ET.Element:
    """Parse one XML part, with a size cap and every parser error turned
    into a `SheetError` (the GUI shows it, so it must be readable)."""
    try:
        info = zf.getinfo(name)
    except KeyError as exc:
        raise SheetError(f"{filename} is missing {name}") from exc
    if info.file_size > MAX_XML_BYTES:
        raise SheetError(
            f"{filename}: {name} is larger than "
            f"{MAX_XML_BYTES // (1024 * 1024)} MB uncompressed; refusing to "
            "read it")
    try:
        return ET.fromstring(zf.read(name))
    except (ET.ParseError, zipfile.BadZipFile) as exc:
        raise SheetError(f"{filename}: {name} is not readable: {exc}") from exc


def _xlsx_text(node: ET.Element) -> str:
    return "".join(t.text or "" for t in node.findall(".//main:t", _NS))


def _xlsx_workbook(zf: zipfile.ZipFile,
                   filename: str) -> tuple[list[tuple[str, str]], int]:
    workbook = _xml_part(zf, "xl/workbook.xml", filename)
    active = 0
    view = workbook.find("main:bookViews/main:workbookView", _NS)
    if view is not None:
        try:
            active = int(view.get("activeTab", "0"))
        except ValueError:
            active = 0
    rels: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in zf.namelist():
        root = _xml_part(zf, "xl/_rels/workbook.xml.rels", filename)
        for rel in root.findall("pkg:Relationship", _NS):
            rels[rel.get("Id", "")] = rel.get("Target", "")
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
    # An error result (#DIV/0!, #N/A, ...) is data the import must not
    # silently read as blank -- and must not try to re-evaluate either.
    error = (f"the cell shows {text or 'an error'}" if kind == "e" else "")
    value = raw if formula is None or cached_text is not None else None
    return Cell(raw=raw, formula=formula, cached=raw, text=text,
                kind=kind, value=value, error=error)


def _trim(row: list[Cell]) -> list[Cell]:
    """Drop trailing blank cells (a spreadsheet pads its grid with them)."""
    while row and not row[-1].text and row[-1].value is None \
            and not row[-1].formula and not row[-1].error:
        row.pop()
    return row


def _number(text: Any) -> float | int | None:
    if text is None or text == "":
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _xlsx_row(row_node: ET.Element, shared: list[str],
              filename: str) -> list[Cell]:
    try:
        row_number = int(row_node.get("r", "1"))
    except ValueError:
        row_number = 1
    row: list[Cell] = []
    for cell_node in row_node.findall("main:c", _NS):
        ref = cell_node.get("r", "")
        try:
            _r, col = split_ref(ref)
        except FormulaError:
            col = len(row)
        if col >= MAX_COLUMNS:
            # An empty stray cell far to the right is harmless; a cell with
            # content there would silently be dropped, so refuse the sheet.
            if not len(cell_node) and cell_node.get("t") is None:
                continue
            raise SheetError(
                f"{filename}: data in column {col + 1} is beyond the "
                f"{MAX_COLUMNS}-column import limit")
        if col * max(1, row_number) > MAX_CELLS:
            raise SheetError(
                f"{filename}: rows reach column {col + 1}; a sheet of this "
                f"shape would exceed the {MAX_CELLS:,}-cell import limit")
        while len(row) < col:
            row.append(Cell())
        cell = _xlsx_cell(cell_node, shared)
        if col < len(row):                # a repeated or out-of-order ref
            row[col] = cell
        else:
            row.append(cell)
    return row


def _xlsx_rows(sheet_data: ET.Element, shared: list[str],
               filename: str) -> tuple[list[list[Cell]], list[int]]:
    """The rows of one worksheet and their spreadsheet row numbers."""
    rows: list[list[Cell]] = []
    row_numbers: list[int] = []
    for row_node in sheet_data.findall("main:row", _NS):
        try:
            number = int(row_node.get("r", ""))
        except ValueError:
            number = 0
        trimmed = _trim(_xlsx_row(row_node, shared, filename))
        if trimmed:                   # a blank row is not a header or data
            rows.append(trimmed)
            row_numbers.append(number)
        if len(rows) > MAX_ROWS:
            raise SheetError(
                f"{filename} has more than {MAX_ROWS:,} rows; split it or "
                "raise the limit")
    return rows, row_numbers


def _read_xlsx(path: Path, sheet: str | None) -> Sheet:  # noqa: C901, PLR0912
    if not zipfile.is_zipfile(path):
        raise SheetError(f"{path.name} is not an Excel workbook")
    with zipfile.ZipFile(path) as zf:
        sheets, active = _xlsx_workbook(zf, path.name)
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
        if not target:
            raise SheetError(f"{path.name}: sheet {name!r} is missing")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = _xml_part(zf, "xl/sharedStrings.xml", path.name)
            shared = [_xlsx_text(si) for si in root.findall("main:si", _NS)]
        root = _xml_part(zf, target, path.name)
        sheet_data = root.find("main:sheetData", _NS)
        rows, row_numbers = _xlsx_rows(
            sheet_data if sheet_data is not None else root,
            shared, path.name)
    out = Sheet(name=name, rows=rows, row_numbers=row_numbers)
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
_TABLE_CELL = f"{{{_NS['table']}}}table-cell"
_TABLE_COVERED = f"{{{_NS['table']}}}covered-table-cell"
_TABLE_ROW = f"{{{_NS['table']}}}table-row"


def _ods_text(cell: ET.Element) -> str:
    return "".join("".join(p.itertext())
                   for p in cell.findall("text:p", _NS))


def _ods_cell(cell_node: ET.Element) -> Cell:
    """One ODS cell: its value, its formula and its cached result."""
    formula = cell_node.get(_TABLE_FORMULA)
    kind = cell_node.get(_OFFICE_TYPE, "")
    text = _ods_text(cell_node)
    raw: Any = None
    if kind in ("float", "percentage", "currency"):
        raw = _number(cell_node.get(_OFFICE_VALUE))
    elif kind == "boolean":
        raw = str(cell_node.get(_OFFICE_VALUE, "")).lower() in ("true", "1")
    elif kind == "date" or kind == "time":
        raw = cell_node.get(_OFFICE_VALUE) or text
    elif text:
        raw = text
    cell = Cell(raw=raw, formula=formula, cached=raw, text=text,
                kind=kind, value=raw)
    if formula and cell_node.get(_OFFICE_VALUE) is None:
        cell.value = None
    return cell


def _read_ods(path: Path, sheet: str | None) -> Sheet:  # noqa: C901, PLR0912
    if not zipfile.is_zipfile(path):
        raise SheetError(f"{path.name} is not an OpenDocument spreadsheet")
    with zipfile.ZipFile(path) as zf:
        root = _xml_part(zf, "content.xml", path.name)
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
        # `table.iter` is document order, so a header wrapped in
        # <table:table-header-rows> stays ahead of the data rows.
        for row_node in table.iter(_TABLE_ROW):
            row: list[Cell] = []
            for cell_node in row_node:
                if cell_node.tag == _TABLE_COVERED:
                    # A merged cell's hidden slots still occupy columns;
                    # count them as blanks so the columns stay aligned.
                    room = MAX_COLUMNS - len(row)
                    if room > 0:
                        row.extend(Cell() for _ in range(min(
                            _repeat(cell_node.get(_TABLE_REPEAT_COLS)), room)))
                    continue
                if cell_node.tag != _TABLE_CELL:
                    continue
                repeat = _repeat(cell_node.get(_TABLE_REPEAT_COLS))
                cell = _ods_cell(cell_node)
                room = MAX_COLUMNS - len(row)
                if room <= 0:
                    raise SheetError(
                        f"{path.name} has more than {MAX_COLUMNS:,} columns")
                for _ in range(min(repeat, room)):
                    row.append(cell if repeat == 1 else Cell(
                        raw=cell.raw, formula=cell.formula, cached=cell.cached,
                        text=cell.text, kind=cell.kind, value=cell.value))
            repeat_rows = _repeat(row_node.get(_TABLE_REPEAT_ROWS))
            row = _trim(row)
            # Empty repeated rows are how a spreadsheet pads its grid; do
            # not turn a 1,048,576-row trailing pad into memory.
            if not any(c.text or c.value is not None or c.formula for c in row):
                repeat_rows = min(repeat_rows, 1)
            if not row:
                continue
            for _ in range(min(repeat_rows, MAX_ROWS - len(rows))):
                rows.append([Cell(raw=c.raw, formula=c.formula,
                                  cached=c.cached, text=c.text, kind=c.kind,
                                  value=c.value, error=c.error) for c in row])
            if len(rows) >= MAX_ROWS:
                raise SheetError(
                    f"{path.name} has more than {MAX_ROWS:,} rows; split it "
                    "or raise the limit")
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
                except RecursionError:
                    cell.error = ("a chain of formulas refers to itself too "
                                  "deeply to evaluate")


def read_sheet(path: str | Path, sheet: str | None = None) -> Sheet:
    """Read the named (or active/first) sheet of an `.xlsx` or `.ods` file."""
    source = Path(path)
    suffix = source.suffix.lower()
    try:
        if suffix == ".xlsx":
            return _read_xlsx(source, sheet)
        if suffix == ".ods":
            return _read_ods(source, sheet)
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        # A truncated download, or a spreadsheet saved with some other
        # tool: report it, do not let a parser exception reach the GUI.
        raise SheetError(
            f"{source.name} is not a readable spreadsheet: {exc}") from exc
    raise SheetError(
        f"{source.name}: expected an .xlsx or .ods spreadsheet, got "
        f"{suffix or 'no extension'}")
