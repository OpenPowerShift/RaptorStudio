# Spreadsheet data-table import

**Data ▸ Import Table…** reads an `.xlsx` or `.ods` sheet, validates every
row against the model, shows what would change, and applies the lot as one
undo step (`Ctrl+Z` restores the whole import).

## Sheet shape

The first row is a header. At least one key column and one field column are
required.

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

Case, spaces and punctuation are ignored, and a parenthetical unit
(`Failure Param 1 (hours)`) is ignored for matching. If both `id` and
`name` are present, the id wins and a mismatched name is a warning. An
unrelated column (Notes, Owner, …) is a warning, not a blocker; two
columns claiming the same field is an error. Stock on a block whose sparing
type is not Custom is stored but warned as having no effect.

## Formulas and calculated values

Each cell keeps three things: its formula, the workbook's cached result,
and (when there is no cache) a value from the reader's safe evaluator.

- A formula with a cached value uses that value — the number Excel or
  LibreOffice showed.
- A formula without a cached value is evaluated by a restricted arithmetic
  evaluator: numbers, cell references, `+ - * / ^ %`, comparisons,
  parentheses, and `SUM`, `MIN`, `MAX`, `AVERAGE`, `ABS`, `ROUND`, `IF`,
  `AND`, `OR`, `NOT`, `SQRT`, `EXP`, `LN`, `LOG`, `LOG10`, `POWER`.
- The formula is shown beside the imported value in the review dialog, so
  `=1/2400` and `0.000417` are visible together.
- Anything outside that subset (a macro, an external link, an unsupported
  function) is a row error. It is never imported as zero.

This supports the edit-and-reupload workflow: change parameters in the
spreadsheet, import again, and only the changed cells appear.

## Validation and applying

1. Every row is resolved to a block (missing/ambiguous names are errors).
2. Distributions must be in the model's offered list; parameters are
   checked with the same `validate_distro_params` rules as the Data Tables.
3. A distribution change without its parameters is accepted only when the
   current parameters are valid for the new law; otherwise the row is
   refused.
4. The review dialog lists element, field, current value, imported value
   and formula, plus every error or warning. Apply is disabled while any
   error remains.
5. Apply changes only the affected `BLOCK_DATA` rows and refreshes the
   canvas. The command stores the before/after rows, so undo and redo are
   complete and never leave a half-applied table.

The import works on blocks on every hierarchy level, not only the level on
screen.
