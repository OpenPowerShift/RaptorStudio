# Design note: canvas scale, grid, placement and annotations

Status: **proposal, for decision.** Nothing in this note is implemented. It
answers a user complaint ("the grid is oddly placed; annotations don't
position like other elements; I expect to move things in steps related to a
typical block size") and the layout problems reported in #4.

Revision 2 (2026-10-30) checks the first draft against the Raptor 7 MODSIM
source (`IDisplay.mod`, `IObjects.mod`, `IFileFx.mod`, `IMenubar.mod`) and
the repository's 296 `.rbd` and 30 `.rbds` files. The diagnosis in the first
draft stands. Its numbers do not: the fixed scale it proposed (60 px per
stored unit) would draw overlapping blocks in 53 of the 296 `.rbd` files.
It also missed two compatibility problems. Auto layout can place elements
outside Raptor's canvas, and the app reads a block's stored position as its
centre where Raptor reads it as the top-left corner.

## 1. What Raptor 7 does (from the source)

| Fact | Source |
|---|---|
| The canvas is a fixed world of 120 x 80 display units with y increasing upward. A grid line falls on every whole unit from 0,0, giving 121 vertical and 81 horizontal lines with a heavier border. | `IDisplay.mod` `GridObj.ObjInit` (6109-6170) |
| The file stores display x 1.25. The world is therefore 150 x 100 stored units, and one grid cell is 1.25 stored units. | `IFileFx.mod` 696 (`DisplayAt(x/1.25)`), 3686 (`xPosition*1.25`) |
| The stored position is the element's **top-left anchor**, not its centre. Links attach at (x+0.26, y-0.21) display units for blocks, nodes and events, and at (x+0.56, y-0.21) for hierarchies. Rubber-band selection counts the box (x, y) to (x+0.8, y-0.6). | `IObjects.mod` 1243-1267; `IFileFx.mod` 1325-1354; `IDisplay.mod` 7195-7216, 8076-8339 |
| An element's size is fixed in world units, and the whole picture zooms. A block is about 0.5 x 0.45 display units: the arrowhead stops 0.22-0.25 short of the centre on the axes. That is about **half a grid cell**. | `IObjects.mod` `arrowhead` (104-150); `IDisplay.mod` `SetView` (5124-5190) |
| Zoom is the visible width of the world in display units. It runs from 6 to 118 in steps of 6, and a new file opens at 24 with origin 0/80. Zoom to fit has a minimum of 10. The view is saved per file (`zoomVal`, `xOrigin`, `yOrigin`) and per hierarchy (the zoom and origin columns of each hierarchy row). | `IMenubar.mod` `Zoom` (1017-1083), 1227-1230; `IDisplay.mod` `ZoomFit` (4817-4934) |
| Dropping or dragging snaps the top-left anchor to a **whole display unit** (TRUNC), clamped to x <= 119 and y <= 80. A group move also clamps to y >= 1. Every element Raptor places therefore sits on a multiple of 1.25 stored units, inside [0, 148.75] x [1.25, 100]. | `IDisplay.mod` 6764-6776, 7015-7022, 7595-7605 |
| `OpenFile` does **not** clamp positions. A stored coordinate outside the world is drawn outside the grid, and `SetView` clamps the view to the world. | `IFileFx.mod` 696; `IDisplay.mod` 5134-5141 |
| `symbolScale` (Small/Medium/Large/Grande) sizes only the status face. It does not change block size. | `IMenubar.mod` 3997-4040 |
| Raptor 7 has **no annotations**. No module contains annotation code. | grep of all `I*.mod` |

Not verified: the block bitmap's exact size, which lives in the binary
graphics library, not the source. The half-cell figure comes from the link
geometry above. I have also not checked on a real Raptor that an element
outside the world cannot be reached. The source suggests it cannot.

## 2. What the code does today

Three different spaces are in play, and a number derived from each model's
own data is the only thing that ties them together.

| Space | Unit | Where | Notes |
|---|---|---|---|
| Model (file) | stored units, grid 1.25 | `xPos`/`yPos` (`.rbd`), `x`/`y` (`.rbds`) | Stored for file fidelity, as in §1. |
| Scene | pixels | `core/fileio/view_transform.py` | `x_scene = (x - origin_x) * scale`, `y_scene = (flip_base - y) * scale`. |
| Item size | pixels, constant | `BLOCK_W x BLOCK_H` = 108 x 46, `NODE_R` = 18 | A block is the same size on screen whatever the scale is. |

- **The scale is derived from the data, once, then frozen** on
  `RbdFile.view_transform` (`derive()`). It is the largest of
  `LEGACY_SCALE` (8), the scale that keeps adjacent columns
  `MIN_ELEMENT_GAP` (52 px) apart, and the scale that does the same for rows
  in one column. It is clamped to keep the scene under about 20,000 px.
  `origin_x` is the leftmost element and `flip_base` the topmost, so the
  origin follows the data too.
- **The drawn grid and the snap step come from the same data**
  (`diagram.py` `_apply_scene_layout`). The pitch is the closest authored
  spacing times the scale. Where an axis has no uniform pitch, it falls back
  to a heuristic. The snap step is a quarter of the drawn pitch
  (`SNAP_SUBDIVISIONS = 4`). On a 1.25 grid that snaps to 0.3125 stored
  units, which is off Raptor's lattice.
- **The stored position is treated as the centre** of a block
  (`diagram.py` bulk placement: `setPos(sx - BLOCK_W / 2, sy - BLOCK_H / 2)`).
  Raptor treats it as the top-left anchor (§1). Blocks, nodes and events
  share one offset in Raptor, so the difference shows up only for
  hierarchies. Their offset is +0.56 display units rather than +0.26, so
  hierarchies are drawn 0.375 stored units left of where Raptor puts them
  relative to blocks.
- **The saved view is ignored.** The GUI never reads `zoomVal`, `xOrigin`
  or `yOrigin`. It always opens at fit-to-content.
- **Annotations** are stored in scene pixels and never snap. A name-anchored
  annotation is resolved once to pixels. The `anchor` object is metadata
  that the renderer ignores (`docs/RBDS_FORMAT.md`), so an annotation does
  not follow its element.

## 3. What that produces (measured)

The per-model scale from `derive()`, with the block fixed at 108 x 46 px:

| Model | scale (px/unit) | drawn grid, x (px) | drawn grid, y (px) |
|---|---|---|---|
| Cascade, Hierarchy1-7 | 128.0 | 160 | 160 |
| HierarchyExample | 99.2 | 124 | 124 |
| MotorDemo | 64.0 | 160 | none (fallback) |
| Capacity1 | 99.2 | none (fallback) | 124 |
| LadL8BEvents | 64.0 | 160 | 80 |
| TwoBlockRedundancy | 14.2 | 124 | 443 |

Across all 296 `.rbd` files in `tests/golden` and `tests/fixtures`, the
derived scale ranges from 14.2 to 300 (the most common values are 99.2, 64,
14.2, 30 and 128). The same 108 px block sits on a grid cell anywhere from
80 to 443 px, or on none, and the snap step varies by model from about 20
to 110 px.

The same 296 files under a **fixed** transform:

| Fixed scale | Files with two elements at *different* positions overlapping |
|---|---|
| 60 px/unit (the first draft) | **53** |
| 128 px/unit (this revision) | **1**: `RedrawProbe`, written by this app and 100 % off-grid |

The remaining overlaps at 128 are elements stacked on the identical spot,
which overlap in Raptor too: `PieFacility2` has 18 elements at 0,0, and a
few probes stack blocks. 295 of the 296 files lie inside Raptor's 150 x 100
world. The exception, `CSExcessSwitchProbe` (y = -2.5), is a probe built
here.

None of the 30 `.rbds` files in the repository carries annotations or a
`view_transform`. Six have off-grid coordinates: `Van1`, `Water1`,
`Water2`, `ProdLine0`, `ProdLine1` and `CrammedDataCentre`. v0.8.0 is
tagged, so users may already have `.rbds` files with annotations.

## 4. Auto layout can leave Raptor's world (new; a bug today)

Output of `legacy_layout.layout_container_positions`:

| Shape | x range | y range | Inside 150 x 100? |
|---|---|---|---|
| series, 20 blocks | 1.25-53.75 | 92.5 | yes |
| series, 60 blocks | 1.25-**153.75** | 92.5 | no |
| series, 100 blocks | 1.25-**253.75** | 92.5 | no |
| parallel, 12 | 1.25-6.25 | 78.75-**106.25** | no |
| parallel, 24 | 1.25-8.75 | 68.75-**113.75** | no |

Rows are centred on `ROW_CENTRE = 92.5` at a 2.5 pitch, so a parallel of 8
or more branches already passes y = 100. A full `ROW_CHUNK` of 12 rows spans
27.5 units, wider than the 81-99 band (`_BAND_LO`/`_BAND_HI`) the layout is
meant to keep to. Columns never wrap in x. A `.rbd` exported from such a
layout opens in Raptor with elements outside the grid (§1). This happens
whatever is decided about the scale, and it should be fixed first.

## 5. Assessment of the first draft

| Point | Verdict |
|---|---|
| 1. One fixed scale | **Right idea, wrong value.** The draft took 60 px/unit from `_HALF["block"] = (0.9, 0.45)`, but that is the layout engine's collision box with margin, not a block's size. Raptor's block is about 0.65 x 0.55 stored units, half a cell. At 60, two blocks on adjacent lattice points are 75 px apart and a block is 108 px wide, so 53 real files overlap (§3). The draft also claims that overlap "is true in Raptor 7 as well", which is false: a block is narrower than a cell, so two elements on different lattice points never overlap in Raptor. |
| 2. Absolute grid | **Right, but incomplete.** Grid lines at multiples of 1.25 are only absolute if the transform's origin is fixed too. The draft kept `origin_x` = leftmost element and `flip_base` = topmost. A half-cell snap (0.625) also leaves elements off Raptor's lattice. Raptor re-snaps them the first time they are touched, and once enough of a container is off-grid with a pitch under 1.24, the save-time normalizer (`needs_legacy_normalization`) re-lays out the whole container. |
| 3. Annotations in model units | Agreed. |
| 4. Authoritative anchors | Agreed. |
| Scene-size risk "the main technical risk" | **Overstated.** If content stays inside Raptor's world, the scene is at most 150 x 100 x 128 = 19,200 x 12,800 px, under the limit by construction. The real risk is coordinates outside the world, which today come from auto layout (§4). |
| Open: hierarchy levels | **Resolved.** In Raptor every level shares one world and keeps its own zoom and origin, so a fixed transform needs nothing per level. |
| Missing | The saved view, the hierarchy offset, how new elements are placed, and the auto-layout bounds. |

## 6. Revised proposal

The file format does not change, and stored element positions are never
rewritten on open.

1. **A fixed transform matching Raptor's world.** `scale` = 128 px per
   stored unit, `origin_x` = 0, `flip_base` = 100. One Raptor grid cell is
   160 px. Two 108 x 46 blocks on adjacent lattice points then have 52 px
   (`MIN_ELEMENT_GAP`) clear horizontally and 114 px vertically. That is
   exactly the clearance `derive()` already aims for on the densest files,
   so Cascade and Hierarchy1-7 look the same as today. The scene is at most
   19,200 x 12,800 px. `derive()` and the frozen `view_transform` go away.
2. **Raptor's grid.** Lines every 1.25 stored units from 0, drawn over the
   world rectangle with its border, as Raptor draws it. Outside the world is
   shaded so you can see what Raptor cannot.
3. **Snap = one grid cell (1.25),** as in Raptor, with the anchor clamped to
   [0, 148.75] x [1.25, 100]. A held modifier key places freely. The
   position is stored as is and still loads in Raptor.
4. **The stored point is the centre, with no numeric change.** The app
   keeps treating the stored (x, y) as an element's centre, and the grid
   lines and snap pass through it. Raptor treats the same point as a
   top-left anchor, but it offsets blocks, nodes and events by the same
   amount (+0.26, -0.21 display units), so the two readings differ only by
   a translation of the whole picture. No conversion is needed and nothing
   in the file changes. The stored values must stay as they are: rewriting
   them as true centres (adding 0.325 / 0.2625) would break `.rbd` round
   trips and take every element off the 1.25 lattice. Hierarchies are the
   one exception, because Raptor's offset for them is +0.56. They are drawn
   0.375 stored units right of their stored point, and the layout engine
   and placement treat their footprint as shifted by the same amount. The
   stored value is untouched, so a hierarchy lines up with its neighbours
   as it does in Raptor.
5. **Open at the saved view.** Each level opens at its own `zoomVal` (home)
   or hierarchy zoom, and at its `xOrigin`/`yOrigin`. Zoom means "this many
   display units visible across the window". A Raptor file then opens with
   the framing Raptor shows. If no view is saved, the level opens at fit.
   Save writes the current view back. Fit to content stays as a command.
6. **Placement as cell occupancy.** A block is narrower than a cell, so the
   canvas is a 120 x 80 grid of cells. A block, node or event occupies one
   cell and a hierarchy two cells wide. A palette drop goes to the nearest
   free cell to the pointer. An element added without coordinates (MCP
   `add_block`, `add_node`, etc.) goes to the first free cell to the right
   of the element it follows, or of the rightmost element in the level, then
   downward. Paste keeps the copied cluster's shape and moves it as a whole
   to the nearest position where every cell is free. Nothing is ever placed
   outside the world.
7. **Auto layout inside the world** (fixes §4). Rows hang down from the top
   (first row at 98.75), not centred on 92.5. Column chunks are sized to the
   height available, and a long series wraps into a new band below when x
   would pass 148.75. The default pitch stays 2 cells (2.5), which fits 59
   columns x 39 rows. When a level does not fit, the pitch drops to 1 cell,
   which fits 119 x 79. A level that does not fit even then cannot be
   represented in Raptor, and `.rbd` export reports it as an error instead
   of writing coordinates nobody can reach. `.rbds` saves are unaffected.
8. **Annotations in model units, anchors authoritative** (the draft's 3 and
   4). `x`/`y` are in stored units and drawn through the same transform. A
   name-anchored annotation keeps `{target, side, gap}`, with `gap` in
   stored units, and is re-placed from its anchor on every draw. Arrows
   follow both ends. Raptor has no annotations, so they are free to snap to
   half a cell for finer placement. Migration on open: if the file carries a
   `view_transform`, convert with it. Otherwise convert with `derive()` on
   the positions as loaded, which reproduces what the user last saw. Then
   drop `view_transform`. The old form gets a read path for one release.

## 7. Will existing files open properly?

- **`.rbd` from Raptor 7: yes.** Positions are untouched, so round trips and
  golden fixtures stay byte-stable. Every file in `tests/` draws without
  overlap at 128, except elements stacked on the identical spot, which
  overlap in Raptor too. Files that today render at another derived scale
  (most of them: 99.2, 64, 30, 14.2) change in proportion, gaps against
  block size, and now match Raptor's proportions. Point 5 opens them at
  Raptor's framing.
- **`.rbds` without annotations (all 30 in the repository): yes**, with no
  change. Off-grid ones draw at their coordinates, and snapping applies only
  to new moves.
- **`.rbds` with annotations (v0.8.0 users):** yes, through the one-off
  conversion in point 8.
- **`.rbds` or `.rbd` with coordinates outside the world:** they draw. The
  scene rectangle grows to the content, keeping today's cap on the panning
  margin. Export flags them (point 7).
- **Auto layout: not today** for series longer than about 58 blocks or
  parallels of 8 or more branches (§4). Yes after point 7.

## 8. Cost and risk

- GUI tests and screenshots that assume a derived scale, and the UI sweeps
  (`tests/integration/sweeps`) that read pixel positions.
- MCP: `add_annotation`'s explicit `x`/`y` change from scene pixels to
  stored units. Update the tool description and `docs/RBDS_FORMAT.md`
  together.
- Print/PDF export and `render_diagram` read the scene. A fixed scale makes
  them simpler, not harder.
- Changing the layout's row origin (point 7) moves the coordinates of every
  model whose layout is regenerated. Raptor 7 fixtures are protected by the
  normalizer gate, but `.rbds` models re-laid out on demand will differ from
  before. Expected, but it affects tests that pin layout output.

## 9. Decisions needed

1. Adopt the fixed transform of point 1 (128 px/unit, origin 0/100) with
   Raptor's grid? *Recommended.*
2. Centre semantics with a hierarchy-only drawing offset (point 4), or no
   offset, accepting that hierarchies sit 0.375 units left of where Raptor
   draws them? *Recommended: centre semantics with the offset. Stored
   values never change either way.*
3. Snap one cell like Raptor (with a free-placement modifier), or half a cell
   and accept off-lattice positions? *Recommended: one cell.*
4. A level too big for Raptor's world: an error on `.rbd` export (point 7),
   or a warning? *Recommended: error, since the file would not be usable.*
5. Annotation migration in the same release as the transform, or anchors
   first as their own release?

Suggested order: (a) the auto-layout bounds fix (§4, a bug today and
independent of the rest); (b) annotations in model units with authoritative
anchors; (c) fixed transform, grid, snap, hierarchy offset and saved view together,
since they change the picture once; (d) cell-occupancy placement.
