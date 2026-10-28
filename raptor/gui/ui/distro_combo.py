"""Distribution combo boxes: one fill loop, one display-name rule.

Ten near-identical `QComboBox` population loops existed across
`distro_editor.py`, `inspector.py`, `data_tables.py` and `dialogs.py`, and
the code-18 "None (unrepairable)" relabel was applied **four different
ways**:

| Site | Rule |
|---|---|
| `inspector.py`'s combo fill | **conditional** — relabel only when *not* a failure law |
| `dialogs.py` (×2) | **unconditional** |
| `inspector.py`'s read-only `_row` branch | a **fourth** rendering, testing `== 18` separately |
| `data_tables.py::_distro_cell` | **no relabel at all** |

So the Repair distribution read *"None (unrepairable)"* in the Inspector and
in two dialogs, but the raw *"None"* in the **Data Tables** grid — the same
block, on the same screen, showing two different names for one value.

The conditional rule is the correct one, and the reason is the format's
own semantics rather than taste: code 18 is `None` as a *failure* law (a
block that cannot fail) but *unrepairable* as a *repair* law (a block that
fails and never comes back). Relabelling it unconditionally — as two
dialogs do — tells the user a block "cannot fail" in a combo that is
choosing its repair law.

Qt lives here: all ten call sites are widgets.
"""
from __future__ import annotations

from typing import Any

from raptor.core.distro_rules import OFFERED
from raptor.core.distro_rules import DISTRO_NAMES

#: Distribution 18 is `None` in `DISTRO_NAMES`. As a REPAIR law it means
#: the block can never be repaired, which is the more useful thing to tell
#: someone choosing a repair distribution -- hence the relabel, and hence
#: only there.
_UNREPAIRABLE = "None (unrepairable)"

#: The distribution code that carries that meaning.
_UNREPAIRABLE_CODE = 18


def distro_display_name(code: int, *, is_failure: bool) -> str:
    """How *code* should read in a combo for a failure or repair law.

    *is_failure* is the whole point of the function: code 18 is named
    differently as a repair law than as a failure law, and conflating the
    two is what produced four inconsistent copies.
    """
    name = DISTRO_NAMES.get(code, f"Unknown (code {code})")
    if not is_failure and code == _UNREPAIRABLE_CODE:
        return _UNREPAIRABLE
    return name


def fill_distro_combo(combo: Any, current: int | None, *,
                      is_failure: bool, kind: str | None = None) -> Any:
    """Populate *combo* with the distributions offered for *kind* (Raptor 7's
    list, plus Fixed for a failure law: `distro_rules.OFFERED`)
    for *kind* (``"failure"``, ``"repair"``, ``"delay"`` or ``"event"``; by default
    ``"failure"`` or ``"repair"`` from *is_failure*), selecting *current*.

    Returns *combo* so a caller can chain.

    *current* is `None` for a combo that is presenting a PROPOSAL rather
    than reading an existing value -- the Mass Edit dialogs, where the
    first entry is the default the user is expected to replace. That is not
    a mistake to warn about, so it gets no tooltip.

    A *current* that is a known code outside the list for this picker
    (a file written by another tool, or by an older version of this port) is
    still listed, after the others and marked, so opening a model never
    changes it behind the user's back.

    A *current* that is a code the combo does not contain at all is a
    different thing: a hand-edited or corrupt file. It is selected to index 0
    **and** given a tooltip saying so, because silently landing on the first
    entry would make the file look valid while showing the wrong
    distribution -- and the value would be replaced on the next save.
    """
    codes = OFFERED[kind or ("failure" if is_failure else "repair")]
    for code in codes:
        combo.addItem(distro_display_name(code, is_failure=is_failure), code)
    if current is not None and current not in codes and current in DISTRO_NAMES:
        combo.addItem(distro_display_name(current, is_failure=is_failure)
                      + " (not offered by Raptor 7 here)", current)
    if current is None:
        combo.setCurrentIndex(0)
        return combo
    idx = combo.findData(current)
    combo.setCurrentIndex(max(idx, 0))
    if idx < 0:
        combo.setToolTip(
            f"Unknown distribution code #{current} — showing the first "
            "entry instead. Saving will replace it.")
    return combo
