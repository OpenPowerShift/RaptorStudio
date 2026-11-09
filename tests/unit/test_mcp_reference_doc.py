"""The MCP guide and generated reference stay honest about the live server.

The reference is exhaustive: every live tool has a detail section, its full
description, a typed parameter table, declared return fields and an example.
The guide's facts block and README counts come from the same live tool list,
so a new tool or a stale count fails here rather than in a reader's browser.

Regenerate with ``.venv/bin/python tools/gen_mcp_reference.py``.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "docs" / "MCP-reference.md"
GUIDE = ROOT / "docs" / "MCP.md"
README = ROOT / "README.md"

_TOOL_SECTION = re.compile(
    r'<a id="tool-([^"]+)"></a>\n\n#### `([^`]+)`\n(.*?)'
    r'(?=\n<a id="tool-|\n<a id="resources"></a>)',
    re.S,
)
_FENCE = "```"


def _module():
    spec = importlib.util.spec_from_file_location(
        "gen_mcp_reference", ROOT / "tools/gen_mcp_reference.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _live():
    mod = _module()
    tools, fixed, templates, prompts = mod._live()
    return mod, tools, fixed, templates, prompts


def _tool_sections(text: str) -> dict[str, str]:
    return {name: body for name, heading, body in _TOOL_SECTION.findall(text)
            if name == heading}


def _table_rows(section: str, heading: str) -> list[list[str]]:
    match = re.search(rf"\*\*{heading}\*\*\n\n((?:\|.*\n)+)", section)
    if not match:
        return []
    lines = match.group(1).strip().splitlines()[2:]
    rows: list[list[str]] = []
    for line in lines:
        cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
        rows.append([cell.strip() for cell in cells])
    return rows


def _anchors(text: str) -> set[str]:
    return set(re.findall(r'<a id="([^"]+)"></a>', text))


def _links(text: str) -> list[str]:
    return re.findall(r"\]\(#([^)]+)\)", text)


def test_reference_and_guide_are_current():
    mod = _module()
    assert REFERENCE.read_text(encoding="utf-8") == mod.render(), (
        "docs/MCP-reference.md is stale: run tools/gen_mcp_reference.py")
    assert GUIDE.read_text(encoding="utf-8") == mod.render_guide(), (
        "docs/MCP.md's generated facts block is stale: run "
        "tools/gen_mcp_reference.py")


def test_every_live_tool_has_a_complete_detail_section():
    _mod, tools, _fixed, _templates, _prompts = _live()
    sections = _tool_sections(REFERENCE.read_text(encoding="utf-8"))
    assert set(sections) == {tool.name for tool in tools}
    for tool in tools:
        section = sections[tool.name]
        assert "**Kind:**" in section
        assert "**Parameters**" in section
        assert "**Returns**" in section
        assert "**Example**" in section
        # The whole description, not the old first paragraph only.
        expected = " ".join((tool.description or "").split())
        assert expected in " ".join(section.split()), (
            f"{tool.name}: description is truncated")


def test_every_parameter_default_and_enum_is_in_the_table():
    mod, tools, _fixed, _templates, _prompts = _live()
    sections = _tool_sections(REFERENCE.read_text(encoding="utf-8"))
    for tool in tools:
        schema = tool.input_schema or {}
        properties = schema.get("properties") or {}
        rows = _table_rows(sections[tool.name], "Parameters")
        if not properties:
            assert rows == []
            assert "No parameters." in sections[tool.name]
            continue
        assert len(rows) == len(properties), tool.name
        assert "| Parameter | Type | Required | Schema default |" in sections[
            tool.name]
        by_name = {row[0].strip("`"): row for row in rows}
        assert set(by_name) == set(properties), tool.name
        required = set(schema.get("required") or [])
        for name, prop in properties.items():
            row = by_name[name]
            assert row[1] == mod._type_label(prop), f"{tool.name}.{name} type"
            assert row[2] == ("yes" if name in required else "no"), (
                f"{tool.name}.{name} required")
            assert row[3] == mod._default_label(prop), (
                f"{tool.name}.{name} default")
            _types, values = mod._schema_types(prop)
            assert row[4] == mod._enum_label(values), (
                f"{tool.name}.{name} enum")


def test_read_only_docs_do_not_overstate_the_gate():
    reference = REFERENCE.read_text(encoding="utf-8")
    guide = GUIDE.read_text(encoding="utf-8")
    assert "not the `--read-only` gate" in reference
    assert "`simulate`, `start_run` and `open_model`" in reference
    flat_guide = " ".join(guide.split())
    assert "refuse tools that create, edit or delete" in flat_guide
    assert "opening an existing model, analysis and simulation stay" in flat_guide


def test_computed_defaults_are_documented():
    mod, _tools, _fixed, _templates, _prompts = _live()
    sections = _tool_sections(REFERENCE.read_text(encoding="utf-8"))
    assert mod.COMPUTED_DEFAULTS
    for (tool_name, parameter), note in mod.COMPUTED_DEFAULTS.items():
        section = sections[tool_name]
        assert f"- `{parameter}` — {note}" in section, (
            f"{tool_name}.{parameter}: computed default not documented")
    phase_note = mod.COMPUTED_DEFAULTS[("export_analytical", "phase")]
    assert "no phase" in phase_note
    assert "Choose one phase" in phase_note
    assert "all" in phase_note
    folder_note = mod.COMPUTED_DEFAULTS[("export_analytical", "folder")]
    assert "Defaults to `Tables/<model>` when omitted" in folder_note
    assert "file name" in folder_note
    assert "_h<hierarchy>" in folder_note
    assert "_phase<N>" in folder_note
    assert "current phase" not in phase_note
    assert "hierarchy/phase-specific name" not in folder_note


def test_computed_default_coverage_is_honest():
    reference = REFERENCE.read_text(encoding="utf-8")
    guide = GUIDE.read_text(encoding="utf-8")
    assert "otherwise the schema value is all the catalogue can claim" in reference
    assert "all computed real defaults are called out" not in reference
    assert "schema default" in guide


def test_delete_annotation_return_is_documented_as_an_id():
    reference = REFERENCE.read_text(encoding="utf-8")
    guide = GUIDE.read_text(encoding="utf-8")
    assert "Returns {deleted: annotation id, kind}" in reference
    assert "deleted annotation id and its kind" in " ".join(guide.split())


def test_every_declared_return_field_is_in_the_table():
    mod, tools, _fixed, _templates, _prompts = _live()
    sections = _tool_sections(REFERENCE.read_text(encoding="utf-8"))
    for tool in tools:
        section = sections[tool.name]
        schema = tool.output_schema
        if not schema:
            assert tool.name in mod.RETURN_NOTES, (
                f"{tool.name} has no output schema and no RETURN_NOTES entry")
            assert mod.RETURN_NOTES[tool.name] in section
            continue
        properties = schema.get("properties") or {}
        if not properties:
            continue
        rows = _table_rows(section, "Returns")
        assert len(rows) == len(properties), tool.name
        by_name = {row[0].strip("`"): row for row in rows}
        assert set(by_name) == set(properties), tool.name
        required = set(schema.get("required") or [])
        for name, prop in properties.items():
            row = by_name[name]
            assert row[1] == mod._type_label(prop), (
                f"{tool.name}.{name} return type")
            assert row[2] == ("yes" if name in required else "no"), (
                f"{tool.name}.{name} always-present")


def _matches_type(schema: dict, value) -> bool:
    """A small top-level check for the examples (no jsonschema dependency)."""
    if "anyOf" in schema:
        return any(_matches_type(alt, value) for alt in schema["anyOf"])
    if "const" in schema:
        return value == schema["const"]
    if "enum" in schema:
        return value in schema["enum"]
    kind = schema.get("type")
    if isinstance(kind, list):
        return any(_matches_type({**schema, "type": item}, value)
                   for item in kind)
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "array":
        return (isinstance(value, list)
                and all(_matches_type(schema.get("items", {}), item)
                        for item in value))
    if kind == "object":
        return isinstance(value, dict)
    if kind == "null":
        return value is None
    return True


def test_every_example_is_a_valid_argument_object():
    mod, tools, _fixed, _templates, _prompts = _live()
    sections = _tool_sections(REFERENCE.read_text(encoding="utf-8"))
    assert set(mod.EXAMPLES) == {tool.name for tool in tools}
    for tool in tools:
        match = re.search(
            r"\*\*Example\*\*\n\n```json\n(.*?)\n```",
            sections[tool.name], re.S)
        assert match, f"{tool.name}: no fenced JSON example"
        example = json.loads(match.group(1))
        assert example == mod.EXAMPLES[tool.name], tool.name
        schema = tool.input_schema or {}
        properties = schema.get("properties") or {}
        assert set(example) <= set(properties), (
            f"{tool.name}: example has unknown arguments "
            f"{sorted(set(example) - set(properties))}")
        missing = set(schema.get("required") or []) - set(example)
        assert not missing, f"{tool.name}: example misses {sorted(missing)}"
        for name, value in example.items():
            assert _matches_type(properties[name], value), (
                f"{tool.name}.{name}: example value {value!r} does not match "
                f"the schema")


def test_markdown_fences_balance():
    for path in (GUIDE, REFERENCE):
        lines = path.read_text(encoding="utf-8").splitlines()
        fences = [line for line in lines if line.startswith(_FENCE)]
        assert len(fences) % 2 == 0, f"{path}: unclosed code fence"


def test_table_of_contents_anchors_resolve():
    for path in (GUIDE, REFERENCE):
        text = path.read_text(encoding="utf-8")
        anchors = _anchors(text)
        assert len(anchors) == len(re.findall(r'<a id="([^"]+)"></a>', text)), (
            f"{path}: duplicate explicit anchor")
        missing = sorted(set(_links(text)) - anchors)
        assert not missing, f"{path}: TOC links with no anchor: {missing}"


def test_counts_match_the_live_server_and_the_book():
    mod, tools, fixed, templates, prompts = _live()
    n_tools = len(tools)
    n_without_render = n_tools - (
        1 if any(tool.name == "render_diagram" for tool in tools) else 0)
    n_resources = len(fixed) + len(templates)
    n_prompts = len(prompts)
    n_chapters, mcp_position, mcp_source = mod._book_facts()

    readme = README.read_text(encoding="utf-8")
    tool_counts = {int(n) for n in re.findall(r"\b(\d+) tools\b", readme)}
    assert tool_counts == {n_tools}, (
        f"README says {sorted(tool_counts)} tools; server has {n_tools}")
    assert (f"{n_without_render} when `--no-render`" in readme
            or f"{n_without_render} with `--no-render`" in readme), (
        f"README does not mention the {n_without_render}-tool "
        "--no-render build")
    chapter_counts = {
        int(n) for n in re.findall(r"\b(\d+)[ -](?:chapter|chapters)\b", readme)
    }
    assert chapter_counts == {n_chapters}, (
        f"README says {sorted(chapter_counts)} chapters; book has {n_chapters}")

    reference = REFERENCE.read_text(encoding="utf-8")
    assert f"**{n_tools} tools**" in reference
    assert f"({n_without_render} with `--no-render`)" in reference
    assert f"**{n_resources} resources**" in reference
    assert f"**{n_prompts} prompts**" in reference
    assert f"**{n_chapters} chapters**" in reference
    assert f"book chapter {mcp_position} of {n_chapters}" in reference
    assert f"`new_docs/chapters/{mcp_source}.adoc`" in reference
    chapters = mod._book_chapters()
    assert chapters[mcp_position - 1] == mcp_source == "ch16-mcp"

    guide = GUIDE.read_text(encoding="utf-8")
    block = guide.split(mod.GUIDE_BEGIN, 1)[1].split(mod.GUIDE_END, 1)[0]
    assert f"**{n_tools} tools**" in block
    assert f"**{n_without_render}**" in block
    assert f"**{n_resources} resources**" in block
    assert f"**{n_prompts} prompts**" in block
    assert f"**{n_chapters} chapters**" in block
    assert f"book chapter {mcp_position} of {n_chapters}" in block
    assert f"`new_docs/chapters/{mcp_source}.adoc`" in block


def test_no_render_keeps_svg_and_drops_only_the_png_tool():
    """The real startup path, not a count assumed from the docs."""
    script = """\
