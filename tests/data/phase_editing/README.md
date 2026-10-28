Real Raptor 7 Student saves of `tests/golden/Phasing3`, 2026-10-10, through Options > Phases (kwin capture).

* `input.rbd` - Phasing3 opened and saved again by real, with no change (the baseline: real's own formatting).
* `real_ins3.rbd` - Insert Phase with row 3 selected (name typed into the box: `D151-225_OInsMidrbit`).
* `real_ins1.rbd` - Insert Phase with row 1 selected (name left as the row's: `D000-075_Orbit`).
* `real_add.rbd` - Insert Phase with the first free row selected (adds at the end; name `unnamed`, Fixed 100).
* `real_del2.rbd` - Delete Phase on row 2.
* `real_clear.rbd` - Clear Phases.

`tests/unit/test_phase_edit.py` applies the same action to `input.rbd` with `raptor.core.fileio.phase_edit` and requires the
same phase list, the same `P` columns and the same tokens in every row.
