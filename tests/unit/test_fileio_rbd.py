"""Tests for raptor.core.fileio.rbd against real captured .rbd fixtures.

These are the strongest correctness signal available for the file reader:
each fixture's header declares exact Blocks/Events/Nodes/Links counts, and
we can check the parser extracted exactly that many rows (plus the known
"default block" template wrinkle documented below).
"""
from __future__ import annotations

import pathlib

import pytest

from raptor.core.fileio.rbd import (
    LEGACY_VERSION,
    load_rbd,
    parse_rbd,
    render_rbd,
)

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"


def _fixture(name: str) -> pathlib.Path:
    path = GOLDEN / name / "input.rbd"
    if not path.exists():
        pytest.skip(f"golden fixture not present: {path}")
    return path


class TestEventsFixture:
    def test_header(self) -> None:
        # parse_rbd exposes the header's internal file name; load_rbd
        # overrides it with the disk basename (reports title themselves
        # with the disk name).
        from raptor.core.fileio.rbd import parse_rbd
        f = parse_rbd(open(str(_fixture("Events")),
                          encoding="latin-1").read())
        assert f.version == "Version7_7.0.09"
        assert f.file_name == "Events.rbd"
        f2 = load_rbd(str(_fixture("Events")))
        assert f2.file_name == "input.rbd"

    def test_declared_counts(self) -> None:
        f = load_rbd(str(_fixture("Events")))
        assert f.counts == {
            "Phases": 0, "Blocks": 0, "Events": 5, "Nodes": 10, "Links": 18,
            "SpPools": 0, "ResPools": 0, "Triggers": 0, "Hiers": 0, "HierDepths": 0,
        }

    def test_row_counts_match_declared(self) -> None:
        f = load_rbd(str(_fixture("Events")))
        assert len(f.section("EVENT_DATA").rows) == f.counts["Events"]
        assert len(f.section("NODE_DATA").rows) == f.counts["Nodes"]
        assert len(f.section("LINK_DATA").rows) == f.counts["Links"]
        assert len(f.section("HIERARCHY_DATA").rows) == f.counts["Hiers"]
        assert len(f.section("SPARE_POOL_DATA").rows) == f.counts["SpPools"]
        assert len(f.section("TRIGGERS").rows) == f.counts["Triggers"]

    def test_block_data_has_default_block_template_row(self) -> None:
        # BLOCK_DATA always carries one extra row beyond the declared Blocks
        # count: a default/template block definition present even in
        # fixtures with zero real blocks (Events Blocks=0 -> 1 row,
        # UniformProbe Blocks=1 -> 2 rows).
        f = load_rbd(str(_fixture("Events")))
        assert len(f.section("BLOCK_DATA").rows) == f.counts["Blocks"] + 1

    def test_event_rows_match_known_content(self) -> None:
        f = load_rbd(str(_fixture("Events")))
        rows = f.section("EVENT_DATA").rows
        names = [r["eventName"] for r in rows]
        assert names == ["P=0.91", "P=0.87", "P=0.65", "P=0.72", "P=0.53"]
        # all five share the same stream (GetStream(203) -> SysStream[3])
        assert all(r["failStream"] == "203" for r in rows)
        assert all(r["Distro"] == "17" for r in rows)  # Bernoulli
        assert [r["failVal"] for r in rows] == [
            "0.910000", "0.870000", "0.650000", "0.720000", "0.530000",
        ]

    def test_system_data_stream_config(self) -> None:
        f = load_rbd(str(_fixture("Events")))
        row = f.section("SYSTEM_DATA").rows[0]
        assert row["TerminationType"] == "FailSim"
        assert row["NumRuns"] == "1000.000000"
        # SysStreamsArray is the 11-integer system-stream config (one per
        # stream A-J plus Ancillary); GetStream(203) -> SysStream[3] ->
        # stream seed 9. This also confirms the multi-token column handling
        # in _zip_row keeps later fields aligned.
        assert row["SysStreamsArray"] == "7 8 9 10 70 71 72 73 74 75 101"
        assert row["xOrigin"] == "0.000000"
        assert row["nodeAnalType"] == "Availability"
        assert row["YRthreshold"] == "0.900000"


