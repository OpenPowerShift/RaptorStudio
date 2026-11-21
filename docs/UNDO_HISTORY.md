# Model history and the undo cap

**View ▸ Panels ▸ Model History** (Ctrl+Shift+H) opens a modeless list of
the model's undo steps. Each row is one command (an edit, a bulk edit, a
layout move, an import). The selected row is the jump target; the note
above the list says where the model is now, which step matches the saved
state, and whether the cap is in force. Steps after the current one are
greyed out and can be redone.

- Row 0 is the model **as loaded**. Once the history reaches the cap it
  becomes “before step 1 — at the history cap”, so the numbering always
  refers to the steps you can still reach.
- Double-click a step (or select it and press **Go to step**) to move the
  model to just after that step.
- **Undo** / **Ctrl+Z** and **Redo** / **Ctrl+Y** (or Ctrl+Shift+Z)
  move one step. **Ctrl+Home** and **Ctrl+End** jump to the first and
  last retained step.
- The row matching the saved state carries a **· saved** marker; when that
  state has fallen out of the history the note says so instead.
- The dialog stays open while you edit and follows the stack. A jump is
  refused while a run or step session is active; if results are on screen
  it asks before discarding them.
- A jump is **atomic**: the commands are replayed, then the hierarchy
  level is rebuilt once and the model-changed refresh runs once, not once
  per command. That is what keeps a long jump on a large model from
  flashing the canvas and the Inspector through every intermediate state.
- Opening or creating a model **starts a new history**;
  closing the model discards it and closes the panel, because the old
  steps refer to rows that no longer exist.

**Preferences ▸ Working ▸ History steps** sets the maximum number of steps
kept. `0` means unlimited; the default is 200. It is a **count cap, not a
byte budget**: one command can hold the old and new values of a large edit,
so a 200-step history on a huge model can still be large. Qt only accepts a
new cap on an empty stack, so a change made mid-session applies when the
current model is closed or the next one is loaded.
