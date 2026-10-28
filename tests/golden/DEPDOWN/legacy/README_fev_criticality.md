Captured from Raptor 7 (Student) launched with the developer switch, 2026-10-11, Failure Effects View on `tests/fixtures/DEPDOWN`.

`fev_interactive_log_criticality.txt` is the Interactive Log written (File > Write To Failure Effects Log) after:
1. click BlockA, click BlockB twice (A failed, B Idle): Red, `Criticality = 0.800000`
2. click BlockB once more (A failed, B operating): Yellow, `Criticality = 0.0`
3. (more clicks, then BlockA repaired and BlockB clicked: B failed alone): Yellow, `Criticality = 0.0`

The status bar showed `Criticality = 0.800000` through the whole A-then-B sequence and after A was repaired, and
`1.000000` once B failed on its own.
`tests/unit/test_fev_criticality_capture.py` replays it; `tests/data/fev_colours_raptor7.json` holds the colour classes read from
screenshots of the same session (not kept in the repository; see `test_fev_colours_vs_raptor7.py`).
