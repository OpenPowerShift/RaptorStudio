# Accessibility and interface scaling

RAPTOR Studio aims to be usable without a mouse, in bright rooms, on
high-DPI screens, and by people with low vision.

## Interface scale

**Preferences ▸ Appearance ▸ Interface scale** sets 100%, 125%, 150%, 175%
or 200%. It scales the application font, the design stylesheet's px lengths
and the ribbon/widget geometry constants together: text, padding, borders,
ribbon heights and control sizes. The choice is saved per user and applies
on the next launch. Canvas labels are drawing-scale, not UI text; use the
canvas zoom for them.

From a script or a managed install:

```sh
raptor config set appearance/ui_scale 150
```

## High contrast

**Preferences ▸ Appearance ▸ Contrast** switches to black/white surfaces,
black/white text, strong borders and a darker accent. Simulation-state
colours stay as they are: they are data, not chrome, and their names
(Green/Yellow/Red, Running/Repairing, …) are part of the model's meaning.

```sh
raptor config set appearance/high_contrast true
```

## Keyboard and focus

- Every ribbon command is in **command search** (Ctrl+Shift+P) and has a
  key tip on its tab. Key tips use stable page keys, not translated labels.
- Dialogs use standard Tab/Shift+Tab order and Enter/Escape.
- Focused text fields, combo boxes, spin boxes, tables and buttons draw a
  2 px accent ring, including on dark and high-contrast themes.
- The canvas supports keyboard nudge, select-all, copy/paste, zoom and
  hierarchy navigation; see **Help ▸ Keyboard Shortcuts**.

## Screen readers

Major controls carry accessible names: the ribbon, docks, most result
tables, charts, history dialogs and the dialogs built for analysis and
import. This is an ongoing audit, not a claim that every custom canvas
pixel has a text alternative.
Dynamic status text is also written to the status bar. Canvas drawing is
visual by design; the Model Explorer and Data Tables provide the same
elements as text for assistive technology.

## Charts

Every chart series keeps a pattern as well as a colour, so charts read in
greyscale and for colour-blind readers. The chart palette setting chooses
the colour set; the patterns do not change.
