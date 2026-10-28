"""Run experiment + run manifest: the reproducibility contract.

These two modules exist because of a specific hazard 
and the tests exist to pin that hazard down rather than to
increase coverage:

1. Run settings (trials, termination, analyses, recording) must be able
   to live with the model WITHOUT becoming part of the model's
   identity. Otherwise bumping the trial count changes the model content
   hash and invalidates the provenance of every result set already
   produced -- a parameter sweep would destroy its own evidence.
2. A result set must be able to describe itself after the model has
   moved on.
3. Anything read from a file, a legacy row or a user's hand-edited JSON
   has to degrade to a usable value rather than raise.
"""
from __future__ import annotations

import pytest

from raptor.core import run_experiment as rx
from raptor.core import run_manifest as rm
from raptor.core.fileio.rbd import load_rbd

FIXTURE = "tests/golden/HierarchyExample/input.rbd"


# --------------------------------------------------------------- experiment

class TestDefaults:
    def test_defaults_are_a_fresh_copy(self):
        """The nested `recording` dict must not be shared, or one caller's
        mutation becomes everyone's default."""
        a = rx.defaults()
        b = rx.defaults()
        a["recording"]["event_log"] = False
        assert b["recording"]["event_log"] is True

    def test_run_length_default_matches_the_original(self):
        """The original's default run length was 1000;
       ours had drifted to 100 in the embedded
        template. Pin the documented value so it cannot drift again."""
        assert rx.RUN_DEFAULTS["term_condition"] == 1000.0
        assert rx.RUN_DEFAULTS["recording"]["ao_interval"] == 10.0
        assert rx.RUN_DEFAULTS["recording"]["cost_interval"] == 720.0


class TestNormalise:
    @pytest.mark.parametrize("raw,key,bad", [
        ({"trials": "not a number"}, "trials", "not a number"),
        ({"trials": 0}, "trials", 0),          # below the minimum
        ({"trials": -5}, "trials", -5),
        ({"term_condition": "x"}, "term_condition", "x"),
        ({"start_stats": None}, "start_stats", None),
    ])
    def test_garbage_falls_back_per_field(self, raw, key, bad):
        """Each field recovers INDEPENDENTLY, so one bad value does not
        discard the rest of a hand-edited file."""
        out = rx.normalise(raw)
        assert out[key] != bad
        assert out[key] == rx.RUN_DEFAULTS[key]

    def test_unknown_termination_is_rejected_not_stored(self):
        out = rx.normalise({"termination_type": "BananaSim"})
        assert out["termination_type"] in ("TimeSim", "FailSim", "CycleSim")

    def test_analyses_are_filtered_to_known_names(self):
        out = rx.normalise({"analyses": ["weak", "nonsense", "cost"]})
        assert out["analyses"] == ["weak", "cost"]

    def test_non_dict_recording_is_ignored(self):
        out = rx.normalise({"recording": "yes please"})
        assert out["recording"]["event_log"] is True

    def test_zero_interval_is_clamped(self):
        """0.0 would mean "sample infinitely often"."""
        out = rx.normalise({"recording": {"ao_interval": 0}})
        assert out["recording"]["ao_interval"] >= 0.001


class TestProfiles:
    def test_every_profile_produces_a_valid_experiment(self):
        for name in rx.PROFILES:
            exp = rx.apply_profile(name, {})
            assert exp["trials"] >= 1
            assert exp["termination_type"] in ("TimeSim", "FailSim", "CycleSim")
            assert isinstance(exp["analyses"], list)

    def test_profile_overlays_rather_than_replaces(self):
        """A profile that does not mention `recording` must not blank it."""
        base = rx.normalise({"recording": {"ao_plot": True}})
        out = rx.apply_profile("quick_check", base)
        assert out["recording"]["event_log"] is True

    def test_unknown_profile_is_a_no_op(self):
        base = rx.normalise({"trials": 7})
        assert rx.apply_profile("nope", base)["trials"] == 7


class TestLegacyRoundTrip:
    def test_from_system_row_reads_what_legacy_has(self):
        row = {"NumRuns": "250", "TerminationType": "FailSim",
               "TermCondition": "12.5", "StartStats": "3",
               "weakAnalysis": "1", "costAnalysis": "0",
               "capacityAnalysis": "1"}
        exp = rx.from_system_row(row)
        assert exp["trials"] == 250
        assert exp["termination_type"] == "FailSim"
        assert exp["term_condition"] == 12.5
        assert exp["start_stats"] == 3.0
        assert exp["analyses"] == ["weak", "capacity"]

    def test_from_empty_row_is_all_defaults(self):
        assert rx.from_system_row({}) == rx.defaults()
        assert rx.from_system_row(None) == rx.defaults()

    def test_to_system_row_writes_the_seven_legacy_columns(self):
        pairs = dict(rx.to_system_row(
            rx.apply_profile("steady_state", {})))
        assert set(pairs) == {
            "NumRuns", "TerminationType", "TermCondition", "StartStats",
            "weakAnalysis", "costAnalysis", "capacityAnalysis"}

    def test_analysis_flags_round_trip(self):
        exp = rx.normalise({"analyses": ["weak", "capacity"]})
        row = dict(rx.to_system_row(exp))
        assert row["weakAnalysis"] == "1"
        assert row["costAnalysis"] == "0"
        assert row["capacityAnalysis"] == "1"
        assert rx.from_system_row(row)["analyses"] == ["weak", "capacity"]


