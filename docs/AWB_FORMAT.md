# AWB project interchange (`.awb`)

**Status:** import/export implemented in `raptor/core/fileio/awb.py`; format
checked against Availability Workbench 5.0 (AWB, Isograph).

RAPTOR Studio can read and write the project file of Isograph's Availability
Workbench 5.0 (`.awb`).  The conversion is designed for RBD/AvSim projects:
blocks, nodes, links, hierarchies, failure models and corrective tasks.  It
does **not** try to be a universal AWB database converter.  Everything the
destination cannot represent is counted and named in a summary before the
conversion is accepted (and, with `--strict`, before anything is written).

```sh
# RAPTOR -> AWB
raptor convert model.rbds model.awb
raptor convert model.rbds model.awb --strict          # refuse on any loss
raptor convert model.rbds model.awb -f json           # machine-readable report
raptor convert model.rbds model.awb --report report.md

# AWB -> RAPTOR
raptor convert project.awb model.rbds
raptor convert project.awb model.rbd
```

AWB's own demonstration mode can *open and simulate* a project but blocks
**Save** and **Export**.  RAPTOR writes the `.awb` file directly, so the
exporter does not need an AWB licence or a running AWB.  To save a file from
AWB itself you need an AWB licence; that is a licensing restriction in AWB,
not in the converter.

---

## 1. The file at a glance

An AWB 5.0 `.awb` file is a little-endian binary table store.  It is not a
ZIP, SQLite or Access database.  The layout is:

```
uint32      table count
table       repeated `table count` times
```

A table record is:

```
string      table name
uint32      column count
column      repeated `column count` times:
  string      column name
  uint32      column type (0 string, 1 int32, 2 double, 3 bool/byte)
uint32      row count
cell        each row, values in column order
```

A `string` is a varint byte length followed by that many UTF-8 bytes.  A
varint is the usual little-endian base-128 integer: seven data bits per byte,
high bit set on every byte except the last.  An `int32` is four bytes,
two's-complement, little-endian.  A `double` is IEEE-754 binary64,
little-endian.  A bool is one byte; AWB writes 0 or 1.

There is no per-table byte length and no end marker: the reader walks the
tables and cells in order.  A truncated file, an unknown column type, or
trailing bytes are reported as `AwbFormatError` rather than guessed at.

The six tables RAPTOR writes are:

| Table | Purpose |
|---|---|
| `Project` | project settings, lifetime, simulation count, module state |
| `FailureModels` | failure law per block (or shared by identical blocks) |
| `FailureModelCorrectiveTasks` | repair task per failure model |
| `RbdBlocks` | RBD blocks and hierarchy superblocks |
| `RbdNodes` | RBD nodes and K-of-N votes |
| `RbdConnections` | links between blocks and nodes |

AWB adds the tables its other modules need when it opens the project.  A
minimal six-table project was opened, validated and simulated by AWB 5.0 in
demonstration mode; the tables below are the ones that matter to AvSim.

---

## 2. Pages and hierarchies

AWB's page number is the **row index of the superblock that owns the page**.
This is the key to the whole RBD mapping:

* `RbdBlocks` row 0 is the project root.  Its `Page` is `-1`, its `Super` is
  `1`, and it owns page `0` (the top-level diagram).
* A superblock row at index `i` owns page `i`.  Its own `Page` is the page it
  is drawn on (the parent page); its contents are the rows whose `Page` is
  `i`.
* An ordinary block's `Page` is the page it is drawn on.
* An `RbdNode`'s `Page` is the page it is drawn on.
* An `RbdConnection`'s `Page` is the page its two endpoints are drawn on.

So a top-level block has `Page = 0`; a block inside the hierarchy whose
superblock is row 3 has `Page = 3`; and a nested hierarchy's superblock has
`Page = 3` while its own contents have `Page = <its row index>`.

RAPTOR's hierarchy model maps one-to-one:

