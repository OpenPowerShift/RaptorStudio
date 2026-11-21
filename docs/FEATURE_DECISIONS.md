# Four feature decisions

Status: `.rbdz` implemented in this branch; the other three are decided and
sequenced here. Each section separates the recommendation from the build.

---

## 1. `.rbdz`: an archive of a model and its results — **implemented**

**Decision:** keep `.rbds` as the plain model. Add `.rbdz` as a ZIP
container holding the same `.rbds` document plus the result files a run
already writes. Do not make `.rbdz` the default Save format; Save As picks
it deliberately, and plain Save keeps whatever the file already is.

**Compression:** ZIP with bzip2 for entries of 4 KB or more and deflate for
small metadata, with LZMA available as a "smallest" profile. On a real
400-run batch, End-of-Run text went 39.3 MB → 0.58 MB (bzip2) and the
475 MB availability plot went to 80 MB; a hand-written columnar codec could
beat the plot slightly but would add a parser, a binary schema and a new
reader for every future plot kind. Full measurements and the entry layout
are in [`RBDZ_FORMAT.md`](RBDZ_FORMAT.md).

**Opening:** `load_model_file` accepts `.rbdz`, so the GUI, CLI and MCP use
the same schema-checked model reader. The GUI then extracts result entries
to a temporary folder and restores them lazily (a 475 MB plot is not read
into RAM), including a saved `attribution.json` so Drivers do not replay
the log. A model-only archive is valid.

**Not in v1:** several named result sets in one archive, and a CLI
`--save-archive`. The manifest and entry layout leave room for both.

---

## 2. Component groups and tags

**Decision:** groups are **orthogonal metadata**, not a second hierarchy.
A hierarchy is simulation structure: it owns links, ports and the scope of
a run. A group is a saved view over the same model and may span
hierarchies. Making groups "instead of" hierarchy would force the engine
to answer questions it does not need to answer, and would make a group
membership change a structural edit.

Groups and tags are two views of one idea:

- **Tag** — a free-form label on an element (`pumps`, `rotating`,
  `safety-case-A`). Many per element, many elements per tag.
- **Group** — a named, optionally coloured set with a stable id. A group
  is either an explicit member list or a tag query. Explicit membership
  lets a user group two differently tagged elements; a tag query keeps a
  group current when new elements are tagged.

### Proposed `.rbds` shape (format version 5)

```json
"tags": {
  "block:3": ["pumps", "rotating"],
  "block:4": ["pumps", "standby"]
},
"groups": [
  {
    "id": "g_pumps",
    "name": "Pumps",
    "colour": "#2F80ED",
    "description": "All duty and standby pumps",
    "members": [{"kind": "block", "id": 3}, {"kind": "block", "id": 4}]
  },
  {
    "id": "g_rotating",
    "name": "Rotating equipment",
    "colour": "#9B51E0",
    "tag_query": "rotating"
  }
]
```

`kind` is one of `block`, `node`, `event`, `hierarchy`. Membership is
resolved live from tags for a query group; an explicit group keeps its
list, and deleting an element removes it from explicit groups on load (a
warning names the dangling member).

### Views

1. **Explorer:** a group/tag filter beside Find. Filtering dims rather than
   hides by default; a "Only these" switch hides the rest. Hierarchy
   folders stay visible so the structural context is not lost.
2. **Canvas:** "Show group" applies the existing result-overlay machinery:
   dim everything outside the group, keep links to the selected members.
   A second mode, "Group colours", outlines blocks by their first group's
   colour.
3. **Results:** Drivers, Weak Links and export tables gain a group filter.
   Rows aggregate by group as an extra grouping key beside `element`,
   `failure mode` and `hierarchy`.
4. **Editing:** *Group from selection* and *Group from hierarchy* commands;
   the Inspector gets a Tags field with completion. Groups are saved with
   the model, so they travel in `.rbds` and `.rbdz`.

### Why not element fields in the table

Tags live in one top-level map rather than a new column on every element
row. The `.rbds` rule is "each value stored once"; a tag map is one place,
works for blocks, nodes, events and hierarchies, and leaves the legacy
`.rbd` writer untouched (tags are dropped on `.rbd` save, with the same
loss warning the other `.rbds`-only fields get).