class TestUniformProbeFixture:
    def test_declared_counts(self) -> None:
        f = load_rbd(str(_fixture("UniformProbe")))
        assert f.counts["Blocks"] == 1
        assert f.counts["Nodes"] == 2
        assert f.counts["Links"] == 2

    def test_block_data_has_default_block_template_row(self) -> None:
        f = load_rbd(str(_fixture("UniformProbe")))
        assert len(f.section("BLOCK_DATA").rows) == f.counts["Blocks"] + 1

    def test_block_row_matches_known_rng_config(self) -> None:
        # UniformProbe is the fixture the base RNG recovery was built on;
        # pin its exact config so a parser change cannot drift it.
        f = load_rbd(str(_fixture("UniformProbe")))
        rows = f.section("BLOCK_DATA").rows
        real_block = next(r for r in rows if r["ID"] == "3")
        assert real_block["blockName"] == "unnamed"
        assert real_block["FDist"] == "10"  # Uniform Real
        assert real_block["FStream"] == "1"
        assert real_block["FParam1"] == "0.000000"
        assert real_block["FParam2"] == "1.000000"
        assert real_block["RDist"] == "10"
        assert real_block["RStream"] == "2"

    def test_block_data_duplicate_param_columns_disambiguated(self) -> None:
        # BLOCK_DATA's header repeats "Param1"/"Param2"/"Param3" three
        # times (Pre-LDT, Post-LDT, PM-trigger distribution params) — a
        # naive dict(zip(...)) would silently collide these and keep only
        # the last group; _disambiguate_columns keeps them apart.
        f = load_rbd(str(_fixture("UniformProbe")))
        cols = f.section("BLOCK_DATA").columns
        assert "Param1#1" in cols and "Param1#2" in cols and "Param1#3" in cols
        assert "Param1" not in cols  # bare name must not survive ambiguously
        real_block = next(r for r in f.section("BLOCK_DATA").rows if r["ID"] == "3")
        # Pre-LDT and Post-LDT both default to Fixed(0.0) on this block;
        # PM-trigger's own Dist/Param1 group defaults to Fixed(1.0) —
        # different value, confirming the three groups didn't collide.
        assert real_block["Param1#1"] == "0.000000"  # Pre-LDT
        assert real_block["Param1#2"] == "0.000000"  # Post-LDT
        assert real_block["Param1#3"] == "1.000000"  # PM trigger


class TestRoundTrip:
    """render_rbd(parse_rbd(x)) should reproduce parse_rbd(x) exactly
    (semantically — see render_rbd's docstring re: not being byte-exact
    against the legacy app's own padding). This is the strongest
    correctness signal available for the writer: it can only pass if
    reading and writing agree on every section, column, and row for a
    real file, not just a hand-picked value."""

    @pytest.mark.parametrize("fixture_name", ["Events", "UniformProbe"])
    def test_full_structural_round_trip(self, fixture_name: str) -> None:
        original = load_rbd(str(_fixture(fixture_name)))
        rendered_text = render_rbd(original)
        reparsed = parse_rbd(rendered_text)

        assert reparsed.version == original.version
        # The writer emits release-style headers (release Raptor refuses
        # the "Student" token), so the app name is intentionally not
        # round-tripped: reading back yields "" regardless of input.
        assert reparsed.app_name == ""
        assert reparsed.file_name == original.file_name
        assert reparsed.counts == original.counts
        assert reparsed.sections.keys() == original.sections.keys()
        for name in original.sections:
            orig_sec, new_sec = original.sections[name], reparsed.sections[name]
            assert new_sec.columns == orig_sec.columns, f"columns differ in {name}"
            assert new_sec.rows == orig_sec.rows, f"rows differ in {name}"

    def test_render_is_idempotent(self) -> None:
        # render(parse(render(parse(x)))) == render(parse(x)) — a second
        # round trip shouldn't drift further from the first.
        original = load_rbd(str(_fixture("Events")))
        once = render_rbd(original)
        twice = render_rbd(parse_rbd(once))
        assert once == twice

    def test_large_real_file_round_trips(self) -> None:
        # Hierarchy1-7.rbd: 149 blocks, 61 nodes, 309 links — the strongest
        # available stress test (the other two fixtures are small/synthetic
        # by comparison). See tests/golden/Hierarchy1-7/README.md.
        original = load_rbd(str(_fixture("Hierarchy1-7")))
        assert original.counts["Blocks"] == 149
        assert original.counts["Nodes"] == 61
        assert original.counts["Links"] == 309
        reparsed = parse_rbd(render_rbd(original))
        for name in original.sections:
            orig_sec, new_sec = original.sections[name], reparsed.sections[name]
            assert new_sec.columns == orig_sec.columns, f"columns differ in {name}"
            assert new_sec.rows == orig_sec.rows, f"rows differ in {name}"

    def test_empty_comments_section_emits_bare_slash_terminator(self) -> None:
        # COMMENTS has no `EndOf...Data` sentinel (see _END_MARKERS'
        # docstring); real files instead have a bare "/" line terminating
        # it. The reader tolerates that line as noise, but the writer must
        # still emit it: without it, the LINK_DATA section marker and header
        # are read as COMMENTS data and everything after is corrupt. A
        # parse->render->reparse round trip cannot see this because both
        # sides agree either way.
        original = load_rbd(str(_fixture("UniformProbe")))
        assert original.sections["COMMENTS"].rows == []
        rendered_text = render_rbd(original)
        lines = rendered_text.splitlines()
        comments_idx = next(i for i, l in enumerate(lines) if l.startswith("/") and "COMMENTS" in l)
        link_idx = next(i for i, l in enumerate(lines) if l.startswith("/") and "LINK_DATA" in l)
        assert "/" in lines[comments_idx + 2:link_idx], (
            "expected a bare '/' line terminating the empty COMMENTS section"
        )


