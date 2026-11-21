# Project health and share-safe copies

## Project health

**Model ▸ Project Health** (or `raptor health MODEL`) answers the questions a
reviewer asks before a run or a hand-off:

- how many blocks, nodes, events, hierarchies, links, pools, resources,
  triggers and phases are in the file;
- how many annotations, comments, formulas and rates it carries;
- every validation finding (errors and warnings), with a **Locate** action
  for the element it concerns;
- unnamed blocks/nodes/events/hierarchies;
- blocks that share a name;
- spare or resource pools no block uses.

`raptor health MODEL -f json` returns the same counts and issues for a CI
gate or a review script. Validation errors make the command exit non-zero.

## Share-safe copy

**File ▸ Share-safe Copy…** (or `raptor declassify MODEL OUT.rbds`) writes a
copy with the identifying prose removed. The original is never changed.

Options:

- **Replace element and hierarchy names** with `Block 101`, `Node 3`, etc.
  Named spare pools, resources, triggers and phases are renamed too, and
  the references from blocks follow them.
- **Remove element comments** (the Comments layer, including comment notes
  drawn on the canvas).
- **Annotations**: remove all, remove text annotations only, or keep them.
  Text annotations are the notes drawn on the canvas; shapes and arrows are
  the graphic layer. Anchors are rewritten/de-referenced so they do not carry
  an old element name.
- **Clear project metadata** (print title block, display unit, targets,
  currency and unknown extension keys, including per-element ones).
- **Remove rate names, descriptions and field formulas** (the numbers remain
  in the element rows).
- **Clear Advisor notes** (the reasons attached to dismissed findings).

IDs, links, failure/repair data, costs, spares, resources, phases and the
simulation structure are kept, so the copy still runs and can still be
compared with the original.

**What is not removed:** free prose you explicitly keep (`--keep-comments`,
`--keep-annotations`), and numerical costs. If costs themselves are
sensitive, remove them in the source model before sharing. The CLI refuses
to write the copy over the source model; the write is atomic.

The CLI mirrors every option:

```sh
raptor declassify plant.rbds plant-shared.rbds \
  --annotations text --keep-comments --keep-metadata
raptor declassify plant.rbds plant-shared.rbds --keep-rates   # explicit risk
```
