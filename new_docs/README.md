# new_docs — the RAPTOR Studio tutorial book

AsciiDoc training material for RAPTOR Studio. Not derived from the
Raptor 7 workbook: new structure, new examples, new model files. Written in Simplified-Technical-English-flavored prose
(short active sentences, domain terms defined on first use).

## Layout

    book.adoc            master document (renders the whole book)
    chapters/            23 chapters (ch00–ch16 + ch-dependency + ch-events
                         + ch-phasing + ch-transients + ch-capstone-line
                         + ch-capstone-flight) + 3 appendices (glossary,
                         IEC 61078, developer options) — reading order
                         is book.adoc's own include:: list, not filename
                         order. Answers live in each chapter, in a
                         [%collapsible] block under the questions they
                         answer; there is no separate answer key.
    models/*.rbds        27 tutorial models (Lantern1, Espresso1, Drone1,
                         EspressoDep1, FinaleShow1, CarWash1, Laundromat1,
                         Toaster_Fixed/Expo/Weibull, Vendo1, TacoTruck1,
                         Bakery1, CarCare0/1, Van1, VanFlat, Water1/2,
                         DC1/DC2, ProdLine0/1, Flight1, HospitalPower,
                         WaterTreatment, AirlinerMission)
    images/              GUI screenshots (PNG) + diagrams (SVG)
    tools/gen_models.py        regenerates the chapter models (24 files)
    tools/gen_advanced_models.py  regenerates HospitalPower, WaterTreatment,
                               AirlinerMission (the larger start-page examples)
    tools/check_models.py      headless run of every model (sanity check)
    tools/check_links.py       broken xrefs, missing images, dangling opsmodel::
    tools/check_numbers.py     re-runs registered models, catches drifted numbers
    tools/capture_screens.py   recaptures screenshots (offscreen Qt)
    tools/gen_diagrams.py      regenerates the data-driven SVGs

Quality gates run as part of the test suite
(`tests/unit/test_docs_quality_gates.py`) rather than as a separate script a
reviewer has to remember to invoke. Plain `pytest -q` runs the link, tool and
model checks; the quoted-number check is marked `long` (`pytest -q -m long`,
CI's long-checks job).

## Render the book

    new_docs/tools/render_docs.sh

Renders BOTH help docs with the shared `ops_asciidoctor.css` theme and
the `opsmodel::` macro extension (`tools/ops_adoc_ext.rb`), each dir
self-contained (stylesheet copied next to the HTML):

    asciidoctor -r new_docs/tools/ops_adoc_ext.rb \
      -a stylesheet=ops_asciidoctor.css -a linkcss \
      new_docs/book.adoc -o new_docs/book.html
    asciidoctor -r new_docs/tools/ops_adoc_ext.rb \
      -a stylesheet=ops_asciidoctor.css -a linkcss \
      docs/manual.adoc -o docs/manual.html

## Application reference manual (separate from the book)

`docs/manual.adoc` is the field-by-field *reference* (what every
control does), while this book is the *tutorial* (why and when).
Its "UI Reference" section is generated from the live GUI:

    new_docs/tools/render_docs.sh   # book + manual, one command
    .venv/bin/python new_docs/tools/gen_help_reference.py   # regenerate the UI tables

The app's **Help** ribbon tab opens `book.html` (User Guide) and
`manual.html` (Reference Manual) **inside the app** (QtWebEngine
viewer: Ctrl+F find, in-app cross-links between book and manual,
external links go to the system browser). `opsmodel::Name[]` links in
either doc open the companion model in RAPTOR Studio. F1 jumps into
the in-app viewer at the section for the focused dock or dialog.

## What every chapter must satisfy

The quality gates (`tests/unit/test_docs_quality_gates.py`) say what every chapter must
satisfy: links resolve, models open and run, quoted numbers match a fresh run.

## Regenerate everything from scratch

From `new_docs/tools`:

    ../../.venv/bin/python gen_models.py       # writes models/*.rbds
    ../../.venv/bin/python gen_advanced_models.py  # the three larger examples
    ../../.venv/bin/python check_models.py     # runs them headless
    ../../.venv/bin/python capture_screens.py  # rewrites images/*.png
    ../../.venv/bin/python gen_diagrams.py     # rewrites curve SVGs
    ../../.venv/bin/python ../../tools/make_example_thumbnails.py  # start-page thumbnails

## Model provenance and verified numbers

All models are original to this tutorial. Long-run values below were
verified with 2000-trial headless runs (`check_models.py`); quote them
as "about", never as exact:

| model | metric | long-run value |
|---|---|---|
| Lantern1 | reliability @48 h | ≈ 0.38 |
| Espresso1 | reliability @500 h | ≈ 0.37 |
| EspressoDep1 | reliability @500 h | ≈ 0.21 (collapses to the single-pump figure — see ch-dependency) |
| Drone1 | reliability @48 h | ≈ 0.59 |
| FinaleShow1 | reliability (need 3 of 5, p=0.8 each) | ≈ 0.94 (analytic 0.94208; Availability always 1.000, MTBDE/MDT N/A — see ch-events) |
| CarWash1 | availability / dependability @480 min (1000 runs) | ≈ 0.70 / ≈ 0.98 (Reliability is NOT trustworthy for this model — see ch-phasing's Limits box) |
| Laundromat1 | availability, naive 200h vs 2200h+2000h-delayed (2000 runs) | ≈ 0.96 (new-machine honeymoon) vs ≈ 0.94 (steady state) — see ch-transients |
| Toaster_Fixed | reliability @600 h | 1.00 |
| Toaster_Expo | reliability @600 h | ≈ 0.55 |
| Toaster_Weibull | reliability @600 h | ≈ 0.67 |
| Vendo1 | availability (year) | ≈ 0.97, ~35 stoppages/run |
| TacoTruck1 | availability @1000 h (20 runs) | ≈ 0.63, ~3.95 failures/run |
| Bakery1 | availability @720 h (20 runs) | ≈ 0.997, ~3 stoppages/run |
| CarCare0 | availability @2000 h (200 runs) | ≈ 0.990, MDT ≈ 8.1 h |
| CarCare1 | availability @2000 h (200 runs) | ≈ 0.992, MDT ≈ 3.3 h, MTBDE ≈ 423 h |
| Van1 / VanFlat | availability @2000 h (200 runs) | ≈ 0.987, MTBDE ≈ 421 h, MDT ≈ 4.6 h (identical both) |
| Water1 | units produced @8760 h (200 runs) | ≈ 1 514 000 of 1 576 800 (≈ 63 000 lost), avail ≈ 0.9999 |
| Water2 | grand total cost @8760 h (200 runs) | ≈ 2.9 M (≈ 2.5 M lost flow, ≈ 215 k repairs, 120 k purchase) |
| DC1 / DC2 | availability @8760 h (200 runs) | 99.948% (4.5 h down/yr) vs 99.984% (1.4 h/yr); downtime bill 1.36 M vs 0.42 M |
| ProdLine0 / ProdLine1 | availability @8760 h (200 runs) | ≈ 93.5% (MDT 11.4 h) vs ≈ 96.5% (MDT 5.3 h); Grand_Total ≈ 4.60 M vs ≈ 2.54 M/yr |
| Flight1 | reliability @2 h (2000 runs) | ≈ 82% (avail ≈ 96.1%, yellow ≈ 2.9%, red ≈ 3.9%) — one worked answer to the open-ended Capstone III brief |

Notes:

* Tutorial models write `ESO=0` (the legacy writer's default `ESO=1`
  trips RAPTOR Studio's emergency-spare-ordering preflight gate for
  blocks that never use a spare pool).
* Raptor 7 allows one link out of a Start or hierarchy In marker, so
  `write()` puts a `Split` node after any marker a model fans out from, and
  declares k on every multi-input node (Raptor 7 reads an unset k as
  undeclared). Neither changes a result.
* All blocks use the shared system stream 203, exactly like real
  legacy files; per-block streams are not exercised in this book.
* The `.rbds` files are written by `tools/gen_models.py`; export one as
  `.rbd` (File ▸ Save As) for the classic Raptor 7 text format
  (latin-1, CRLF).
