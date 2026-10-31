# `.rbds` — RAPTOR Studio Model Format (v1)

**Status:** default save format for new models. Legacy `.rbd` files open
exactly as before and are re-saved as `.rbd` unless you explicitly Save
As `.rbds`.

## Goals

1. **Simple** — one UTF-8 JSON document, human-readable, diff-friendly;
   an element shows only what differs from the file's defaults.
2. **Each value stored once** — a setting lives in exactly one place, so
   editing the file by hand (or by an AI) always takes effect.
3. **Complete** — every Raptor 7 column is kept, so nothing is lost and a
   `.rbd` → `.rbds` → `.rbd` round trip is byte-exact, except that the run
   settings come back in Raptor 7's own spelling (a trial count written
   `1` returns as `1.000000`, the same value).
4. **Extensible** — unknown keys are preserved on read→write.
5. **Checked** — every load validates against the JSON Schema
   (`docs/rbds.schema.json`): a wrong value stops the load with a message
   naming the field, an unknown key is reported as a warning.
6. **AI-generatable** — friendly snake_case names for everything an
   author needs; a minimal valid file is ~30 lines.

## Top-level shape

```json
{
  "format": "raptor-studio",
  "version": 2,
  "meta":       { "generator": "RAPTOR Studio 0.9.0", "file_name": "..." },
  "system":     { ...mission-wide settings (not the run settings)... },
  "experiment": { ...the run settings: trials, termination, analyses... },
  "elements": {
    "blocks":       [ Block, ... ],
    "nodes":        [ Node, ... ],
    "events":       [ Event, ... ],
    "hierarchies":  [ Hierarchy, ... ]
  },
  "links":          [ Link, ... ],
  "spare_pools":    [ SparePool, ... ],
  "resources":      [ Resource, ... ],
  "triggers":       [ Trigger, ... ],
  "phases":         [ Phase, ... ],
  "defaults":       { "blocks": Block, ... },
  "comments":       [ {"type": "...", "id": 1, "name": "...",
                       "text": "..."} ],
  "rates": [...], "formulas": {...}, "annotations": [...],
  "display_unit": "...", "view_transform": {...}, "project_meta": {...}
}
```

`meta.generator` records the program and version that wrote the file; it
is informational. The model's file name is the file's own name. `defaults` holds the hidden template rows Raptor 7 keeps
in a model (the settings a new element starts from); they are not
elements. A default row that is not first in its section carries
`"at": <index>` so its place is kept.

All sections except `"format"` are optional. A reader merges whatever is
present over its built-in defaults, exactly like opening a sparse legacy
file.

## Element objects

Every element has **two layers**, and each value is in exactly one:

1. **Friendly fields** — the ones authors (human or AI) set:
   `id`, `name`, `parent` (hierarchy id, 0 = home), `x`, `y`, plus the
   per-kind fields listed below. Numbers are JSON numbers
   (`"fail_dist": 4`, not `"4"`).
2. **`"legacy"`** — the few Raptor 7 columns with no friendly name
   (a phased element's `P 001`… cells, a hierarchy's view state), verbatim
   as text.

