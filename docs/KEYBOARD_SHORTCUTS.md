# Keyboard Shortcuts

## File
| Shortcut | Action |
|---|---|
| Ctrl+N | New blank model |
| Ctrl+O | Open model (.rbds / .rbd) |
| Ctrl+S | Save (works anywhere in the app) |
| Ctrl+Shift+S | Save As… |
| Ctrl+P | Print… (diagram / results / weak link, with title block) |
| Ctrl+Q* | Quit |

## Editing (Model view)
| Shortcut | Action |
|---|---|
| Ctrl+Z | Undo (works anywhere in the app) |
| Ctrl+Y / Ctrl+Shift+Z | Redo (works anywhere in the app) |
| Ctrl+A | Select all elements, hierarchy ovals and annotations |
| Ctrl+C | Copy selection (elements, links, annotations, hierarchy subtrees) |
| Ctrl+X | Cut selection |
| Ctrl+V | Paste at mouse cursor |
| Del / Backspace | Delete selection (nothing selected: arms click-to-delete) |
| Esc | Cancel armed tool / delete mode / connect / Failure Effects fail-repair targeting; cancels a running batch |

Nudging with the arrow keys (one grid step; Shift: 1 px) also moves selected
routing points, and the routing points of a link whose two ends both move go
with them. One undo step.

## Annotation z-order (Model view)
Needs one or more annotations selected. Each command is greyed out when
it would not move anything, so a note already at the front has *Bring to
Front* and *Bring Forward* disabled.
| Shortcut | Action |
|---|---|
| Ctrl+Shift+] | Bring to Front — in front of every diagram element |
| Ctrl+] | Bring Forward — one step up the stack |
| Ctrl+[ | Send Backward — one step down the stack |
| Ctrl+Shift+[ | Send to Back — behind every diagram element |

## Model view
| Shortcut | Action |
|---|---|
| Q | Select tool |
| Ctrl+L | Auto Layout (columns by flow; selection alone if selected) |
| Ctrl+= / Ctrl+- | Zoom in / out (also Ctrl+wheel) |
| Ctrl+0 | Zoom to fit |
| Ctrl+Shift+0 | Zoom to the selection |

## Connect tool
Left-click the source element, then click (left or right) the target.
Right-click on empty canvas cancels.

## Annotation tools
The four tools (Text, Rectangle, Ellipse, Arrow) are **one-shot**: each
shape is placed with one click (text, arrow) or two (rectangle, ellipse),
and the tool then switches itself back to Select — click the ribbon
icon again for the next one. That stops every later click from dropping
another shape.

- **Esc** or **right-click** cancels a placement in progress (the first
  corner, or an arrow's tail) and puts nothing on the canvas. Esc also
  cancels *Pick on canvas…* anchoring from the Inspector.
- Right-click *anywhere* else on the canvas opens the context menu.

## Anchoring annotations
Select an annotation and use the Inspector's **Anchor** group: pick a
target, a **Location** (Right, Left, Above, Below or Centre) and a gap.
**Pick on canvas…** then a click on an element or another annotation sets
the target; Esc cancels. Anchored annotations follow their target, and
chains are allowed.

Drag an anchored note wherever you like: it keeps following its target
from there. **Reset position** returns it to its Location and **Break
link** frees it. Auto Layout leaves room for notes above or below a
block (it widens the gap between stacked blocks) and nudges a note that
would still land on something.

An arrow's two ends attach separately. Each has its own **Pick…** (click
an element or annotation) and **Remove** (frees that end where it is). An
attached end follows its target and stops at the target's edge, not its
centre. Dragging an anchored arrow by hand frees it.

To make your own look the default, right-click a shape you have styled
and choose **Set as Default for New Rectangles** (or Ellipses / Text
Boxes / Arrows). Every later shape of that kind starts with those
colours, line width, dash style and font. **Reset … Default to
Factory** appears once one is saved. The choice is remembered between
sessions.

## Simulation
| Shortcut | Action |
|---|---|
| F5 | Run batch |
| F6 | Step one event |
| Shift+F6 | Next Time — step through every event at the current sim time in one click, stop when time advances |
| Ctrl+Shift+R | Show the Results panel full height |
| F7 | Validate model |
| Esc | Stop a running batch |

## Key tips
Not every ribbon button has a shortcut letter. Press **Alt** to show the
letters that do exist — **File (E)**, Open (O), Save (V), New (W),
Copy View (Y), Project Settings (C), Issues (L), Run (R), Step (S),
Next Time (N), Finish (F), Restart (X), Pause (A), Stop (P), Jump (J)
and Animate (M) — then Alt + that letter to run it. A button with no
letter keeps its plain label and is reached with its own shortcut (see
above) or the mouse. Press Alt again (or any other key) to hide the
tips. The **Run** panel is under View ▸ Panels ▸ Run Panel.

## Help
| Shortcut | Action |
|---|---|
| F1 | Context help — opens the manual at the section for the focused dock/dialog |
| Ctrl+F | In the Help window: find text on the page |

\* Quit follows the platform menu convention.
