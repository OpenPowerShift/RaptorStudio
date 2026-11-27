"""The Paste Special dialog: tick what to copy from the clipboard element.

One grouped checklist (Reliability, Maintenance, Costs, Behaviour,
Organisation), a group greyed with its reason when it cannot apply, the
modes for tags and comments, and a live line saying what the paste would
change. The planning is `raptor.core.edit.paste_special`; this is only the
form. The last choice is remembered for the session.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QRadioButton, QVBoxLayout, QWidget,
)

from raptor.core.edit.paste_special import (
    COMMENT_MODES, DEFAULT_GROUPS, SECTIONS)

#: What the dialog last returned, for the rest of the session.
_REMEMBERED: dict = {"groups": set(DEFAULT_GROUPS), "tag_mode": "add",
                     "comment_mode": "replace"}

_COMMENT_LABELS = {"replace": "Replace the comment",
                   "append": "Append to the comment",
                   "empty": "Only where there is no comment"}

#: preview(groups, tag_mode, comment_mode) -> (line, would_change)
Preview = Callable[[list[str], str, str], tuple[str, bool]]


def forget_choices() -> None:
    """Back to the defaults (tests)."""
    _REMEMBERED.update(groups=set(DEFAULT_GROUPS), tag_mode="add",
                       comment_mode="replace")


class PasteSpecialDialog(QDialog):
    def __init__(self, parent: QWidget | None, heading: str,
                 applicable: dict[str, str], preview: Preview) -> None:
        super().__init__(parent)
        self.setWindowTitle("Paste Special")
        self._preview = preview
        outer = QVBoxLayout(self)
        intro = QLabel(heading)
        intro.setWordWrap(True)
        outer.addWidget(intro)
        self.boxes: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        outer.addLayout(grid)
        #: Reliability | Maintenance, Costs | Behaviour, Organisation across
        places = [(0, 0, 1), (0, 1, 1), (1, 0, 1), (1, 1, 1), (2, 0, 2)]
        for (title, groups), (row, col, span) in zip(SECTIONS, places):
            frame = QGroupBox(title)
            lay = QVBoxLayout(frame)
            for group in groups:
                box = QCheckBox(group)
                reason = applicable.get(group, "")
                box.setEnabled(not reason)
                if reason:
                    box.setToolTip(f"Not available: {reason}.")
                box.setChecked(not reason and group in _REMEMBERED["groups"])
                box.toggled.connect(self._refresh)
                self.boxes[group] = box
                lay.addWidget(box)
                if group == "Tags":
                    lay.addLayout(self._tag_modes())
                elif group == "Comment":
                    lay.addLayout(self._comment_mode())
            grid.addWidget(frame, row, col, 1, span)
        self.preview_label = QLabel()
        self.preview_label.setWordWrap(True)
        self.preview_label.setObjectName("pasteSpecialPreview")
        outer.addWidget(self.preview_label)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Paste")
        ok.setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self._sync_modes()
        self._refresh()

    # -- the two mode controls (indented under their group) ---------------
    def _tag_modes(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addSpacing(22)
        self.tag_add = QRadioButton("Add to existing tags")
        self.tag_replace = QRadioButton("Replace tags")
        group = QButtonGroup(self)
        group.addButton(self.tag_add)
        group.addButton(self.tag_replace)
        (self.tag_replace if _REMEMBERED["tag_mode"] == "replace"
         else self.tag_add).setChecked(True)
        self.tag_add.toggled.connect(self._refresh)
        row.addWidget(self.tag_add)
        row.addWidget(self.tag_replace)
        row.addStretch(1)
        return row

    def _comment_mode(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addSpacing(22)
        self.comment_combo = QComboBox()
        for mode in COMMENT_MODES:
            self.comment_combo.addItem(_COMMENT_LABELS[mode], mode)
        self.comment_combo.setCurrentIndex(
            COMMENT_MODES.index(_REMEMBERED["comment_mode"]))
        self.comment_combo.currentIndexChanged.connect(self._refresh)
        row.addWidget(self.comment_combo)
        row.addStretch(1)
        return row

    def _sync_modes(self) -> None:
        """A mode only matters while its group is ticked."""
        tags = self.boxes["Tags"]
        self.tag_add.setEnabled(tags.isEnabled() and tags.isChecked())
        self.tag_replace.setEnabled(tags.isEnabled() and tags.isChecked())
        comment = self.boxes["Comment"]
        self.comment_combo.setEnabled(comment.isEnabled()
                                      and comment.isChecked())

    # -- results ----------------------------------------------------------
    def groups(self) -> list[str]:
        return [g for g, box in self.boxes.items()
                if box.isEnabled() and box.isChecked()]

    def tag_mode(self) -> str:
        return "replace" if self.tag_replace.isChecked() else "add"

    def comment_mode(self) -> str:
        return str(self.comment_combo.currentData())

    def _refresh(self, *_args) -> None:
        self._sync_modes()
        groups = self.groups()
        if not groups:
            line, change = "Tick what to paste.", False
        else:
            line, change = self._preview(groups, self.tag_mode(),
                                         self.comment_mode())
        self.preview_label.setText(line)
        self.buttons.button(
            QDialogButtonBox.StandardButton.Ok).setEnabled(change)

    def remember(self) -> None:
        _REMEMBERED.update(
            groups={g for g, box in self.boxes.items() if box.isChecked()}
            | {g for g in _REMEMBERED["groups"]
               if not self.boxes[g].isEnabled()},
            tag_mode=self.tag_mode(), comment_mode=self.comment_mode())


def ask_paste_special(parent: QWidget | None, heading: str,
                      applicable: dict[str, str], preview: Preview
                      ) -> tuple[list[str], str, str] | None:
    """`(groups, tag_mode, comment_mode)`, or None when cancelled."""
    from raptor.gui.ui.modality import exec_dialog
    dlg = PasteSpecialDialog(parent, heading, applicable, preview)
    if exec_dialog(dlg) != QDialog.DialogCode.Accepted:
        return None
    dlg.remember()
    groups = dlg.groups()
    return (groups, dlg.tag_mode(), dlg.comment_mode()) if groups else None