class TestPhaseColumns:
    """`BLOCK_DATA`/`EVENT_DATA`'s phase columns are named `"P 001"` (with
    a space) in the real file, with ragged two-token `"X: value"` cells
    only on rows whose `Phasing` flag is `"1"`; `NODE_DATA` uses `"P001"`
    with single-token cells. See `raptor/core/fileio/rbd.py`'s
    `_PHASE_COLUMN_RE` / `_merge_phase_header_tokens` docstrings. The
    snippets below are copied verbatim from `tests/data/models/Phasing1.rbd`."""

    _BLOCK_HEADER = (
        "blockName ID xPos yPos ParentId In Out Phasing SimStart AmtExhausted "
        "FDist #FParam FStream FParam1 FParam2 FParam3 RDist #RParam RStream "
        "RParam1 RParam2 RParam3 SBStress GDType GDRate GDLimit SparePoolName "
        "SpType RSO Stock NewSp ArriveEvery ESO EmerTime SLO Level Quan SLOTime "
        "Pre-LDTDist Param1 Param2 Param3 Post-LDTDist Param1 Param2 Param3 "
        "NumRes #Res1Rep #Res1pmRep Resource1Name usesPM PMSpare PMRefresh "
        "PMMisDefer PMFailReset PMReqDefer PMStaggerTime PMFreq pmTriggered "
        "pmTrig Dist Param1 Param2 Param3 DepNum DepNothing DepIdle DepPM "
        "DepFail DepPMThresh defDepStateIdle DepType initial Running standby "
        "idle repHold repPerTime repFixed donePerTime doneFixed pmHoldCost "
        "pmCost pmFixedCost perSpare emerShipping addDone P 001 P 002 P 003"
    )
    _PHASED_BLOCK_ROW = (
        "Thermal 4 18.750000 91.250000 0 1 1 1 3 0.000000 4 2 203 2435.000000 "
        "0.000000 0.000000 14 3 203 6.000000 16.500000 24.000000 0.000 0 "
        "0.000000 2435.000000 unnamed Custom 1 1 1 1460.000000 1 48.000000 0 0 "
        "1 24.000000 14 0.100000 0.150000 0.650000 14 0.100000 0.150000 "
        "0.650000 1 2 1 Astronauts 0 1 1 0 1 1 0.000000 100.000000 0 none 19 "
        "1.000000 0.000000 0.000000 0 0.00 100.00 0.00 0.00 75.00 1 None "
        "85000.00 15.00 0.00 0.00 175.00 55.00 10108.00 0.00 0.00 0.00 0.00 "
        "0.00 5185.00 12689644.00 0 A:   1.000 A:   1.500 A:   1.500"
    )
    _UNPHASED_BLOCK_ROW = (
        "SolarP_650W 8 3.750000 92.500000 0 1 1 0 3 0.000000 6 3 203 24.000000 "
        "34.300000 0.000000 8 2 203 23.000000 0.870000 0.000000 0.000 0 "
        "0.000000 823.200000 PhotoVPanels Pooled 1 100 1 720.000000 1 24.000000 "
        "0 0 1 24.000000 19 2.000000 0.000000 0.000000 19 0.000000 0.000000 "
        "0.000000 1 3 1 Astronauts 1 0 0 0 1 1 0.000000 100.000000 1 "
        "650W-PowerString 14 1.000000 1.500000 2.000000 -1 10.00 80.00 0.00 "
        "10.00 75.00 1 None 3125.00 225.00 0.00 33.00 150.00 500.00 9025.00 "
        "0.00 0.00 50.00 100.00 1000.00 3125.00 50000.00 0"
    )

    def _parse_block_section(self, rows: list[str]) -> dict:
        text = (
            "Version7_7.0.11 Student         Test.rbd        Sun Aug 16 12:00:00 2026\n"
            "Phases=       3\n"
            "/\n"
            "/___BLOCK_DATA___\n" + self._BLOCK_HEADER + "\n"
            + "\n".join(rows) + "\n"
            "EndOfBlockData\n"
        )
        return parse_rbd(text).section("BLOCK_DATA")

    def test_header_p_columns_merged_despite_embedded_space(self) -> None:
        section = self._parse_block_section([self._PHASED_BLOCK_ROW])
        assert "P 001" in section.columns
        assert "P 002" in section.columns
        assert "P 003" in section.columns
        assert "P" not in section.columns
        assert "001" not in section.columns

    def test_phased_row_gets_two_token_phase_values(self) -> None:
        section = self._parse_block_section([self._PHASED_BLOCK_ROW])
        row = section.rows[0]
        assert row["blockName"] == "Thermal"
        assert row["Phasing"] == "1"
        assert row["P 001"] == "A: 1.000"
        assert row["P 002"] == "A: 1.500"
        assert row["P 003"] == "A: 1.500"

    def test_unphased_row_has_no_phase_columns_and_stays_aligned(self) -> None:
        section = self._parse_block_section([self._UNPHASED_BLOCK_ROW])
        row = section.rows[0]
        assert row["blockName"] == "SolarP_650W"
        assert row["Phasing"] == "0"
        assert "P 001" not in row
        # The ragged row must not misalign anything BEFORE the phase
        # columns -- addDone (the real last ordinary column) must still
        # read correctly.
        assert row["addDone"] == "0"

    def test_mixed_phased_and_unphased_rows_stay_independently_correct(self) -> None:
        # Phasing is derived per row, not from a per-file flag: a per-file
        # flag would apply one row's phasing to its neighbor.
        section = self._parse_block_section([self._PHASED_BLOCK_ROW, self._UNPHASED_BLOCK_ROW])
        assert len(section.rows) == 2
        thermal, solar = section.rows
        assert thermal["P 001"] == "A: 1.000"
        assert "P 001" not in solar

    def test_node_phase_columns_are_single_token_no_space_header(self) -> None:
        text = (
            "Version7_7.0.11 Student         Test.rbd        Sun Aug 16 12:00:00 2026\n"
            "Phases=       3\n"
            "/\n"
            "/___NODE_DATA___\n"
            "Name ID xPos yPos parent Type k n Out Cold PR Autos k* Phases depNum "
            "depType divFlow fullflow repCap repNA P001 P002 P003\n"
            "out 25 15.000000 93.750000 23 5 -1 1 0 0 0 0 0 1 0 None 1 1 0 0 C 1 1\n"
            "Power 19 16.250000 91.250000 0 2 2 5 1 0 0 1 0 1 0 None 1 0 0 1 1 2 2\n"
            "n1 16 6.250000 91.250000 0 2 -1 1 5 0 0 1 0 0 0 None 0 1 0 1\n"
            "EndOfNodeData\n"
        )
        section = parse_rbd(text).section("NODE_DATA")
        assert "P001" in section.columns
        rows = {r["Name"]: r for r in section.rows}
        assert rows["out"]["P001"] == "C"
        assert rows["out"]["P002"] == "1"
        assert rows["out"]["P003"] == "1"
        assert rows["Power"]["P001"] == "1"
        assert rows["Power"]["P002"] == "2"
        assert "P001" not in rows["n1"]  # Phases=0


