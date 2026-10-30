# Design note: canvas scale, grid and annotations

Status: **proposal, for decision.** Nothing in this note is implemented. It
answers a user complaint ("the grid is oddly placed; annotations don't
position like other elements; I expect to move things in steps related to a
typical block size") and the layout problems reported in #4.

## 1. What the code does today

Three different spaces are in play, and they are tied together only by a
number derived from each model's own data.

| Space | Unit | Where | Notes |
|---|---|---|---|
| Model (file) | stored units, grid 1.25 | `xPos`/`yPos` in `.rbd`/`.rbds` | Raptor 7 stores `display x 1.25`; its files sit on 1.25 grid lines (`legacy_layout.py`: `GRID`, `LAYER_PITCH` 2.5, `ROW_PITCH` 2.5). Kept for file fidelity. |
| Scene | pixels | `core/fileio/view_transform.py` | `x_scene = (x - origin_x) * scale`, `y_scene = (flip_base - y) * scale`. |
| Item size | pixels, **constant** | `BLOCK_W x BLOCK_H` = 108 x 46, `NODE_R` = 18 | A block is the same size on screen whatever `scale` is. |

**The scale is derived from the data, once, then frozen** on
`RbdFile.view_transform` (`derive()`): the largest of
`LEGACY_SCALE` (8), the scale that keeps horizontally adjacent columns
`MIN_ELEMENT_GAP` (52 px) apart, and the scale that does the same for the
closest rows in one column, clamped so the scene stays under about 20,000 px
(display drivers fail on larger scenes) and 300 px per unit. `origin_x` is the
leftmost element's x and `flip_base` the topmost element's y.

**The drawn grid and the snap step come from the same data**
(`diagram.py`, `_apply_scene_layout`): the grid pitch is the model's closest
authored spacing (`dx_min`, `dy_min`) times the scale, and an axis with no
uniform pitch or a pitch under 20 px falls back to `step * 5`. The snap step is
a quarter of the drawn pitch (`SNAP_SUBDIVISIONS = 4`).

**Annotations** are stored in *scene pixels* (`annotations.py`: "annotations
are stored in that resulting scene space"). They do not snap at all
(`diagram_annotations.py` has no snap code). A name-anchored annotation
(`adjacent_to`, `side`, `gap`; arrows `from_`/`to`) is resolved once to
absolute pixels; the `anchor` object is "display/round-trip metadata: the
renderer ignores it (the geometry is authoritative)" (`docs/RBDS_FORMAT.md`).
So an annotation does not follow its element when the element is dragged or
the diagram is re-laid-out. `gap` defaults to 12 px.

## 2. What that produces (measured)

`derive()` on real Raptor 7 fixtures, with the block fixed at 108 x 46 px:

| Model | scale (px/unit) | drawn grid, x (px) | drawn grid, y (px) |
|---|---|---|---|
| Cascade | 128.0 | 160 | 160 |
| Hierarchy1-7 | 128.0 | 160 | 160 |
| HierarchyExample | 99.2 | 124 | 124 |
| MotorDemo | 64.0 | 160 | none (fallback) |
| Capacity1 | 99.2 | none (fallback) | 124 |
| LadL8BEvents | 64.0 | 160 | 80 |
| TwoBlockRedundancy | 14.2 | 124 | 443 |

- The same 108 px block sits on a grid cell anywhere from 80 to 443 px, or on
  none.
- The snap step (a quarter of that) is therefore anywhere from about 20 to
  110 px, per model. There is no "one nudge is about a quarter of a block".
- Because origin and pitch follow the data, adding or removing the leftmost
  or topmost element can change where the grid lines fall.
- Annotations ignore all of it: they do not snap, and they sit in pixels
  while everything else sits in model units times a per-model scale.

Layout time (separate, but it shares the engine): a parallel template took
10 ms at 12 branches, about 1 s at 24, about 42 s at 48, and was still running
after 9 minutes at 150. Nearly all of it is the refine step's scoring.

## 3. The underlying problem

Element size is fixed in pixels while spacing, grid and snap are derived per
model. That is the wrong way round for the stated expectation. A user thinks
in blocks ("move it half a block"), and the application offers a distance that
depends on the model. Annotations add a second, independent frame.

I believe Raptor 7 does the opposite: block size is fixed in grid units and the
whole picture zooms. That is inferred from `legacy_layout.py`'s own comments
(views are anchored at origin 0/80 and "zoom ~= visible display width"), **not
verified against the program**; it should be checked on a real Raptor 7 before
we cite it as the reason.

## 4. Proposal

Keep the file format exactly as it is. Change the presentation layer so that
distances are the same for every model.

1. **One fixed scale.** Pick `SCALE` so a block is a fixed number of grid
   cells. The layout engine already assumes a block is about 1.8 x 0.9 stored
   units (`_HALF["block"] = (0.9, 0.45)`), and 108 / 1.8 = 60 px per unit. Zoom
   (the view), not the model scale, handles density and size. Models whose
   closest spacing is tighter than a block get overlapping boxes, which is
   true in Raptor 7 as well and is what auto layout is for.
2. **An absolute grid.** Grid lines at multiples of 1.25 stored units from the
   model origin (0), not from the leftmost/topmost element. A fixed fraction of
   a block as the snap step (half a grid cell, 0.625 units, about 37 px at
   `SCALE = 60`). The same for every model.
3. **Annotations in model units.** Stored with the same `x`/`y` units as
   elements, drawn through the same transform, snapped by the same step.
4. **Authoritative anchors.** An annotation created by name keeps
   `{target, side, gap}` and the renderer positions it from the anchor each
   time, so it follows the element (and arrows follow both ends). `gap` in
   fractions of the target's size, not pixels. Free-placed annotations keep
   absolute model coordinates.

Consequences worth being explicit about:

- `view_transform` stops being derived. For files that carry one, it is read
  once to convert old annotation coordinates to model units, then dropped.
- The 20,000 px scene limit becomes a real constraint, not a derived clamp:
  at 60 px/unit it is about 333 stored units across. A level wider than that
  (the 150-block cases) needs the view to render in tiles or the layout to wrap,
  as `ROW_CHUNK` already does for tall columns. This is the main technical risk.
- Element positions in files do not change, so `.rbd` round trips and every
  golden fixture are untouched. Only `.rbds` annotation coordinates migrate.

### Alternatives considered

- **B. Keep the per-model scale, express snap in block fractions.** Cheapest:
  only the snap step and the grid change. It makes a nudge consistent, but
  distances between blocks on screen still differ per model, annotations still
  live in pixels, and the grid origin still moves with the data. Fixes the
  symptom the user named, not the cause.
- **C. Anchors only.** Makes annotations follow their elements and leaves the
  scale alone. Worth doing in any case (point 4); not enough on its own.

Recommendation: **A, delivered in the order 4, 3, 1+2**, because 4 and 3 are
independent of the scale change and remove the annotation inconsistency first.

## 5. Interaction with the layout work in #4

- **Time budget (done separately):** the refine step has a 10 s budget and keeps
  the layered layout if it runs out. This is independent of the scale decision.
- **Save-time layout:** `save_rbds` runs the legacy layout gate on every save
  (cramped, mostly off-grid models). Applying run options no longer triggers it
  (`no_layout_on_save`), but any other save still can. Under the proposal the
  gate stays, since it exists for `.rbd` export fidelity; it should report when
  it moves things.
- **Layout units:** the layout engine already works in model units on the
  1.25 grid, so a fixed scale needs no change to it.

## 6. Cost and risk

- GUI tests and screenshots that assume the derived scale will change; the UI
  sweeps (`tests/integration/sweeps`) read pixel positions in places.
- A migration for `.rbds` files with annotations (convert scene px to model
  units with the file's own frozen transform), and a one-release read path for
  the old form.
- The scene-size limit above.
- Not yet checked: how the frozen transform interacts with hierarchy levels
  (each level has its own span), and the print/PDF export, which also reads the
  scene.

## 7. Decisions needed

1. Option A (fixed scale and absolute grid) or B (block-fraction snap only)?
2. Is `SCALE = 60` (a block is 1.8 x 0.9 units) right, or should it be chosen
   against Raptor 7's own block size once that is confirmed?
3. May annotations move to model units, with a migration, in the same release
   as the scale change, or should anchors (4) go first as their own release?
4. Wide levels: tiled rendering, wrapping in the layout, or a documented
   limit?
