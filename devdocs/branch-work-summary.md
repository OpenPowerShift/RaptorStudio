# Branch work summary

This document indexes every workstream merged into the temporary
integration branch `test/large-model-work`. It is written for a reviewer:
each section says what problem the branch addressed, what changed, where
to look, and how to check it.

The integration branch itself only adds merge commits and the test/report
documents. Feature and fix commits live on the named branches.

## How to review a branch

```bash
git fetch --all
git log --oneline <branch>            # its own commits
git diff main...<branch> --stat       # files it changed
git diff main...<branch> -- <paths>   # focused review

# Run the branch's focused suite from the integration checkout
.venv/bin/python -m pytest <tests> -q
```

The repository venv is `.venv`. Use `PYTHONPATH="$PWD"` when running a
test from a separate worktree. Keep test runs sequential or at most a few
workers on this machine; several suites are GUI-heavy.

## Branch map

| Branch | Tip | Workstream |
| --- | --- | --- |
| `fix/batch-results-large` | `629410c5` | Large batch reports, streamed results, smooth results progress |
| `perf/hierarchy-navigation` | `bf381dbf` | Visible-level only parsing, hierarchy navigation performance |
| `feat/layout-size-aware` | `19a416e9` | Size-aware layout pitches and exact GUI extents |
| `feat/layout-direction` | `630d430f` | LR/RL/TB/BT layout, wrap bands, visible Auto Layout, faster annotation follow |
| `feat/routing-nodes` | `d2190c36` | First routing-junction implementation (replaced later by `feat/link-grip`) |
| `feat/analytic-dependency-state` | `44cf5f59` | Analytical solver `dependencies=up/down` |
| `fix/analytic-bdd-limit` | `a6038621` | 2,000,000-node BDD cap and grouped limits |
| `feat/analytic-hierarchy-scope` | `0f3af8a5` | Solve one hierarchy, Cancel button, dependency/BDD integration |
| `fix/analytic-polish` | `646fbef6` | Grouped BDD counts, GUI cancel test, movable junction test |
| `feat/link-grip` | `fb4d8285` | GUI-only routing drag points replacing model routing nodes |
| `fix/band-routing` | `59ec72ae` | Band hand-over down the leading corner with stubs |
| `fix/grip-delete` | `1b3758c7` | Click/right-click delete a routing point, link intact |
| `fix/grip-idle` | `91463138` | New point idle until dragged; Auto Layout clears points |
| `fix/max-band-help` | `3a4a2740` | Max band counts series elements, not marker columns |
| `fix/worker-thread-safety` | `754f5724` | No QAction reads or cyclic GC on the simulation worker |
| `feat/multi-drag` | `6cd22d08` | Multi-selection drags as one item |
| `fix/layout-bands` | `11385520` | Port constraints, Z/S bands, spacing, selection layout, label side |
| `fix/analytic-review` | `f3f92914` | Hierarchy labels/exports, boundary errors, limit bounds, MCP schema |
| `feat/explorer-expand` | `e918927e` | Chunked Expand all / Collapse all in Model Explorer |
| `fix/grip-coords` | `6fa432c5` | Routing-point menu model→scene coordinate fix |
| `fix/grip-snap` | `84554312` | Point starts on the link, snaps when dragged, exact route preserved |
| `feat/fev-responsiveness` | `071e1b49` | Failure Effects View performance, inspector, panel toggle, dock icons |
| `perf/gui-large-models` | `e6273e0c` | Large-level GUI interaction performance |
| `docs/final-report` | `284da0d1` | Test/report round-up |

## Results and reports

### `fix/batch-results-large` — `629410c5`

**Issue.** Large batches built the whole End-of-Run report in memory; the
results dock re-materialised the stream; population ran on the GUI thread
with repeated status repaints, so a fast batch flickered and large batches
used excessive memory.

**Built.**
- Streamed End-of-Run and plot data to disk-backed `ResultStream`s.
- Bounded previews instead of materialising the full stream.
- Weak metrics computed once per element/run instead of repeatedly.
- Results population progress is monotonic and throttle-painted; no
  per-stage status flicker.