| RAPTOR | AWB |
|---|---|
| `HIERARCHY_DATA` row | `RbdBlocks` row with `Super = 1` |
| hierarchy id | superblock row index (its page) |
| hierarchy's parent | the `Page` of the superblock row |
| `in` / `out` marker nodes | ordinary `RbdNodes` on the hierarchy page |
| links inside a hierarchy | `RbdConnections` whose `Page` is the hierarchy page |
| links to a hierarchy | `RbdConnections` whose endpoint is the superblock row |

When an AWB page has no explicit input/output nodes, the importer synthesises
AWB's `in`/`out` (or RAPTOR's Start/End) and wires the page's first and last
items to them.  If a page has several open ends, an ordinary junction node is
added so RAPTOR's one-link-per-block rule is kept.

---

## 3. `RbdBlocks` columns RAPTOR uses

The full table has 101 columns in AWB 5.0.  The important ones are:

| Column | Type | Meaning |
|---|---|---|
| `Id` | string | label, unique in the project (40 characters) |
| `Description` | string | block description |
| `Super` | bool | 1 = hierarchy superblock |
| `Page` | int | page the block is drawn on; `-1` on the root |
| `FailureModel` | int | row index in `FailureModels`, `-1` for none |
| `Consequence` | int | row index in `Consequences`, `-1` for none |
| `Bom` / `Bitmap` | int | row index, `-1` for none |
| `XPosition`, `YPosition` | double | diagram position |
| `Width`, `Height` | double | symbol size (RAPTOR writes 100 x 60) |
| `LogicMode`, `StandbyMode` | int | 0 = normal, no standby |
| `NonMaintainable` | bool | 1 = a failure is never repaired |
| `NonOperational` | bool | 1 = block starts out of service |
| `MaxCapacity` | double | capacity product 1 (not exported) |
| `MaxCapacity2..4` | double | further capacity products |
| `Guid` | string | stable object id (AWB accepts a fresh UUID) |

`RbdNodes` has `Id`, `Page`, `Vote`, `XPosition`, `YPosition`,
`OperationalCapacityTarget` (and the `2..4` variants), `LocalStandby`,
`NotLogic`, `BackgroundColor` and `Guid`.  `Vote` is the K of a K-of-N node.

`RbdConnections` has `Id`, `Page`, `Type`, `Color`,
`InputObjectType`, `InputObjectIndex`, `OutputObjectType`,
`OutputObjectIndex` and `Guid`.  The object types are:

| Code | Object |
|---:|---|
| 20 | ordinary block (`RbdBlocks` row) |
| 21 | repeat block (`RbdRepeatBlocks` row) |
| 22 | node (`RbdNodes` row) |

The index is the **zero-based row index** of the object in its own table, not
the `Id` label.

---

## 4. Failure and repair laws

### AWB failure-model codes

`FailureModels.FmDistribution` is an enum, not a RAPTOR code:

| Code | AWB law | Parameters RAPTOR writes |
|---:|---|---|
| 0 | Exponential | `FmMttf` = mean time to failure |
| 1 | Weibull | `FmEta1` = scale, `FmBeta1` = shape, `FmGamma1` = location |
| 2 | Bi-Weibull | three modes; RAPTOR keeps the first |
| 3 | Tri-Weibull | three modes; RAPTOR keeps the first |
| 4 | Fixed | `FmMttf` = fixed time |
| 5 | Lognormal | `FmMttf` = mean, `FmStd` = standard deviation |
| 6 | Normal | `FmMttf` = mean, `FmStd` = standard deviation |
| 7 | Buffer | stored capacity; no RAPTOR equivalent |
| 8 | Phased Bi-Weibull | phased; RAPTOR keeps the first mode |
| 9 | Phased Tri-Weibull | phased; RAPTOR keeps the first mode |

All times in AWB are **hours**.  RAPTOR converts from its model unit with
`raptor/core/units_conv.py` (`days` = 24, `years` = 8760, etc.) and records
the conversion in the summary.

