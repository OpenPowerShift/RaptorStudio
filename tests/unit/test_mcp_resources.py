"""Tests for the MCP resources and prompts: example, model, run, docs
and render resources, and the three built-in prompts.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


class TestResources:
    def test_list_examples(self):
        from raptor.mcp.resources import list_examples_resource
        result = json.loads(list_examples_resource())
        assert isinstance(result, list)
        assert "MotorDemo" in [e["name"] for e in result]

    def test_get_example_returns_rbds(self):
        from raptor.mcp.resources import get_example
        content = get_example("MotorDemo")
        assert isinstance(content, str)
        assert "BLOCK_DATA" in content or "blocks" in content

    def test_get_model_resource(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import open_model
            from raptor.mcp.resources import get_model
            result = open_model("MotorDemo")
            assert not result.is_error
            content = get_model(result.structured_content["model_id"])
            assert "blocks" in content or "BLOCK_DATA" in content

    def test_get_chapter(self):
        from raptor.mcp.resources import get_chapter
        # Any chapter file will do; fall back to the book.
        chapters = sorted((REPO / "new_docs" / "chapters").glob("*.adoc"))
        if not chapters:
            pytest.skip("no chapters in this checkout")
        content = get_chapter(chapters[0].stem)
        assert isinstance(content, str)
        assert len(content) > 50

    def test_nonexistent_example_raises(self):
        from raptor.mcp.resources import get_example
        with pytest.raises(FileNotFoundError):
            get_example("NonExistentModel")


class TestPrompts:
    def test_prompts_import(self):
        from raptor.mcp import prompts  # noqa: F401

    def test_new_availability_study_prompt(self):
        from raptor.mcp.prompts import new_availability_study
        result = new_availability_study()
        assert isinstance(result, str)
        assert "build" in result.lower() or "pump" in result.lower()

    def test_explain_model_prompt(self):
        from raptor.mcp.prompts import explain_model
        result = explain_model("m_123")
        assert "m_123" in result
        assert "single_point_failures" in result

    def test_improve_availability_prompt(self):
        from raptor.mcp.prompts import improve_availability
        result = improve_availability("m_123", target=0.995)
        assert "compare" in result.lower() or "sweep" in result.lower()
        assert "0.995" in result


class TestResourcesIntegration:
    def test_open_example_via_workspace(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import open_model
            result = open_model("MotorDemo")
            assert not result.is_error
            assert "model_id" in result.structured_content
            assert "summary" in result.structured_content
