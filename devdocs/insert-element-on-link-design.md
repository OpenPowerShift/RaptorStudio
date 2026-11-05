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

The original connector is removed and two new rows are created with fresh
ids, exactly as a manual "delete the link and draw two new ones" would
produce: the new rows are cloned from the first `LINK_DATA` row, as
`connect_elements` already does. The old row's `pID` is copied so the new
links stay on the same hierarchy level. The old link id is not reused.

This is deliberately the manual-reconnect behaviour rather than a special
inheritance rule. It keeps the feature consistent with how the Connect
tool builds links today.

## Undo

One macro: add element, move new element + downstream subgraph, replace
link, migrate routing points. Undo restores the original link, its row
index, its routing points and all positions.

## Test plan

- Pure geometry: flow inference, axis/sign choice, equal-gap placement in
  all four directions, ambiguity sorting.
- GUI: allowed and blocked hover; click inserts for all four element
  kinds; only the downstream subgraph moves; undo/redo; dog-leg split and
  restore; duplicate/overlapping links are blocked; link rows follow the
  manual-reconnect template.