RAPTOR reads the same enum in reverse.  A multi-mode or buffer law becomes
its first Weibull mode and is listed under "multi-mode or buffer failure
distribution".  A dormant AWB failure (`FmDormant = 1`) becomes a RAPTOR
block with no repair.  A non-zero start-up failure probability is reported
but cannot be modelled by RAPTOR.

### Repair tasks

`FailureModelCorrectiveTasks` is one row per failure model.  RAPTOR writes:

* `FailureModel` = the failure-model row index,
* `TaskDuration` = mean repair time (RAPTOR's repair law mean, or the fixed
  duration) in hours,
* `RampTime` = 0,
* `Distribution` = 2 with `Std` = 0 (AWB's deterministic/fixed task form),
* `TaskId` = `CM1`, `CM2`, ... and a fresh `Guid`.

RAPTOR's exponential (4), Weibull (15), lognormal (7), normal (8) and fixed
(19) failure laws map exactly to AWB 0, 1, 5, 6 and 4.  RAPTOR's "None" (18)
becomes a fixed time of 9e8 hours (never fails).  Anything else (gamma,
triangular, Pearson, ...) becomes an exponential with the same mean and is
named in the summary.

RAPTOR's repair law is a distribution more often than not.  AWB's task
duration in the generated file is therefore the distribution's **mean**; the
summary names this as "repair distribution simplified to a fixed duration".
On import, AWB task codes are read as:

| Code | RAPTOR repair law |
|---:|---|
| 0 | Exponential (`TaskDuration` as mean) |
| 1 | Lognormal (`TaskDuration`, `Std`) |
| 2 | Normal when `Std > 0`, otherwise Fixed |
| 3 | Weibull (`Beta`, `TaskDuration`, `Gamma`) |
| other | Fixed (`TaskDuration`) |

A block with `NonMaintainable = 1`, or a dormant failure, gets RAPTOR's
`repair_dist = 18` (no repair).

---

## 5. What the summary reports

Every conversion returns a report.  The CLI prints it in text, JSON or
Markdown; `--report FILE` also writes the Markdown.  The report has:

* `direction`, `source`, `target`;
* `counts` — blocks, nodes, hierarchies, links, failure models;
* `not_in_target` — features the **destination** has no equivalent for,
  with a count and a short explanation;
* `not_in_source` — the few features the source had that are also absent
  from the destination (used for information, not to block);
* `notes` and `warnings`.

### Features reported when exporting to AWB

| Feature | Why |
|---|---|
| failure events | AWB RBDs use blocks; events are not exported |
| diagram annotations | text boxes, rectangles and arrows are draw-only |
| rate definitions and formula-linked fields | values are written as plain numbers |
| triggers | AWB has no equivalent trigger logic |
| spare pools | AWB spare holdings are not populated by this export |
| resource pools | AWB labour/equipment are not populated |
| mission phases and per-element phase schedules | AWB phase rules are not populated |
| out-of-service elements | AWB `OutOfService` rules are not populated |
| capacity analysis and flow links | AWB capacity targets are not populated |
| block dependencies | AWB dependency rules are not populated |
| preventive maintenance | AWB scheduled tasks are not populated |
| age, restoration and growth settings | AWB initial age is 0 |
| block cost data | AWB cost fields are not populated |
| node standby/priority settings | AWB nodes carry only a vote |
| link switching and priority | AWB connections carry no switching data |
| link routing grips and view settings | AWB owns the diagram view |
| advisor state and print title block | RAPTOR-only metadata |
| other failure laws (gamma, triangular, ...) | simplified to an exponential with the same mean |
| non-fixed repair laws | simplified to a fixed task duration with the same mean |
| no-repair blocks | written as `NonMaintainable = 1` |
| no-failure blocks | written with a fixed time of 9e8 hours |

### Features reported when importing from AWB

| Feature | Why |
|---|---|
| consequences and cost models | RAPTOR has no financial/safety/environmental consequence model |
| mission phases and phase rules | AWB phase schedules are not imported |
| planned maintenance and inspection tasks | RAPTOR PM is not populated |
| spares and sparing rules | RAPTOR spare pools are not populated |
| labour and equipment resources | RAPTOR resource pools are not populated |
| RCM location hierarchy | the RCMCost module is not imported |
| fault trees | only RBDs are imported |
| Weibull sets | linked historical data is flattened |
| diagram bitmaps | AWB images are not imported |
| capacity flow and capacity targets | not imported |
| standby and NOT logic | RAPTOR nodes have no equivalent |
| block consequence links | not imported |
| dormant failures | modelled as never repaired |
| multi-mode/buffer failure laws | only the first Weibull mode is kept |
| non-maintainable blocks | RAPTOR repair is off |
| start-up failure probability | not modelled by RAPTOR |

Labels (`RbdLabels`) and repeat blocks (`RbdRepeatBlocks`) are **converted**,
not lost: labels become RAPTOR annotations, and a repeat block becomes a
never-failing block that is idle whenever the original block is down.

---

## 6. Column defaults and the six-table file

RAPTOR ships the six table schemas in
`raptor/core/fileio/awb_tables.json` (column names and types plus the neutral
project row).  The file is data, not a template project: rows are generated
from the RAPTOR model, so a generated `.awb` contains no Isograph sample
data.

The project row sets:

* `Id = "<PROJECTID>"`, `Description` = RAPTOR file name,
* `ProductVersion = "5.0"`, `ProjectVersionLong = "5.0.0.0"`,
* `LifeTime` = the RAPTOR run length in hours,
* `AvsimNoSimulationsRequested` = the RAPTOR trial count,
* `AvsimCurrentRbdPage = 0`, `WeibullCurrentWeibullSet = -1`,
* the capacity/importance/result-cache fields at neutral values.

AWB 5.0 accepts a fresh UUID in every `Guid` column and creates any missing
module table when the project is opened.

---

## 7. Verification

The format and mapping were checked against AWB 5.0.0.0 under Wine:

1. A six-table file with two series blocks, two nodes and three connections
   was opened by AWB without validation errors.
2. **Simulation → Start** completed 1000 replications on that file.
3. A RAPTOR model with two hierarchies (three blocks, eight nodes, ten
   links) exported by this converter was opened by AWB and showed the same
   hierarchy tree.
4. AWB's own `OilAndGasExample.awb` and `Example1.awb` were parsed, imported
   to `.rbds` and validated by `raptor validate` (0 errors; Example1's
   multi-link hierarchy items produce RAPTOR layout warnings).
5. The imported Example1 model was exported again and re-imported with the
   same element and link counts.

AWB demonstration mode was used for opening and simulation.  It does not
allow Save or Export, which is why the writer constructs the file directly
rather than driving AWB's own export dialog.

---

## 8. Limits and compatibility

* The reader accepts the AWB 5.0 table-store layout.  Older AWB projects with
  fewer columns are accepted; missing columns take the type default.
* The writer emits the six tables above.  AWB upgrades the project to its
  full module schema when it opens it; it may show validation warnings for
  fields that are not meaningful in a pure AvSim project, but the RBD data is
  complete.
* AWB IDs are limited to 40 characters and RAPTOR names to 20.  RAPTOR
  sanitises names and reports any rename; it never changes the source model.
* AWB uses the source model's x/y positions as-is and does not auto-layout.
  RAPTOR's canvas is often more compact than AWB's 100-unit block symbols;
  use AWB's own **Auto Layout** after opening a model whose blocks overlap.
* The conversion is one-way per invocation.  Round trips are not guaranteed
  to be byte-identical: AWB renumbers pages and rows, and RAPTOR renumbers
  element ids.  The **logic**, failure data, repair data and hierarchy
  structure are preserved.
* The `.awb` layout was reverse-engineered for interoperability; Isograph may
  change it in a future release.  A file that does not parse is reported as
  `AwbFormatError` with the table and byte offset where possible.
