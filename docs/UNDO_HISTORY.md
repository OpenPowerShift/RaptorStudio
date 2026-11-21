# Model history and the undo cap

**View ▸ Panels ▸ Model History** opens a modeless list of the model's
undo steps. Each row is one command (an edit, a bulk edit, a layout move,
an import). The current position is highlighted; steps after it are greyed
out and can be redone.

- Double-click a step (or select it and press **Go to step**) to move the
  model to just after that retained step. The base row moves to the state
  before the first retained step.
- **Undo** and **Redo** move one step.
- The dialog stays open while you edit and follows the stack. A jump is
  refused while a run or step session is active; if results are on screen
  it asks before discarding them. Level changes and view refreshes are
  applied once at the end of a jump, not per command.

**Preferences ▸ Working ▸ History steps** sets the maximum number of steps
kept. `0` means unlimited; the default is 200. It is a **count cap, not a
byte budget**: one command can hold the old and new values of a large edit,
so a 200-step history on a huge model can still be large. Qt only accepts a
new cap on an empty stack, so a change made mid-session applies when the
next model is loaded.

The list marks the saved state (or says it is no longer in history) and
adds a “Before the first retained step” row. When the cap has discarded
older steps, the panel says so; step numbers then start at the oldest
retained step, not the original model.