class TestProtectedFileAndVersion:
    """Original's overwrite protection + Version 5/6 handling."""

    def _text(self, version: str, last: str) -> str:
        return (
            f"{version} Student         Test.rbd        Sun Aug 16 12:00:00 2026\n"
            "Blocks=        0\n"
            "/\n"
            "/___SYSTEM_DATA___\n"
            "imageString NumRuns NotUsed\n"
            f"None 1 {last}\n"
            "EndOfSystemData\n"
        )

    def test_last_system_data_minus_one_marks_protected(self) -> None:
        assert parse_rbd(self._text("Version7_7.0.11", "-1")).protected_file

    def test_ordinary_system_data_is_not_protected(self) -> None:
        assert not parse_rbd(self._text("Version7_7.0.11", "1")).protected_file

    def test_version_5_and_6_rejected_with_clear_message(self) -> None:
        for ver in ("Version6_6.0.0", "Version5_5.0.0"):
            with pytest.raises(ValueError, match="unsupported RAPTOR file "
                                                 "version"):
                parse_rbd(self._text(ver, "1"))


class TestLegacyVersionNormalisation:
    """A spec-built model carries ``version="rbds"`` (see
    ``raptor.api.build.build_from_spec``), which is not a valid legacy
    header. ``render_rbd`` normalises it to a ``Version7`` release token,
    otherwise ``parse_rbd`` rejects the export and ``simulate(workers>1)``
    — whose parallel path serialises the model to legacy ``.rbd`` for its
    workers — fails with "unrecognized .rbd header line"."""

    def test_rbds_built_model_renders_parseable_legacy_header(self) -> None:
        from raptor.api.build import build_from_spec, k_of_n

        rbd = build_from_spec(k_of_n(2, 3))
        assert rbd.version == "rbds"  # the .rbds-origin marker
        # Mirror the real export path (save_rbd stamps the disk basename,
        # which ends in .rbd — required for parse_rbd's release-header
        # file_name/app disambiguation).
        rbd.file_name = "roundtrip.rbd"

        text = render_rbd(rbd)
        assert text.splitlines()[0].startswith("Version7")

        # Must not raise (the actual bug: parse_rbd rejected this file).
        reparsed = parse_rbd(text)
        assert reparsed.version == LEGACY_VERSION
        assert reparsed.file_name == rbd.file_name
        # Every section real Raptor expects is written, in its order, even those the spec-built model
        # left out (COMMENTS) or left without a header (2026-10-01: real opens such a file blank).
        from raptor.core.fileio.rbd import _LEGACY_SECTION_ORDER
        assert list(reparsed.sections) == _LEGACY_SECTION_ORDER
        assert set(rbd.sections) <= set(reparsed.sections)

    def test_rbds_marker_model_round_trips_structure(self) -> None:
        # A fully-formed model carrying the .rbds version marker must
        # round-trip every section/row through the legacy writer.
        rbd = load_rbd(str(_fixture("Events")))
        rbd.version = "rbds"
        reparsed = parse_rbd(render_rbd(rbd))
        assert reparsed.version == LEGACY_VERSION
        for name, section in rbd.sections.items():
            assert reparsed.sections[name].columns == section.columns, name
            assert reparsed.sections[name].rows == section.rows, name

    def test_non_version7_token_normalised(self) -> None:
        # Any non-Version7 token (a Version6 file rendered through the
        # writer, say) is stamped with the legacy release version.
        rbd = load_rbd(str(_fixture("Events")))
        rbd.version = "Version6_6.0.0"
        assert render_rbd(rbd).splitlines()[0].startswith(LEGACY_VERSION)

    def test_real_release_version_preserved(self) -> None:
        # A genuine Version7 token must not be rewritten.
        rbd = load_rbd(str(_fixture("Events")))
        assert render_rbd(rbd).splitlines()[0].startswith(rbd.version)

    def test_spec_built_model_exports_rows_not_just_header(self) -> None:
        # build_from_spec leaves section.columns empty; render_rbd derives
        # the column order from the rows when a section has none, so the
        # .rbd export (and the parallel-worker path, which serialises via
        # render_rbd) carries real elements.
        from raptor.api.build import build_from_spec

        spec = {
            "blocks": [{"name": "B1", "fail_dist": 4, "fail_params": [100, 0]}],
            "nodes": [{"name": "start", "type": "start"},
                      {"name": "end", "type": "end"}],
            "links": [
                {"from_type": "Node", "from": "start",
                 "to_type": "Block", "to": "B1"},
                {"from_type": "Block", "from": "B1",
                 "to_type": "Node", "to": "end"},
            ],
        }
        rbd = build_from_spec(spec)
        rbd.file_name = "spec.rbd"
        reparsed = parse_rbd(render_rbd(rbd))
        names = [r.get("blockName")
                 for r in reparsed.section("BLOCK_DATA").rows]
        assert "B1" in names, f"block row lost in legacy export: {names}"
        assert len(reparsed.section("NODE_DATA").rows) >= 2
        assert len(reparsed.section("LINK_DATA").rows) >= 2


