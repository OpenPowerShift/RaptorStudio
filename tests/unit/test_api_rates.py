"""Tests for named rates and expression resolution.

Covers `resolve_value` over plain numbers, expressions and `@rate`
references, expression detection, the add/update/delete rate CRUD schema,
and a rate referenced from a block surviving a `.rbds` save/reload with its
inline comment intact.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor.api.rates import (
    list_rates, add_rate, update_rate, delete_rate,
    resolve_value, is_expression,
)
from raptor.api.build import update_element, BuildError
from raptor.api.workspace import Workspace
from raptor.core.fileio.rbds import load_rbds

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


@pytest.fixture
def motor_model(workspace):
    result = workspace.open_model(GOLDEN / "MotorDemo" / "input.rbd")
    return result["model_id"]


class TestResolveValue:
    """resolve_value wraps expr_eval.resolve correctly."""

    def test_plain_number(self):
        assert resolve_value("100.0") == 100.0

    def test_numeric_passthrough(self):
        assert resolve_value(42) == 42.0

    def test_expression(self):
        assert abs(resolve_value("50.0 + 50.0") - 100.0) < 0.001

    def test_expression_with_comment(self):
        expected = 0.00015 * 20000 / 8760
        assert abs(resolve_value("0.00015 * 20000 / 8760  # basis")
                   - expected) < 1e-12

    def test_rate_reference(self):
        rates = [{"name": "Highway", "value": 1.71}]
        assert resolve_value("@Highway", rates) == 1.71
        assert abs(resolve_value("@Highway * 1.4", rates) - 1.71 * 1.4) < 1e-12

    def test_chained_rates(self):
        rates = [{"name": "Base", "value": 0.5}]
        derived = resolve_value("@Base * 1.4", rates)
        rates.append({"name": "Derived", "value": derived})
        assert resolve_value("@Derived * 2", rates) == pytest.approx(1.4)


class TestIsExpression:
    """is_expression distinguishes numbers from expressions."""

    def test_plain_number(self):
        assert not is_expression("100.0")
        assert not is_expression("42")

    def test_expression(self):
        assert is_expression("50.0 + 50.0")
        assert is_expression("@Highway * 1.4")

    def test_non_string(self):
        assert not is_expression(42)


class TestRateCRUD:
    """add_rate / update_rate / delete_rate use the documented schema."""

    def test_add_rate_schema(self, workspace, motor_model):
        entry = add_rate(workspace, motor_model, "Highway",
                         "0.00015 * 20000 / 8760  # basis",
                         comment="Highway basis")
        assert entry["name"] == "Highway"
        assert entry["expr"] == "0.00015 * 20000 / 8760  # basis"
        assert isinstance(entry["value"], float)
        assert abs(entry["value"] - 0.00015 * 20000 / 8760) < 1e-12
        assert entry["description"] == "Highway basis"
        # `resolved` is not part of the rate schema.
        assert "resolved" not in entry

    def test_list_rates(self, workspace, motor_model):
        add_rate(workspace, motor_model, "TestRate", "1.0")
        rates = json.loads(list_rates(workspace, motor_model))
        assert len(rates) >= 1
        assert rates[0]["name"] == "TestRate"
        assert rates[0]["value"] == 1.0

    def test_update_rate(self, workspace, motor_model):
        add_rate(workspace, motor_model, "MyRate", "100.0")
        updated = update_rate(workspace, motor_model, "MyRate", "200.0",
                              comment="Updated")
        assert updated["expr"] == "200.0"
        assert updated["value"] == 200.0
        assert updated["description"] == "Updated"

    def test_chained_rate_reference(self, workspace, motor_model):
        # A rate's own expression may reference another saved rate.
        add_rate(workspace, motor_model, "Base", "0.5")
        derived = add_rate(workspace, motor_model, "Derived", "@Base * 1.4")
        assert derived["value"] == pytest.approx(0.7)
        # Updating the base updates the derived rate too: a rate defined
        # from another one follows it (it used to keep 0.7, a stale value
        # nothing told the user about).
        update_rate(workspace, motor_model, "Base", "1.0")
        assert workspace.get(motor_model).rbd.rates[1]["value"] == \
            pytest.approx(1.4)

    def test_delete_rate(self, workspace, motor_model):
        add_rate(workspace, motor_model, "DelMe", "42.0")
        result = delete_rate(workspace, motor_model, "DelMe")
        assert result["deleted"] == "DelMe"
        assert not [r for r in workspace.get(motor_model).rbd.rates
                    if r["name"] == "DelMe"]

    def test_duplicate_rate_raises(self, workspace, motor_model):
        add_rate(workspace, motor_model, "Dup", "1.0")
        with pytest.raises(BuildError, match="already exists"):
            add_rate(workspace, motor_model, "Dup", "2.0")

    def test_delete_nonexistent_raises(self, workspace, motor_model):
        with pytest.raises(BuildError):
            delete_rate(workspace, motor_model, "NoSuchRate")

    def test_bad_expression_raises(self, workspace, motor_model):
        with pytest.raises(Exception):
            add_rate(workspace, motor_model, "Bad", "__import__('os')")


class TestRateInModel:
    """A rate referenced in a block survives save/reload."""

    def test_rate_and_formula_survive_reload(self, workspace, motor_model):
        entry = add_rate(workspace, motor_model, "Highway",
                         "0.00015 * 20000 / 8760  # basis",
                         comment="Highway basis")
        expected = entry["value"] * 1.4

        row = update_element(workspace, motor_model, "Armature",
                             {"fail_params[0]": "@Highway * 1.4"})
        assert abs(float(row["FParam1"]) - expected) < 1e-6

        handle = workspace.get(motor_model)
        key = f"block:{row['ID']}:FParam1"
        assert handle.rbd.formulas[key]["expr"] == "@Highway * 1.4"

        reloaded = load_rbds(str(handle.path))
        assert len(reloaded.rates) == 1
        rate = reloaded.rates[0]
        assert rate["name"] == "Highway"
        assert rate["expr"] == "0.00015 * 20000 / 8760  # basis"
        assert abs(rate["value"] - entry["value"]) < 1e-12
        assert rate["description"] == "Highway basis"

        assert reloaded.formulas[key]["expr"] == "@Highway * 1.4"
        assert abs(reloaded.formulas[key]["value"] - expected) < 1e-9

    def test_bare_rate_reference_recorded_as_rate(self, workspace,
                                                  motor_model):
        add_rate(workspace, motor_model, "Highway", "0.5")
        row = update_element(workspace, motor_model, "Armature",
                             {"repair_params[0]": "@Highway"})
        key = f"block:{row['ID']}:RParam1"
        entry = workspace.get(motor_model).rbd.formulas[key]
        assert entry["rate"] == "Highway"
        assert entry["value"] == 0.5


def test_a_rate_edit_reaches_the_fields_linked_to_it():
    """The stored float is what the engine reads, so a field linked to a
    rate must take the rate's new value -- it used to keep the old one."""
    from raptor.api.build import build_from_spec
    from raptor.api.rates import propagate_rates
    rbd = build_from_spec({
        "rates": [{"name": "Base", "expr": "100"},
                  {"name": "Derived", "expr": "@Base * 2"}],
        "nodes": [{"id": 1, "name": "S", "type": "start"},
                  {"id": 2, "name": "E", "type": "end"}],
        "blocks": [{"name": "A", "fail_dist": 4, "fail_params": ["@Derived"],
                    "repair_dist": 19, "repair_params": ["@Base / 10"]}],
        "links": [{"from": "S", "to": "A"}, {"from": "A", "to": "E"}]})
    rbd.rates[0]["expr"] = "50"
    assert propagate_rates(rbd) == 2
    row = next(r for r in rbd.section("BLOCK_DATA").rows if r["blockName"] == "A")
    assert float(row["FParam1"]) == 100.0       # @Derived = @Base * 2
    assert float(row["RParam1"]) == 5.0         # @Base / 10
    assert propagate_rates(rbd) == 0            # idempotent
