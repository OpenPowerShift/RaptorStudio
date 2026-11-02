"""MCP auto_layout scope and argument-safety regressions.

The no-argument call used to repaint every hierarchy level; it now
matches the GUI (home level only) and `all_levels=True` opts in. Bad
directions / column limits must come back as tool errors without
touching the model.
"""
from __future__ import annotations

import tempfile

import pytest
from pydantic import ValidationError


def _text(result) -> str:
    return " ".join(c.text for c in result.content
                    if getattr(c, "type", "") == "text")


def _sc(result):
    assert not result.is_error, _text(result)
    return result.structured_content


@pytest.fixture
def hierarchy_model():
    from raptor.mcp.server import mcp
    with tempfile.TemporaryDirectory() as tmpdir:
        mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
        mcp._raptor_ws = None
        from raptor.mcp.tools_model import open_model
        yield _sc(open_model("HierarchyExample"))["model_id"]


def _rows(model_id):
    from raptor.mcp import context
    return context.workspace().get(model_id).rbd


def _set_xy(rbd, ident: str, value: str) -> None:
    for row in rbd.section("BLOCK_DATA").rows:
        if str(row.get("ID")) == ident:
            row["xPos"] = value
            row["yPos"] = value
            return
    raise AssertionError(f"no block {ident}")


def _xy(rbd, ident: str) -> tuple[str, str]:
    for row in rbd.section("BLOCK_DATA").rows:
        if str(row.get("ID")) == ident:
            return row["xPos"], row["yPos"]
    raise AssertionError(f"no block {ident}")


def _save(model_id):
    from raptor.mcp import context
    context.workspace().save(model_id)


def test_no_argument_layout_is_home_level_only(hierarchy_model):
    from raptor.mcp.tools_model import auto_layout
    rbd = _rows(hierarchy_model)
    # Block 4 is on the home level; block 1 lives inside hierarchy 23.
    _set_xy(rbd, "4", "0.000000")
    _set_xy(rbd, "1", "0.000000")
    _save(hierarchy_model)

    out = _sc(auto_layout(hierarchy_model))
    assert out["moved"] is True
    assert out["scope"] == "home level"
    rbd = _rows(hierarchy_model)
    assert _xy(rbd, "4") != ("0.000000", "0.000000")
    assert _xy(rbd, "1") == ("0.000000", "0.000000")


def test_all_levels_opt_in_repaints_the_hierarchy(hierarchy_model):
    from raptor.mcp.tools_model import auto_layout
    rbd = _rows(hierarchy_model)
    _set_xy(rbd, "1", "0.000000")
    _save(hierarchy_model)

    out = _sc(auto_layout(hierarchy_model, all_levels=True))
    assert out["scope"] == "model"
    rbd = _rows(hierarchy_model)
    assert _xy(rbd, "1") != ("0.000000", "0.000000")


def test_explicit_hierarchy_scope_leaves_home_alone(hierarchy_model):
    from raptor.mcp.tools_model import auto_layout
    rbd = _rows(hierarchy_model)
    _set_xy(rbd, "4", "0.000000")       # home
    _set_xy(rbd, "1", "0.000000")       # inside hierarchy 23 ("500path")
    _save(hierarchy_model)

    out = _sc(auto_layout(hierarchy_model, hierarchy="Hier:23"))
    assert out["scope"].startswith("hierarchy")
    rbd = _rows(hierarchy_model)
    assert _xy(rbd, "1") != ("0.000000", "0.000000")
    assert _xy(rbd, "4") == ("0.000000", "0.000000")


@pytest.mark.parametrize("kwargs", [
    {"direction": "sideways"},
    {"direction": ""},
    {"max_columns": 0},
    {"max_columns": -3},
    {"max_columns": True},
    {"max_columns": "four"},
])
def test_bad_layout_arguments_are_rejected_without_changing_the_model(
        hierarchy_model, kwargs):
    from raptor.mcp.tools_model import auto_layout
    before = {i: _xy(_rows(hierarchy_model), i) for i in ("1", "4")}
    result = auto_layout(hierarchy_model, **kwargs)
    assert result.is_error, _text(result)
    after = {i: _xy(_rows(hierarchy_model), i) for i in ("1", "4")}
    assert after == before


@pytest.mark.parametrize("kwargs", [
    {"selectors": ["../../etc/passwd"]},
    {"selectors": ["Block:999999"]},
    {"selectors": ["NoSuchElement"]},
    {"hierarchy": "../../etc/passwd"},
    {"hierarchy": "NoSuchHierarchy"},
])
def test_untrusted_selectors_fail_safely(hierarchy_model, kwargs):
    from raptor.mcp.tools_model import auto_layout
    before = {i: _xy(_rows(hierarchy_model), i) for i in ("1", "4")}
    result = auto_layout(hierarchy_model, **kwargs)
    assert result.is_error, _text(result)
    after = {i: _xy(_rows(hierarchy_model), i) for i in ("1", "4")}
    assert after == before


def test_hierarchy_and_selectors_together_are_refused(hierarchy_model):
    from raptor.mcp.tools_model import auto_layout
    result = auto_layout(hierarchy_model, hierarchy="500path",
                         selectors=["Block:4"])
    assert result.is_error
    assert "either hierarchy or selectors" in _text(result)


def test_auto_layout_input_schema_documents_the_same_limits():
    from raptor.mcp.schemas import AutoLayoutInput
    default = AutoLayoutInput(model_id="m_1")
    assert default.direction == "LR"
    assert default.all_levels is False
    assert default.max_columns is None
    with pytest.raises(ValidationError):
        AutoLayoutInput(model_id="m_1", direction="sideways")
    with pytest.raises(ValidationError):
        AutoLayoutInput(model_id="m_1", max_columns=0)