# ----------------------------------------------------------------- manifest

class TestContentHash:
    def test_stable_across_loads(self):
        a, b = load_rbd(FIXTURE), load_rbd(FIXTURE)
        assert rm.model_content_hash(a) == rm.model_content_hash(b)

    def test_run_settings_do_not_change_the_hash(self):
        """THE point of this module. If trials, run length, statistics
        start or the analysis flags changed the model's identity, then
        running a convergence sweep would invalidate the provenance of
        every result set already produced -- the sweep would destroy its
        own evidence."""
        a = load_rbd(FIXTURE)
        before = rm.model_content_hash(a)
        row = a.sections["SYSTEM_DATA"].rows[0]
        for col, val in (("NumRuns", "9999"), ("TerminationType", "FailSim"),
                         ("TermCondition", "3.0"), ("StartStats", "7"),
                         ("weakAnalysis", "1"), ("capacityAnalysis", "1"),
                         ("graphicsUse", "GraphicsOn")):
            row[col] = val
        assert rm.model_content_hash(a) == before, \
            "a run setting must not change the model's content hash"

    def test_view_state_does_not_change_the_hash(self):
        """Panning or zooming is cosmetic; it must not look like an
        engineering change."""
        a = load_rbd(FIXTURE)
        before = rm.model_content_hash(a)
        row = a.sections["SYSTEM_DATA"].rows[0]
        for col, val in (("zoomVal", "99"), ("xOrigin", "-500"),
                         ("yOrigin", "250"), ("imageString", "Truck")):
            row[col] = val
        assert rm.model_content_hash(a) == before

    def test_engineering_change_does_change_the_hash(self):
        a = load_rbd(FIXTURE)
        before = rm.model_content_hash(a)
        row = a.sections["BLOCK_DATA"].rows[1]
        row["FParam1"] = "999.0"
        assert rm.model_content_hash(a) != before, \
            "a changed failure distribution must change the hash"

    def test_a_new_link_changes_the_hash(self):
        a = load_rbd(FIXTURE)
        before = rm.model_content_hash(a)
        a.sections["LINK_DATA"].rows[0]["CapPri"] = "1"
        assert rm.model_content_hash(a) != before

    def test_random_streams_are_engineering_content(self):
        """Stream numbers determine the draws, hence the results, so
        moving them IS a change of the model."""
        a = load_rbd(FIXTURE)
        before = rm.model_content_hash(a)
        a.sections["SYSTEM_DATA"].rows[0]["SysStreamsArray"] = "9 8 7 6"
        assert rm.model_content_hash(a) != before


class TestManifest:
    def test_carries_identity_settings_and_seed_policy(self):
        """The spec's checklist: model identifier, revision and content
        hash, enabled analysis modules, and never
        "random" without a recorded seed ."""
        m = rm.build_manifest(load_rbd(FIXTURE),
                              rx.apply_profile("steady_state", {}),
                              profile="steady_state",
                              engine_version="raptor-studio test")
        for key in ("model_name", "model_hash", "engine_version",
                    "profile", "trials", "termination_type",
                    "start_stats", "analyses", "recording",
                    "seed_strategy"):
            assert key in m, f"manifest is missing {key}"
        assert m["model_hash"] == rm.model_content_hash(load_rbd(FIXTURE))
        assert m["analyses"] == ["weak"]
        assert m["seed_strategy"]

    def test_digest_is_stable_and_short(self):
        a = rm.build_manifest(load_rbd(FIXTURE), rx.defaults())
        b = rm.build_manifest(load_rbd(FIXTURE), rx.defaults())
        assert rm.manifest_digest(a) == rm.manifest_digest(b)
        assert len(rm.manifest_digest(a)) == 12

    def test_digest_changes_with_the_experiment(self):
        a = rm.build_manifest(load_rbd(FIXTURE), rx.defaults())
        b = rm.build_manifest(
            load_rbd(FIXTURE), rx.normalise({"trials": 500}))
        assert rm.manifest_digest(a) != rm.manifest_digest(b)

    def test_manifest_is_normalised(self):
        """A hand-edited experiment reaches the manifest sanitised."""
        m = rm.build_manifest(load_rbd(FIXTURE),
                              {"trials": -1, "termination_type": "Nope"})
        assert m["trials"] == 1
        assert m["termination_type"] in ("TimeSim", "FailSim", "CycleSim")


def test_results_hash_ignores_layout_but_not_engineering():
    """`model_results_hash` decides whether displayed results are stale.
    Moving a block cannot change a simulated number; a distribution can."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.core.run_manifest import (model_content_hash,
                                          model_results_hash)
    rbd = load_rbd("tests/golden/TwoBlockRedundancy/input.rbd")
    base_results = model_results_hash(rbd)
    base_identity = model_content_hash(rbd)
    row = rbd.sections["BLOCK_DATA"].rows[0]

    row["xPos"] = "987.000000"
    assert model_results_hash(rbd) == base_results
    # The stored manifest identity is deliberately NOT layout-blind:
    # widening it would orphan every existing manifest's model_hash.
    assert model_content_hash(rbd) != base_identity

    row["FParam1"] = "12345.000000"
    assert model_results_hash(rbd) != base_results