def test_fractional_capacity_flow_is_written_as_a_whole_number() -> None:
    """Regression: a decimal in ``LINK_DATA.nomFlow``/``MaxFlow`` crashed
    real Raptor 7 on open (``MODSIM Runtime error: Access Violation``).

    The reader takes those columns with ``ReadInt``, which
    stops at the decimal point and leaves the fraction in the token stream,
    shifting every later read. A 3,470-block model whose only two fractional
    cells were these columns failed to open until the export truncated them.
    """
    rbd = load_rbd(str(_fixture("Events")))
    link = next(r for r in rbd.section("LINK_DATA").rows if r.get("ID") != "0")
    link["nomFlow"] = "133.760000"
    link["MaxFlow"] = "133.760000"

    reparsed = parse_rbd(render_rbd(rbd))
    written = next(r for r in reparsed.section("LINK_DATA").rows
                   if r.get("ID") == link["ID"])
    # Truncated, matching typed.row_int / the engine, not rounded.
    assert written["nomFlow"] == "133"
    assert written["MaxFlow"] == "133"


def test_rendered_integer_columns_never_carry_a_decimal_point() -> None:
    """Every column the legacy reader consumes with ``ReadInt`` must be ONE
    whole-number token in the export. A decimal is not merely truncated by
    the reader -- it desynchronises the whole rest of the file."""
    from raptor.core.fileio.rbd import _LEGACY_INT_COLUMNS

    rbd = load_rbd(str(_fixture("Events")))
    # Give several integer columns fractional values, not just the capacity
    # flows, so the table itself is covered rather than one call site.
    rbd.section("LINK_DATA").rows[0].update(nomFlow="2.5", MaxFlow="2.5")
    rbd.section("NODE_DATA").rows[0].update(k="1.5")
    rbd.section("BLOCK_DATA").rows[0].update(Stock="3.25")

    reparsed = parse_rbd(render_rbd(rbd))
    for section, columns in _LEGACY_INT_COLUMNS.items():
        for row in reparsed.sections[section].rows:
            for column in columns:
                value = row.get(column, "")
                assert "." not in value, f"{section}.{column} = {value!r}"


