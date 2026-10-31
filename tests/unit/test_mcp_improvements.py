"""Tests for the MCP-related review fixes:

* validator: dependency gate uses DepPM (not PMFreq); the real unported
  dependent-block+PM/sparing combination is caught; k > n is an error;
* `build_model` populates spare_pools/resources/triggers/phases/annotations;
* annotation tools (add/list/update/delete) with name-anchored placement;
* `save_model_as` appends a missing extension;
* sweep rows carry provenance and warn when all rows are identical;
* `simulate(seed=...)` re-rolls reproducibly;
* error/documentation contract for handles, `k=-1`, render `level`.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _tools() -> dict:
    from raptor.mcp import server, tools_model, tools_run  # noqa: F401
    try:
        from raptor.mcp import tools_view  # noqa: F401
    except ImportError:
        pass
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def _use_workspace(tmp_path, **flags):
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path), **flags})()
    mcp._raptor_ws = None


def _text(result) -> str:
    return " ".join(c.text for c in result.content
                    if getattr(c, "type", "") == "text")


def _simple_spec(**extra) -> dict:
    spec = {
        "system": {"term_condition": 1000, "num_runs": 3},
        "blocks": [
            {"id": 1, "name": "B1", "fail_dist": 4, "fail_params": [100.0, 0.0]},
            {"id": 2, "name": "B2", "fail_dist": 4, "fail_params": [100.0, 0.0]},
        ],
        "nodes": [
            {"id": 10, "name": "Start", "type": "start"},
            {"id": 13, "name": "S", "type": "regular", "k": 1},
            {"id": 11, "name": "J", "type": "regular", "n": 2, "k": 1},
            {"id": 12, "name": "End", "type": "end"},
        ],
        # Start takes one link out, so the pair splits at S.
        "links": [
            {"from": "Start", "to": "S"},
            {"from": "S", "to": "B1"}, {"from": "S", "to": "B2"},
            {"from": "B1", "to": "J"}, {"from": "B2", "to": "J"},
            {"from": "J", "to": "End"},
        ],
    }
    spec.update(extra)
    return spec


# --------------------------------------------------------------------------
# Validator: dependency gate + unported combination + k>n
# --------------------------------------------------------------------------

class TestValidator:
    def _dep_spec(self, *, dep_pm=0.0, fail_dist=4, fail_params=(100.0, 0.0),
                  block_extra=None):
        a = {"id": 1, "name": "A", "fail_dist": fail_dist,
             "fail_params": list(fail_params),
             "dep_type": "RBDBlock", "dep_num": 2,
             "dep_nothing": 0.0, "dep_idle": 100.0 if dep_pm == 0 else 0.0,
             "dep_pm": dep_pm, "dep_fail": 0.0}
        if block_extra:
            a.update(block_extra)
        return {
            "system": {"term_condition": 1000},
            "blocks": [a, {"id": 2, "name": "B", "fail_dist": 4,
                           "fail_params": [300.0, 0.0]}],
            "nodes": [{"id": 10, "name": "Start", "type": "start"},
                      {"id": 11, "name": "s", "type": "regular",
                       "n": 2, "k": 1},
                      {"id": 12, "name": "End", "type": "end"}],
            "links": [{"from": "Start", "to": "s"}, {"from": "s", "to": "A"},
                      {"from": "s", "to": "B"}, {"from": "A", "to": "End"},
                      {"from": "B", "to": "End"}],
        }

    def _errors(self, spec):
        from raptor.api.build import build_from_spec
        from raptor.api.validate import issues
        rbd = build_from_spec(spec, auto_layout=False)
        return [i.message for i in issues(rbd) if i.severity == "error"]

    def test_exponential_dependency_idle_is_clean(self):
        # The old gate read PMFreq (default 100) and wrongly rejected this.
        assert self._errors(self._dep_spec(dep_pm=0.0)) == []

    def test_undefined_mean_with_dep_pm_errors(self):
        errs = self._errors(self._dep_spec(dep_pm=50.0, fail_dist=15,
                                           fail_params=(0.01, 10.0, 0.0)))
        assert any("undefined" in m for m in errs)

    def test_k_over_n_is_an_error(self):
        spec = {
            "system": {"term_condition": 10},
            "blocks": [{"id": 1, "name": "A", "fail_dist": 4,
                        "fail_params": [100.0, 0.0]}],
            "nodes": [{"id": 10, "name": "Start", "type": "start"},
                      {"id": 11, "name": "v", "type": "regular",
                       "n": 2, "k": 2},
                      {"id": 12, "name": "End", "type": "end"}],
            "links": [{"from": "Start", "to": "v"}, {"from": "v", "to": "A"},
                      {"from": "A", "to": "End"}],
        }
        assert any("exceeds" in m for m in self._errors(spec))

    def test_k_equal_to_n_is_clean(self):
        assert self._errors(_simple_spec()) == []


# --------------------------------------------------------------------------
# build_model populates the four auxiliary sections + annotations
# --------------------------------------------------------------------------

class TestBuildModelSections:
    def test_sections_and_annotations_are_built_and_round_trip(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import build_model, open_model, save_model_as
        spec = _simple_spec(
            spare_pools=[{"name": "Spares", "stock": 3, "new_spares": 1}],
            resources=[{"name": "Crew", "num_in": 1}],
            triggers=[{"name": "PMTrig", "params": [100.0, 0.0, 0.0]}],
            phases=[{"name": "Cruise", "params": [100.0, 0.0, 0.0],
                     "mission": 1}],
            annotations=[{"kind": "text", "text": "hot-swap",
                          "adjacent_to": "B1", "side": "right"}],
        )
        r = build_model("aux", spec)
        assert not r.is_error, _text(r)
        mid = r.structured_content["model_id"]

        from raptor.mcp import context
        from raptor.api.annotations import list_annotations
        ws = context.workspace()
        rbd = ws.get(mid).rbd
        assert len(rbd.sections["SPARE_POOL_DATA"].rows) == 1
        assert len(rbd.sections["RESOURCE_DATA"].rows) == 1
        assert len(rbd.sections["TRIGGERS"].rows) == 1
        assert len(rbd.sections["PHASES"].rows) == 1
        anns = list_annotations(ws, mid)["annotations"]
        assert len(anns) == 1 and anns[0]["text"] == "hot-swap"
        assert anns[0]["anchor"]["target"] == "B1"
        # name-anchored, so it must not sit at the origin
        assert (anns[0]["x"], anns[0]["y"]) != (0.0, 0.0)

        # save/open round-trip preserves the sections + annotation.
        saved = save_model_as(mid, "aux_out")   # no extension given
        assert not saved.is_error, _text(saved)
        out = Path(saved.structured_content["path"])
        assert out.suffix == ".rbds", out
        from raptor.mcp import context as _ctx
        reopened = open_model(str(out.relative_to(_ctx.workspace().root)))
        assert not reopened.is_error, _text(reopened)
        rid = reopened.structured_content["model_id"]
        rbd2 = context.workspace().get(rid).rbd
        assert len(rbd2.sections["PHASES"].rows) == 1
        assert len(rbd2.annotations) == 1

    def test_build_model_docstring_lists_sections(self):
        doc = _tools()["build_model"].description
        for key in ("spare_pools", "resources", "triggers", "phases",
                    "annotations", "hierarchies"):
            assert key in doc, key


# --------------------------------------------------------------------------
# Annotation tools
# --------------------------------------------------------------------------

class TestAnnotationTools:
    def _model(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import build_model
        r = build_model("ann", _simple_spec())
        assert not r.is_error, _text(r)
        return r.structured_content["model_id"]

    def test_tool_registered_and_annotated(self):
        tools = _tools()
        for name in ("add_annotation", "list_annotations",
                     "update_annotation", "delete_annotation"):
            assert name in tools, name

    def test_add_by_name_list_update_delete(self, tmp_path):
        mid = self._model(tmp_path)
        from raptor.mcp.tools_model import (add_annotation, list_annotations,
                                             update_annotation,
                                             delete_annotation)
        r = add_annotation(mid, kind="text", text="note",
                           adjacent_to="B1", side="above")
        assert not r.is_error, _text(r)
        aid = r.structured_content["annotation_id"]
        assert list_annotations(mid).structured_content["count"] == 1

        up = update_annotation(mid, aid, {"text": "changed",
                                          "adjacent_to": "B2",
                                          "side": "below"})
        assert not up.is_error, _text(up)
        assert up.structured_content["summary"]["text"] == "changed"
        assert up.structured_content["summary"]["anchor"]["target"] == "B2"

        dele = delete_annotation(mid, aid)
        assert not dele.is_error, _text(dele)
        assert list_annotations(mid).structured_content["count"] == 0

    def test_arrow_by_name(self, tmp_path):
        mid = self._model(tmp_path)
        from raptor.mcp.tools_model import add_annotation
        r = add_annotation(mid, kind="arrow", from_="B1", to="B2")
        assert not r.is_error, _text(r)
        s = r.structured_content["summary"]
        assert {k: s["anchor"][k] for k in ("from", "to")} == {"from": "B1", "to": "B2"}
        assert s["x1"] != s["x2"] or s["y1"] != s["y2"]

    def test_unknown_name_is_not_found(self, tmp_path):
        mid = self._model(tmp_path)
        from raptor.mcp.tools_model import add_annotation
        r = add_annotation(mid, kind="text", adjacent_to="NoSuchBlock")
        assert r.is_error
        assert r.structured_content["error"]["code"] == "not_found"

    def test_explicit_coordinates(self, tmp_path):
        mid = self._model(tmp_path)
        from raptor.mcp.tools_model import add_annotation
        r = add_annotation(mid, kind="rect", text="", x=10.0, y=20.0)
        assert not r.is_error, _text(r)
        s = r.structured_content["summary"]
        assert (s["x"], s["y"]) == (10.0, 20.0)
        assert "anchor" not in s


# --------------------------------------------------------------------------
# save_model_as extension, handle error, k=-1 docs
# --------------------------------------------------------------------------

class TestContractFixes:
    def test_save_appends_extension(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import build_model, save_model_as, open_model
        mid = build_model("m", _simple_spec()).structured_content["model_id"]
        r = save_model_as(mid, "noext")
        assert not r.is_error, _text(r)
        assert str(r.structured_content["path"]).endswith(".rbds")
        from raptor.mcp import context as _ctx
        rel = Path(r.structured_content["path"]).relative_to(_ctx.workspace().root)
        assert not open_model(str(rel)).is_error

    def test_unknown_handle_mentions_recovery(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import describe_model
        r = describe_model("m_nope")
        assert r.is_error
        text = _text(r)
        assert "list_models" in text and "expire" in text

    def test_add_node_k_minus_one_stored_as_sentinel(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import (build_model, add_node, get_element)
        mid = build_model("k", _simple_spec()).structured_content["model_id"]
        row = add_node(mid, type="regular", name="V", k=-1)
        vid = row.structured_content["node_id"]
        # k=-1 is stored as the "never set" sentinel ...
        from raptor.mcp import context
        raw = context.workspace().get(mid).rbd.sections["NODE_DATA"].rows
        stored = [r for r in raw if str(r.get("ID")) == str(vid)][0]
        assert stored["k"] == "-1"
        # get_element reports the stored sentinel; the engine coerces it to
        # 1-of-N at run time (the -1 sentinel is ported from).
        got = get_element(mid, f"Node:{vid}")
        assert not got.is_error, _text(got)
        assert got.structured_content["k"] == -1

    def test_add_node_docstring_explains_k(self):
        doc = _tools()["add_node"].description
        assert "all incoming" in doc and "1-of-N" in doc

    def test_render_level_docstring_is_hierarchy_id(self):
        tools = _tools()
        if "render_diagram" not in tools:
            pytest.skip("rendering unavailable")
        doc = tools["render_diagram"].description
        assert "hierarchy id" in doc.lower()


# --------------------------------------------------------------------------
# Sweep provenance
# --------------------------------------------------------------------------

class TestSweepProvenance:
    def test_rows_have_run_id_and_identical_warning(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import build_model
        from raptor.mcp.tools_run import sweep_parameter
        mid = build_model("sw", _simple_spec()).structured_content["model_id"]
        # Sweep a field the model does not use -> identical rows -> warning.
        r = asyncio.run(sweep_parameter(
            mid, "B1", "pm_stagger_time", [0.0, 1.0, 2.0],
            num_runs=2, metrics=["availability"]))
        assert not r.is_error, _text(r)
        data = r.structured_content
        assert all("run_id" in row and "runs" in row for row in data["rows"])
        assert data["all_rows_identical"] is True
        assert data["warnings"]


# --------------------------------------------------------------------------
# seed
# --------------------------------------------------------------------------

class TestSeed:
    def test_seed_reproducible_and_effective(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import open_model
        from raptor.mcp.tools_run import simulate
        r = open_model("MotorDemo")
        mid = r.structured_content["model_id"]
        a = asyncio.run(simulate(mid, num_runs=5, confirm_long_run=True,
                                 seed=7))
        b = asyncio.run(simulate(mid, num_runs=5, confirm_long_run=True,
                                 seed=7))
        c = asyncio.run(simulate(mid, num_runs=5, confirm_long_run=True,
                                 seed=8))
        ma = a.structured_content["summary"]
        mb = b.structured_content["summary"]
        mc = c.structured_content["summary"]
        assert ma == mb
        assert ma != mc

    def test_negative_seed_rejected(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import open_model
        from raptor.mcp.tools_run import simulate
        mid = open_model("MotorDemo").structured_content["model_id"]
        r = asyncio.run(simulate(mid, num_runs=2, confirm_long_run=True,
                                 seed=-1))
        assert r.is_error
        assert r.structured_content["error"]["code"] == "invalid_argument"


# --------------------------------------------------------------------------
# simulate truncation flag
# --------------------------------------------------------------------------

class TestTruncation:
    def test_truncated_flag_present(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import open_model
        from raptor.mcp.tools_run import simulate
        mid = open_model("MotorDemo").structured_content["model_id"]
        r = asyncio.run(simulate(mid, num_runs=3, confirm_long_run=True))
        assert not r.is_error, _text(r)
        assert r.structured_content["truncated"] is False
        assert r.structured_content["requested_runs"] == 3

    def test_requested_runs_from_model_config_when_omitted(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import open_model
        from raptor.api.run import model_num_runs
        from raptor.mcp.tools_run import simulate
        from raptor.mcp import context
        mid = open_model("MotorDemo").structured_content["model_id"]
        configured = model_num_runs(context.workspace().get(mid).rbd, strict=True)
        r = asyncio.run(simulate(mid, confirm_long_run=True))
        assert not r.is_error, _text(r)
        # requested_runs must reflect the model's own count, not be dropped
        # because num_runs was omitted.
        assert r.structured_content["requested_runs"] == configured


class TestAnnotationEdgeCases:
    def _model(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import build_model
        return build_model("ae", _simple_spec()).structured_content["model_id"]

    def test_missing_annotation_id_is_not_found(self, tmp_path):
        mid = self._model(tmp_path)
        from raptor.mcp.tools_model import update_annotation, delete_annotation
        r = update_annotation(mid, 999, {"text": "x"})
        assert r.is_error
        assert r.structured_content["error"]["code"] == "not_found"
        d = delete_annotation(mid, 999)
        assert d.structured_content["error"]["code"] == "not_found"

    def test_lone_side_keeps_existing_anchor(self, tmp_path):
        mid = self._model(tmp_path)
        from raptor.mcp.tools_model import add_annotation, update_annotation
        aid = add_annotation(mid, kind="text", text="n",
                             adjacent_to="B1", side="right") \
            .structured_content["annotation_id"]
        s = update_annotation(mid, aid, {"side": "left"}) \
            .structured_content["summary"]
        assert s["anchor"]["target"] == "B1"
        assert s["anchor"]["side"] == "left"

    def test_change_kind_without_placement_is_rejected(self, tmp_path):
        mid = self._model(tmp_path)
        from raptor.mcp.tools_model import add_annotation, update_annotation
        aid = add_annotation(mid, kind="text", text="n",
                             adjacent_to="B1").structured_content["annotation_id"]
        r = update_annotation(mid, aid, {"kind": "arrow"})
        assert r.is_error
        assert r.structured_content["error"]["code"] == "invalid_argument"

    def test_rect_and_ellipse(self, tmp_path):
        mid = self._model(tmp_path)
        from raptor.mcp.tools_model import add_annotation, list_annotations
        for kind in ("rect", "ellipse"):
            r = add_annotation(mid, kind=kind, adjacent_to="B1", side="right")
            assert not r.is_error, _text(r)
        assert list_annotations(mid).structured_content["count"] == 2

    def test_arrow_reanchor(self, tmp_path):
        mid = self._model(tmp_path)
        from raptor.mcp.tools_model import add_annotation, update_annotation
        aid = add_annotation(mid, kind="arrow", from_="B1", to="B2") \
            .structured_content["annotation_id"]
        s = update_annotation(mid, aid, {"from_": "B2", "to": "B1"}) \
            .structured_content["summary"]
        assert {k: s["anchor"][k] for k in ("from", "to")} == {"from": "B2", "to": "B1"}

    def test_reanchor_arrow_to_box_and_back(self, tmp_path):
        mid = self._model(tmp_path)
        from raptor.mcp.tools_model import add_annotation, update_annotation
        aid = add_annotation(mid, kind="arrow", from_="B1", to="B2") \
            .structured_content["annotation_id"]
        # arrow -> box: an explicit adjacent_to must override from_/to
        s = update_annotation(mid, aid, {"kind": "text",
                                         "adjacent_to": "B1",
                                         "side": "left"}) \
            .structured_content["summary"]
        assert s["kind"] == "text"
        assert s["anchor"]["target"] == "B1"
        # box -> arrow
        s2 = update_annotation(mid, aid, {"kind": "arrow",
                                          "from_": "B1", "to": "B2"}) \
            .structured_content["summary"]
        assert {k: s2["anchor"][k] for k in ("from", "to")} == {"from": "B1", "to": "B2"}

    def test_list_parent_filter(self, tmp_path):
        mid = self._model(tmp_path)
        from raptor.mcp.tools_model import add_annotation, list_annotations
        add_annotation(mid, kind="text", text="home", adjacent_to="B1")
        assert list_annotations(mid, parent=0).structured_content["count"] == 1
        assert list_annotations(mid, parent=99).structured_content["count"] == 0

    def test_validation_errors(self, tmp_path):
        mid = self._model(tmp_path)
        from raptor.mcp.tools_model import add_annotation
        assert add_annotation(mid, kind="bogus", adjacent_to="B1").is_error
        assert add_annotation(mid, kind="text", adjacent_to="B1",
                              style={"nonsense": 1}).is_error
        assert add_annotation(mid, kind="arrow").is_error
        assert add_annotation(mid, kind="arrow",
                              from_="B1").is_error
        assert add_annotation(mid, kind="text").is_error  # no placement


class TestCompareContract:
    def test_too_few_is_invalid_argument(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import build_model
        from raptor.mcp.tools_run import compare_models
        mid = build_model("c", _simple_spec()).structured_content["model_id"]
        r = asyncio.run(compare_models([mid]))
        assert r.is_error
        assert r.structured_content["error"]["code"] == "invalid_argument"

    def test_too_many_is_rejected(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_run import compare_models
        r = asyncio.run(compare_models(["a", "b", "c", "d", "e", "f"]))
        assert r.is_error
        assert r.structured_content["error"]["code"] == "invalid_argument"