**Build order:** (1) format + schema + round-trip tests; (2) Explorer
filter and Inspector tags; (3) canvas dim/colour; (4) results grouping and
export columns; (5) group commands.

---

## 3. When to calculate downtime drivers

**Decision:** calculate once, at the end of the run, in the same worker
pass that already has the event log, and **store the attribution with the
results**. Keep the on-demand path as a fallback, with a progress bar and
Cancel.

Reasons:

- The Story and Overview name drivers as soon as results appear; a lazy
  pass means the first view either waits with no progress or shows a blank
  Story.
- The Charts tab, the canvas "Share of downtime" overlay and
  Recommend/Tornado all read the same attribution. Lazy calculation can
  repeat the replay several times per session.
- The event log is already the dominant memory cost of the batch. The
  replay is a single extra pass; doing it inside the run means the data is
  hot and the UI thread never stalls.
- An `.rbdz` should reopen with its drivers. Storing `attribution.json`
  makes that free.

The existing behaviour is the fallback half of this: small result sets
compute eagerly, sets over 200,000 log lines wait for the Drivers tab. This
branch adds a modal progress bar (run count, Cancel) to that lazy path and
saves the result in `.rbdz`. The remaining step is to run the same
`attribute_logs` call inside the background worker after the batch, before
`state["result"]` is published, and pass the finished attribution into
`set_results`. If the worker was stopped or the run had no event log, the
lazy path still works.

**Guard:** attribution needs the event log. When "Detailed event log" is
off, the Drivers tab should say so and offer the one-click "Turn on event
log and run again" path that other result-option hints already use.

---

## 4. Importing data tables from a spreadsheet

**Decision:** support it, but as a **reviewed import**, not a blind paste.
Read `.xlsx` and `.ods` in-process (both are ZIP+XML), validate every row
against the model and the distribution rules, show a diff, then apply all
changes as one undoable macro.

### Formula and calculated value

Both. A workbook cell can carry a formula and its cached result. On import:

- the **cached value** is used when the workbook was saved by Excel,
  LibreOffice or another spreadsheet application;
- if a cell has a formula but no cached value, evaluate the small, safe
  arithmetic subset (`+ - * / ^`, parentheses, `MIN`, `MAX`, `ABS`,
  `ROUND`, `IF`, and workbook-local cell refs) in a restricted evaluator —
  no macros, no external links, no file access;
- the formula text is kept beside the value and shown in the review, so the
  user can see that `0.00042` came from `=1/2400`;
- any cell the evaluator cannot resolve is a blocking row error, never a
  silent zero.

This covers the stated workflow: change parameters in the spreadsheet,
re-upload, and let the import validate and update the model. A workbook
that uses a macro or an external link is refused for that cell with the
reason.

### Sheet contract

The first row is the header. Required columns are a stable element key
(`id` or `name`) and one or more field columns. The importer accepts:

- the exact friendly `.rbds` field names (`fail_dist`, `fail_params`,
  `repair_params`, `stock`, `pm_freq`, `currency`, …);
- the columns the Data Tables show (`Failure Distribution`,
  `Failure Param 1`, …), mapped to the same fields;
- an optional `unit` column, converted with the same display-unit rules as
  the Data Tables.

A saved import record in `.rbds` keeps the source file's hash, sheet name,
column mapping and the target fields, so the next upload of the same
workbook reuses the mapping and reports only what changed. The workbook
itself is not embedded.

### Applying it

1. Parse and map; unknown columns and unknown element keys are errors
   listed by row, not warnings.
2. Validate every proposed value first (`validate_distro_params`, spare /
   PM / resource rules, numeric bounds) against a **copy** of the model.
3. Show a review dialog: element, field, old → new, formula, and the
   validation result. Rows can be unticked.
4. On Apply, `QUndoStack.beginMacro("Import data tables: <file>")`, dispatch
   the same per-field setters the Data Tables use (so every row is already
   an undo command), then `endMacro()`. A failure mid-apply aborts the
   macro and restores the pre-import state; the undo command list is never
   partly applied.
5. Mark the model dirty once, refresh validation once, and record the
   import in the model's metadata.

**Build order:** (1) `.xlsx`/`.ods` reader returning sheet cells with
formula and value; (2) safe formula evaluator; (3) field/column mapping and
dry-run validator; (4) review dialog; (5) the undo macro and import record.