def test_save_derives_nested_hierarchy_levels(tmp_path) -> None:
    """A hierarchy's ``level`` is its nesting depth, not a free field.

    Raptor 7's pre-sim analysis walks down with ``mamaLevel :=
    hier.level + 1``; when every hierarchy claimed level 1 while ``Parent``
    nested them three deep, the walk missed whole subtrees and the engine
    died on Simulate with ``MODSIM Runtime error: Array allocation: high
    bound is less than low bound``. ``save_rbd`` must derive it.
    """
    from raptor.core.fileio.rbd import save_rbd

    rbd = load_rbd(str(_fixture("LadL8A")))
    hiers = [r for r in rbd.section("HIERARCHY_DATA").rows if r.get("ID") != "0"]
    child = next(r for r in hiers if r.get("Parent") not in ("0", ""))
    for row in hiers:
        row["level"] = "1"          # the stale all-flat value

    out = tmp_path / "levels.rbd"
    save_rbd(rbd, str(out))

    saved = {r["ID"]: r for r in load_rbd(str(out)).section("HIERARCHY_DATA").rows}
    assert saved[child["ID"]]["level"] == "2"
    assert saved[child["Parent"]]["level"] == "1"


def test_save_writes_a_status_image_real_raptor_can_load(tmp_path) -> None:
    """Raptor 7 loads the run's status faces as ``<first 4 chars>_G/_Y/_R``
    from a fixed library. The port does not model the picture, so its
    template says ``None`` -- which crashes the legacy app at Simulate with
    ``GRAPHICS ERROR 220: Object 'None_G' not found in library``. An export
    must name a real family; a real one already in the model is kept.
    """
    from raptor.core.fileio.rbd import (LEGACY_SYSTEM_IMAGE, save_rbd)

    rbd = load_rbd(str(_fixture("Events")))
    row = rbd.section("SYSTEM_DATA").rows[0]
    row["imageString"] = "None"
    save_rbd(rbd, str(tmp_path / "none.rbd"))
    assert load_rbd(str(tmp_path / "none.rbd")).section("SYSTEM_DATA").rows[0][
        "imageString"] == LEGACY_SYSTEM_IMAGE

    row["imageString"] = "Aircraft"
    save_rbd(rbd, str(tmp_path / "kept.rbd"))
    assert load_rbd(str(tmp_path / "kept.rbd")).section("SYSTEM_DATA").rows[0][
        "imageString"] == "Aircraft"


