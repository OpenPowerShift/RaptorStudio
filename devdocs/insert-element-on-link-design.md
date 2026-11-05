# Insert an element on a link

## Goal

While an Add Block / Add Node / Add Event / Add Hierarchy tool is armed,
hovering a connector shows whether it can host the new element. Clicking
inserts the element in series, splitting the connector into two and moving
the downstream part of the model just enough to keep standard spacings.
Everything is one undo step.

## Interaction

- Hover is active only for the four Add tools, never in Select mode.
- The pick radius is measured in screen pixels and converted through the
  view scale, so it is equally easy at every zoom.
- The nearest connector within the radius is a candidate.
- If a second candidate is within a small screen-space margin, the hover
  is **blocked**: the link is drawn in the warning colour and the status
  bar explains that the links overlap. This is the common case for
  orthogonal routes that share a segment.
- A blocked hover consumes the click: the element is not placed somewhere
  arbitrary. Moving off the connectors restores normal canvas placement.
- A valid hover draws an accent halo under the link. A blocked hover draws
  the same halo in the failure colour.
- After insertion the new element is selected; the Add tool stays armed
  for the next placement, matching the existing Add tools.

## Which way is downstream?

1. If `flow_direction()` is an explicit LR/RL/TB/BT (Auto Layout), that is
   the page axis.
2. Otherwise infer the page axis from the visible links: a length-weighted
   vote over their source→target vectors. Empty or degenerate models fall
   back to LR.
3. The connector's own arrow chooses the sign. On a diagonal, the page axis
   chooses the axis unless the connector is mostly perpendicular to it, in
   which case its own dominant component wins. This means a back-edge in an
   LR diagram still pushes its own target left, not right.

## Placement

- **No manual dog-legs:** place the element midway between the source and
  target extents along the downstream axis. If the current gap is large
  enough, the two side gaps are equal and nothing else moves. If not, the
  target and its downstream subgraph shift just enough that both side gaps
  become the standard gap.
- **Manual dog-legs:** place the element at the midpoint of the drawn
  polyline, so it lands between the existing routing points. The same
  minimum-gap rule then applies. Routing points before the split stay on
  the source half; points after it stay on the target half. Downstream
  points move by the same delta as the target so the dog-leg shape is
  preserved. Undo restores the original link and every point exactly.
- The standard gap is `MIN_ELEMENT_GAP`, scaled by the user's Auto Layout
  spacing setting and then by 1.5 for insertion. The extra room is
  deliberate: a symbol squeezed between two existing ones reads cramped at
  Auto Layout's bare minimum.
- The formula is written for both signs, so RL/BT use the mirror of LR/TB.

## Reflow scope

Only the target and everything reachable from it through outgoing links on
the current level moves. Upstream elements and unrelated branches keep
their positions. All positions and the new element are recorded through
the normal `MoveCmd`, so annotations anchored to moved elements follow.

## Link rewrite

Insert-on-link is automation of the manual steps: delete the link, then
draw two new ones with Connect. The original row is removed and two new
rows are created with fresh ids, built exactly as Connect builds a link.
The old link id is not reused.

Connect matches real Raptor. The connect handler in `IDisplay.mod` gives
every drawn link fixed defaults before `LinkInit` -- `ColdPri` 50,
`CapPri` 50, `nomFlow` -2, `MaxFlow` -1, `AProb` 1, `AutoTime` 0,
`ManTime` 0 -- and parents it to the open level
(`SetParentID(activeWindow)`). Only copy/paste carries a link's own
values. `make_link_row` holds those defaults and is shared with the API
builder. (Before this change, Connect cloned the first `LINK_DATA` row,
inheriting that row's settings and its `pID`; that was a fidelity bug.)

Consequence, accepted deliberately: a split link's standby
(`ColdPri`/`AProb`/`AutoTime`/`ManTime`) and capacity
(`CapPri`/`nomFlow`/`MaxFlow`) settings are reset to the defaults, the
same as after a manual delete-and-redraw in Raptor. Re-enter them on the
new link if they mattered. Link ids also change, which can reorder ties
the engine breaks by link id, again as in the manual case.

## Undo

One macro: add element, move new element + downstream subgraph, replace
link, migrate routing points. Undo restores the original link, its row
index, its routing points and all positions.

## Test plan

- Pure geometry: flow inference, axis/sign choice, equal-gap placement in
  all four directions, ambiguity sorting.
- GUI: allowed and blocked hover; click inserts for all four element
  kinds; only the downstream subgraph moves; undo/redo; dog-leg split and
  restore; duplicate/overlapping links are blocked; the new link rows use
  Raptor's Connect defaults and the open level.
