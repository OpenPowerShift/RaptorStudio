# UI localisation

## Choice: Qt Linguist (`.ts` → `.qm`)

RAPTOR Studio uses Qt's own translation system: source strings go through
`QCoreApplication.translate`, catalogues are edited in Qt Linguist, and the
compiled `.qm` file is loaded at start-up. It is the best fit because it is
already in the Qt runtime, understands message contexts, and shipped with
the compiler (`pyside6-lrelease`). RAPTOR's ribbon strings are data, so a
small repository generator emits the `.ts` instead of `lupdate`.

### Compared with the alternatives

| System | Example | Strength | Why not here |
|---|---|---|---|
| **Qt Linguist** | `tr("Model", "Page")` → `Modèle`; `.ts` `<message><source>Model</source><translation>Modèle</translation></message>` | Native to Qt; contexts, plurals, numerus; graphical editor; no runtime dependency | Requires a catalogue for every string and a compile step |
| **GNU gettext** | `_("Model")`; `.po` `msgid "Model"` / `msgstr "Modèle"` | Standard in Python, excellent tooling, docs and CLI share it | Qt widgets need an adapter; context/plural handling is weaker for UI |
| **Project Fluent** | `model = Modèle`; `msg("model")` in code | Excellent gender/plural/select expressions; translator-friendly syntax | Extra runtime dependency; no native Qt integration or Linguist workflow |
| **JSON/YAML dictionaries** | `{"Model": "Modèle"}` | Trivial to start | No contexts, plurals, comments or translator tooling; becomes unmaintainable |

The choice is deliberately the one with the fewest moving parts in a Qt
application. A future CLI/help catalogue can share the same locale codes;
the GUI does not need a second runtime.

## What is translated now

- Ribbon page titles, group titles, command text and command tooltips that
  are present in the catalogue. Qt's own standard buttons and file dialogs
  follow the language too.
- Preference: **Appearance ▸ Language**, `System language` or a shipped
  catalogue. A change applies on the next launch (existing widgets are not
  rebuilt mid-session). The language row shows the catalogue's coverage, so
  a partial catalogue is never presented as complete.
- The settings key is `appearance/language`; the CLI writes the same
  QSettings file as the GUI:

  ```sh
  raptor config set appearance/language fr
  ```

- `raptor/gui/locale/raptor_fr.ts` is the source catalogue and
  `raptor_fr.qm` the compiled one. The shipped French catalogue is a worked
  example (36 of 179 ribbon strings); untranslated strings fall back to
  English, never blank.

## Adding or updating a language

The ribbon strings are data (`CommandSpec.text`, `Page.title`,
`Group.title`), which `pyside6-lupdate` cannot see. Use the generator; it
keeps existing translations and adds unfinished entries for new strings:

```sh
python tools/gen_locale_catalogue.py raptor/gui/locale/raptor_fr.ts --write
pyside6-lrelease raptor/gui/locale/raptor_fr.ts \
                  -qm raptor/gui/locale/raptor_fr.qm
```

`--check` is the CI gate for a stale catalogue. Keep the locale code in the
file name (`raptor_<code>.qm`); `available_locales()` discovers it, and the
label includes the translated/total count.

Plurals use Qt's numerus form:

```python
qt_plural("Run", "%n run", "%n runs", count)
```

`qt_plural` is a thin wrapper over `QCoreApplication.translate`; a
catalogue entry for a numerus message needs `<numerusform>` children.

## Coverage plan

The framework is in place; the remaining work is incremental string
migration. Priority order:

1. Ribbon and main-window chrome (framework done; French is partial).
2. Preferences, File view and common dialogs.
3. Results, Advisor and Inspector labels.
4. Help book and reference manual (generated documents need their own
   translation pass).

Do not machine-translate engineering terms without a reviewer: `MTBDE`,
`spares`, `repair hold` and `duty cycle` have precise meanings in this
domain.
