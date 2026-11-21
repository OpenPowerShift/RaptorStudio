# UI localisation

## Choice: Qt Linguist (`.ts` → `.qm`)

RAPTOR Studio uses Qt's own translation system: source strings go through
`QCoreApplication.translate`, catalogues are edited in Qt Linguist, and the
compiled `.qm` file is loaded at start-up. It is the best fit because it is
already in the Qt runtime, understands message contexts, and shipped with
the compiler (`pyside6-lrelease`). RAPTOR's ribbon strings are data, so a
repository generator emits the `.ts` instead of `lupdate`.

### Compared with the alternatives

| System | Example | Strength | Why not here |
|---|---|---|---|
| **Qt Linguist** | `tr("Model", "Page")` → `Modelo`; `.ts` `<message><source>Model</source><translation>Modelo</translation></message>` | Native to Qt; contexts, plurals, numerus; graphical editor; no runtime dependency | Requires a catalogue for every string and a compile step |
| **GNU gettext** | `_("Model")`; `.po` `msgid "Model"` / `msgstr "Modelo"` | Standard in Python, excellent tooling, docs and CLI share it | Qt widgets need an adapter; context/plural handling is weaker for UI |
| **Project Fluent** | `model = Modelo`; `msg("model")` in code | Excellent gender/plural/select expressions; translator-friendly syntax | Extra runtime dependency; no native Qt integration or Linguist workflow |
| **JSON/YAML dictionaries** | `{"Model": "Modelo"}` | Trivial to start | No contexts, plurals, comments or translator tooling; becomes unmaintainable |

The choice is deliberately the one with the fewest moving parts in a Qt
application. A future CLI/help catalogue can share the same locale codes;
the GUI does not need a second runtime.

## Two source kinds, one catalogue

The GUI has two kinds of English text, and the generator
(`tools/gen_locale_catalogue.py`) collects both into one `.ts`:

1. **Data-driven chrome** — `CommandSpec.text`, page and group titles.
   `pyside6-lupdate` cannot see these; they are read from
   `raptor/gui/command_table.py` and translated where the registry builds
   the `QAction`s and the ribbon pages (`Page`, `Group`, `Command`
   contexts).
2. **Ordinary widget literals** — `QLabel("Recent")`, `setToolTip("...")`,
   `QPushButton("Open")`, `addTab(..., "Overview")`, file-dialog captions,
   `QMessageBox` titles and bodies. The generator scans `raptor/gui` with
   `ast` and writes them to the `Gui` context. Explicit `tr()` calls keep
   their named context (`Command search`, ...).

Only `raptor/gui` is scanned. The documentation book and the MCP server
are deliberately outside the boundary: their English is not translated.

## How ordinary widgets get translated

Remembering to wrap a thousand literal call sites is fragile. Instead,
`i18n.translate_widgets(root)` walks a widget tree once and translates the
chrome properties in place: window titles, label/button/check-box/group-box
text, tab titles, combo entries, placeholders, tooltips, status tips and
`QAction` text/tooltips. Each object keeps its English source on itself, so
the pass is idempotent and switching language (or back to English) restores
the source instead of translating a translation.

The pass runs:

* at the end of `MainWindow.__init__`, covering the ribbon, docks, run
  panel, results and inspector;
* in `modality.exec_dialog` for every modal dialog that uses the standard
  seam, plus the few dialogs that execute themselves
  (`i18n.exec_translated`);
