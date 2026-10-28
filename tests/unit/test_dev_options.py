"""Developer options: parsing, precedence, and refusing typos."""
from __future__ import annotations

import pytest

from raptor.core import dev_options as d


def test_defaults_enable_nothing():
    o = d.DeveloperOptions()
    assert not o.any_enabled() and o.enabled_keys() == []


@pytest.mark.parametrize("text,key,val", [
    ("random_draw_count", "random_draw_count", True),
    ("random_draw_count=false", "random_draw_count", False),
    ("cumulative_by_trial=5", "cumulative_by_trial", 5),
    ("cumulative_by_time=12.5", "cumulative_by_time", 12.5),
    ("event_log_filter=block+node", "event_log_filter",
     frozenset({"block", "node"})),
    ("event_log_component=Pump", "event_log_component", "Pump"),
])
def test_enable_list(text, key, val):
    o = d.from_mapping(d.parse_enable_list(text))
    assert getattr(o, key) == val


def test_none_and_empty_and_all():
    assert d.parse_enable_list(None) == {}
    assert d.parse_enable_list("none") == {}
    assert d.parse_enable_list("") == {}
    allv = d.from_mapping(d.parse_enable_list("all"))
    assert allv.show_criticality and allv.random_draw_count
    # 'all' does not switch on options that write files to fixed places
    assert not allv.sim_details and not allv.mission_file


@pytest.mark.parametrize("text", [
    "nonsense", "random_draw_count=maybe", "cumulative_by_trial",
    "cumulative_by_trial=-1", "event_log_filter=block+planet",
])
def test_typos_and_bad_values_are_errors(text):
    with pytest.raises(d.DevOptionError):
        d.from_mapping(d.parse_enable_list(text))


def test_from_mapping_rejects_unknown_keys():
    with pytest.raises(d.DevOptionError):
        d.from_mapping({"random_draws": True})


def test_environment_layers_over_a_base():
    base = d.from_mapping({"show_criticality": True})
    o = d.from_environment({"RAPTOR_DEV_FEATURES": "random_draw_count"}, base)
    assert o.show_criticality and o.random_draw_count


def test_every_feature_has_a_default_that_round_trips():
    default = d.DeveloperOptions().to_dict()
    assert set(default) == {f.key for f in d.DEV_FEATURES}
    assert d.from_mapping(default) == d.DeveloperOptions()


def test_each_feature_documents_its_source():
    for f in d.DEV_FEATURES:
        assert f.source and f.description and f.label