def test_the_blank_model_template_names_a_real_status_image() -> None:
    """`.rbds` does not store `imageString`; a file built from a blank model
    inherits this template value, so it must be one legacy can load."""
    from raptor.core.fileio.rbd import _LEGACY_IMAGE_FAMILIES, parse_rbd
    from raptor.gui.new_model import NEW_MODEL_TEXT

    image = parse_rbd(NEW_MODEL_TEXT).section("SYSTEM_DATA").rows[0]["imageString"]
    assert image[:4] in _LEGACY_IMAGE_FAMILIES, image


def test_saving_legacy_does_not_mutate_the_open_model(tmp_path) -> None:
    """Legacy compatibility (20-char names, 1.25 grid) belongs to the
    file being written, not the model still open on screen. In-place
    mutation hid the change from undo and made a later .rbds save keep
    the truncated names."""
    from raptor.core.fileio.rbd import save_rbd

    rbd = load_rbd(str(_fixture("TwoBlockRedundancy")))
    blocks = [r for r in rbd.section("BLOCK_DATA").rows
              if r.get("ID") not in (None, "", "0")]
    assert len(blocks) >= 2
    blocks[0]["blockName"] = "A_very_long_block_name_over_twenty"
    long_name = blocks[0]["blockName"]
    for row in blocks[:2]:
        row["xPos"] = "7.777777"
        row["yPos"] = "7.777777"
    out = tmp_path / "legacy.rbd"
    save_rbd(rbd, str(out))

    assert blocks[0]["blockName"] == long_name
    assert blocks[0]["xPos"] == "7.777777"
    assert blocks[0]["yPos"] == "7.777777"
    saved = load_rbd(str(out))
    saved_block = next(r for r in saved.section("BLOCK_DATA").rows
                       if r.get("ID") == blocks[0]["ID"])
    assert len(saved_block["blockName"]) <= 20
    assert saved_block["blockName"] != long_name


@pytest.mark.parametrize("fixture", sorted(p.parent.name for p in GOLDEN.glob("*/input.rbd")))
def test_render_keeps_ragged_phase_rows_token_for_token(fixture: str) -> None:
    """Every data row must render with exactly the token count the real
    file has. The semantic round-trip cannot see extra trailing tokens on
    an unphased row (parse_rbd skips them), but real Raptor reads them as
    the next row's name and crashes on open."""
    path = GOLDEN / fixture / "input.rbd"
    original = path.read_text(encoding="latin-1")
    rendered = render_rbd(parse_rbd(original))

    def data_rows(text: str) -> dict[str, list[int]]:
        rows: dict[str, list[int]] = {}
        section = None
        header_next = False
        for line in text.splitlines():
            if line.startswith("/___"):
                section, header_next = line.strip("/_ "), True
                continue
            if section is None or not line.strip() or line.startswith(("EndOf", "/")):
                continue
            if header_next:
                header_next = False
                continue
            rows.setdefault(section, []).append(len(line.split()))
        return rows

    assert data_rows(rendered) == data_rows(original)