* when the lazily-built File view (`Backstage`) is constructed;
* when a context menu is shown;
* on the first focus of any other window (a `focusChanged` hook), without
  an application-wide event filter — that filter saw QtWebEngine's internal
  objects and segfaulted (#142).

Status messages go through `i18n.TranslatingStatusBar`: a static message
with a catalogue entry is translated; an f-string with runtime values is
left as written rather than half-substituted.

## What is translated now

- **Brazilian Portuguese (`pt_BR`)** ships the full GUI catalogue:
  ribbon, File view/backstage, Preferences, About/Issues/dialogs,
  import/export, pools, phases, Run panel, Inspector, results tabs, menus,
  buttons, tooltips and status messages. The source stays English.
- Preference: **Appearance ▸ Language**, `System language` or a shipped
  catalogue. A change applies on the next launch; the label includes the
  translated/total count whenever a catalogue is partial, so partial work
  is never presented as complete.
- Qt's own standard buttons and file dialogs follow the language too
  (`qtbase_pt_BR.qm`), and the installer bundles both catalogues.
- The settings key is `appearance/language`; the CLI writes the same
  QSettings file as the GUI:

  ```sh
  raptor config set appearance/language pt_BR
  ```

- Source catalogue: `raptor/gui/locale/raptor_pt_BR.ts`; compiled:
  `raptor_pt_BR.qm`. Coverage is reported by
  `python tools/gen_locale_catalogue.py raptor/gui/locale/raptor_pt_BR.ts --coverage`
  and by `raptor.gui.i18n.coverage_report()`.

## Adding or updating a language

Generate the catalogue (the generator keeps existing translations, adds
unfinished entries for new strings, and seeds them from
`tools/locale_<code>.json`):

```sh
python tools/gen_locale_catalogue.py raptor/gui/locale/raptor_pt_BR.ts --write
pyside6-lrelease raptor/gui/locale/raptor_pt_BR.ts \
                  -qm raptor/gui/locale/raptor_pt_BR.qm
```

`--check` is the CI gate for a stale catalogue. Keep the locale code in the
file name (`raptor_<code>.qm`); `available_locales()` discovers it.

Adding a language is: add an entry to `i18n._SUPPORTED`, generate its `.ts`,
translate it (in Qt Linguist or in `tools/locale_<code>.json`), release the
`.qm`, and add the code to the tests.

Plurals use Qt's numerus form:

```python
qt_plural("Run", "%n run", "%n runs", count)
```

`qt_plural` is a thin wrapper over `QCoreApplication.translate`; a
catalogue entry for a numerus message needs `<numerusform>` children.

## Remaining gaps

- F-strings with runtime values (file names, counts, element names) are not
  translated as whole sentences; their fixed parts are not substituted.
  A catalogue entry exists for the static form of common status messages.
- Dynamic data displayed in a label that exactly matches a UI source string
  can be translated by the tree pass (for example a block named "Model");
  this is rare and harmless, but it is why the pass only runs once per
  window.
- Documentation and MCP strings remain English by design.
- Do not machine-translate engineering terms without a reviewer: `MTBDE`,
  `spares`, `repair hold` and `duty cycle` have precise meanings in this
  domain. The `pt_BR` glossary uses *sobressalentes* for spares,
  *ciclo de trabalho* for duty cycle, and keeps acronyms such as MTBDE,
  BDD and RBD unchanged.

## pt_BR glossary and reviewer notes

`tools/locale_pt_BR.json` is the translation memory; one English source
string has one translation there. The `.ts` keeps a separate entry per Qt
context, so a context may be overridden by hand when the same English word
needs different Portuguese (for example *Run* the button vs *run* the
data row). Regeneration preserves those overrides.

| English | pt_BR | Note |
| --- | --- | --- |
| block / node / event / hierarchy | bloco / nó / evento / hierarquia | fixed domain nouns |
| run (noun) / to run | execução / executar | the results table's *Run* column is a noun; the catalogue has one entry, so it reads *Executar* |
| trial | ensaio | Monte Carlo replication, not *teste* |
| failure / repair | falha / reparo | the FEV command is *Reparar*; the engine state *Repairing* stays English |
| spare / sparing / spare pool | sobressalente / provisão de sobressalentes / pool de sobressalentes | *pool* is kept as the usual industry loanword |
| downtime | tempo de inatividade | the *down times* data file lists *instantes de inatividade* |
| dependability | dependabilidade | ABNT NBR 5462 term; distinct from *confiabilidade* (reliability) |
| PM | MP | *manutenção preventiva*; the acronym is kept |
| availability / target | disponibilidade / meta | |
| duty cycle | ciclo de trabalho | *rated duty* is *regime nominal* |
| canvas | diagrama | *tela* only for the screen (*na tela*, *tela cheia*) |
| model / template | modelo / modelo | ambiguous in English too; *modelo de bloco* and *modelos da biblioteca* disambiguate |
| stream (random) | fluxo | capacity *flow* is also *fluxo*; prefer *fluxo aleatório* when space allows |

Engine state names (`Running`, `Repairing`, `Idle`, `Done`) and the
`Start`/`End` node names stay English because the results, FEV and data
tables show them verbatim. Ribbon key tips are fixed letters in
`raptor/gui/key_tips.py` and are not translated; the File button is `F`,
so its tooltip says *Alt e depois F* even though the English source still
says *then E*.

Known untranslated strings outside the catalogue: the theme action
alternates between the catalogued *Dark Theme* and the runtime-composed
*Light Theme*, which has no entry, and status messages built from
f-strings keep their English fixed parts (see *Remaining gaps*).
