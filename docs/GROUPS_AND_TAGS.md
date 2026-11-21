# Component groups and tags

A group is a saved view over the model, not a second hierarchy. A
hierarchy is simulation structure: it owns links, ports and the scope of an
isolated run. A group is navigation only, may span hierarchies, and
overlaps freely with other groups.

## In the model

`.rbds` stores both in one place (see
[`RBDS_FORMAT.md`](RBDS_FORMAT.md#component-tags-and-groups-rbds-only)):

```json
"tags": {"block:3": ["pumps", "rotating"]},
"groups": [
  {"id": "g_pumps", "name": "Pumps", "colour": "#2F80ED",
   "members": ["block:3", "block:4"]},
  {"id": "g_rotating", "name": "Rotating equipment",
   "colour": "#9B51E0", "tag": "rotating"}
]
```

- `members` is an explicit list of `block:<id>`, `node:<id>`,
  `event:<id>` or `hier:<id>` references.
- `tag` includes every element carrying that tag, so a group can stay
  current as elements are tagged later.
- A group may have both, or neither: an empty group is kept as a named
  place to collect elements.
- Tags and groups are presentation metadata: changing one never changes a
  distribution, a link or a run result. The legacy `.rbd` writer drops
  them, with the same warning as annotations.
- A reference to an element the file no longer has is dropped when the
  file is loaded and when the editor saves. A group whose members were all
  deleted is kept, empty.

## In the application

**Data ▸ Groups / Tags…** opens the editor.

- **New** creates a group from the canvas selection, or an empty group
  when nothing is selected. Group names must be unique (ignoring case).
- **Add selection / Remove selection** change the explicit member list.
  Elements included only by the tag query stay when removed from the list.
- **Tag query** turns the group into a live query over tags.
- **Apply to selection** replaces the comma-separated tags on every
  selected element; an empty box removes their tags.
- The member list shows element names and counts, marks any reference the
  model no longer has, and summarises very large groups instead of laying
  out every row.
- A hand-edited custom colour is offered in the colour list instead of
  being replaced by the first palette colour.
- The whole dialog is one undo step: OK pushes a single
  `GroupsCommand`; Cancel changes nothing.

The **Model Explorer** gains a group/tag selector beside Expand/Collapse;
its tooltip gives each entry's element count and description. Choosing a
group filters the whole-model tree to its members (hierarchy folders stay
when they hold a member), and dims every element outside the group on the
canvas. Hierarchy ovals on the path to a member stay bright, so a group
that spans levels keeps its context. Choosing **All elements** restores
the full tree and full opacity. Find and the group view combine: a row
must be in the group *and* match the search. An empty result says whether
the group has no members or nothing matched the search.

The canvas dim is a view, not an edit: elements keep their positions,
links, selection and participation in the run. A group view follows the
user through hierarchy levels and clears when another model is opened.
