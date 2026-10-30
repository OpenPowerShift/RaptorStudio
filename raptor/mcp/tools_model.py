"""MCP tools, part 1: model building, editing, and lifecycle.

Registers the model lifecycle, building/editing, model-feature and
reference tools as MCP tools over the API.

Every tool returns a :class:`~mcp.types.CallToolResult` (see
:mod:`raptor.mcp.result`): structured JSON for the assistant, Markdown for
the user.  No reliability logic lives here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.result import ADDITIVE, DESTRUCTIVE, EDIT, READ_ONLY, guarded, markdown_table, ok
from raptor.mcp.server import mcp


# ══════════════════════════════════════════════════════════════════════
# Workspace and lifecycle
# ══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations=READ_ONLY("List models"))
@guarded
def list_models(include_examples: bool = True, limit: int = 100,
                offset: int = 0) -> CallToolResult:
    """List open models and (optionally) bundled example models.

    Returns structuredContent.models — rows of
    {model_id, name, blocks, nodes, last_modified, source} — and
    structuredContent.examples (names) when include_examples=true.
    Use this to find model_id values for the other tools. Long workspaces
    are paged: `limit` (max 200) and `offset`; `total` and `next_offset`
    describe the rest.
    """
    ws = context.workspace()
    all_models = ws.list_models()
    total = len(all_models)
    offset, limit = context.page_args(offset, limit, 200)
    models = [{**m, "source": context.public_source(m.get("source", ""))}
              for m in all_models[offset:offset + limit]]
    examples: list[dict[str, str]] = []
    if include_examples:
        from raptor.mcp.examples import list_examples
        # names only: a file path would tell the caller where the server's
        # install lives
        examples = [{"name": e["name"]} for e in list_examples()]

    md_rows = [[m["model_id"], m["name"], m["blocks"], m["nodes"]] for m in models]
    md = markdown_table(["model_id", "name", "blocks", "nodes"], md_rows) if md_rows else "*No open models.*"
    if total > offset + len(models):
        md += f"\n\n_Showing {offset + 1}-{offset + len(models)} of {total}; call again with offset={offset + len(models)}._"
    if examples:
        md += "\n\nBundled examples: " + ", ".join(e["name"] for e in examples)
    next_offset = offset + len(models) if offset + len(models) < total else None
    return ok({"models": models, "examples": examples, "total": total,
               "next_offset": next_offset}, markdown=md)


@mcp.tool(annotations=ADDITIVE("Open model"))
@guarded
def open_model(path: str) -> CallToolResult:
    """Copy a model file into the workspace and return its handle.

    The original file is never mutated.  Accepts a .rbds or .rbd path
    **relative to the workspace** (or an `--allow-read` directory), or a
    bundled example name like 'MotorDemo'.  Absolute paths are refused,
    as are paths outside those roots, so the MCP boundary cannot read
    arbitrary files.  Returns {model_id, summary}.
    Allowed under --read-only (it never changes the source file).
    """
    path = context.require_relative_path(path)
    ws = context.workspace()
    resolved = _resolve_model_path(path)
    result = ws.open_model(resolved)
    md = (
        f"Opened **{result['summary']}** from `{path}`.\n\n"
        f"model_id: `{result['model_id']}`"
    )
    return ok(result, markdown=md)


_NOT_FOUND = ("No model file at {path!r} in the workspace or an --allow-read "
              "directory, and no bundled example with that name.")


def _resolve_model_path(path: str) -> Path:  # noqa: C901
    """Resolve a file, a model directory, or an example name to a model path.

    MCP is an input boundary: an arbitrary path parameter must not turn
    `open_model` into a read-any-file primitive.  Allowed roots are the
    workspace, the bundled example trees, and explicit `--allow-read`
    directories.  A bare example name is resolved through the example
    finder, so it keeps working without widening the boundary.
    """
    p = Path(path)
    candidate: Path | None = None
    from raptor.mcp import context
    from raptor.mcp.examples import GOLDEN, NEW_DOCS_MODELS
    roots = [context.workspace().root, GOLDEN, NEW_DOCS_MODELS,
             *context.allowed_read_dirs()]

    def _as_model_file(base: Path) -> Path | None:
        if base.is_file():
            return base
        if base.is_dir():
            for name in ("input.rbds", "input.rbd", "model.rbds"):
                found = base / name
                if found.is_file():
                    return found
        return None

    # A relative path is relative to each allowed root, not to whatever
    # directory the server happens to have been started in. Absolute paths
    # are rejected by `context.require_relative_path` before this point.
    if not p.is_absolute():
        for root in roots:
            candidate = _as_model_file(Path(root) / p)
            if candidate is not None:
                break
    if candidate is None:
        candidate = _as_model_file(p)
    if candidate is None:
        from raptor.mcp.examples import find_example
        example = find_example(path)
        if example is not None:
            candidate = example
    if candidate is None:
        raise FileNotFoundError(_NOT_FOUND.format(path=path))

    resolved = candidate.resolve()
    for root in roots:
        try:
            root_resolved = Path(root).resolve()
        except OSError:
            continue
        if resolved == root_resolved or root_resolved in resolved.parents:
            return resolved
    # A file that exists but is outside the allowed roots answers exactly like
    # one that does not exist, so the call cannot be used to probe the host.
    raise FileNotFoundError(_NOT_FOUND.format(path=path))


def _choose_spec(spec: dict[str, Any] | None,
                 spec_path: str | None) -> dict[str, Any]:
    """The inline spec, or the one in `spec_path`; exactly one must be given."""
    if (spec is None) == (spec_path is None):
        raise ValueError("give exactly one of `spec` (inline) and `spec_path` "
                         "(a JSON file in the workspace)")
    if spec is not None:
        return spec
    return _read_spec_file(str(spec_path))


def _read_spec_file(path: str) -> dict[str, Any]:
    """Read a JSON spec from the workspace or an `--allow-read` directory.

    Same boundary as `open_model`: a relative path, resolved under an allowed
    root, and a file outside them answers like one that does not exist."""
    import json as _json
    from raptor.api.limits import MAX_MODEL_BYTES
    path = context.require_relative_path(path, "spec_path")
    roots = [context.workspace().root, *context.allowed_read_dirs()]
    for root in roots:
        try:
            root_resolved = Path(root).resolve()
            found = (root_resolved / path).resolve()
        except OSError:
            continue
        if (root_resolved in found.parents and found.is_file()):
            if found.stat().st_size > MAX_MODEL_BYTES:
                raise ValueError(f"{path!r} is larger than {MAX_MODEL_BYTES} bytes")
            try:
                data = _json.loads(found.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError(f"{path!r} is not valid JSON: {exc}") from None
            if not isinstance(data, dict):
                raise ValueError(f"{path!r} must hold a JSON object (the spec)")
            return data
    raise FileNotFoundError(f"No spec file at {path!r} in the workspace or an "
                            "--allow-read directory.")


@mcp.tool(annotations=ADDITIVE("Create model"))
@guarded
def create_model(name: str,
                 template: Literal["blank", "series", "parallel", "k_of_n"] = "blank",
                 n: int = 2, k: int = 1) -> CallToolResult:
    """Create a new model.

    template: 'blank' (default), 'series' (n blocks in series),
    'parallel' (n in parallel), or 'k_of_n' (k-of-n voting of n blocks).
    Non-blank templates are auto-laid-out on the shared layered grid, so
    the diagram renders cleanly without a separate `auto_layout` call.
    Returns {model_id, summary}.
    """
    context.require_write()
    ws = context.workspace()
    result = ws.create_model(name, template=template, n=n, k=k)
    return ok(result, markdown=(
        f"Created **{result['summary']}** (`{result['model_id']}`) "
        f"from template `{template}`."
    ))


@mcp.tool(annotations=DESTRUCTIVE("Save model as"))
@guarded
def save_model_as(model_id: str, path: str,
                  format: Literal["rbds", "rbd"] = "rbds") -> CallToolResult:
    """Save a model to a file path.

    The path must be inside the workspace or an --allow-write directory.
    format: 'rbds' (default) or 'rbd'.  If `path` has **no extension** the
    format's own extension is appended (so `"out"` -> `out.rbds`).  An
    explicit extension must match `format`; a mismatch or a path into the
    managed models/runs/renders trees is refused.  A .rbd export runs
    validate() first and refuses on ERROR.
    """
    context.require_write()
    ws = context.workspace()
    path = context.require_relative_path(path)
    dest = ws.resolve_output_path(path, allow_extra=context.allowed_write_dirs())
    result = ws.save_model_as(model_id, dest, fmt=format)
    md = f"Saved `{result['bytes']}` bytes to `{result['path']}`."
    dropped = result.get("dropped") or []
    if dropped:
        md += ("\n\n**Not carried by .rbd:** " + ", ".join(dropped)
               + ". The .rbd opens in RAPTOR Studio without these.")
    return ok(result, markdown=md)


@mcp.tool(annotations=DESTRUCTIVE("Close model"))
@guarded
def close_model(model_id: str | list[str],
                delete: bool = False) -> CallToolResult:
    """Close one or more models.  Set delete=true to also remove their files.

    `model_id` accepts a single id or a list, for cleaning up several
    scratch/sweep models in one call. Each id is closed independently and
    only the ids you pass are affected -- other open models are never
    touched, whatever their names. One bad id doesn't abort the rest; the
    failures are reported alongside the successes.

    `delete=true` removes that model's own workspace directory (its
    `model.rbds`, backups and meta) and is **irreversible** -- the undo
    history goes with it. It does not delete exported files (use
    `delete_file`) or runs.
    """
    context.require_write()
    ws = context.workspace()
    raw_ids = [model_id] if isinstance(model_id, str) else list(model_id)
    ids = list(dict.fromkeys(raw_ids))
    closed: list[str] = []
    failed: dict[str, str] = {}
    for mid in ids:
        try:
            ws.close(mid, delete=delete)
            closed.append(mid)
        except Exception as exc:  # noqa: BLE001 -- one bad id shouldn't abort the batch
            failed[mid] = f"{type(exc).__name__}: {exc}"
    verb = "Closed and deleted" if delete else "Closed"
    data = {"closed": closed, "failed": failed}
    if len(ids) == 1:
        if failed:
            raise ValueError(f"Could not close `{ids[0]}`: {failed[ids[0]]}")
        md = f"{verb} `{ids[0]}`."
    else:
        md = f"{verb} {len(closed)}/{len(ids)}."
        if failed:
            md += " Failed: " + ", ".join(f"`{k}` ({v})" for k, v in failed.items())
    return ok(data, markdown=md)


@mcp.tool(annotations=DESTRUCTIVE("Delete file"))
@guarded
def delete_file(path: str) -> CallToolResult:
    """Delete a file previously exported with `save_model_as` (or any
    other file inside the workspace or an --allow-write directory).

    Does not touch an open model's own files -- use `close_model(...,
    delete=true)` for those. Refuses a path outside the workspace/
    allow-write directories, same containment `save_model_as` enforces.
    """
    context.require_write()
    ws = context.workspace()
    path = context.require_relative_path(path)
    target = ws.resolve_output_path(path, allow_extra=context.allowed_write_dirs())
    if not target.is_file():
        raise FileNotFoundError(f"no such file: {path!r}")
    # A model's own directory (model.rbds, meta.json, backups) is not an
    # exported file. This includes CLOSED models, whose files are meant to
    # survive a restart; deleting one succeeded while the in-memory handle
    # kept answering, so the loss only appeared later.
    models_dir = (ws.root / "models").resolve()
    if target == models_dir or models_dir in target.parents:
        raise ValueError(
            f"{target} belongs to the workspace's model store; use "
            "close_model(delete=true) to remove it")
    runs_dir = (ws.root / "runs").resolve()
    if target == runs_dir or runs_dir in target.parents:
        raise ValueError(
            "run artefacts are managed by the engine; use the run "
            "retention tools instead of delete_file")
    renders_dir = (ws.root / "renders").resolve()
    if target == renders_dir or renders_dir in target.parents:
        raise ValueError(
            "renders are managed by render_diagram; use the render "
            "retention policy instead of delete_file")
    size = target.stat().st_size
    target.unlink()
    return ok({"deleted": str(target), "bytes": size},
             markdown=f"Deleted `{target}` ({size} bytes).")


# ══════════════════════════════════════════════════════════════════════
# Building and editing
# ══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations=ADDITIVE("Build model"))
@guarded
def build_model(name: str, spec: dict[str, Any] | None = None,
                auto_layout: bool = True,
                validate_model: bool = True,
                model_id: str | None = None,
                spec_path: str | None = None) -> CallToolResult:
    """Build a complete model from a friendly .rbds spec document.

    Give the spec inline as `spec`, or, for a large one, as `spec_path`: a
    JSON file **relative to the workspace** (or an `--allow-read`
    directory). Exactly one of the two. To open a finished `.rbds` or
    `.rbd` file instead, use `open_model`, which takes the same kind of path.

    `build_model` always creates a NEW model. `model_id` is accepted only
    to reject it with a clear message: it used to be ignored silently, so
    a caller that passed one believed it was appending to that model.
    Use `add_elements`/`add_block` to edit an existing model.

    `spec` is a JSON object with `blocks`, `nodes`, `events`, `hierarchies`,
    `links`, `system`, `experiment`, `rates`, `spare_pools`, `resources`,
    `triggers`, `phases` and `annotations`, using the friendly snake_case
    fields from docs/RBDS_FORMAT.md (e.g. a block's `fail_dist`/`fail_params`;
    distributions may be given by name or code).  Ids
    are assigned where omitted, links are wired, counts synced.  This is
    the main tool for creating a whole model in one call.

    `experiment` takes the .rbds run block itself (`trials`,
    `termination_type`, `term_condition`, `analyses`, `recording`, ...), so
    the run settings no longer need a second `configure_system` call.

    RAPTOR Studio's input rules apply as they do to every edit: a spec with a link
    RAPTOR Studio will not let you draw (Start linked to two blocks, say -- use
    Start -> a Node -> the blocks) or a value its dialogs refuse is rejected
    with RAPTOR Studio's own message.

    `spare_pools`/`resources`/`triggers`/`phases` are lists of dicts built
    with the same row constructors the `configure_*` tools use.  Friendly
    fields per entry:
    - spare_pools: name, stock, new_spares, arrive_every, eso, emer_time,
      slo, level, quantity, slo_time, rso
    - resources: name, num_in, spare_cost, fixed_per_use, per_time_unit,
      phases
    - triggers: name, init_used, dist, params, repeats
    - phases: name, duration_dist, params, mission
    - phase_states (top level, needs `phases`): {element name or "Block:N": one
      state per phase}.  Block/event state `"A: 1.000"` (active at that
      stress/probability), `"C: 1.000"` cut, `"L: 1.000"` linked, events also
      `"F"`/`"P"`; node state `"C"`, `"L"` or an integer k.  Elements not
      listed are unphased.
    `phases` here does **not** take per-element `assignments` (that needs a
    live model) -- build first, then call `configure_phases`.

    `annotations` is a list of drawing overlays (text/rect/ellipse/arrow)
    placed after auto-layout, so `adjacent_to`/`from_`/`to` resolve to real
    element positions.  Each entry takes the `add_annotation` fields (note
    the arrow spelling is `from_`); `add_annotation` adds one afterwards.

    IDs: a Block, Node, Event and Hierarchy each have their OWN id space
    in the file format -- a Block `id=2` and a Node `id=2` are different,
    unrelated elements, and RAPTOR Studio itself allows this (every real
    fixture this project has captured bears it out). Auto-assigned ids
    (when you omit `id`) are allocated from one shared pool across every
    kind, so two auto-assigned elements never collide -- but if you set
    `id` explicitly, nothing stops you from choosing one that matches an
    existing element of a DIFFERENT kind, and downstream tools/reports
    always disambiguate by kind (`"Block:2"`, not bare `2`) for exactly
    this reason. When giving a selector or a dependency `dep_num` to any
    tool, always pair the id with its kind. And because auto-assigned ids
    are pooled in section order, any element you intend to reference by
    id (`parent`, a hierarchy's `in_id`/`out_id`, a link endpoint,
    `dep_num`) should be given an explicit `id` -- the number it would
    otherwise auto-receive is not predictable from declaration order.

    `auto_layout` (default True) places every element on the shared
    layered grid (Start left -> End right) so the model's diagram renders
    cleanly; pass False only when the spec carries its own `xPos`/`yPos`
    coordinates you want to keep.

    Format: `.rbds` is the native, preferred format; only produce an `.rbd`
    when the user explicitly asks (via `save_model_as(format="rbd")`).

    Capacity analysis: set `system.capacity_analysis: true` and
    `system.flow_generated` (the demand), give links `nom_flow`/`max_flow`,
    and mark the boundary node(s) with `full_flow: false` — capacity caps
    only bind on links entering a node whose unrestricted flow is off.
    Link friendly fields: `nom_flow`, `max_flow`, `cap_pri`, `cold_pri`,
    `a_prob`, `auto_time`, `man_time`.

    Parallel/converging paths: a node's `n` must equal its real incoming
    link count exactly (validation refuses the mismatch -- RAPTOR Studio
    crashes on it) and every node, including End, defaults to `n=1`. Two
    blocks converging on End directly is therefore an error; either give
    End `"n": 2` (plus `"k"` if not all paths are required), or route
    through an intermediate node first, e.g. for A and B in parallel:
    `nodes: [{"name":"Join","type":"regular","n":2,"k":1}, ...]`,
    `links: [{"from":"A","to":"Join"}, {"from":"B","to":"Join"},
    {"from":"Join","to":"End"}]` (`create_model(template="parallel")`
    builds exactly this shape for a plain A||B case).

    Returns {model_id, summary, validation}.  A successful build is **not**
    the same as a runnable model: check `validation.runnable` -- the build
    can succeed structurally while `issues()` still reports engine-gate
    errors (e.g. `k > n`).
    """
    context.require_write()
    if model_id is not None:
        raise ValueError(
            "build_model always creates a new model; it cannot append to an "
            "existing one. Call build_model without `model_id`, or use "
            "add_block/add_elements against the model you want to edit.")
    ws = context.workspace()
    from raptor.api.build import build_from_spec
    from raptor.api.limits import MAX_SPEC_BYTES
    from raptor.api.validate import issues

    import json as _json
    spec = _choose_spec(spec, spec_path)
    try:
        encoded = _json.dumps(spec, default=str).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"spec must be JSON-serialisable: {exc}") from None
    if len(encoded) > MAX_SPEC_BYTES:
        raise ValueError(
            f"spec is too large ({len(encoded)} bytes); the limit is "
            f"{MAX_SPEC_BYTES} bytes")

    rbd = build_from_spec(spec, auto_layout=auto_layout)
    # A spec is a drawing: what RAPTOR Studio refuses to draw or accept in a
    # dialog is refused here too, the same rules every edit tool meets.
    from raptor.core.input_rules import InputRuleViolation, new_violations
    refused = new_violations(None, rbd)
    if refused:
        raise InputRuleViolation(refused)
    result = ws.register_model(rbd, name=name, source="build_model")

    validation: dict[str, Any] | None = None
    if validate_model:
        found = issues(rbd)
        validation = {
            "errors": [str(e) for e in found if e.severity == "error"],
            "warnings": [str(e) for e in found if e.severity == "warning"],
            "runnable": not any(e.severity == "error" for e in found),
        }
        result["validation"] = validation

    md = f"Built **{result['summary']}** (`{result['model_id']}`)."
    if validation:
        md += (f"\n\nValidation: {len(validation['errors'])} error(s), "
               f"{len(validation['warnings'])} warning(s).")
        for e in validation["errors"][:10]:
            md += f"\n- {e}"
    return ok(result, markdown=md)


def _named_elements_md(result: dict) -> str:
    """Block and event id/name tables for describe_model."""
    md = ""
    for key, label in (("block_names", "block"), ("event_names", "event")):
        rows = [[item.get("id"), item.get("name")]
                for item in result.get(key, [])]
        if rows:
            md += "\n\n" + markdown_table(["id", label], rows)
    return md


@mcp.tool(annotations=READ_ONLY("Describe model"))
@guarded
def describe_model(model_id: str, detail: Literal["summary", "full"] = "summary",  # noqa: C901
                   level: int = 0) -> CallToolResult:
    """Describe the model structure.

    detail='summary' gives counts plus a topology sentence; detail='full'
    lists every element in one response (use get_table for paged input
    tables).  `level` filters to a hierarchy level (0 = whole model).
    """
    ws = context.workspace()
    from raptor.api.build import describe
    from raptor.api.limits import MAX_DESCRIBE_ELEMENTS
    if detail == "full":
        handle = ws.get(model_id)
        total = sum(len(handle.rbd.section(name).rows) for name in (
            "BLOCK_DATA", "NODE_DATA", "EVENT_DATA", "HIERARCHY_DATA",
            "LINK_DATA"))
        if total > MAX_DESCRIBE_ELEMENTS:
            raise ValueError(
                f"model has {total} elements; detail='full' is limited to "
                f"{MAX_DESCRIBE_ELEMENTS}. Use get_table for paged input "
                "tables instead.")
    result = describe(ws, model_id, detail=(detail == "full"), level=str(level))

    link_count = result.get("link_count", result.get("links"))
    if isinstance(link_count, list):
        link_count = len(link_count)
    md = (f"**{result['name']}** — {result['blocks']} block(s), "
          f"{result['nodes']} node(s), {result['events']} event(s), "
          f"{link_count} link(s).")
    md += _named_elements_md(result)

    hiers = result.get("hierarchies") or []
    if hiers:
        md += "\n\n**Hierarchies** (use the id as `parent`, or `Hier:<id>`)\
\n\n" + markdown_table(
            ["id", "hierarchy", "parent", "in", "out", "selector"],
            [[h.get("id"), h.get("name"), h.get("parent"),
              h.get("in_id"), h.get("out_id"), h.get("selector")]
             for h in hiers])

    links = result.get("link_rows") or result.get("links_detail")
    if links is None and isinstance(result.get("links"), list):
        links = result["links"]
    if isinstance(links, list) and links and isinstance(links[0], dict):
        first = links[0]
        if "from_type" in first:
            def _ep(link: dict, side: str) -> str:
                return f"{link.get(side + '_type')} {link.get(side + '_id')}"
            md += "\n\n**Links**\n\n" + markdown_table(
                ["from", "to", "nom_flow", "max_flow", "cap_pri"],
                [[_ep(link_row, "from"), _ep(link_row, "to"), link_row.get("nom_flow"),
                  link_row.get("max_flow"), link_row.get("cap_pri")] for link_row in links])
        else:
            headers = [h for h in ("from", "to", "nom_flow", "max_flow",
                                   "cap_pri")
                       if any(h in link_row for link_row in links)] or list(first)
            md += "\n\n**Links**\n\n" + markdown_table(
                headers, [[link_row.get(h) for h in headers] for link_row in links])

    nodes = result.get("nodes_detail") or result.get("node_details")
    if isinstance(nodes, list) and nodes and isinstance(nodes[0], dict):
        headers = [h for h in ("id", "name", "type", "k", "n",
                               "full_flow", "div_flow")
                   if any(h in node for node in nodes)] or list(nodes[0])
        md += "\n\n**Nodes**\n\n" + markdown_table(
            headers, [[node.get(h) for h in headers] for node in nodes])

    system = result.get("system")
    if isinstance(system, dict) and system:
        md += "\n\n**System**\n\n" + markdown_table(
            ["setting", "value"], [[k, v] for k, v in system.items()])
    return ok(result, markdown=md)


@mcp.tool(annotations=ADDITIVE("Add block"))
@guarded
def add_block(
    model_id: str,
    name: str,
    fail_dist: int | str | None = None,
    fail_params: list[float | str] | None = None,
    repair_dist: int | str | None = None,
    repair_params: list[float | str] | None = None,
    parent: int = 0,
    after: str | None = None,
) -> CallToolResult:
    """Add a block to the model.

    `fail_dist`/`repair_dist` take a distribution name ("Weibull") or a
    code from `list_distributions`.
    Numeric parameters accept expression strings with a `#` comment for
    traceability, e.g. "0.00015 * 20000 / 8760  # failure rate".  `after`
    wires the new block after an existing element name/id, which must not
    already have its one link out. A block takes at most one incoming and
    one outgoing link (RAPTOR Studio) -- to feed it from/to more than one other
    element, route through a Node (`add_node` + `connect`; see `connect`).
    Returns {block_id, name, warnings}.
    """
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import add_block as _add_block
    kwargs: dict[str, Any] = {}
    if fail_dist is not None:
        kwargs["fail_dist"] = fail_dist
    if fail_params is not None:
        kwargs["fail_params"] = fail_params
    if repair_dist is not None:
        kwargs["repair_dist"] = repair_dist
    if repair_params is not None:
        kwargs["repair_params"] = repair_params
    if parent:
        kwargs["parent"] = parent
    row = _add_block(ws, model_id, name, after=after, **kwargs)
    warnings = _placement_warnings(ws, model_id, row, "BLOCK_DATA")
    data = {
        "block_id": row.get("ID"),
        "name": row.get("blockName", name),
        "warnings": warnings,
    }
    md = f"Added block **{data['name']}** (id {data['block_id']})."
    if warnings:
        md += "\n\n" + "\n".join(f"- {w}" for w in warnings)
    return ok(data, markdown=md)


def _placement_warnings(ws: Any, model_id: str, row: dict,
                        section: str) -> list[str]:
    """Soft problems with a just-placed element, plus any layout notice
    the save raised.  Both are things the caller would otherwise only
    discover by looking at the diagram and wondering why it moved."""
    warnings: list[str] = []
    try:
        from raptor.api.build import check_new_block
        warnings += check_new_block(ws.get(model_id).rbd, row)
    except Exception:  # noqa: BLE001 - a warning must never fail a placement
        pass
    notice = getattr(ws.get(model_id), "layout_notice", None)
    if notice:
        warnings.append(notice)
    return warnings


@mcp.tool(annotations=ADDITIVE("Add node"))
@guarded
def add_node(
    model_id: str,
    type: Literal["start", "regular", "end", "in", "out"] = "regular",
    k: int = -1,
    name: str = "",
    parent: int = 0,
) -> CallToolResult:
    """Add a node to the model.

    type: 'start', 'regular', 'end', 'in' or 'out'.

    k: K-out-of-N threshold.  A node's n comes from its *incoming* links,
    so set k only after the voting blocks are wired INTO the node.  `-1`
    (the default) is RAPTOR's "K-of-N never set" sentinel and is treated
    as 1-of-N -- it does **not** mean "all incoming paths required"; use
    `k = n` for an all-required node.  It is stored verbatim as `-1`, so
    `get_element` reports `k=-1`; the coercion to 1 happens when the model
    runs.
    """
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import add_node as _add_node
    row = _add_node(ws, model_id, name, ntype=type, k=k, parent=parent)
    data = {"node_id": row.get("ID"), "name": row.get("Name", name)}
    return ok(data, markdown=f"Added {type} node **{data['name']}** (id {data['node_id']}).")


@mcp.tool(annotations=ADDITIVE("Add event"))
@guarded
def add_event(model_id: str, name: str, prob: float = 1.0) -> CallToolResult:
    """Add an event to the model.  `prob` is its probability (0-1)."""
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import add_event as _add_event
    row = _add_event(ws, model_id, name, prob=prob)
    data = {"event_id": row.get("ID"), "name": row.get("eventName", name)}
    return ok(data, markdown=f"Added event **{data['name']}** (id {data['event_id']}).")


@mcp.tool(annotations=ADDITIVE("Connect"))
@guarded
def connect(model_id: str, from_element: Any = None,
            to_element: Any = None,
            replace: bool = False) -> CallToolResult:
    """Connect two elements with a link.

    `from_element`/`to_element` are either ["Block", id] / ["Node", id]
    or a bare element name like "PumpA".

    `replace=True` removes any link already leaving the source and any
    link already entering the target first, so a wrong connection is
    corrected in one call. Without it, RAPTOR Studio's own limit (one link
    in/out per element) refuses the second connection.

    RAPTOR Studio's link rules apply, and a link it will not let you draw is
    refused with its own message, leaving the model unchanged: a block,
    event or hierarchy takes at most one link in and one out; a Start or In
    marker one link out and none in; an End or Out marker one link in and
    none out. Fan-in/fan-out (two blocks feeding one element, or Start
    feeding two) goes through a Node: `add_node` first, then connect each
    element to it.
    """
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import connect as _connect
    ftype, fid = _resolve_endpoint(ws, model_id, from_element)
    ttype, tid = _resolve_endpoint(ws, model_id, to_element)
    row = _connect(ws, model_id, ftype, fid, ttype, tid, replace=replace)
    data = {"link_id": row.get("ID")}
    return ok(data, markdown=f"Linked {ftype} {fid} -> {ttype} {tid} (link id {data['link_id']}).")


@mcp.tool(annotations=DESTRUCTIVE("Delete link"))
@guarded
def delete_link(model_id: str, link_id: str) -> CallToolResult:
    """Delete one link by id, leaving both elements in place.

    This is the safe alternative to deleting an element just to break one
    connection (which removed every other link on that element too). Find
    link ids in `describe_model(detail='full')` or `get_table('inputs.links')`.
    """
    context.require_write()
    from raptor.api.build import delete_link as _delete_link
    result = _delete_link(context.workspace(), model_id, link_id)
    return ok(result, markdown=f"Deleted link {link_id}.")


@mcp.tool(annotations=EDIT("Move element"))
@guarded
def move_element(model_id: str, selector: str, parent: int) -> CallToolResult:
    """Move a block, node, event or hierarchy to another hierarchy level.

    `parent=0` moves it back to the top level; otherwise pass a hierarchy
    id. `update_element` refuses `parent` because it is structural, and
    the only alternative used to be deleting and recreating the element
    (losing its links, sparing and PM settings). A hierarchy cannot be
    moved into itself or one of its own descendants.
    """
    context.require_write()
    from raptor.api.build import move_element as _move
    row = _move(context.workspace(), model_id, selector, parent)
    return ok({"moved": selector, "parent": parent, "element": row},
              markdown=f"Moved `{selector}` to hierarchy {parent}.")


@mcp.tool(annotations=ADDITIVE("Add hierarchy"))
@guarded
def add_hierarchy(
    model_id: str,
    name: str,
    parent: int = 0,
) -> CallToolResult:
    """Create an empty hierarchy -- a sub-model boundary -- with its In and
    Out markers already wired up correctly.

    This is the fiddly part to do by hand: each marker's own `parent` must
    be the new hierarchy's id (not the enclosing level) and both markers'
    `n` must match their real link counts, or the engine crashes on load
    with an unhelpful KeyError. Building one through `build_model` means
    declaring `in_id`/`out_id`, the two marker nodes with their `parent`
    set to the hierarchy's own id, and their link counts -- three
    non-obvious constraints.

    The hierarchy starts empty and unwired. Add contents with
    `add_block(parent=<hier_id>)` (read the id from
    `describe_model(detail='full')`, or from the return value here), then
    `connect` the In marker to the first element and the last element to
    the Out marker. To build a hierarchy *around* elements you already
    have, use `collapse_selection` instead -- it does all the rewiring.

    Returns {hier_id, name, in_id, out_id}.
    """
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import add_hierarchy as _add_hierarchy
    row = _add_hierarchy(ws, model_id, name, parent=parent)
    data = {
        "hier_id": row["ID"],
        "name": row["hierarchyName"],
        "in_id": row["in"],
        "out_id": row["out"],
        "parent": row["Parent"],
    }
    return ok(
        data,
        markdown=f"Added hierarchy **{data['name']}** (id {data['hier_id']}), "
                 f"with In marker {data['in_id']} and Out marker "
                 f"{data['out_id']}.\n\n"
                 f"It is empty and not connected to anything yet. Add "
                 f"contents with `add_block(parent={data['hier_id']}, ...)` "
                 f"and wire it up with `connect`. To wrap elements that "
                 f"already exist, use `collapse_selection` instead.",
    )


@mcp.tool(annotations=EDIT("Collapse selection"))
@guarded
def collapse_selection(
    model_id: str,
    selectors: list[str],
    name: str | None = None,
    parent: int = 0,
) -> CallToolResult:
    """Move existing blocks/nodes/events into a new hierarchy, rewiring the
    boundary.

    The API counterpart of the GUI's *Collapse Selection*. `selectors` names
    the elements (by name, or `Block:N` / `Node:N` / `Event:N`) that should
    become the new hierarchy's contents. Links wholly inside travel down a
    level untouched; links that crossed the boundary are rewritten to
    address the hierarchy, and each is split so the sub-model's own graph
    stays complete (the outside half keeps its id, an inside half is added
    off the In/Out marker).

    Start/End/In/Out nodes and existing hierarchies are refused -- a
    hierarchy cannot contain the model's own terminators, and nesting one
    hierarchy inside another needs the boundary wired by hand.

    This is the one-call way to wrap a branch you have already built, and
    the way to reproduce the book's "three ovals" figures. Read
    `describe_model(detail='full')` afterwards to see the new level.

    Returns {hier_id, name, in_id, out_id, moved}.
    """
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import collapse_selection as _collapse
    result = _collapse(ws, model_id, selectors, name, parent=parent)
    moved = [r.get("blockName") or r.get("Name") or r.get("eventName")
             for r in result.get("moved", [])]
    data = {
        "hier_id": result["ID"],
        "name": result["hierarchyName"],
        "in_id": result["in"],
        "out_id": result["out"],
        "parent": result["Parent"],
        "moved": moved,
    }
    rows = [[s, "yes"] for s in selectors]
    return ok(
        data,
        markdown=f"Collapsed {len(selectors)} element(s) into hierarchy "
                 f"**{data['name']}** (id {data['hier_id']}); boundary links "
                 f"rewired to the hierarchy.\n\n"
                 + markdown_table(["element", "moved in"], rows)
                 + f"\n\nSee the inside with `render_diagram(level="
                   f"{data['hier_id']})`, or navigate it with "
                   f"`get_element('{data['name']}')`.",
    )


def _resolve_endpoint(ws: Any, model_id: str, elem: Any) -> tuple[str, int]:
    """Resolve ["Block", 3] / "PumpA" to (kind, id)."""
    from raptor.api.build import resolve_element
    rbd = ws.get(model_id).rbd
    if isinstance(elem, (list, tuple)) and len(elem) == 2:
        kind, ident = elem
        # Resolve the list form through the same validator as a textual
        # selector: accepting ["Widget", 1] wrote a LINK_DATA row with an
        # unknown FromType that only failed on the next validate.
        k = str(kind).strip().lower()
        k = {"hierarchy": "Hier", "hier": "Hier"}.get(
            k, str(kind).strip().capitalize())
        try:
            ident = int(ident)
        except (TypeError, ValueError):
            raise ValueError(f"invalid element id {ident!r}") from None
        section, row = resolve_element(rbd, f"{k}:{ident}")
    else:
        section, row = resolve_element(rbd, str(elem))
    kind = {
        "BLOCK_DATA": "Block", "NODE_DATA": "Node",
        "EVENT_DATA": "Event", "HIERARCHY_DATA": "Hier",
    }.get(section, section)
    return kind, int(row["ID"])


@mcp.tool(annotations=READ_ONLY("Get element"))
@guarded
def get_element(model_id: str, selector: str,
                detail: Literal["summary", "full"] = "summary") -> CallToolResult:
    """Read one block, node, event, hierarchy or link back in full.

    `selector` is an element name or 'Block:N' / 'Node:N' / 'Event:N' /
    'Hier:N' / 'Link:N' (links have no name; use their id).  This is the
    read counterpart of `update_element`: every field uses the friendly name
    `update_element` accepts.

    A block returns its connections (upstream/downstream), `failure` and
    `repair` (distribution name, named parameters, mean/stdev), any
    `logistics_delay` before/after repair, `sparing`, `maintenance`,
    `dependency` and `phasing`.  `dependency` says what the block follows
    (a block, node, event, hierarchy, its own node, or the whole system),
    the % split of what happens when that goes down (nothing / idle / pm /
    failure) and a plain-English `meaning`.  detail='full' adds costs,
    growth/decay and standby stress.

    A distribution's mean/stdev is null (with a note) when it has no finite
    value for those parameters, e.g. a Pearson 5 with shape <= 2.
    """
    ws = context.workspace()
    from raptor.api.inspect import get_element as _get
    data = _get(ws, model_id, selector, detail)
    return ok(data, markdown=_element_markdown(data))


#: At most this many field changes in a `detail="summary"` diff.
_DIFF_SUMMARY_LIMIT = 50


@mcp.tool(annotations=READ_ONLY("Diff models"))
@guarded
def diff_models(model_a: str, model_b: str, ignore: list[str] | None = None,
                match: Literal["auto", "name", "id"] = "auto",
                detail: Literal["summary", "full"] = "summary") -> CallToolResult:
    """Say what is different between two models (their inputs, not their results).

    This compares *the models themselves*: elements added, removed, renamed or
    changed (distributions, sparing, maintenance, costs...), connections added
    or removed, hierarchy moves, k-of-n changes, system settings, rates, spare
    pools, resources, triggers and phases. It runs nothing. `compare_models`
    is the other one: it runs models and compares their *results*.

    `model_a` / `model_b` are workspace model ids (the reference and the one
    changed); open files with `open_model` first, this tool does not load
    paths. Elements are paired by name, then by id (`match`: "auto"; "name" or
    "id" to use only one; "id" is right for a model against an earlier copy of
    itself).

    `ignore` lists categories to leave out. The default (omit it) is
    ["layout", "derived"]: screen positions and columns the file reader
    computes. Pass [] to show everything. Categories: layout, derived,
    identity, failure, repair, streams, sparing, maintenance, logistics,
    dependency, cost, phasing, capacity, structure, system, project, other.

    detail="summary" returns the counts, the added/removed/renamed lists and
    at most 50 field changes (`truncated` says whether more exist;
    `total_changes` counts them); detail="full" returns every change. Each
    change has `field`, `label`, `category` and the values `a` and `b`
    (distributions are described in words, with raw columns in `a_raw` /
    `b_raw`).
    """
    ws = context.workspace()
    from raptor.api import model_diff
    from raptor.api.markdown import diff_markdown
    kept = model_diff.DEFAULT_IGNORE if ignore is None else tuple(ignore)
    full = model_diff.diff(ws, model_a, model_b, match=match, ignore=kept)
    from raptor.api.limits import MAX_DIFF_CHANGES
    limit = (_DIFF_SUMMARY_LIMIT if detail == "summary"
             else MAX_DIFF_CHANGES)
    result = model_diff.limit_changes(full, limit)
    md = diff_markdown(result)
    if result["truncated"]:
        if detail == "full":
            md += (f"\n\n*Showing the first {MAX_DIFF_CHANGES} of "
                   f"{result['total_changes']} field changes; the response "
                   "size limit applies. Narrow the diff with `ignore` or "
                   "compare smaller models.*")
        else:
            md += (f"\n\n*Showing the first {limit} of "
                   f"{result['total_changes']} field changes; use "
                   "detail='full' for more (up to the response limit).*")
    return ok(result, markdown=md)


def _dist_line(d: dict[str, Any]) -> str:
    if not d:
        return "(none)"
    params = d.get("params")
    if isinstance(params, dict):
        ptxt = ", ".join(f"{k}={v:g}" for k, v in params.items())
    else:
        ptxt = ", ".join(f"{v:g}" for v in (params or []))
    line = f"{d['name']}({ptxt})"
    if d.get("mean") is not None:
        line += f" — mean {d['mean']:.6g}"
        if d.get("stdev") is not None:
            line += f", stdev {d['stdev']:.6g}"
    for note in d.get("notes", []):
        line += f" ({note})"
    return line


def _element_markdown(d: dict[str, Any]) -> str:
    def refs(items: list[dict[str, Any]]) -> str:
        return ", ".join(f"{i.get('name') or i.get('id')} ({i['kind']})"
                         for i in items) or "—"

    if d["kind"] == "Link":
        return (f"**Link {d['id']}**: {d['from']['name']} → {d['to']['name']}"
                f" — nom_flow {d['nom_flow']}, max_flow {d['max_flow']}, "
                f"cap_pri {d['cap_pri']}")
    lines = [f"**{d['kind']} {d.get('name')}** (id {d['id']})",
             f"- upstream: {refs(d.get('upstream', []))}",
             f"- downstream: {refs(d.get('downstream', []))}"]
    if d["kind"] == "Block":
        lines.append(f"- failure: {_dist_line(d['failure'])}")
        lines.append(f"- repair: {_dist_line(d['repair'])}")
        delay = d.get("logistics_delay")
        if delay:
            lines.append(f"- delay before repair: {_dist_line(delay['before_repair'])}; "
                         f"after repair: {_dist_line(delay['after_repair'])}")
        dep = d["dependency"]
        lines.append(f"- dependency: {dep['meaning']}")
        sp = d["sparing"]
        lines.append(f"- sparing: {sp['type']}"
                     + (f", stock {sp['stock']}" if sp.get("stock") is not None else ""))
        lines.append("- maintenance: "
                     + ("on" if d["maintenance"]["enabled"] else "off"))
    elif d["kind"] == "Node":
        lines.append(f"- type {d['node_type']}, k={d['k']}, n={d['n']}, "
                     f"full_flow={d['full_flow']}, div_flow={d['div_flow']}")
    elif d["kind"] == "Event":
        lines.append(f"- probability {d['prob']}")
    return "\n".join(lines)


def _patch_description() -> str:
    from raptor.api.build import settable_fields
    fields = "; ".join(f"{kind}: {', '.join(names)}" for kind, names in settable_fields().items())
    return ("Field -> value. Friendly names by element kind (the .rbd file's column names and indexed forms such as "
            f"repair_params[0] also work) -- {fields}")


@mcp.tool(annotations=EDIT("Update element"))
@guarded
def update_element(model_id: str, selector: str,
                   patch: Annotated[dict[str, Any], Field(description=_patch_description())],
                   full: bool = False) -> CallToolResult:
    """Update fields on an element.

    `selector` is an element name, 'Block:N' / 'Node:N' / 'Event:N', a link
    ('Link:N', which carries capacity: `nom_flow`, `max_flow`, `cap_pri`),
    or an auxiliary row by name ('Pool:RackSpares', 'Resource:Crew',
    'Trigger:Storm', 'Phase:Summer' -- the bare name works too).
    `patch` accepts any friendly .rbds field (e.g. `fail_params`,
    `pm_freq`, pool `stock`) or a raw column name, including indexed forms
    like `repair_params[0]`.  `fail_dist`/`repair_dist` take a distribution
    name ("Weibull") or a code.  To make a block dependent on another element
    use `dep_type` (RBDBlock/RBDNode/RBDEvent/RBDHier), `dep_num` (the
    target's id; -1 = own node, -2 = whole system) and the effect split
    `dep_nothing`/`dep_idle`/`dep_pm`/`dep_fail` (percent); read it back
    with `get_element`.

    Numeric fields may be numbers OR expression strings such as
    "50.0 + @Rate * 1.4  # basis"; expressions are resolved to a float and
    their derivation is kept in the model's `formulas`.  Returns
    {changed, warnings, element} where `element` is a compact
    {id, name, kind} summary; pass full=true for every field of the element.

    A value RAPTOR Studio's dialogs refuse (a PM stagger at or above the PM
    frequency, a cost below 0, a name with a space, ...) is refused with
    RAPTOR Studio's own message and the model is left unchanged. A problem the
    model already had (a file written elsewhere) does not block other edits.
    """
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import update_element as _update
    row = _update(ws, model_id, selector, patch)
    summary = _element_summary(row)
    if full:
        # `row` is the raw .rbd dict (legacy column names -- `FDist`,
        # `FParam1`, ...); re-resolve through `get_element`'s own
        # friendly-name translation instead of returning it verbatim, so
        # `full=true` matches what `get_element(detail="full")` returns
        # for the same element (the two are documented as the same
        # shape). Re-select by kind:id, not the original `selector`: a
        # patch that renamed the element would leave a by-name selector
        # pointing at nothing.
        from raptor.api.inspect import get_element as _get_full
        kind_token = _FULL_SELECTOR_KIND.get(summary["kind"], summary["kind"])
        element = _get_full(ws, model_id, f"{kind_token}:{summary['id']}", "full")
    else:
        element = summary
    data: dict[str, Any] = {
        "changed": patch,
        "warnings": [],
        "element": element,
    }
    md = (f"Updated {', '.join(f'`{k}`' for k in patch)} on "
          f"{data['element'].get('kind', 'element')} "
          f"`{data['element'].get('name')}`.")
    if data["warnings"]:
        md += "\n\n" + "\n".join(f"- {w}" for w in data["warnings"])
    return ok(data, markdown=md)


@mcp.tool(annotations=EDIT("Update elements"))
@guarded
def update_elements(
    model_id: str,
    updates: Annotated[list[dict[str, Any]], Field(
        description='List of {"selector": str, "patch": {...}} objects')],
) -> CallToolResult:
    """Update many elements with one save.

    Same `selector` and `patch` semantics as `update_element`, but the whole
    list is applied inside one workspace batch: the model is written once at
    the end instead of once per element. On a large model this is the
    difference between seconds and tens of minutes (one `update_element` on
    a ~10k-element model costs ~10 s).

    The batch is atomic: if any item is refused, the model is restored to
    its state before the call and nothing is written.

    Returns {updated, results: [{selector, id, name, kind, changed}]}.
    """
    context.check_batch(updates, "updates")
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import update_element as _update
    results: list[dict[str, Any]] = []
    with ws.batch(model_id):
        for index, item in enumerate(updates):
            if not isinstance(item, dict) or not item.get("selector"):
                raise ValueError(
                    f"updates[{index}] needs a 'selector'")
            selector = str(item["selector"])
            patch = item.get("patch") or {}
            if not isinstance(patch, dict):
                raise ValueError(f"updates[{index}].patch must be an object")
            row = _update(ws, model_id, selector, patch)
            summary = _element_summary(row)
            results.append({"selector": selector, **summary,
                            "changed": list(patch)})
    data = {"updated": len(results), "results": results}
    md = f"Updated {len(results)} element(s) in one save."
    return ok(data, markdown=md)


@mcp.tool(annotations=ADDITIVE("Connect many"))
@guarded
def connect_many(
    model_id: str,
    links: Annotated[list[dict[str, Any]], Field(
        description='List of {"from": endpoint, "to": endpoint} objects')],
) -> CallToolResult:
    """Create many links with one save.

    Endpoints are the same as `connect` (a name, or ["Block", id] /
    ["Node", id] / ["Hier", id]). Applied as one atomic batch; the model is
    written once. Returns {connected, links: [{link_id, from, to}]}.
    """
    context.check_batch(links, "links")
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import connect as _connect
    made: list[dict[str, Any]] = []
    with ws.batch(model_id):
        for index, item in enumerate(links):
            if not isinstance(item, dict) or "from" not in item or "to" not in item:
                raise ValueError(f"links[{index}] needs 'from' and 'to'")
            source, target = item["from"], item["to"]
            ftype, fid = _resolve_endpoint(ws, model_id, source)
            ttype, tid = _resolve_endpoint(ws, model_id, target)
            row = _connect(ws, model_id, ftype, fid, ttype, tid)
            made.append({"link_id": row.get("ID"),
                         "from": f"{ftype} {fid}", "to": f"{ttype} {tid}"})
    data = {"connected": len(made), "links": made}
    return ok(data, markdown=f"Created {len(made)} link(s) in one save.")


@mcp.tool(annotations=ADDITIVE("Add elements"))
@guarded
def add_elements(
    model_id: str,
    elements: Annotated[list[dict[str, Any]], Field(
        description='List of {"kind": "block"|"node"|"event", ...fields}')],
) -> CallToolResult:
    """Add many blocks, nodes or events with one save.

    Each item is `kind` plus the same fields the single add tool takes:

    - block: `name`, optional `fail_dist`, `fail_params`, `repair_dist`,
      `repair_params`, `parent`, `after`
    - node: `name`, `type` (start/regular/end/in/out), `parent`, `k`
    - event: `name`, `prob`, `parent`

    Items are created in list order, so a later item may use `after` to wire
    itself to an earlier one. Atomic: a refused item restores the model.
    Returns {added, elements: [{kind, id, name}]}.
    """
    context.check_batch(elements, "elements")
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import (add_block as _add_block,
                                  add_event as _add_event,
                                  add_node as _add_node)
    added: list[dict[str, Any]] = []
    with ws.batch(model_id):
        for index, item in enumerate(elements):
            if not isinstance(item, dict):
                raise ValueError(f"elements[{index}] must be an object")
            kind = str(item.get("kind", "")).strip().lower()
            fields = {k: v for k, v in item.items() if k != "kind"}
            if kind == "block":
                name = fields.pop("name", None)
                if not name:
                    raise ValueError(f"elements[{index}] (block) needs a name")
                block_fields = {k: fields.pop(k) for k in
                                ("fail_dist", "fail_params", "repair_dist",
                                 "repair_params", "parent") if k in fields}
                row = _add_block(ws, model_id, str(name),
                                 after=fields.pop("after", None),
                                 **block_fields)
            elif kind == "node":
                name = fields.pop("name", None)
                row = _add_node(ws, model_id, str(name or ""),
                                type=fields.pop("type", None),
                                k=int(fields.pop("k", -1)),
                                parent=int(fields.pop("parent", 0)))
            elif kind == "event":
                name = fields.pop("name", None)
                if not name:
                    raise ValueError(f"elements[{index}] (event) needs a name")
                row = _add_event(ws, model_id, str(name),
                                 prob=float(fields.pop("prob", 0.5)),
                                 parent=int(fields.pop("parent", 0)))
            else:
                raise ValueError(
                    f"elements[{index}].kind must be block, node or event")
            summary = _element_summary(row)
            added.append({"kind": summary["kind"], "id": summary["id"],
                          "name": summary["name"]})
    data = {"added": len(added), "elements": added}
    return ok(data, markdown=f"Added {len(added)} element(s) in one save.")


@mcp.tool(annotations=DESTRUCTIVE("Delete elements"))
@guarded
def delete_elements(
    model_id: str,
    selectors: Annotated[list[str], Field(
        description="Element names or Kind:id selectors to delete")],
    reconnect: bool = False,
) -> CallToolResult:
    """Delete many elements with one save.

    `reconnect` behaves as for `delete_element`. Applied as one atomic
    batch; a refused selector restores the model.
    Returns {deleted: [{selector, kind}], links_removed}.
    """
    context.check_batch(selectors, "selectors")
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import delete_element as _delete
    done: list[dict[str, Any]] = []
    removed = 0
    with ws.batch(model_id):
        for selector in selectors:
            result = _delete(ws, model_id, selector, reconnect=reconnect)
            done.append({"selector": selector,
                         "kind": result.get("kind", "")})
            removed += int(result.get("links_removed", 0) or 0)
    data = {"deleted": done, "links_removed": removed}
    return ok(data, markdown=f"Deleted {len(done)} element(s) in one save.")


def _element_summary(row: dict[str, Any]) -> dict[str, Any]:
    """A compact {id, name, kind} view of an element row (default response)."""
    if "blockName" in row:
        kind, name = "Block", row.get("blockName")
    elif "eventName" in row:
        kind, name = "Event", row.get("eventName")
    elif "hierarchyName" in row:
        kind, name = "Hierarchy", row.get("hierarchyName")
    elif "Name" in row:
        kind, name = "Node", row.get("Name")
    elif "FromType" in row:
        kind, name = "Link", f"{row.get('FromType')} {row.get('FromID')} -> {row.get('ToType')} {row.get('ToID')}"
    else:
        kind, name = "Element", row.get("Name") or row.get("ID")
    return {"id": row.get("ID"), "name": name, "kind": kind}


#: `_element_summary`'s display kind ("Hierarchy") -> the short token
#: `get_element`/`resolve_element` selectors use ("Hier:N"); every other
#: kind's token is spelled the same as its display kind.
_FULL_SELECTOR_KIND = {"Hierarchy": "Hier"}




#: Every section whose rows carry canvas positions.
_LAYOUT_SECTIONS = ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA",
                    "HIERARCHY_DATA")


def _layout_scope(rbd: Any, hierarchy: str | int | None,
                  selectors: list[str] | None) -> tuple[str, set[tuple[str, str]] | None]:
    """The auto-layout scope: a title, and the rows it may repaint.

    ``allowed=None`` means the whole model. Resolving the scope is
    separate from applying it so the tool body reads as a sequence, and
    so the "hierarchy or selectors, never both" rule is tested directly.
    """
    if hierarchy is not None and selectors:
        raise ValueError("pass either hierarchy or selectors, not both")
    from raptor.api.build import resolve_element

    if hierarchy is not None:
        section, row = resolve_element(rbd, str(hierarchy))
        if section != "HIERARCHY_DATA":
            raise ValueError(f"{hierarchy!r} is not a hierarchy")
        hid = str(row["ID"])
        allowed: set[tuple[str, str]] = set()
        for sec in _LAYOUT_SECTIONS:
            for r in rbd.section(sec).rows:
                if str(r.get("ID")) == "0":
                    continue
                parent = r.get("ParentId", r.get("parent", "0"))
                eid = str(r.get("ID"))
                if str(parent) == hid or (
                        sec == "HIERARCHY_DATA" and eid == hid):
                    allowed.add((sec, eid))
        return f"hierarchy {row.get('hierarchyName', hid)!r}", allowed

    if selectors:
        selected: set[tuple[str, str]] = set()
        for selector in selectors:
            section, row = resolve_element(rbd, str(selector))
            selected.add((section, str(row["ID"])))
        return f"{len(selected)} selected element(s)", selected
    return "model", None


def _layout_coords(rbd: Any) -> dict[tuple[str, str], tuple[Any, Any]]:
    """Every positioned row, keyed by (section, id)."""
    out: dict[tuple[str, str], tuple[Any, Any]] = {}
    for sec in _LAYOUT_SECTIONS:
        for r in rbd.section(sec).rows:
            eid = str(r.get("ID"))
            if eid != "0":
                out[(sec, eid)] = (r.get("xPos"), r.get("yPos"))
    return out


def _apply_layout_positions(rbd: Any,
                            allowed: set[tuple[str, str]] | None,
                            report: dict | None = None) -> int:
    """Lay the whole model out on a copy and adopt the in-scope positions.

    A partial run of the layout engine does not know where its neighbours
    are and produces "wonky" subsets (the GUI's DiagramView.auto_layout
    reaches the same conclusion). Returns the number of rows changed.
    """
    import copy
    from raptor.core.fileio.legacy_layout import normalize_legacy_layout

    trial = copy.deepcopy(rbd)
    normalize_legacy_layout(trial, force=True, report=report)
    before, after = _layout_coords(rbd), _layout_coords(trial)
    applied = 0
    rows_by_section = {
        sec: {str(r.get("ID")): r for r in rbd.section(sec).rows}
        for sec in _LAYOUT_SECTIONS
    }
    for key, xy in after.items():
        if allowed is not None and key not in allowed:
            continue
        if before.get(key) == xy:
            continue
        sec, eid = key
        row = rows_by_section[sec].get(eid)
        if row is not None:
            row["xPos"], row["yPos"] = xy
            applied += 1
    return applied


@mcp.tool(annotations=EDIT("Auto layout"))
@guarded
def auto_layout(model_id: str,
                hierarchy: str | int | None = None,
                selectors: list[str] | None = None) -> CallToolResult:
    """Re-arrange a model's diagram onto the shared layered grid.

    Runs the same engine the GUI's Auto Layout button and .rbd save
    normalization use: longest-path columns from the Start node(s)
    (left = upstream), rows spread within each column, so no connector
    runs through a block.  Positions are written back to the model file,
    so a subsequent `render_diagram` reflects them.

    Scope the pass with one of:

    - `hierarchy`: a hierarchy name or id — only that level's elements
      are repositioned; every other level keeps its coordinates.
    - `selectors`: names / `Kind:id` of the elements to move. The layout
      is still computed for the whole level (a subset laid out in
      isolation does not line up with its neighbours), but only the named
      elements adopt the new positions.

    With neither, every level is laid out. `build_model` already lays out
    by default (`auto_layout=True`).

    The collision-refining step has a time budget (10 s in all): a dense
    model can otherwise take minutes. If it runs out, the layered layout is
    kept with whatever refinement was done, and `refine_cut_short` says how
    many levels were left less than fully refined (0 when none).

    Returns {moved, model_id, scope, elements_moved, refine_cut_short}.
    """
    context.require_write()
    ws = context.workspace()
    handle = ws.get(model_id)
    scope, allowed = _layout_scope(handle.rbd, hierarchy, selectors)
    report: dict = {}
    applied = _apply_layout_positions(handle.rbd, allowed, report)
    if applied:
        ws.save(model_id)
    cut = int(report.get("refine_cut_short", 0))
    data = {"model_id": model_id, "moved": bool(applied), "scope": scope,
            "elements_moved": applied, "refine_cut_short": cut}
    md = (f"Auto-laid out {scope}: {applied} element(s) moved."
          if applied else
          f"Auto layout over {scope}: nothing needed to move.")
    if cut:
        md += (f"\n\nThe refinement stopped at its time budget on {cut} "
               "level(s); the layout is layered and valid but may still have "
               "connectors crossing blocks.")
    return ok(data, markdown=md)


@mcp.tool(annotations=DESTRUCTIVE("Delete element"))
@guarded
def delete_element(model_id: str, selector: str,
                   reconnect: bool = True) -> CallToolResult:
    """Delete an element from the model.

    reconnect=true removes its links and rewires adjacent connections.
    Returns {removed, links_removed}.
    """
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import delete_element as _delete
    result = _delete(ws, model_id, selector, reconnect=reconnect)
    return ok(result, markdown=(
        f"Deleted {result.get('kind', 'element')} `{selector}` "
        f"({result.get('links_removed', 0)} link(s) removed)."
    ))


@mcp.tool(annotations=DESTRUCTIVE("Undo last edit"))
@guarded
def undo_last_edit(model_id: str) -> CallToolResult:
    """Step one edit back (multi-step: call again to go further back).

    Every edit tool backs up the prior state first (up to 20 deep), so
    repeated calls walk further back rather than repeating the same
    restore. Each undo can itself be undone with `redo_last_edit`, until
    a new edit is made -- which clears the redo history, same as any
    other editor.
    """
    context.require_write()
    ws = context.workspace()
    result = ws.undo(model_id)
    return ok(result, markdown=f"Undid one edit (restored version {result.get('restored_version')}).")


@mcp.tool(annotations=DESTRUCTIVE("Redo last undo"))
@guarded
def redo_last_edit(model_id: str) -> CallToolResult:
    """Step one edit forward again after `undo_last_edit` (multi-step).

    Fails with a clear error once there's nothing left to redo, or once
    a new edit has cleared the redo history.
    """
    context.require_write()
    ws = context.workspace()
    result = ws.redo(model_id)
    return ok(result, markdown=f"Redid one edit (restored version {result.get('restored_version')}).")


# ══════════════════════════════════════════════════════════════════════
# Model features with dedicated tools
# ══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations=EDIT("Configure sparing"))
@guarded
def configure_sparing(
    model_id: str,
    selector: str,
    mode: Literal["infinite", "custom", "pool"] = "infinite",
    spare_pool: str | None = None,
    stock: int | None = None,
    restock_every: float | None = None,
    emergency: float | None = None,
    slo: int | None = None,
    level: int | None = None,
    quantity: int | None = None,
    slo_time: float | None = None,
) -> CallToolResult:
    """Configure sparing for a block.

    mode: 'infinite' (default), 'custom', or 'pool'. `spare_pool` is
    required for mode='pool' and must name an existing pool (create one
    with `manage_spare_pools`).
    stock: initial spare count; restock_every: restock interval (hours);
    emergency: emergency shipping time (hours); slo: stock level order.
    With mode='pool' the POOL's stock and restock interval govern; the
    block's own `stock` only applies to mode='custom'.
    Returns {applied, warnings}.
    """
    context.require_write()
    ws = context.workspace()
    if mode == "pool":
        if not spare_pool:
            raise ValueError("mode='pool' requires spare_pool")
        pools = {r.get("PoolName", "")
                 for r in ws.get(model_id).rbd.section(
                     "SPARE_POOL_DATA").rows}
        if spare_pool not in pools:
            raise ValueError(
                f"spare pool {spare_pool!r} not found; available: "
                f"{', '.join(sorted(p for p in pools if p)) or 'none'}")
    from raptor.api.build import configure_sparing as _cfg
    row = _cfg(
        ws, model_id, selector, mode=mode, spare_pool=spare_pool,
        stock=stock, restock_every=restock_every, emergency=emergency,
        slo=slo, level=level, quantity=quantity, slo_time=slo_time,
    )
    data = {"applied": True, "warnings": [], "element": row}
    return ok(data, markdown=f"Applied sparing mode `{mode}` to `{selector}`.")


@mcp.tool(annotations=EDIT("Configure maintenance"))
@guarded
def configure_maintenance(
    model_id: str,
    selector: str,
    pm_freq: float | None = None,
    pm_duration_dist: int | None = None,
    pm_params: list[float] | None = None,
    trigger_name: str | None = None,
    defer_flags: dict[str, bool] | None = None,
) -> CallToolResult:
    """Configure preventive maintenance for a block.

    pm_freq: maintenance interval (hours).  pm_duration_dist/pm_params:
    the PM duration distribution.  trigger_name: a trigger element (for
    triggered PM).

    defer_flags: bool-valued PM Options, matching the block Properties
    dialog exactly (unknown keys raise an error):
    - `spare`: needs a spare on hand to conduct PM.
    - `refresh`: after PM completes, refresh the block like new.
    - `mis_defer`: defer PM until a non-mission phase occurs (only
      meaningful when the model has phases).
    - `req_defer`: defer PM until a spare or resources are available.
    - `fail_reset`: a failure resets the time to next PM activity.
    """
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import configure_maintenance as _cfg
    row = _cfg(
        ws, model_id, selector, pm_freq=pm_freq,
        pm_duration_dist=pm_duration_dist, pm_params=pm_params,
        trigger_name=trigger_name, defer_flags=defer_flags,
    )
    data = {"applied": True, "element": row}
    return ok(data, markdown=f"Configured maintenance on `{selector}`.")


@mcp.tool(annotations=EDIT("Configure phases"))
@guarded
def configure_phases(
    model_id: str,
    phases: list[dict[str, Any]] | None = None,
    assignments: dict[str, list] | None = None,
    mode: Literal["replace", "add", "remove"] = "replace",
    remove: list[str] | None = None,
    unassign: list[str] | None = None,
) -> CallToolResult:
    """Configure mission phases.

    phases: [{name, duration_dist, params, mission}].

    `mode='replace'` (default) makes `phases` the whole list;
    `mode='add'` appends them to the phases already there (cells for the
    new phase are filled with Raptor's default); `mode='remove'` deletes
    the phases named in `remove` and shifts the rest down.

    `unassign` names blocks/events/nodes whose phasing is cleared (flag
    off, per-phase cells dropped) -- previously there was no MCP route
    for this.

    assignments: {element: [per-phase state]}, one list entry per phase
    (a short list is an error, not padded).  A block/event state is
    `"<code>: <value>"` -- code `A` (Active), `C` (Cut) or `L` (Linked),
    value the stress multiplier, e.g. `"A: 1.000"`, `"C: 0.500"`; the
    colon and value are required even for Cut (a bare word like "Cut"
    crashes the engine's phase parser instead of erroring here). A node
    state is a bare token: `"C"`, `"L"`, or an integer k-of-n threshold
    for that phase.  Phase rows are always built through rbdlib.make_phase_row.
    """
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import configure_phases as _cfg
    result = _cfg(ws, model_id, phases, assignments=assignments,
                  mode=mode, remove=remove, unassign=unassign)
    return ok(result, markdown=f"Configured {result.get('phases', 0)} phase(s).")


@mcp.tool(annotations=EDIT("Configure system"))
@guarded
def configure_system(
    model_id: str,
    run_length: float | None = None,
    num_runs: int | None = None,
    termination: Literal["TimeSim", "CycleSim"] | None = None,
    start_stats: float | None = None,
    units: str | None = None,
    weak_link: bool | None = None,
    cost: bool | None = None,
    capacity: bool | None = None,
    gy_threshold: float | None = None,
    yr_threshold: float | None = None,
    system_streams: str | None = None,
    display_unit: str | None = None,
    flow_generated: float | None = None,
) -> CallToolResult:
    """Configure system-level simulation settings.

    run_length: simulation duration (hours unless `units` says otherwise).
    termination: 'TimeSim' or 'CycleSim'.  display_unit overrides the
    display unit without changing stored values.  capacity=true enables
    capacity analysis; `flow_generated` sets its demand (the flow the
    system is asked to deliver).  Returns {applied, previous}.
    """
    context.require_write()
    ws = context.workspace()
    from raptor.api.build import configure_system as _cfg
    result = _cfg(
        ws, model_id,
        run_length=run_length, num_runs=num_runs, termination=termination,
        start_stats=start_stats, units=units, weak_link=weak_link,
        cost=cost, capacity=capacity, gy_threshold=gy_threshold,
        yr_threshold=yr_threshold, system_streams=system_streams,
        display_unit=display_unit, flow_generated=flow_generated,
    )
    applied = result.get("applied", {})
    md = "Updated system settings" + (
        ": " + ", ".join(f"`{k}`" for k in applied) if applied else "."
    )
    return ok(result, markdown=md)


@mcp.tool(annotations=EDIT("Manage rates", idempotent=False))
@guarded
def manage_rates(
    model_id: str,
    action: Literal["list", "add", "update", "delete"] = "list",
    name: str | None = None,
    expr: str | None = None,
    description: str | None = None,
) -> CallToolResult:
    """Manage the model's named Rates library.

    action: 'list', 'add', 'update' or 'delete'.
    `expr` is an expression like "0.00015 * 20000 / 8760  # failure rate".
    Rates can then be referenced in any numeric field as `@RateName * k`,
    which keeps the derivation in the model's `formulas`.
    Returns {rates} for 'list', else the affected entry.
    """
    if action != "list":
        context.require_write()
    ws = context.workspace()
    from raptor.api.rates import list_rates, add_rate, update_rate, delete_rate

    if action == "list":
        rates = list_rates(ws, model_id)
        if isinstance(rates, str):
            import json as _json
            rates = _json.loads(rates)
        rows = [[r.get("name"), r.get("expr"), r.get("value")] for r in rates]
        md = markdown_table(["name", "expr", "value"], rows) if rows else "*No rates defined.*"
        return ok({"rates": rates}, markdown=md)
    if action not in ("add", "update", "delete"):
        raise ValueError(f"unknown action {action!r}; use list/add/update/delete")
    if not name:
        raise ValueError(f"action {action!r} requires a rate name")
    if action in ("add", "update") and expr is None:
        raise ValueError(f"action {action!r} requires an expr")
    rate_name, rate_expr = str(name), str(expr)
    if action == "add":
        entry = add_rate(ws, model_id, rate_name, rate_expr,
                         description=description)
    elif action == "update":
        entry = update_rate(ws, model_id, rate_name, rate_expr,
                            description=description)
    else:
        entry = delete_rate(ws, model_id, name)
    verb = {"add": "added", "update": "updated", "delete": "deleted"}[action]
    return ok(entry, markdown=f"Rate `{name}` {verb}.")


@mcp.tool(annotations=EDIT("Configure block defaults"))
@guarded
def configure_block_defaults(
    action: Literal["list", "set", "clear"] = "list",
    fail_dist: int | None = None,
    fail_params: list[float] | None = None,
    repair_dist: int | None = None,
    repair_params: list[float] | None = None,
) -> CallToolResult:
    """Set the distributions `add_block` uses when you don't name any.

    This is the MCP equivalent of the GUI's *Block Defaults* button: set
    your house values once and every block you place picks them up, instead
    of restating four numbers on every `add_block` call.

    - `action='list'` — show the current defaults (`{}` means "use each
      model's own template-row defaults").
    - `action='set'` — store the fields you supply; omitted fields are
      left as they are.  `fail_dist`/`repair_dist` are distribution codes
      from `list_distributions`.
    - `action='clear'` — forget them and fall back to model defaults.

    Precedence at placement time: the arguments you pass to `add_block`,
    then these defaults, then the model's own block template row, then the
    `rbdlib` canonical values.  Defaults are per-workspace and persist
    across server restarts.
    """
    ws = context.workspace()
    from raptor.api.workspace import BLOCK_DEFAULT_FIELDS as _FIELDS
    if action == "list":
        stored = dict(ws.block_defaults)
        rows = [[f, stored.get(f, "-")] for f in _FIELDS]
        return ok(
            {"block_defaults": stored},
            markdown=markdown_table(["field", "value"], rows),
        )
    if action == "clear":
        context.require_write()
        stored = ws.set_block_defaults(None)
        return ok({"block_defaults": stored},
                  markdown="Cleared block defaults; `add_block` now uses each "
                           "model's own block template row.")
    if action != "set":
        raise ValueError(f"unknown action {action!r}; use list/set/clear")
    context.require_write()
    supplied = {
        "fail_dist": fail_dist, "fail_params": fail_params,
        "repair_dist": repair_dist, "repair_params": repair_params,
    }
    supplied = {k: v for k, v in supplied.items() if v is not None}
    if not supplied:
        raise ValueError(
            "action='set' requires at least one of fail_dist, fail_params, "
            "repair_dist, repair_params"
        )
    merged = {**ws.block_defaults, **supplied}
    # Validate now, not only when add_block later uses the defaults: an
    # invalid code would persist across restarts and break every build.
    from raptor.api.build import _checkdist_code
    from raptor.api.distributions import PARAM_INFO
    for kind in ("fail", "repair"):
        code = merged.get(f"{kind}_dist")
        if code is None:
            continue
        _checkdist_code(kind, code)
        params = merged.get(f"{kind}_params")
        if params is not None:
            expected = len(PARAM_INFO[int(code)]["params"])
            if len(params) != expected:
                raise ValueError(
                    f"{kind}_params has {len(params)} value(s); "
                    f"distribution {int(code)} needs {expected}")
    stored = ws.set_block_defaults(merged)
    rows = [[f, stored.get(f, "-")] for f in _FIELDS]
    return ok({"block_defaults": stored},
              markdown="Block defaults updated -- `add_block` will use these "
                       "for any field you do not pass explicitly.\n\n"
                      + markdown_table(["field", "value"], rows))


# ══════════════════════════════════════════════════════════════════════
# Annotations (diagram overlays, .rbds only)
# ══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations=ADDITIVE("Add annotation"))
@guarded
def add_annotation(
    model_id: str,
    kind: Literal["text", "rect", "ellipse", "arrow"] = "text",
    text: str = "",
    adjacent_to: str | None = None,
    side: Literal["right", "left", "above", "below"] = "right",
    gap: float = 12.0,
    x: float | None = None,
    y: float | None = None,
    w: float | None = None,
    h: float | None = None,
    from_: str | None = None,
    to: str | None = None,
    x1: float | None = None,
    y1: float | None = None,
    x2: float | None = None,
    y2: float | None = None,
    parent: int | None = None,
    style: dict[str, Any] | None = None,
) -> CallToolResult:
    """Add a drawing annotation (text box, rect, ellipse or arrow).

    Place it **by name** with `adjacent_to` (a block/node/event/hierarchy
    name or id) plus `side` (`right`/`left`/`above`/`below`) and `gap` --
    no coordinates, and it stays put when the diagram is re-laid-out.  Or
    place it **explicitly** with `x`/`y` (+ `w`/`h`), in the same scene
    space the GUI uses.

    An `arrow` connects two elements by name: give `from_` and `to`, or
    the four coordinates `x1`/`y1`/`x2`/`y2`.

    `style` carries the cosmetic fields: `border_colour`, `border_width`
    (0 = none), `line_style` (`solid`/`dash`/`dot`/`dash-dot`/
    `dash-dot-dot`), `fill_colour` (null = transparent), `text_colour`,
    `font_family`, `font_size`, `font_bold`, `font_italic`, `z`.
    Returns {annotation_id, annotation, summary}.

    For an arrow the parameter is spelled `from_` (a Python keyword
    conflict); the stored `anchor` object reads back as `from`.  In a
    `build_model` spec the same `from_` spelling applies.
    Annotations exist only in `.rbds`; exporting with
    `save_model_as(format="rbd")` drops them.
    """
    context.require_write()
    ws = context.workspace()
    from raptor.api.annotations import add_annotation as _add
    kwargs: dict[str, Any] = {
        "kind": kind, "text": text, "adjacent_to": adjacent_to, "side": side,
        "gap": gap, "x": x, "y": y, "w": w, "h": h, "from_": from_, "to": to,
        "x1": x1, "y1": y1, "x2": x2, "y2": y2, "parent": parent,
    }
    kwargs.update(style or {})
    result = _add(ws, model_id, **kwargs)
    s = result["summary"]
    md = (f"Added {s['kind']} annotation "
          f"`{s['annotation_id']}`"
          + (f" anchored {s['anchor'].get('side')} of "
             f"`{s['anchor'].get('target') or s['anchor'].get('from')}`."
             if s.get("anchor") else "."))
    return ok(result, markdown=md)


@mcp.tool(annotations=READ_ONLY("List annotations"))
@guarded
def list_annotations(model_id: str,
                     parent: int | None = None) -> CallToolResult:
    """List a model's diagram annotations.

    `parent` filters to one hierarchy level (the `parent` field, 0 =
    home).  Returns {annotations, count}; each annotation gives
    annotation_id, kind, parent, text, geometry and, for name-anchored
    ones, its `anchor` (target/side/gap, or from/to for an arrow).
    """
    ws = context.workspace()
    from raptor.api.annotations import list_annotations as _list
    from raptor.api.limits import MAX_LIST_ANNOTATIONS
    result = _list(ws, model_id, parent=parent)
    total = len(result.get("annotations") or [])
    if total > MAX_LIST_ANNOTATIONS:
        result["annotations"] = result["annotations"][:MAX_LIST_ANNOTATIONS]
        result["total"] = total
        result["truncated"] = True
    rows = [[a["annotation_id"], a["kind"], a["parent"], a["text"]]
            for a in result["annotations"]]
    md = markdown_table(["id", "kind", "parent", "text"], rows) if rows \
        else "*No annotations.*"
    return ok(result, markdown=md)


@mcp.tool(annotations=EDIT("Update annotation"))
@guarded
def update_annotation(model_id: str, annotation_id: int,
                      patch: dict[str, Any]) -> CallToolResult:
    """Patch one annotation.

    Accepts any style field, `text`, explicit geometry (`x`/`y`/`w`/`h`/
    `x1`/`y1`/`x2`/`y2`), or a re-anchor via `adjacent_to`/`side`/`gap`
    (text/rect/ellipse) or `from_`/`to` (arrow).  Returns
    {annotation_id, annotation, summary}.
    """
    context.require_write()
    ws = context.workspace()
    from raptor.api.annotations import update_annotation as _update
    result = _update(ws, model_id, annotation_id, patch)
    return ok(result, markdown=(
        f"Updated annotation `{annotation_id}` "
        f"({', '.join(sorted(patch))})."))


@mcp.tool(annotations=DESTRUCTIVE("Delete annotation"))
@guarded
def delete_annotation(model_id: str, annotation_id: int) -> CallToolResult:
    """Delete one annotation by id.  Returns {deleted, kind}."""
    context.require_write()
    ws = context.workspace()
    from raptor.api.annotations import delete_annotation as _delete
    result = _delete(ws, model_id, annotation_id)
    return ok(result, markdown=(
        f"Deleted annotation `{result['deleted']}` ({result['kind']})."))


# ══════════════════════════════════════════════════════════════════════
# Reference
# ══════════════════════════════════════════════════════════════════════

@mcp.tool(annotations=READ_ONLY("Search docs"))
@guarded
def search_docs(query: str, limit: int = 5) -> CallToolResult:
    """Search the RAPTOR tutorial docs for passages matching a query.

    Multi-word queries match passages containing **all** terms (so
    "capacity analysis system capacity" finds the capacity passages, not
    nothing).  Returns {results}, each {chapter, file, anchor, text,
    score}.  Use `read_doc` to read a whole document.
    """
    from raptor.api.limits import MAX_SEARCH_RESULTS
    from raptor.mcp.docs import search_chapters
    limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
    results = search_chapters(query, limit=limit)
    if not results:
        return ok({"results": []}, markdown=f"No passages matched {query!r}.")
    md_parts = []
    for r in results:
        where = f"`{r['file']}`" + (f" `#{r['anchor']}`" if r.get("anchor") else "")
        md_parts.append(f"**{r['chapter']}** — {where}\n\n> {r['text']}")
    return ok({"results": results}, markdown="\n\n".join(md_parts))


@mcp.tool(annotations=READ_ONLY("Read doc"))
@guarded
def read_doc(name: str, offset: int = 0, limit: int = 200) -> CallToolResult:
    """Read a documentation source as text (paginated).

    `name` is 'rbds' (the `.rbds` format guide), 'dependency' (the
    dependency tutorial chapter), 'mcp-guide' (this MCP
    chapter), a tutorial chapter stem like 'ch11-capacity', a chapter
    title fragment, or a path under `docs/` or `new_docs/`.  `offset`/
    `limit` page the lines (limit <= 400).  Returns {name, file,
    total_lines, offset, next_offset, text}.
    """
    from raptor.mcp.docs import read_doc as _read
    doc = _read(name)
    lines = doc["text"].splitlines()
    offset, limit = context.page_args(offset, limit, 400)
    page = lines[offset:offset + limit]
    next_offset = offset + limit if offset + limit < len(lines) else None
    data = {
        "name": doc["name"], "file": doc["file"],
        "total_lines": len(lines), "offset": offset,
        "next_offset": next_offset, "text": "\n".join(page),
    }
    md = (f"### `{doc['file']}` — lines {offset}-{offset + len(page)} "
          f"of {len(lines)}\n\n" + "\n".join(page))
    return ok(data, markdown=md)


@mcp.tool(annotations=READ_ONLY("Workspace info"))
@guarded
def workspace_info() -> CallToolResult:
    """Return the server workspace root and read/write rules.

    Learn where models/runs/renders live and which directories
    `save_model_as` may write to (the workspace plus any `--allow-write`
    dirs) and `open_model` may read from (the workspace, examples and any
    `--allow-read` dirs).  Relative save paths resolve against the
    workspace root.
    """
    from raptor.mcp import context
    ws = context.workspace()
    if hasattr(ws, "info"):
        info = dict(ws.info())
    else:  # pragma: no cover - older Workspace
        info = {
            "root": str(ws.root),
            "retain_days": ws.retain_days,
            "max_models": ws.max_models,
            "max_bytes": getattr(ws, "max_bytes", None),
        }
    info.setdefault("root", str(ws.root))
    # Workspace.info() only knows its own root; include the CLI's
    # --allow-write dirs, which save_model_as/delete_file accept.
    info["allowed_write"] = [str(p) for p in
                             dict.fromkeys([ws.root,
                                            *context.allowed_write_dirs()])]
    info["allowed_read"] = [str(p) for p in
                            dict.fromkeys([ws.root,
                                           *context.allowed_read_dirs()])]
    md = (f"Workspace root: `{info['root']}`\n\n"
          "Allowed write dirs: " + ", ".join(info.get("allowed_write") or [info["root"]])
          + "\n\nAllowed read dirs: "
          + ", ".join(info.get("allowed_read") or [info["root"]]))
    return ok(info, markdown=md)
