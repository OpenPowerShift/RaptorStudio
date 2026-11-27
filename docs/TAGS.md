# Component tags

A tag is a label on a block, node, event or hierarchy: "pumps",
"east yard", "critical spares". Tags cross hierarchies, overlap freely
(one element can have several), and never change the simulation. A
hierarchy is simulation structure; a tag is a way to look at the model
and its results. Why there are tags and no groups is recorded in
[`FEATURE_DECISIONS.md`](FEATURE_DECISIONS.md#2-component-tags-groups-dropped).

## In the model

`.rbds` stores the tags on each element, and optional details per tag (see
[`RBDS_FORMAT.md`](RBDS_FORMAT.md#component-tags-rbds-only)):

```json
"tags": {"block:3": ["pumps", "rotating"], "hier:7": ["intake"]},
"tag_info": {"pumps": {"colour": "#2F80ED", "description": "Feed pumps"}}
```

- A tag with no stored colour takes one from its name, the same in every
  model and session, so the file stores only a colour someone chose.
- An entry in `tag_info` with no element is a tag made but not used yet.
- **A tag on a hierarchy oval means everything inside it** (at any depth)
  wherever results are counted: the chart roll-up, the results-table
  filter and the MCP and CLI member lists.
- A reference to an element the file no longer has is dropped on load. The
  legacy `.rbd` writer drops tags, with a warning before the save.
- Tags travel with Copy/Paste, Duplicate, Import as Hierarchy and Extract
  Hierarchy; a pasted tag keeps the target's colour if it already has one.

## In the application

| Where | What it does |
|---|---|
| **Data ▸ Tags…** (also on the right-click menu and at the end of the Explorer's tag box) | Create, rename (renaming onto an existing tag merges), delete, colour and describe tags; add or remove a tag on the canvas selection. One undo step. |
| **Inspector ▸ Tags** | The selected element's tags as chips: × removes one, the box adds one (an existing tag completes as you type; a new name makes a new tag). One undo step per change. |
| **Model Explorer tag box** | Filters the tree to one tag and dims the rest of the canvas. Hierarchy folders and ovals on the way to a tagged element stay. **Here only** narrows it to the hierarchy open on the canvas, at any depth. The box searches as you type, so a model with hundreds of tags stays usable. |
| **Results ▸ Tag** (beside Hierarchy) | Shows only result rows for elements with the tag. It is the same choice as the Explorer's tag box: choosing in either sets both. It stacks with the Hierarchy filter. |
| **View ▸ Tag Colours** | A stripe above each tagged element: one colour per tag, up to three, with the rest in the tooltip. It is drawn above the element, not as a fill, so it shows next to any result overlay. A legend in the canvas corner lists the tags on this level (searchable past eight); clicking one shows it in the Explorer. A hierarchy oval shows its own tags; open it to see what is tagged inside. |
| **Charts ▸ Scope and Compare** | Tags are listed under **Tags**, after the hierarchies. Under each tag, "in Intake > Pumps" is the tag inside one hierarchy. |

The Explorer view, the dim and the colours are views, not edits: elements
keep their positions, links, selection and their part in the run.

## Charts and results by tag

The downtime roll-up adds a row for each tag, and for each tag inside every
hierarchy that holds one of its elements. A tag row is built the same way
as a hierarchy row: the downtime of outages started by its elements, pooled
over the runs, so its interval is honest.

- **Fixed at run time.** Tag rows are worked out when the run is analysed,
  from the tags at that moment. Editing tags afterwards does not change a
  finished run. Charts of a tag say so once the tags have changed, and a new
  run brings them up to date.
- **No chart of the tags as parts.** Tags overlap and cut across
  hierarchies, so a chart that splits the system into tags (the hierarchy
  treemap and sunburst, the stacked overlay, the "not inside a hierarchy"
  remainder, the scorecard) would draw something false. A tag scope offers
  the ranking (Pareto), run-to-run outcome, chance of reaching a value,
  convergence, small multiples, run by run, cause heatmap, component
  failures, cost Pareto, the importance quadrant, and the downtime, lost
  capacity and cost treemaps of the tag's own elements (those elements are
  a true whole, so their treemap holds).
- **A tag inside a hierarchy** has no per-run list (it would multiply the
  stored results by the number of hierarchies), so its run-by-run charts are
  unavailable; the tag itself has them.
- **Compare** takes tags beside hierarchies in small multiples and run by
  run. The outage-timeline overlay is kept per hierarchy only.

A tag's id in the results is negative and comes from its name, so a chart's
choice survives a re-run that adds other tags.

## Scripting

- CLI: `raptor tags MODEL list|apply|describe|rename|delete|show`
  (see `raptor tags --help`).
- MCP: `list_tags`, `tag_elements`, `describe_tag`, `rename_tag`,
  `delete_tag` (see [`MCP.md`](MCP.md#tags)).
- A chart's `hierarchy` argument (CLI `--hierarchy`, MCP `run_chart`) takes
  a tag as `#pumps` or `Tag: pumps`.