- Background and synchronous paths share the same cap/stream rules.

**Review.** `tests/unit/test_results_large.py`,
`tests/unit/test_gui_results.py`,
`tests/unit/test_gui_feedback_regressions.py`.

### `perf/hierarchy-navigation` — `bf381dbf`

**Issue.** Entering/leaving a hierarchy reparsed every row and rebuilt
route/badge indexes for the whole model; large models paused on every
level change.

**Built.**
- Parse only the visible level; lazy whole-model name index.
- Batch label/link work and generation-safe route caches.
- Hierarchy slicing/routes/badges kept consistent across level changes.
- Overflowing numeric cells treated as unreadable rather than crashing.

**Review.** `tests/unit/test_hierarchy_navigation.py`,
`tests/unit/test_gui_hierarchy.py`, `tests/unit/test_gui_large_models.py`.

### `feat/layout-size-aware` — `19a416e9` and `feat/layout-direction` — `630d430f`

**Issue.** Fixed-size layout boxes overlapped long names; layout had no
direction or wrapping control; Auto Layout froze with no feedback; the
annotation-follow pass was quadratic inside a hierarchy.

**Built.**
- Size-aware column/row pitches from real text extents; scale agreement
  between headless and GUI layout.
- Directions `LR`, `RL`, `TB`, `BT`; maximum band wrapping.
- Visible Auto Layout status/wait cursor and a faster annotation follow.
- Layout/Max-band controls moved to the Arrange/Layout tab.

**Review.** `tests/unit/test_layout_sizes.py`,
`tests/unit/test_layout_direction.py`,
`tests/unit/test_layout_raptor_world.py`,
`tests/unit/test_autolayout_annotation_perf.py`.

## Routing points

### `feat/routing-nodes` — `d2190c36` (historical)

First attempt inserted a real `NODE_DATA` row for a dog-leg. It was made
transparent by marking `repCap` and contracting it in `engine.load`, which
worked but overloaded a public column and still required special engine
logic. **Superseded by `feat/link-grip`.**

### `feat/link-grip` — `fb4d8285`

**Issue.** Routing nodes looked and edited like real model nodes, changed
dependency/EFPA semantics, and were risky in capacity models.

**Built.**
- Routing points are GUI-only: no `NODE_DATA` row, no properties dialog,
  no EFPA/capacity/dependency effect.
- Stored in `.rbds` as `link_grips`; legacy `.rbd` drops them with the
  drawing layer.
- Drawn as a small point on the link, draggable, undoable.
- Capacity and simulation results are bit-identical with or without one.

**Review.** `tests/unit/test_routing_nodes.py`,
`tests/unit/test_capacity_e2e.py`, `tests/unit/test_rbds_schema.py`.

### `fix/band-routing` — `59ec72ae`

**Issue.** A wrapped band whose next element is upstream drew a long
backwards line through the diagram.

**Built.** Hand-over leaves the last element on a short stub, runs through
the band gap, turns at the next band's leading corner (left for LR, right
for RL, top for TB, bottom for BT), and enters the upstream port.

**Review.** `tests/unit/test_layout_direction.py`.

### `fix/grip-delete` — `1b3758c7`, `fix/grip-idle` — `91463138`

**Issue.** A routing point could not be removed cleanly; a new point
immediately rerouted the link; Auto Layout collapsed stale points on each
other.

**Built.**
- Click or right-click a point → delete it; the link remains.
- Undo restores the point.
- New point is inactive until dragged, so the original path is unchanged.
- Auto Layout removes the level's points in the same undo macro.

### `fix/grip-coords` — `6fa432c5`, `fix/grip-snap` — `84554312`

**Issue.** The context menu passes model units but the grip is drawn in
scene units, so the point landed off-canvas and appeared not to be added.
A point also started wherever the click landed and did not snap.