import argparse, asyncio
from raptor.mcp import server
server._import_tools(argparse.Namespace(no_render=True))
print("\\n".join(sorted(t.name for t in asyncio.run(server.mcp.list_tools()))))
"""
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    names = set(result.stdout.split())
    _mod, tools, _fixed, _templates, _prompts = _live()
    assert names == {tool.name for tool in tools} - {"render_diagram"}
    assert "render_diagram_svg" in names
    assert len(names) == len(tools) - 1


def test_categories_cover_every_tool_exactly_once():
    mod, tools, _fixed, _templates, _prompts = _live()
    names = {tool.name for tool in tools}
    assert set(mod.TOOL_CATEGORY) == names
    slugs = [slug for slug, _title, _blurb in mod.CATEGORIES]
    assert len(slugs) == len(set(slugs))
    assert set(mod.TOOL_CATEGORY.values()) == set(slugs)
    reference = REFERENCE.read_text(encoding="utf-8")
    for slug, title, _blurb in mod.CATEGORIES:
        count = sum(1 for value in mod.TOOL_CATEGORY.values() if value == slug)
        assert f'<a id="tools-{slug}"></a>\n\n### {title}' in reference
        assert f"[{title}](#tools-{slug}) — {count} tools" in reference


def test_guide_mentions_every_tool():
    """A tool absent from the human guide is hard to discover from a recipe."""
    _mod, tools, _fixed, _templates, _prompts = _live()
    guide = GUIDE.read_text(encoding="utf-8")
    missing = [tool.name for tool in tools if f"`{tool.name}`" not in guide]
    assert not missing, f"guide does not mention: {', '.join(missing)}"


def test_every_resource_and_prompt_is_listed():
    _mod, _tools, fixed, templates, prompts = _live()
    reference = REFERENCE.read_text(encoding="utf-8")
    guide = GUIDE.read_text(encoding="utf-8")
    for resource in fixed:
        assert f"`{resource.uri}`" in reference
        assert f"`{resource.uri}`" in guide
    for resource in templates:
        assert f"`{resource.uri_template}`" in reference
        assert f"`{resource.uri_template}`" in guide
    for prompt in prompts:
        assert f"`{prompt.name}`" in reference
        assert f"`{prompt.name}`" in guide
        for argument in prompt.arguments or []:
            assert f"`{argument.name}`" in reference
            assert f"`{argument.name}`" in guide


def test_generator_check_passes_as_a_subprocess():
    """The exact command CI and CONTRIBUTING tell a contributor to run."""
    result = subprocess.run(
        [sys.executable, "tools/gen_mcp_reference.py", "--check"],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_generated_reference_is_deterministic():
    mod = _module()
    assert mod.render() == mod.render()
    assert mod.render_guide() == mod.render_guide()
