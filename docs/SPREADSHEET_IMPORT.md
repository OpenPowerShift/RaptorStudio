# Spreadsheet data-table import

**Data ▸ Import Table…** (also **File ▸ Import ▸ Import Table…**) reads an `.xlsx` or `.ods` sheet, validates every
row against the model, shows what would change, and applies the lot as one
undo step (`Ctrl+Z` restores the whole import).

**File ▸ Import ▸ Data Table Template…** writes a starter `.xlsx` or `.ods`
sheet for the open model (one row per block, with the field columns), ready
to edit and bring back with **File ▸ Import ▸ Import Table…**.

## Sheet shape

The first row with content is the header (blank rows above it are ignored).
At least one key column and one field column are required.

| Column | Meaning |
|---|---|
| `id` | block id, e.g. `101` |
| `name` | block name, when it is unique |
| `fail_dist` / `Failure Distribution` | failure law name (`Weibull`) or code |
| `fail_param_1`…`fail_param_3` / `F Param 1`… | failure parameters |
| `fail_params` | the parameters in one cell, comma/space separated |
| `repair_dist` / `R Dist` | repair law |
| `repair_param_1`…`repair_param_3` / `R Param 1`… | repair parameters |
| `repair_params` | repair parameters in one cell |
| `pm_freq` / `PM Frequency` | PM frequency |
| `uses_pm` / `Preventive Maintenance` | yes/no, true/false, 1/0 |
| `stock` / `Initial Stock` | initial stock (Custom sparing) |

Case, spaces and punctuation are ignored, and a parenthetical or bracketed
unit (`Failure Param 1 (hours)`, `Fail Param 1 [hours]`) is ignored for
matching. A trailing unit word also maps (`fail_param_1_hours`), but an
unrelated heading such as `Name of owner` does not become the block name.
If both `id` and `name` are present, the id wins and a mismatched name is a
warning. An unrelated column (Notes, Owner, …) is a warning, not a blocker;
two columns claiming the same field is an error. Stock on a block whose
sparing type is not Custom is stored but warned as having no effect. A block
listed on two rows is allowed, with a warning that the later row wins.

A completely empty sheet is an error; a header with no data rows is a
warning. Ids and stock must be whole numbers (a fractional value is
refused, not truncated).

The workbook's active sheet is read (the first sheet when the workbook has
no active tab). Save the sheet you want to import as the active one before
importing.

## Formulas and calculated values

Each cell keeps three things: its formula, the workbook's cached result,
and (when there is no cache) a value from the reader's safe evaluator.

- A formula with a cached value uses that value — the number Excel or
  LibreOffice showed.
- A formula without a cached value is evaluated by a restricted arithmetic
  evaluator: numbers, cell references, `+ - * / ^ %`, comparisons,
  parentheses, and `SUM`, `MIN`, `MAX`, `AVERAGE`, `ABS`, `ROUND`, `IF`,
  `AND`, `OR`, `NOT`, `SQRT`, `EXP`, `LN`, `LOG`, `LOG10`, `POWER`.
  `%` is Excel's postfix percent (`=10%*200` is 20), and text cells in a
  `SUM`/`AVERAGE` range are ignored, as in a spreadsheet.
- A cell whose cached result is an error (`#DIV/0!`, `#N/A`, …) is a row
  error. It is never read as blank.
- A formula that refers to another sheet (`Sheet2!A1`, `[Sheet2.A1]`) is a
  row error: evaluating it against this sheet would silently read the wrong
  cell.
- The formula is shown beside the imported value in the review dialog, so
  `=1/2400` and `0.000417` are visible together. The dialog's **Row**
  column is the spreadsheet row the value came from.
- Anything outside that subset (a macro, an external link, an unsupported
  function) is a row error. It is never imported as zero.

The evaluator has no file, network or macro access. It also refuses a
formula longer than 10,000 characters, a range over 100,000 cells, and an
exponent that would produce an astronomically large number.

This supports the edit-and-reupload workflow: change parameters in the
spreadsheet, import again, and only the changed cells appear.

## Limits

A sheet is read up to 20,000 rows and 512 columns. Data beyond those
limits, an XML part larger than 64 MB uncompressed, or a corrupt ZIP/XML
file is refused with a message rather than read into memory.

## Validation and applying

1. Every row is resolved to a block (missing/ambiguous names are errors;
   a repeated block warns and the later row wins).
2. Distributions must be in the model's offered list; parameters are
   checked with the same `validate_distro_params` rules as the Data Tables.
   A parameter list of more than three values is refused.
3. A distribution change without its parameters is accepted only when the
   current parameters are valid for the new law; otherwise the row is
   refused, and the law is not changed on its own.
4. The review dialog lists row, element, field, current value, imported
   value and formula, plus every error or warning (errors first). Apply is
   disabled while any error remains.
5. Apply changes only the affected `BLOCK_DATA` rows and refreshes the
   canvas. The command stores the before/after rows, so undo and redo are
   complete and never leave a half-applied table. Callers outside the GUI
   get the same guarantee: `apply_to_rbd` refuses a plan with errors.

The import works on blocks on every hierarchy level, not only the level on
screen.