**Built.**
- Convert model coordinates to scene coordinates.
- Project the initial point onto the drawn link path.
- Snap movement with the same grid state as elements; snap off = free.
- An inactive point does not reroute the link; exact path preserved in
  straight, angled and orthogonal styles.

## Analytical solver

### `feat/analytic-dependency-state` — `44cf5f59`

**Built.** `dependencies=up|down` for the exact solver; up treats a
dependent element's own law as independent, down forces it out of the
success path. Choice is scoped to dependencies only.

### `fix/analytic-bdd-limit` — `a6038621`

**Built.** Hard cap raised to **2,000,000** BDD nodes (was 1,000,000).
Values above the cap are refused with a usage error rather than crashing;
GUI spin boxes show comma-grouped limits and the explanatory text updates
when a limit changes.

### `feat/analytic-hierarchy-scope` — `0f3af8a5`

**Built.** `hierarchy=` solves one hierarchy as a standalone port
extension (In→Start, Out→End, outside dropped). Exposed in API, CLI
(`--hierarchy`), MCP (`hierarchy`) and GUI combo. GUI has a Cancel button
for long solves; cached results are ignored after Cancel.

### `fix/analytic-review` — `f3f92914`

**Built.**
- Hierarchy scope named in GUI banner/scope note, text summary, CSV/Excel
  export and MCP markdown.
- Cross-boundary `dependencies=down` is refused explicitly instead of
  silently returning the `up` answer.
- Bad hierarchy selector and below-minimum limits are usage errors.
- MCP output schema advertises `hierarchy`.

**Review.** `tests/unit/test_analytical_api.py`,
`tests/unit/test_analytical_bdd.py`,
`tests/unit/test_analytical_dependencies.py`,
`tests/unit/test_gui_analytic.py`, `tests/unit/test_isolate.py`.

## Layout options and annotations

### `fix/layout-bands` — `11385520`

**Built.**
- Page-flow port constraints: TB/BT enter top/exit bottom (mirrored for
  BT); LR/RL enter left/exit right.
- Band hand-over keeps the requested flow direction with a Z/S side lane.
- `spacing` factor, nominal `1.0`: lower compacts, higher opens. GUI
  Arrange ▸ Layout ▸ Spacing; MCP `auto_layout(spacing=...)`.
- Selection-scoped Auto Layout: selected elements lay out in isolation
  around their old bbox centre; neighbours stay put. MCP `selectors`.
- `Max band` counts **series elements**, not marker columns; Start/End and
  hierarchy In/Out do not consume the limit, parallel branches share one.
- `label_side` for vertical flow: above/below anchored labels become a
  left/right side (auto = right for TB, left for BT). GUI Label side combo;
  MCP `auto_layout(label_side=...)`.

**Review.** `tests/unit/test_layout_direction.py`,
`tests/unit/test_gui_auto_layout_selection.py`,
`tests/unit/test_mcp_layout_scope.py`,
`tests/unit/test_annotation_location_and_anchor.py`.

## GUI reliability and controls

### `fix/worker-thread-safety` — `754f5724`

**Issue.** A background simulation could segfault in Python's cyclic GC
while the GUI painted. The worker also read QAction state and the status
bar through Qt objects owned by the GUI thread.

**Built.**
- Snapshot report choices and event delay on the GUI thread; worker uses
  plain values only.
- Worker disables cyclic GC for the batch and restores it on exit.
- `_open_report_streams` accepts the plain snapshot; no Qt reads on the
  worker.

### `feat/multi-drag` — `6cd22d08`

**Built.** Dragging one selected item carries the whole selection by the
same snapped delta; arrow-key nudge already moved all selected items.
Annotations follow with their endpoint-aware shift.

### `feat/explorer-expand` — `e918927e`

**Built.**
- Expand all / Collapse all buttons above the Model Explorer tree.
- Expand all is chunked (150 folders per event-loop turn) with painting
  suspended, so large levels stay responsive.
- Collapse all cancels an in-progress expand.
- Search walks collapsed descendants and expands the chain to a hit; no
  Expand all is needed first. Unvisited hierarchy levels are not in the
  tree yet, so they are not searched.