**Only what differs from the defaults is written.** An element is read
starting from the file's own `defaults` entry for its kind (or, without
one, the built-in template), and the writer leaves out every field equal
to it — so a block shows only what makes it different. `id`, `name`,
`parent`, `x`, `y` (and a node's `type`) are always written. The
`defaults` entry itself is written against the built-in template.
(Version 1 files wrote every field and read every element from the
built-in template; they still open unchanged.)

Precedence on read: start row ← `legacy` ← friendly fields. A friendly
field always wins over the same column in `legacy`.

**A value is never written twice.** Raptor 7 does not always spell a
number the same way (`"1"` in one file, `"1.000000"` in another). When
the reader would not rebuild a column's exact text from a friendly value,
the writer puts that column in `legacy` only, verbatim, instead of the
friendly field; that keeps a `.rbd` → `.rbds` → `.rbd` round trip
byte-exact. Setting the friendly field by hand overrides it.

### Values the reader computes (never stored)

A value that follows from the rest of the model is not written, so a hand
edit cannot leave it stale. The reader computes:

| Value | From |
|---|---|
| a node's incoming paths (`n`) and outgoing links (`Out`) | the links |
| a block's or event's link counts (`In`, `Out`) | the links |
| a block's parameter counts (`#FParam`, `#RParam`) | its distribution (not for Empirical, whose count is its number of points) |
| a link's level (`parent`) | its two ends, when they are on the same level; a link into or out of a hierarchy stores it |
| a rate's `value` | its `expr` |
| a linked field's number | its rate or expression in `formulas` |
| the header counts | the rows |

**One column is not carried.** `imageString`, the original's system-status picture, is dropped on write
(the product shows the system colour in the status bar instead). A `.rbds` read back fills it from the
template's default so a `.rbd` made from it still opens in real Raptor. A legacy `.rbd` opened and saved
as `.rbd` keeps whatever value it had; only the `.rbds` route forgets it.

**Ids and references.** `id` is optional; an omitted id is auto-assigned from
**one pool shared across every element kind** (Block, Node, Event, Hierarchy,
Link), in section order (blocks → nodes → events → hierarchies → links). The
value therefore depends on how many elements of *other* kinds precede it:
declare four blocks first and the first node becomes `5`, not `1`. The format
itself only requires uniqueness *within* a kind (see the Block `id` row), but
because auto-assigned numbers are pooled, any element that another field points
at by id — `parent`, a hierarchy's `in_id`/`out_id`, a dependency `dep_num`, or
a `["Block", n]`-style link endpoint — should be given an **explicit `id`**
rather than left to auto-assignment.

### Block

| Field | Type | Default | Meaning |
|---|---|---|---|
| id | int | required | unique among Blocks (NOT globally -- a Node/Event/Hierarchy may legitimately share this same number; real Raptor allows it, every reference elsewhere pairs an id with its kind, e.g. `"Block:2"`) |
| name | str | "unnamed" | no spaces, at most 20 characters (RAPTOR Studio refuses longer names) |
| parent | int | 0 | hierarchy id this block lives in |
| x, y | float | 0,0 | canvas position (model units) |
| fail_dist | int | 4 | distribution code (see table) |
| fail_params | [f,f,f] | [100,0,0] | up to 3 params |
| fail_stream | int | 203 | ±1..110 independent (negative = antithetic) or 201..210 = system A–J |
| repair_dist | int | 7 | code |
| repair_params | [f,f,f] | [10,2,0] | |
| repair_stream | int | 203 | same encoding |
| uses_phasing | bool | false | element runs a phase schedule (the schedule itself is the `P 001`… columns in `legacy`) |
| dep_type | str | "None" | what this block follows: `RBDBlock`, `RBDNode`, `RBDEvent`, `RBDHier` or `None` (see Dependency below) |
| dep_num | int | 0 | id of the element it follows; `-1` = its own node (local), `-2` = the whole system |
| dep_nothing, dep_idle, dep_pm, dep_fail | float % | 0, 100, 0, 0 | what happens when the followed element goes down: no effect / forced Idle / preventive maintenance / induced failure. Should add to 100 |
| dep_pm_thresh | float % | 75 | a `dep_pm` reaction only does the PM when the block has operated more than this % of its mean time to failure |
| def_dep_state_idle | bool | false | while waiting on the element it follows, the block sits Idle instead of operating |
| sim_start | int | 3 | how the block starts a run: 1 random state; 2 up, random life used; 3 up, `amount_exhausted` of life used; 4 down, random point in its repair; 5 down, `amount_exhausted` % of the repair done; 6 down, repair just starting |
| amount_exhausted | float | 0 | life used (start 3) or % of the repair done (start 5) |
| standby_stress | float | 0 | failure-clock rate while in standby (0 = no ageing, 1 = as running) |
| growth_decay_type, growth_decay_rate, growth_decay_limit | int, float, float | 0, 1, 0.5 | reliability growth/decay model (type 0 = none) |

**Sparing.** `stock`, `new_spares`, `arrive_every`, `rso`, `eso`, `emer_time`, `slo`, `level`, `quantity` and `slo_time` mean what they mean for a spare pool (see "Auxiliary tables"); they apply when `sparing_type` is `Custom`.

| Field | Type | Default | Meaning |
|---|---|---|---|
| sparing_type | str | "Infinite" | `Infinite`, `Custom` (the block's own stock), `Pooled` (draws from `spare_pool`) or `None` |
| spare_pool | str | "unnamed" | the spare pool a `Pooled` block draws from |
| stock, new_spares, rso, eso, slo, level, quantity | int | 100, 1, 0, 0, 0, 0, 1 | own stock and ordering rules (Custom) |
| arrive_every, emer_time, slo_time | float | 720, 24, 24 | restock interval, emergency shipping time, stock-level order time |
| pre_ldt_dist, pre_ldt_params | int, [f,f,f] | 19, [0,0,0] | logistics delay before a repair starts (19 = Fixed) |
| post_ldt_dist, post_ldt_params | int, [f,f,f] | 19, [0,0,0] | logistics delay after a repair ends |
| num_resources | int | 0 | how many resource types repairs and PM use (0 = none); the file names the first, `resource_name` |
| resource_name | str | "None" | the resource pool repairs and PM draw from |
| repair_resources_needed, pm_resources_needed | int | 1, 1 | units of that resource one repair / one PM needs |

**Preventive maintenance.**

| Field | Type | Default | Meaning |
|---|---|---|---|
| uses_pm | bool | false | the block has scheduled PM |
| pm_freq | float | 100 | operating time between PMs |
| pm_stagger_time | float | 0 | how much earlier the FIRST PM comes: it is due after `pm_freq - pm_stagger_time` of operation |
| pm_duration_dist, pm_params | int, [f,f,f] | 19, [1,0,0] | how long a PM takes |
| pm_triggered, pm_trigger | bool, str | false, "none" | PM is started by the named trigger instead of the interval |
| pm_spare | bool | false | needs a spare to conduct PM |
| pm_refresh | bool | false | after a PM the block is refreshed like new |
| pm_mis_defer | bool | false | defer PM until a non-mission phase (phased models) |
| pm_fail_reset | bool | false | a failure resets the time to the next PM |
| pm_req_defer | bool | false | defer PM until a spare or the resources are available |

The PM clock counts the block's **operating** time, as Raptor 7's does: the
first PM is due after `pm_freq - pm_stagger_time`, and the clock refills to
`pm_freq` when a PM ends, so each later PM is `pm_freq` of operation after
the end of the previous one, not on a fixed calendar. With `pm_freq` 105,120 h
and `pm_stagger_time` 8,760 h, the first PM comes at 96,360 h of operation.

**Costs.** `initial_cost`, `running_cost`, `standby_cost`, `idle_cost`,
`rep_hold_cost`, `rep_per_time_cost`, `rep_fixed_cost`, `done_per_time`,
`done_fixed`, `pm_hold_cost`, `pm_cost`, `pm_fixed_cost`, `per_spare_cost`
and `emer_ship_cost` (floats, default 1) are the block's cost-analysis
rates; `add_done` (bool) always adds the Done cost at run end.

The per-phase states of a phased block (`P 001`, `P 002`, …) are the
columns still written under `legacy`.

`fail_dist` and `repair_dist` also accept a distribution **name**
(`"Weibull"`, `"Uniform Real"`, `"Pearson 5"`; see the table below); the
writer stores the code. An unknown name is an error.

**A block that never fails.** Raptor 7 has no "never fails" setting, and
the file format keeps to what Raptor 7 can open. (`None`, code 18, is a
*repair* distribution: the block is never repaired.) Give the block a
`Fixed` failure time (code 19) far beyond the run length, for example
`"fail_dist": "Fixed", "fail_params": [900000000, 0, 0]`. The largest value
Raptor 7's input rules accept is 999,999,999.999999, so `1e9` is
rejected. If the item can never fail, it can usually be left out of the
diagram instead.

#### Dependency

A dependent block reacts when the element it follows goes down. The
percentages give the chance of each reaction: **Idle** pauses the block
(its failure clock stops and resumes when the target is back), **nothing**
leaves it alone, **pm** sends it for preventive maintenance and **fail**
induces a failure. For `dep_type = RBDHier`, `dep_num` is the hierarchy's
**Out** node id, not the hierarchy id. Use `get_element` (MCP) to read the
decoded setting with a plain-English `meaning`, and `update_element` to
change it. A block that depends on the unit it backs up gives no independent
redundancy: it is idle exactly when it is needed.

### Node

| Field | Type | Default | Meaning |
|---|---|---|---|
| id | int | required | unique among Nodes only -- see the Block `id` row above |
| name | str | | optional; at most 20 characters, no spaces. `start` and `end` are reserved |
| type | int | 2 | 1 Start · 2 Regular · 3 End · 4 In-marker · 5 Out-marker |
| parent | int | 0 | |
| x, y | float | | |
| k | int | 1 | required-good count (K-of-N). `-1` is the "K-of-N dialog never opened" sentinel: it is stored and reported as `-1`, and treated as **1** when the model runs — it does **not** mean "all incoming paths required"; use `k = n` for an all-required node. Real Raptor ignores `k` on an **End** node (it needs one good path there) and its GUI never offers it: put the K-of-N on a node in front of End and link that node to End. `validate_model` warns about an End node with `k > 1`. |
| full_flow | int 0/1 | 1 | "Unrestricted flow" — 1 lets all flow pass straight through this node; **0 makes it a capacity boundary** (see Links/System below) |
| div_flow | int 0/1 | 1 | "Use any available path" — 0 also makes the node a capacity boundary |
| cold_standby | bool | false | engage the standby logic (the node's Standby tab) |
| priority_return | bool | false | return to the primary path once it is repaired |
| check_autos_first | bool | true | try automatic switchover before manual |
| k_star | int | 0 | the Standby tab's k\* (at least `k`) |
| uses_phasing | bool | false | the node has per-phase k values (the `P001`… columns, in `legacy`) |
| dep_type, dep_num | str, int | "None", 0 | what the node follows, as for a block |
| report_node_anal | bool | true | include the node in the node-analysis output |
| report_capacity | int | 0 | Raptor 7's unused report-capacity column |

A node's incoming and outgoing link counts are computed from the links
(see "Values the reader computes").

**Start and In markers allow one outgoing link.** A `Node` of type
`start` (1) or `in` (4) may carry a single outgoing link, and an `end` (3)
or `out` (5) marker a single incoming one. Fanning a Start/In marker out to
several branches therefore needs an ordinary node first:

```
Start -> Split -> (A | B | C)
```

`build_model` enforces this with RAPTOR Studio's own message; a spec that
links a Start/In marker to two elements is refused.

**Fan-in/fan-out only happens through a Node.** A Block or Event allows
at most one incoming and one outgoing link; two blocks feeding the same
downstream element, or one block feeding two others, must each route
through an intermediate Node instead of linking block-to-block directly.
Raptor 7 also needs a Node's `n` to equal its real incoming link count
exactly; the `.rbds` reader computes it from the links, so only `k` needs
setting when not all paths are required. Violating the fan-in/fan-out
rule is an `ERROR` from `build_model`'s validation (ported from
`builder.validate()`); on a hand-edited `.rbd` it crashes real Raptor
with `MODSIM Runtime error: Access Violation` instead, so the check
exists to catch it first. `create_model(template="parallel"|"k_of_n")`
builds a correct converge-through-a-Node example to copy.

### Event

| Field | Type | Default | Meaning |
|---|---|---|---|
| id, name, parent, x, y | | | as Block |
| prob | float 0..1 | 0.5 | firing probability |
| fail_dist, fail_stream | int | 17, 203 | its distribution (17 = Bernoulli) and random-number stream |
| uses_phasing | bool | false | the event has a phase schedule |
| init_cost, op_cost, rep_cost | float | 1, 1, 1 | cost-analysis rates |

### Hierarchy

| Field | Type | Default | Meaning |
|---|---|---|---|
| id | int | required | referenced by children's `parent` |
| name | str | | oval label; at most 20 characters, no spaces |
| parent | int | 0 | enclosing hierarchy (nesting) |
| in_id, out_id | int | required | ids of its type-4/5 marker nodes |
| x, y | float | | oval position on the PARENT level |
| uses_phasing | bool | false | Raptor 7's hierarchy phasing flag |

A hierarchy's view state (`zoom`, `xCenter`, `yCenter`, `xOrigin`,
`yOrigin`, …) is written under `legacy`.

**The In/Out marker nodes' own `parent` must be the hierarchy's `id`
itself** (they live *inside* the hierarchy they mark), not the
hierarchy's own `parent` (its enclosing level). E.g. hierarchy `id=100`
with `in_id=3, out_id=4` needs node 3 and node 4 each written with
`"parent": 100` — not `0` or the id of whatever hierarchy `100` itself
nests inside. Getting this backwards is caught by `validate_model`
(`ERROR: node ... is hierarchy ...'s 'in'/'out' marker but its own
parent=... -- must equal ...`); before that check existed it crashed
model-building with a bare `KeyError`.

### Link

| Field | Type | Default | Meaning |
|---|---|---|---|
| id | int | required | unique among links |
| from | ["Block"\|"Node"\|"Event"\|"Hier", id] | required | source endpoint |
| to | same | required | target endpoint |
| parent | int | from its ends | level the link is drawn on. Only needed (and only written) for a link whose ends are on different levels |
| cold_pri | int | 50 | cold-standby priority (lower switches in first) |
| a_prob | float | 1.0 | probability an automatic switchover succeeds |
| auto_time, man_time | float | 0 | automatic / manual switchover time |
| nom_flow | int | -2 | nominal flow this link carries; `-2` = even share of the node's flow, `-1` = `flow_generated`, `>= 0` = an explicit cap |
| max_flow | int | -1 | maximum flow this link can carry; same `-2`/`-1` sentinels, `>= 0` = explicit cap |
| cap_pri | int | 50 | capacity priority — lower fills first when a boundary node distributes flow |

Capacity caps (`nom_flow`/`max_flow`) bind **only on links entering a
capacity-boundary node** — a node with `full_flow = 0` or `div_flow = 0`
(or a Start/End node). On any other (transparent) node the caps are
ignored. See `capacity_analysis` / `flow_generated` under System below.

Both flow fields are **whole numbers**. The legacy `.rbd` format stores
them with `ReadInt`/`WriteInt`, so a decimal is refused on entry
(`Nominal flow must be a whole number!` / `Max flow must be a whole
number!`) and reported by `validate_model`: the reader stops at the
decimal point, which shifts every later token and makes Raptor 7 crash
with `MODSIM Runtime error: Access Violation` while opening the file. The
analyses engine already truncates a fractional value (`133.76` behaves as
`133`), so enter the whole number you mean.

### Auxiliary tables (spare pools, resources, triggers, phases)

These four sections are **keyed** lists rather than element lists: each entry
is a flat object whose friendly field names map onto the legacy table columns,
and none of them carries a meaningful numeric id. As for elements, an entry
is read starting from its table's defaults and only what differs is written;
files written before 0.9 kept every column under `legacy` with a `key`, and
still open.

```json
"spare_pools": [
  {"name": "RackSpares", "stock": 33, "new_spares": 1,
   "arrive_every": 730, "eso": 1, "emer_time": 730,
   "slo": 0, "level": 0, "quantity": 1, "slo_time": 24,
   "rso": 1, "spare_cost": 1.0, "emer_ship_cost": 1.0}
],
"resources": [
  {"name": "Crew", "num_in": 2, "spare_cost": 1.0,
   "fixed_per_use": 100.0, "per_time_unit": 50.0, "phases": 0}
],
"triggers": [
  {"name": "Storm", "repeats": 1, "init_used": 0.0,
   "dist": 8, "params": [288, 1, 0]}
],
"phases": [
  {"name": "Summer", "num": 1, "mission": 1,
   "duration_dist": 19, "params": [8760, 0, 0]}
]
```

| Section | Friendly fields |
|---|---|
| `spare_pools` | `name`, `stock`, `new_spares`, `arrive_every`, `eso`, `emer_time`, `slo`, `level`, `quantity`, `slo_time`, `rso`, `spare_cost`, `emer_ship_cost` |
| `resources` | `name`, `num_in`, `spare_cost`, `fixed_per_use`, `per_time_unit`, `phases` |
| `triggers` | `name`, `repeats`, `init_used`, `dist`, `params` |
| `phases` | `name`, `num`, `mission`, `duration_dist`, `params` |

A block's `sparing_type` is `Infinite`, `Custom`, `Pooled` or `None`
(Raptor 7 also reads `Resource` and `ColdPool`, as no sparing; the schema
check refuses any other value, which Raptor 7 would silently read as none).
With `Custom`, the block's own `stock`/`restock_every` govern. With `Pooled`,
the **pool's** `stock` and `arrive_every` govern; the block's own `stock`
column is not used. `dist`/`duration_dist` accept a name or a code, like
`fail_dist`.

The API addresses these rows by name (`Pool:RackSpares`, `Resource:Crew`,
`Trigger:Storm`, `Phase:Summer`), and they can be created and edited on a live
model through `manage_spare_pools`, `manage_resources` and `manage_triggers`
(`configure_phases` for phases) — not only at `build_model` time.

### System

Friendly names for the mission-wide knobs (all optional):

`units` ("hours"), `graphics` ("GraphicsOn"/"GraphicsOff"),
`flow_generated` (int, the demand injected at the Start node),
`node_anal_type` ("Availability"/"Dependability"),
`gy_threshold`, `yr_threshold` (floats, availability-style bands),
`sys_streams` ([10+ ints], seeds for shared streams A–J),
`red_cost`, `lost_cost`. Other SYSTEM_DATA columns are in its `legacy`.

### Experiment (run settings)

The run settings live here and nowhere else:

```json
"experiment": {
  "trials": 200,
  "termination_type": "TimeSim",      // "TimeSim" | "FailSim" | "CycleSim"
  "term_condition": 8760.0,           // run length, or failure / cycle count
  "start_stats": 0.0,
  "analyses": ["weak", "cost"],       // any of "weak", "cost", "capacity"
  "importance": "none",               // "none" | "improvement" | "risk" | "both"
  "recording": {"event_log": true, "ao_plot": false, "capacity_plot": false,
                "cost_plot": false, "ao_interval": 10.0, "cost_interval": 720.0}
}
```

`importance` switches on the Annex D importance estimate: `improvement` gives
RRW and Vesely–Fussell, `risk` gives RAW and `both` adds Birnbaum. It costs one
extra batch per block (two for `both`), so it defaults to `none`.

A file without `experiment` (written before it existed) takes its run
settings from the system row's `NumRuns`, `TerminationType`,
`TermCondition`, `StartStats` and analysis columns in `system.legacy`;
with none there either, the defaults apply (1 trial, a 1000-unit
time-terminated run, no analyses).

With `capacity_analysis` on, set `flow_generated` to the total flow
entering the Start node and put explicit `nom_flow`/`max_flow` caps on
the links that feed a capacity-boundary node (`full_flow = 0` or
`div_flow = 0`). Caps on links entering any other node are ignored, and
`flow_generated <= 0` means there is no flow to distribute, so the
analysis reports nothing useful.

## Distribution codes

1 Beta · 2 Chi-square · 3 Binomial · 4 Exponential · 5 Erlang ·
6 Gamma · 7 Lognormal · 8 Normal · 9 Uniform Int · 10 Uniform Real ·
11 Pearson V · 12 Pearson VI · 13 Poisson · 14 Triangular · 15 Weibull ·
16 Empirical · 17 Point Estimate · 18 None · 19 Fixed ·
20 Exp. Power · 21 Extreme Value · 22 Laplace

## Annotations (.rbds only)

Annotations are drawing overlays (text boxes, rectangles, ellipses,
arrows) that live on top of the diagram. They exist ONLY in `.rbds` —
the legacy `.rbd` format has no representation, and saving to `.rbd`
drops them (the GUI warns first).

```json
"annotations": [
  {
    "kind": "text",           // "text" | "rect" | "ellipse" | "arrow"
    "id": 1,
    "parent": 0,              // hierarchy level drawn on
    "x": 120.0, "y": 80.0,
    "w": 140.0, "h": 40.0,    // rect/ellipse only
    "x1": 0.0, "y1": 0.0,     // arrow only: tail
    "x2": 100.0, "y2": 0.0,   // arrow only: head (arrowhead drawn here)
    "text": "Check this branch",
    "z": -50,                 // stacking band -- see "Z-order" below
                              // (new annotations start behind; 20/40 on top)
    "border_colour": "#5D7080",  // null = draw no border (any kind)
    "border_width": 0,        // 0 = no border; arrow's own default is 2
    "line_style": "solid",    // "solid" | "dash" | "dot" | "dash-dot" |
                               // "dash-dot-dot" -- rect/ellipse/arrow border
    "fill_colour": null,      // null = transparent; arrow has no fill
    "text_colour": "#132238",
    "font_family": "sans-serif",
    "font_size": 10,
    "font_bold": false,
    "font_italic": false
  }
]
```

Unknown keys inside an annotation are preserved verbatim under
`"_extra"` on read and re-emitted unchanged.

**`text` is meaningful only for `kind: "text"`.** The renderer draws text
for text annotations only; a `rect`, `ellipse` or `arrow` ignores the
field, so `update_annotation(text=…)` on one of those kinds stores a
value nothing displays. It round-trips through the file unchanged.

**No colour = `null`.** `border_colour: null` and `fill_colour: null`
both mean "draw nothing here" and are honoured identically for every
kind, including a text box's frame. A `border_width` of `0` is the other
way to suppress a border, and the two compose: a null colour always wins.

**Per-kind style defaults are NOT stored here.** The context menu's
"Set as Default for New Rectangles" remembers the style for each kind in
the application's own settings (QSettings), not in the `.rbds`: it is a
personal preference for new shapes, not model data, and a `.rbds` has to
stay portable between Raptor Studio installs and plain Raptor. Only the
fields that kind actually has are kept (a rectangle stores no font, a
text box no line style), and a default saved for one kind never applies
to another.

**Z-order (`z`).** Annotations have three stacking bands, so a note can
sit behind the reliability elements, in the middle (the default), or in
front of everything:

| `z` | band | relative to |
|---|---|---|
| `-50` | behind (default) | below the grid, links, hierarchy ovals **and** blocks/nodes/events |
| `20` | middle | above every element, below the drag handles |
| `40` | front | above every element |

A newly placed annotation starts in the **behind** band so it cannot
cover the blocks it describes. The GUI's *Bring Forward* / *Send
Backward* step to the next value actually in use on the current
hierarchy level (the three bands plus any custom value an entry or
`update_annotation` carries), so a step always lands on a real slot
rather than an arbitrary increment.

The value is persisted per annotation and round-trips through undo/redo,
level changes, save and reload.

**Coordinate space.** `x`/`y` (top-left for text/rect/ellipse) and
`x1`/`y1`/`x2`/`y2` (arrow tail/head) are **scene** coordinates, i.e. the
values the GUI stores from `item.scenePos()`. They are *not* model
coordinates. The canvas maps model coordinates to scene coordinates with
`scene_x = (x − origin_x) · scale` and `scene_y = (flip_base − y) · scale`
using the frozen `view_transform` (below); writing model coordinates
straight into an annotation places it in the wrong spot. The API and the
MCP `add_annotation`/`update_annotation` tools take either: explicit
`x`/`y` are scene coordinates by default, or model coordinates (the
numbers element `x`/`y` use) with `units: "model"`; `w`/`h` are always
scene pixels (a block is 108 x 46). `adjacent_to="SomeBlock"` (or
`adjacent_to="Annotation:7"`) places it next to that target with no
coordinates at all.

**`view_transform`.** `.rbds`-only frozen canvas transform,
`{scale, origin_x, flip_base, dx_min, dy_min}`. It is written the first
time the model is laid out and then reused verbatim on every open, so
element positions and annotation geometry stay put between sessions. A
hand-authored file without it has one derived on open, deterministically,
from the element positions.

**Anchors.** An annotation placed next to an element or another
annotation (`adjacent_to`, or an arrow's `from_`/`to`) carries an
`anchor` —
`{"target": "B1", "ref": "Block:3", "location": "right", "gap": 12}` or
`{"from": "A", "to": "B", "from_ref": "Node:1", "to_ref": "Block:3"}`.
`ref` is the target's kind and id (names need not be unique; an anchor
written before `ref` existed is resolved by name). The name beside it is
kept in step with the target: renaming a block, node, event or
hierarchy (or editing an annotation's text) rewrites it, and Auto Layout
refreshes it too, so it never goes stale. A target can be any
block, node, event or hierarchy, or another annotation
(`Annotation:<id>`; a label is accepted when it is unique). For an
annotation target the bounding box is its stored `x`/`y`/`w`/`h`, or
the box spanned by an arrow's two endpoints. `location` is `right`,
`left`, `above`, `below` or `center`; the default is `above`.
`center` places the annotation over the target and ignores `gap`.

The stored geometry is what is drawn; the anchor says what keeps it
there: the annotation moves with its target when the target is dragged,
aligned or laid out, in the GUI and through `auto_layout`. Anchors can
chain (a note anchored to a note anchored to a block follows the block);
a chain that loops back to the annotation is rejected. In the GUI the
Inspector's **Anchor** group shows and changes the target and location,
**Pick on canvas…** arms a click-to-anchor mode (click an element or
another annotation; Esc cancels), and a dashed line joins a selected
anchored note to its target. Moving the annotation itself by hand (or by
coordinates through `update_annotation`) removes the anchor;
`"anchor": null` removes it and leaves the annotation where it is.

**Nothing re-lays out a model implicitly.** Saving, opening and exporting
keep every position as it is. A model whose spacing is finer than Raptor
7's grid gets a validation warning (Raptor 7 would draw it overlapping)
and the `.rbd` export repeats it; only an explicit Auto Layout (the GUI's
button, the MCP `auto_layout` tool, or `build_model`'s default
`auto_layout=True`) moves elements, and anchored annotations go with them
while the others keep their place in the model.

## Rates (.rbds only)

The named Rates library: reusable value expressions with a human-readable
basis. A rate is defined once and referenced from any numeric field as
`@RateName`, optionally scaled (`@RateName * 1.4`). Entries are:

```json
"rates": [
  {
    "name": "HighwayRate",
    "expr": "0.00015 * 20000 / 8760",   // the typed expression
    "value": 0.0003424657534246575,      // the resolved float (authoritative)
    "description": "0.00015 fail/km, 20000 km/yr"   // optional basis
  }
]
```

`expr` is the derivation; the reader computes `value` from it (the writer
does not store `value` when there is an `expr`). A rate given only a
`value` is a plain constant.
Legacy `.rbd` has no representation, so saving to `.rbd` drops the
library (the GUI confirms first).

## Formulas (.rbds only)

The expression or rate behind a field, so reopening a model shows *why*
a value is what it is and a rate change reaches every field that uses it.
Keyed `"<kind>:<element id>:<legacy column>"` (for example
`"block:5:FParam1"`).

```json
"formulas": {
  "block:5:FParam1": {"expr": "0.00015 * 20000 / 8760"},
  "block:5:FParam2": {"rate": "HighwayRate"}
}
```

A bare `@RateName` field is stored as `{"rate": <name>}`; an arithmetic
expression (with or without an embedded `@Rate`) as `{"expr": <text>}`.
Plain numbers have no entry. On read, every linked field is set from its
rate or expression, so the link wins over a number typed into the field:
to give a linked field a plain number, delete its `formulas` entry.

## Display units (.rbds only)

`display_unit` overrides how times are *shown and entered* without
touching any stored value: an hours-native model can be viewed in years.
`null` (or absent) means "use the model's native `units`".

```json
"display_unit": "years"
```

## Frozen canvas transform (.rbds only)

`view_transform` remembers the diagram's canvas scale/origin so a model
reopens on the same grid it was saved on. Without it, moving one element
can shrink the model's global closest-spacing and rescale the whole canvas
on reopen. Keys are all floats:

```json
"view_transform": {
  "scale": 24.0,
  "origin_x": 0.0,
  "flip_base": 0.0,
  "dx_min": 0.0,
  "dy_min": 0.0
}
```

Legacy `.rbd` has no representation and re-derives a fresh transform on
each load.

## Schema and checking

`docs/rbds.schema.json` is the format's JSON Schema (draft 2020-12),
generated from the reader's own field maps (`tools/gen_rbds_schema.py`;
`raptor schema` prints it). Every load checks the file against it:

* **Errors stop the load**, naming the element and field: a value of the
  wrong type (`elements.blocks[1] "Fuel_Day_Tank".pm_freq: must be a
  number; got 'abc'`), an element or link without its `id`, a link
  without `from`/`to`, an unknown distribution name.
* **Warnings** are unknown keys: kept and written back, but not used, so a
  misspelt field would otherwise be silently ignored. They name the
  nearest real field (`fial_dist: unknown key, kept but not used; did
  you mean fail_dist?`).

`raptor validate MODEL.rbds`, and the MCP `open_model`, `build_model` and
`validate_model` tools, report both. Keys starting with `x_` are an
extension namespace: kept, and never warned about.

## Extensibility rules

* Readers MUST ignore unknown keys at every level (but preserve them —
  see below), and report them as warnings unless they start with `x_`.
* Writers MUST round-trip unknown keys verbatim, top-level ones included.
  Implementation: each parsed object keeps an `_extra` dict; the writer
  emits it unchanged.
* Bumping `version` is only for breaking changes; additive fields do
  NOT bump it. A change an older reader would silently misread counts as
  breaking. Version 2 did both: it moved maintenance, sparing and the other
  API-named columns out of `legacy` into friendly fields, which a version-1
  reader would have ignored, and it started leaving out fields equal to the
  file's `defaults`, which a version-1 reader would have filled from the
  wrong row. A reader refuses a `version` newer than its own, saying which
  RAPTOR Studio saved the file.
* Writers emit one value per setting and never a value the reader computes
  (see "Element objects").

## Minimal example (two fans in parallel, then a power supply)

Node path counts (`n`, `out`) are left out: the reader counts them from the
links. This example is part of the parser's own regression suite and validates clean.

```json
{
  "format": "raptor-studio",
  "version": 2,
  "elements": {
    "blocks": [
      {"id": 1, "name": "Fan_A", "fail_dist": "Exponential",
       "fail_params": [800, 0, 0]},
      {"id": 2, "name": "Fan_B", "fail_dist": "Exponential",
       "fail_params": [800, 0, 0]},
      {"id": 3, "name": "PSU", "fail_dist": "Weibull",
       "fail_params": [1.5, 1200, 0]}
    ],
    "nodes": [
      {"id": 4, "type": 1, "name": "start"},
      {"id": 5, "type": 2, "name": "split"},
      {"id": 6, "type": 2, "name": "either_fan", "k": 1},
      {"id": 7, "type": 3, "name": "end"}
    ]
  },
  "links": [
    {"id": 1, "from": ["Node", 4], "to": ["Node", 5]},
    {"id": 2, "from": ["Node", 5], "to": ["Block", 1]},
    {"id": 3, "from": ["Node", 5], "to": ["Block", 2]},
    {"id": 4, "from": ["Block", 1], "to": ["Node", 6]},
    {"id": 5, "from": ["Block", 2], "to": ["Node", 6]},
    {"id": 6, "from": ["Node", 6], "to": ["Block", 3]},
    {"id": 7, "from": ["Block", 3], "to": ["Node", 7]}
  ],
  "system": {"units": "hours"},
  "experiment": {"trials": 100, "termination_type": "TimeSim",
                 "term_condition": 1000.0}
}
```
