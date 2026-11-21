# Model history and the undo cap

**View ▸ Panels ▸ Model History** opens a modeless list of the model's
undo steps. Each row is one command (an edit, a bulk edit, a layout move,
an import). The current position is highlighted; steps after it are greyed
out and can be redone.

- Double-click a step (or select it and press **Go to step**) to move the
  model to just after that step.
- **Undo** and **Redo** move one step.
- The dialog stays open while you edit and follows the stack.

**Preferences ▸ Working ▸ History steps** sets the maximum number of steps
kept. `0` means unlimited; the default is 200. A cap bounds the memory a
long session can use, because one command can hold the old and new values
of a large edit. Qt only accepts a new cap on an empty stack, so a change
made mid-session applies when the next model is loaded.