### `perf/gui-large-models` — `e6273e0c`

**Built.** Selective drag sync (only moved items), row index, minimap and
selection hot-path fixes. A 10k-block drag release went from ~3.3 s to
~3.3 ms in the review model; minimap polling from ~108 ms to ~0.014 ms.

## Failure Effects View

### `feat/fev-responsiveness` — `071e1b49`

**Issue.** Opening Failure Effects View rebuilt the engine model every
time and re-walked the tail for each block: a 10,000-block chain took about
a minute. The view also lacked a structured inspector, panel toggle and
element navigation.

**Built (all four commits).**
- `b08fc91d` — share one forward-walk cache across the graph build
  (**73.5 s → 1.3 s** on the 10k chain); parse each event/node/hierarchy
  row once; determinate progress window; freeze canvas repaints during the
  bulk colour pass; cache capacity for the current FEV state.
- `d0219090` — structured **Failure Effects** inspector tab: system
  colour/phase/criticality, failed/induced/idle/PM/cut/linked elements,
  down/degraded nodes and subsystems, capacity flow; TSV copy; full System
  Status Summary copy.
- `3fe94fed` — **Failure Effects** under View ▸ Panels, enabled only while
  the view is open; right-click a row → **Go to element**, entering the
  element's hierarchy and selecting the exact id even with duplicate
  names; FMECA name/kind/id entries; positive dock minimum sizes.
- `071e1b49` — dock icons in window titles and tabs, matching the ribbon
  glyphs; theme switches refresh them.

**Review.** `tests/unit/test_fev_capacity.py`,
`tests/unit/test_fev_interaction.py`,
`tests/unit/test_fev_invariants.py`,
`tests/unit/test_gui_fev.py`,
`tests/unit/test_gui_fev_capacity.py`,
`tests/unit/test_gui_dock_icons.py`,
`tests/unit/test_engine_load_progress.py`.

## Cross-cutting / integration-only fixes

- MCP layout argument validation: strict integer `max_columns`; booleans
  rejected; shared validation between GUI and MCP.
- Dependency-link down handling and invalid saved-direction fallback.
- Menu `None`-action guard.
- Generated CLI man page/completions and MCP output schemas refreshed.
- Golden/snapshot fixtures refreshed where behaviour changed deliberately.
- Test report: `devdocs/large-model-work-test-report.md`.

## Test evidence

Representative final runs on the integration branch:

- Targeted affected suite (routing, layout, results, analytical, capacity,
  file IO, MCP snapshot, CLI docs): **881 passed**.
- MCP/security/large-model subset: **174 passed**.
- Latest focused round (multi-drag, selection layout, spacing, label side,
  routing points, Explorer, annotations): **102 passed**.
- Explorer + hierarchy: **36 passed**.
- Routing points: **17 passed**.

The analytical hierarchy run on the user's
`ProjectX_BESS_Hierarchical.rbds`, hierarchy `Unit 1.1`, availability,
`dependencies=up`: applicable, 110 BDD nodes, availability
`0.9997188184`, exit 0. The whole model at the 2M cap is refused cleanly
with exit 3.

## Known limitations / follow-ups

- `Max band`, `spacing` and `label_side` require the current integration
  branch; older saved app settings fall back to nominal values.
- A routing point is stored in `.rbds` only; legacy `.rbd` drops it with
  the annotation layer.
- Cross-hierarchy search in the Explorer is not global: the tree contains
  the current level; unvisited hierarchy rows are not loaded.
- The 10k-block initial canvas load is still linear (roughly tens of
  seconds in the worst reviewed case); hierarchy enter/exit and drag are
  much faster.
- `make_room_for_annotations` grows vertical room for above/below labels;
  vertical-flow side labels are placed to the chosen side and nudged clear,
  but do not stretch horizontal element gaps.
- Background parallel runs are statistically independent by design, not
  bit-identical to a sequential batch.
