# Large-model work — test and review record

Temporary integration branch: `test/large-model-work`.

This branch rolls up the workstreams below. Each was developed on its own
branch, then reviewed by a separate agent, and the reviewed commits were
merged here.

## Branches

| Branch | Work | Final reviewed commit |
| --- | --- | --- |
| `fix/batch-results-large` | cap/stream End-of-Run, report stages, GUI population stages, weak-metric precompute | `1b9eedbc` |
| `perf/hierarchy-navigation` | parse only the visible level, lazy name index, label/link batch work, route caches | `bf381dbf` |
| `feat/layout-size-aware` | size-aware column pitches, exact GUI extents, scale agreement | `19a416e9` |
| `feat/layout-direction` | orthogonal link styles, LR/RL/TB/BT, max-column wrapping, MCP options | `70e29e63` |
| `feat/routing-nodes` | insert a pass-through routing node into a link, undo/redo, save round-trip | `33f8cb6a` |
| `feat/analytic-dependency-state` | analytical solver `dependencies=up/down` | `44cf5f59` |
| `perf/gui-large-models` | selective drag sync, row index, minimap/selection hot spots | `e6273e0c` |

Integration-only fixes: menu `None` action guard, dependency-link down,
invalid saved direction fallback, strict `max_columns`, generated docs,
golden/snapshot refreshes.

## Automated tests run

- Broad affected suite on integration: **1120 passed, 8 deselected**.
- MCP/data/security subset: **215 passed** (includes `test_mcp_hardening`,
  `test_mcp_security_hardening`, `test_mcp_http`, `test_mcp_server_starts`,
  `test_mcp_large_models`, `test_mcp_layout_scope`, `test_mcp_tools_model`,
  tool snapshot, review fixes).
- New-feature suite: **146 passed, 1 deselected** (results, hierarchy,
  layout size/direction, routing nodes, analytic dependencies, large GUI).
- Per-workstream reviews also ran their own targeted/full subsets:
  report/golden fidelity, GUI workflows, file IO, rbds schema, MCP, SVG.
- Report behaviour suite: **25 passed**.
- CLI/man-page suite: **7 passed** after regenerating `raptor-analytic.1`
  and completions for the new `--dependencies` option.
- `ruff check raptor tests`: clean after the layout review.

## Manual smoke checks

- Offscreen GUI: load a 300-element hierarchy, enter/leave it, Auto Layout
  `TB` / `max_columns=4`, insert a routing node, undo/redo, save to
  `.rbds`, reload. All succeeded.
- CLI: `raptor --help`; `raptor analytic ... --dependencies up` returned a
  valid JSON result with the new assumption; invalid `--dependencies`
  returned exit code 2 and a usage error.
- MCP: `create_model` → `auto_layout` home/TB/4 and `all_levels`/RL;
  `open_model` a dependency fixture → `analytical_analysis` with
  `dependencies=up`/`down`. Invalid enum values are rejected by the MCP
  protocol validator; numeric `max_columns=0` returns a structured
  `invalid_argument` error.
- MCP large-model smoke: 500-block series `build_model` 0.6 s; LR and
  TB/8 auto-layout ~0.5 s; straight and orthogonal 800 px renders ~2.2 s
  and ~2.5 s. Output is correctly marked not legible at that scale.

## Security checks

- MCP path boundary: absolute paths and `../` paths refused; only workspace-relative paths accepted.
- Untrusted selectors (`../../etc/passwd`, missing ids, unknown names) return `not_found` and leave the model unchanged.
- Bad layout options: `max_columns=0/-1` return structured `invalid_argument`;
  `max_columns=True` and unknown enum values are rejected by the MCP
  protocol validator before the tool runs.
- Model names with control characters are refused; spaces/tag-like text are
  accepted as plain labels (rendering paths must HTML-escape them, which the
  report renderer does).
- Workspace symlink escaping: a workspace-relative symlink pointing outside
  the workspace is refused by `open_model` (`not_found`).
- `save_model_as` refuses `../`, absolute paths, and writes into managed
  `models/` with `permission_denied`/`invalid_argument`/`precondition_failed`.
- Existing MCP hardening/security suites: **215 passed**, plus **157** for
  data/diff/get-element/resources/tables/run/review-fix tools.

## Findings fixed during review

- Results dock re-materialised the full stream via `list(...)`; now keeps
  the stream and previews only.
- Printed/PDF raw report used the preview rather than the full stream.
- Temp streams leaked on failed batches; discarded on both sync/bg paths.
- Results population could re-enter via `processEvents`; input events are
  now excluded and the run path refuses while populating.
- Report weak-link metrics computed four times per element/run; now once.
- Hierarchy rebuild parsed every level; now only visible rows, with safe
  hidden-link id allocation and undo/redo.
- Label mean/sigma computed twice per block; single `describe` pass.
- Stale route/centre caches after moves and label width changes.
- Long names overlapped adjacent columns; exact extents now drive column
  pitch and the frozen scale.
- TB/BT transposition collapsed wrapped bands; orientation-aware flow/cross
  axes now keep unique coordinates.
- 3,000-element fan refine was quadratic; skipped when wrapping/size makes
  refinement pointless (**58.5 s → 1.9 s** in review measurement).
- Routing-node context menu matched `None` when dismissed (AttributeError);
  guarded.
- Routing nodes are refused on non-neutral links (capacity/cold standby).
- Analytical dependency choice accidentally waived unrelated standby gates;
  scoped back to dependencies only.
- Analytical invalid enum could surface as `NotApplicable`; now validated as
  a usage error.
- A one-block drag walked every item and row; selective sync makes drag
  release **~3.3 s → ~3.3 ms** on a 10,000-block/5,001-link review model.
- Minimap 200 ms poll hashed every item (~108 ms/tick); now uses a layout
  generation counter (~0.014 ms/tick).

## Residual risks / limitations

- 10,000-item **load/rebuild** is still linear and measured at roughly
  36 s in an isolated worst-case review; hierarchy enter/exit is much
  better, but initial load remains the largest cost.
- Explorer refresh is O(n) per model edit (~1.65 s at 10k); deferred and
  coalesced, not structurally rewritten.
- TB/BT or explicit `max_columns` layouts with 80+ very narrow bands can
  exceed Raptor's 150 x 100 canvas; layout reports `outside_world` and the
  GUI canvas is unbounded.
- Collision refine is intentionally skipped for TB/BT and wrapped layouts.
- `ResultStream.read_preview` reads whole lines before the byte cap; a
  single pathological huge line could still allocate it.
- Export Tables still materialises the full End-of-Run report; it is an
  explicit user action.
- MCP invalid enum values are rejected by pydantic at protocol level rather
  than returned as a structured tool error. Numeric/bounds errors return the
  normal structured `invalid_argument` result.
- Background worker still reads Qt action state while opening report
  streams (pre-existing thread-safety smell).

## Commands

```bash
# Integration branch
git checkout integration/large-model-work

# Broad affected suite (sequential; suitable for this machine)
.venv/bin/python -m pytest tests/unit/test_results_large.py \
  tests/unit/test_hierarchy_navigation.py tests/unit/test_layout_sizes.py \
  tests/unit/test_layout_direction.py tests/unit/test_link_routing.py \
  tests/unit/test_routing_nodes.py tests/unit/test_analytical_dependencies.py \
  tests/unit/test_gui_large_models.py tests/unit/test_mcp_layout_scope.py \
  tests/unit/test_gui_results.py tests/unit/test_reports.py \
  tests/unit/test_editing_workflows.py -q
```
