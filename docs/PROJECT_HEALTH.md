# Project health and share-safe copies

## Project health

**Model ▸ Project Health** (or `raptor health MODEL`) answers the questions a
reviewer asks before a run or a hand-off:

- how many blocks, nodes, events, hierarchies, links, pools, resources,
  triggers and phases are in the file;
- how many annotations, comments, formulas and rates it carries (a comment
  can be a Comments-layer row or a note drawn on the canvas; both count);
- every validation finding (errors and warnings), with a **Locate** action
  for the element it concerns;
- unnamed blocks/nodes/events/hierarchies;
- blocks that share a name (the finding locates the first one);
- spare or resource pools no block uses.

`raptor health MODEL -f json` returns the same counts and issues for a CI
gate or a review script. Validation errors make the command exit non-zero.
The text output says `no issues` when the list is empty; the GUI says so too
instead of showing a blank list.

## Share-safe copy

**File ▸ Share-safe Copy…** (or `raptor declassify MODEL OUT.rbds`) writes a
copy with the identifying prose removed. The original is never changed.

Options:

- **Replace element and hierarchy names** with `Block_101`, `Node_3`, etc.
  The labels obey the model's own naming rules (no spaces, at most 20
  characters), so the copy does not acquire new input-rule warnings. Named
  spare pools, resources, triggers and phases are renamed too, and the
  references from blocks follow them. A reference to a pool or trigger that
  is not in the file is replaced with a placeholder rather than left behind.
- **Remove element comments** (the Comments layer, including comment notes
  drawn on the canvas). With comments kept, the element name a comment row
  carries is still anonymised when names are removed.
- **Annotations**: remove all, remove text annotations only, or keep them.
  Text annotations are the notes drawn on the canvas; shapes and arrows are
  the graphic layer. "Text only" removes the notes and clears the captions
  of shapes and arrows, but keeps the shapes themselves. Every anchor name
  is rewritten to the anonymous label,
  or dropped when the anchor still carries its element ID: this covers notes,
  shapes and both ends of an arrow, so no old name is carried by the drawing.
- **Clear project metadata** (print title block, display unit, targets,
  currency reset to `$`, and unknown extension keys, including per-element
  ones).
- **Remove rate names, descriptions and field formulas** (the numbers remain
  in the element rows).
- **Clear Advisor notes** (the reasons attached to dismissed findings).

IDs, links, failure/repair data, costs, spares, resources, phases and the
simulation structure are kept, so the copy still runs and can still be
compared with the original.

### Residual risk

Declassification removes labels and prose, not the system's identity. Every
copy still discloses:

- its structure — element counts, IDs, links, hierarchy levels and positions
  — and its numerical data (failure/repair distributions, costs, spares and
  run settings). A diagram or a set of numbers can identify a plant even when
  no name survives.

Keeping an option also keeps what it protects: names, comments, annotation
text, project metadata, Advisor reasons or rate names/descriptions. The CLI
prints the residual-risk lines after every copy, and the GUI repeats them in
the success message. If costs themselves are sensitive, remove them from the
source model before sharing.

The CLI refuses to write the copy over the source model; the write is atomic.

The CLI mirrors every option:

```sh
raptor declassify plant.rbds plant-shared.rbds \
  --annotations text --keep-comments --keep-metadata
raptor declassify plant.rbds plant-shared.rbds --annotations keep
raptor declassify plant.rbds plant-shared.rbds --keep-rates   # explicit risk
```

`--annotations keep` keeps every annotation; there is no separate
`--keep-annotations` flag.
