# UI localisation

## Choice: Qt Linguist (`.ts` → `.qm`)

RAPTOR Studio uses Qt's own translation system: source strings go through
`QCoreApplication.translate`, catalogues are edited in Qt Linguist, and the
compiled `.qm` file is loaded at start-up. It is the best fit because it is
already in the Qt runtime, understands message contexts, and Qt ships the
extraction/compiler tools (`pyside6-lupdate`, `pyside6-lrelease`).

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

- Ribbon page titles, group titles, command text and command tooltips.
- Preference: **Appearance ▸ Language**, `System language` or a shipped
  catalogue. A change applies on the next launch (existing widgets are not
  rebuilt mid-session).
- The settings key is `appearance/language`; from a script:

  ```sh
  raptor config set appearance/language fr
  ```

- `raptor/gui/locale/raptor_fr.ts` is the source catalogue and
  `raptor_fr.qm` the compiled one. `fr` is a worked example, not a claim of
  complete coverage; untranslated strings fall back to English, never blank.

## Adding or updating a language

```sh
# Edit (or create) the catalogue in Qt Linguist, then compile:
pyside6-lrelease raptor/gui/locale/raptor_fr.ts \
                  -qm raptor/gui/locale/raptor_fr.qm
```

Use `pyside6-lupdate` over the source tree when adding a new batch of
translatable strings. Keep the locale code in the file name
(`raptor_<code>.qm`); `available_locales()` discovers it automatically.

Plurals use Qt's numerus form:

```python
qt_plural("Run", "%n run", "%n runs", count)
```

## Coverage plan

The framework is in place; the remaining work is incremental string
migration. Priority order:

1. Ribbon and main-window chrome (done).
2. Preferences, File view and common dialogs.
3. Results, Advisor and Inspector labels.
4. Help book and reference manual (generated documents need their own
   translation pass).

Do not machine-translate engineering terms without a reviewer: `MTBDE`,
`spares`, `repair hold` and `duty cycle` have precise meanings in this
domain.
